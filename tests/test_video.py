"""Video sites: format choice, site detection, the yt-dlp engine (offline, against the local server) and the
queue's routing (with fakes). No test reaches a real video site."""
import os
import threading
import time

import pytest

from accipio.core import rules, video
from tests import samples
from accipio.core.queue import Queue
from accipio.core.store import ACTIVE, COMPLETE, ERROR, PAUSED, Store
from accipio.core.video import VideoEngine, VideoProbe
from tests.test_queue import TUE_1014, FakeEngine, FakeProber

pytest.importorskip("yt_dlp")


# ---- choosing formats and recognising sites --------------------------------------------------------------

def test_format_choice_with_and_without_ffmpeg():
    best = video.format_options("best", "ffmpeg.exe")
    assert best["format"] == "bv*+ba/b/b" and best["merge_output_format"] == "mp4/mkv"
    assert best["format_sort"] == ["res", "vcodec:h264", "ext:mp4:m4a"]          # H.264 plays everywhere
    assert video.format_options("1080", "ffmpeg.exe")["format"] == "bv*[height<=1080]+ba/b[height<=1080]/b"
    assert video.format_options("720", None) == {"format": "b[height<=720]/b"}       # no ffmpeg: no merging
    assert video.format_options("audio", "ffmpeg.exe")["postprocessors"][0]["preferredcodec"] == "mp3"
    assert "postprocessors" not in video.format_options("audio", None)


@pytest.mark.parametrize("url, answer", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", True),
    ("https://youtu.be/dQw4w9WgXcQ", True),
    ("https://vimeo.com/76979871", True),
    ("https://example.com/files/setup.exe", False),          # the catch-all extractor does not count
    ("ftp://example.com/a.iso", False),
])
def test_video_sites_are_recognised_without_asking_the_internet(url, answer):
    assert video.is_video_site(url) is answer


def test_a_probe_names_the_file_from_the_title():
    p = VideoProbe("u", title="Talk: part 3/4?", ext="mkv", channel="XYZ Talks", uploader="xyz")
    assert p.filename == "Talk_ part 3_4_.mkv"
    assert p.fields == {"channel": "XYZ Talks", "uploader": "xyz", "title": "Talk: part 3/4?"}
    assert VideoProbe("u").filename == "video.mp4"


# ---- the engine, with a stand-in for yt-dlp --------------------------------------------------------------

class Gate:
    """A stand-in downloader that waits until the test lets it go, reporting progress through the hooks."""

    def __init__(self):
        self.go = threading.Event()
        self.started = []
        self.fail = None

    def __call__(self, options, url):
        self.started.append(url)
        hook = options["progress_hooks"][0]
        target = options["outtmpl"].replace("%(ext)s", "mp4")
        hook({"status": "downloading", "filename": target, "tmpfilename": target + ".part",
              "downloaded_bytes": 10, "total_bytes": 100, "speed": 50})
        open(target + ".part", "wb").close()
        while not self.go.wait(0.02):
            hook({"status": "downloading", "filename": target, "tmpfilename": target + ".part",
                  "downloaded_bytes": 10, "total_bytes": 100, "speed": 50})
        if self.fail:
            raise RuntimeError(self.fail)
        os.replace(target + ".part", target)
        hook({"status": "finished", "filename": target, "downloaded_bytes": 100, "total_bytes": 100})
        options["post_hooks"][0](target)


def wait_state(engine, gid, states, timeout=5):
    deadline = time.monotonic() + timeout
    while engine.status(gid).state not in states:
        assert time.monotonic() < deadline, f"still {engine.status(gid).state}"
        time.sleep(0.01)
    return engine.status(gid)


def test_the_engine_runs_reports_and_finishes(tmp_path):
    gate = Gate()
    engine = VideoEngine(downloader=gate)
    gid = engine.add("https://v/1", str(tmp_path), "Talk.mp4")
    s = wait_state(engine, gid, {"active"})
    assert s.total == 100 and s.done == 10
    gate.go.set()
    s = wait_state(engine, gid, {"complete"})
    assert s.path == os.path.join(str(tmp_path), "Talk.mp4") and os.path.isfile(s.path) and s.speed == 0


