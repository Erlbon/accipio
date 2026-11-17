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
asked for title, channel, uploader and playlist, which the rules can use. Each row remembers its engine
through ``source`` ("direct" or "video").

A playlist is never downloaded without a yes (decided 2026-11-06, 57 A+C, 58D). A link to a list itself waits
as one paused row with ``video["ask"]`` until ``answer_playlist``; yes makes it one row per video. A video link
that carries a list (``watch?v=…&list=…``) downloads just that video at once, and the list is then looked up
and offered the same way (``ask_playlists`` off: never offered). A YouTube Mix (``list=RD…``, a radio list
YouTube keeps making) is never offered: such a link is just its video (59B). The window shows the question. Adding with
``whole_list`` (the Add box, where the user ticked the playlist row) is the yes in advance.

A rule may send a download to another Accipio (a place of the kind "accipio", 2026-11-17). The link is still
checked here first, because the rules need its real name and type; then the row waits for that Accipio
(``WAITING``), is handed over (``SENT``) and ends as finished or failed there. Nothing in this file talks to the
network for that: ``handover.Sender`` carries the messages and calls ``handed``, ``not_handed`` and ``outcome``;
``handover.Collector`` is the other side and calls ``receive``, ``begin``, ``report``, ``heard`` and
``remove_for``. A download that came from another Accipio is never sent on to a third.

