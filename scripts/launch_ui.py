"""Start the local arranging workbench from a fresh source checkout."""

import shutil
import subprocess
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    uv = shutil.which("uv")
    if not uv or not shutil.which("git"):
        raise SystemExit("Install Git and uv, then run this launcher again.")
    subprocess.run(
        [sys.executable, "scripts/bootstrap_vendor.py"], cwd=root, check=True
    )
    return subprocess.call(
        [uv, "run", "--locked", "transscribe", "ui", *sys.argv[1:]], cwd=root
    )


if __name__ == "__main__":
    raise SystemExit(main())
