"""Rules: where a download goes, and what happens to it.

A rule set is an ordered list (the editor's drag order). A download goes through it from the top, and the
first rule whose conditions ALL match decides: the folder it goes to and how it is renamed. No rule matches:
the Default folder, name kept. (Until 2026-11-04#05 a rule could "continue" so the next one added a
subfolder; ``modernize`` turns such rules into ones with a folder of their own.)

Everything here is plain data and functions, with no Qt and no disk access, so the editor's test box, the
start of a download and the re-check when it finishes all call the same ``place()``.
"""
from __future__ import annotations

import datetime
import fnmatch
import functools
import json
import os
import re
from dataclasses import asdict, dataclass, field, fields
from typing import Callable, Dict, List, Optional, Tuple, Union
from urllib.parse import urlsplit

from accipio.core import cron

# ---- file types ------------------------------------------------------------------------------------------

GROUPS: Dict[str, List[str]] = {
    "Video": ["mp4", "mkv", "webm", "avi", "mov", "wmv", "flv", "m4v", "mpg", "mpeg", "ts", "m2ts", "3gp", "ogv", "vob"],
    "Audio": ["mp3", "flac", "m4a", "aac", "ogg", "oga", "opus", "wav", "wma", "aiff", "ape", "alac", "mka"],
    "Images": ["jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff", "svg", "heic", "avif", "ico", "raw", "cr2", "nef", "dng"],
    "Documents": ["pdf", "doc", "docx", "odt", "rtf", "txt", "md", "xls", "xlsx", "ods", "csv", "ppt", "pptx", "odp"],
    "Ebooks": ["epub", "mobi", "azw", "azw3", "kfx", "fb2", "djvu", "lit"],
    "Comics": ["cbz", "cbr", "cb7", "cbt", "cba"],
    "Archives": ["zip", "7z", "rar", "tar", "gz", "bz2", "xz", "zst", "tgz", "tar.gz", "tar.bz2", "tar.xz", "tar.zst", "iso"],
    "Programs": ["exe", "msi", "msix", "appx", "bat", "cmd", "ps1", "apk", "deb", "rpm", "appimage", "dmg", "pkg"],
}

# Used only when the file name has no extension any group knows (a link ending in .php, or no name at all).
GROUP_MIMES: Dict[str, List[str]] = {
    "Video": ["video/*"],
    "Audio": ["audio/*"],
    "Images": ["image/*"],
    "Documents": ["application/pdf", "application/msword", "application/vnd.openxmlformats-officedocument.*",
                  "application/vnd.oasis.opendocument.*", "text/plain", "text/csv", "text/markdown"],
    "Ebooks": ["application/epub+zip", "application/x-mobipocket-ebook"],
    "Comics": ["application/vnd.comicbook+zip", "application/vnd.comicbook-rar"],
    "Archives": ["application/zip", "application/x-zip-compressed", "application/x-7z-compressed",
                 "application/x-rar-compressed", "application/vnd.rar", "application/gzip", "application/x-tar",
                 "application/x-bzip2", "application/x-xz", "application/zstd"],
    "Programs": ["application/x-msdownload", "application/x-msi", "application/vnd.microsoft.portable-executable",
                 "application/vnd.android.package-archive"],
}

_COMPOUND = sorted((e for exts in GROUPS.values() for e in exts if "." in e), key=len, reverse=True)
KNOWN_EXTENSIONS = {e for exts in GROUPS.values() for e in exts}


@dataclass
class Group:
    """What a download is: a named group the user can edit, which a file belongs to when ANY one entry fits
    (an extension, a name wildcard such as "*invoice*", or a MIME type). A MIME type only counts when the
    file's extension is in no group, so "report.php" sent as a PDF is a document but "film.mkv" sent as
    text/plain is still video. Where a download comes from is a SiteGroup, never part of a Group."""
    name: str
    extensions: List[str] = field(default_factory=list)
    mimes: List[str] = field(default_factory=list)
    names: List[str] = field(default_factory=list)


@dataclass
class SiteGroup:
    """Where a download comes from: sites (``example.com`` covers its subdomains, wildcards allowed), matched
    against the page the link was clicked on, the file's own host, or either (``where``). Link sites such as
    a comics blog hand out files from hosters, so "page" or "either" is what catches them."""
    name: str
    sites: List[str] = field(default_factory=list)
    where: str = "either"


@dataclass
class Folder:
    """A named place downloads go: an absolute path, or one relative to the base folder; tokens allowed."""
    name: str
    path: str


@dataclass
class Library:
    """What rules point at by name: the folders, the groups and the site groups."""
    folders: List[Folder] = field(default_factory=list)
    groups: List[Group] = field(default_factory=list)
    site_groups: List[SiteGroup] = field(default_factory=list)


