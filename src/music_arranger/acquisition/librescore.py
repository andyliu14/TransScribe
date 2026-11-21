"""Optional, pinned LibreScore subprocess boundary; no implicit npm installs."""

import json
import shutil
import subprocess
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[3] / "tools" / "librescore"
DL_VERSION = "0.35.40"
ENGINE_VERSION = "1.2.1"


class LibreScoreError(RuntimeError):
    pass


def _check_package(name, expected):
    path = TOOLS / "node_modules" / name / "package.json"
    if not path.is_file() or json.loads(path.read_text())["version"] != expected:
        raise LibreScoreError(
            f"Install the pinned optional tools first: npm ci --ignore-scripts "
            f"--prefix {TOOLS} (expected {name} {expected})"
        )


def run_notation_tool(command, output, suffixes, timeout=180):
    """Some upstream failures exit zero; require an actual, nonempty artifact."""
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise LibreScoreError(
            f"Notation tool timed out after {timeout} seconds"
        ) from exc
    log = (result.stdout + "\n" + result.stderr).strip()
    files = sorted(
        p for p in output.iterdir() if p.is_file() and p.suffix.lower() in suffixes
    )
    if result.returncode or not files or any(p.stat().st_size == 0 for p in files):
        raise LibreScoreError(
            f"Notation tool produced no usable output (exit {result.returncode}): {log[-2000:]}"
        )
    return files, log


class LibreScore:
    """URL PDF acquisition and native notation conversion without MIDI."""

    def _node(self):
        node = shutil.which("node")
        if not node:
            raise LibreScoreError(
                "Node.js is required for the optional LibreScore adapter"
            )
        return node

    def download(self, url, output):
        _check_package("dl-librescore", DL_VERSION)
        files, log = run_notation_tool(
            [
                self._node(),
                str(TOOLS / "node_modules/dl-librescore/dist/cli.js"),
                "-i",
                url,
                "-t",
                "pdf",
                "-o",
                str(output),
                "-v",
            ],
            output,
            {".pdf"},
        )
        return files, {
            "tool": "dl-librescore",
            "version": DL_VERSION,
            "repository": "https://github.com/LibreScore/dl-librescore",
            "format_requested": "pdf",
            "log": log,
            "limitation": "This pinned CLI cannot download MSCZ/MSCX/MusicXML from URLs; PDF retains notation but needs transcription. MIDI is never substituted.",
        }

    def convert(self, native, output):
        _check_package("webmscore", ENGINE_VERSION)
        target = output / "converted.mxl"
        files, log = run_notation_tool(
            [
                self._node(),
                str(TOOLS / "convert.mjs"),
                str(native),
                str(target),
            ],
            output,
            {".mxl"},
        )
        if files != [target]:
            raise LibreScoreError("Expected one full-score MusicXML conversion")
        return target, {
            "tool": "webmscore",
            "version": ENGINE_VERSION,
            "repository": "https://github.com/LibreScore/webmscore",
            "relationship": "The notation conversion engine used by dl-librescore",
            "input_format": native.suffix.lower(),
            "output_format": "mxl",
            "full_score": True,
            "midi_intermediate": False,
            "log": log,
        }
