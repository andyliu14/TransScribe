from music21 import instrument, meter, note, stream, tie

from music_arranger.transcription import compare_transcription


def test_comparison_finds_pitch_tie_and_rhythm_errors_without_requiring_alignment(
    tmp_path,
):
    score = stream.Score()
    for cls, pitch in [(instrument.Flute, "C5"), (instrument.Violoncello, "C3")]:
        part = stream.Part()
        part.insert(0, cls())
        for number in (1, 2):
            m = stream.Measure(number=number)
            if number == 1:
                m.insert(0, meter.TimeSignature("4/4"))
            n = note.Note(pitch, quarterLength=4)
            n.tie = tie.Tie("start" if number == 1 else "stop")
            m.append(n)
            part.append(m)
        score.insert(0, part)
    original = tmp_path / "reference.musicxml"
    score.write("musicxml", fp=original)
    before = original.read_bytes()
    same = compare_transcription(original, original)
    assert same["matches_checked_notation"]
    assert same["matched_note_events"] == 4
    assert same["review_required"]
    first = score.parts[0].measure(1).notes[0]
    first.transpose(1, inPlace=True)
    first.tie = None
    first.quarterLength = 3
    altered = tmp_path / "candidate.musicxml"
    score.write("musicxml", fp=altered)
    result = compare_transcription(original, altered)
    assert not result["matches_checked_notation"]
    assert not result["checks"]["exact_pitches_onsets_durations_ties"]
    assert result["missing_or_changed_notes"]
    assert result["extra_or_changed_notes"]
    assert original.read_bytes() == before
