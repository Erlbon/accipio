"""Rules: where a download goes, and what happens to it.

A rule set is an ordered list (the editor's drag order). A download goes through it from the top. A rule
whose conditions ALL match applies its actions, then either stops the walk or lets it go on to the rules
below. That is how rules combine, as in mail filters: a domain rule sets a base folder and continues, and a
file-type rule further down adds the subfolder.

Everything here is plain data and functions, with no Qt and no disk access, so the editor's test box, the
start of a download and the re-check when it finishes all call the same ``place()``.
"""
from __future__ import annotations

import datetime
import fnmatch
import json
import os
import re
from dataclasses import asdict, dataclass, field, fields
from typing import Callable, Dict, List, Optional
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
_KNOWN = {e for exts in GROUPS.values() for e in exts}


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


def in_group(group: str, filename: str, mime: str = "") -> bool:
    """Whether a file belongs to a type group. A known extension decides; the MIME type only counts without one."""
    group = _group_name(group)
    if group is None:
        return False
    ext = split_name(filename)[1]
    if ext in _KNOWN:
        return ext in GROUPS[group]
    mime = _mime(mime)
    return bool(mime) and any(fnmatch.fnmatchcase(mime, pattern) for pattern in GROUP_MIMES.get(group, []))


def _group_name(text: str) -> Optional[str]:
    for name in GROUPS:
        if name.lower() == text.strip().lower():
            return name
    return None


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

KINDS = ("domain", "type", "name", "size", "time", "source", "video")
DOMAIN_WHERE = ("either", "file", "page")
VIDEO_FIELDS = ("channel", "uploader", "playlist", "title")