# The one folder every setup has (decided 2026-11-04): the base folder, shown as "Default" at the top of the Folders
# tab. Everything goes there unless a rule says otherwise; other folders are written "%default%\Comics".
DEFAULT_FOLDER = "Default"
DEFAULT_TOKEN = "%default%"


def default_groups() -> List[Group]:
    return [Group(name, list(exts), list(GROUP_MIMES.get(name, []))) for name, exts in GROUPS.items()]


def default_library() -> Library:
    """A new setup: no folders but Default (which is the base folder, not stored here), the built-in file
    groups, no site groups."""
    return Library([], default_groups(), [])


def is_default(name: str) -> bool:
    return name.strip().lower() == DEFAULT_FOLDER.lower()


def find_named(items, text: str):
    """The folder, group or site group called ``text`` (any case), or None."""
    wanted = text.strip().lower()
    return next((item for item in items if item.name.lower() == wanted), None)


find_group = find_named


def group_matches(group: Group, d: "Download", known_extensions: set) -> bool:
    ext = split_name(d.filename)[1]
    if ext and ext in {e.strip().lower().lstrip(".") for e in group.extensions}:
        return True
    mime = _mime(d.mime)
    if mime and ext not in known_extensions and any(fnmatch.fnmatchcase(mime, p.strip().lower()) for p in group.mimes if p.strip()):
        return True
    return _text_matches([n for n in group.names if n.strip()], d.filename, False)


def site_group_matches(group: SiteGroup, d: "Download") -> bool:
    hosts = {"file": [host_of(d.url)], "page": [host_of(d.page_url)]}.get(group.where, [host_of(d.url), host_of(d.page_url)])
    return any(domain_matches(s, h) for s in group.sites if s.strip() for h in hosts if h)


def known_extensions(groups: List[Group]) -> set:
    return {e.strip().lower().lstrip(".") for g in groups for e in g.extensions if e.strip()}


def split_name(filename: str):
    """("release-2.4", "tar.gz") for "release-2.4.tar.gz"; the extension is lower case and has no dot."""
    lower = filename.lower()
    for ext in _COMPOUND:
        if lower.endswith("." + ext) and len(lower) > len(ext) + 1:
            return filename[: -len(ext) - 1], ext
    stem, dot, ext = filename.rpartition(".")
    if not dot or not stem:
        return filename, ""
    return stem, ext.lower()


def _mime(text: str) -> str:
    return text.split(";")[0].strip().lower()



# ---- what a rule sees ------------------------------------------------------------------------------------

@dataclass
class Download:
    url: str
    filename: str
    page_url: str = ""                  # the page the link was clicked on, when the browser says
    mime: str = ""
    size: Optional[int] = None          # bytes; None when the server does not say
    source: str = "direct"              # "direct", or "video" for a site yt-dlp handles
    video: Dict[str, str] = field(default_factory=dict)    # channel, uploader, playlist, title
    when: Optional[datetime.datetime] = None               # the moment it starts; None = now


def host_of(url: str) -> str:
    try:
        host = urlsplit(url if "://" in url else "//" + url).hostname or ""
    except ValueError:
        return ""
    return host.rstrip(".").lower()


def domain_matches(pattern: str, host: str) -> bool:
    """``example.com`` matches it and every subdomain; a pattern with ``*`` or ``?`` is a wildcard.

    A whole URL pasted as the pattern counts as its host.
    """
    pattern = pattern.strip().lower()
    if "://" in pattern or "/" in pattern:
        pattern = host_of(pattern)
    if not pattern or not host:
        return False
    if any(c in pattern for c in "*?["):
        return fnmatch.fnmatchcase(host, pattern)
    return host == pattern or host.endswith("." + pattern)


# ---- conditions ------------------------------------------------------------------------------------------

KINDS = ("domain", "type", "sites", "name", "size", "time", "source", "video")
DOMAIN_WHERE = ("either", "file", "page")
VIDEO_FIELDS = ("channel", "uploader", "playlist", "title")


@dataclass
class Condition:
    """One test. A condition matches when ANY of its values does; a rule needs ALL its conditions.

    kind and values:
      domain  host patterns; ``where`` says whose host: the file's, the page's, or either
      type    groups ("Video", or one of your own), extensions ("mkv"), MIME types ("video/*"); an extension
              in ``exclude`` never matches here, even when its group would (switched off for this rule only)
      sites   site group names; the download comes from one of their sites
      name   wildcards on the file name, or regular expressions when ``regex`` is set (case-insensitive)
      size    ``min_size`` and/or ``max_size`` in bytes; an unknown size never matches
      time    cron lines (see ``cron``); any line fitting the start moment is enough
      source  "direct" or "video"
      video   wildcards (or regexes) on the yt-dlp field named in ``where``
    """
    kind: str
    values: List[str] = field(default_factory=list)
    where: str = ""
    regex: bool = False
    min_size: Optional[int] = None
    max_size: Optional[int] = None
    exclude: List[str] = field(default_factory=list)


