import copy
from pathlib import Path

import pytest
from music21 import instrument, meter, note, stream, tie

from music_arranger.core import (
    ALTO,
    BASS,
    CELLO,
    arrange_trio,
    fingerprint,
    load_score,
    optimize_register,
)
from music_arranger.trio_judge import evaluate_trio


def phrase():
    part = stream.Part()
    for number, pitches in enumerate(
        [("E5",), ("E5",), ("E5", "G5", "B4"), ("A5",)], 1
    ):
        m = stream.Measure(number=number)
        if number == 1:
            m.insert(0, meter.TimeSignature("3/4"))
        for pitch in pitches:
            n = note.Note(pitch, quarterLength=3 / len(pitches))
            if number < 3:
                n.tie = tie.Tie("start" if number == 1 else "stop")
            m.append(n)
        part.append(m)
    return part


@pytest.mark.parametrize("profile", [ALTO, CELLO, BASS])
def test_destination_register_does_not_depend_on_source_octave(profile):
    outputs = []
    for octave in (-24, 0, 24):
        part = phrase()
        part.transpose(octave, inPlace=True)
        before = [
            (n.quarterLength, n.tie.type if n.tie else None)
            for n in part.recurse().notes
        ]
        optimize_register(part, profile, [])
        ns = list(part.recurse().notes)
        assert before == [(n.quarterLength, n.tie.type if n.tie else None) for n in ns]
        assert all(profile.low <= n.pitch.midi <= profile.high for n in ns)
        assert ns[0].pitch == ns[1].pitch
        outputs.append([n.pitch.midi for n in ns])
    assert outputs[0] == outputs[1] == outputs[2]


def test_sparse_sustained_bass_is_not_left_in_solo_register():
    part = phrase()
    changes = []
    optimize_register(part, BASS, changes)
    assert max(n.pitch.midi for n in part.recurse().notes) <= BASS.preferred_high
    assert any(abs(c["semitones"]) > 12 for c in changes)


@pytest.mark.skipif(
    not Path("sources/themes/hedwigs-theme/source.musicxml").exists(),
    reason="Fetch reviewed theme source first",
)
def test_hedwig_reduction_retains_material_and_avoids_high_bass_tessitura():
    source = load_score("sources/themes/hedwigs-theme/source.musicxml")
    before = fingerprint(source)
    score, report = arrange_trio(source)
    assert fingerprint(source) == before
    metrics = report["independent_evaluation"]["metrics"]
    assert metrics["retained_pitches"] == metrics["source_pitches"] == 171
    assert metrics["register_usage"][2]["above_preferred_quarters"] == 0
    assert not metrics["tessitura_needs_review"]
    # Range-valid solo writing must still receive a practical-register warning.
    high = copy.deepcopy(score)
    high.parts[2].transpose(12, inPlace=True)
    review = evaluate_trio(source, high)
    assert review["gates"]["in_range"]
    assert review["metrics"]["register_usage"][2]["needs_review"]
    assert any(i["rule"] == "tessitura_review" for i in review["issues"])
    assert not review["performance_ready"]


def test_reordered_mixed_sextet_in_high_register_gets_target_tessituras():
    score = stream.Score()
    for cls, pitch in [
        (instrument.Flute, "E7"),
        (instrument.Clarinet, "C6"),
        (instrument.Violin, "G6"),
        (instrument.Viola, "B5"),
        (instrument.Piano, "E6"),
        (instrument.Violoncello, "C5"),
    ]:
        part = stream.Part()
        part.insert(0, cls())
        for i in (1, 2):
            m = stream.Measure(number=i)
            if i == 1:
                m.insert(0, meter.TimeSignature("3/4"))
            m.append(note.Note(pitch, quarterLength=3))
            part.append(m)
        score.insert(0, part)
    score.atSoundingPitch = True
    _, report = arrange_trio(score)
    review = report["independent_evaluation"]
    assert review["eligible"]
    assert not review["metrics"]["tessitura_needs_review"]
