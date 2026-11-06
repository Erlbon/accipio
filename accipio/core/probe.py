"""Find out what a link really is before downloading it: the file name, the type and the size.

The rules need a file name to place a download, and the link often does not show one: ``get.php?id=7``,
a redirect to a mirror, or a name the server only gives in its Content-Disposition header. One quick
request answers it: a HEAD, or, for servers that refuse HEAD, a GET for the first byte only.

The name is found in this order: the server's Content-Disposition, the last part of the final URL after
redirects, then "download". When the name has no extension the rules know and the server's content type
has one (a ``.php`` that is a zip), that extension is used instead, so the file can be opened.
"""
from __future__ import annotations

import mimetypes
import posixpath
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import Message
from typing import Dict, Optional
from urllib.parse import unquote, urlsplit

from accipio.core import rules
from accipio.core.version import APP_NAME, APP_VERSION

USER_AGENT = f"Mozilla/5.0 (compatible; {APP_NAME}/{APP_VERSION})"

# MIME types Python's own table does not know, or maps to an extension the rules do not use
EXTENSIONS = {
    "application/epub+zip": "epub", "application/x-mobipocket-ebook": "mobi",
    "application/vnd.comicbook+zip": "cbz", "application/vnd.comicbook-rar": "cbr",
    "application/x-7z-compressed": "7z", "application/vnd.rar": "rar", "application/x-rar-compressed": "rar",
    "application/x-zip-compressed": "zip", "application/x-msdownload": "exe", "application/x-msi": "msi",
    "application/vnd.microsoft.portable-executable": "exe", "video/x-matroska": "mkv", "audio/flac": "flac",
    "audio/mpeg": "mp3", "image/jpeg": "jpg", "application/gzip": "gz",
}
SCRIPT_EXTENSIONS = {"php", "asp", "aspx", "cgi", "jsp", "do", "pl", "py"}
_TABLE = mimetypes.MimeTypes()         # Python's built-in table only, not the Windows registry: same on every machine


class ProbeError(RuntimeError):
    """The server said no (404, 403...) or could not be reached; the message is fit to show."""


class WebPageError(ProbeError):
    """The link is a web page, not a file: a file hoster's landing page (often with a captcha) that the file only
    comes after. Downloading it would save the page under the file's place (decided 2026-11-06, Q59A)."""


WEB_PAGE_MESSAGE = ("this link is a web page, not a file: the site wants you to click through it first (often past a "
                    "captcha). Open it in the browser (Downloads > Open Link in Browser) and download from there.")
PAGE_TYPES = {"text/html", "application/xhtml+xml"}
PAGE_EXTENSIONS = {"htm", "html", "xhtml", "shtml", "mht", "mhtml"}


@dataclass
class Probe:
    url: str                       # after redirects
    filename: str
    mime: str
    size: Optional[int]
    resumable: bool                # the server takes byte ranges, so aria2 can resume and split


def filename_from_disposition(value: str) -> str:
    """The file name in a Content-Disposition header, ``filename*=UTF-8''...`` preferred; "" when none."""
    if not value:
        return ""
    star = re.search(r"filename\*\s*=\s*([^;]+)", value, re.IGNORECASE)
    name = ""
    if star:                                   # RFC 5987: charset'language'percent-encoded
        charset, _, rest = star.group(1).strip().strip('"').partition("'")
        _, _, encoded = rest.partition("'")
        try:
            name = unquote(encoded, encoding=charset or "utf-8", errors="strict")
        except (LookupError, UnicodeDecodeError):
            name = ""
    if not name:
        message = Message()
        message["content-disposition"] = value
        name = message.get_filename() or ""
    return posixpath.basename(name.replace("\\", "/")).strip()


def filename_from_url(url: str) -> str:
    path = urlsplit(url).path
    return posixpath.basename(unquote(path)).strip()


def extension_for(mime: str) -> str:
    """The extension the rules know for a MIME type, or ""."""
    mime = mime.split(";")[0].strip().lower()
    ext = EXTENSIONS.get(mime) or (_TABLE.guess_extension(mime) or "").lstrip(".")
    return ext if ext in rules.KNOWN_EXTENSIONS else ""


def choose_filename(disposition: str, final_url: str, mime: str) -> str:
    name = filename_from_disposition(disposition)
    from_server = bool(name)
    if not name:
        name = filename_from_url(final_url) or "download"
    stem, ext = rules.split_name(name)
    better = extension_for(mime)
    if better and not from_server and ext not in rules.KNOWN_EXTENSIONS:
        # a script's extension is replaced (get.php -> get.zip); anything else keeps its name (report.2024 -> report.2024.pdf)
        name = f"{stem}.{better}" if ext in SCRIPT_EXTENSIONS else f"{name}.{better}"
    return rules.safe_part(name, "download")


def _open(request: urllib.request.Request, timeout: float):
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, OSError) as error:
        reason = getattr(error, "reason", error)
        raise ProbeError(f"could not reach the server: {reason}") from None


def probe(url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 15.0) -> Probe:
    """Ask the server about a link without downloading it."""
    sent = {"User-Agent": USER_AGENT}
    sent.update(headers or {})
    try:
        response = _open(urllib.request.Request(url, headers=sent, method="HEAD"), timeout)
        ranged = False
    except urllib.error.HTTPError as error:
        if error.code not in (400, 403, 405, 501):
            raise ProbeError(f"the server answered {error.code} {error.reason}") from None
        response = None
    if response is None:                       # a server that refuses HEAD: ask for one byte instead
        try:
            response = _open(urllib.request.Request(url, headers={**sent, "Range": "bytes=0-0"}), timeout)
        except urllib.error.HTTPError as error:
            raise ProbeError(f"the server answered {error.code} {error.reason}") from None
        ranged = response.status == 206
    with response:
        info = response.headers
        final_url = response.geturl()
        mime = (info.get("Content-Type") or "").split(";")[0].strip().lower()
        size: Optional[int] = None
        content_range = info.get("Content-Range") or ""
        match = re.search(r"/(\d+)\s*$", content_range)
        if match:
            size = int(match.group(1))
        elif not ranged and (info.get("Content-Length") or "").isdigit():
            size = int(info["Content-Length"])
        resumable = ranged or (info.get("Accept-Ranges") or "").lower() == "bytes"
        disposition = info.get("Content-Disposition") or ""
        filename = choose_filename(disposition, final_url, mime)
    if mime in PAGE_TYPES and not filename_from_disposition(disposition) \
            and rules.split_name(filename_from_url(final_url))[1] not in PAGE_EXTENSIONS:
        raise WebPageError(WEB_PAGE_MESSAGE)        # a page asked for by name (.html), or handed over as a file, is fine
    return Probe(final_url, filename, mime, size, resumable)