def test_only_max_downloads_run_at_once(tmp_path):
    gate = Gate()
    engine = VideoEngine(downloader=gate, max_downloads=1)
    first = engine.add("https://v/1", str(tmp_path), "a.mp4")
    second = engine.add("https://v/2", str(tmp_path), "b.mp4")
    wait_state(engine, first, {"active"})
    time.sleep(0.1)
    assert engine.status(second).state == "waiting" and gate.started == ["https://v/1"]
    engine.set_limits(max_downloads=2)
    wait_state(engine, second, {"active"})
    gate.go.set()
    wait_state(engine, second, {"complete"})


class Clock:
    """A clock that only moves when the engine sleeps."""

    def __init__(self):
        self.now = 100.0
        self.slept = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept += seconds
        self.now += seconds


def test_the_speed_limit_holds_for_all_videos_together(tmp_path):
    """Before 2026-11-05 each video got the whole limit (yt-dlp's ratelimit is per download)."""
    clock = Clock()
    engine = VideoEngine(downloader=Gate(), clock=clock, sleep=clock.sleep)
    engine.set_limits(speed_limit=1000)
    first = video._Job("v1", "u1", str(tmp_path), "a.mp4", [])
    second = video._Job("v2", "u2", str(tmp_path), "b.mp4", [])
    for done in (1000, 2000, 3000):          # the first report is where the part stands (a resume may start far in)
        for job in (first, second):
            engine._progress(job, {"status": "downloading", "filename": job.filename, "downloaded_bytes": done})
    assert clock.slept == pytest.approx(4.0)                     # 4000 new bytes at 1000 B/s, shared by the two
    assert "ratelimit" not in engine._options(first)             # no per-video limit on top


def test_no_limit_means_no_waiting_and_each_part_counts_its_own_bytes(tmp_path):
    clock = Clock()
    engine = VideoEngine(downloader=Gate(), clock=clock, sleep=clock.sleep)
    job = video._Job("v1", "u1", str(tmp_path), "a.mp4", [])
    for done in (0, 5000):
        engine._progress(job, {"status": "downloading", "filename": "a.f137.mp4", "downloaded_bytes": done})
    assert clock.slept == 0
    engine.set_limits(speed_limit=1000)
    for done in (0, 500):
        engine._progress(job, {"status": "downloading", "filename": "a.f140.m4a", "downloaded_bytes": done})
    assert clock.slept == pytest.approx(0.5)                    # the sound part's 500 bytes, not 500 - 5000


def test_pause_keeps_the_part_and_resume_starts_again(tmp_path):
    gate = Gate()
    engine = VideoEngine(downloader=gate)
    gid = engine.add("https://v/1", str(tmp_path), "a.mp4")
    wait_state(engine, gid, {"active"})
    engine.pause(gid)
    wait_state(engine, gid, {"paused"})
    assert os.path.isfile(os.path.join(str(tmp_path), "a.mp4.part"))
    engine.resume(gid)
    wait_state(engine, gid, {"active"})
    gate.go.set()
    wait_state(engine, gid, {"complete"})
    assert gate.started == ["https://v/1", "https://v/1"]


def test_a_resume_right_after_pause_is_not_lost(tmp_path):
    """Resume before the download has noticed the pause: it must go on, not end up paused."""
    gate = Gate()
    engine = VideoEngine(downloader=gate)
    gid = engine.add("https://v/1", str(tmp_path), "a.mp4")
    wait_state(engine, gid, {"active"})
    engine.pause(gid)
    engine.resume(gid)
    gate.go.set()
    assert wait_state(engine, gid, {"complete", "paused"}).state == "complete"


class SlowToLetGo(threading.Condition):
    """Holds a video thread back for a moment each time it takes the lock after its job was marked paused: the gap
    a busy PC leaves between "paused" and the thread's end."""

    def __init__(self, jobs):
        super().__init__()
        self.jobs = jobs

    def __enter__(self):
        if threading.current_thread().name.startswith("video-") and any(j.state == "paused" for j in self.jobs()):
            time.sleep(0.3)
        return super().__enter__()


