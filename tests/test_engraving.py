import xml.etree.ElementTree as ET

from music21 import clef, instrument, meter, note, stream

from music_arranger.core import fingerprint, written_score
from music_arranger.render import _printable_xml


def test_rests_do_not_force_clef_changes_or_change_material():
    score = stream.Score()
    part = stream.Part()
    part.insert(0, instrument.Violoncello())
    for number, event in enumerate(
        [note.Note("G5", quarterLength=4), note.Rest(quarterLength=4)] * 3,
        1,
    ):
        measure = stream.Measure(number=number)
        if number == 1:
            measure.insert(0, meter.TimeSignature("4/4"))
        measure.append(event)
        part.append(measure)
    score.insert(0, part)
    score.atSoundingPitch = True
    before = fingerprint(score)
    written = written_score(score)
    assert fingerprint(score) == before == fingerprint(written)
    clefs = list(written.parts[0].recurse().getElementsByClass(clef.Clef))
    assert len(clefs) == 1
    assert isinstance(clefs[0], clef.TrebleClef)


def test_dense_tempo_print_copy_keeps_endpoints_and_source_immutable(tmp_path):
    path = tmp_path / "score.musicxml"
    directions = "".join(
        f"<direction><direction-type><metronome><beat-unit>quarter</beat-unit>"
        f"<per-minute>{bpm}</per-minute></metronome></direction-type>"
        f'<sound tempo="{bpm}"/></direction>'
        for bpm in [72, 68, 62, 30]
    )
    path.write_text(
        f'<score-partwise><part id="P1"><measure number="5">{directions}'
        "</measure></part></score-partwise>"
    )
    before = path.read_bytes()
    printable, audit = _printable_xml(path)
    assert path.read_bytes() == before
    assert [e.text for e in ET.fromstring(printable).findall(".//per-minute")] == [
        "72",
        "30",
    ]
    assert [entry["printed_bpm"] for entry in audit] == ["68", "62"]
    assert all(entry["measure"] == "5" for entry in audit)