@dataclass
class Condition:
    """One test. A condition matches when ANY of its values does; a rule needs ALL its conditions.

    kind and values:
      domain  host patterns; ``where`` says whose host: the file's, the page's, or either
      type    type groups ("Video"), extensions ("mkv"), MIME types ("video/*")
      name    wildcards on the file name, or regular expressions when ``regex`` is set (case-insensitive)
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


def _text_matches(patterns: List[str], text: str, regex: bool) -> bool:
    if regex:
        return any(re.search(p, text, re.IGNORECASE) for p in patterns)
    lower = text.lower()
    return any(fnmatch.fnmatchcase(lower, p.lower()) for p in patterns)


def condition_matches(c: Condition, d: Download, when: datetime.datetime) -> bool:
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
    if c.kind == "type":
        ext = split_name(d.filename)[1]
        mime = _mime(d.mime)
        for value in values:
            v = value.strip().lower()
            if _group_name(v):
                if in_group(v, d.filename, d.mime):
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
class Rule:
    """Conditions (none = always) and what to do. ``folder`` replaces the folder so far (a relative one is
    under the base folder), ``subfolder`` is added to it, ``rename`` is a name template, ``command`` runs
    when the download is finished. Then the walk stops, or goes on when ``stop`` is off."""
    name: str
    conditions: List[Condition] = field(default_factory=list)
    folder: str = ""
    subfolder: str = ""
    rename: str = ""
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
            return "No rule matched: the base folder."
        parts = [f'rule {s.number} "{s.name}": ' + (", ".join(s.effects) or "no change") for s in self.steps]
        return " → ".join(parts)


# ---- tokens ----------------------------------------------------------------------------------------------

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
TOKENS = ("yyyy", "MM", "dd", "week", "weekday", "domain", "pagedomain", "name", "ext",
          "channel", "uploader", "playlist", "title")
_TOKEN = re.compile(r"\{(\w+)\}")
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


def expand(template: str, values: Dict[str, str]) -> str:
    """Fill in the tokens. Each value is made safe as a name of its own, so a channel called "AC/DC" stays
    one folder. An unknown token is left as it is (validate() reports it)."""
    def one(match):
        key = match.group(1)
        return safe_part(values[key]) if key in values else match.group(0)
    return _TOKEN.sub(one, template)


def _expand_folder(template: str, values: Dict[str, str]) -> str:
    text = template.replace("\\", "/")
    parts = [p for p in text.split("/")]
    drive = ""
    if re.fullmatch(r"[A-Za-z]:", parts[0] or ""):
        drive, parts = parts[0] + os.sep, parts[1:]
    elif text.startswith("//"):
        return os.path.normpath(expand(template, values))     # a network share: \\server\share\...
    elif text.startswith("/"):
        drive = os.sep
    out = [safe_part(expand(p, values), "_") for p in parts if p and p != "."]
    return os.path.normpath(drive + os.path.join(*out)) if out else (drive or "")


def place(rules: List[Rule], d: Download, base_folder: str) -> Placement:
    """Walk the rules for one download: the folder, the file name, the commands, and which rules did it."""
    when = d.when or datetime.datetime.now()
    values = token_values(d, when)
    folder = base_folder
    filename = d.filename
    commands: List[str] = []
    steps: List[Step] = []
    for number, rule in enumerate(rules, 1):
        if not rule.enabled or not all(condition_matches(c, d, when) for c in rule.conditions):
            continue
        effects = []
        if rule.folder.strip():
            target = _expand_folder(rule.folder, values)
            folder = target if os.path.isabs(target) or re.match(r"[A-Za-z]:", target) else os.path.join(base_folder, target)
            effects.append(f"set folder {folder}")
        if rule.subfolder.strip():
            sub = _expand_folder(rule.subfolder.lstrip("\\/"), values)
            if sub:
                folder = os.path.join(folder, sub)
                effects.append(f"add subfolder {sub}")
        if rule.rename.strip():
            filename = expand(rule.rename, values)
            effects.append(f"rename to {safe_part(filename, d.filename)}")
        if rule.command.strip():
            commands.append(rule.command)
            effects.append("run command")
        steps.append(Step(number, rule.name, effects, rule.stop))
        if rule.stop:
            break
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

def validate(rule: Rule) -> List[str]:
    """What is wrong with a rule, in words for the editor; empty when it is fine."""
    found = []
    for label, template in (("folder", rule.folder), ("subfolder", rule.subfolder), ("rename", rule.rename)):
        for key in _TOKEN.findall(template):
            if key not in TOKENS:
                found.append(f"{label}: unknown token {{{key}}}")
    if rule.rename.strip() and re.search(r"[\\/]", rule.rename):
        found.append("rename: a file name cannot contain \\ or /; use the folder for that")
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
        if c.kind == "domain" and c.where not in ("",) + DOMAIN_WHERE:
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

FORMAT = 1


def rules_to_json(rules: List[Rule]) -> str:
    return json.dumps({"format": FORMAT, "rules": [asdict(r) for r in rules]}, indent=2, ensure_ascii=False)


def _known(cls, data: dict) -> dict:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in names}    # keys from a newer version are skipped, not fatal


def rules_from_json(text: str) -> List[Rule]:
    data = json.loads(text)
    rules = []
    for item in data.get("rules", []):
        item = _known(Rule, item)
        item["conditions"] = [Condition(**_known(Condition, c)) for c in item.get("conditions", [])]
        rules.append(Rule(**item))
    return rules


def default_rules() -> List[Rule]:
    """The rule set a new installation starts with: one subfolder per kind of file, then Other."""
    def by_type(name: str, groups: List[str], sub: str) -> Rule:
        return Rule(name, [Condition("type", groups)], subfolder=sub)
    return [
        Rule("Video sites", [Condition("source", ["video"])], subfolder="Video/{uploader}"),
        by_type("Video", ["Video"], "Video"),
        by_type("Audio", ["Audio"], "Audio"),
        by_type("Images", ["Images"], "Images"),
        by_type("Ebooks and comics", ["Ebooks", "Comics"], "Books"),
        by_type("Documents", ["Documents"], "Documents"),
        by_type("Archives", ["Archives"], "Archives"),
        by_type("Programs", ["Programs"], "Programs"),
        Rule("Other", subfolder="Other"),
    ]