def test_a_resume_while_the_paused_thread_ends_starts_it_again(tmp_path):
    """2026-11-07: a resume in that gap marked the video active and started nothing: "still active" for ever
    (test_real_yt_dlp_pauses_and_resumes failed under load)."""
    gate = Gate()
    engine = VideoEngine(downloader=gate)
    engine._cond = SlowToLetGo(lambda: list(engine._jobs.values()))
    gid = engine.add("https://v/1", str(tmp_path), "a.mp4")
    wait_state(engine, gid, {"active"})
    engine.pause(gid)
    wait_state(engine, gid, {"paused"})
    engine.resume(gid)
    gate.go.set()
    assert wait_state(engine, gid, {"complete"}).state == "complete"
    assert gate.started == ["https://v/1", "https://v/1"]


def test_remove_deletes_only_the_half_finished_parts(tmp_path):
    gate = Gate()
    engine = VideoEngine(downloader=gate)
    gid = engine.add("https://v/1", str(tmp_path), "a.mp4")
    wait_state(engine, gid, {"active"})
    engine.remove(gid)
    assert not os.path.exists(os.path.join(str(tmp_path), "a.mp4.part")) and engine.all() == []


def test_a_failure_shows_the_message_and_restart_tries_again(tmp_path):
    gate = Gate()
    gate.fail = "ERROR: [youtube] abc: Video unavailable\nmore lines"
    gate.go.set()
    engine = VideoEngine(downloader=gate)
    gid = engine.add("https://v/1", str(tmp_path), "a.mp4")
    s = wait_state(engine, gid, {"error"})
    assert s.error_message == "[youtube] abc: Video unavailable" and s.error_code == "video"
    gate.fail = None
    again = engine.restart(gid)
    assert wait_state(engine, again, {"complete"}).state == "complete"


def test_added_paused_waits_for_resume(tmp_path):
    gate = Gate()
    gate.go.set()
    engine = VideoEngine(downloader=gate)
    gid = engine.add("https://v/1", str(tmp_path), "a.mp4", paused=True)
    time.sleep(0.1)
    assert engine.status(gid).state == "paused" and gate.started == []
    engine.resume(gid)
    wait_state(engine, gid, {"complete"})


# ---- the engine with real yt-dlp, offline ----------------------------------------------------------------

def test_real_yt_dlp_downloads_a_plain_video_link(tmp_path, web):
    engine = VideoEngine(ffmpeg=None)
    gid = engine.add(web + "/media/clip.mp4", str(tmp_path / "Video"), "My clip.mp4")
    s = wait_state(engine, gid, {"complete", "error"}, timeout=30)
    assert s.state == "complete", s.error_message
    assert os.path.getsize(tmp_path / "Video" / "My clip.mp4") == 256 * 1024


def test_real_yt_dlp_pauses_and_resumes(tmp_path, web):
    engine = VideoEngine(ffmpeg=None)
    gid = engine.add(web + "/media/slow.mp4", str(tmp_path), "slow.mp4")
    deadline = time.monotonic() + 15
    while engine.status(gid).done < 100_000:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    engine.pause(gid)
    paused = wait_state(engine, gid, {"paused"}, timeout=15)
    engine.resume(gid)
    s = wait_state(engine, gid, {"complete", "error"}, timeout=40)
    assert s.state == "complete", s.error_message
    assert os.path.getsize(tmp_path / "slow.mp4") == 4 * 1024 * 1024 and paused.done > 0


def test_real_probe_of_a_plain_video_link(web):
    p = video.probe_video(web + "/media/clip.mp4")
    assert (p.title, p.ext, p.entries) == ("clip", "mp4", None)


# ---- the queue routes video links ------------------------------------------------------------------------

class FakeVideoProber:
    """``answers``: what a link is on its own (yt-dlp's noplaylist); ``lists``: what it is when the whole list is
    asked for (a video link that carries a list)."""

    def __init__(self):
        self.answers = {}
        self.lists = {}
        self.list_calls = []

    def __call__(self, url, headers=None, playlist="", whole_list=False):
        if whole_list:
            self.list_calls.append(url)
            if url in self.lists:
                return self.lists[url]
        answer = self.answers.get(url)
        if answer is None:
            return None
        if answer.entries is None and playlist:
            answer = VideoProbe(answer.url, answer.title, answer.ext, answer.size, answer.channel, answer.uploader, playlist)
        return answer


@pytest.fixture
def world(tmp_path):
    base = str(tmp_path / "Downloads")
    direct, videos, prober, vprober = FakeEngine(), FakeEngine(), FakeProber(), FakeVideoProber()
    rule_list = samples.sorting_rules()
    q = Queue(Store(str(tmp_path / "q.db")), direct, lambda: rule_list, base, prober=prober, clock=lambda: TUE_1014,
              video_engine=videos, video_prober=vprober, library_set=samples.sorting_library)
    q.in_background = lambda fn, *args: fn(*args)            # the list lookup, in place
    return q, direct, videos, prober, vprober, base, rule_list


