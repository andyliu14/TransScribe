import copy

import pytest
from music21 import instrument, meter, note, stream

from music_arranger.judge import evaluate, spacing_cost


def duo(upper="E2", lower="C2", stagger=False):
    score = stream.Score()
    for role, (pitch, inst) in enumerate(
        ((upper, instrument.Violoncello()), (lower, instrument.Contrabass()))
    ):
        part = stream.Part()
        part.insert(0, inst)
        measure = stream.Measure(number=1)
        measure.insert(0, meter.TimeSignature("4/4"))
        if stagger and role == 0:
            measure.append(note.Rest(quarterLength=1))
            measure.append(note.Note(pitch, quarterLength=3))
        else:
            measure.append(note.Note(pitch, quarterLength=4))
        part.append(measure)
        part.atSoundingPitch = True
        score.insert(0, part)
    score.atSoundingPitch = True
    return score


def test_register_sensitive_third_and_tenth():
    assert spacing_cost(40, 36) > spacing_cost(52, 36)
    assert spacing_cost(40, 36) > spacing_cost(52, 48)
    low = evaluate(duo(), duo())
    tenth = evaluate(duo(), duo("E3"))
    assert (
        low["metrics"]["low_spacing"]["penalty"]
        > tenth["metrics"]["low_spacing"]["penalty"]
    )


def test_sustained_notes_overlap_despite_different_onsets():
    result = evaluate(duo(stagger=True), duo(stagger=True))
    assert result["simultaneous_exposure_quarters"] == 3
    assert result["metrics"]["low_spacing"]["penalty"] == pytest.approx(0.4)


def test_written_bass_is_converted_before_judging(tmp_path):
    score = duo("E3", "C2")
    written = score.toWrittenPitch(inPlace=False)
    path = tmp_path / "duo.musicxml"
    written.write("musicxml", fp=path)
    assert written.parts[1].recurse().notes[0].pitch.midi == 48
    result = evaluate(score, path)
    expected = evaluate(score, score)
    assert result["metrics"] == expected["metrics"]
    assert result["eligible"]


def test_timing_change_fails_gate():
    source = duo()
    candidate = copy.deepcopy(source)
    candidate.parts[0].recurse().notes[0].quarterLength = 3
    assert not evaluate(source, candidate)["eligible"]


def test_octave_change_preserves_timing_gate():
    assert evaluate(duo(), duo("E3"))["eligible"]


def test_deleting_part_cannot_pass_integrity_gate():
    source = duo()
    candidate = copy.deepcopy(source)
    m = candidate.parts[0].measure(1)
    m.replace(m.notes[0], note.Rest(quarterLength=4))
    result = evaluate(source, candidate)
    assert not result["eligible"]
    assert any(
        i["kind"] == "excessive_event_removal"
        for i in result["structural_integrity"]["issues"]
    )


def test_nonmutating_and_deterministic():
    score = duo()
    original = score.parts[1].recurse().notes[0].pitch.midi
    assert evaluate(score, score) == evaluate(score, score)
    assert score.parts[1].recurse().notes[0].pitch.midi == original


def test_uniform_transposition_has_no_contour_penalty_with_overlapping_voices():
    source = duo("E4", "C3")
    m = source.parts[0].measure(1)
    m.insert(0, note.Note("C4", quarterLength=2))
    m.insert(2, note.Note("G4", quarterLength=2))
    shifted = source.transpose(-12)
    assert evaluate(source, shifted)["metrics"]["contour_change"]["penalty"] == 0
