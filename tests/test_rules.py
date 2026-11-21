import math

import pytest
from music21 import (
    articulations,
    chord,
    expressions,
    instrument,
    meter,
    note,
    stream,
    tempo,
)

from music_arranger.core import optimize_joint_register
from music_arranger.rules import (
    ScoreClock,
    audit_rules,
    bowing_modes,
    harmonic_notation,
    partial_roughness,
    proximity_cost,
    rule_catalog,
    rule_hash,
    sonority_cost,
    spectral_roughness,
    string_assignments,
)


def duo(upper, lower):
    score = stream.Score()
    for inst, pitches in (
        (instrument.Violoncello(), upper),
        (instrument.Contrabass(), lower),
    ):
        part = stream.Part()
        part.insert(0, inst)
        m = stream.Measure(number=1)
        m.insert(0, meter.TimeSignature("4/4"))
        m.insert(0, chord.Chord(pitches, quarterLength=4))
        part.append(m)
        score.insert(0, part)
    score.atSoundingPitch = True
    return score


def test_roughness_distinguishes_register_and_compound_intervals():
    assert spectral_roughness((36, 40)) > spectral_roughness((36, 52))
    assert spectral_roughness((36, 40)) > spectral_roughness((60, 64))
    assert spectral_roughness((48, 49)) > spectral_roughness((48, 48))
    assert partial_roughness(440, 1, 440, 0.5) == 0
    assert partial_roughness(440, 1, 480, 0.5) == partial_roughness(480, 0.5, 440, 1)
    assert partial_roughness(100, 1, 10000, 1) < 1e-10
    # Peak predicted by the published exponential fit, at a positive separation.
    scale = 0.24 / (0.0207 * 440 + 18.96)
    peak_delta = math.log(5.75 / 3.51) / (5.75 - 3.51) / scale
    peak = partial_roughness(440, 1, 440 + peak_delta, 1)
    assert peak > partial_roughness(440, 1, 440 + peak_delta / 2, 1)
    assert peak > partial_roughness(440, 1, 440 + peak_delta * 2, 1)


def test_full_sonority_checks_interior_chords_and_held_notes():
    upper = [(0, 4, chord.Chord(["E2", "C5"]))]
    lower = [(1, 2, note.Note("C2"))]
    full = sonority_cost([upper, lower], (0, 0))
    outer_only = sonority_cost([[(0, 4, note.Note("C5"))], lower], (0, 0))
    assert full > outer_only
    # Moving the held bass to the start leaves its two-quarter exposure equal.
    assert full == pytest.approx(
        sonority_cost([upper, [(0, 2, note.Note("C2"))]], (0, 0))
    )


def test_in_range_double_stops_can_fail_adjacent_string_assignment():
    assert not string_assignments((40, 41), 0)  # Both below cello G string.
    assert string_assignments((43, 50), 0)
    assert not string_assignments((29, 31), 1)  # Both below bass A string.
    assert string_assignments((33, 38), 1)


def test_literature_search_repairs_unassignable_ordinary_double_stop():
    score = duo(["E2", "F2"], ["C2"])
    optimize_joint_register(score, [], literature=True)
    ps = tuple(p.midi for p in score.parts[0].recurse().notes[0].pitches)
    assert string_assignments(ps, 0)


def test_tempo_map_integrates_changes_and_dotted_beats():
    score = duo(["C3"], ["C2"])
    m = score.parts[0].measure(1)
    m.insert(0, tempo.MetronomeMark(number=60))
    m.insert(2, tempo.MetronomeMark(number=60, referent=note.Note(quarterLength=1.5)))
    clock = ScoreClock(score)
    assert clock.seconds(0, 4) == pytest.approx(2 + 4 / 3)
    assert clock.seconds(1, 3) == pytest.approx(1 + 2 / 3)


def test_streaming_rule_respects_long_silence_and_tone_duration():
    assert proximity_cost(12, 0.1, 0) > 0
    assert proximity_cost(12, 0.8, 0) == 0
    assert proximity_cost(12, 0.1, 0.8) == 0
    assert proximity_cost(2, 0.1, 0) == 0


def test_harmonics_are_not_certified_as_ordinary_double_stops():
    c = chord.Chord(["E2", "F2"])
    c.notes[1].notehead = "diamond"
    assert harmonic_notation(c)
    c = chord.Chord(["E2", "F2"])
    c.articulations.append(articulations.OpenString())
    assert harmonic_notation(c)  # Source circle may be a harmonic, not a tuning claim.


def test_pizzicato_context_is_not_treated_as_bowed_adjacency():
    score = duo(["G3"], ["C2"])
    part = score.parts[0]
    m = part.measure(1)
    m.notes[0].quarterLength = 1
    m.insert(1, expressions.TextExpression("pizz."))
    m.insert(1, note.Note("A3", quarterLength=1))
    m.insert(2, expressions.TextExpression("arco"))
    m.insert(2, note.Note("B3", quarterLength=2))
    modes = bowing_modes(part)
    assert [modes[id(n)] for n in m.notes] == ["arco", "pizzicato", "arco"]


def test_audit_sounding_pitch_invariant_and_citations_present(tmp_path):
    from music21 import converter

    score = duo(["G3"], ["C2"])
    original = audit_rules(score)
    path = tmp_path / "written.musicxml"
    score.toWrittenPitch().write("musicxml", fp=path)
    restored = audit_rules(converter.parse(path))
    assert original["metrics"] == restored["metrics"]
    assert restored["rulebook_sha256"] == rule_hash()
    assert all(
        s["url"].startswith("https://") for s in rule_catalog()["sources"].values()
    )
    assert restored["tempo"]["fallback_used_at_start"]
