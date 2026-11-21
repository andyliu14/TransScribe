"""Acquire immutable source artifacts, then normalize symbolic notation.

Adding a source provider does not change conversion, validation or arranging.
Bundles publish atomically and never replace an existing nonempty destination.
"""

import hashlib
import json
import tempfile
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from music21.exceptions21 import Music21Exception
from pypdf import PdfReader

from .librescore import LibreScore
from .providers import MAX_BYTES, PROVIDERS, provider_catalog

__all__ = ["ingest_source", "provider_catalog"]


def _xml_bytes(path):
    if path.suffix.lower() != ".mxl":
        data = path.read_bytes()
    else:
        with ZipFile(path) as archive:
            if archive.getinfo("META-INF/container.xml").file_size > 1024 * 1024:
                raise ValueError("Oversized MXL container manifest")
            container = ET.fromstring(archive.read("META-INF/container.xml"))
            roots = [
                e.get("full-path")
                for e in container.iter()
                if e.tag.rsplit("}", 1)[-1] == "rootfile"
                and e.get("media-type", "application/vnd.recordare.musicxml+xml")
                == "application/vnd.recordare.musicxml+xml"
            ]
            if len(roots) != 1 or not roots[0]:
                raise ValueError("Expected exactly one MusicXML root file in MXL")
            if archive.getinfo(roots[0]).file_size > MAX_BYTES:
                raise ValueError("Expanded MusicXML exceeds the ingestion limit")
            data = archive.read(roots[0])
    root = ET.fromstring(data)
    if root.tag != "score-partwise":
        raise ValueError(
            "Expected partwise MusicXML notation, not HTML or another XML document"
        )
    return data


