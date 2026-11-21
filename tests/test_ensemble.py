"""Source-count, voice, transposition and notation regressions for trio reduction."""

import copy
from collections import Counter

import pytest
from music21 import (
    bar,
    chord,
    instrument,
    layout,
    metadata,
    meter,
    note,
    spanner,
    stream,
    tempo,
    tie,
)

from music_arranger.core import arrange_trio, export, fingerprint, load_score
from music_arranger.trio_judge import evaluate_trio


def ensemble(count=6):
    score = stream.Score()
    # Deliberately unordered: part position is not a musical role.
    for i, pitch in enumerate(["G3", "E5", "C3", "C5", "E4", "G4"][:count]):
        part = stream.Part(id=f"source-{i}")
        part.partName = f"Source {i + 1}"
        part.insert(0, instrument.Clarinet() if i == 0 else instrument.Piano())
        for number in range(1, 9):
            m = stream.Measure(number=number)
            if number == 1:
                m.insert(0, meter.TimeSignature("3/4"))
                m.insert(0, tempo.MetronomeMark(number=90))
            m.append(note.Note(pitch, quarterLength=3))
            if number == 8:
                m.rightBarline = bar.Repeat(direction="end")
            part.append(m)
        score.insert(0, part)
    score.atSoundingPitch = True
    return score


@pytest.mark.parametrize("count", [1, 2, 3, 4, 6])
def test_arbitrary_source_count_and_material_audit(count, tmp_path):
    source = ensemble(count)
    before = fingerprint(source)
    candidate, report = arrange_trio(source)
    assert fingerprint(source) == before
    assert report["independent_evaluation"]["eligible"]
    assert len(candidate.parts) == 3
    assert all(len(p.getElementsByClass(stream.Measure)) == 8 for p in candidate.parts)
    assert len(report["source_assignments"]) == sum(
        len(p.recurse().notes) for p in candidate.parts
    )
    for assignment in report["source_assignments"]:
        assert assignment["semitones"] % 12 == 0
    omitted = sum(
        c["count"] for c in report["changes"] if c["action"] == "omit_source_pitch"
    )
    assert omitted + len(report["source_assignments"]) == count * 8
    export(candidate, report, tmp_path, render=False)
    assert evaluate_trio(source, tmp_path / "score.musicxml")["eligible"]
    for i, name in enumerate(report["part_files"]):
        separate = load_score(tmp_path / f"{name}.musicxml", expected_parts=None)
        assert fingerprint(separate)[0] == fingerprint(candidate)[i]


def test_source_order_does_not_define_lead_or_bass():
    source = ensemble()
    _, report = arrange_trio(source, "sax-led")
    selected = {
        a["destination"]: a["pitch"]
        for a in report["source_assignments"]
        if a["measure"] == 1
    }
    assert selected["alto-saxophone"] == "E5"
    assert selected["double-bass"] == "C3"
    assert report["independent_evaluation"]["metrics"]["pitch_retention_needs_review"]
    assert report["independent_evaluation"]["eligible"]


def test_transposing_source_is_read_at_sounding_pitch(tmp_path):
    source = ensemble(3)
    written = source.toWrittenPitch(inPlace=False)
    path = tmp_path / "clarinet.musicxml"
    written.write("musicxml", fp=path)
    loaded = load_score(path, expected_parts=None)
    assert fingerprint(loaded) == fingerprint(source)
    _, report = arrange_trio(loaded)
    assert report["independent_evaluation"]["gates"][
        "no_new_pitch_class_onset_duration"
    ]


def test_grand_staff_sound_tempo_and_source_immutability(tmp_path):
    score = stream.Score()
    for i, pitch in enumerate(("C5", "C3")):
        part = stream.PartStaff(id=f"staff-{i}")
        part.insert(0, instrument.Piano())
        m = stream.Measure(number=1)
        m.insert(0, meter.TimeSignature("3/4"))
        m.append(note.Note(pitch, quarterLength=3))
        if i == 0:
            mark = tempo.MetronomeMark(number=90)
            mark.numberSounding = 65
            m.insert(0, mark)
        part.append(m)
        score.insert(0, part)
    score.insert(
        0,
        layout.StaffGroup(
            list(score.parts), name="Piano", symbol="brace", barTogether=True
        ),
    )
    path = tmp_path / "piano.musicxml"
    score.write("musicxml", fp=path)
    before = path.read_bytes()
    loaded = load_score(path, expected_parts=None)
    assert len(loaded.parts) == 2
    assert (
        loaded.parts[0]
        .recurse()
        .getElementsByClass(tempo.MetronomeMark)[0]
        .getQuarterBPM()
        == 65
    )
    candidate, report = arrange_trio(loaded)
    export(candidate, report, tmp_path / "trio", render=False)
    assert report["tempo_roundtrip_verified"]
    assert path.read_bytes() == before