def test_a_video_link_goes_to_the_video_engine_with_its_details(world):
    q, direct, videos, prober, vprober, base, _ = world
    url = "https://www.youtube.com/watch?v=1"
    vprober.answers[url] = VideoProbe(url, "Talk 3", "mp4", 5000, channel="XYZ Talks", uploader="xyz")
    row = q.store.get(q.add(url))
    assert (row.state, row.source, row.folder, row.filename) == (ACTIVE, "video", os.path.join(base, "Video", "xyz"), "Talk 3.mp4")
    assert row.video == {"channel": "XYZ Talks", "uploader": "xyz", "title": "Talk 3"} and row.rules == "1"
    assert row.gid in videos.jobs and direct.jobs == {}


def test_rules_can_use_the_channel(world, tmp_path):
    q, direct, videos, prober, vprober, base, rule_list = world
    rule_list.insert(0, rules.Rule("XYZ", [rules.Condition("video", ["xyz*"], where="channel")],
                                   folder=str(tmp_path / "XYZ" / "{yyyy}")))
    url = "https://www.youtube.com/watch?v=1"
    vprober.answers[url] = VideoProbe(url, "Talk", "mp4", channel="XYZ Talks")
    assert q.store.get(q.add(url)).folder == os.path.join(str(tmp_path), "XYZ", "2026")


PLAYLIST = "https://www.youtube.com/playlist?list=P"


def serve_list(vprober, count=3):
    links = [f"https://www.youtube.com/watch?v={n}" for n in range(count)]
    vprober.answers[PLAYLIST] = VideoProbe("p", "My list", playlist="My list", entries=links)
    for n, link in enumerate(links):
        vprober.answers[link] = VideoProbe(link, f"Part {n}", "mp4", uploader="xyz")
    return links


def test_a_playlist_link_waits_for_a_yes_then_becomes_one_row_per_video(world):
    q, direct, videos, prober, vprober, base, rule_list = world
    rule_list.insert(0, rules.Rule("Lists", [rules.Condition("source", ["video"])], subfolder="Lists/{playlist}"))
    serve_list(vprober)
    row_id = q.add(PLAYLIST)
    [row] = q.rows()
    assert row.state == PAUSED and videos.jobs == {}                        # nothing downloads before the yes
    assert {k: row.video["ask"][k] for k in ("kind", "count", "title", "mix")} == \
        {"kind": "list", "count": 3, "title": "My list", "mix": False}
    q.resume(row_id)                                                         # Resume is not a yes
    assert videos.jobs == {}
    q.answer_playlist(row_id, take_all=True)
    rows = q.rows()
    assert [r.filename for r in rows] == ["Part 0.mp4", "Part 1.mp4", "Part 2.mp4"]
    assert {r.folder for r in rows} == {os.path.join(base, "Lists", "My list")}
    assert all(r.state == ACTIVE and r.video["playlist"] == "My list" and "ask" not in r.video for r in rows)


def test_no_to_a_playlist_link_adds_nothing(world):
    q, direct, videos, prober, vprober, base, _ = world
    serve_list(vprober)
    q.answer_playlist(q.add(PLAYLIST), take_all=False)
    assert q.rows() == [] and videos.jobs == {}


def test_a_playlist_ticked_in_the_add_box_needs_no_second_yes(world):
    q, direct, videos, prober, vprober, base, _ = world
    serve_list(vprober)
    q.add(PLAYLIST, whole_list=True)
    assert [r.state for r in q.rows()] == [ACTIVE] * 3


def test_a_video_from_a_mix_is_just_that_video_with_no_question(world):
    """A YouTube Mix is a radio list YouTube keeps making (371 to 1,954 videos for one song): not offered (59B)."""
    q, direct, videos, prober, vprober, base, _ = world
    url = "https://www.youtube.com/watch?v=18VpnaSGHto&list=RD18VpnaSGHto&start_radio=1&pp=oAcB"
    vprober.answers[url] = VideoProbe(url, "Song", "mp4", uploader="xyz")
    vprober.lists[url] = VideoProbe(url, "Mix - Song", playlist="Mix - Song", entries=["a", "b", "c"])
    row = q.store.get(q.add(url))
    assert row.state == ACTIVE and row.filename == "Song.mp4" and len(videos.jobs) == 1
    assert "ask" not in row.video and vprober.list_calls == []            # not even looked up


