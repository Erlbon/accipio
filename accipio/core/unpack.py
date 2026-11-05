"""Unpacking a downloaded archive (a trial since 2026-11-05, decision 43E): look inside, suggest what to do, put
each file where it belongs, and take it all back with Undo.

Three choices, picked by the user on the archive's row or remembered in a rule:
- ``FOLDER``: everything goes to ``<folder>\\<archive name>\\``, together (a program with its readme).
- ``SORT``: each file goes through the rules as if it had been downloaded on its own (decision 40B); a file no
  rule fits goes to ``Default\\<archive name>\\``.
- ``KEEP``: the archive stays packed.

Only real archive endings are opened (decision 42): .zip and .tar with its compressed forms. Formats that are
zips underneath (.cbz, .epub, .docx, .jar, .apk) never are. rar and 7z need 7-Zip and come later (45: A, then C).

Safety: no entry can land outside its folder (``..``, absolute paths and drive letters are taken apart), an
archive that would unpack to more than ``LIMIT`` bytes is refused before anything is written, links and devices
inside a tar are skipped, nothing is ever overwritten, and every file gets Windows' internet mark as the archive
had it (as Windows' own unzip does). A failure halfway removes what was written.

This module is Qt-free; the queue calls it on its worker thread.
"""
from __future__ import annotations

import os
import shutil
import tarfile
import zipfile
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from accipio.core import rules, zone

FOLDER, SORT, KEEP = "folder", "sort", "keep"
ENDINGS = ("zip", "tar", "tar.gz", "tgz", "tar.bz2", "tbz2", "tar.xz", "txz")
PROGRAM_EXTENSIONS = {"exe", "msi", "msix", "appx", "bat", "cmd", "ps1"}
LIMIT = 50 * 1024 ** 3               # bytes an archive may unpack to; more is refused (a zip bomb, or a mistake)
MAX_FILES = 20_000


class UnpackError(Exception):
    """Why an archive cannot be opened or unpacked, in words for the row."""


@dataclass
class Entry:
    name: str                        # the path inside the archive, with /
    size: int


@dataclass
class Move:
    entry: str                       # the path inside the archive
    target: str                      # the full path it is written to
    why: str = ""                    # for sorting: the rule that placed it


@dataclass
class Done:
    """What an unpack wrote, so Undo can take exactly that back."""
    archive: str
    files: List[Tuple[str, int, float]] = field(default_factory=list)    # path, size, mtime as written
    folders: List[str] = field(default_factory=list)                      # created, deepest last


def is_archive(filename: str) -> bool:
    lower = filename.lower()
    return any(lower.endswith("." + ending) and len(lower) > len(ending) + 1 for ending in ENDINGS)


def _is_zip(path: str) -> bool:
    return path.lower().endswith(".zip")


# ---- looking inside ------------------------------------------------------------------------------------------

def contents(path: str, limit: int = LIMIT) -> List[Entry]:
    """The files inside (folders, links and devices left out). Raises UnpackError for a password, a damaged
    archive, or one that would unpack to more than ``limit`` bytes."""
    try:
        if _is_zip(path):
            with zipfile.ZipFile(path) as z:
                infos = [i for i in z.infolist() if not i.is_dir()]
                if any(i.flag_bits & 0x1 for i in infos):
                    raise UnpackError("This zip needs a password; Accipio leaves it packed.")
                entries = [Entry(i.filename, i.file_size) for i in infos]
        else:
            with tarfile.open(path) as t:
                entries = [Entry(m.name, m.size) for m in t.getmembers() if m.isfile()]
    except UnpackError:
        raise
    except (zipfile.BadZipFile, tarfile.TarError, EOFError, OSError, ValueError) as error:
        raise UnpackError(f"The archive is damaged or not what its name says ({error}).") from error
    if len(entries) > MAX_FILES:
        raise UnpackError(f"The archive holds {len(entries):,} files; Accipio leaves it packed.")
    if sum(e.size for e in entries) > limit:
        raise UnpackError("The archive would unpack too large; Accipio leaves it packed.")
    return entries


def suggest(entries: List[Entry], known: Callable[[str], bool]) -> str:
    """The button to offer first: a program inside means keep it together; files the rules all know mean sort
    them; anything else stays packed until the user says."""
    names = [_parts(e.name)[-1] for e in entries if _parts(e.name)]
    if not names:
        return KEEP
    if any(rules.split_name(n)[1] in PROGRAM_EXTENSIONS for n in names):
        return FOLDER
    return SORT if all(known(n) for n in names) else KEEP


# ---- where each file goes --------------------------------------------------------------------------------------

def _parts(name: str) -> List[str]:
    """The path inside the archive as safe folder and file names: no "..", no root, no drive letter."""
    pieces = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    return [rules.safe_part(p) for p in pieces]