def _text_matches(patterns: List[str], text: str, regex: bool) -> bool:
    if regex:
        return any(re.search(p, text, re.IGNORECASE) for p in patterns)
    lower = text.lower()
    return any(fnmatch.fnmatchcase(lower, p.lower()) for p in patterns)


def condition_matches(c: Condition, d: Download, when: datetime.datetime,
                      library: Optional[Library] = None) -> bool:
    if c.kind == "size":
        if d.size is None or (c.min_size is None and c.max_size is None):
            return False
        return (c.min_size is None or d.size >= c.min_size) and (c.max_size is None or d.size <= c.max_size)
    values = [v for v in c.values if v.strip()]
    if not values:
        return False                     # an unfinished condition never fires; validate() reports it
    if c.kind == "domain":
        hosts = {"file": [host_of(d.url)], "page": [host_of(d.page_url)]}.get(c.where, [host_of(d.url), host_of(d.page_url)])
        return any(domain_matches(p, h) for p in values for h in hosts)
    library = default_library() if library is None else library
    if c.kind == "sites":
        return any(site_group_matches(g, d) for g in (find_named(library.site_groups, v) for v in values) if g)
    if c.kind == "type":
        groups = library.groups
        ext = split_name(d.filename)[1]
        if ext and ext in {e.strip().lower().lstrip(".") for e in c.exclude}:
            return False
        mime = _mime(d.mime)
        known = known_extensions(groups)
        for value in values:
            v = value.strip().lower()
            group = find_group(groups, v)
            if group is not None:
                if group_matches(group, d, known):
                    return True
            elif "/" in v:
                if mime and fnmatch.fnmatchcase(mime, v):
                    return True
            elif ext == v.lstrip("."):
                return True
        return False
    if c.kind == "name":
        return _text_matches(values, d.filename, c.regex)
    if c.kind == "time":
        try:
            return cron.matches(values, when)
        except cron.CronError:
            return False
    if c.kind == "source":
        return d.source in {v.strip().lower() for v in values}
    if c.kind == "video":
        text = d.video.get(c.where, "")
        return bool(text) and _text_matches(values, text, c.regex)
    return False


# ---- rules -----------------------------------------------------------------------------------------------

@dataclass
class Rename:
    """One rename line: when ``pattern`` (a regular expression, any case) is found somewhere in the name, the
    file is renamed to the template ``to``, in which the pattern's groups are {1}, {2}... (or {show} for
    (?P<show>...)), with the usual tokens and filters."""
    pattern: str
    to: str


@dataclass
class Rule:
    """Conditions (none = always) and what to do: ``target`` names the Folder the download goes to (none =
    Default), ``renames`` are tried in order and the first whose pattern is in the name renames the file (none =
    the name is kept), ``command`` runs when the download is finished. The first rule that fits wins.

    ``folder``, ``subfolder`` and ``stop`` are what older rules files held (a typed path, a subfolder added to
    it, and "continue to the rules below"); ``modernize`` turns them into named folders and first-fit rules
    when a rules file is loaded, and nothing writes them any more."""
    name: str
    conditions: List[Condition] = field(default_factory=list)
    folder: str = ""
    subfolder: str = ""
    renames: List[Rename] = field(default_factory=list)
    target: str = ""
    command: str = ""
    stop: bool = True
    enabled: bool = True


@dataclass
class Step:
    """One rule that hit, for "why here?"."""
    number: int                          # 1-based, as the editor shows it
    name: str
    effects: List[str]
    stopped: bool


@dataclass
class Placement:
    folder: str
    filename: str
    commands: List[str]
    steps: List[Step]

    @property
    def path(self) -> str:
        return os.path.join(self.folder, self.filename)

    @property
    def rule_numbers(self) -> str:
        """The table's Rule column: "1+5"."""
        return "+".join(str(s.number) for s in self.steps)

    def why(self) -> str:
        if not self.steps:
            return "No rule matched: the Default folder."
        parts = [f'rule {s.number} "{s.name}": ' + (", ".join(s.effects) or "no change") for s in self.steps]
        return " → ".join(parts)


# ---- tokens ----------------------------------------------------------------------------------------------

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
TOKENS = ("yyyy", "MM", "dd", "week", "weekday", "domain", "pagedomain", "name", "ext",
          "channel", "uploader", "playlist", "title")
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def safe_part(text: str, fallback: str = "unknown") -> str:
    """One folder or file name that is legal on Windows, Linux and macOS and cannot climb out of its folder."""
    text = _BAD_CHARS.sub("_", text).strip().rstrip(". ")
    if not text or set(text) <= {"."}:
        return fallback
    if text.split(".")[0].lower() in _RESERVED:
        text = "_" + text
    return text[:200]


