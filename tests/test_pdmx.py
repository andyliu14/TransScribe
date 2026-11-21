import csv
import hashlib
import io
import tarfile
from pathlib import Path
from zipfile import ZipFile

import httpx
import pytest

from music_arranger.acquisition import ingest_source, pdmx
from music_arranger.workflow import resolve_source

DUET = "mxl/2/32/Duet.mxl"
PIANO = "mxl/1/1/Piano.mxl"


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    root = tmp_path / "dataset"
    root.mkdir()
    monkeypatch.setenv("PDMX_DIR", str(root))
    entries = []
    for member, tracks, name, composer in (
        (DUET, "40-41", "Violin duet", "A"),
        (PIANO, "0", "Piano solo", "B"),
    ):
        entries.append(
            {
                "mxl": "./" + member,
                "tracks": tracks,
                "n_tracks": str(len(tracks.split("-"))),
                "title": f"Theme — {name}",
                "song_name": "Theme",
                "composer_name": composer,
                "artist_name": name,
                "license": "cc-zero",
                "license_url": "",
                "license_conflict": str(member == PIANO),
            }
        )
    entries.append({**entries[0], "mxl": "NA"})
    catalog = root / "PDMX.csv"
    with catalog.open("w", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=entries[0])
        writer.writeheader()
        writer.writerows(entries)
    payload = io.BytesIO()
    with ZipFile(payload, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            '<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>',
        )
        archive.writestr("score.xml", Path("examples/courtyard.musicxml").read_bytes())
    with tarfile.open(root / "mxl.tar.gz", "w:gz") as archive:
        entry = tarfile.TarInfo("./" + DUET)
        entry.size = len(payload.getvalue())
        archive.addfile(entry, io.BytesIO(payload.getvalue()))
    for filename in pdmx.FILES:
        path = root / filename
        monkeypatch.setitem(
            pdmx.FILES,
            filename,
            (hashlib.md5(path.read_bytes()).hexdigest(), 1_000_000),
        )
    return root


def test_search_select_ingest(dataset, tmp_path):
    result = pdmx.search("Theme", limit=1)
    assert result["next_offset"] == 1
    assert result["results"] == pdmx.search("Viola", composer="A")["results"]
    piano = pdmx.search("Theme", offset=1)["results"][0]
    assert piano["instruments"] == ["Piano"] and piano["license_conflict"]
    source = result["results"][0]["source"]
    bundle = tmp_path / "bundle"
    assert ingest_source(source, bundle)["status"] == "ready"
    _, info = resolve_source(bundle)
    assert info["pdmx"]["source"] == source
    assert info["pdmx"]["retrieval"] == "verified_archive"
    assert not pdmx.search("flute")["results"]
    with pytest.raises(ValueError, match="missing from archive"):
        ingest_source(piano["source"], tmp_path / "missing")
    assert not (tmp_path / "missing").exists()


def test_download_cache_and_interrupted_transfer(dataset, monkeypatch):
    path = dataset / "PDMX.csv"
    contents = path.read_bytes()
    path.unlink()

    def respond(data):
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=data)
            )
        )
        monkeypatch.setattr(pdmx.httpx, "stream", client.stream)
        return client

    with respond(contents[:-10]), pytest.raises(ValueError, match="Incomplete"):
        pdmx.search("theme")
    assert not path.exists()
    with respond(contents):
        assert len(pdmx.search("theme")["results"]) == 2
    assert path.read_bytes() == contents


def test_invalid_edition_and_corrupt_cache(dataset, tmp_path):
    for source in ("pdmx:../../private.mxl", "pdmx:mxl/4/5/Unknown.mxl"):
        with pytest.raises(ValueError):
            ingest_source(source, tmp_path / "result")
    (dataset / "PDMX.csv").write_text("partial")
    with pytest.raises(ValueError, match="Checksum"):
        pdmx.search("theme")
    assert not (tmp_path / "result").exists()
