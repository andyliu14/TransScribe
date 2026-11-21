"""Reproduce the selected PDMX source without extracting the entire dataset.

Usage: uv run python scripts/fetch_source.py [--archive /path/to/mxl.tar.gz]
The remote gzip archive is ~1.9 GB; streaming stops at the selected member.
"""

import argparse
import hashlib
import json
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = "https://zenodo.org/api/records/15571083/files/mxl.tar.gz/content"
MEMBER = "mxl/2/32/QmcMF7zhLai37n5i1GK4uFimeeysZx7xV9K6conDJaX7Qr.mxl"
DEST = ROOT / "sources/handel-halvorsen-violin-viola.mxl"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive", type=Path)
    args = p.parse_args()
    raw = (
        args.archive.open("rb")
        if args.archive
        else urllib.request.urlopen(URL, timeout=120)
    )
    with raw, tarfile.open(fileobj=raw, mode="r|gz") as archive:
        for member in archive:
            if member.name.lstrip("./") != MEMBER:
                continue
            data = archive.extractfile(member).read()
            provenance_path = ROOT / "sources/provenance.json"
            if provenance_path.exists():
                expected = json.loads(provenance_path.read_text())["sha256"]
                if hashlib.sha256(data).hexdigest() != expected:
                    raise ValueError("Source checksum mismatch")
            DEST.parent.mkdir(parents=True, exist_ok=True)
            DEST.write_bytes(data)
            from music_arranger.ingest import normalize_tuplet_ticks

            normalize_tuplet_ticks(DEST, ROOT / "sources/handel-halvorsen.musicxml")
            print(DEST)
            return
    raise FileNotFoundError(MEMBER)


if __name__ == "__main__":
    main()
