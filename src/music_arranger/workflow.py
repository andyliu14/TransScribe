"""Build a portable rehearsal packet from notation or a ready acquisition bundle."""

import hashlib
import json
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from .core import (
    TRIO_VARIANTS,
    VARIANTS,
    arrange,
    arrange_trio,
    export,
    inspect_score,
    load_score,
)
from .judge import evaluate
from .trio_judge import evaluate_trio


@dataclass(frozen=True)
class Ensemble:
    variants: dict
    control: str
    default: str
    source_parts: int | None
    arrange: Callable
    evaluate: Callable


ENSEMBLES = {
    "duo": Ensemble(VARIANTS, "reference", "literature-chamber", 2, arrange, evaluate),
    "trio": Ensemble(
        TRIO_VARIANTS, "sax-led", "relay-two", None, arrange_trio, evaluate_trio
    ),
}


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def resolve_source(path):
    if not path.is_dir():
        return path, {"name": path.name}
    manifest = json.loads((path / "manifest.json").read_text())
    if manifest.get("status") != "ready":
        raise ValueError(f"Source bundle requires review ({manifest.get('status')})")
    relative = manifest.get("normalized_score")
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError("Expected a bundle-relative normalized_score")
    score = (path / relative).resolve()
    if (
        not score.is_relative_to(path.resolve())
        or not score.is_file()
        or digest(score) != manifest.get("normalized_sha256")
    ):
        raise ValueError("Source bundle path or checksum mismatch")
    return score, {
        "name": manifest.get("provenance", {}).get("original_name", score.name),
        **({"pdmx": manifest["provenance"]} if manifest.get("provider") == "pdmx" else {}),
        "acquisition_manifest_sha256": digest(path / "manifest.json"),
        "originals": manifest["originals"],
        "provider": manifest["provider"],
    }


def build_packet(source, output, ensemble="duo", variant=None, render=True):
    """Export control and proposal, independently evaluate both, then publish together."""
    if ensemble not in ENSEMBLES:
        raise ValueError(f"Choose an ensemble from {list(ENSEMBLES)}")
    backend = ENSEMBLES[ensemble]
    variant = backend.default if variant is None else variant
    if variant not in backend.variants:
        raise ValueError(f"Choose a {ensemble} variant from {list(backend.variants)}")
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Choose a new packet directory: {output}")
    source, origin = resolve_source(Path(source))
    origin["sha256"] = digest(source)
    score = load_score(source, expected_parts=backend.source_parts)
    manifest = {
        "schema_version": 1,
        "versions": {
            name: version(name)
            for name in (
                "transscribe",
                "music21",
                "music21-mcp",
                "verovio",
            )
        },
        "ensemble": ensemble,
        "control": backend.control,
        "candidate": variant,
        "source": origin,
        "inspection": inspect_score(score),
        "rendered": render,
        "editions": {},
        "files": {},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".arranger-") as tmp:
        stage = Path(tmp) / "packet"
        for policy in dict.fromkeys((backend.control, variant)):
            folder = stage / policy
            proposal, report = backend.arrange(score, policy)
            export(proposal, report, folder, render=render)
            # Keep reports readable after the packet is moved.
            report["input"] = origin
            for entry in report.get("rendering", {}).values():
                entry["pdf"] = Path(entry["pdf"]).name
            write_json(folder / "report.json", report)
            review = backend.evaluate(source, folder / "score.musicxml")
            write_json(folder / "evaluation.json", review)
            manifest["editions"][policy] = {"eligible": review["eligible"]}
        if digest(source) != origin["sha256"]:
            raise ValueError(
                "Source changed during arranging. Retry with a stable copy."
            )
        parts = ", ".join(
            f"{part['name']} ({part['measures']} measures)"
            for part in manifest["inspection"]["parts"]
        )
        lines = [
            "# Rehearsal packet",
            "",
            f"Source instruments and written lengths — {parts}.",
            "",
            f"Control edition — {backend.control}. Candidate edition — {variant}.",
            "",
        ]
        for policy, entry in manifest["editions"].items():
            status = "passed" if entry["eligible"] else "flagged issues"
            extension = "pdf" if render else "musicxml"
            lines.append(
                f"- [{policy} score]({policy}/score.{extension}) — structural evaluation {status}. "
                f"[Measure audit]({policy}/report.json) · [Evaluation]({policy}/evaluation.json)"
            )
        lines.extend(
            [
                "",
                (
                    "Each edition includes separate player parts and a MIDI audition. "
                    "Review the extracted notation, register shifts and phrasing with the players."
                ),
            ]
        )
        (stage / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        files = sorted(p for p in stage.rglob("*") if p.is_file())
        manifest["files"] = {p.relative_to(stage).as_posix(): digest(p) for p in files}
        index = stage / "manifest.json"
        write_json(index, manifest)
        with ZipFile(stage / "rehearsal.zip", "x", ZIP_DEFLATED) as archive:
            for path in [index, *files]:
                archive.write(path, path.relative_to(stage))
        stage.rename(output)
    return {
        "directory": str(output),
        "archive": str(output / "rehearsal.zip"),
        "manifest": str(output / "manifest.json"),
        "editions": manifest["editions"],
    }
