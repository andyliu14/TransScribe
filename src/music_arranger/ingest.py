"""Conservative repair of integer-tick rounding in explicitly encoded tuplets."""

import json
import xml.etree.ElementTree as ET
from fractions import Fraction
from math import lcm
from pathlib import Path
from zipfile import ZipFile

from music21 import tempo


def musicxml_root(source):
    source = Path(source)
    if source.suffix.lower() == ".mxl":
        with ZipFile(source) as archive:
            container = ET.fromstring(archive.read("META-INF/container.xml"))
            rootfile = next(e for e in container.iter() if e.tag.endswith("rootfile"))
            return ET.fromstring(archive.read(rootfile.attrib["full-path"]))
    return ET.fromstring(source.read_bytes())


def restore_sound_tempos(score, source):
    """Keep MusicXML playback tempo separate from its printed metronome number.

    music21 10.5 skips <sound tempo> when a direction also has a metronome.
    MusicXML defines sound tempo in quarter notes/minute, even when the
    printed metronome uses half notes or dotted beats. Do not rewrite input.
    Default direction offsets retain music21's legacy placement convention;
    this repair reconciles tempo values, not legacy offset interpretation.
    https://www.w3.org/2021/06/musicxml40/musicxml-reference/elements/sound/
    """
    root = musicxml_root(source)
    xml_parts = root.findall("part")
    # music21 expands one grand-staff XML part into several PartStaff objects.
    # Keep the XML cursor shared, but route directions to their encoded staff.
    groups = []
    index = 0
    for xml_part in xml_parts:
        count = max(
            [int(e.text) for e in xml_part.findall("./measure/attributes/staves")]
            or [1]
        )
        groups.append(list(score.parts[index : index + count]))
        index += count
    if index != len(score.parts) or any(not group for group in groups):
        raise ValueError("Cannot reconcile MusicXML sound tempos with parsed parts")
    repairs = []
    for part_index, (xml_part, group) in enumerate(zip(xml_parts, groups)):
        divisions = None
        staff_measures = [list(p.getElementsByClass("Measure")) for p in group]
        if any(len(xml_part.findall("measure")) != len(ms) for ms in staff_measures):
            raise ValueError(
                "Cannot reconcile MusicXML sound tempos with parsed measures"
            )
        for measure_index, xml_measure in enumerate(xml_part.findall("measure")):
            cursor = Fraction(0)
            for element in xml_measure:
                if (
                    element.tag == "attributes"
                    and element.find("divisions") is not None
                ):
                    divisions = Fraction(element.findtext("divisions"))
                elif element.tag in {"backup", "forward"}:
                    cursor += (
                        Fraction(element.findtext("duration"))
                        / divisions
                        * (-1 if element.tag == "backup" else 1)
                    )
                elif element.tag == "note":
                    if element.find("chord") is None and element.find("grace") is None:
                        cursor += (
                            Fraction(element.findtext("duration", "0")) / divisions
                        )
                elif element.tag in {"direction", "sound"}:
                    staff = int(element.findtext("staff", "1"))
                    if not 1 <= staff <= len(group):
                        raise ValueError("MusicXML tempo references an absent staff")
                    measure = staff_measures[staff - 1][measure_index]
                    sounds = (
                        [element]
                        if element.tag == "sound"
                        else element.findall("sound")
                    )
                    for sound in sounds:
                        if "tempo" not in sound.attrib:
                            continue
                        bpm = float(sound.get("tempo"))
                        if bpm <= 0:
                            raise ValueError(
                                "MusicXML sound tempo must be resolved to a positive BPM"
                            )
                        offset_element = sound.find("offset")
                        if offset_element is None and element.tag == "direction":
                            offset_element = element.find("offset")
                        offset = cursor
                        if (
                            offset_element is not None
                            and offset_element.get("sound") != "no"
                        ):
                            offset += Fraction(offset_element.text) / divisions
                        marks = [
                            m
                            for m in measure.recurse().getElementsByClass(
                                tempo.MetronomeMark
                            )
                            if abs(float(m.getOffsetInHierarchy(measure) - offset))
                            < 1e-6
                        ]
                        if len(marks) > 1:
                            raise ValueError(
                                "Ambiguous simultaneous metronomes in MusicXML tempo import"
                            )
                        before = marks[0].getQuarterBPM() if marks else None
                        if before is not None and abs(before - bpm) < 1e-6:
                            continue
                        if marks:
                            mark = marks[0]
                            mark.numberSounding = bpm / float(
                                mark.referent.quarterLength
                            )
                        else:
                            mark = tempo.MetronomeMark(numberSounding=bpm)
                            measure.insert(offset, mark)
                        repairs.append(
                            {
                                "part": part_index + 1,
                                "staff": staff,
                                "measure": measure.number,
                                "offset": float(offset),
                                "before_quarter_bpm": before,
                                "sound_quarter_bpm": bpm,
                                "printed_number": mark.number,
                            }
                        )
    score.editorial["sound_tempo_repairs"] = repairs
    return repairs


