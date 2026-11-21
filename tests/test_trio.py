import copy
from collections import Counter
from pathlib import Path

import pytest
from music21 import (
    chord,
    clef,
    converter,
    instrument,
    key,
    meter,
    note,
    spanner,
    stream,
    tempo,
    tie,
)

from music_arranger.core import (
    TRIO_VARIANTS,
    arrange_trio,
    export,
    fingerprint,
    load_score,
    wind_direction,
    written_score,
)
from music_arranger.trio_judge import evaluate_trio


def source_duet():
    s = stream.Score()
    for i, inst in enumerate((instrument.Violin(), instrument.Viola())):
        p = stream.Part()
        p.insert(0, inst)
        for number in range(1, 7):
            m = stream.Measure(number=number)
            if number == 1:
                m.insert(0, meter.TimeSignature("4/4"))
                m.insert(0, key.Key("g"))
                m.insert(0, tempo.MetronomeMark(number=80))
            for j in range(4):
                m.append(
                    chord.Chord(["B-4", "D5"], quarterLength=1)
                    if i == 0 and number <= 2
                    else note.Note("D5" if i == 0 else "G3", quarterLength=1)
                )
            p.append(m)
        s.insert(0, p)
    s.atSoundingPitch = True
    return s


def material(s):
    return Counter(
        (
            m.number,
            float(n.getOffsetInHierarchy(m)),
            float(n.quarterLength),
            p.pitchClass,
        )
        for part in s.parts
        for m in part.getElementsByClass(stream.Measure)
        for n in m.recurse().notes
        for p in n.pitches
    )


def test_relay_changes_player_without_changing_material_and_creates_wind_gaps():
    source = source_duet()
    before = fingerprint(source)
    control, _ = arrange_trio(source, "sax-led")
    relay, report = arrange_trio(source, "relay-two")
    assert fingerprint(source) == before
    assert material(control) == material(relay)
    assert not list(relay.parts[0].measure(3).recurse().notes)
    assert list(relay.parts[1].measure(3).recurse().notes)
    assert report["independent_evaluation"]["eligible"]
    assert (
        evaluate_trio(source, relay)["metrics"]["max_wind_run_seconds"]
        < evaluate_trio(source, control)["metrics"]["max_wind_run_seconds"]
    )


def test_alto_written_pitch_key_and_separate_parts_roundtrip(tmp_path):
    score, report = arrange_trio(source_duet())
    export(score, report, tmp_path, render=False)
    written = converter.parse(tmp_path / "alto-saxophone.musicxml")
    assert (
        written.parts[0].recurse().notes[0].pitch.midi
        - score.parts[0].recurse().notes[0].pitch.midi
        == 9
    )
    assert (
        written.parts[0].recurse().getElementsByClass(key.KeySignature)[0].sharps == 1
    )
    assert all(
        isinstance(c, clef.TrebleClef)
        for c in written.recurse().getElementsByClass(clef.Clef)
    )
    for i, name in enumerate(report["part_files"]):
        extracted = converter.parse(tmp_path / f"{name}.musicxml").toSoundingPitch(
            inPlace=False
        )
        assert fingerprint(extracted)[0] == fingerprint(score)[i]
    assert len(load_score(tmp_path / "score.musicxml", expected_parts=None).parts) == 3


def test_stale_source_chord_beams_are_removed():
    score, _ = arrange_trio(source_duet())
    n = score.parts[0].recurse().notes[0]
    n.beams.fill("eighth", "start")
    written = written_score(score)
    assert not written.parts[0].recurse().notes[0].beams.beamsList
    assert wind_direction("spiccato") == "lightly tongued"
    assert wind_direction("2te mal ponticello") is None
    assert wind_direction("dolce") == "dolce"


def test_handoff_respects_slur_and_tie():
    source = source_duet()
    upper = source.parts[0]
    span = spanner.Slur(
        upper.measure(2).recurse().notes[0], upper.measure(3).recurse().notes[-1]
    )
    upper.insert(0, span)
    upper.measure(2).recurse().notes[-1].notes[-1].tie = tie.Tie("start")
    upper.measure(3).recurse().notes[0].tie = tie.Tie("stop")
    _score, report = arrange_trio(source, "relay-two")
    route = [x for x in report["changes"] if x["action"] == "route_phrase"]
    assert route[2]["melody"] == "alto-saxophone"
    assert route[3]["melody"] == "cello"
    assert report["independent_evaluation"]["gates"]["ties_connected"]
    assert report["independent_evaluation"]["gates"]["spanner_endpoints_present"]


def test_wide_source_run_fits_ordinary_alto_range_with_audit():
    source = source_duet()
    m = source.parts[0].measure(1)
    for n, pitch in zip(m.recurse().notes, ["G3", "C5", "G5", "D7"]):
        m.replace(n, note.Note(pitch, quarterLength=1))
    score, report = arrange_trio(source, "sax-led")
    assert all(49 <= n.pitch.midi <= 80 for n in score.parts[0].recurse().notes)
    assert any(c["action"] == "line_octave_repair" for c in report["changes"])
    assert report["independent_evaluation"]["gates"][
        "no_new_pitch_class_onset_duration"
    ]


def test_judge_rejects_material_and_tie_corruption():
    source = source_duet()
    score, _ = arrange_trio(source)
    altered = copy.deepcopy(score)
    altered.parts[0].recurse().notes[0].transpose(1, inPlace=True)
    assert not evaluate_trio(source, altered)["gates"][
        "no_new_pitch_class_onset_duration"
    ]
    altered = copy.deepcopy(score)
    altered.parts[0].recurse().notes[0].tie = tie.Tie("stop")
    assert not evaluate_trio(source, altered)["gates"]["ties_connected"]


@pytest.mark.parametrize("variant", TRIO_VARIANTS)
@pytest.mark.skipif(
    not Path("sources/handel-halvorsen.musicxml").exists(),
    reason="Fetch full source first",
)
def test_full_trio_integrity(variant, tmp_path):
    source = load_score("sources/handel-halvorsen.musicxml")
    before = fingerprint(source)
    score, report = arrange_trio(source, variant)
    assert fingerprint(source) == before
    assert report["independent_evaluation"]["eligible"], report[
        "independent_evaluation"
    ]["gates"]
    assert [len(p.getElementsByClass(stream.Measure)) for p in score.parts] == [
        98,
        98,
        98,
    ]
    export(score, report, tmp_path, render=False)
    judged = evaluate_trio(source, tmp_path / "score.musicxml")
    assert judged["eligible"], judged["gates"]
    assert report["midi_playback"]["audit"]["valid"]
    assert report["midi_playback"]["grace_realizations"]
    assert all(
        t["max_active_notes"] == 1 for t in report["midi_playback"]["audit"]["tracks"]
    )
