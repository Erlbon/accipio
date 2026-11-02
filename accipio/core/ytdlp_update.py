"""Keeping yt-dlp up to date without a new Accipio build.

Video sites change often and an old yt-dlp soon stops working on them, while the copy inside the exe is frozen at
build time. Tools > Update yt-dlp fetches the newest release from PyPI (the wheel, plus the yt-dlp-ejs version it
pins, which solves YouTube's JavaScript challenges), checks each file's SHA-256 against PyPI's, and unpacks them
into ``<config folder>\\yt-dlp\\<version>``. ``current.txt`` beside it names the version in use; it is written last,
so a cancelled or failed update leaves the old one working.

``activate`` runs at every start, before anything imports yt-dlp: it puts that folder at the front of ``sys.path``
so Python finds it before the bundled copy, but only when it is newer than the bundled one (a later Accipio build
may bring a newer yt-dlp than an update made earlier; the older update is then deleted). A change takes effect at
the next start: a running Python cannot swap a package it has already loaded.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import sys
import threading
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Callable, List, Optional, Tuple

from accipio.core.version import APP_NAME, APP_VERSION

PYPI_JSON = "https://pypi.org/pypi/{name}/json"
PYPI_VERSION_JSON = "https://pypi.org/pypi/{name}/{version}/json"
USER_AGENT = f"{APP_NAME}/{APP_VERSION} (yt-dlp update)"
FOLDER = "yt-dlp"
CURRENT = "current.txt"
IN_USE = "in-use.txt"
_EJS_PIN = re.compile(r"^yt-dlp-ejs\s*==\s*([\w.]+)")

Fetch = Callable[[str], bytes]


class UpdateError(Exception):
    """The update could not be fetched or installed; the yt-dlp in use is unchanged."""


class Cancelled(Exception):
    pass


@dataclass
class Wheel:
    url: str
    sha256: str


@dataclass
class Release:
    version: str
    wheels: List[Wheel]                   # yt-dlp first, then yt-dlp-ejs when it pins one


@dataclass
class Result:
    latest: str
    installed: bool                       # a newer yt-dlp was installed just now
    waiting: bool                         # a newer yt-dlp is installed and starts with the next start


def version_key(version: Optional[str]) -> Tuple[int, ...]:
    """``2026.08.19`` and ``2026.8.19`` compare equal; a nightly's extra part sorts after the release. None is oldest."""
    return tuple(int(part) for part in re.findall(r"\d+", version or ""))


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as answer:
            return answer.read()
    except OSError as error:                          # URLError and HTTPError are OSErrors
        raise UpdateError(f"Could not reach PyPI: {error}") from error


def _wheel_of(answer: dict) -> Wheel:
    for item in answer.get("urls", []):
        if item.get("packagetype") == "bdist_wheel" and item.get("filename", "").endswith("-py3-none-any.whl"):
            return Wheel(item["url"], item["digests"]["sha256"])
    raise UpdateError("PyPI lists no wheel for this release")


def latest_release(fetch: Fetch = _fetch) -> Release:
    try:
        answer = json.loads(fetch(PYPI_JSON.format(name="yt-dlp")))
        wheels = [_wheel_of(answer)]
        pins = [m.group(1) for m in map(_EJS_PIN.match, answer["info"].get("requires_dist") or []) if m]
        if pins:
            wheels.append(_wheel_of(json.loads(fetch(PYPI_VERSION_JSON.format(name="yt-dlp-ejs", version=pins[0])))))
        return Release(answer["info"]["version"], wheels)
    except (ValueError, KeyError, TypeError) as error:
        raise UpdateError(f"PyPI's answer could not be read: {error}") from error


def _unpack(blob: bytes, target: str) -> None:
    """Unpack a wheel's packages; refuse any name that would land outside ``target``."""
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            parts = PurePosixPath(name).parts
            if name.startswith("/") or ":" in name or ".." in parts:
                raise UpdateError(f"The download holds a file with an unsafe name: {info.filename}")
            if info.is_dir() or parts[0].endswith(".data"):    # .data: man pages and shell completions
                continue
            path = os.path.join(target, *parts)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as out:
                out.write(archive.read(info))


