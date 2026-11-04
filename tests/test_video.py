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
    def __init__(self):
        self.answers = {}

    def __call__(self, url, headers=None, playlist=""):
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


def test_a_playlist_becomes_one_row_per_video(world):
    q, direct, videos, prober, vprober, base, rule_list = world
    rule_list.insert(0, rules.Rule("Lists", [rules.Condition("source", ["video"])], subfolder="Lists/{playlist}"))
    links = [f"https://www.youtube.com/watch?v={n}" for n in range(3)]
    vprober.answers["https://www.youtube.com/playlist?list=P"] = VideoProbe("p", "My list", playlist="My list", entries=links)
    for n, link in enumerate(links):
        vprober.answers[link] = VideoProbe(link, f"Part {n}", "mp4", uploader="xyz")
    q.add("https://www.youtube.com/playlist?list=P")
    rows = q.rows()
    assert [r.filename for r in rows] == ["Part 0.mp4", "Part 1.mp4", "Part 2.mp4"]
    assert {r.folder for r in rows} == {os.path.join(base, "Lists", "My list")}
    assert all(r.state == ACTIVE and r.video["playlist"] == "My list" for r in rows)


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

    def broken(url, headers=None, playlist=""):
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