TYPES = {
    "whole": Fraction(4),
    "half": Fraction(2),
    "quarter": Fraction(1),
    "eighth": Fraction(1, 2),
    "16th": Fraction(1, 4),
    "32nd": Fraction(1, 8),
    "64th": Fraction(1, 16),
    "128th": Fraction(1, 32),
}


def normalize_tuplet_ticks(source: Path, destination: Path) -> list[dict]:
    if source.suffix == ".mxl":
        with ZipFile(source) as z:
            container = ET.fromstring(z.read("META-INF/container.xml"))
            rootfile = next(e for e in container.iter() if e.tag.endswith("rootfile"))
            data = z.read(rootfile.attrib["full-path"])
    else:
        data = source.read_bytes()
    root = ET.fromstring(data)
    replacements = []
    fixes = []
    divisions_elements = []
    denominator = 1
    for part in root.findall("part"):
        divisions = None
        for measure in part.findall("measure"):
            for item in measure:
                if item.tag == "attributes":
                    d = item.find("divisions")
                    if d is not None:
                        divisions = int(d.text)
                        denominator = lcm(denominator, divisions)
                        divisions_elements.append(d)
                for field in list(item.findall("duration")) + list(
                    item.findall("offset")
                ):
                    if divisions is None:
                        raise ValueError("Missing MusicXML divisions")
                    old = Fraction(field.text) / divisions
                    new = old
                    ratio = item.find("time-modification")
                    if (
                        field.tag == "duration"
                        and item.tag == "note"
                        and ratio is not None
                    ):
                        value = TYPES[item.findtext("type")]
                        dots = len(item.findall("dot"))
                        value *= sum(Fraction(1, 2**i) for i in range(dots + 1))
                        new = value * Fraction(
                            int(ratio.findtext("normal-notes")),
                            int(ratio.findtext("actual-notes")),
                        )
                        if abs(new - old) > Fraction(1, divisions):
                            raise ValueError(
                                f"Tuplet discrepancy larger than one tick in {part.get('id')} measure {measure.get('number')}"
                            )
                        if new != old:
                            fixes.append(
                                {
                                    "part": part.get("id"),
                                    "measure": measure.get("number"),
                                    "before_quarters": str(old),
                                    "after_quarters": str(new),
                                    "reason": "Exact encoded tuplet ratio replaces rounded integer ticks",
                                }
                            )
                    denominator = lcm(denominator, new.denominator)
                    replacements.append((field, new))
    for d in divisions_elements:
        d.text = str(denominator)
    for field, value in replacements:
        ticks = value * denominator
        assert ticks.denominator == 1
        field.text = str(ticks.numerator)
    ET.indent(root)
    destination.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    destination.with_suffix(".repairs.json").write_text(
        json.dumps({"divisions": denominator, "repairs": fixes}, indent=2)
    )
    return fixes
