import runpy
from pathlib import Path

import pytest

from music_arranger.core import fingerprint, load_score

PDF = Path("sources/themes/hedwigs-theme/original.pdf")


@pytest.mark.skipif(not PDF.exists(), reason="Fetch the pinned source PDF first")
def test_hedwig_vector_transcription_ledger_note_ties_and_pickup():
    transcribe = runpy.run_path("scripts/ingest_hedwig_pdf.py")["transcribe"]
    before = PDF.read_bytes()
    score, audit = transcribe(PDF)
    assert PDF.read_bytes() == before
    assert [len(p.getElementsByClass("Measure")) for p in score.parts] == [51, 51]
    assert [len(p.recurse().notes) for p in score.parts] == [98, 73]
    assert all(p.highestTime == 151 for p in score.parts)
    assert score.parts[0].measure(0).notes[0].pitch.nameWithOctave == "B4"
    assert score.parts[0].measure(0).highestTime == 1
    assert score.parts[1].measure(50).notes[0].pitch.nameWithOctave == "E6"
    assert score.parts[1].measure(8).notes[0].pitch.nameWithOctave == "E-4"
    assert score.parts[1].measure(8).notes[0].tie.type == "stop"
    assert [score.parts[0].measure(m).notes[0].tie.type for m in (47, 48, 49, 50)] == [
        "start",
        "continue",
        "continue",
        "stop",
    ]
    assert len(audit) == 176  # 171 notes and five printed rests.
    assert fingerprint(score) == fingerprint(
        load_score(PDF.with_name("source.musicxml"))
    )