def token_values(d: Download, when: datetime.datetime) -> Dict[str, str]:
    stem, ext = split_name(d.filename)
    domain = host_of(d.url)
    page = host_of(d.page_url)
    values = {
        "yyyy": f"{when.year:04d}", "MM": f"{when.month:02d}", "dd": f"{when.day:02d}",
        "week": f"{when.isocalendar()[1]:02d}", "weekday": WEEKDAY_NAMES[when.weekday()],
        "domain": domain[4:] if domain.startswith("www.") else domain,
        "pagedomain": page[4:] if page.startswith("www.") else page,
        "name": stem, "ext": ext,
    }
    for key in VIDEO_FIELDS:
        values[key] = d.video.get(key, "")
    return values


# ---- templates: tokens with filters ----------------------------------------------------------------------
#
# A token is {key} or {key|filter|filter:arg|...}. The filters run left to right on the token's value, each
# on the result of the one before, so a messy name can be trimmed step by step:
#     {name|dots|cut:\b(1080p|720p|WEB|x264)\b|trim}
# Arguments are split on ":"; write "\:" for a literal : inside one, and "\|" for a | outside (...) or [...]. A backslash before any
# other character is kept, so regular expressions read as usual (\d, \b). Braces may nest inside a token
# ({name|cut:\d{4}}). Text that is not a well-formed token is kept as it is written.

@dataclass
class _Token:
    key: str
    filters: List[Tuple[str, List[str]]]
    text: str                            # as written, for an unknown key


def _split(text: str, sep: str) -> List[str]:
    """Split on ``sep`` where it is not escaped; "\\" + sep becomes sep, other escapes are kept whole. A "|"
    inside (...) or [...] stays, so a regex alternative (1080p|720p) needs no escape."""
    parts, buf, i, depth = [], [], 0, 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            buf.append(nxt if nxt == sep else ch + nxt)
            i += 2
            continue
        if sep == "|" and ch in "([":
            depth += 1
        elif sep == "|" and ch in ")]" and depth:
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _closing_brace(text: str, start: int) -> int:
    """The index of the "}" that closes the "{" at ``start``, or -1."""
    depth, i = 0, start
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def parse_template(template: str) -> List[Union[str, _Token]]:
    out: List[Union[str, _Token]] = []
    i = 0
    while i < len(template):
        start = template.find("{", i)
        if start < 0:
            out.append(template[i:])
            break
        if start > i:
            out.append(template[i:start])
        end = _closing_brace(template, start)
        inner = template[start + 1:end] if end > 0 else ""
        m = re.fullmatch(r"(\w+)(\|.*)?", inner, re.DOTALL) if end > 0 else None
        if not m:
            out.append("{")
            i = start + 1
            continue
        filters = []
        for piece in _split(m.group(2) or "", "|")[1:]:
            name, *args = _split(piece, ":")
            filters.append((name.strip().lower(), args))
        out.append(_Token(m.group(1), filters, template[start:end + 1]))
        i = end + 1
    return out


def _icase_replace(text: str, old: str, new: str) -> str:
    return re.sub(re.escape(old), lambda _: new, text, flags=re.IGNORECASE) if old else text


def _title(text: str) -> str:
    """Capital first letter in each word, the rest kept ("don't" stays "Don't", "AC" stays "AC")."""
    return re.sub(r"(^|[\s\-_.(\[])(\w)", lambda m: m.group(1) + m.group(2).upper(), text)


def _cut_at(text: str, mark: str, keep_before: bool) -> str:
    at = text.lower().find(mark.lower()) if mark else -1
    if at < 0:
        return text
    return text[:at] if keep_before else text[at + len(mark):]


def _max(text: str, n: str) -> str:
    try:
        return text[:max(int(n), 1)].rstrip()
    except ValueError:
        return text


# name -> (smallest number of arguments, largest or None, what it does on (value, args))
FILTERS: Dict[str, Tuple[int, Optional[int], Callable[[str, List[str]], str]]] = {
    "lower":   (0, 0, lambda v, a: v.lower()),
    "upper":   (0, 0, lambda v, a: v.upper()),
    "title":   (0, 0, lambda v, a: _title(v)),
    "dots":    (0, 0, lambda v, a: re.sub(r"[._]+", " ", v)),
    "trim":    (0, 0, lambda v, a: re.sub(r"\s+", " ", v).strip(" -_.,;")),
    "replace": (2, 2, lambda v, a: _icase_replace(v, a[0], a[1])),
    "strip":   (1, None, lambda v, a: functools.reduce(lambda t, s: _icase_replace(t, s, ""), a, v)),
    "cut":     (1, 1, lambda v, a: re.sub(a[0], "", v, flags=re.IGNORECASE)),
    "sub":     (2, 2, lambda v, a: re.sub(a[0], a[1], v, flags=re.IGNORECASE)),
    "before":  (1, 1, lambda v, a: _cut_at(v, a[0], True)),
    "after":   (1, 1, lambda v, a: _cut_at(v, a[0], False)),
    "max":     (1, 1, lambda v, a: _max(v, a[0])),
    "default": (1, 1, lambda v, a: v if v.strip() else a[0]),
}
REGEX_FILTERS = ("cut", "sub")