The probe makes a network request, so ``add`` and ``preview`` belong on a worker thread, not the window's.
"""
from __future__ import annotations

import dataclasses
import datetime
import os
import re
import secrets
import shutil
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from accipio.core import aria2, debug_log, probe, rules, sniff, torrents, unpack, video, zone
from accipio.core.video import VideoProbe
from accipio.core.rules import Download, Placement, Rule
from accipio.core.store import (ACTIVE, COMPLETE, ERROR, OWE_HEARD, OWE_REMOVE, PAUSED, PROBING, SENT, UNFINISHED,
                                WAITING, Row, Store)

MAX_RESTARTS = 3
INSIDE_OTHERS = 0.10        # share of an archive (by size) that may land under other rules before Accipio asks (78A)
AT_ONCE_GRACE = 5          # seconds a finished row stays with "as soon as they finish": the window sees it finish


def trash(path: str) -> None:
    """To the Recycle Bin (an unpacked archive, when Preferences say so)."""
    from redactor_common.core.trash import move_to_trash
    move_to_trash(path)


def share_speed(total: int, direct: int, video: int) -> Tuple[int, int]:
    """Split the total speed limit (bytes/s, 0 = none) between aria2 and the video engine by how many downloads
    each is running. An engine with nothing running keeps the whole limit, so the first download that starts
    there is not held back until the next refresh."""
    if not total or not direct or not video:
        return total, total
    direct_share = max(1, total * direct // (direct + video))
    return direct_share, max(1, total - direct_share)


def _video_id(url: str) -> str:
    """The video a YouTube link points at (``v=`` or the youtu.be path), to keep one video from being added twice."""
    import urllib.parse
    parts = urllib.parse.urlsplit(url)
    if parts.netloc.lower().endswith("youtu.be"):
        return parts.path.strip("/")
    return (urllib.parse.parse_qs(parts.query).get("v") or [url])[0]


def browser_site(url: str) -> str:
    """The address the rules see for a file the browser saved itself: a blob: link names the page's site
    (``blob:https://example.org/<id>`` -> ``https://example.org/<id>``); a data: link has none."""
    if url.lower().startswith("blob:"):
        return url[5:]
    return "" if url.lower().startswith("data:") else url


_NUMBERED = re.compile(r"(.*) \(\d+\)((?:\.[^.\s]+)*)")


def named_by_hand(suggested: str, saved: str) -> bool:
    """Whether the browser saved a file under another name than it proposed, which only happens when the user typed
    one in Save As. The browser's own "name (1).txt" for a name already taken does not count. Unknown = no."""
    suggested = os.path.basename(suggested.replace("\\", "/"))
    if not suggested or os.path.normcase(suggested) == os.path.normcase(saved):
        return False
    numbered = _NUMBERED.fullmatch(saved)
    return not (numbered and os.path.normcase(numbered.group(1) + numbered.group(2)) == os.path.normcase(suggested))


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
                 clear_finished: str = "start", clear_after_minutes: int = 15,
                 clock: Callable[[], datetime.datetime] = datetime.datetime.now,
                 video_engine=None, video_prober: Optional[Callable[..., Optional[VideoProbe]]] = None,
                 library_set: Optional[Callable[[], rules.Library]] = None, speed_limit: int = 0):
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
        self.speed_limit = speed_limit                   # the total for both engines; refresh() shares it out
        self._shares: Dict[str, int] = {}
        self.unpack_on = False                           # the archive trial (Preferences); off: archives are files
        self.after_unpack = "keep"                       # or "delete": the archive goes to the Recycle Bin
        self.ask_playlists = True                        # offer the rest of a list a video link carries (Settings)
        # Whether this browser ("Chrome", "Vivaldi"...) asks where to save each file (browser_settings.asks); None =
        # not known, which is all the queue knows by itself. The window puts the real reader here.
        self.browser_asks: Callable[[str], Optional[bool]] = lambda browser: None
        # Torrents are passed on to a torrent program, never downloaded here (torrents.py; Settings, off by default)
        self.torrents = torrents.OFF
        self.qbittorrent_path = ""
        self.pass_torrent: Callable[..., str] = torrents.hand_over       # tests put a stand-in here
        # Looking a list up can take many seconds (a YouTube Mix: 16 s for 371 videos), so it runs beside the
        # queue's worker, never on it; the store is safe from any thread. Tests run it in place.
        self.in_background: Callable[..., None] = \
            lambda fn, *args: threading.Thread(target=fn, args=args, daemon=True, name="accipio-list").start()
        # Called when there is something new to tell another Accipio (a row to hand over, a removal). Nothing
        # listens until a handover.Sender is attached; rows for another Accipio then simply wait.
        self.link_nudge: Callable[[], None] = lambda: None
        self.link_tick: Callable[[], None] = lambda: None     # from refresh(): time to ask the other Accipios again?
        self.clear_finished_how = clear_finished          # settings.CLEAR_*: when finished rows leave the list
        self.clear_after_minutes = clear_after_minutes
        if clear_finished != "never":                     # at the start every automatic choice clears what is due
            self.tidy(at_start=True)
        self._reconcile()

    def tidy(self, at_start: bool = False) -> int:
        """Finished rows leave the list as Settings says (2026-11-07, 71A): at the start ("start"), the given
        minutes after they finished ("after"), or at once ("done"). An archive still waiting for the user's choice
        stays. Only rows go, never files. Returns how many went."""
        how = self.clear_finished_how
        if how == "after":
            cutoff = self.clock() - datetime.timedelta(minutes=self.clear_after_minutes)
            return self.store.forget_finished(before=cutoff.isoformat(timespec="seconds"), keep_waiting=True)
        if how == "done" and not at_start:
            # a few seconds' grace: the window must see the row finish, or its "finished" notice never comes
            cutoff = self.clock() - datetime.timedelta(seconds=AT_ONCE_GRACE)
            return self.store.forget_finished(before=cutoff.isoformat(timespec="seconds"), keep_waiting=True)
        if how in ("done", "start") and at_start:
            return self.store.forget_finished(keep_waiting=True)
        return 0

    # ---- start-up --------------------------------------------------------------------------------------------

    def engine_of(self, row: Row):
        return self.engines.get(row.source) or self.engine

    def _reconcile(self) -> None:
        """After a restart: rows the engine no longer knows become errors that can be retried. Video downloads
        are started again (paused ones stay paused): yt-dlp continues from its .part file."""
        known = {s.gid for s in self.engine.all()}
        for row in self.store.rows(UNFINISHED):
            if row.video.get("ask") and not row.gid:
                continue                                  # a playlist link still waiting for its yes or no
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

    def _place(self, d: Download, local_only: bool = False) -> Placement:
        """``local_only``: rules that send to another Accipio are passed over (the file is, or must be, on this PC)."""
        return rules.place(self.rule_set(), d, self.base_folder, self.library_set(), local_only)

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
        if torrents.is_magnet(url):
            return Preview(url, torrents.magnet_name(url), None, "torrent", self._place_magnet(url, page_url, when))
        found = self.prober(url, headers=headers)
        placement = self._place(self._download(found, page_url, when))
        return Preview(url, placement.filename, found.size, "file", placement)

    # ---- torrents: passed on, not downloaded (2026-11-17#07, torrents.py) ----------------------------------------

    def _place_magnet(self, url: str, page_url: str, when: datetime.datetime, local_only: bool = False) -> Placement:
        """Where the rules put a magnet link's torrent: it counts as a file "<its name>.torrent" from that page."""
        name = rules.safe_part(torrents.magnet_name(url), "torrent") + "." + torrents.TORRENT_EXTENSION
        return self._place(Download(url="", filename=name, page_url=page_url, when=when), local_only)

    def _pass_magnet(self, row_id: int, folder: str = "") -> None:
        """A magnet link never reaches an engine (aria2 would download the torrent itself): the torrent program gets
        it, with the folder the rules chose. The row is done as soon as it is passed on; no file of its own."""
        row = self.store.get(row_id)
        name = torrents.magnet_name(row.url)
        placement = self._place_magnet(row.url, row.page_url, row.added_at, local_only=True)
        target = os.path.normpath(folder) if folder else placement.folder
        try:
            told = self.pass_torrent(row.url, self.torrents, folder=target, qbittorrent=self.qbittorrent_path)
        except torrents.TorrentError as error:
            debug_log.note("magnet not passed on", row=row_id, way=self.torrents, error=str(error))
            self.store.update(row_id, state=ERROR, error=f"A magnet link: {error}")
            return
        knows_folder = self.torrents == torrents.QBITTORRENT
        why = f"A magnet link. {told}" + (f" {placement.why()}" if knows_folder and not folder else "") \
            + self._whence(row)
        debug_log.note("magnet passed on", row=row_id, way=self.torrents, name=name,
                       folder=target if knows_folder else "the torrent program's own", rules=placement.rule_numbers or "none")
        self.store.update(row_id, state=COMPLETE, folder=target if knows_folder else "", filename=name, error="",
                          rules=placement.rule_numbers if knows_folder and not folder else "", why=why, headers={},
                          finished=self.clock().isoformat(timespec="seconds"))

    def _pass_torrent_file(self, row_id: int) -> None:
        """A finished .torrent file goes to the torrent program, which saves the torrent's files beside it."""
        row = self.store.get(row_id)
        try:
            told = self.pass_torrent(row.path, self.torrents, folder=row.folder, qbittorrent=self.qbittorrent_path)
        except torrents.TorrentError as error:
            told = f"It could not be passed on to a torrent program: {error}"
        debug_log.note("torrent file", row=row_id, way=self.torrents, filename=row.filename, result=told)
        self.store.update(row_id, why=f"{row.why} {told}")

    def add(self, url: str, *, page_url: str = "", headers: Optional[Dict[str, str]] = None,
            folder: str = "", filename: str = "", whole_list: bool = False) -> int:
        """Queue a link. ``folder`` and ``filename`` are the Add box's overrides (empty = the rules decide);
        ``whole_list``: a playlist was approved already, so no question."""
        row_id = self.store.add(url, page_url=page_url, headers=headers or {}, state=PROBING, pinned=bool(folder),
                                added=self.clock().isoformat(timespec="seconds"),
                                video={"whole_list": True} if whole_list else {})
        self._start(row_id, folder=folder, filename=filename)
        return row_id

    @staticmethod
    def _stays_here(row: Row, folder: str, here: str) -> bool:
        """Whether this download must be placed on this PC whatever the rules say about another Accipio: its folder
        was chosen by hand, it is being downloaded here because the other Accipio could not, or it came from
        another Accipio itself (never sent on: two Accipios pointing at each other would pass it round for ever)."""
        return bool(folder or here or row.remote.get("from"))

    @staticmethod
    def _whence(row: Row) -> str:
        """For "why here?" on the collector: who sent it."""
        return f" Sent from {row.remote.get('from_name') or 'another Accipio'}." if row.remote.get("from") else ""

    def _start(self, row_id: int, folder: str = "", filename: str = "", here: str = "") -> None:
        """Check the link, place it and start it. ``here``: why it is downloaded on this PC although a rule sends
        it to another Accipio (told in "why here?"); the rules that say "another Accipio" are then passed over."""
        row = self.store.get(row_id)
        self.store.update(row_id, state=PROBING, error="")
        local = self._stays_here(row, folder, here)
        if torrents.is_magnet(row.url):
            placement = self._place_magnet(row.url, row.page_url, row.added_at, local_only=local)
            if placement.remote is not None:               # the other Accipio's torrent setting handles it
                self._hand_over(row_id, placement, torrents.magnet_name(row.url))
                return
            self._pass_magnet(row_id, folder)
            return
        debug_log.note("check", row=row_id, url=row.url, page=row.page_url, headers=row.headers)
        started = time.monotonic()
        try:
            found_video = None
            if self.video_prober:
                found_video = self.video_prober(row.url, headers=row.headers, playlist=row.video.get("playlist", ""),
                                                whole_list=bool(row.video.get("whole_list")))
                debug_log.note("video check", row=row_id, seconds=round(time.monotonic() - started, 2),
                               video=found_video is not None)
            if found_video is not None:
                self._start_video(row_id, found_video, folder, filename, here)
                return
            link_started = time.monotonic()
            found = self.prober(row.url, headers=row.headers)
        except probe.ProbeError as error:
            debug_log.note("check failed", row=row_id, seconds=round(time.monotonic() - started, 2), error=str(error))
            if not local:
                # This PC cannot reach the link (a site blocked here, say), but the other Accipio may: go by the
                # name in the link, and hand it over when that lands on a rule for another Accipio.
                guess = rules.safe_part(probe.filename_from_url(row.url), "download")
                placement = self._place(Download(url=row.url, filename=guess, page_url=row.page_url, when=row.added_at))
                if placement.remote is not None:
                    self._hand_over(row_id, placement, guess,
                                    note=f" This PC could not check the link itself ({error}), so it went by the "
                                         "name in the link.")
                    return
            self.store.update(row_id, state=ERROR, error=str(error))
            return
        debug_log.note("link check", row=row_id, seconds=round(time.monotonic() - link_started, 2), final_url=found.url,
                       filename=found.filename, mime=found.mime, size=found.size, resumable=found.resumable)
        placement = self._place(self._download(found, row.page_url, row.added_at, filename), local)
        if placement.remote is not None:
            self._hand_over(row_id, placement, placement.filename, size=found.size, mime=found.mime)
            return
        target = os.path.normpath(folder) if folder else placement.folder
        name = rules.safe_part(filename, placement.filename) if filename else placement.filename
        name = self._free(target, name, row_id)
        why = placement.why() + (f" {here}" if here else "") + self._whence(row)
        if folder:
            why += f" Folder chosen by hand: {target}."
        debug_log.note("placed", row=row_id, rules=placement.rule_numbers or "none", folder=target, filename=name,
                       archive_choice=placement.unpack or "none", by_hand=bool(folder), why=why)
        try:
            gid = self.engine.add(row.url, target, name, headers=[f"{k}: {v}" for k, v in row.headers.items()])
        except aria2.Aria2Error as error:
            debug_log.note("engine refused", row=row_id, error=str(error))
            self.store.update(row_id, state=ERROR, error=f"The engine refused it: {error}")
            return
        self.store.update(row_id, state=ACTIVE, gid=gid, folder=target, filename=name, mime=found.mime,
                          size=found.size, total=found.size or 0, done=0, resumable=found.resumable,
                          rules=placement.rule_numbers, why=why)

    def _start_video(self, row_id: int, found: VideoProbe, folder: str, filename: str, here: str = "") -> None:
        if found.entries is not None:                     # a playlist: after a yes, this row becomes its first video
            if not found.entries:
                self.store.update(row_id, state=ERROR, error="The playlist is empty.")
                return
            row = self.store.get(row_id)
            if not row.video.get("whole_list"):
                self.store.update(row_id, state=PAUSED, source="video",
                                  video={"ask": self._list_question("list", row.url, found)})
                return
            listed = {"playlist": found.playlist} if found.playlist else {}
            ids = [row_id] + [self.store.add(link, page_url=row.page_url, headers=row.headers, state=PROBING,
                                             source="video", video=listed, pinned=row.pinned, added=row.added)
                              for link in found.entries[1:]]
            self.store.update(row_id, url=found.entries[0], source="video", video=listed)
            for each in ids:
                self._start(each, folder=folder)
            return
        row = self.store.get(row_id)
        d = Download(url=found.url, filename=filename or found.filename, page_url=row.page_url, size=found.size,
                     source="video", video=found.fields, when=row.added_at)
        placement = self._place(d, self._stays_here(row, folder, here))
        offer = self.ask_playlists and video.carries_a_list(row.url) and not video.is_mix(row.url) \
            and not row.remote.get("from")                # a Mix is YouTube's endless radio: not offered (59B); and
        #                                                   the Accipio that sent a video has asked about its list
        if placement.remote is not None:                  # the list is still asked about here, where the user is
            self.store.update(row_id, source="video", video=found.fields)
            self._hand_over(row_id, placement, placement.filename, size=found.size)
            if offer:
                self.in_background(self._offer_the_list, row_id)
            return
        target = os.path.normpath(folder) if folder else placement.folder
        name = rules.safe_part(filename, placement.filename) if filename else placement.filename
        name = self._free(target, name, row_id)
        why = placement.why() + (f" {here}" if here else "") + self._whence(row) \
            + (f" Folder chosen by hand: {target}." if folder else "")
        self.store.update(row_id, source="video", video=found.fields)
        try:
            gid = self.engines["video"].add(row.url, target, name, headers=[f"{k}: {v}" for k, v in row.headers.items()])
        except Exception as error:
            self.store.update(row_id, state=ERROR, error=f"The video engine refused it: {error}")
            return
        self.store.update(row_id, state=ACTIVE, gid=gid, folder=target, filename=name, mime="", size=found.size,
                          total=found.size or 0, done=0, resumable=True, rules=placement.rule_numbers, why=why)
        if offer:
            self.in_background(self._offer_the_list, row_id)

    # ---- playlists: only with a yes --------------------------------------------------------------------------

    @staticmethod
    def _list_question(kind: str, url: str, found: VideoProbe) -> dict:
        title = found.playlist or found.title
        return {"kind": kind, "count": len(found.entries or []), "title": title, "mix": video.is_mix(url),
                "links": list(found.entries or [])}

    def _offer_the_list(self, row_id: int) -> None:
        """The video is on its way; now see how big the list it came with is, so the window can offer the rest.
        A list that cannot be read is simply not offered."""
        row = self.store.get(row_id)
        try:
            found = self.video_prober(row.url, headers=row.headers, whole_list=True)
        except Exception:                                 # a thread of its own: nothing may escape it
            return
        if found is None or not found.entries or len(found.entries) < 2:
            return
        row = self.store.get(row_id)                      # read again: the download went on meanwhile
        if row is not None:
            self.store.update(row_id, video={**row.video, "ask": self._list_question("rest", row.url, found)})

    def answer_playlist(self, row_id: int, take_all: bool) -> None:
        """The user's answer. A list link: yes = one row per video, no = the row goes. A video's list: yes = the
        other videos are added, no = nothing more."""
        row = self.store.get(row_id)
        ask = row.video.get("ask") if row else None
        if not ask:
            return
        rest = {k: v for k, v in row.video.items() if k != "ask"}
        folder = row.folder if row.pinned else ""
        if ask["kind"] == "list":
            if not take_all:
                self.remove(row_id)
                return
            self.store.update(row_id, video={**rest, "whole_list": True})
            self._start_video(row_id, VideoProbe(row.url, ask["title"], playlist=ask["title"], entries=ask["links"]),
                              folder, "")
            return
        self.store.update(row_id, video=rest)
        if not take_all:
            return
        mine = _video_id(row.url)
        listed = {"playlist": ask["title"]} if ask["title"] else {}
        for link in ask["links"]:
            if _video_id(link) != mine:
                new = self.store.add(link, page_url=row.page_url, headers=row.headers, state=PROBING, source="video",
                                     video=listed, pinned=row.pinned, added=row.added)
                self._start(new, folder=folder)

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
        self._share_speed()
        self.tidy()
        self.link_tick()
        return changed

    def _share_speed(self) -> None:
        """Give each engine its part of the one total limit; sent only when a part changes."""
        running = [r.source if r.source in self.engines else "direct" for r in self.store.rows((ACTIVE,))]
        direct, video = share_speed(self.speed_limit, running.count("direct"), running.count("video"))
        for source, limit in (("direct", direct), ("video", video)):
            engine = self.engines.get(source)
            if engine is not None and self._shares.get(source) != limit:
                engine.set_limits(speed_limit=limit)
                self._shares[source] = limit

    def _failed(self, row: Row, status: aria2.Status) -> None:
        if status.error_code == aria2.CANNOT_RESUME and row.restarts < MAX_RESTARTS:
            gid = self.engine_of(row).restart(row.gid)
            self.store.update(row.id, gid=gid, done=0, restarts=row.restarts + 1,
                              why=row.why + " The server cannot resume, so it started over.")
            return
        self.speeds.pop(row.id, None)
        debug_log.note("failed", row=row.id, code=status.error_code, error=status.error_message, done=status.done,
                       total=status.total)
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
                placement = self._place(d, local_only=True)       # the file is on this PC: it stays on it
                target = folder if row.pinned else placement.folder
                final = rules.free_name(target, placement.filename, self.exists)
                os.makedirs(target, exist_ok=True)
                shutil.move(row.path, os.path.join(target, final))
                folder, name = target, final
                why += f" After the download the content turned out to be {real.upper()}, so it became {final}: {placement.why()}"
                numbers = placement.rule_numbers if not row.pinned else numbers
        zone.mark(os.path.join(folder, name), row.url, row.page_url)    # as Chrome does: SmartScreen warns
        self.store.update(row.id, state=COMPLETE, folder=folder, filename=name, why=why, rules=numbers,
                          done=status.done, total=status.total, headers={}, error="",
                          finished=self.clock().isoformat(timespec="seconds"))
        self.speeds.pop(row.id, None)
        self.engine_of(row).remove(row.gid)               # forget the result in the engine; the row keeps it
        debug_log.note("finished", row=row.id, folder=folder, filename=name, size=status.total, rules=numbers or "none",
                       archive=unpack.is_archive(name), archive_trial_on=self.unpack_on)
        if self.unpack_on and unpack.is_archive(name):
            self._archive_arrived(row.id)
        if self.torrents != torrents.OFF and torrents.is_torrent_file(name):
            self._pass_torrent_file(row.id)

    # ---- files the browser saved itself (2026-11-17) ---------------------------------------------------------

    def file_saved(self, path: str, *, url: str = "", page_url: str = "", mime: str = "",
                   suggested: str = "", browser: str = "") -> Optional[int]:
        """A file the browser had to download itself (one a page built: a blob: or data: link; or one Accipio's own
        request failed for) is on disk now: move it to where the rules say and list it as finished. Returns its row.

        A file the user placed stays where they put it: when the browser is set to ask where to save each file
        (``browser_asks``, from the browser's own settings), or, where that is not known, when the name differs from
        ``suggested``, the one the browser proposed (so the user typed one in Save As). A file that cannot be moved
        stays too; the row says why. None when the file is not there."""
        path = os.path.normpath(path)
        if not os.path.isabs(path) or not os.path.isfile(path):
            debug_log.note("browser's file not found", path=path)
            return None
        folder, name = os.path.split(path)
        site = browser_site(url)
        now = self.clock()
        size = os.path.getsize(path)
        why = "The browser downloaded this file itself (a page built it, or the site refused Accipio)."
        numbers = ""
        try:
            asks = self.browser_asks(browser)
        except Exception as error:                        # the browser's settings are not ours: never fatal
            debug_log.note("browser settings unreadable", error=str(error))
            asks = None
        if asks:
            why += " The browser is set to ask where to save each file, so you chose this place and it stays there."
        elif named_by_hand(suggested, name):
            why += " You gave it a name of your own in the browser's Save As box, so it stays where you put it."
        else:
            # a file only the browser could fetch stays on this PC (the user's 100A): never to another Accipio
            placement = self._place(Download(url=site, filename=name, page_url=page_url, mime=mime, size=size, when=now),
                                    local_only=True)
            target, final = placement.folder, placement.filename
            if os.path.normcase(os.path.join(target, final)) == os.path.normcase(path):
                why += f" It was already in place: {placement.why()}"
                numbers = placement.rule_numbers
            else:
                final = self._free(target, final, 0)
                try:
                    os.makedirs(target, exist_ok=True)
                    shutil.move(path, os.path.join(target, final))
                except OSError as error:
                    why += f" It could not be moved to {target} ({error}), so it stays in {folder}."
                else:
                    why += f" Then it was moved from {folder}: {placement.why()}"
                    folder, name, numbers = target, final, placement.rule_numbers
        zone.mark(os.path.join(folder, name), site, page_url)       # a move to another drive loses the browser's mark
        row_id = self.store.add(page_url or site, page_url=page_url, state=COMPLETE, folder=folder, filename=name,
                                mime=mime, size=size, total=size, done=size, rules=numbers, why=why,
                                added=now.isoformat(timespec="seconds"), finished=now.isoformat(timespec="seconds"))
        debug_log.note("filed the browser's file", row=row_id, saved_as=path,
                       browser=browser or "not known",
                       browser_asks_where={True: "yes", False: "no", None: "not known"}[asks], folder=folder, filename=name, size=size,
                       rules=numbers or "none", why=why)
        if self.unpack_on and unpack.is_archive(name):
            self._archive_arrived(row_id)
        return row_id

    # ---- archives (a trial since 2026-11-05, decision 43E; see unpack.py) ---------------------------------------

    def _archive_arrived(self, row_id: int) -> None:
        """A finished archive: unpacked at once when its rule says how, else it waits for the user's choice with a
        suggested one."""
        row = self.store.get(row_id)
        try:
            entries = unpack.contents(row.path)
        except unpack.UnpackError as error:
            debug_log.note("archive unreadable", row=row_id, error=str(error))
            self.store.update(row_id, unpack={"state": "failed", "message": str(error)})
            return
        placement = self._place(Download(url=row.url, filename=row.filename, page_url=row.page_url, mime=row.mime,
                                         size=row.size, when=row.added_at), local_only=True)
        choice, folder, why = placement.unpack, placement.folder, f"the archive's own rule ({placement.rule_numbers})"
        if not choice and not row.pinned:
            choice, folder, why = self._choice_from_inside(row, entries)
        debug_log.note("archive", row=row_id, filename=row.filename, files_inside=len(entries),
                       rules=placement.rule_numbers or "none", rule_choice=placement.unpack or "none",
                       folder_by_hand=row.pinned,
                       decision=(f"unpack ({choice}) by {why}" if choice and not row.pinned
                                 else "ask: folder chosen by hand" if choice else f"ask: {why}"))
        if choice and not row.pinned:
            try:
                self.unpack_archive(row_id, choice, folder=folder, entries=entries, by_rule=True)
            except unpack.UnpackError as error:
                self.store.update(row_id, unpack={"state": "failed", "message": str(error)})
            return
        self._wait_for_choice(row, entries)

    def _choice_from_inside(self, row: Row, entries: List[unpack.Entry]) -> Tuple[str, str, str]:
        """(choice, folder, why) for an archive whose own name no rule with an archive choice takes (2026-11-07,
        76C, 77A, 78A): the rule its files land under decides, when that rule has an archive choice, its files are
        more than half of the archive by size, and files under other rules are at most INSIDE_OTHERS of it (a
        cover or an .nfo under no rule goes along). Otherwise ("", "", why Accipio asks)."""
        sizes: Dict[int, int] = {}
        placements: Dict[int, Placement] = {}
        rule_list = self.rule_set()
        total = 0
        for entry in entries:
            weight = max(entry.size, 1)
            total += weight
            name = os.path.basename(entry.name.replace("\\", "/"))
            inner = self._inner(row, name)
            if inner.steps and rule_list[inner.steps[0].number - 1].conditions:
                number = inner.steps[0].number
                sizes[number] = sizes.get(number, 0) + weight
                placements.setdefault(number, inner)
        if not sizes:
            return "", "", "no rule that matches this file, or the files inside, says what to do with an archive"
        top = max(sizes, key=sizes.get)
        shares = ", ".join(f"rule {n}: {round(100 * s / total)}%" for n, s in sorted(sizes.items()))
        others = sum(s for n, s in sizes.items() if n != top)
        choice = placements[top].unpack
        if not choice:
            return "", "", f"the files inside land under rule {top}, which has no archive choice ({shares})"
        if sizes[top] * 2 <= total:
            return "", "", f"rule {top}'s files are not most of the archive ({shares})"
        if others > total * INSIDE_OTHERS:
            return "", "", f"the files inside land under different rules ({shares})"
        return choice, placements[top].folder, f"rule {top}, which most of the files inside land under ({shares})"

    def _wait_for_choice(self, row: Row, entries: List[unpack.Entry]) -> None:
        suggestion = unpack.suggest(entries, lambda name: self._fits_a_rule(row, name))
        self.store.update(row.id, unpack={"state": "waiting", "count": len(entries), "suggest": suggestion})

    def _inner(self, row: Row, name: str) -> Placement:
        """Where a file from inside the archive would go if it had been downloaded on its own, from the same link."""
        return self._place(Download(url=row.url, filename=name, page_url=row.page_url, when=row.added_at),
                           local_only=True)

    def _fits_a_rule(self, row: Row, name: str) -> bool:
        """For the suggestion: a rule with conditions knows this file (a catch-all rule does not count)."""
        steps = self._inner(row, name).steps
        rule_list = self.rule_set()
        return bool(steps) and bool(rule_list[steps[0].number - 1].conditions)

    def archive_plan(self, row_id: int, mode: str, folder: str = "",
                     entries: Optional[List[unpack.Entry]] = None) -> List[unpack.Move]:
        """Where each file would go: "folder" into ``folder`` (default: the archive's own folder), "sort" by the
        rules with the rest in ``folder`` (default: Default). Raises UnpackError."""
        row = self.store.get(row_id)
        entries = unpack.contents(row.path) if entries is None else entries
        if mode == unpack.FOLDER:
            return unpack.plan_folder(entries, folder or row.folder, row.filename, exists=self.exists)

        def placed(name: str):
            p = self._inner(row, name)
            return (p.folder, p.filename, p.why()) if p.steps else None
        return unpack.plan_sort(entries, placed, folder or self.base_folder, row.filename, exists=self.exists)

    def unpack_archive(self, row_id: int, mode: str, folder: str = "", *,
                       entries: Optional[List[unpack.Entry]] = None, by_rule: bool = False) -> None:
        """Do the user's (or the rule's) choice. Raises UnpackError; nothing is left behind then."""
        if mode == unpack.KEEP:
            self.keep_packed(row_id)
            return
        row = self.store.get(row_id)
        moves = self.archive_plan(row_id, mode, folder, entries)
        done = unpack.extract(row.path, moves, row.url, row.page_url)
        trashed = False
        if self.after_unpack == "delete":
            try:
                trash(row.path)
                trashed = True
            except OSError:
                pass                                      # it stays; the row still says it was unpacked
        how = "as a folder in " + os.path.dirname(moves[0].target) if mode == unpack.FOLDER and moves \
            else "sorted by the rules"
        who = "its rule " if by_rule else ""
        self.store.update(row_id, why=f"{row.why} Then {who}unpacked {len(moves)} files {how}.",
                          unpack={"state": "done", "mode": mode, "count": len(moves), "by_rule": by_rule,
                                  "trashed": trashed, "done": dataclasses.asdict(done)})

    def together_folder(self, row_id: int) -> str:
        """The folder name "Unpack as a folder" offers first: where the program inside would go by the rules, else
        the folder of the rule that placed the archive, else Default."""
        row = self.store.get(row_id)
        rule_list = self.rule_set()
        try:
            names = [e.name.replace("\\", "/").rsplit("/", 1)[-1] for e in unpack.contents(row.path)]
        except unpack.UnpackError:
            names = []
        for name in names:
            if rules.split_name(name)[1] in unpack.PROGRAM_EXTENSIONS:
                steps = self._inner(row, name).steps
                if steps:
                    return rule_list[steps[0].number - 1].target or rules.DEFAULT_FOLDER
                break
        return self.placed_folder(row_id)

    def placed_folder(self, row_id: int) -> str:
        """The folder name of the rule that placed this download (Default when none did)."""
        row = self.store.get(row_id)
        rule_list = self.rule_set()
        first = row.rules.split("+")[0]
        if first.isdigit() and 0 < int(first) <= len(rule_list):
            return rule_list[int(first) - 1].target or rules.DEFAULT_FOLDER
        return rules.DEFAULT_FOLDER

    def folder_named(self, row_id: int, name: str) -> str:
        """The path of a folder from the Folders tab, with its tokens filled in for this download."""
        row = self.store.get(row_id)
        d = Download(url=row.url, filename=row.filename, page_url=row.page_url, when=row.added_at)
        return rules.place([Rule("", target=name or rules.DEFAULT_FOLDER)], d, self.base_folder, self.library_set()).folder

    def seen(self, row_id: int) -> None:
        """The user has seen what a rule unpacked by itself: its box goes (Undo stays in the row's menu)."""
        row = self.store.get(row_id)
        if row and row.unpack:
            self.store.update(row_id, unpack={**row.unpack, "seen": True})

    def keep_packed(self, row_id: int) -> None:
        self.store.update(row_id, unpack={"state": "kept"})

    def undo_unpack(self, row_id: int) -> List[str]:
        """Take an unpack back: its files and the folders it made go, and the archive waits for a choice again.
        Returns the files kept because they were changed since."""
        row = self.store.get(row_id)
        info = row.unpack if row else {}
        if info.get("state") != "done":
            return []
        saved = info["done"]
        kept = unpack.undo(unpack.Done(saved["archive"], [tuple(f) for f in saved["files"]], saved["folders"]))
        why = row.why + " Unpacking undone."
        if os.path.isfile(row.path):
            try:
                self.store.update(row_id, why=why)
                self._wait_for_choice(row, unpack.contents(row.path))
                return kept
            except unpack.UnpackError:
                pass
        self.store.update(row_id, why=why, unpack={
            "state": "kept", "message": "The archive is in the Recycle Bin; restore it from there to unpack it again."})
        return kept

    # ---- buttons ---------------------------------------------------------------------------------------------

    def pause(self, row_id: int) -> None:
        row = self.store.get(row_id)
        if row and row.state == ACTIVE:
            self.engine_of(row).pause(row.gid)
            self.store.update(row_id, state=PAUSED)

    def resume(self, row_id: int) -> None:
        row = self.store.get(row_id)
        if row and row.state == PAUSED and row.gid:          # a playlist waiting for its yes has nothing to resume
            self.engine_of(row).resume(row.gid)
            self.store.update(row_id, state=ACTIVE)

    def retry(self, row_id: int) -> None:
        """Start a failed download again. One that never got a name goes through the probe and rules again."""
        row = self.store.get(row_id)
        if not row or row.state != ERROR:
            return
        if row.remote.get("peer"):                # it failed on, or never got to, another Accipio: send it again
            self._forget_there(row)
            self.store.update(row_id, remote={})
            self._start(row_id)
            return
        if not row.filename or torrents.is_magnet(row.url):       # a magnet link is passed on again, never downloaded
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
        a finished file is never touched. One that another Accipio holds unfinished is stopped and removed there
        too, at once or when that Accipio is back (the user's 103A); what it has finished stays there."""
        row = self.store.get(row_id)
        if not row:
            return
        if row.state != COMPLETE:
            self._forget_there(row)
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
        if row.folder and row.path and os.path.isfile(row.path + ".aria2"):       # no folder: nothing was ever here
            for leftover in (row.path, row.path + ".aria2"):
                if os.path.isfile(leftover):
                    os.remove(leftover)

    # ---- another Accipio: this one sends (2026-11-17; the messages are handover.Sender's business) --------------

    def _hand_over(self, row_id: int, placement: Placement, name: str, *, size: Optional[int] = None,
                   mime: str = "", note: str = "") -> None:
        """The rules send this download to another Accipio: the row waits for it. Magnet links and .torrent files
        always wait, whatever the place says (the user's 97C): they are never downloaded on this PC instead."""
        place = placement.remote
        row = self.store.get(row_id)
        torrent = torrents.is_magnet(row.url) or torrents.is_torrent_file(name)
        away = rules.AWAY_HERE if place.away == rules.AWAY_HERE and not torrent else rules.AWAY_WAIT
        debug_log.note("for another Accipio", row=row_id, place=place.name, peer=place.peer, away=away,
                       cookies=bool(place.cookies), rules=placement.rule_numbers, filename=name)
        self.store.update(row_id, state=WAITING, gid="", folder="", filename=name, mime=mime, size=size,
                          total=size or 0, done=0, rules=placement.rule_numbers, why=placement.why() + note, error="",
                          remote={"peer": place.peer, "place": place.name, "away": away, "cookies": bool(place.cookies),
                                  "torrent": torrent, "ref": f"{row_id}-{secrets.token_hex(4)}"})
        self.link_nudge()

    def _handed_row(self, row_id: int, ref: str, state: str) -> Optional[Row]:
        """The row a message was about, when it is still the same hand-over (not removed or retried meanwhile)."""
        row = self.store.get(row_id)
        return row if row is not None and row.state == state and row.remote.get("ref") == ref else None

    def handed(self, row_id: int, ref: str, peer: str, remote_id: int) -> None:
        """The other Accipio has taken it and calls it ``remote_id``. A row removed here while the message was on
        its way is removed there too."""
        row = self._handed_row(row_id, ref, WAITING)
        if row is None:
            self.store.owe(peer, OWE_REMOVE, remote_id)
            self.link_nudge()
            return
        self.store.update(row_id, state=SENT, remote={**row.remote, "id": remote_id})

    def not_handed(self, row_id: int, ref: str, reason: str, away: bool) -> None:
        """It could not be handed over. ``away``: the other Accipio was not reached, so the row waits, or is
        downloaded here when its place says so. Otherwise that Accipio was reached and said no (not paired any
        more, not a link it takes): waiting would never end, so the row fails with the reason, unless its place
        says "download here"."""
        row = self._handed_row(row_id, ref, WAITING)
        if row is None:
            return
        if row.remote.get("away") == rules.AWAY_HERE:
            self._download_here(row, reason)
        elif not away:
            debug_log.note("not taken by the other Accipio", row=row_id, place=row.remote.get("place"), error=reason)
            self.store.update(row_id, state=ERROR, error=reason)

    def outcome(self, row_id: int, ref: str, report: Dict) -> None:
        """What the other Accipio says about a download it has (the user's 98B): finished there, failed there, or
        no longer listed. "working" changes nothing: there is no live percent."""
        row = self._handed_row(row_id, ref, SENT)
        state = report.get("state")
        if row is None or state not in ("finished", "failed", "gone"):
            return
        place, peer, remote_id = row.remote.get("place") or "the other Accipio", row.remote["peer"], row.remote["id"]
        debug_log.note("outcome from the other Accipio", row=row_id, place=place, state=state,
                       folder=report.get("folder") or "", error=report.get("error") or "")
        if state == "finished":
            folder = str(report.get("folder") or "")
            told = f"Finished on {place}" + (f", in {folder}" if folder else "")
            self.store.update(row_id, state=COMPLETE, filename=str(report.get("filename") or "") or row.filename,
                              why=f"{row.why} {told}.", headers={}, error="", remote={**row.remote, "outcome": told},
                              finished=self.clock().isoformat(timespec="seconds"))
            self.store.owe(peer, OWE_HEARD, remote_id)
        elif state == "failed":
            reason = str(report.get("error") or "no reason given")
            if row.remote.get("away") == rules.AWAY_HERE:
                self.store.owe(peer, OWE_REMOVE, remote_id)       # its failed row there has no use any more
                self._download_here(row, f"{place} could not download it ({reason})")
            else:
                self.store.update(row_id, state=ERROR, error=f"Failed on {place}: {reason}")
                self.store.owe(peer, OWE_HEARD, remote_id)
        else:
            self.store.update(row_id, state=ERROR, remote={k: v for k, v in row.remote.items() if k != "id"},
                              error=f"{place} no longer lists this download: it was removed there before this "
                                    "Accipio heard how it went.")
        self.link_nudge()

    def _download_here(self, row: Row, reason: str) -> None:
        """The place says "download here" (97C): by this PC's other rules, as if the rules that say "another
        Accipio" were not there, else the Default folder (the user's 102A)."""
        self.store.update(row.id, remote={})
        self._start(row.id, here=f"{reason.rstrip('.')}, so it is downloaded on this PC instead.")

    def _forget_there(self, row: Row) -> None:
        """This row is going, or is being sent anew: what the other Accipio holds for it (unfinished or failed)
        is to be stopped and removed there, now or when it is back."""
        if row.remote.get("peer") and row.remote.get("id") is not None:
            self.store.owe(row.remote["peer"], OWE_REMOVE, row.remote["id"])
            self.link_nudge()

    # ---- another Accipio: this one collects (called by handover.Collector) --------------------------------------

    def receive(self, url: str, *, peer_id: str, peer_name: str, page_url: str = "",
                headers: Optional[Dict[str, str]] = None, ref: str = "") -> Tuple[int, bool]:
        """A link another Accipio sends: listed at once, so that Accipio has its answer without waiting for the
        check (``begin`` does the rest). Returns the row and whether it is new: the same ``ref`` asked twice (the
        first answer got lost on the way) is one download. No network here, safe from any thread."""
        if ref:
            for row in self.store.rows():
                if row.remote.get("from") == peer_id and row.remote.get("ref") == ref:
                    return row.id, False
        return self.store.add(url, page_url=page_url, headers=headers or {}, state=PROBING,
                              added=self.clock().isoformat(timespec="seconds"),
                              remote={"from": peer_id, "from_name": peer_name, "ref": ref}), True

    def begin(self, row_id: int) -> None:
        """Check, place (by THIS Accipio's rules, the user's 96A) and start what ``receive`` listed. Network I/O,
        like ``add``."""
        row = self.store.get(row_id)
        if row is not None and row.state == PROBING:
            self._start(row_id)

    def _from(self, row_id: int, peer_id: str) -> Optional[Row]:
        """The row, when it is one that Accipio sent: no Accipio may ask about or remove another's downloads."""
        row = self.store.get(row_id)
        return row if row is not None and row.remote.get("from") == peer_id else None

    def report(self, row_id: int, peer_id: str) -> Dict:
        """How a download from that Accipio went, for its list: "working", "finished" (with the NAME of the
        place it is in, as the rules call it), "failed" (with the reason) or "gone" (removed or cleared here)."""
        row = self._from(row_id, peer_id)
        if row is None:
            return {"id": row_id, "state": "gone"}
        if row.state == COMPLETE:
            return {"id": row_id, "state": "finished", "filename": row.filename,
                    "folder": self.placed_folder(row_id) if row.folder else ""}
        if row.state == ERROR:
            return {"id": row_id, "state": "failed", "filename": row.filename, "error": row.error}
        return {"id": row_id, "state": "working", "filename": row.filename}

    def heard(self, row_id: int, peer_id: str) -> None:
        """That Accipio has recorded the outcome: the row need not be kept for it any longer."""
        row = self._from(row_id, peer_id)
        if row is not None and not row.remote.get("heard"):
            self.store.update(row_id, remote={**row.remote, "heard": True})

    def remove_for(self, row_id: int, peer_id: str) -> None:
        """Remove on that Accipio (103A): an unfinished or failed download from it is stopped and removed here;
        a finished one stays, file and row, and is only marked as heard."""
        row = self._from(row_id, peer_id)
        if row is None:
            return
        if row.state == COMPLETE:
            self.heard(row_id, peer_id)
        else:
            self.remove(row_id)

    def clear_finished(self) -> int:
        return self.store.forget_finished()

    def rows(self) -> List[Row]:
        return self.store.rows()
