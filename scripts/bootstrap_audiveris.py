"""Install the pinned optional Linux OMR engine in the user's cache, without sudo."""

import hashlib
import json
import platform
import subprocess
from pathlib import Path

import httpx


def main():
    root = Path(__file__).resolve().parents[1]
    pinned = json.loads((root / "tools/audiveris/upstream.json").read_text())
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "amd64"}:
        raise SystemExit("This pinned installer supports Linux x86_64.")
    cache = Path.home() / ".cache/music-arranger" / f"audiveris-{pinned['version']}"
    cache.mkdir(parents=True, exist_ok=True)
    package = cache / pinned["asset"]
    if not package.exists():
        download = package.with_suffix(".download")
        try:
            with httpx.stream(
                "GET", pinned["url"], follow_redirects=True, timeout=120
            ) as response:
                response.raise_for_status()
                with download.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
            if hashlib.sha256(download.read_bytes()).hexdigest() != pinned["sha256"]:
                raise ValueError("Audiveris release checksum mismatch")
            download.rename(package)
        finally:
            download.unlink(missing_ok=True)
    if hashlib.sha256(package.read_bytes()).hexdigest() != pinned["sha256"]:
        raise ValueError("Cached Audiveris release checksum mismatch")
    executable = cache / "opt/audiveris/bin/Audiveris"
    if not executable.is_file():
        subprocess.run(["dpkg-deb", "-x", str(package), str(cache)], check=True)
    model = pinned["text_ocr"]
    tessdata = cache / "tessdata/eng.traineddata"
    tessdata.parent.mkdir(exist_ok=True)
    if not tessdata.exists():
        response = httpx.get(model["url"], follow_redirects=True, timeout=120)
        response.raise_for_status()
        if hashlib.sha256(response.content).hexdigest() != model["sha256"]:
            raise ValueError("Tesseract language model checksum mismatch")
        tessdata.write_bytes(response.content)
    if hashlib.sha256(tessdata.read_bytes()).hexdigest() != model["sha256"]:
        raise ValueError("Cached Tesseract language model checksum mismatch")
    (cache / "installation.json").write_text(json.dumps(pinned, indent=2) + "\n")
    print(executable)


if __name__ == "__main__":
    main()
