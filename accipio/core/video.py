"""Video sites through yt-dlp.

``is_video_site`` says whether yt-dlp has an extractor for a link (its catch-all "generic" extractor does not
count: a plain file link goes to aria2). ``probe_video`` asks the site, before downloading, for the title,
channel, uploader, playlist and the extension of the chosen format, so the rules can place a video like any
other file and use ``{channel}``, ``{uploader}``, ``{playlist}`` and ``{title}``.

``VideoEngine`` runs the downloads. It takes the same calls as the aria2 engine (add, pause, resume, remove,
restart, status, all, set_limits), so the queue treats both alike. Each download runs yt-dlp on a thread of
its own; at most ``max_downloads`` run at once and the rest wait. Pausing stops yt-dlp and keeps its
``.part`` file; resuming starts it again and yt-dlp continues where it stopped.

YouTube and most big sites serve video and sound separately, and ffmpeg joins them. Without ffmpeg the best
single file is taken instead (often lower quality), and "audio only" keeps the site's own sound format
instead of converting to mp3.
"""
from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from accipio.core import rules
from accipio.core.aria2 import Status
from accipio.core.probe import ProbeError

QUALITIES = {"best": "Best", "1080": "Up to 1080p", "720": "Up to 720p", "audio": "Audio only (mp3)"}
FFMPEG_NAME = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"


def find_ffmpeg(chosen: str = "") -> Optional[str]:
    """The ffmpeg picked under Settings (``chosen``) while that file is there; else ffmpeg the way the Redactor
    apps find it (redactor_common's ``find_tool``, as videoredactor does): a bundled copy (``ffmpeg/`` next to a
    frozen exe, or the checkout's ``bin/ffmpeg``), then PATH, then the usual Windows install folders, including
    winget's Links folder (where the installer's ffmpeg option and Settings' Install ffmpeg put it; a PATH change
    there reaches only programs started afterwards)."""
    if chosen and os.path.isfile(chosen):
        return chosen
    from redactor_common.core.tool_locator import find_tool, windows_program_dirs
    bundled = Path(sys.executable).parent / "ffmpeg" if getattr(sys, "frozen", False) \
        else Path(__file__).resolve().parents[2] / "bin" / "ffmpeg"
    install_dirs = windows_program_dirs("ffmpeg/bin")
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        install_dirs.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "WinGet" / "Links")
    found = find_tool(FFMPEG_NAME, tools_dir=bundled, install_dirs=install_dirs)
    return str(found) if found else None


# The same command as the installer's ffmpeg option (installer/accipio.iss).
WINGET_FFMPEG = ("install", "--id", "Gyan.FFmpeg", "--exact", "--silent", "--accept-package-agreements",
                 "--accept-source-agreements", "--disable-interactivity")


class FfmpegInstallError(Exception):
    pass


class FfmpegInstallCancelled(Exception):
    pass


def find_winget() -> Optional[str]:
    """winget is an app alias in WindowsApps, which a plain PATH search can miss."""
    alias = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WindowsApps", "winget.exe")
    return alias if os.path.isfile(alias) else shutil.which("winget")


def install_ffmpeg(cancel: Optional[threading.Event] = None, winget_exe: Optional[str] = None,
                   popen: Callable[..., Any] = subprocess.Popen) -> None:
    """Install ffmpeg with winget (Settings > Install ffmpeg), without a console window. Raises
    FfmpegInstallError with winget's last words when it fails (it also "fails" when ffmpeg is already installed,
    so the caller looks with find_ffmpeg first), FfmpegInstallCancelled when ``cancel`` is set."""
    exe = winget_exe or find_winget()
    if not exe:
        raise FfmpegInstallError("winget is not on this PC. Install ffmpeg yourself (ffmpeg.org), then pick "
                                 "ffmpeg.exe with Browse….")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with tempfile.TemporaryFile() as output:                 # a file, not a pipe: winget's output cannot fill it up
        process = popen([exe, *WINGET_FFMPEG], stdout=output, stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL, creationflags=flags)
        while process.poll() is None:
            if cancel is not None and cancel.is_set():
                process.kill()
                raise FfmpegInstallCancelled()
            time.sleep(0.2)
        if process.poll() != 0:
            output.seek(0)
            lines = [line.strip() for line in output.read().decode("utf-8", "replace").splitlines() if line.strip()]
            raise FfmpegInstallError("winget could not install ffmpeg: " + (lines[-1] if lines else
                                                                             f"exit code {process.poll()}"))


@functools.lru_cache(maxsize=1)
def _extractors():
    from yt_dlp.extractor import gen_extractor_classes
    return [ie for ie in gen_extractor_classes() if ie.ie_key() != "Generic"]