def installed_version(folder: str) -> Optional[str]:
    """The updated yt-dlp named in ``current.txt``, when its folder is there."""
    root = os.path.join(folder, FOLDER)
    try:
        with open(os.path.join(root, CURRENT), encoding="utf-8") as f:
            version = f.read().strip()
    except OSError:
        return None
    return version if version and os.path.isdir(os.path.join(root, version)) else None


def _tidy(root: str, keep: Optional[str]) -> None:
    """Delete every version folder but ``keep`` (and with no ``keep``, ``current.txt`` too). Best effort."""
    try:
        names = os.listdir(root)
    except OSError:
        return
    for name in names:
        path = os.path.join(root, name)
        if os.path.isdir(path) and name != keep:
            shutil.rmtree(path, ignore_errors=True)
    if keep is None:
        try:
            os.remove(os.path.join(root, CURRENT))
        except OSError:
            pass


def install(folder: str, release: Release, fetch: Fetch = _fetch, cancel: Optional[threading.Event] = None) -> None:
    root = os.path.join(folder, FOLDER)
    work = os.path.join(root, f".{release.version}.part")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    try:
        for wheel in release.wheels:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            blob = fetch(wheel.url)
            if hashlib.sha256(blob).hexdigest() != wheel.sha256.lower():
                raise UpdateError("The download does not match PyPI's checksum; nothing was changed.")
            _unpack(blob, work)
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        target = os.path.join(root, release.version)
        shutil.rmtree(target, ignore_errors=True)
        os.replace(work, target)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    tmp = os.path.join(root, CURRENT + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(release.version)
    os.replace(tmp, os.path.join(root, CURRENT))
    _tidy(root, keep=release.version)


def bundled_version() -> Optional[str]:
    """The yt-dlp inside the exe (or installed with Python), read from its metadata without importing it."""
    try:
        from importlib.metadata import version
        return version("yt-dlp")
    except Exception:
        return None


_NOT_GIVEN = object()


def activate(folder: str, bundled=_NOT_GIVEN) -> Optional[str]:
    """At start, before yt-dlp is imported: use the updated yt-dlp when it is newer than the bundled one. Returns
    its version, or None when the bundled one is used. Never raises: a broken update must not stop Accipio."""
    try:
        version = installed_version(folder)
        if version is None:
            return None
        if bundled is _NOT_GIVEN:
            bundled = bundled_version()
        root = os.path.join(folder, FOLDER)
        if version_key(version) <= version_key(bundled):
            _tidy(root, keep=None)
            return None
        sys.path.insert(0, os.path.join(root, version))
        return version
    except Exception:
        return None


def note_in_use(folder: str) -> str:
    """Write which yt-dlp Python really loaded, and from where, to ``in-use.txt`` (one line, rewritten at every
    start): the proof that an update was picked up, readable without opening any window."""
    import yt_dlp
    from yt_dlp.version import __version__
    line = f"yt-dlp {__version__} from {os.path.dirname(os.path.abspath(yt_dlp.__file__))}\n"
    root = os.path.join(folder, FOLDER)
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, IN_USE), "w", encoding="utf-8") as f:
        f.write(line)
    return line


def update(folder: str, running: Optional[str], fetch: Fetch = _fetch,
           cancel: Optional[threading.Event] = None) -> Result:
    """Install the newest yt-dlp unless the one running (or one waiting for the next start) is as new."""
    release = latest_release(fetch)
    waiting = installed_version(folder)
    have = max(version_key(running), version_key(waiting))
    if version_key(release.version) <= have:
        return Result(release.version, installed=False,
                      waiting=version_key(waiting) > version_key(running))
    install(folder, release, fetch=fetch, cancel=cancel)
    return Result(release.version, installed=True, waiting=True)
