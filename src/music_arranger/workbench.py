"""Local browser interface to the same acquisition and packet operations as the CLI."""

import json
import re
import socket
import sys
import tempfile
import threading
import uuid
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

import uvicorn
from music21.exceptions21 import Music21Exception
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Route

from .acquisition import ingest_source
from .acquisition.pdmx import search
from .acquisition.providers import MAX_BYTES
from .workflow import ENSEMBLES, build_packet

WEB = Path(__file__).with_name("web")
EXAMPLE = WEB / "courtyard.musicxml"
if not EXAMPLE.is_file():
    EXAMPLE = Path(__file__).resolve().parents[2] / "examples/courtyard.musicxml"


def item(root, identifier):
    if not re.fullmatch(r"[0-9a-f]{32}", identifier):
        raise HTTPException(404)
    path = root / identifier
    if not path.is_dir():
        raise HTTPException(404)
    return path


def make_app(workspace, opened=None):
    workspace = Path(workspace).resolve()
    sources, packets = workspace / "sources", workspace / "packets"
    sources.mkdir(parents=True, exist_ok=True)
    packets.mkdir(exist_ok=True)
    render_lock = threading.Lock()

    def index(request):
        return FileResponse(WEB / "index.html")

    def asset(request):
        name = request.path_params["name"]
        if name not in {"style.css", "workbench.js", "courtyard.png"}:
            raise HTTPException(404)
        return FileResponse(WEB / name)

    def desktop_config(request):
        from .acquisition.pdmx import dataset_dir

        config = {
            "mcpServers": {
                "transscribe": {
                    "command": sys.executable,
                    "args": ["-m", "music_arranger.server"],
                    "env": {"PDMX_DIR": str(dataset_dir().absolute())},
                }
            }
        }
        return JSONResponse(
            config,
            headers={
                "Content-Disposition": 'attachment; filename="transscribe-desktop-config.json"'
            },
        )

    def options(request):
        return JSONResponse(
            {
                key: {"variants": list(value.variants), "default": value.default}
                for key, value in ENSEMBLES.items()
            }
        )

    def capabilities(request):
        from .render import register_music_font

        try:
            import cairosvg  # noqa: F401

            register_music_font()
        except (ImportError, OSError, RuntimeError) as exc:
            return JSONResponse({"pdf": False, "reason": str(exc)})
        return JSONResponse({"pdf": True})

    def catalog(request):
        args = request.query_params
        return JSONResponse(
            search(
                args.get("q", ""),
                args.get("composer", ""),
                limit=8,
                offset=int(args.get("offset", "0")),
            )
        )

    async def import_score(request):
        mode = request.query_params.get("mode")
        identifier = uuid.uuid4().hex
        output = sources / identifier
        if mode in {"example", "pdmx"}:
            source = (
                EXAMPLE if mode == "example" else request.query_params.get("source", "")
            )
            result = await run_in_threadpool(
                ingest_source, source, output, "local" if mode == "example" else "pdmx"
            )
        elif mode == "file":
            name = Path(request.query_params.get("name", "")).name
            if Path(name).suffix.lower() not in {".xml", ".musicxml", ".mxl"}:
                raise ValueError("Choose a MusicXML or MXL file.")
            with tempfile.TemporaryDirectory(dir=workspace) as tmp:
                source = Path(tmp) / name
                with source.open("xb") as dest:
                    async for chunk in request.stream():
                        if dest.tell() + len(chunk) > MAX_BYTES:
                            raise ValueError("File exceeds 64 MiB.")
                        dest.write(chunk)
                result = await run_in_threadpool(ingest_source, source, output)
        else:
            raise ValueError("Choose an example, a catalog edition or a notation file.")
        manifest = json.loads((output / "manifest.json").read_text())
        provenance = manifest["provenance"]
        title = provenance.get("title") or provenance.get("original_name", "Score")
        if mode == "example":
            title = "Courtyard Round"
        return JSONResponse(
            {"id": identifier, "title": title, "parts": result["inspection"]["parts"]}
        )

    def create_packet(request):
        args = request.query_params
        source = item(sources, args.get("source", ""))
        identifier = uuid.uuid4().hex
        with render_lock:
            build_packet(
                source,
                packets / identifier,
                args.get("ensemble", "trio"),
                args.get("variant") or None,
                args.get("pdf") != "false",
            )
        return JSONResponse({"id": identifier})

    def history(request):
        summaries = []
        for path in sorted(
            packets.glob("*/manifest.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            if not re.fullmatch(r"[0-9a-f]{32}", path.parent.name):
                continue
            manifest = json.loads(path.read_text())
            source = manifest["source"]
            summaries.append(
                {
                    "id": path.parent.name,
                    "saved": path.stat().st_mtime,
                    "name": source.get("pdmx", {}).get("title") or source["name"],
                    "ensemble": manifest["ensemble"],
                    "candidate": manifest["candidate"],
                    "rendered": manifest["rendered"],
                    "eligible": manifest["editions"][manifest["candidate"]]["eligible"],
                }
            )
        return JSONResponse(summaries)

    def download(request):
        params = request.path_params
        if params["asset"] == "original":
            folder = item(sources, params["id"])
            info = json.loads((folder / "manifest.json").read_text())["originals"][0]
            path = folder / info["path"]
        else:
            folder = item(packets, params["id"])
            manifest = json.loads((folder / "manifest.json").read_text())
            candidate = folder / manifest["candidate"]
            if params["asset"] == "brief":
                review = json.loads((candidate / "evaluation.json").read_text())
                parts = manifest["inspection"]["parts"]
                details = {
                    "source": manifest["source"].get("pdmx", {}).get("title")
                    or manifest["source"]["name"],
                    "instrumentation": [
                        {"part": part["name"], "measures": part["measures"]}
                        for part in parts
                    ],
                    "ensemble": manifest["ensemble"],
                    "control": manifest["control"],
                    "candidate": manifest["candidate"],
                    "editions": manifest["editions"],
                    "total_findings": len(review["issues"]),
                    "first_findings": review["issues"][:8],
                    "limitations": review["limitations"],
                }
                return PlainTextResponse(
                    "Help me review this rehearsal edition. Identify passages to check with the players "
                    "and distinguish measured findings from musical suggestions.\n\n"
                    + json.dumps(details, indent=2, ensure_ascii=False)
                )
            path = {
                "zip": folder / "rehearsal.zip",
                "score": candidate / "score.pdf",
                "audit": candidate / "evaluation.json",
            }.get(params["asset"])
        if not path or not path.is_file() or not path.resolve().is_relative_to(folder):
            raise HTTPException(404)
        return FileResponse(
            path,
            filename=path.name,
            content_disposition_type="inline"
            if params["asset"] in {"score", "audit"}
            else "attachment",
        )

    @asynccontextmanager
    async def lifespan(app):
        if opened:
            opened()
        yield

    app = Starlette(
        routes=[
            Route("/", index),
            Route("/assets/{name}", asset),
            Route("/api/options", options),
            Route("/api/capabilities", capabilities),
            Route("/desktop-config.json", desktop_config),
            Route("/api/search", catalog, methods=["POST"]),
            Route("/api/source", import_score, methods=["POST"]),
            Route("/api/packet", create_packet, methods=["POST"]),
            Route("/api/history", history),
            Route("/files/{id}/{asset}", download),
        ],
        lifespan=lifespan,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    async def local_request(request, call_next):
        if (
            request.method == "POST"
            and request.headers.get("x-arranger") != "workbench"
        ):
            return JSONResponse(
                {"error": "Use the local workbench to submit requests."},
                status_code=403,
            )
        return await call_next(request)

    app.add_middleware(BaseHTTPMiddleware, dispatch=local_request)

    async def error(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=400)

    for error_type in (
        ValueError,
        OSError,
        RuntimeError,
        ParseError,
        BadZipFile,
        Music21Exception,
    ):
        app.add_exception_handler(error_type, error)
    return app


def launch(workspace, port, open_browser=True):
    if not 0 <= port <= 65535:
        raise ValueError("Choose a port from 0 to 65535 (0 selects an available port).")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", port))
        actual_port = listener.getsockname()[1]
        url = f"http://127.0.0.1:{actual_port}"
        print(f"TransScribe  {url}\nWorkspace  {Path(workspace).resolve()}", flush=True)

        def open_tab():
            threading.Timer(0.4, webbrowser.open, args=(url,)).start()

        server = uvicorn.Server(
            uvicorn.Config(make_app(workspace, open_tab if open_browser else None))
        )
        server.run(sockets=[listener])
