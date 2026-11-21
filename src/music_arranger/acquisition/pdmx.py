"""Search the pinned PDMX catalog and acquire a selected MusicXML edition."""

import csv
import hashlib
import os
import re
import shutil
import tarfile
import tempfile
from functools import lru_cache
from pathlib import Path

import httpx
from music21 import instrument

RECORD = "https://zenodo.org/records/15571083"
# Checksums published in the Zenodo record.
FILES = {
    "PDMX.csv": ("30392ccf38bb63ce70e7afae70f9c88c", 230_000_000),
    "mxl.tar.gz": ("49ffd75ecf5489c0be6d41182eb11ff7", 2_000_000_000),
}
MEMBER = re.compile(r"mxl/[A-Za-z0-9]+/[A-Za-z0-9]+/[A-Za-z0-9]+\.mxl")


def dataset_dir():
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return Path(os.environ.get("PDMX_DIR", cache / "music-arranger/pdmx/15571083"))


def dataset_file(name):
    checksum, maximum = FILES[name]
    path = dataset_dir() / name
    if path.is_file():
        with path.open("rb") as source:
            if hashlib.file_digest(source, "md5").hexdigest() != checksum:
                raise ValueError(
                    f"Checksum mismatch for {path}; expected PDMX record 15571083"
                )
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
        download = Path(tmp) / name
        digest = hashlib.md5()
        try:
            with httpx.stream(
                "GET",
                f"{RECORD}/files/{name}?download=1",
                follow_redirects=True,
                timeout=90,
            ) as response:
                response.raise_for_status()
                with download.open("wb") as dest:
                    for chunk in response.iter_bytes(1024 * 1024):
                        if dest.tell() + len(chunk) > maximum:
                            raise ValueError(f"Oversized PDMX download: {name}")
                        dest.write(chunk)
                        digest.update(chunk)
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"Could not retrieve {name} from {RECORD}. "
                f"Place the pinned file in {path.parent} or set PDMX_DIR. {exc}"
            ) from exc
        if digest.hexdigest() != checksum:
            raise ValueError(f"Incomplete or incorrect PDMX download: {name}")
        download.replace(path)
    return path


def rows():
    with dataset_file("PDMX.csv").open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            member = row["mxl"].removeprefix("./")
            if MEMBER.fullmatch(member):
                yield member, row


@lru_cache(maxsize=128)
def instrument_name(program):
    return instrument.instrumentFromMidiProgram(program).instrumentName


def edition(member, row):
    names = [
        instrument_name(int(i))
        for i in row["tracks"].split("-")
        if i.isdecimal() and int(i) < 128
    ]
    return {
        "source": f"pdmx:{member}",
        "title": row["title"],
        "song_name": row["song_name"],
        "composer": row["composer_name"],
        "artist": row["artist_name"],
        "n_tracks": row["n_tracks"],
        "track_programs": row["tracks"],
        "instruments": names,
        "license": row["license"],
        "license_url": row["license_url"],
        "license_conflict": {"true": True, "false": False}.get(
            row["license_conflict"].lower()
        ),
    }


def search(query, composer="", limit=10, offset=0):
    """Find downloadable editions in stable catalog order."""
    terms = re.findall(r"[^\W_]+", query.casefold())
    composer = composer.casefold().strip()
    if not terms and not composer:
        raise ValueError("Supply search terms or a composer")
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("Use a limit from 1 to 100 and a nonnegative offset")
    matches = []
    skipped = 0
    for member, row in rows():
        if composer and composer not in row["composer_name"].casefold():
            continue
        info = edition(member, row)
        fields = [
            info["title"],
            info["song_name"],
            info["composer"],
            info["artist"],
            *info["instruments"],
        ]
        haystack = " ".join(fields).casefold()
        if not all(term in haystack for term in terms):
            continue
        if skipped < offset:
            skipped += 1
            continue
        matches.append(info)
        if len(matches) > limit:
            break
    return {
        "dataset": RECORD,
        "results": matches[:limit],
        "next_offset": offset + limit if len(matches) > limit else None,
    }


def acquire(source, output):
    from .providers import MAX_BYTES, Acquired

    member = source.removeprefix("pdmx:")
    if not MEMBER.fullmatch(member):
        raise ValueError("Use the exact pdmx: source identifier returned by search")
    row = next((row for path, row in rows() if path == member), None)
    if row is None:
        raise ValueError(f"Edition absent from the pinned PDMX catalog: {member}")
    target = output / "source.mxl"
    local = dataset_dir() / member
    if local.is_file():
        if (
            not local.resolve().is_relative_to(dataset_dir().resolve())
            or local.stat().st_size > MAX_BYTES
        ):
            raise ValueError("Invalid or oversized local PDMX score")
        shutil.copyfile(local, target)
        origin = "local_tree"
    else:
        archive = dataset_file("mxl.tar.gz")
        with tarfile.open(archive, "r|gz") as tar:
            for item in tar:
                if item.name.removeprefix("./") == member:
                    if not item.isfile() or not 0 < item.size <= MAX_BYTES:
                        raise ValueError("Invalid or oversized PDMX archive member")
                    with tar.extractfile(item) as score, target.open("xb") as dest:
                        shutil.copyfileobj(score, dest)
                    break
            else:
                raise ValueError(f"Edition missing from archive: {member}")
        origin = "verified_archive"
    return Acquired(
        [target],
        {
            "dataset": RECORD,
            "catalog_md5": FILES["PDMX.csv"][0],
            "member": member,
            "retrieval": origin,
            **edition(member, row),
        },
    )
