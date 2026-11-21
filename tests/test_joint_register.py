from music21 import chord, note, stream, tie

from music_arranger.core import _spacing_cost, _timed_notes, optimize_joint_register


def bar(pitches):
    m = stream.Measure(number=1)
    for pitch, duration in pitches:
        m.append(note.Note(pitch, quarterLength=duration))
    return m


def test_spacing_uses_absolute_register_and_compound_interval():
    close_low = [_timed_notes(bar([(p, 4)])) for p in ("E2", "C2")]
    close_high = [_timed_notes(bar([(p, 4)])) for p in ("E4", "C4")]
    assert _spacing_cost(*close_low, (0, 0)) > _spacing_cost(*close_low, (12, 0))
    assert _spacing_cost(*close_low, (0, 0)) > _spacing_cost(*close_high, (0, 0))


def test_spacing_considers_sustained_notes_between_onsets():
    cello = _timed_notes(bar([("E2", 4)]))
    bass = _timed_notes(bar([("C1", 1), ("C2", 3)]))
    assert _spacing_cost(cello, bass, (0, 0)) > 0
    assert _spacing_cost(cello, bass, (0, 0)) == 0.75 * _spacing_cost(
        cello, _timed_notes(bar([("C2", 4)])), (0, 0)
    )


def test_joint_search_protects_partial_chord_tie_and_rhythm():
    score = stream.Score()
    for role in range(2):
        part = stream.Part()
        for number in (1, 2):
            m = stream.Measure(number=number)
            if role == 0:
                n = chord.Chord(["C4", "G4"], quarterLength=4)
                n.notes[0].tie = tie.Tie("start" if number == 1 else "stop")
            else:
                n = note.Note("C2" if number == 1 else "E1", quarterLength=4)
            m.append(n)
            part.append(m)
        score.insert(0, part)
    durations = [n.quarterLength for n in score.recurse().notes]
    changes = []
    optimize_joint_register(score, changes)
    assert (
        score.parts[0].measure(1).notes[0].pitches[0]
        == score.parts[0].measure(2).notes[0].pitches[0]
    )
    assert durations == [n.quarterLength for n in score.recurse().notes]
    assert min(p.midi for n in score.parts[1].recurse().notes for p in n.pitches) >= 28


def test_joint_search_opens_low_third():
    score = stream.Score()
    for pitch in ("E2", "C2"):
        part = stream.Part()
        part.append(bar([(pitch, 4)]))
        score.insert(0, part)
    optimize_joint_register(score, [])
    upper, lower = [p.recurse().notes[0].pitch.midi for p in score.parts]
    assert upper - lower >= 12 or lower >= 48