FILTER_HELP = (
    "Filters, after a | and run left to right:  {name|dots|cut:1080p|trim}\n"
    "  lower  upper  title      change the case\n"
    "  dots                     dots and underscores become spaces\n"
    "  trim                     single spaces, no spaces or - _ . at the ends\n"
    "  replace:old:new          replace text (any case)\n"
    "  strip:a:b:...            remove each text\n"
    "  cut:regex                remove what the regular expression matches\n"
    "  sub:regex:new            replace with a regular expression (\\1 for a piece)\n"
    "  before:text  after:text  keep what comes before / after the first text\n"
    "  max:80                   at most 80 characters\n"
    "  default:text             use text when the value is empty\n"
    "Write \\: for a : inside an argument, and \\| for a | outside brackets ((1080p|720p) needs no \\).\n"
    "Pieces of a regular expression in a name or video condition: {1} {2} ..., or {show} for (?P<show>...).")


def apply_filters(value: str, filters: List[Tuple[str, List[str]]]) -> str:
    for name, args in filters:
        known = FILTERS.get(name)
        if known is None or len(args) < known[0] or (known[1] is not None and len(args) > known[1]):
            continue                                       # validate() reports it; the rest still runs
        try:
            value = known[2](value, args)
        except (re.error, IndexError):
            continue
    return value


def expand(template: str, values: Dict[str, str]) -> str:
    """Fill in the tokens, each run through its filters. Each value is made safe as a name of its own, so a
    channel called "AC/DC" stays one folder. An unknown token is left as it is (validate() reports it)."""
    out = []
    for part in parse_template(template):
        if isinstance(part, str):
            out.append(part)
        elif part.key in values:
            # empty becomes "unknown", unless the template gives its own default (which may be empty)
            fallback = "" if any(name == "default" for name, _ in part.filters) else "unknown"
            out.append(safe_part(apply_filters(values[part.key], part.filters), fallback))
        else:
            out.append(part.text)
    return "".join(out)


def _path_parts(template: str) -> List[str]:
    """Split a folder template on \\ and / outside tokens (a filter's argument may hold either)."""
    parts, buf, i = [], [], 0
    while i < len(template):
        ch = template[i]
        if ch == "{":
            end = _closing_brace(template, i)
            if end > 0:
                buf.append(template[i:end + 1])
                i = end + 1
                continue
        if ch in "\\/":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def folder_path(template: str, values: Dict[str, str], base_folder: str) -> str:
    """Where a folder template points: "%default%\\Comics" and a plain relative path are under the Default (base)
    folder, "%default%" alone is the Default folder, an absolute path is itself."""
    text = template.strip()
    if text.lower().startswith(DEFAULT_TOKEN):
        text = text[len(DEFAULT_TOKEN):].lstrip("\\/")
        if not text:
            return base_folder
    target = _expand_folder(text, values)
    return target if os.path.isabs(target) or re.match(r"[A-Za-z]:", target) else os.path.join(base_folder, target)


def _expand_folder(template: str, values: Dict[str, str]) -> str:
    parts = _path_parts(template)
    drive = ""
    if re.fullmatch(r"[A-Za-z]:", parts[0] or ""):
        drive, parts = parts[0] + os.sep, parts[1:]
    elif len(parts) > 2 and parts[0] == "" and parts[1] == "":
        rest = [safe_part(expand(p, values), "_") for p in parts[2:] if p and p != "."]
        return os.path.normpath(os.sep * 2 + os.path.join(*rest)) if rest else ""   # \\server\share\...
    elif parts[0] == "" and len(parts) > 1:
        drive = os.sep
    out = [safe_part(expand(p, values), "_") for p in parts if p and p != "."]
    return os.path.normpath(drive + os.path.join(*out)) if out else (drive or "")


# ---- pieces of a pattern ---------------------------------------------------------------------------------

def _regex_conditions(rule: Rule) -> List[Condition]:
    return [c for c in rule.conditions if c.regex and c.kind in ("name", "video")]


def captures(rule: Rule, d: Download) -> Dict[str, str]:
    """The pieces the rule's regular expressions cut out of the name (or video field): numbered groups as
    "1", "2", ..., named ones under their name. The first pattern of a condition that matches gives them; a
    piece that took no part in the match is empty."""
    found: Dict[str, str] = {}
    for c in _regex_conditions(rule):
        text = d.filename if c.kind == "name" else d.video.get(c.where, "")
        for pattern in (v for v in c.values if v.strip()):
            try:
                m = re.search(pattern, text, re.IGNORECASE)
            except re.error:
                continue
            if m:
                found.update(_pieces(m))
                break
    return found