@functools.lru_cache(maxsize=256)
def is_video_site(url: str) -> bool:
    """True when yt-dlp has a site extractor for the link (not just its catch-all)."""
    if not url.lower().startswith(("http://", "https://")):
        return False
    try:
        return any(ie.suitable(url) for ie in _extractors())
    except Exception:                                  # a broken extractor must never stop a download
        return False


def format_options(quality: str, ffmpeg: Optional[str]) -> Dict[str, Any]:
    """yt-dlp options for a quality. Prefers mp4 (it plays everywhere) when it can choose."""
    height = {"1080": 1080, "720": 720}.get(quality)
    limit = f"[height<={height}]" if height else ""
    if quality == "audio":
        options: Dict[str, Any] = {"format": "ba/b"}
        if ffmpeg:
            options["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"}]
        return options
    if not ffmpeg:
        return {"format": f"b{limit}/b"}                # one file that already has both picture and sound
    # highest resolution first; at the same resolution H.264 in mp4, which every player plays (YouTube's
    # AV1 is often picked otherwise, and Windows cannot play it without an extra codec)
    return {"format": f"bv*{limit}+ba/b{limit}/b", "format_sort": ["res", "vcodec:h264", "ext:mp4:m4a"],
            "merge_output_format": "mp4/mkv"}


def _clean(error: Exception) -> str:
    text = str(error).strip()
    for prefix in ("ERROR: ", "ERROR:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    return text.splitlines()[0] if text else type(error).__name__


@dataclass
class VideoProbe:
    url: str
    title: str = ""
    ext: str = ""
    size: Optional[int] = None
    channel: str = ""
    uploader: str = ""
    playlist: str = ""
    entries: Optional[List[str]] = None        # a playlist: the links of its videos

    @property
    def filename(self) -> str:
        return f"{rules.safe_part(self.title, 'video')}.{self.ext or 'mp4'}"

    @property
    def fields(self) -> Dict[str, str]:
        found = {"channel": self.channel, "uploader": self.uploader, "playlist": self.playlist, "title": self.title}
        return {k: v for k, v in found.items() if v}


def _base_options(headers: Optional[Dict[str, str]], ffmpeg: Optional[str]) -> Dict[str, Any]:
    options: Dict[str, Any] = {"quiet": True, "no_warnings": True, "noprogress": True, "color": "no_color"}
    if headers:
        options["http_headers"] = dict(headers)
    if ffmpeg:
        options["ffmpeg_location"] = ffmpeg
    return options


def carries_a_list(url: str) -> bool:
    """A link to one video that also names a list it was opened from (``watch?v=…&list=…``, ``youtu.be/…?list=…``),
    as opposed to a link to the list itself. Such a link means the video; the list is only offered."""
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parts.query)
    if "list" not in query:
        return False
    return "v" in query or parts.netloc.lower().endswith("youtu.be")


def is_mix(url: str) -> bool:
    """A YouTube Mix: a radio list YouTube makes around a video (its id starts with RD), not one anybody picked."""
    return any(v.startswith("RD") for v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("list", []))


def probe_video(url: str, headers: Optional[Dict[str, str]] = None, quality: str = "best",
                ffmpeg: Optional[str] = None, playlist: str = "", whole_list: bool = False) -> VideoProbe:
    """Ask the site about a video (or a playlist) without downloading it. A video link that carries a list is
    just that video unless ``whole_list`` (yt-dlp's noplaylist); a link to a list itself is always the list."""
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError
    options = {**_base_options(headers, ffmpeg), **format_options(quality, ffmpeg),
               "skip_download": True, "extract_flat": "in_playlist", "noplaylist": not whole_list}
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as error:
        raise ProbeError(_clean(error)) from None
    if info is None:
        raise ProbeError("the site gave no information about this link")
    if info.get("_type") == "playlist":
        links = [e.get("webpage_url") or e.get("url") for e in info.get("entries") or [] if e]
        return VideoProbe(url, title=info.get("title") or "", channel=info.get("channel") or "",
                          uploader=info.get("uploader") or "", playlist=info.get("title") or "",
                          entries=[link for link in links if link])
    ext = "mp3" if quality == "audio" and ffmpeg else (info.get("ext") or "")
    size = info.get("filesize") or info.get("filesize_approx")
    if not size and info.get("requested_formats"):
        size = sum(f.get("filesize") or f.get("filesize_approx") or 0 for f in info["requested_formats"]) or None
    return VideoProbe(info.get("webpage_url") or url, title=info.get("title") or "", ext=ext, size=size,
                      channel=info.get("channel") or "", uploader=info.get("uploader") or info.get("channel") or "",
                      playlist=playlist or info.get("playlist_title") or info.get("playlist") or "")