def test_yes_to_a_videos_playlist_adds_the_others_once(world):
    q, direct, videos, prober, vprober, base, _ = world
    url = "https://www.youtube.com/watch?v=18VpnaSGHto&list=PLtalks"
    vprober.answers[url] = VideoProbe(url, "Song", "mp4", uploader="xyz")
    listed = [f"https://www.youtube.com/watch?v={v}" for v in ("18VpnaSGHto", "b", "c")]
    vprober.lists[url] = VideoProbe(url, "Talks", playlist="Talks", entries=listed)
    for link in listed[1:]:
        vprober.answers[link] = VideoProbe(link, link[-1], "mp4")
    row_id = q.add(url)
    ask = q.store.get(row_id).video["ask"]
    assert (ask["kind"], ask["count"], ask["mix"]) == ("rest", 3, False)
    q.answer_playlist(row_id, take_all=True)
    assert sorted(r.filename for r in q.rows()) == ["Song.mp4", "b.mp4", "c.mp4"]      # the song is not fetched twice
    assert "ask" not in q.store.get(row_id).video and q.store.get(row_id).video["title"] == "Song"


def test_a_slow_list_lookup_does_not_hold_up_the_queue(world):
    q, direct, videos, prober, vprober, base, _ = world
    fresh = Queue(Store(os.path.join(os.path.dirname(base), "fresh.db")), FakeEngine(), list, base)
    q.in_background = fresh.in_background                     # the real one: a thread beside the queue's worker
    url = "https://www.youtube.com/watch?v=1&list=PLbig"
    vprober.answers[url] = VideoProbe(url, "Talk", "mp4")
    release, looked_up = threading.Event(), threading.Event()
    real = vprober.__call__

    def slow(link, headers=None, playlist="", whole_list=False):
        if whole_list:
            release.wait(5)
            looked_up.set()
            return VideoProbe(link, "Big", playlist="Big", entries=[f"https://www.youtube.com/watch?v={n}" for n in range(9)])
        return real(link, headers, playlist)
    q.video_prober = slow
    row_id = q.add(url)                                        # returns while the list is still being looked up
    assert q.store.get(row_id).state == ACTIVE and "ask" not in q.store.get(row_id).video
    release.set()
    assert looked_up.wait(5)
    deadline = time.monotonic() + 5
    while "ask" not in q.store.get(row_id).video:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert q.store.get(row_id).video["ask"]["count"] == 9


def test_just_the_video_keeps_one_row_and_never_asking_skips_the_list_check(world):
    q, direct, videos, prober, vprober, base, _ = world
    url = "https://www.youtube.com/watch?v=1&list=PLx"
    vprober.answers[url] = VideoProbe(url, "Talk", "mp4")
    vprober.lists[url] = VideoProbe(url, "Talks", playlist="Talks", entries=["https://www.youtube.com/watch?v=1",
                                                                             "https://www.youtube.com/watch?v=2"])
    row_id = q.add(url)
    q.answer_playlist(row_id, take_all=False)
    assert len(q.rows()) == 1 and "ask" not in q.store.get(row_id).video
    q.ask_playlists = False
    vprober.list_calls.clear()
    q.add(url)
    assert vprober.list_calls == [] and all("ask" not in r.video for r in q.rows())


def test_a_waiting_playlist_survives_a_restart(tmp_path):
    store = Store(str(tmp_path / "q.db"))
    ask = {"kind": "list", "count": 2, "title": "L", "mix": False, "links": ["https://y/1", "https://y/2"]}
    waiting = store.add(PLAYLIST, state=PAUSED, source="video", video={"ask": ask})
    Queue(store, FakeEngine(), rules.default_rules, str(tmp_path), prober=FakeProber(),
          video_engine=FakeEngine(), video_prober=FakeVideoProber())
    assert store.get(waiting).state == PAUSED and store.get(waiting).video["ask"] == ask


