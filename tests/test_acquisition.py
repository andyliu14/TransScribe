import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

import pytest
from music21 import bar, instrument, meter, note, stream, tie
from pypdf import PdfWriter

from music_arranger.acquisition import ingest_source, provider_catalog
from music_arranger.acquisition.librescore import (
    TOOLS,
    LibreScore,
    LibreScoreError,
    run_notation_tool,
)
from music_arranger.core import fingerprint, load_score


def source_file(tmp_path):
    score = stream.Score()
    for inst, pitch in ((instrument.Clarinet(), "G4"), (instrument.Contrabass(), "C3")):
        p = stream.Part()
        p.insert(0, inst)
        for number in (1, 2):
            m = stream.Measure(number=number)
            if number == 1:
                m.insert(0, meter.TimeSignature("3/4"))
            n = note.Note(pitch, quarterLength=3)
            n.tie = tie.Tie("start" if number == 1 else "stop")
            m.append(n)
            if number == 2:
                m.rightBarline = bar.Repeat(direction="end")
            p.append(m)
        score.insert(0, p)
    score.atSoundingPitch = True
    path = tmp_path / "original.musicxml"
    score.toWrittenPitch(inPlace=False).write("musicxml", fp=path)
    return path


def test_musicxml_bundle_preserves_source_and_records_real_score(tmp_path):
    source = source_file(tmp_path)
    before = source.read_bytes()
    result = ingest_source(source, tmp_path / "bundle")
    manifest = json.loads(Path(result["manifest"]).read_text())
    assert result["status"] == "ready"
    assert source.read_bytes() == before == Path(result["score"]).read_bytes()
    assert manifest["originals"][0]["sha256"] == hashlib.sha256(before).hexdigest()
    assert len(result["inspection"]["parts"]) == 2
    assert result["inspection"]["sounding_pitch"]
    assert fingerprint(load_score(source)) == fingerprint(load_score(result["score"]))
    with pytest.raises(FileExistsError):
        ingest_source(source, tmp_path / "bundle")
    assert source.read_bytes() == before


def test_mxl_archive_normalizes_without_changing_notation(tmp_path):
    source = source_file(tmp_path)
    mxl = tmp_path / "score.mxl"
    with ZipFile(mxl, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            """<container>
        <rootfiles><rootfile full-path="nested/full.musicxml"
        media-type="application/vnd.recordare.musicxml+xml"/></rootfiles></container>""",
        )
        archive.writestr("nested/full.musicxml", source.read_bytes())
    result = ingest_source(mxl, tmp_path / "bundle")
    assert Path(result["score"]).read_bytes() == source.read_bytes()


def test_musescore_pdf_is_never_misrepresented_as_ready(monkeypatch, tmp_path):
    calls = []

    def download(self, url, output):
        calls.append(url)
        path = output / "download.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        writer.write(path)
        return [path], {"tool": "fixture"}

    monkeypatch.setattr(LibreScore, "download", download)
    result = ingest_source(
        "https://musescore.com/user/123/scores/456?tracking=ignored",
        tmp_path / "bundle",
    )
    assert calls == ["https://musescore.com/user/123/scores/456"]
    assert result["status"] == "needs_transcription"
    assert result["score"] is None
    assert list((tmp_path / "bundle" / "original").glob("*.pdf"))
    assert not list((tmp_path / "bundle").rglob("*.mid"))
    assert provider_catalog()["providers"][1]["native_download_supported"] is False


def test_failed_upstream_exit_zero_does_not_count_as_download(monkeypatch, tmp_path):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", "Score not found"),
    )
    with pytest.raises(LibreScoreError, match="Score not found"):
        run_notation_tool(["node", "fake"], tmp_path, {".pdf"})


def test_missing_or_invalid_notation_does_not_publish_a_bundle(tmp_path):
    path = tmp_path / "fake.musicxml"
    path.write_text("<html>Download unavailable</html>")
    with pytest.raises(ValueError, match="MusicXML"):
        ingest_source(path, tmp_path / "result")
    assert not (tmp_path / "result").exists()
    assert path.read_text() == "<html>Download unavailable</html>"


