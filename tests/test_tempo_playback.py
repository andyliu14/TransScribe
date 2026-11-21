import copy
from pathlib import Path

import pytest
from music21 import articulations, expressions, instrument, meter, note, stream, tempo

from music_arranger.core import fingerprint, interpret_playback_tempos, load_score
from music_arranger.playback import write_midi_preview


def test_sound_tempo_is_not_replaced_by_printed_half_note_tempo(tmp_path):
    xml = """<?xml version="1.0"?>
    <score-partwise version="4.0"><part-list><score-part id="P1">
    <part-name>Cello</part-name></score-part></part-list><part id="P1">
    <measure number="1"><attributes><divisions>4</divisions><time><beats>4</beats>
    <beat-type>4</beat-type></time></attributes><direction><direction-type>
    <metronome><beat-unit>half</beat-unit><beat-unit-dot/><per-minute>80</per-minute>
    </metronome></direction-type><sound tempo="50"/></direction>
    <note><pitch><step>C</step><octave>3</octave></pitch><duration>16</duration><type>whole</type></note>
    </measure></part></score-partwise>"""
    path = tmp_path / "tempo.musicxml"
    path.write_text(xml)
    score = load_score(path, expected_parts=None)
    mark = score.parts[0].recurse().getElementsByClass(tempo.MetronomeMark)[0]
    assert mark.number == 80 and mark.referent.quarterLength == 3
    assert mark.getQuarterBPM() == 50
    output = tmp_path / "roundtrip.musicxml"
    score.write("musicxml", fp=output)
    reloaded = load_score(output, expected_parts=None)
    assert (
        reloaded.parts[0]
        .recurse()
        .getElementsByClass(tempo.MetronomeMark)[0]
        .getQuarterBPM()
        == 50
    )
    report = write_midi_preview(score, tmp_path / "score.mid")
    assert report["audit"]["tempo_events"][0]["quarter_bpm"] == 50
    assert path.read_text() == xml


def rit_score():
    s = stream.Score()
    p = stream.Part()
    p.insert(0, instrument.Violoncello())
    m = stream.Measure(number=1)
    m.insert(0, meter.TimeSignature("4/4"))
    m.insert(0, note.Note("C3", quarterLength=4))
    m.insert(0, tempo.MetronomeMark(number=120))
    m.insert(1, expressions.TextExpression("rit."))
    m.insert(3, tempo.MetronomeMark(numberSounding=60))
    p.append(m)
    m2 = stream.Measure(number=2)
    m2.insert(0, note.Note("D3", quarterLength=4))
    m2.insert(0, tempo.MetronomeMark(number=100))
    m2.insert(0, expressions.TextExpression("a tempo rubato"))
    p.append(m2)
    s.insert(0, p)
    s.atSoundingPitch = True
    return s


def test_smooth_rit_preserves_anchors_and_does_not_invent_rubato(tmp_path):
    score = rit_score()
    score.parts[0].measure(2).notes[0].expressions.append(expressions.Fermata())
    score.parts[0].measure(2).notes[0].articulations.append(articulations.Caesura())
    before = fingerprint(score)
    control = write_midi_preview(score, tmp_path / "encoded.mid")
    smooth = write_midi_preview(score, tmp_path / "smooth.mid", tempo_mode="smooth-rit")
    assert len(control["audit"]["tempo_events"]) == 3
    events = smooth["audit"]["tempo_events"]
    by_offset = {e["quarter_offset"]: e["quarter_bpm"] for e in events}
    assert by_offset[0] == 120 and by_offset[3] == 60
    assert by_offset[2] == 80  # halfway in seconds/quarter, not BPM
    assert by_offset[4] == 100
    assert all(60 <= e["quarter_bpm"] <= 120 for e in events if e["quarter_offset"] < 4)
    assert (
        smooth["tempo_interpretation"]["directions"][1]["status"]
        == "uninterpreted_text"
    )
    assert {
        r["text"]
        for r in smooth["tempo_interpretation"]["directions"]
        if r["status"] == "uninterpreted_hold_or_pause"
    } == {"Fermata", "Caesura"}
    assert fingerprint(score) == before
    assert len(score.recurse().getElementsByClass(tempo.MetronomeMark)) == 3


def test_unknown_tempo_mode_fails():
    with pytest.raises(ValueError, match="Tempo mode"):
        interpret_playback_tempos(copy.deepcopy(rit_score()), "guess-everything")


@pytest.mark.skipif(
    not Path("sources/handel-halvorsen.musicxml").exists(), reason="Fetch source first"
)
def test_full_source_hidden_tempos_and_rubato_location():
    score = load_score("sources/handel-halvorsen.musicxml")
    repairs = score.editorial["sound_tempo_repairs"]
    assert [(r["measure"], r["sound_quarter_bpm"]) for r in repairs] == [
        (76, 50),
        (88, 60),
        (95, 134),
        (95, 140),
        (95, 145),
        (95, 163),
    ]
    upper = score.parts[0]
    assert not upper.measure(29).recurse().getElementsByClass(tempo.MetronomeMark)
    assert any(
        "rubato" in t.content
        for t in upper.measure(33)
        .recurse()
        .getElementsByClass(expressions.TextExpression)
    )