def test_chord_ranks_cannot_break_ties_and_explicit_voices_are_distinct():
    score = stream.Score()
    part = stream.Part()
    for number in (1, 2):
        m = stream.Measure(number=number)
        if number == 1:
            m.insert(0, meter.TimeSignature("3/4"))
        v = stream.Voice(id="melody")
        c = chord.Chord(["C5", "E5"] if number == 1 else ["C5", "A4"], quarterLength=3)
        next(n for n in c.notes if n.pitch.nameWithOctave == "C5").tie = tie.Tie(
            "start" if number == 1 else "stop"
        )
        v.append(c)
        m.insert(0, v)
        bass = stream.Voice(id="bass")
        bass.append(note.Note("C3", quarterLength=3))
        m.insert(0, bass)
        part.append(m)
    score.insert(0, part)
    score.atSoundingPitch = True
    _, report = arrange_trio(score)
    assert report["independent_evaluation"]["eligible"]
    assert report["independent_evaluation"]["metrics"]["retained_pitches"] == 6


def test_role_selection_can_follow_another_source_at_safe_phrase_boundary():
    source = ensemble(4)
    # Initial lead becomes silent; another part takes over with a slurred phrase.
    for m in list(source.parts[1].getElementsByClass(stream.Measure))[4:]:
        m.replace(m.notes[0], note.Rest(quarterLength=3))
    lead = source.parts[3]
    lead.insert(0, spanner.Slur(lead.measure(5).notes[0], lead.measure(8).notes[0]))
    _, report = arrange_trio(source, "sax-led")
    owners = {
        a["measure"]: a["part"]
        for a in report["source_assignments"]
        if a["destination"] == "alto-saxophone"
    }
    assert owners[1] == 2 and owners[5] == 4
    assert report["independent_evaluation"]["eligible"]


def test_percussion_is_audited_and_cannot_become_fake_pitches():
    source = ensemble(3)
    percussion = copy.deepcopy(source.parts[0])
    for m in percussion.getElementsByClass(stream.Measure):
        m.replace(m.notes[0], note.Unpitched(quarterLength=3))
    source.insert(0, percussion)
    _, report = arrange_trio(source)
    assert (
        len([c for c in report["changes"] if c["action"] == "omit_unpitched_event"])
        == 8
    )
    assert report["independent_evaluation"]["eligible"]


def test_control_and_relay_keep_same_selected_material():
    source = ensemble(6)
    candidates = [
        arrange_trio(source, v)[0] for v in ("sax-led", "relay-four", "relay-two")
    ]

    def material(s):
        return Counter(
            (m.number, n.offset, n.quarterLength, n.pitch.pitchClass)
            for p in s.parts
            for m in p.getElementsByClass(stream.Measure)
            for n in m.recurse().notes
        )

    assert material(candidates[0]) == material(candidates[1]) == material(candidates[2])


def test_meter_tempo_and_repeat_endings_survive_an_unselected_staff():
    source = ensemble(6)
    for p in source.parts:
        for m in p.getElementsByClass(stream.Measure):
            for mark in list(
                m.getElementsByClass((meter.TimeSignature, tempo.MetronomeMark))
            ):
                m.remove(mark)
            m.rightBarline = None
    # Source 6 is not selected in this fixture, but its conductor instructions
    # apply to the whole ensemble.
    conductor = source.parts[5]
    conductor.measure(1).insert(0, meter.TimeSignature("3/4"))
    conductor.measure(1).insert(0, tempo.MetronomeMark(number=76))
    conductor.measure(7).rightBarline = bar.Repeat(direction="end")
    conductor.insert(0, spanner.RepeatBracket(conductor.measure(7), number=1))
    conductor.insert(0, spanner.RepeatBracket(conductor.measure(8), number=2))
    candidate, report = arrange_trio(source)
    assert report["independent_evaluation"]["eligible"]
    for p in candidate.parts:
        assert p.measure(1).timeSignature.ratioString == "3/4"
        assert (
            p.recurse().getElementsByClass(tempo.MetronomeMark)[0].getQuarterBPM() == 76
        )
        assert len(p.spannerBundle.getByClass(spanner.RepeatBracket)) == 2
    corrupted = copy.deepcopy(candidate)
    ending = corrupted.parts[0].spannerBundle.getByClass(spanner.RepeatBracket)[0]
    ending.activeSite.remove(ending)
    assert not evaluate_trio(source, corrupted)["gates"]["repeat_endings_preserved"]


def test_conflicting_meters_are_not_silently_rewritten():
    source = ensemble(3)
    source.parts[1].measure(1).timeSignature = meter.TimeSignature("6/8")
    with pytest.raises(ValueError, match="Conflicting source meters"):
        arrange_trio(source)


def test_source_credits_survive_arranging_and_export(tmp_path):
    source = ensemble(2)
    source.metadata = metadata.Metadata(title="Source edition", composer="Composer")
    source.metadata.copyright = "Source notice retained"
    source.metadata.addContributor(metadata.Contributor(name="Editor", role="arranger"))
    candidate, report = arrange_trio(source)
    export(candidate, report, tmp_path, render=False)
    reloaded = load_score(tmp_path / "score.musicxml", expected_parts=None)
    assert str(reloaded.metadata.copyright) == "Source notice retained"
    assert reloaded.metadata.composer == "Composer"
    assert any(c.name == "Editor" for c in reloaded.metadata.contributors)
    assert source.metadata.title == "Source edition"
