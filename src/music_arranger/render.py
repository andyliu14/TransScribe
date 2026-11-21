"""Headless MusicXML → Verovio SVG → multipage PDF, no GUI dependency."""

import base64
import ctypes
import ctypes.util
import hashlib
import re
import xml.etree.ElementTree as ET
from functools import lru_cache
from html import escape
from io import BytesIO
from pathlib import Path

import verovio
from fontTools.ttLib import TTFont
from pypdf import PdfReader, PdfWriter


def prepare_written_notation(score):
    """Choose stable destination clefs on the written-pitch export copy."""
    from music21 import clef, dynamics, instrument, layout, stream

    if len(score.parts) == 3:
        rebeam_redistributed_parts(score)
    choices = (clef.BassClef, clef.TenorClef, clef.TrebleClef)
    # Diatonic note numbers at the bottom/top staff lines.
    bounds = ((19, 27), (23, 31), (31, 39))
    for part in score.parts:
        for n in part.recurse().notes:
            n.stemDirection = "unspecified"
        for obj in list(
            part.recurse().getElementsByClass(
                (clef.Clef, layout.SystemLayout, layout.PageLayout)
            )
        ):
            obj.activeSite.remove(obj)
        measures = list(part.getElementsByClass(stream.Measure))
        if not measures:
            continue
        costs, paths = [0.0] * 3, [[], [], []]
        for m in measures:
            marks = list(m.recurse().getElementsByClass(dynamics.Dynamic))
            if len(marks) >= 3:
                for mark in marks[1:-1]:
                    mark.activeSite.remove(mark)
            pitches = [p.diatonicNoteNum for n in m.recurse().notes for p in n.pitches]
            next_costs, next_paths = [], []
            for index, (low, high) in enumerate(bounds):
                ledger_cost = sum(
                    (max(0, low - p - 1, p - high - 1) / 2) ** 2 for p in pitches
                ) / max(1, len(pitches))
                previous = min(
                    range(3),
                    key=lambda j: (
                        costs[j] + (0 if j == index else 4 if pitches else float("inf"))
                    ),
                )
                next_costs.append(
                    costs[previous] + ledger_cost + (4 if previous != index else 0)
                )
                next_paths.append(paths[previous] + [index])
            costs, paths = next_costs, next_paths
        route = paths[min(range(3), key=lambda j: costs[j])]
        if isinstance(part.getInstrument(), instrument.Saxophone):
            route = [2] * len(measures)
        current = None
        for m, index in zip(measures, route):
            if index != current:
                m.insert(0, choices[index]())
                current = index


def _printable_xml(path):
    """Thin crowded metronome stacks only in the PDF input, never the score."""
    root = ET.fromstring(path.read_bytes())
    suppressed = []
    for part in root.findall("part"):
        for measure in part.findall("measure"):
            directions = [
                d
                for d in measure.findall("direction")
                if d.find("direction-type/metronome") is not None
            ]
            if len(directions) < 3:
                continue
            for direction in directions[1:-1]:
                suppressed.append(
                    {
                        "part": part.get("id"),
                        "measure": measure.get("number"),
                        "printed_bpm": direction.findtext(
                            "direction-type/metronome/per-minute"
                        ),
                        "offset": direction.findtext("offset", "0"),
                    }
                )
                for kind in list(direction.findall("direction-type")):
                    for mark in list(kind.findall("metronome")):
                        kind.remove(mark)
                    if not len(kind):
                        direction.remove(kind)
                if direction.find("direction-type") is None:
                    measure.remove(direction)
    return ET.tostring(root, encoding="unicode"), suppressed


def rebeam_redistributed_parts(score):
    """Discard source chord beams after voice extraction and instrument handoffs."""
    from music21 import beam

    for part in score.parts:
        for n in part.recurse().notes:
            n.beams = beam.Beams()
        part.makeBeams(inPlace=True, setStemDirections=False)


@lru_cache(maxsize=1)
def register_music_font():
    """Register Verovio's bundled webfont with Cairo, within this process only."""
    css = (Path(verovio.toolkit().getResourcePath()) / "Leipzig.css").read_text()
    encoded = re.search(r"base64,([^)]*)", css).group(1)
    raw = base64.b64decode(encoded)
    directory = Path.home() / ".cache/music-arranger/fonts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"Leipzig-{hashlib.sha256(raw).hexdigest()[:12]}.ttf"
    if not path.exists():
        font = TTFont(BytesIO(raw))
        font.flavor = None
        font.save(path)
    libname = ctypes.util.find_library("fontconfig")
    if not libname:
        raise RuntimeError("PDF rendering requires Fontconfig (used by Cairo).")
    fc = ctypes.CDLL(libname)
    fc.FcConfigGetCurrent.restype = ctypes.c_void_p
    fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    fc.FcConfigAppFontAddFile.restype = ctypes.c_int
    if not fc.FcConfigAppFontAddFile(fc.FcConfigGetCurrent(), str(path).encode()):
        raise RuntimeError("Could not register the bundled music font.")


def render_pdf(path: Path) -> dict:
    register_music_font()
    try:
        import cairosvg
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            "PDF output needs Cairo. Install Cairo or disable PDF engraving."
        ) from exc
    tk = verovio.toolkit()
    tk.setOptions(
        {
            "pageWidth": 2100,
            "pageHeight": 2970,
            # Wide string runs can lift slurs above the first staff's nominal
            # box. Leave headroom at page starts (Passacaglia bar 82).
            "pageMarginTop": 150,
            "scale": 40,
            "adjustPageHeight": False,
            "breaks": "auto",
            "header": "auto",
            "footer": "none",
        }
    )
    printable, suppressed = _printable_xml(path)
    if not tk.loadData(printable):
        raise ValueError(f"Verovio could not load {path}")
    writer = PdfWriter()
    pages = tk.getPageCount()
    if pages == 0:
        raise ValueError("Renderer produced no pages")
    source_metadata = ET.fromstring(printable)
    credits = [
        e.text.strip()
        for e in source_metadata.findall("identification/creator")
        + source_metadata.findall("identification/rights")
        if e.text and e.text.strip() != "Music21"
    ]
    credit_line = " · ".join(dict.fromkeys(credits))
    for page in range(1, pages + 1):
        svg = tk.renderToSVG(page)
        width = float(re.search(r'<svg[^>]* width="([\d.]+)', svg).group(1))
        height = float(re.search(r'<svg[^>]* height="([\d.]+)', svg).group(1))
        footer = f'<text x="{width / 2}" y="{height - 12}" text-anchor="middle" font-family="serif" font-size="11">{page} / {pages}</text>'
        if page == 1 and credit_line:
            footer += f'<text x="{width / 2}" y="{height - 28}" text-anchor="middle" font-family="serif" font-size="8">{escape(credit_line)}</text>'
        svg = svg.rsplit("</svg>", 1)[0] + footer + "</svg>"
        svg_path = path.parent / f"{path.stem}-{page:02d}.svg"
        svg_path.write_text(svg)
        pdf = cairosvg.svg2pdf(bytestring=svg.encode())
        writer.add_page(PdfReader(BytesIO(pdf)).pages[0])
    output = path.with_suffix(".pdf")
    writer.write(output)
    return {
        "pdf": str(output),
        "pages": pages,
        "engine": tk.getVersion(),
        "page_margin_top": 150,
        "source_credit_line": credit_line,
        "suppressed_dense_metronomes": suppressed,
        "tempo_print_policy": "In bars with 3+ metronome marks, print first and last. MusicXML and MIDI retain every tempo.",
    }
