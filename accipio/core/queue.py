"""The download queue: a link goes in, a file comes out where the rules say.

Adding a link: the probe asks the server what it is, the rules choose folder and name, ``free_name`` makes
sure nothing is overwritten (counting the other downloads still running into the same folder), and the
engine starts it. ``refresh()`` is called on a timer and brings the rows up to date from the engine.

When a download finishes and its name showed no type the rules know (``download``, ``file.bin``), the first
bytes are read (``sniff``). If they show a known type, the file gets that extension and goes through the
rules again, which may move it; "why here?" says so.

A server that cannot resume makes resuming after a pause fail; the queue then starts that download over by
itself (a few times at most).

Links to video sites (when a video engine and prober are given) go to yt-dlp instead of aria2: the site is
asked for title, channel, uploader and playlist, which the rules can use. A playlist becomes one row per
video. Each row remembers its engine through ``source`` ("direct" or "video").

The probe makes a network request, so ``add`` and ``preview`` belong on a worker thread, not the window's.
"""
from __future__ import annotations

import datetime
import os
import shutil
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from accipio.core import aria2, probe, rules, sniff
from accipio.core.video import VideoProbe
from accipio.core.rules import Download, Placement, Rule
from accipio.core.store import ACTIVE, COMPLETE, ERROR, PAUSED, PROBING, UNFINISHED, Row, Store

MAX_RESTARTS = 3


@dataclass
class Preview:
    """What the Add box shows for one link before it is added."""
    url: str
    filename: str
    size: Optional[int]
    kind: str                      # "file", "video" or "playlist"
    placement: Placement
    count: int = 1                 # videos in a playlist