def test_the_probe_takes_only_the_video_unless_the_whole_list_is_asked_for(monkeypatch):
    import yt_dlp
    seen = []

    class Recorder:
        def __init__(self, options):
            seen.append(options.get("noplaylist"))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False):
            return {"title": "t", "ext": "mp4"}
    monkeypatch.setattr(yt_dlp, "YoutubeDL", Recorder)
    video.probe_video("https://www.youtube.com/watch?v=1&list=RD1")
    video.probe_video("https://www.youtube.com/watch?v=1&list=RD1", whole_list=True)
    assert seen == [True, False]


@pytest.mark.parametrize("url, carries", [
    ("https://www.youtube.com/watch?v=18VpnaSGHto&list=RD18VpnaSGHto&start_radio=1", True),
    ("https://www.youtube.com/watch?v=1", False),
    ("https://www.youtube.com/playlist?list=PL1", False),               # the list itself, not a video in one
    ("https://youtu.be/1?list=PL1", True),
])
def test_a_video_link_that_carries_a_list_is_recognised(url, carries):
    assert video.carries_a_list(url) is carries


def test_a_finished_video_takes_the_engines_final_name(world):
    q, direct, videos, prober, vprober, base, _ = world
    url = "https://www.youtube.com/watch?v=1"
    vprober.answers[url] = VideoProbe(url, "Talk", "mp4", uploader="xyz")
    row = q.store.get(q.add(url))
    job = videos.jobs[row.gid]
    job["filename"] = "Talk.mkv"                               # yt-dlp had to fall back to mkv
    videos.finish(row.gid, b"\x1a\x45\xdf\xa3")
    q.refresh()
    done = q.store.get(row.id)
    assert (done.state, done.filename) == (COMPLETE, "Talk.mkv") and os.path.isfile(done.path)


def test_pause_resume_remove_use_the_rows_own_engine(world):
    q, direct, videos, prober, vprober, base, _ = world
    url = "https://www.youtube.com/watch?v=1"
    vprober.answers[url] = VideoProbe(url, "Talk", "mp4")
    row_id = q.add(url)
    gid = q.store.get(row_id).gid
    q.pause(row_id)
    assert videos.jobs[gid]["state"] == "paused" and q.store.get(row_id).state == PAUSED
    q.resume(row_id)
    q.remove(row_id)
    assert gid in videos.removed and direct.removed == []


def test_a_site_error_is_shown(world):
    q, direct, videos, prober, vprober, base, _ = world
    from accipio.core.probe import ProbeError

    def broken(url, headers=None, playlist="", whole_list=False):
        raise ProbeError("[youtube] 1: Video unavailable")
    q.video_prober = broken
    row = q.store.get(q.add("https://www.youtube.com/watch?v=1"))
    assert row.state == ERROR and "unavailable" in row.error


def test_video_downloads_start_again_after_a_restart(tmp_path):
    store = Store(str(tmp_path / "q.db"))
    running = store.add("https://y/1", state=ACTIVE, source="video", gid="old", folder=str(tmp_path), filename="a.mp4")
    paused = store.add("https://y/2", state=PAUSED, source="video", gid="old2", folder=str(tmp_path), filename="b.mp4")
    videos = FakeEngine()
    Queue(store, FakeEngine(), rules.default_rules, str(tmp_path), prober=FakeProber(),
          video_engine=videos, video_prober=FakeVideoProber())
    assert store.get(running).state == ACTIVE and store.get(running).gid in videos.jobs
    assert store.get(paused).gid in videos.jobs and store.get(paused).state == PAUSED


def test_preview_knows_videos_and_playlists(world):
    q, direct, videos, prober, vprober, base, _ = world
    url = "https://www.youtube.com/watch?v=1"
    vprober.answers[url] = VideoProbe(url, "Talk", "mp4", 900, uploader="xyz")
    shown = q.preview(url)
    assert (shown.kind, shown.filename, shown.size) == ("video", "Talk.mp4", 900)
    assert shown.placement.folder == os.path.join(base, "Video", "xyz")
    vprober.answers["https://www.youtube.com/playlist?list=P"] = VideoProbe("p", "L", playlist="L", entries=["a", "b"])
    shown = q.preview("https://www.youtube.com/playlist?list=P")
    assert (shown.kind, shown.count, shown.filename) == ("playlist", 2, "Playlist: 2 videos")
    assert q.rows() == [] and videos.jobs == {}
