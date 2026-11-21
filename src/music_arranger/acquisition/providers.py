"""Independent acquisition adapters. Providers never make arranging decisions."""

import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

from .librescore import LibreScore

SUFFIXES = {".musicxml", ".xml", ".mxl", ".mscz", ".mscx", ".pdf"}
MAX_BYTES = 64 * 1024 * 1024


@dataclass
class Acquired:
    files: list[Path]
    provenance: dict


class LocalFile:
    name = "local"

    def accepts(self, source):
        return urlparse(str(source)).scheme == ""

    def acquire(self, source, output):
        path = Path(source).resolve()
        if path.suffix.lower() not in SUFFIXES:
            raise ValueError(
                "Ingest MusicXML/MXL, MSCZ/MSCX or PDF. MIDI is not a notation-preserving source."
            )
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("Source exceeds the 64 MiB ingestion limit")
        target = output / ("source" + path.suffix.lower())
        shutil.copyfile(path, target)
        return Acquired(
            [target], {"original_path": str(path), "original_name": path.name}
        )


class MuseScore:
    name = "musescore"

    def accepts(self, source):
        url = urlparse(str(source))
        return url.scheme in {"http", "https"} and url.hostname in {
            "musescore.com",
            "www.musescore.com",
            "s.musescore.com",
        }

    def acquire(self, source, output):
        import re

        url = urlparse(str(source))
        match = re.fullmatch(r"/(?:user/\d+|[\w-]+)/scores/(\d+)/?", url.path)
        if not match:
            raise ValueError(
                "Use a full MuseScore score URL, not a search, profile or individual-part URL"
            )
        canonical = f"https://musescore.com{url.path.rstrip('/')}"
        files, details = LibreScore().download(canonical, output)
        return Acquired(
            files,
            {
                "url": canonical,
                "musescore_id": match.group(1),
                "license": "Not inferred from downloader availability; retain source notices",
                "acquisition": details,
            },
        )


class DirectNotationURL:
    name = "url"

    def accepts(self, source):
        url = urlparse(str(source))
        return url.scheme in {"http", "https"} and not MuseScore().accepts(source)

    def acquire(self, source, output):
        url = urlparse(str(source))
        if url.username or url.password:
            raise ValueError("Credential-bearing URLs are not accepted")
        suffix = Path(unquote(url.path)).suffix.lower()
        if suffix not in SUFFIXES:
            raise ValueError("Use a direct MusicXML/MXL, MSCZ/MSCX or PDF URL")
        target = output / ("source" + suffix)
        total = 0
        with httpx.stream(
            "GET", str(source), follow_redirects=True, timeout=60
        ) as response:
            response.raise_for_status()
            final_url = str(response.url)
            with target.open("xb") as handle:
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ValueError("Download exceeds the 64 MiB ingestion limit")
                    handle.write(chunk)
        return Acquired([target], {"url": str(source), "resolved_url": final_url})


class PDMX:
    name = "pdmx"

    def accepts(self, source):
        return str(source).startswith("pdmx:")

    def acquire(self, source, output):
        from .pdmx import acquire

        return acquire(source, output)


PROVIDERS = (LocalFile(), MuseScore(), DirectNotationURL(), PDMX())


def provider_catalog():
    return {
        "providers": [
            {"name": "local", "inputs": sorted(SUFFIXES)},
            {
                "name": "musescore",
                "inputs": ["MuseScore full-score URL"],
                "tool": "dl-librescore@0.35.40",
                "url_formats": ["pdf"],
                "native_download_supported": False,
            },
            {
                "name": "url",
                "inputs": ["Direct notation-file URL"],
                "formats": sorted(SUFFIXES),
            },
            {
                "name": "pdmx",
                "inputs": ["pdmx: edition ID from pdmx-search"],
                "record": "15571083",
            },
        ],
        "format_policy": "Preserve originals. Prefer native MSCZ/MSCX for archival fidelity, MusicXML/MXL for arranging. PDF is needs_transcription. Never downgrade notation to MIDI.",
        "native_conversion": "Offline webmscore@1.2.1, the dl-librescore conversion engine",
        "pdf_transcription": {
            "engine": "Audiveris",
            "version": "5.11.0",
            "opt_in": "transcribe_pdf=True / --transcribe-pdf",
            "status": "needs_review even when structural validation passes",
            "setup": "uv run python scripts/bootstrap_audiveris.py",
        },
    }
