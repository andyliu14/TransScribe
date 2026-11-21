"""Exercise real stdio handshakes and calls, not just Python wrapper imports."""

import asyncio
import json
import tempfile
import tomllib
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from music21 import instrument, meter, note, stream

ROOT = Path(__file__).resolve().parents[1]


async def check():
    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        source = ROOT / "sources/handel-halvorsen.musicxml"
        if not source.exists():
            s = stream.Score()
            for cls, pitch in [(instrument.Violin, "G4"), (instrument.Viola, "G3")]:
                p = stream.Part()
                p.insert(0, cls())
                m = stream.Measure(number=1)
                m.append(meter.TimeSignature("4/4"))
                m.append(note.Note(pitch, quarterLength=4))
                p.append(m)
                s.insert(0, p)
            source = temp / "input.musicxml"
            s.write("musicxml", fp=source)
        servers = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]
        codex = tomllib.loads((ROOT / ".codex/config.toml").read_text())["mcp_servers"]
        for name, server in servers.items():
            assert server == codex[name], f"Client configuration differs for {name}"
        params = StdioServerParameters(cwd=str(ROOT), **servers["transscribe"])
        ensemble = stream.Score()
        for pitch in ("G3", "E5", "C3", "C5", "E4", "G4"):
            part = stream.Part()
            part.insert(0, instrument.Piano())
            for number in range(1, 5):
                measure = stream.Measure(number=number)
                if number == 1:
                    measure.insert(0, meter.TimeSignature("4/4"))
                measure.append(note.Note(pitch, quarterLength=4))
                part.append(measure)
            ensemble.insert(0, part)
        ensemble_path = temp / "sextet.musicxml"
        ensemble.write("musicxml", fp=ensemble_path)
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            names = [t.name for t in (await session.list_tools()).tools]
            assert "build_rehearsal_packet" in names, names
            assert "search_pdmx_scores" in names, names
            invalid = await session.call_tool(
                "search_pdmx_scores", {"query": "", "limit": 0}
            )
            assert invalid.isError
            results = []
            # CI works offline. A populated pinned catalog also exercises a real edition lookup.
            if (Path.home() / ".cache/music-arranger/pdmx/15571083/PDMX.csv").is_file():
                result = await session.call_tool(
                    "search_pdmx_scores",
                    {"query": "passacaglia violin viola", "composer": "Halvorsen"},
                )
                assert not result.isError and "pdmx:mxl/" in result.content[0].text, (
                    result
                )
                results.append({"tool": "search_pdmx_scores", "ok": True})
            for name, args in [
                ("score_source_providers", {}),
                (
                    "compare_score_transcription",
                    {
                        "reference_path": str(ensemble_path),
                        "candidate_path": str(ensemble_path),
                    },
                ),
                (
                    "ingest_score_source",
                    {
                        "source": str(ensemble_path),
                        "output_dir": str(temp / "ingested"),
                    },
                ),
                (
                    "build_rehearsal_packet",
                    {
                        "path": str(temp / "ingested"),
                        "output_dir": str(temp / "packet"),
                        "ensemble": "trio",
                        "render": False,
                    },
                ),
                ("arrangement_policies", {}),
                ("inspect_score_file", {"path": str(source)}),
                (
                    "transpose_score",
                    {
                        "path": str(source),
                        "output_path": str(temp / "transposed.musicxml"),
                        "semitones": -2,
                    },
                ),
                (
                    "arrange_duo",
                    {
                        "path": str(source),
                        "output_dir": str(temp / "arranged"),
                        "variant": "literature-chamber",
                        "render": False,
                    },
                ),
                ("critique_duo", {"path": str(temp / "arranged/score.musicxml")}),
                ("literature_rules", {"path": str(temp / "arranged/score.musicxml")}),
                (
                    "evaluate_arrangement",
                    {
                        "source_path": str(source),
                        "candidate_path": str(temp / "arranged/score.musicxml"),
                    },
                ),
                (
                    "arrange_alto_trio",
                    {
                        "path": str(ensemble_path),
                        "output_dir": str(temp / "trio"),
                        "variant": "relay-two",
                        "render": False,
                    },
                ),
                (
                    "evaluate_alto_trio",
                    {
                        "source_path": str(ensemble_path),
                        "candidate_path": str(temp / "trio/score.musicxml"),
                    },
                ),
            ]:
                result = await session.call_tool(name, args)
                assert not result.isError, result
                results.append({"tool": name, "ok": True})
        params = StdioServerParameters(cwd=str(ROOT), **servers["music21-upstream"])
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            upstream = [t.name for t in (await session.list_tools()).tools]
            assert "mcp_transpose_midi" in upstream
        from starlette.testclient import TestClient

        from music_arranger.workbench import make_app

        with TestClient(
            make_app(temp / "workbench"), base_url="http://localhost"
        ) as client:
            desktop = client.get("/desktop-config.json").json()["mcpServers"][
                "transscribe"
            ]
        async with (
            stdio_client(StdioServerParameters(cwd=str(temp), **desktop)) as (
                read,
                write,
            ),
            ClientSession(read, write) as client_session,
        ):
            await client_session.initialize()
            result = await client_session.call_tool("score_source_providers", {})
            assert not result.isError and "pdmx" in result.content[0].text
        print(
            json.dumps(
                {
                    "local": results,
                    "upstream_tools": len(upstream),
                    "desktop_config": "verified",
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    asyncio.run(check())