def _group_names_of(patterns) -> set:
    names = set()
    for pattern in patterns:
        try:
            compiled = re.compile(pattern)
        except re.error:
            continue
        names.update(str(n) for n in range(1, compiled.groups + 1))
        names.update(compiled.groupindex)
    return names


def capture_names(rule: Rule) -> set:
    return _group_names_of(v for c in _regex_conditions(rule) for v in c.values)


def _pieces(m: "re.Match") -> Dict[str, str]:
    found = {str(n): g or "" for n, g in enumerate(m.groups(), 1)}
    found.update({k: g or "" for k, g in m.groupdict().items()})
    return found


def rename_by_lines(lines: List[Rename], filename: str, values: Dict[str, str]) -> Tuple[Optional[str], int]:
    """The new name from the first line whose pattern is in ``filename``, and that line's number (1-based);
    (None, 0) when no pattern is found, so the name is kept."""
    for number, line in enumerate(lines, 1):
        if not line.pattern.strip():
            continue
        try:
            m = re.search(line.pattern, filename, re.IGNORECASE)
        except re.error:
            continue
        if m:
            return safe_part(expand(line.to, {**values, **_pieces(m)}), filename), number
    return None, 0


def place(rules: List[Rule], d: Download, base_folder: str, library: Optional[Library] = None) -> Placement:
    """Walk the rules for one download: the folder, the file name, the commands, and which rules did it."""
    when = d.when or datetime.datetime.now()
    library = default_library() if library is None else library
    values = token_values(d, when)
    folder = base_folder
    filename = d.filename
    commands: List[str] = []
    steps: List[Step] = []
    for number, rule in enumerate(rules, 1):
        if not rule.enabled or not all(condition_matches(c, d, when, library) for c in rule.conditions):
            continue
        effects = []
        here = {**values, **captures(rule, d)}             # a pattern's pieces belong to its own rule
        named = find_named(library.folders, rule.target) if rule.target.strip() else None
        if is_default(rule.target):
            named = Folder(DEFAULT_FOLDER, DEFAULT_TOKEN)
        template = named.path if named else rule.folder
        if template.strip():
            folder = folder_path(template, here, base_folder)
            effects.append(f"folder {named.name}: {folder}" if named else f"set folder {folder}")
        if rule.subfolder.strip():
            sub = _expand_folder(rule.subfolder.lstrip("\\/"), here)
            if sub:
                folder = os.path.join(folder, sub)
                effects.append(f"add subfolder {sub}")
        if rule.renames:
            renamed, line = rename_by_lines(rule.renames, filename, here)
            if renamed is None:
                effects.append("name kept (no rename pattern in the name)")
            else:
                filename = renamed
                effects.append(f"rename to {filename} (line {line})")
                values["name"], values["ext"] = split_name(filename)   # a rule below trims on from here
        if rule.command.strip():
            commands.append(rule.command)
            effects.append("run command")
        steps.append(Step(number, rule.name, effects, True))
        break                                    # the first rule that fits wins (decided 2026-11-04)
    return Placement(os.path.normpath(folder), safe_part(filename, "download"), commands, steps)


def free_name(folder: str, filename: str, exists: Callable[[str], bool]) -> str:
    """The file name to use so nothing is overwritten: "name (1).ext", "name (2).ext", ..."""
    if not exists(os.path.join(folder, filename)):
        return filename
    stem, ext = split_name(filename)
    suffix = filename[len(stem):]        # the extension as written, with its dot ("" when none)
    n = 1
    while exists(os.path.join(folder, f"{stem} ({n}){suffix}")):
        n += 1
    return f"{stem} ({n}){suffix}"


# ---- checking a rule in the editor -----------------------------------------------------------------------