def ingest_source(source, output, provider="auto", transcribe_pdf=False):
    from music_arranger.core import inspect_score, load_score

    output = Path(output).resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError(f"Choose an empty/new source bundle directory: {output}")
    adapter = next(
        (
            p
            for p in PROVIDERS
            if (provider == "auto" or provider == p.name) and p.accepts(source)
        ),
        None,
    )
    if adapter is None:
        raise ValueError(f"No {provider} source adapter accepts {source}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".ingest-", dir=output.parent) as temporary:
        stage = Path(temporary) / "bundle"
        raw = stage / "original"
        raw.mkdir(parents=True)
        acquired = adapter.acquire(str(source), raw)
        if not acquired.files or any(
            not p.is_file() or not p.stat().st_size for p in acquired.files
        ):
            raise ValueError("Source adapter returned no usable artifacts")
        if any(not p.resolve().is_relative_to(raw.resolve()) for p in acquired.files):
            raise ValueError("Source adapter returned an artifact outside its bundle")
        if any(p.stat().st_size > MAX_BYTES for p in acquired.files):
            raise ValueError("Source exceeds the ingestion limit")
        # Select notation by fidelity, never by directory ordering.
        ranking = {
            ".mscz": 0,
            ".mscx": 1,
            ".musicxml": 2,
            ".mxl": 3,
            ".xml": 4,
            ".pdf": 5,
        }
        selected = min(acquired.files, key=lambda p: ranking.get(p.suffix.lower(), 99))
        suffix = selected.suffix.lower()
        manifest = {
            "schema_version": 1,
            "provider": adapter.name,
            "retrieved_utc": datetime.now(UTC).isoformat(),
            "provenance": acquired.provenance,
            "selected_format": suffix.removeprefix("."),
            "transformations": [],
            "originals": [],
            "normalized_score": None,
        }
        for p in acquired.files:
            manifest["originals"].append(
                {
                    "path": str(p.relative_to(stage)),
                    "bytes": p.stat().st_size,
                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                }
            )
        if suffix == ".pdf":
            pdf = PdfReader(selected)
            if not pdf.pages:
                raise ValueError("Source PDF contains no pages")
            manifest.update(
                {
                    "status": "needs_transcription",
                    "pdf_pages": len(pdf.pages),
                    "next_step": "Transcribe/OMR and review this PDF, or ingest an MSCZ/MSCX/MusicXML/MXL edition. A PDF is not an arrangement-ready score.",
                }
            )
            if transcribe_pdf:
                from .omr import transcribe_pdf as run_omr

                recognized = run_omr(selected, stage / "omr")
                candidates = []
                for index, path in enumerate(recognized["files"], 1):
                    entry = {
                        "raw_musicxml": str(path.relative_to(stage)),
                        "raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "structurally_valid": False,
                    }
                    try:
                        if path.stat().st_size > MAX_BYTES:
                            raise ValueError("OMR export exceeds the ingestion limit")
                        normalized = stage / "normalized"
                        normalized.mkdir(exist_ok=True)
                        candidate = normalized / f"candidate-{index}.musicxml"
                        candidate.write_bytes(_xml_bytes(path))
                        entry["path"] = str(candidate.relative_to(stage))
                        entry["sha256"] = hashlib.sha256(
                            candidate.read_bytes()
                        ).hexdigest()
                        score = load_score(candidate, expected_parts=None)
                        entry["inspection"] = inspect_score(score)
                        entry["structurally_valid"] = True
                    except (
                        ValueError,
                        OSError,
                        ET.ParseError,
                        BadZipFile,
                        Music21Exception,
                    ) as exc:
                        # Keep flawed OCR output for correction; parsing success
                        # would not prove that it matches the printed source.
                        entry["validation_error"] = str(exc)
                    candidates.append(entry)
                manifest["transcription"] = {
                    k: v for k, v in recognized.items() if k != "files"
                }
                from .omr import relocate_projects

                manifest["transcription"]["project_relocations"] = relocate_projects(
                    stage / "omr", selected, output / selected.relative_to(stage)
                )
                manifest["transcription"]["log"] = "omr/audiveris.log"
                artifacts = []
                for artifact in sorted((stage / "omr").rglob("*")):
                    if artifact.is_file():
                        with artifact.open("rb") as handle:
                            digest = hashlib.file_digest(handle, "sha256").hexdigest()
                        artifacts.append(
                            {"path": str(artifact.relative_to(stage)), "sha256": digest}
                        )
                manifest["transcription"]["artifacts"] = artifacts
                manifest["candidates"] = candidates
                manifest["status"] = (
                    "needs_review" if candidates else "transcription_failed"
                )
                manifest["next_step"] = (
                    "Compare each candidate with the original PDF and correct pitches, "
                    "voices, durations, accidentals, ties and repeats before arranging. "
                    "Editable Audiveris projects and raw exports are in omr/. "
                    "Ingest the reviewed MusicXML as a new source bundle."
                    if candidates
                    else recognized.get("error", "No OMR candidates were produced")
                )
        else:
            normalized = stage / "normalized"
            normalized.mkdir()
            if suffix in {".mscz", ".mscx"}:
                selected, conversion = LibreScore().convert(selected, normalized)
                conversion["output_sha256"] = hashlib.sha256(
                    selected.read_bytes()
                ).hexdigest()
                manifest["transformations"].append(conversion)
            elif suffix not in {".musicxml", ".xml", ".mxl"}:
                raise ValueError("Adapter did not provide a supported notation format")
            target = normalized / "score.musicxml"
            target.write_bytes(_xml_bytes(selected))
            # Native editors can round explicitly encoded tuplets to integer
            # ticks. Repair only the existing conservative, <= one-tick case;
            # retain both original notation and a measure-level repair audit.
            if (
                ET.fromstring(target.read_bytes()).find(".//time-modification")
                is not None
            ):
                from music_arranger.ingest import normalize_tuplet_ticks

                repaired = normalized / "exact-tuplets.musicxml"
                try:
                    fixes = normalize_tuplet_ticks(target, repaired)
                except (ValueError, KeyError) as exc:
                    fixes = []
                    manifest["tuplet_review"] = str(exc)
                if fixes:
                    target.write_bytes(repaired.read_bytes())
                    manifest["transformations"].append(
                        {
                            "action": "normalize_encoded_tuplet_rounding",
                            "audit": "normalized/exact-tuplets.repairs.json",
                            "repairs": fixes,
                            "pitch_changes": False,
                        }
                    )
                else:
                    repaired.with_suffix(".repairs.json").unlink(missing_ok=True)
                repaired.unlink(missing_ok=True)
            score = load_score(target, expected_parts=None)
            manifest.update(
                {
                    "status": "ready",
                    "normalized_score": str(target.relative_to(stage)),
                    "normalized_sha256": hashlib.sha256(
                        target.read_bytes()
                    ).hexdigest(),
                    "inspection": inspect_score(score),
                    "sound_tempo_import_repairs": score.editorial.get(
                        "sound_tempo_repairs", []
                    ),
                }
            )
            manifest["transformations"].append(
                {
                    "action": "unpack_musicxml"
                    if selected.suffix.lower() == ".mxl"
                    else "copy_musicxml",
                    "pitches_edited": False,
                    "tuplet_rounding_repairs": sum(
                        len(t.get("repairs", [])) for t in manifest["transformations"]
                    ),
                }
            )
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        if output.exists():
            output.rmdir()  # Succeeds only if it is still empty.
        stage.rename(output)
    return {
        "directory": str(output),
        "manifest": str(output / "manifest.json"),
        "status": manifest["status"],
        "score": str(output / manifest["normalized_score"])
        if manifest["normalized_score"]
        else None,
        "inspection": manifest.get("inspection"),
        "next_step": manifest.get("next_step"),
        "candidates": [
            {
                **entry,
                "path": str(output / entry["path"]) if entry.get("path") else None,
            }
            for entry in manifest.get("candidates", [])
        ],
    }
