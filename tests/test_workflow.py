import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from music21 import instrument, meter, note, stream

from music_arranger.acquisition import ingest_source
from music_arranger.workflow import build_packet

EXAMPLE = Path("examples/courtyard.musicxml")


@pytest.mark.parametrize("ensemble", ["duo", "trio"])
def test_packet_payload(tmp_path, ensemble):
    source = EXAMPLE
    if ensemble == "trio":
        solo = stream.Part()
        solo.insert(0, instrument.Flute())
        solo.append(meter.TimeSignature("4/4"))
        solo.append(note.Note("B5", quarterLength=4))
        path = tmp_path / "solo.musicxml"
        solo.write("musicxml", fp=path)
        source = tmp_path / "source"
        ingest_source(path, source)
    output = tmp_path / "packet"
    result = build_packet(source, output, ensemble, render=False)
    with ZipFile(result["archive"]) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert set(archive.namelist()) == {"manifest.json", *manifest["files"]}
        for name, digest in manifest["files"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest
        for edition in (manifest["candidate"], manifest["control"]):
            assert json.loads(archive.read(f"{edition}/report.json"))[
                "roundtrip_verified"
            ]
            assert (
                json.loads(archive.read(f"{edition}/evaluation.json"))["eligible"]
                == result["editions"][edition]["eligible"]
            )
    with pytest.raises(FileExistsError):
        build_packet(source, output, ensemble)


def test_reject_modified_or_unreviewed_bundle(tmp_path):
    bundle = tmp_path / "source"
    ingest_source(EXAMPLE, bundle)
    output = tmp_path / "result"
    (bundle / "normalized/score.musicxml").write_text("changed")
    with pytest.raises(ValueError, match="checksum"):
        build_packet(bundle, output)
    (bundle / "manifest.json").write_text('{"status":"needs_review"}')
    with pytest.raises(ValueError, match="requires review"):
        build_packet(bundle, output)
    assert not output.exists()


def test_failed_evaluation_does_not_publish(tmp_path, monkeypatch):
    from dataclasses import replace

    from music_arranger import workflow

    def fail(*args):
        raise RuntimeError("evaluation failed")

    monkeypatch.setitem(
        workflow.ENSEMBLES, "duo", replace(workflow.ENSEMBLES["duo"], evaluate=fail)
    )
    with pytest.raises(RuntimeError, match="evaluation failed"):
        build_packet(EXAMPLE, tmp_path / "result", render=False)
    assert not list(tmp_path.iterdir())