class Queue:
    def __init__(self, store: Store, engine: aria2.Aria2, rule_set: Callable[[], List[Rule]], base_folder: str, *,
                 prober: Callable[..., probe.Probe] = probe.probe, exists: Callable[[str], bool] = os.path.exists,
                 keep_history: bool = False, clock: Callable[[], datetime.datetime] = datetime.datetime.now,
                 video_engine=None, video_prober: Optional[Callable[..., Optional[VideoProbe]]] = None,
                 library_set: Optional[Callable[[], rules.Library]] = None):
        self.store = store
        self.engine = engine
        self.engines = {"direct": engine, "video": video_engine}
        self.video_prober = video_prober if video_engine is not None else None
        self.rule_set = rule_set
        self.library_set = library_set or rules.default_library
        self.base_folder = base_folder
        self.prober = prober
        self.exists = exists
        self.clock = clock
        self.speeds: Dict[int, int] = {}
        if not keep_history:
            store.forget_finished()
        self._reconcile()

    # ---- start-up --------------------------------------------------------------------------------------------

    def engine_of(self, row: Row):
        return self.engines.get(row.source) or self.engine

    def _reconcile(self) -> None:
        """After a restart: rows the engine no longer knows become errors that can be retried. Video downloads
        are started again (paused ones stay paused): yt-dlp continues from its .part file."""
        known = {s.gid for s in self.engine.all()}
        for row in self.store.rows(UNFINISHED):
            if row.state != PROBING and row.source == "video" and self.engines["video"] is not None and row.filename:
                gid = self.engines["video"].add(row.url, row.folder, row.filename,
                                                headers=[f"{k}: {v}" for k, v in row.headers.items()],
                                                paused=row.state == PAUSED)
                self.store.update(row.id, gid=gid)
            elif row.state == PROBING:
                self.store.update(row.id, state=ERROR, error="Accipio closed while checking this link. Retry to start it.")
            elif row.gid not in known:
                self.store.update(row.id, state=ERROR, error="The engine lost this download (its saved queue was missing). Retry to start it again.")

    # ---- adding ----------------------------------------------------------------------------------------------

    def _place(self, d: Download) -> Placement:
        return rules.place(self.rule_set(), d, self.base_folder, self.library_set())

    def _download(self, found: probe.Probe, page_url: str, when: datetime.datetime, filename: str = "") -> Download:
        return Download(url=found.url, filename=filename or found.filename, page_url=page_url, mime=found.mime,
                        size=found.size, when=when)

    def preview(self, url: str, *, page_url: str = "", headers: Optional[Dict[str, str]] = None) -> Preview:
        """What adding the link would do, for the Add box: its name, size and where the rules put it.
        Raises ProbeError when the server or site says no."""
        when = self.clock()
        found_video = self.video_prober(url, headers=headers) if self.video_prober else None
        if found_video is not None and found_video.entries is not None:
            video = {"playlist": found_video.playlist} if found_video.playlist else {}
            d = Download(url=url, filename=found_video.filename, page_url=page_url, source="video", video=video, when=when)
            return Preview(url, f"Playlist: {len(found_video.entries)} videos", None, "playlist",
                           self._place(d), len(found_video.entries))
        if found_video is not None:
            d = Download(url=found_video.url, filename=found_video.filename, page_url=page_url, size=found_video.size,
                         source="video", video=found_video.fields, when=when)
            return Preview(url, found_video.filename, found_video.size, "video", self._place(d))
        found = self.prober(url, headers=headers)
        placement = self._place(self._download(found, page_url, when))
        return Preview(url, placement.filename, found.size, "file", placement)

    def add(self, url: str, *, page_url: str = "", headers: Optional[Dict[str, str]] = None,
            folder: str = "", filename: str = "") -> int:
        """Queue a link. ``folder`` and ``filename`` are the Add box's overrides (empty = the rules decide)."""
        row_id = self.store.add(url, page_url=page_url, headers=headers or {}, state=PROBING, pinned=bool(folder),
                                added=self.clock().isoformat(timespec="seconds"))
        self._start(row_id, folder=folder, filename=filename)
        return row_id

    def _start(self, row_id: int, folder: str = "", filename: str = "") -> None:
        row = self.store.get(row_id)
        self.store.update(row_id, state=PROBING, error="")
        try:
            found_video = None
            if self.video_prober:
                found_video = self.video_prober(row.url, headers=row.headers, playlist=row.video.get("playlist", ""))
            if found_video is not None:
                self._start_video(row_id, found_video, folder, filename)
                return
            found = self.prober(row.url, headers=row.headers)
        except probe.ProbeError as error:
            self.store.update(row_id, state=ERROR, error=str(error))
            return
        placement = self._place(self._download(found, row.page_url, row.added_at, filename))
        target = os.path.normpath(folder) if folder else placement.folder
        name = rules.safe_part(filename, placement.filename) if filename else placement.filename
        name = self._free(target, name, row_id)
        why = placement.why()
        if folder:
            why += f" Folder chosen by hand: {target}."
        try:
            gid = self.engine.add(row.url, target, name, headers=[f"{k}: {v}" for k, v in row.headers.items()])
        except aria2.Aria2Error as error:
            self.store.update(row_id, state=ERROR, error=f"The engine refused it: {error}")
            return
        self.store.update(row_id, state=ACTIVE, gid=gid, folder=target, filename=name, mime=found.mime,
                          size=found.size, total=found.size or 0, done=0, resumable=found.resumable,
                          rules=placement.rule_numbers, why=why)

    def _start_video(self, row_id: int, found: VideoProbe, folder: str, filename: str) -> None:
        if found.entries is not None:                     # a playlist: this row becomes its first video
            if not found.entries:
                self.store.update(row_id, state=ERROR, error="The playlist is empty.")
                return
            row = self.store.get(row_id)
            video = {"playlist": found.playlist} if found.playlist else {}
            ids = [row_id] + [self.store.add(link, page_url=row.page_url, headers=row.headers, state=PROBING,
                                             source="video", video=video, pinned=row.pinned, added=row.added)
                              for link in found.entries[1:]]
            self.store.update(row_id, url=found.entries[0], source="video", video=video)
            for each in ids:
                self._start(each, folder=folder)
            return
        row = self.store.get(row_id)
        d = Download(url=found.url, filename=filename or found.filename, page_url=row.page_url, size=found.size,
                     source="video", video=found.fields, when=row.added_at)
        placement = self._place(d)
        target = os.path.normpath(folder) if folder else placement.folder
        name = rules.safe_part(filename, placement.filename) if filename else placement.filename
        name = self._free(target, name, row_id)
        why = placement.why() + (f" Folder chosen by hand: {target}." if folder else "")
        self.store.update(row_id, source="video", video=found.fields)
        try:
            gid = self.engines["video"].add(row.url, target, name, headers=[f"{k}: {v}" for k, v in row.headers.items()])
        except Exception as error:
            self.store.update(row_id, state=ERROR, error=f"The video engine refused it: {error}")
            return
        self.store.update(row_id, state=ACTIVE, gid=gid, folder=target, filename=name, mime="", size=found.size,
                          total=found.size or 0, done=0, resumable=True, rules=placement.rule_numbers, why=why)

    def _free(self, folder: str, filename: str, row_id: int) -> str:
        """A name nothing has: not a file on disk, and not another unfinished download into the same folder."""
        taken = {os.path.normcase(r.path) for r in self.store.rows((ACTIVE, PAUSED)) if r.id != row_id and r.path}
        return rules.free_name(folder, filename, lambda p: self.exists(p) or os.path.normcase(p) in taken)

    # ---- the timer -------------------------------------------------------------------------------------------

    def refresh(self) -> List[Row]:
        """Bring every running row up to date from the engine. Returns the rows that changed."""
        statuses = {source: {s.gid: s for s in engine.all()} for source, engine in self.engines.items() if engine}
        changed = []
        for row in self.store.rows((ACTIVE, PAUSED)):
            status = statuses.get(row.source if row.source in statuses else "direct", {}).get(row.gid)
            if status is None:
                continue
            self.speeds[row.id] = status.speed
            if status.state == "complete":
                self._finish(row, status)
            elif status.state == "error":
                self._failed(row, status)
            else:
                state = PAUSED if status.state == "paused" else ACTIVE
                if (state, status.done, status.total) == (row.state, row.done, row.total):
                    continue
                self.store.update(row.id, state=state, done=status.done, total=status.total or row.total)
            changed.append(self.store.get(row.id))
        return changed

    def _failed(self, row: Row, status: aria2.Status) -> None:
        if status.error_code == aria2.CANNOT_RESUME and row.restarts < MAX_RESTARTS:
            gid = self.engine_of(row).restart(row.gid)
            self.store.update(row.id, gid=gid, done=0, restarts=row.restarts + 1,
                              why=row.why + " The server cannot resume, so it started over.")
            return
        self.speeds.pop(row.id, None)
        self.store.update(row.id, state=ERROR, error=status.error_message or f"aria2 error {status.error_code}")

    def _finish(self, row: Row, status: aria2.Status) -> None:
        folder, name, why, numbers = row.folder, row.filename, row.why, row.rules
        if status.path and os.path.normcase(status.path) != os.path.normcase(row.path) and os.path.isfile(status.path):
            folder, name = os.path.split(status.path)            # yt-dlp may end up with another container (mkv)
            row.folder, row.filename = folder, name
        stem, ext = rules.split_name(name)
        if ext not in rules.KNOWN_EXTENSIONS:
            real = sniff.sniff(row.path)
            if real and real in rules.KNOWN_EXTENSIONS:
                renamed = f"{stem}.{real}" if ext in probe.SCRIPT_EXTENSIONS else f"{name}.{real}"
                d = Download(url=row.url, filename=renamed, page_url=row.page_url, mime=row.mime, size=status.total,
                             source=row.source, video=row.video, when=row.added_at)
                placement = self._place(d)
                target = folder if row.pinned else placement.folder
                final = rules.free_name(target, placement.filename, self.exists)
                os.makedirs(target, exist_ok=True)
                shutil.move(row.path, os.path.join(target, final))
                folder, name = target, final
                why += f" After the download the content turned out to be {real.upper()}, so it became {final}: {placement.why()}"
                numbers = placement.rule_numbers if not row.pinned else numbers
        self.store.update(row.id, state=COMPLETE, folder=folder, filename=name, why=why, rules=numbers,
                          done=status.done, total=status.total, headers={}, error="",
                          finished=self.clock().isoformat(timespec="seconds"))
        self.speeds.pop(row.id, None)
        self.engine_of(row).remove(row.gid)               # forget the result in the engine; the row keeps it

    # ---- buttons ---------------------------------------------------------------------------------------------

    def pause(self, row_id: int) -> None:
        row = self.store.get(row_id)
        if row and row.state == ACTIVE:
            self.engine_of(row).pause(row.gid)
            self.store.update(row_id, state=PAUSED)

    def resume(self, row_id: int) -> None:
        row = self.store.get(row_id)
        if row and row.state == PAUSED:
            self.engine_of(row).resume(row.gid)
            self.store.update(row_id, state=ACTIVE)

    def retry(self, row_id: int) -> None:
        """Start a failed download again. One that never got a name goes through the probe and rules again."""
        row = self.store.get(row_id)
        if not row or row.state != ERROR:
            return
        if not row.filename:
            self._start(row_id)
            return
        self._drop_partial(row)
        name = self._free(row.folder, row.filename, row_id)       # someone else's file may have appeared there
        try:
            gid = self.engine_of(row).add(row.url, row.folder, name, headers=[f"{k}: {v}" for k, v in row.headers.items()])
        except Exception as error:
            self.store.update(row_id, error=f"The engine refused it: {error}")
            return
        self.store.update(row_id, state=ACTIVE, gid=gid, filename=name, done=0, error="")

    def remove(self, row_id: int) -> None:
        """Take a download off the list. An unfinished one is stopped and its half-finished file deleted;
        a finished file is never touched."""
        row = self.store.get(row_id)
        if not row:
            return
        if row.gid:
            try:
                self.engine_of(row).remove(row.gid)
            except KeyError:
                pass                                      # the engine had already forgotten it
        if row.state != COMPLETE:
            self._drop_partial(row)
        self.speeds.pop(row_id, None)
        self.store.delete(row_id)

    def _drop_partial(self, row: Row) -> None:
        """Delete a half-finished file, but only when aria2's control file beside it proves it is ours."""
        if row.path and os.path.isfile(row.path + ".aria2"):
            for leftover in (row.path, row.path + ".aria2"):
                if os.path.isfile(leftover):
                    os.remove(leftover)

    def clear_finished(self) -> int:
        return self.store.forget_finished()

    def rows(self) -> List[Row]:
        return self.store.rows()
