"""Windows' "this came from the internet" mark (the Mark of the Web), as Chrome and Edge write it.

A downloaded file gets a small NTFS stream, ``file:Zone.Identifier``, saying zone 3 (the internet) and where it
came from. That mark is what makes SmartScreen warn before a downloaded program runs and Office open a
downloaded document in Protected View. aria2 and yt-dlp do not write it, so without this a program Accipio took
over from Chrome would run with no warning at all.

Drives without NTFS streams (FAT32 sticks, some network shares) cannot hold the mark; the file is kept as it is,
exactly as Chrome does there. Other systems have no such mark, so nothing happens off Windows.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit

INTERNET = 3


def _without_password(url: str) -> str:
    """The address without a user name or password (Chrome leaves those out of the mark too)."""
    parts = urlsplit(url)
    if "@" not in parts.netloc:
        return url
    return urlunsplit(parts._replace(netloc=parts.netloc.rsplit("@", 1)[1]))


def text(url: str, page_url: str = "") -> str:
    lines = ["[ZoneTransfer]", f"ZoneId={INTERNET}"]
    if page_url.startswith(("http://", "https://")):
        lines.append(f"ReferrerUrl={_without_password(page_url)}")
    if url.startswith(("http://", "https://", "ftp://")):
        lines.append(f"HostUrl={_without_password(url)}")
    return "\r\n".join(lines) + "\r\n"


def _write(path: str, content: str) -> None:
    with open(path + ":Zone.Identifier", "w", encoding="utf-8", newline="") as stream:
        stream.write(content)


def mark(path: str, url: str, page_url: str = "") -> bool:
    """Mark a finished download as coming from the internet. False where the drive or system cannot hold it."""
    if os.name != "nt" or not os.path.isfile(path):
        return False
    try:
        _write(path, text(url, page_url))
    except OSError:
        return False
    return True
