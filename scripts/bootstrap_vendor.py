"""Clone and check out the reviewed music21 MCP revision; never pulls implicitly."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/cclawton/music21-mcp.git"
REV = "621e7ab34223b3c8f6bffa5e50e52b9e18843d90"
DEST = ROOT / "vendor/music21-mcp"
if not DEST.exists():
    subprocess.run(["git", "clone", REPO, str(DEST)], check=True)
    subprocess.run(["git", "-C", str(DEST), "checkout", "--detach", REV], check=True)
else:
    actual = subprocess.check_output(
        ["git", "-C", str(DEST), "rev-parse", "HEAD"], text=True
    ).strip()
    if actual != REV:
        raise SystemExit(
            f"Existing vendor checkout is {actual}; expected {REV}. Resolve manually to preserve local work."
        )
print(f"{REPO} @ {REV}")
