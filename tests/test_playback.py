import pytest
from music21 import bar, chord, instrument, meter, note, stream, tie

from music_arranger.core import fingerprint, prepare_midi_playback
from music_arranger.playback import audit_midi, write_midi_preview


def ornament_score(chord_grace=False):
    score = stream.Score()
    part = stream.Part()
    part.insert(0, instrument.AltoSaxophone())
    measure = stream.Measure(number=1)
    measure.insert(0, meter.TimeSignature("4/4"))
    # Reproduce the failure: a grace and the main note share A3 at one onset.
    for pitch in ("F#3", "A3"):
        n = chord.Chord([pitch, "C4"]) if chord_grace else note.Note(pitch)
        measure.insert(0, n.getGrace())
    measure.insert(0, note.Note("A3", quarterLength=1))
    measure.insert(1, note.Note("G3", quarterLength=3))
    part.append(measure)
    score.insert(0, part)
    score.atSoundingPitch = True
    return score


def test_auditor_detects_original_stuck_grace_failure(tmp_path):
    path = tmp_path / "broken.mid"
    ornament_score().write("midi", fp=path)
    audit = audit_midi(path)
    assert not audit["valid"]
    assert {"orphan_note_off", "unclosed_note"} <= {e["type"] for e in audit["errors"]}


@pytest.mark.parametrize("chord_grace", [False, True])
def test_graces_finish_before_anchor_without_changing_score_or_bar(
    chord_grace, tmp_path
):
    score = ornament_score(chord_grace)
    before = fingerprint(score)
    playback, changes = prepare_midi_playback(score)
    ns = list(playback.parts[0].recurse().notes)
    assert [(n.offset, n.quarterLength) for n in ns] == [
        (0, 0.125),
        (0.125, 0.125),
        (0.25, 0.75),
        (1, 3),
    ]
    assert playback.highestTime == score.highestTime == 4
    assert changes[0]["borrowed_quarters"] == 0.25
    report = write_midi_preview(score, tmp_path / "fixed.mid")
    assert report["audit"]["valid"]
    assert report["audit"]["tracks"][0]["max_active_notes"] == (2 if chord_grace else 1)
    assert fingerprint(score) == before
    assert score.parts[0].recurse().notes[0].duration.isGrace


def test_repeated_graces_and_tied_anchor_are_realized(tmp_path):
    score = ornament_score()
    m = score.parts[0].measure(1)
    m.notes[2].tie = tie.Tie("start")
    m.notes[3].pitch = note.Note("A3").pitch
    m.notes[3].tie = tie.Tie("stop")
    m.leftBarline = bar.Repeat(direction="start")
    m.rightBarline = bar.Repeat(direction="end", times=2)
    report = write_midi_preview(score, tmp_path / "repeated.mid")
    assert len(report["grace_realizations"]) == 2
    track = report["audit"]["tracks"][0]
    assert track["note_ons"] == 6  # two ornaments + one tied anchor, twice
    assert track["max_active_notes"] == 1
    assert track["longest_note_quarters"] == 3.75


def test_unanchored_grace_fails_without_overwriting_existing_midi(tmp_path):
    score = ornament_score()
    score.parts[0].measure(1).insert(4, note.Note("D4").getGrace())
    path = tmp_path / "existing.mid"
    path.write_bytes(b"previous file")
    with pytest.raises(ValueError, match="one same-voice anchor"):
        write_midi_preview(score, path)
    assert path.read_bytes() == b"previous file"


def test_incoming_tie_is_not_silently_interrupted():
    score = ornament_score()
    score.parts[0].measure(1).notes[2].tie = tie.Tie("stop")
    with pytest.raises(ValueError, match="incoming tie"):
        prepare_midi_playback(score)
