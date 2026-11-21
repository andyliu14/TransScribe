from music21 import instrument, meter, note, stream, tie

from music_arranger.experiment import material_signature


def test_material_gate_allows_octaves_but_rejects_deletion_retiming_and_retie(tmp_path):
    score = stream.Score()
    for inst in (instrument.Violoncello(), instrument.Contrabass()):
        part = stream.Part()
        part.insert(0, inst)
        m = stream.Measure(number=1)
        m.append(meter.TimeSignature("4/4"))
        m.append(note.Note("C3", quarterLength=1))
        m.append(note.Note("D3", quarterLength=1))
        m.append(note.Rest(quarterLength=2))
        part.append(m)
        score.insert(0, part)
    score.atSoundingPitch = True

    def signature(s, name):
        path = tmp_path / (name + ".musicxml")
        s.toWrittenPitch().write("musicxml", fp=path)
        return material_signature(path)

    original = signature(score, "original")
    changed = score.transpose(12)
    assert signature(changed, "octaves") == original
    changed.parts[0].measure(1).notes[0].pitch.transpose(1, inPlace=True)
    assert signature(changed, "semitone") != original
    changed = score.transpose(0)
    changed.parts[0].measure(1).notes[0].tie = tie.Tie("start")
    assert signature(changed, "retie") != original
    changed = score.transpose(0)
    changed.parts[0].measure(1).notes[0].quarterLength = 0.5
    assert signature(changed, "retimed") != original
    changed = score.transpose(0)
    m = changed.parts[0].measure(1)
    m.remove(m.notes[0])
    assert signature(changed, "deleted") != original
