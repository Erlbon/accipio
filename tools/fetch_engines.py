"""Fetch the download engines Accipio ships next to its exe, into ``bin/`` (not committed).

Each file comes from its project's official release and is checked against a SHA-256 pinned here, so a
changed or tampered download is refused. Bumping an engine means changing its URL and hash together.

Only Windows needs this: on Linux and macOS aria2c comes from the system's packages (found on PATH).

Usage:
    python tools/fetch_engines.py
"""
from __future__ import annotations

import hashlib
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin"

ENGINES = {
    "aria2": {
        "url": "https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip",
        "sha256": "67d015301eef0b612191212d564c5bb0a14b5b9c4796b76454276a4d28d9b288",
        # the program, plus the licence texts a release must carry (aria2 is GPL-2.0, its OpenSSL part has its own)
        "keep": ["aria2c.exe", "COPYING", "LICENSE.OpenSSL", "AUTHORS"],
    },
}


def fetch(name: str, spec: dict, bin_dir: Path = BIN) -> Path:
    target = bin_dir / name
    with urllib.request.urlopen(spec["url"], timeout=60) as response:
        data = response.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != spec["sha256"]:
        raise SystemExit(f"ERROR: {name}: the download's SHA-256 is {digest}, expected {spec['sha256']}; nothing written")
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            base = info.filename.rsplit("/", 1)[-1]
            if base in spec["keep"]:
                (target / base).write_bytes(archive.read(info))
    missing = [f for f in spec["keep"] if not (target / f).exists()]
    if missing:
        raise SystemExit(f"ERROR: {name}: not in the archive: {', '.join(missing)}")
    return target


if __name__ == "__main__":
    if sys.platform != "win32":
        print("Not Windows: install aria2 from your system's packages instead.")
        raise SystemExit(0)
    for engine, spec in ENGINES.items():
        print(f"{engine}: {fetch(engine, spec)}")
