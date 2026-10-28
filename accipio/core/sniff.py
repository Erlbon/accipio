"""What a finished file really is, from its first bytes.

Only used when the name gave no type the rules know: a link called ``download`` or ``file.bin`` that a
server sent as ``application/octet-stream``. The extension found here lets the rules place the file again
(and the file open with a double-click). A file with a known extension is never second-guessed.
"""
from __future__ import annotations

import zipfile
from typing import Optional

_SIGNATURES = [
    (0, b"%PDF-", "pdf"),
    (0, b"MZ", "exe"),
    (0, b"\x89PNG\r\n\x1a\n", "png"),
    (0, b"\xff\xd8\xff", "jpg"),
    (0, b"GIF87a", "gif"),
    (0, b"GIF89a", "gif"),
    (0, b"7z\xbc\xaf\x27\x1c", "7z"),
    (0, b"Rar!\x1a\x07", "rar"),
    (0, b"\x1f\x8b", "gz"),
    (0, b"BZh", "bz2"),
    (0, b"\xfd7zXZ\x00", "xz"),
    (0, b"\x28\xb5\x2f\xfd", "zst"),
    (0, b"ID3", "mp3"),
    (0, b"fLaC", "flac"),
    (0, b"OggS", "ogg"),
    (257, b"ustar", "tar"),
]


def _zip_kind(path: str) -> str:
    """A zip can be an epub, a comic, an Office file or a plain archive: look at what is inside."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if "mimetype" in names:
                kind = archive.read("mimetype")[:64].strip()
                if kind == b"application/epub+zip":
                    return "epub"
    except (zipfile.BadZipFile, OSError, KeyError):
        return "zip"
    lower = [n.lower() for n in names]
    if any(n.startswith("word/") for n in lower):
        return "docx"
    if any(n.startswith("xl/") for n in lower):
        return "xlsx"
    if any(n.startswith("ppt/") for n in lower):
        return "pptx"
    files = [n for n in lower if not n.endswith("/")]
    pictures = [n for n in files if n.rsplit(".", 1)[-1] in ("jpg", "jpeg", "png", "webp", "gif", "avif")]
    if files and (len(pictures) >= len(files) - 1 or "comicinfo.xml" in files) and pictures:
        return "cbz"
    return "zip"


def sniff(path: str) -> Optional[str]:
    """The extension (no dot) the content shows, or None when it is not recognised or cannot be read."""
    try:
        with open(path, "rb") as f:
            head = f.read(512)
    except OSError:
        return None
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        return _zip_kind(path)
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        return "m4a" if brand in (b"M4A ", b"M4B ") else "mov" if brand == b"qt  " else "mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm" if b"webm" in head[:64] else "mkv"
    if head.startswith(b"RIFF") and head[8:12] in (b"WAVE", b"AVI "):
        return "wav" if head[8:12] == b"WAVE" else "avi"
    if len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0 and head[1] & 0x06:
        return "mp3"                                   # an MPEG audio frame with no ID3 tag
    for offset, magic, ext in _SIGNATURES:
        if head[offset:offset + len(magic)] == magic:
            return ext
    return None