def _common_top(entries: List[Entry]) -> int:
    """1 when every file sits in the same top folder (tool/...), which "as a folder" then does not repeat."""
    tops = {tuple(_parts(e.name)[:1]) for e in entries}
    return 1 if len(tops) == 1 and all(len(_parts(e.name)) > 1 for e in entries) else 0


def _free_folder(parent: str, name: str, exists: Callable[[str], bool]) -> str:
    candidate, n = os.path.join(parent, name), 1
    while exists(candidate):
        candidate = os.path.join(parent, f"{name} ({n})")
        n += 1
    return candidate


def _stem(archive_name: str) -> str:
    return rules.safe_part(rules.split_name(os.path.basename(archive_name))[0], "archive")


def plan_folder(entries: List[Entry], folder: str, archive_name: str, *, exists: Callable[[str], bool]) -> List[Move]:
    """Everything into ``folder\\<archive name>\\``, keeping the folders inside; a new name if that folder exists."""
    base = _free_folder(os.path.normpath(folder), _stem(archive_name), exists)
    skip = _common_top(entries)
    moves = []
    for e in entries:
        parts = _parts(e.name)[skip:] or ["file"]
        moves.append(Move(e.name, os.path.join(base, *parts)))
    return moves


def plan_sort(entries: List[Entry], place: Callable[[str], Optional[Tuple[str, str, str]]], default_folder: str,
              archive_name: str, *, exists: Callable[[str], bool]) -> List[Move]:
    """Each file through the rules (``place`` gives folder, name and why, or None when no rule fits); the rest
    to ``Default\\<archive name>\\`` with the folders it had inside."""
    rest = os.path.join(os.path.normpath(default_folder), _stem(archive_name))
    taken = set()
    moves = []
    for e in entries:
        parts = _parts(e.name) or ["file"]
        placed = place(parts[-1])
        if placed is None:
            folder, name, why = os.path.join(rest, *parts[:-1]), parts[-1], "no rule fits"
        else:
            folder, name, why = placed
        name = rules.free_name(folder, name, lambda p: exists(p) or os.path.normcase(p) in taken)
        target = os.path.join(folder, name)
        taken.add(os.path.normcase(target))
        moves.append(Move(e.name, target, why))
    return moves


# ---- doing it ------------------------------------------------------------------------------------------------

def _copy_member(source, target: str) -> None:
    temporary = target + ".accipio-part"
    with open(temporary, "wb") as out:
        shutil.copyfileobj(source, out, 1024 * 1024)
    os.replace(temporary, target)


def _make_folders(folder: str, done: Done) -> None:
    missing = []
    while folder and not os.path.isdir(folder):
        missing.append(folder)
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    for path in reversed(missing):
        os.mkdir(path)
        done.folders.append(path)


def extract(archive: str, moves: List[Move], url: str = "", page_url: str = "") -> Done:
    """Write every move's file. Never overwrites; a failure removes what was written and raises UnpackError."""
    done = Done(archive)
    try:
        if _is_zip(archive):
            with zipfile.ZipFile(archive) as z:
                for m in moves:
                    _write(done, m, lambda name=m.entry: z.open(name), url, page_url)
        else:
            with tarfile.open(archive) as t:
                for m in moves:
                    _write(done, m, lambda name=m.entry: t.extractfile(name), url, page_url)
    except (OSError, zipfile.BadZipFile, tarfile.TarError, KeyError, EOFError) as error:
        undo(done)
        raise UnpackError(f"Unpacking stopped: {error}. Nothing was left behind.") from error
    return done


def _write(done: Done, move: Move, opener, url: str, page_url: str) -> None:
    if os.path.exists(move.target):
        raise FileExistsError(f"{move.target} appeared meanwhile")
    _make_folders(os.path.dirname(move.target), done)
    source = opener()
    if source is None:
        raise KeyError(move.entry)
    with source:
        _copy_member(source, move.target)
    zone.mark(move.target, url, page_url)
    stat = os.stat(move.target)                       # after the mark: writing it changes the file's time
    done.files.append((move.target, stat.st_size, stat.st_mtime))


def undo(done: Done) -> List[str]:
    """Delete what the unpack wrote and the folders it made (when empty). A file changed since is kept;
    returns those."""
    kept = []
    for path, size, mtime in reversed(done.files):
        try:
            stat = os.stat(path)
        except OSError:
            continue
        if (stat.st_size, stat.st_mtime) != (size, mtime):
            kept.append(path)
            continue
        os.remove(path)
    for folder in reversed(done.folders):
        try:
            os.rmdir(folder)
        except OSError:
            pass                                     # not empty: something else is in it now
    return kept