def test_midi_and_non_score_musescore_urls_fail_explicitly(tmp_path):
    midi = tmp_path / "fake.mid"
    midi.write_bytes(b"MThd")
    with pytest.raises(ValueError, match="MIDI"):
        ingest_source(midi, tmp_path / "midi")
    with pytest.raises(ValueError, match="full MuseScore score URL"):
        ingest_source("https://musescore.com/user/123", tmp_path / "profile")


def test_pdf_omr_exports_candidates_but_never_claims_reviewed_notation(
    monkeypatch, tmp_path
):
    source = source_file(tmp_path)
    pdf = tmp_path / "printed.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(pdf)
    original = pdf.read_bytes()

    def recognize(input_pdf, output):
        assert input_pdf.read_bytes() == original
        output.mkdir()
        files = []
        # Distinct movements must stay separate rather than being concatenated.
        for i in (1, 2):
            path = output / f"movement-{i}.mxl"
            with ZipFile(path, "w") as archive:
                archive.writestr(
                    "META-INF/container.xml",
                    '<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>',
                )
                archive.writestr("score.xml", source.read_bytes())
            files.append(path)
        (output / "audiveris.log").write_text("fixture log")
        with ZipFile(output / "printed.omr", "w") as archive:
            book = ET.Element("book", path=str(input_pdf))
            ET.SubElement(ET.SubElement(book, "input"), "path").text = str(input_pdf)
            archive.writestr("book.xml", ET.tostring(book))
        return {"engine": "fixture", "files": files, "review_required": True}

    monkeypatch.setattr("music_arranger.acquisition.omr.transcribe_pdf", recognize)
    result = ingest_source(pdf, tmp_path / "bundle", transcribe_pdf=True)
    assert result["status"] == "needs_review"
    assert result["score"] is None
    assert len(result["candidates"]) == 2
    assert all(c["structurally_valid"] for c in result["candidates"])
    assert pdf.read_bytes() == original
    assert (tmp_path / "bundle/omr/printed.omr").is_file()
    assert (tmp_path / "bundle/omr/printed.raw.omr").is_file()
    with ZipFile(tmp_path / "bundle/omr/printed.omr") as archive:
        assert ET.fromstring(archive.read("book.xml")).get("path") == str(
            tmp_path / "bundle/original/source.pdf"
        )
    assert all(len(c["inspection"]["parts"]) == 2 for c in result["candidates"])


def test_omr_failure_retains_original_pdf_and_does_not_fall_back_to_midi(
    monkeypatch, tmp_path
):
    pdf = tmp_path / "printed.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(pdf)
    monkeypatch.setattr(
        "music_arranger.acquisition.omr.transcribe_pdf",
        lambda source, output: {"files": [], "error": "recognition failed"},
    )
    result = ingest_source(pdf, tmp_path / "bundle", transcribe_pdf=True)
    assert result["status"] == "transcription_failed"
    assert result["score"] is None and not result["candidates"]
    assert (tmp_path / "bundle/original/source.pdf").read_bytes() == pdf.read_bytes()
    assert not list((tmp_path / "bundle").rglob("*.mid"))


@pytest.mark.skipif(
    not (TOOLS / "node_modules/webmscore/package.json").exists(),
    reason="Install optional pinned tools with npm ci --ignore-scripts --prefix tools/librescore",
)
def test_real_native_musescore_conversion_preserves_pitches_ties_repeats(tmp_path):
    source = source_file(tmp_path)
    native = tmp_path / "source.mscz"
    subprocess.run(
        ["node", str(TOOLS / "convert.mjs"), str(source), str(native)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    before = native.read_bytes()
    result = ingest_source(native, tmp_path / "bundle")
    assert native.read_bytes() == before
    original, converted = load_score(source), load_score(result["score"])
    assert fingerprint(original) == fingerprint(converted)
    for p in converted.parts:
        assert isinstance(p.measure(2).rightBarline, bar.Repeat)
    manifest = json.loads(Path(result["manifest"]).read_text())
    assert manifest["transformations"][0]["midi_intermediate"] is False
    assert manifest["transformations"][0]["full_score"] is True
