"""Build portable TransScribe downloads from the reviewed sdist and pinned upstream sources."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]


def put(archive, name, data, executable=False):
    entry = ZipInfo("TransScribe/" + name)
    entry.create_system = 3
    entry.external_attr = (0o100755 if executable else 0o100644) << 16
    archive.writestr(entry, data, compress_type=ZIP_DEFLATED)


def runtime_bytes(spec, cache):
    url = spec["url"]
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / url.rsplit("/", 1)[1]
    data = (
        path.read_bytes()
        if path.exists()
        else urllib.request.urlopen(url, timeout=120).read()
    )
    if hashlib.sha256(data).hexdigest() != spec["sha256"]:
        raise ValueError(f"Runtime checksum mismatch for {path}")
    if not path.exists():
        path.write_bytes(data)
    if path.suffix == ".zip":
        with ZipFile(BytesIO(data)) as source:
            return source.read("uv.exe")
    with tarfile.open(fileobj=BytesIO(data)) as source:
        return source.extractfile(path.name.removesuffix(".tar.gz") + "/uv").read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sdist", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/desktop")
    parser.add_argument(
        "--cache", type=Path, default=Path.home() / ".cache/transscribe/build"
    )
    specs = json.loads((ROOT / "desktop/runtimes.json").read_text())
    parser.add_argument("--platform", choices=specs)
    args = parser.parse_args()
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/bootstrap_vendor.py")], check=True
    )
    vendor = subprocess.check_output(
        [
            "git",
            "-C",
            str(ROOT / "vendor/music21-mcp"),
            "archive",
            "HEAD",
            "LICENSE",
            "README.md",
            "pyproject.toml",
            "src",
        ]
    )
    args.output.mkdir(parents=True, exist_ok=True)
    for platform, spec in json.loads(
        (ROOT / "desktop/runtimes.json").read_text()
    ).items():
        if args.platform and args.platform != platform:
            continue
        windows = platform.startswith("Windows")
        launcher = "Start TransScribe." + ("cmd" if windows else "command")
        name = "TransScribe-" + platform + ".zip"
        with tempfile.TemporaryDirectory(dir=args.output) as temp:
            result = Path(temp) / name
            with ZipFile(result, "x") as bundle:
                with tarfile.open(args.sdist) as source:
                    for member in source:
                        relative = Path(member.name).parts[1:]
                        if (
                            not relative
                            or relative[0] in {".github", "desktop"}
                            or member.isdir()
                        ):
                            continue
                        # Materialize repository links as files for Windows Explorer.
                        handle = source.extractfile(member)
                        if handle is not None:
                            put(bundle, "/".join(relative), handle.read())
                with tarfile.open(fileobj=BytesIO(vendor)) as source:
                    for member in source:
                        if member.isfile():
                            put(
                                bundle,
                                "vendor/music21-mcp/" + member.name,
                                source.extractfile(member).read(),
                            )
                put(bundle, launcher, (ROOT / "desktop" / launcher).read_bytes(), True)
                put(
                    bundle,
                    "runtime/" + ("uv.exe" if windows else "uv"),
                    runtime_bytes(spec, args.cache),
                    True,
                )
                for license_path in sorted((ROOT / "desktop/licenses").glob("*.txt")):
                    put(
                        bundle,
                        "runtime/" + license_path.name,
                        license_path.read_bytes(),
                    )
            os.replace(result, args.output / name)
        print(name, flush=True)


if __name__ == "__main__":
    main()