def validate(rule: Rule, library: Optional[Library] = None) -> List[str]:
    """What is wrong with a rule, in words for the editor; empty when it is fine. With the ``library`` the
    names it points at (folder, file groups, site groups) are checked too."""
    found = []
    known_here = set(TOKENS) | capture_names(rule)
    folder_template = rule.folder
    if library is not None and rule.target.strip() and not is_default(rule.target):
        named = find_named(library.folders, rule.target)
        if named is None:
            found.append(f"folder: there is no folder called {rule.target!r} (see Folders)")
        else:
            folder_template = named.path
    if library is not None:
        for n, c in enumerate(rule.conditions, 1):
            if c.kind == "sites":
                found.extend(f"condition {n}: there is no site group called {v!r} (see Site Groups)"
                             for v in c.values if v.strip() and find_named(library.site_groups, v) is None)
            elif c.kind == "type":
                found.extend(f"condition {n}: {v!r} is not a file group, an extension or a MIME type"
                             for v in c.values if v.strip() and find_named(library.groups, v) is None
                             and (" " in v.strip() or (v.strip()[:1].isupper() and "/" not in v)))
    checks = [("folder", folder_template, known_here), ("subfolder", rule.subfolder, known_here)]
    for n, line in enumerate(rule.renames, 1):
        label = f"rename line {n}"
        if not line.pattern.strip():
            found.append(f"{label}: give a pattern to look for in the name")
        else:
            try:
                re.compile(line.pattern)
            except re.error as error:
                found.append(f"{label}: {line.pattern!r} is not a regular expression ({error})")
        if not line.to.strip():
            found.append(f"{label}: give the new name")
        elif re.search(r"[\\/]", "".join(p for p in parse_template(line.to) if isinstance(p, str))):
            found.append(f"{label}: a file name cannot contain \\ or /; use the folder for that")
        checks.append((label, line.to, known_here | _group_names_of([line.pattern])))
    for label, template, known in checks:
        for part in parse_template(template):
            if isinstance(part, str):
                continue
            if part.key not in known:
                hint = (" (a piece needs a group in the line's pattern, or in a name or video condition)"
                        if part.key.isdigit() or part.key not in TOKENS else "")
                found.append(f"{label}: unknown token {{{part.key}}}{hint}")
            for name, args in part.filters:
                spec = FILTERS.get(name)
                if spec is None:
                    found.append(f"{label}: {{{part.key}}} has an unknown filter {name!r}")
                    continue
                low, high = spec[0], spec[1]
                if len(args) < low or (high is not None and len(args) > high):
                    wanted = str(low) if low == high else f"at least {low}" if high is None else f"{low} to {high}"
                    found.append(f"{label}: {name} takes {wanted} argument{'' if wanted.endswith('1') else 's'}, "
                                 f"not {len(args)} (write \\: for a : inside one)")
                elif name in REGEX_FILTERS:
                    try:
                        re.compile(args[0])
                    except re.error as error:
                        found.append(f"{label}: {name}: {args[0]!r} is not a regular expression ({error})")
                elif name == "max" and not args[0].strip().isdigit():
                    found.append(f"{label}: max needs a number, not {args[0]!r}")
    for n, c in enumerate(rule.conditions, 1):
        where = f"condition {n} ({c.kind})"
        if c.kind not in KINDS:
            found.append(f"{where}: unknown kind")
            continue
        if c.kind == "size":
            if c.min_size is None and c.max_size is None:
                found.append(f"{where}: give a smallest or a largest size")
            elif c.min_size is not None and c.max_size is not None and c.min_size > c.max_size:
                found.append(f"{where}: the smallest size is above the largest")
            continue
        if not [v for v in c.values if v.strip()]:
            found.append(f"{where}: no values yet")
        if c.kind in ("domain", "sites") and c.where not in ("",) + DOMAIN_WHERE:
            found.append(f"{where}: 'where' must be one of {', '.join(DOMAIN_WHERE)}")
        if c.kind == "video" and c.where not in VIDEO_FIELDS:
            found.append(f"{where}: pick a video field ({', '.join(VIDEO_FIELDS)})")
        if c.kind == "time":
            found.extend(f"{where}: {p}" for p in cron.problems(v for v in c.values if v.strip()))
        if c.kind == "source":
            found.extend(f"{where}: {v!r} is not direct or video" for v in c.values if v.strip().lower() not in ("direct", "video"))
        if c.regex and c.kind in ("name", "video"):
            for v in c.values:
                try:
                    re.compile(v)
                except re.error as error:
                    found.append(f"{where}: {v!r} is not a regular expression ({error})")
    return found


# ---- storage ---------------------------------------------------------------------------------------------

FORMAT = 3          # 3: folders, file groups and site groups beside the rules; rename lines; rules point at folders
MATCH_ANY = ".*"    # the pattern an old single rename template becomes, so it still renames every file


def rules_to_json(rules: List[Rule], library: Optional[Library] = None) -> str:
    data = {"format": FORMAT, "rules": [asdict(r) for r in rules]}
    if library is not None:
        data["folders"] = [asdict(f) for f in library.folders]
        data["groups"] = [asdict(g) for g in library.groups]
        data["site_groups"] = [asdict(g) for g in library.site_groups]
    return json.dumps(data, indent=2, ensure_ascii=False)


def library_from_json(text: str) -> Optional[Library]:
    """The folders and groups saved with the rules, or None when the file has none (an older file)."""
    data = json.loads(text)
    if not any(key in data for key in ("folders", "groups", "site_groups")):
        return None
    defaults = default_library()
    return Library(
        [Folder(**_known(Folder, f)) for f in data["folders"]] if "folders" in data else defaults.folders,
        [Group(**_known(Group, g)) for g in data["groups"]] if "groups" in data else defaults.groups,
        [SiteGroup(**_known(SiteGroup, g)) for g in data.get("site_groups", [])])


