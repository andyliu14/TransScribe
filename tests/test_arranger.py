from pathlib import Path

import pytest
from music21 import chord, converter, instrument, key, meter, note, stream, tempo, tie

from music_arranger.core import (
    BASS,
    VARIANTS,
    arrange,
    export,
    fingerprint,
    load_score,
    optimize_register,
    transpose_notation,
)


@pytest.fixture
def duo():
    s = stream.Score()
    for inst, root in [(instrument.Violin(), "G4"), (instrument.Viola(), "G3")]:
        p = stream.Part()
        p.insert(0, inst)
        for i in range(1, 3):
            m = stream.Measure(number=i)
            if i == 1:
                m.append(meter.TimeSignature("4/4"))
                m.append(key.Key("g"))
                if isinstance(inst, instrument.Violin):
                    m.insert(0, tempo.MetronomeMark(number=88))
            m.append(note.Note(root, quarterLength=2))
            m.append(
                chord.Chord([root, "D5" if root == "G4" else "D4"], quarterLength=1)
            )
            m.append(note.Rest(quarterLength=1))
            p.append(m)
        s.insert(0, p)
    s.atSoundingPitch = True
    return s


def test_notation_transposition_is_nonmutating(duo):
    before = fingerprint(duo)
    changed = transpose_notation(duo, semitones=-2)
    assert fingerprint(duo) == before
    assert changed.parts[0].recurse().notes[0].pitch.nameWithOctave == "F4"
    assert (
        changed.parts[0].recurse().getElementsByClass(key.KeySignature)[0].sharps == -4
    )


@pytest.mark.parametrize("variant", VARIANTS)
def test_score_and_extracted_parts_roundtrip(duo, tmp_path, variant):
    score, report = arrange(duo, variant)
    export(score, report, tmp_path, render=False)
    assert report["roundtrip_verified"]
    bass = converter.parse(tmp_path / "double-bass.musicxml")
    assert bass.recurse().getElementsByClass(tempo.MetronomeMark)[0].number == 88
    written = bass.parts[0].recurse().notes[0].pitches[0].midi
    sounding = bass.toSoundingPitch().parts[0].recurse().notes[0].pitches[0].midi
    assert written - sounding == 12
    assert sounding == score.parts[1].recurse().notes[0].pitches[0].midi
    from music21 import midi

    mf = midi.MidiFile()
    mf.open(tmp_path / "score.mid")
    mf.read()
    mf.close()
    bass_notes = [
        e.pitch
        for e in mf.tracks[-1].events
        if e.type == midi.ChannelVoiceMessages.NOTE_ON and e.velocity > 0
    ]
    assert bass_notes[0] == sounding
    assert len(list((tmp_path).glob("*.musicxml"))) == 3


def test_octave_optimization_preserves_tied_pitch():
    p = stream.Part()
    for i in (1, 2):
        m = stream.Measure(number=i)
        n = note.Note("C5", quarterLength=4)
        n.tie = tie.Tie("start" if i == 1 else "stop")
        m.append(n)
        p.append(m)
    changes = []
    optimize_register(p, BASS, changes)
    assert p.recurse().notes[0].pitch == p.recurse().notes[1].pitch
    assert p.recurse().notes[0].pitch.midi <= BASS.high


def test_rejects_piano_adaptation(tmp_path):
    s = stream.Score()
    p = stream.Part()
    p.append(note.Note("C4"))
    s.insert(0, p)
    path = tmp_path / "piano.musicxml"
    s.write("musicxml", fp=path)
    with pytest.raises(ValueError, match="exactly two parts"):
        load_score(path)


def test_renderer_produces_readable_pdf(duo, tmp_path):
    from pypdf import PdfReader

    score, report = arrange(duo, "chamber")
    export(score, report, tmp_path, render=True)
    assert len(PdfReader(tmp_path / "score.pdf").pages) >= 1
    assert "Cello" in PdfReader(tmp_path / "score.pdf").pages[0].extract_text()


@pytest.mark.skipif(
    not Path("sources/handel-halvorsen.musicxml").exists(),
    reason="Fetch source first",
)
def test_full_passacaglia_integrity(tmp_path):
    source = load_score("sources/handel-halvorsen.musicxml")
    assert len(source.parts) == 2
    assert len(source.parts[0].getElementsByClass(stream.Measure)) >= 98
    for variant in VARIANTS:
        score, report = arrange(source, variant)
        assert score.highestTime == source.highestTime
        if variant == "reference":
            assert [len(p.recurse().notesAndRests) for p in score.parts] == [
                len(p.recurse().notesAndRests) for p in source.parts
            ]
        else:
            targets = (
                list(score.parts) if variant == "study-d-minor" else [score.parts[1]]
            )
            for part in targets:
                assert not part.recurse().getElementsByClass(chord.Chord)
                assert all(
                    len(m.voices) <= 1 for m in part.getElementsByClass(stream.Measure)
                )
        export(score, report, tmp_path / variant, render=False)


def test_partial_chord_tie_constrains_octaves():
    p = stream.Part()
    for i, pitches in enumerate((["C4", "C5"], ["E1", "C5"]), 1):
        m = stream.Measure(number=i)
        c = chord.Chord(pitches, quarterLength=4)
        c.notes[1].tie = tie.Tie("start" if i == 1 else "stop")
        m.append(c)
        p.append(m)
    optimize_register(p, BASS, [])
    assert p.recurse().notes[0].pitches[1] == p.recurse().notes[1].pitches[1]


def test_raw_source_requires_tuplet_normalization():
    raw = Path("sources/handel-halvorsen-violin-viola.mxl")
    if not raw.exists():
        pytest.skip("Fetch source first")
    with pytest.raises(ValueError, match="unaligned measures"):
        load_score(raw)


def test_tuplet_normalization_preserves_pitches_and_exact_bar_lengths(tmp_path):
    import xml.etree.ElementTree as ET
    import zipfile

    from music_arranger.ingest import normalize_tuplet_ticks

    raw = Path("sources/handel-halvorsen-violin-viola.mxl")
    if not raw.exists():
        pytest.skip("Fetch source first")
    out = tmp_path / "normalized.musicxml"
    fixes = normalize_tuplet_ticks(raw, out)
    assert len(fixes) == 21
    assert {x["measure"] for x in fixes} == {"32", "33", "34"}
    with zipfile.ZipFile(raw) as z:
        original = ET.fromstring(
            z.read(
                next(n for n in z.namelist() if n.endswith(".xml") and "META" not in n)
            )
        )
    normalized = ET.parse(out).getroot()
    assert [
        [p.findtext(tag) for tag in ("step", "alter", "octave")]
        for p in original.findall(".//pitch")
    ] == [
        [p.findtext(tag) for tag in ("step", "alter", "octave")]
        for p in normalized.findall(".//pitch")
    ]
    score = load_score(out)
    for p in score.parts:
        for number in (32, 33, 34):
            assert p.measure(number).highestTime == 4
