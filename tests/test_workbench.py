from pathlib import Path

from starlette.testclient import TestClient

from music_arranger.workbench import make_app

HEADERS = {"x-arranger": "workbench"}


def test_workbench_import_build_and_reopen(tmp_path):
    with TestClient(
        make_app(tmp_path), base_url="http://localhost", headers=HEADERS
    ) as client:
        assert client.get("/").status_code == 200
        assert client.get("/assets/courtyard.png").status_code == 200
        xml = Path("examples/courtyard.musicxml").read_bytes()
        result = client.post(
            "/api/source?mode=file&name=quartet.musicxml", content=xml
        ).json()
        assert len(result["parts"]) == 2
        source = result["id"]
        assert client.get(f"/files/{source}/original").content == xml
        result = client.post(f"/api/packet?source={source}&ensemble=duo&pdf=false")
        packet = result.json()["id"]
    with TestClient(make_app(tmp_path), base_url="http://localhost") as client:
        assert client.get("/api/history").json()[0]["id"] == packet
        assert client.get(f"/files/{packet}/zip").content[:2] == b"PK"
        assert client.get(f"/files/{packet}/audit").json()["eligible"]
        assert "quartet.musicxml" in client.get(f"/files/{packet}/brief").text
        assert client.get(f"/files/{packet}/score").status_code == 404
        assert client.post("/api/source?mode=example").status_code == 403
        assert client.get("/", headers={"host": "untrusted.example"}).status_code == 400
        assert (
            client.post(
                "/api/source?mode=pdmx&source=/etc/passwd", headers=HEADERS
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/source?mode=file&name=bad.xml", headers=HEADERS, content=b"x"
            ).status_code
            == 400
        )


def test_print_capability_tracks_native_renderer(tmp_path, monkeypatch):
    from music_arranger import render

    with TestClient(make_app(tmp_path), base_url="http://localhost") as client:
        monkeypatch.setattr(render, "register_music_font", lambda: None)
        assert client.get("/api/capabilities").json()["pdf"] is True

        def missing_fontconfig():
            raise RuntimeError("Fontconfig unavailable")

        monkeypatch.setattr(render, "register_music_font", missing_fontconfig)
        assert client.get("/api/capabilities").json() == {
            "pdf": False,
            "reason": "Fontconfig unavailable",
        }
