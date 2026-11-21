"""Reproduce the inspected CG Flute transcription from its pinned vector PDF.

This is a source-specific transcription, NOT a general PDF/OMR importer.
Pitch and duration come from Maestro glyph positions; ties are checked against
the printed page. The original PDF remains immutable, including its credits.
"""

import argparse
import hashlib
import json
from pathlib import Path

from music21 import (
    bar,
    clef,
    instrument,
    key,
    metadata,
    meter,
    note,
    pitch,
    stream,
    tie,
)
from pypdf import PdfReader

SOURCE_URL = "https://cgflute.com/wp-content/uploads/2017/10/hedwigs-theme.pdf"
SOURCE_SHA256 = "9c9048f8afd16654f60532bfdeb0c775fb218563a9fd1debe3f217a9bc3e8726"
TIE_STARTS = ({7, 15, 23, 31, 39, 47, 48, 49}, {7, 15, 23, 39})


def transcribe(source):
    if hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError(
            "This transcription recipe only supports the inspected CG Flute PDF"
        )
    pdf = PdfReader(source)
    if len(pdf.pages) != 1:
        raise ValueError("Expected the one-page flute duet")
    glyphs = []

    def visit(text, cm, tm, font, size):
        if text.strip() and font and "Maestro" in str(font.get("/BaseFont")):
            glyphs.append((text, cm[4], cm[5]))

    pdf.pages[0].extract_text(visitor_text=visit)
    staff_origins = [y for text, x, y in glyphs if text == "&"]
    if len(staff_origins) != 12:
        raise ValueError("Expected six systems of two treble staves")
    staves = [[] for _ in staff_origins]
    step_size = 77.729 * 0.24 / 8
    for text, x, y in glyphs:
        if text not in ("œ", "˙", "Œ"):
            continue
        # The final Flute 2 E6 is halfway between staves. This inspected edition
        # contains no notes below E4; nearest-staff alone misassigns that ledger note.
        eligible = [i for i in range(12) if -5 <= y - staff_origins[i] <= 30]
        if not eligible:
            raise ValueError(
                "Unexpected note position outside the inspected staff ranges"
            )
        staff = min(eligible, key=lambda i: abs(y - staff_origins[i]))
        duration = 2 if text == "˙" else 1
        if any(
            t == "J" and abs(gx - x) < 0.2 and abs(gy - y) < 25 for t, gx, gy in glyphs
        ):
            duration = 0.5
        if any(
            t == "." and abs(gx - x - 7.579) < 0.3 and abs(gy - y) < 3
            for t, gx, gy in glyphs
        ):
            duration *= 1.5
        accidental = next(
            (
                t
                for t, gx, gy in glyphs
                if t in ("#", "b", "n") and 0 < x - gx < 7 and abs(gy - y) < 0.2
            ),
            None,
        )
        step = round((y - staff_origins[staff]) / step_size)
        if text != "Œ" and abs((y - staff_origins[staff]) / step_size - step) > 0.01:
            raise ValueError("Notehead does not lie on the expected staff grid")
        staves[staff].append(
            {
                "x": x,
                "y": y,
                "staff": staff + 1,
                "diatonic": pitch.Pitch("G4").diatonicNoteNum + step,
                "accidental": accidental,
                "duration": duration,
                "rest": text == "Œ",
            }
        )
    score = stream.Score()
    audit = []
    for player in range(2):
        part = stream.Part(id=f"flute-{player + 1}")
        part.partName = f"Flute {player + 1}"
        part.insert(0, instrument.Flute())
        records = [
            event
            for system in range(6)
            for event in sorted(staves[2 * system + player], key=lambda e: e["x"])
        ]
        measure_number, offset, accidentals, previous = 0, 0, {}, None
        m = stream.Measure(number=0)
        m.insert(0, clef.TrebleClef())
        m.insert(0, meter.TimeSignature("3/4"))
        m.insert(0, key.KeySignature(1))
        part.append(m)
        for record in records:
            duration = record["duration"]
            if record["rest"]:
                n = note.Rest(quarterLength=duration)
            else:
                p = pitch.Pitch()
                p.diatonicNoteNum = record["diatonic"]
                token = (p.step, p.octave)
                if record["accidental"] is not None:
                    accidentals[token] = {"#": 1, "b": -1, "n": 0}[record["accidental"]]
                alter = accidentals.get(token, 1 if p.step == "F" else 0)
                p.accidental = pitch.Accidental(alter) if alter else None
                incoming = offset == 0 and measure_number - 1 in TIE_STARTS[player]
                if incoming:
                    if previous.pitch.diatonicNoteNum != p.diatonicNoteNum:
                        raise ValueError(
                            "Printed tie does not connect matching staff positions"
                        )
                    p = previous.pitch
                n = note.Note(p, quarterLength=duration)
                outgoing = (
                    measure_number in TIE_STARTS[player] and offset + duration == 3
                )
                if incoming or outgoing:
                    n.tie = tie.Tie(
                        "continue"
                        if incoming and outgoing
                        else "stop"
                        if incoming
                        else "start"
                    )
                previous = n
            if offset + duration > (1 if measure_number == 0 else 3):
                raise ValueError(f"Rhythm overflow at measure {measure_number}")
            m.insert(offset, n)
            audit.append(
                {
                    "part": player + 1,
                    "measure": measure_number,
                    "offset": offset,
                    "pitch": n.pitch.nameWithOctave if n.isNote else None,
                    "tie": n.tie.type if n.tie else None,
                    **record,
                }
            )
            offset += duration
            if offset == (1 if measure_number == 0 else 3):
                if measure_number == 0:
                    m.padAsAnacrusis()
                measure_number += 1
                offset, accidentals = 0, {}
                if record is not records[-1]:
                    m = stream.Measure(number=measure_number)
                    part.append(m)
        if offset or measure_number != 51:
            raise ValueError(
                f"Expected pickup + fifty 3/4 bars; player {player + 1}: {measure_number} bars, remainder {offset}"
            )
        m.rightBarline = bar.Barline("final")
        score.insert(0, part)
    score.atSoundingPitch = True
    score.metadata = metadata.Metadata(title="Hedwig's Theme", composer="John Williams")
    score.metadata.addContributor(
        metadata.Contributor(name="Chew Sze Gee", role="arranger")
    )
    score.metadata.copyright = "©cgflute2017; source flute-duet adaptation"
    return score, audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument(
        "--output", type=Path, default=Path("sources/themes/hedwigs-theme")
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    score, audit = transcribe(args.pdf)
    xml = args.output / "source.musicxml"
    if xml.resolve() == args.pdf.resolve():
        raise ValueError("Do not overwrite the original PDF")
    score.write("musicxml", fp=xml)
    (args.output / "transcription-audit.json").write_text(
        json.dumps(
            {
                "source_pdf_sha256": SOURCE_SHA256,
                "method": "Vector Maestro glyph positions and durations, with manually reviewed tie endpoints; source-specific, not general OMR",
                "events": audit,
                "review_notes": [
                    "51 written measures: one-quarter pickup numbered 0, then bars 1–50 in 3/4.",
                    "All printed pitches retained, including E-flat spellings and key-signature F-sharps in bars 6 and 38. This edition is not asserted to match the orchestral original.",
                    "No tempo is printed in the PDF. Audition uses the existing 120-quarter-BPM fallback; it is not a source performance instruction.",
                ],
            },
            indent=2,
        )
        + "\n"
    )
    print(xml)


if __name__ == "__main__":
    main()
