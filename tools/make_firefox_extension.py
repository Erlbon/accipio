"""Build the Firefox version of the browser extension from the one in ``extension/``.

Firefox runs the same scripts; only the manifest differs (decided 2026-11-17: one code base, two manifests):

- no ``key`` (Chrome's way to fix the extension ID); Firefox takes the ID from ``browser_specific_settings``
- an event page (``background.scripts``) where Chrome has a service worker
- the declaration Mozilla demands since 2025-11-03 of what the extension passes on: the addresses of a download and
  its page (``browsingActivity``) and that site's cookies (``websiteContent``), which go to the Accipio app on the
  same PC and nowhere else. Built-in consent needs Firefox 140, hence ``strict_min_version``.

Usage::

    python tools/make_firefox_extension.py            # writes dist/firefox/ and dist/accipio-firefox-<version>.zip

The zip is what Mozilla signs (``web-ext sign --channel unlisted``, or the Developer Hub's "On your own"); the
signed ``.xpi`` it gives back is what users install. Unsigned, the folder loads only as a temporary add-on
(about:debugging), gone at the next start of Firefox.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import zipfile
from typing import Dict, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "extension")
FIREFOX_ID = "accipio@erlbon.github.io"            # permanent: changing it makes every user install anew
MIN_FIREFOX = "140.0"
MIN_FIREFOX_ANDROID = "142.0"
SKIP = {"package.json"}                             # only marks the scripts as modules for node --check


def firefox_manifest(chrome: Dict) -> Dict:
    manifest = {k: v for k, v in chrome.items() if k != "key"}
    worker = chrome["background"]["service_worker"]
    manifest["background"] = {"scripts": [worker], "type": chrome["background"].get("type", "module")}
    manifest["browser_specific_settings"] = {
        "gecko": {
            "id": FIREFOX_ID,
            "strict_min_version": MIN_FIREFOX,
            "data_collection_permissions": {"required": ["browsingActivity", "websiteContent"]},
        },
        # not meant for Android (no Accipio there); the version only keeps Mozilla's validator quiet
        "gecko_android": {"strict_min_version": MIN_FIREFOX_ANDROID},
    }
    return manifest


def build(target: Optional[str] = None, source: str = SOURCE) -> str:
    """Write the Firefox extension folder and a zip beside it; returns the zip's path."""
    target = target or os.path.join(ROOT, "dist", "firefox")
    if os.path.isdir(target):
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=lambda folder, names: [n for n in names if n in SKIP])
    with open(os.path.join(source, "manifest.json"), encoding="utf-8") as f:
        manifest = firefox_manifest(json.load(f))
    with open(os.path.join(target, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    packed = os.path.join(os.path.dirname(target), f"accipio-firefox-{manifest['version']}.zip")
    with zipfile.ZipFile(packed, "w", zipfile.ZIP_DEFLATED) as archive:
        for folder, _, names in os.walk(target):
            for name in sorted(names):
                path = os.path.join(folder, name)
                archive.write(path, os.path.relpath(path, target).replace(os.sep, "/"))
    return packed


if __name__ == "__main__":
    print(build(sys.argv[1] if len(sys.argv) > 1 else None))
