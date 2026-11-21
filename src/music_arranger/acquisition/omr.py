"""Optional optical music recognition subprocess; candidates always need review."""

import json
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

VERSION = "5.11.0"
CACHE = Path.home() / ".cache/music-arranger" / f"audiveris-{VERSION}"
EXECUTABLE = CACHE / "opt/audiveris/bin/Audiveris"


def relocate_projects(directory, staged_pdf, published_pdf):
    """Repair staging-only image references while retaining the raw OMR project."""
    changes = []
    for project in list(directory.rglob("*.omr")):
        if project.name.endswith(".raw.omr"):
            continue
        try:
            with ZipFile(project) as archive:
                book = ET.fromstring(archive.read("book.xml"))
        except (BadZipFile, ET.ParseError, KeyError) as exc:
            changes.append(
                {
                    "project": str(project.relative_to(directory.parent)),
                    "error": str(exc),
                }
            )
            continue
        count = 0
        if book.get("path") == str(staged_pdf):
            book.set("path", str(published_pdf))
            count += 1
        for path in book.findall(".//input/path"):
            if path.text == str(staged_pdf):
                path.text = str(published_pdf)
                count += 1
        if not count:
            continue
        raw = project.with_suffix(".raw.omr")
        shutil.copyfile(project, raw)
        with ZipFile(raw) as original, ZipFile(project, "w", ZIP_DEFLATED) as output:
            for item in original.infolist():
                output.writestr(
                    item,
                    ET.tostring(book, encoding="utf-8", xml_declaration=True)
                    if item.filename == "book.xml"
                    else original.read(item.filename),
                )
        changes.append(
            {
                "project": str(project.relative_to(directory.parent)),
                "raw_project": str(raw.relative_to(directory.parent)),
                "source_path_references_updated": count,
                "notation_edited": False,
            }
        )
    return changes


def transcribe_pdf(source, output, timeout=600):
    """Keep the editable OMR project, raw MusicXML and full diagnostic log."""
    output.mkdir(parents=True, exist_ok=True)
    log_path = output / "audiveris.log"
    record = {
        "engine": "Audiveris",
        "version": VERSION,
        "midi_intermediate": False,
        "review_required": True,
    }
    if (
        not EXECUTABLE.is_file()
        or not (CACHE / "installation.json").is_file()
        or not (CACHE / "tessdata/eng.traineddata").is_file()
    ):
        error = "Install the pinned OMR engine with uv run python scripts/bootstrap_audiveris.py"
        log_path.write_text(error + "\n")
        return {
            **record,
            "error": error,
            "files": [],
        }
    pinned = json.loads((CACHE / "installation.json").read_text())
    if pinned.get("version") != VERSION:
        raise ValueError("OMR installation version does not match the pinned engine")
    record["provenance"] = pinned
    env = dict(os.environ)
    env["JAVA_TOOL_OPTIONS"] = f"-Djava.awt.headless=true -Duser.home={CACHE / 'home'}"
    env["GSETTINGS_BACKEND"] = "memory"
    env["XDG_CONFIG_HOME"] = str(CACHE / "config")
    env["XDG_DATA_HOME"] = str(CACHE / "data")
    env["XDG_CACHE_HOME"] = str(CACHE / "cache")
    env["TESSDATA_PREFIX"] = str(CACHE / "tessdata")
    command = [
        str(EXECUTABLE),
        "-batch",
        "-transcribe",
        "-export",
        "-save",
        "-output",
        str(output.resolve()),
        "--",
        str(source.resolve()),
    ]
    try:
        with log_path.open("w") as log:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                timeout=timeout,
                check=False,
            )
        record["exit_code"] = result.returncode
        if result.returncode:
            record["error"] = (
                f"Audiveris exited {result.returncode}; inspect audiveris.log"
            )
    except subprocess.TimeoutExpired:
        record["error"] = f"Audiveris timed out after {timeout} seconds"
    except OSError as exc:
        record["error"] = str(exc)
    files = sorted(p for p in output.rglob("*.mxl") if p.is_file() and p.stat().st_size)
    if not files and "error" not in record:
        record["error"] = (
            "Audiveris produced no MusicXML candidate; inspect its log/project"
        )
    return {**record, "files": files}