def unique_name(wanted: str, taken) -> str:
    """``wanted``, or "wanted (2)", "wanted (3)"... when a name in ``taken`` (any case) has it."""
    lowered = {t.lower() for t in taken}
    name, n = wanted, 1
    while name.lower() in lowered:
        n += 1
        name = f"{wanted} ({n})"
    return name


def _folder_for(path: str, wanted_name: str, library: Library) -> Folder:
    """The Folder with this path, or a new one named after ``wanted_name`` (made unique)."""
    same = next((f for f in library.folders if f.path == path), None)
    if same is None:
        same = Folder(unique_name(wanted_name or "Folder", [DEFAULT_FOLDER] + [f.name for f in library.folders]), path)
        library.folders.append(same)
    return same


def modernize(rule_list: List[Rule], library: Library) -> bool:
    """Bring rules from older versions to today's shape, keeping where their files land. True when anything
    changed (the caller saves).

    - A typed folder path becomes a named Folder (one per distinct path) the rule points at.
    - A subfolder becomes a Folder of its own: the rule's folder (or Default) with the subfolder under it.
    - An "except" list on a file-group condition becomes a copy of the group without those extensions
      ("Video without ts"), which the rule then uses; the group itself is left as it was.
    - "Continue to the rules below" goes: the first rule that fits wins.
    """
    changed = False
    for rule in rule_list:
        typed = rule.folder.strip() if not rule.target.strip() else ""
        if rule.subfolder.strip():
            named = None if typed or is_default(rule.target) else find_named(library.folders, rule.target)
            base = (typed or (named.path if named else DEFAULT_TOKEN)).rstrip("\\/")
            path = base + "\\" + rule.subfolder.strip().lstrip("\\/")
            rule.target, rule.folder, rule.subfolder = _folder_for(path, rule.name, library).name, "", ""
            changed = True
        elif typed:
            rule.target, rule.folder = _folder_for(typed, rule.name, library).name, ""
            changed = True
        for c in rule.conditions:
            if c.kind != "type" or not c.exclude:
                continue
            gone = {e.strip().lower().lstrip(".") for e in c.exclude if e.strip()}
            values = []
            for value in c.values:
                group = find_named(library.groups, value)
                if group is None:
                    values.append(value)
                    continue
                kept = [e for e in group.extensions if e.strip().lower().lstrip(".") not in gone]
                wanted = f"{group.name} without {', '.join(sorted(gone))}"
                trimmed = find_named(library.groups, wanted)
                if trimmed is None:
                    trimmed = Group(unique_name(wanted, [g.name for g in library.groups]), kept, list(group.mimes),
                                    list(group.names))
                    library.groups.append(trimmed)
                values.append(trimmed.name)
            c.values, c.exclude = values, []
            changed = True
        if not rule.stop:
            rule.stop = True
            changed = True
    return changed


def rename_references(rule_list: List[Rule], kind: str, old: str, new: str) -> None:
    """A folder, file group or site group was renamed: every rule pointing at ``old`` now points at ``new``.
    ``kind`` is "folder", "type" (file groups) or "sites"."""
    for rule in rule_list:
        if kind == "folder":
            if rule.target.strip().lower() == old.lower():
                rule.target = new
            continue
        for c in rule.conditions:
            if c.kind == kind:
                c.values = [new if v.strip().lower() == old.lower() else v for v in c.values]


def rules_using(rule_list: List[Rule], kind: str, name: str) -> List[int]:
    """The (1-based) numbers of the rules that point at a folder, file group or site group."""
    wanted = name.strip().lower()
    used = []
    for number, rule in enumerate(rule_list, 1):
        if kind == "folder":
            hit = rule.target.strip().lower() == wanted
        else:
            hit = any(c.kind == kind and wanted in {v.strip().lower() for v in c.values} for c in rule.conditions)
        if hit:
            used.append(number)
    return used


def _known(cls, data: dict) -> dict:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in names}    # keys from a newer version are skipped, not fatal


def rules_from_json(text: str) -> List[Rule]:
    data = json.loads(text)
    rules = []
    for item in data.get("rules", []):
        old_rename = str(item.get("rename") or "").strip()
        item = _known(Rule, item)
        item["conditions"] = [Condition(**_known(Condition, c)) for c in item.get("conditions", [])]
        item["renames"] = [Rename(**_known(Rename, r)) for r in item.get("renames", [])]
        if old_rename and not item["renames"]:
            item["renames"] = [Rename(MATCH_ANY, old_rename)]      # format 1 renamed every file the rule caught
        rules.append(Rule(**item))
    return rules


def default_rules() -> List[Rule]:
    """A new setup has no rules: everything goes to the Default folder until you write some."""
    return []