# ---- the engine ------------------------------------------------------------------------------------------

class _Stop(Exception):
    pass


@dataclass
class _Job:
    gid: str
    url: str
    folder: str
    filename: str
    headers: List[str]
    state: str = "waiting"
    path: str = ""
    message: str = ""
    speed: int = 0
    parts: Dict[str, List[int]] = field(default_factory=dict)      # file -> [done, total]
    temp_files: set = field(default_factory=set)
    stop: str = ""                                                  # "", "pause" or "remove"
    thread: Optional[threading.Thread] = None
    run: int = 0                     # each start is a new run; an older run still winding down leaves the job alone

    @property
    def done(self) -> int:
        return sum(p[0] for p in self.parts.values())

    @property
    def total(self) -> int:
        return sum(p[1] for p in self.parts.values())


class VideoEngine:
    def __init__(self, ffmpeg: Optional[str] = None, max_downloads: int = 3, quality: str = "best",
                 downloader: Optional[Callable[[Dict[str, Any], str], None]] = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.ffmpeg = ffmpeg
        self.max_downloads = max_downloads
        self.quality = quality
        self.speed_limit = 0                  # bytes/s for ALL videos together (yt-dlp's own ratelimit is per video)
        self._clock, self._sleep = clock, sleep
        self._window: Optional[List[float]] = None       # [start, bytes since start, limit] of the shared budget
        self._downloader = downloader or self._yt_dlp
        self._jobs: Dict[str, _Job] = {}
        self._cond = threading.Condition()
        self._running = 0
        self._counter = 0

    # ---- the calls the queue makes ---------------------------------------------------------------------------

    def add(self, url: str, folder: str, filename: str, *, headers: Iterable[str] = (), paused: bool = False) -> str:
        with self._cond:
            self._counter += 1
            gid = f"v{self._counter:06d}"
            job = _Job(gid, url, folder, filename, list(headers), state="paused" if paused else "waiting")
            self._jobs[gid] = job
        if not paused:
            self._launch(job)
        return gid

    def pause(self, gid: str) -> None:
        with self._cond:
            job = self._jobs[gid]
            if job.state in ("active", "waiting"):
                job.stop = "pause"
                if job.state == "waiting":
                    job.state = "paused"
                self._cond.notify_all()

    def resume(self, gid: str) -> None:
        """Also right after a pause the download has not noticed yet. Always a new run: the old one may be past the
        point of no return (2026-11-07: a resume while it ended started nothing and left the video "active")."""
        with self._cond:
            job = self._jobs[gid]
            if job.state != "paused" and job.stop != "pause":
                return
            job.stop, job.state = "", "waiting"
            self._launch(job)

    def remove(self, gid: str) -> None:
        """Stop and forget a download, deleting its half-finished parts (never a finished file)."""
        with self._cond:
            job = self._jobs.pop(gid, None)
            if job is None:
                return
            job.stop = "remove"
            self._cond.notify_all()
        if job.thread and job.thread.is_alive():
            job.thread.join(10)
        if job.state != "complete":
            for temporary in job.temp_files:
                for leftover in (temporary, temporary + ".ytdl"):
                    if os.path.isfile(leftover):
                        os.remove(leftover)

    def restart(self, gid: str) -> str:
        job = self._jobs[gid]
        self.remove(gid)
        return self.add(job.url, job.folder, job.filename, headers=job.headers)

    def status(self, gid: str) -> Status:
        with self._cond:
            return self._status(self._jobs[gid])

    def all(self) -> List[Status]:
        with self._cond:
            return [self._status(j) for j in self._jobs.values()]

    def set_limits(self, *, max_downloads: Optional[int] = None, speed_limit: Optional[int] = None) -> None:
        with self._cond:
            if max_downloads is not None:
                self.max_downloads = max_downloads
            if speed_limit is not None:
                self.speed_limit = speed_limit
            self._cond.notify_all()

    def stop(self, wait: float = 0.0) -> None:
        """Ask every download to stop; their .part files stay, so they continue after a restart."""
        with self._cond:
            for job in self._jobs.values():
                job.stop = job.stop or "pause"
            self._cond.notify_all()
            threads = [j.thread for j in self._jobs.values() if j.thread]
        for thread in threads:
            if wait and thread.is_alive():
                thread.join(wait)

    # ---- inside --------------------------------------------------------------------------------------------

    @staticmethod
    def _status(job: _Job) -> Status:
        path = job.path or os.path.join(job.folder, job.filename)
        return Status(job.gid, job.state, job.total, job.done, job.speed if job.state == "active" else 0, 1, path,
                      "video" if job.state == "error" else "", job.message)

    def _launch(self, job: _Job) -> None:
        job.run += 1
        previous = job.thread
        job.thread = threading.Thread(target=self._run, args=(job, job.run, previous), name=f"video-{job.gid}",
                                      daemon=True)
        job.thread.start()

    @staticmethod
    def _stopped(job: _Job) -> str:
        return "paused" if job.stop == "pause" else "removed"

    def _run(self, job: _Job, run: int, previous: Optional[threading.Thread]) -> None:
        if previous is not None:
            previous.join()                               # never two runs writing the same .part
        with self._cond:
            while self._running >= self.max_downloads and not job.stop and job.run == run:
                self._cond.wait(0.5)
            if job.run != run:
                return                                    # resumed again meanwhile: the newer run goes on
            if job.stop:
                job.state = self._stopped(job)
                return
            self._running += 1
            job.state = "active"
        state, message = "complete", ""
        try:
            self._downloader(self._options(job, run), job.url)
        except _Stop:
            state = "stopped"
        except Exception as error:                       # yt-dlp's DownloadError, a network error, ...
            state, message = "error", _clean(error)
        finally:
            with self._cond:                              # one locked step: a resume never lands halfway
                self._running -= 1
                if job.run == run:
                    if job.stop or state == "stopped":
                        job.state = self._stopped(job)
                    else:
                        job.state, job.message = state, message
                    job.speed = 0
                self._cond.notify_all()

    def _options(self, job: _Job, run: int = 0) -> Dict[str, Any]:
        stem = rules.split_name(job.filename)[0] if "." in job.filename else job.filename
        headers = dict(h.split(": ", 1) for h in job.headers if ": " in h)
        options = {**_base_options(headers, self.ffmpeg), **format_options(self.quality, self.ffmpeg),
                   "outtmpl": os.path.join(job.folder, stem.replace("%", "%%") + ".%(ext)s"),
                   "noplaylist": True, "continuedl": True, "overwrites": False, "nopart": False,
                   "progress_hooks": [functools.partial(self._progress, job, run=run)],
                   "post_hooks": [functools.partial(self._finished, job)]}
        return options

    def _progress(self, job: _Job, d: Dict[str, Any], run: int = 0) -> None:
        if job.stop or (run and job.run != run):          # paused, removed, or a newer run took over
            from yt_dlp.utils import DownloadCancelled
            raise DownloadCancelled("stopped by Accipio")           # yt-dlp lets this one through untouched
        name = d.get("filename") or ""
        done = d.get("downloaded_bytes") or 0
        with self._cond:
            if d.get("tmpfilename"):
                job.temp_files.add(d["tmpfilename"])
            # a part's first report says where it stands (a resumed part starts far in): only growth after it counts
            new = done - job.parts[name][0] if name in job.parts else 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            job.parts[name] = [done, int(total)]
            if d.get("status") == "finished":
                job.parts[name][1] = max(job.parts[name][1], job.parts[name][0])
            job.speed = int(d.get("speed") or 0)
            wait = self._spend(max(0, new))
        if wait > 0:
            self._sleep(wait)                     # slows this video's reading; the others share the same budget

    def _spend(self, count: int) -> float:
        """Count bytes against the shared limit; the seconds the caller must wait to stay under it. Called locked."""
        limit = self.speed_limit
        if not limit:
            self._window = None
            return 0.0
        now = self._clock()
        window = self._window
        if (window is None or window[2] != limit
                or (now - window[0] > 5 and now >= window[0] + window[1] / limit)):   # never forgive a debt
            self._window = [now, 0, limit]        # a fresh window now and then, so an idle spell is not saved up
        self._window[1] += count
        return self._window[0] + self._window[1] / limit - now

    def _finished(self, job: _Job, path: str) -> None:
        with self._cond:
            job.path = path

    @staticmethod
    def _yt_dlp(options: Dict[str, Any], url: str) -> None:
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadCancelled
        with YoutubeDL(options) as ydl:
            try:
                code = ydl.download([url])
            except DownloadCancelled:
                raise _Stop() from None
        if code:
            raise RuntimeError("yt-dlp could not download this video")


def make_prober(engine: VideoEngine) -> Callable[..., Optional[VideoProbe]]:
    """The queue's video prober: None for a link that is not a video site, else what the site says."""
    def prober(url: str, headers: Optional[Dict[str, str]] = None, playlist: str = "",
               whole_list: bool = False) -> Optional[VideoProbe]:
        if not is_video_site(url):
            return None
        return probe_video(url, headers, engine.quality, engine.ffmpeg, playlist=playlist, whole_list=whole_list)
    return prober
