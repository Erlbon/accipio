"""The queue: probe -> rules -> engine -> refresh, with a fake engine for the edge cases and real aria2 at the end."""
import datetime
import os
import time
import zipfile

import pytest

from accipio.core import aria2, probe, rules, sniff
from accipio.core.queue import MAX_RESTARTS, Queue
from accipio.core.rules import Condition, Rule
from accipio.core.store import ACTIVE, COMPLETE, ERROR, PAUSED, PROBING, Store

TUE_1014 = datetime.datetime(2026, 10, 27, 10, 14)


class FakeEngine:
    """Stands in for aria2: downloads finish or fail when the test says so."""

    def __init__(self):
        self.jobs = {}
        self.counter = 0
        self.removed = []

    def add(self, url, folder, filename, headers=(), paused=False):
        self.counter += 1
        gid = f"g{self.counter}"
        self.jobs[gid] = dict(url=url, folder=folder, filename=filename, headers=list(headers),
                              state="paused" if paused else "active", done=0, total=0, code="", message="")
        return gid

    def status_of(self, gid):
        j = self.jobs[gid]
        return aria2.Status(gid, j["state"], j["total"], j["done"], 1000 if j["state"] == "active" else 0, 1,
                            os.path.join(j["folder"], j["filename"]), j["code"], j["message"])

    def all(self):
        return [self.status_of(g) for g in self.jobs]

    def pause(self, gid):
        self.jobs[gid]["state"] = "paused"

    def resume(self, gid):
        self.jobs[gid]["state"] = "active"

    def remove(self, gid):
        self.removed.append(gid)
        self.jobs.pop(gid, None)

    def restart(self, gid):
        j = self.jobs.pop(gid)
        return self.add(j["url"], j["folder"], j["filename"], j["headers"])

    # test controls
    def progress(self, gid, done, total):
        self.jobs[gid].update(done=done, total=total)

    def finish(self, gid, content=b"data"):
        j = self.jobs[gid]
        os.makedirs(j["folder"], exist_ok=True)
        with open(os.path.join(j["folder"], j["filename"]), "wb") as f:
            f.write(content)
        j.update(state="complete", done=len(content), total=len(content))

    def fail(self, gid, code, message="it broke"):
        self.jobs[gid].update(state="error", code=code, message=message)


class FakeProber:
    def __init__(self):
        self.answers = {}
        self.seen_headers = []

    def __call__(self, url, headers=None):
        self.seen_headers.append(headers)
        answer = self.answers.get(url)
        if answer is None:
            raise probe.ProbeError("the server answered 404 Not Found")
        return answer

    def serve(self, url, filename, mime="", size=None, resumable=True):
        self.answers[url] = probe.Probe(url, filename, mime, size, resumable)


@pytest.fixture
def world(tmp_path):
    base = str(tmp_path / "Downloads")
    engine, prober = FakeEngine(), FakeProber()
    store = Store(str(tmp_path / "state" / "queue.db"))
    rule_list = rules.default_rules()
    q = Queue(store, engine, lambda: rule_list, base, prober=prober, clock=lambda: TUE_1014)
    return q, engine, prober, base, rule_list


def under(base, *parts):
    return os.path.normpath(os.path.join(base, *parts))


# ---- adding ----------------------------------------------------------------------------------------------

def test_a_link_is_probed_placed_and_started(world):
    q, engine, prober, base, _ = world
    prober.serve("https://x.org/get?id=1", "Dune.epub", "application/epub+zip", 2000)
    row_id = q.add("https://x.org/get?id=1", page_url="https://x.org/books", headers={"Cookie": "a=1"})
    row = q.store.get(row_id)
    assert (row.state, row.folder, row.filename, row.rules) == (ACTIVE, under(base, "Books"), "Dune.epub", "5")
    assert row.total == 2000 and row.resumable and 'rule 5 "Ebooks and comics"' in row.why
    job = engine.jobs[row.gid]
    assert job["url"] == "https://x.org/get?id=1" and job["folder"] == row.folder and job["headers"] == ["Cookie: a=1"]
    assert prober.seen_headers[-1] == {"Cookie": "a=1"}


def test_two_downloads_of_the_same_name_never_share_a_file(world):
    q, engine, prober, base, _ = world
    prober.serve("https://a.org/f", "setup.exe")
    prober.serve("https://b.org/f", "setup.exe")
    first, second = q.add("https://a.org/f"), q.add("https://b.org/f")
    assert q.store.get(first).filename == "setup.exe" and q.store.get(second).filename == "setup (1).exe"


def test_a_file_already_on_disk_is_never_overwritten(world):
    q, engine, prober, base, _ = world
    os.makedirs(under(base, "Programs"))
    open(under(base, "Programs", "setup.exe"), "wb").close()
    prober.serve("https://a.org/f", "setup.exe")
    assert q.store.get(q.add("https://a.org/f")).filename == "setup (1).exe"


def test_the_add_box_can_choose_folder_and_name(world, tmp_path):
    q, engine, prober, base, _ = world
    prober.serve("https://a.org/f", "setup.exe")
    mine = str(tmp_path / "Mine")
    row = q.store.get(q.add("https://a.org/f", folder=mine, filename="tool.exe"))
    assert (row.folder, row.filename, row.pinned) == (os.path.normpath(mine), "tool.exe", True)
    assert "chosen by hand" in row.why


def test_preview_shows_where_it_would_go_without_adding(world):
    q, engine, prober, base, _ = world
    prober.serve("https://a.org/v", "clip.webm", "video/webm")
    found, placement = q.preview("https://a.org/v")
    assert found.filename == "clip.webm" and placement.folder == under(base, "Video")
    assert q.rows() == [] and engine.jobs == {}


def test_time_rules_use_the_moment_it_was_added(world, tmp_path):
    q, engine, prober, base, rule_list = world
    rule_list.insert(0, Rule("Office hours", [Condition("time", ["* 8-15 * * 1-5"])], folder=str(tmp_path / "Work")))
    prober.serve("https://a.org/f", "a.zip")
    assert q.store.get(q.add("https://a.org/f")).folder == os.path.normpath(str(tmp_path / "Work"))


def test_a_failed_probe_is_an_error_that_can_be_retried(world):
    q, engine, prober, base, _ = world
    row_id = q.add("https://gone.org/f")
    row = q.store.get(row_id)
    assert row.state == ERROR and "404" in row.error and engine.jobs == {}
    prober.serve("https://gone.org/f", "back.zip")
    q.retry(row_id)
    row = q.store.get(row_id)
    assert (row.state, row.filename, row.error) == (ACTIVE, "back.zip", "")


# ---- the timer -------------------------------------------------------------------------------------------

def test_progress_and_completion(world):
    q, engine, prober, base, _ = world
    prober.serve("https://a.org/f", "a.zip")
    row_id = q.add("https://a.org/f", headers={"Cookie": "secret"})
    gid = q.store.get(row_id).gid
    engine.progress(gid, 50, 100)
    assert [r.id for r in q.refresh()] == [row_id]
    assert q.store.get(row_id).done == 50 and q.speeds[row_id] == 1000
    assert q.refresh() == []                                            # nothing new: nothing reported
    engine.finish(gid, b"PK\x03\x04")
    q.refresh()
    row = q.store.get(row_id)
    assert row.state == COMPLETE and row.finished and row.headers == {}   # cookies are not kept
    assert gid in engine.removed and row_id not in q.speeds


def test_a_file_with_no_known_type_is_sniffed_renamed_and_placed_again(world):
    q, engine, prober, base, _ = world
    prober.serve("https://a.org/download", "download", "application/octet-stream")
    row_id = q.add("https://a.org/download")
    row = q.store.get(row_id)
    assert row.folder == under(base, "Other")
    engine.finish(row.gid, b"%PDF-1.7 rest of it")
    q.refresh()
    row = q.store.get(row_id)
    assert (row.folder, row.filename, row.rules) == (under(base, "Documents"), "download.pdf", "6")
    assert os.path.isfile(row.path) and not os.path.exists(under(base, "Other", "download"))
    assert "turned out to be PDF" in row.why


def test_a_pinned_folder_keeps_its_place_but_gets_the_right_extension(world, tmp_path):
    q, engine, prober, base, _ = world
    prober.serve("https://a.org/get.php", "get.php", "")
    mine = str(tmp_path / "Mine")
    row_id = q.add("https://a.org/get.php", folder=mine)
    engine.finish(q.store.get(row_id).gid, b"\x89PNG\r\n\x1a\n....")
    q.refresh()
    row = q.store.get(row_id)
    assert (row.folder, row.filename) == (os.path.normpath(mine), "get.png")


def test_a_known_extension_is_never_second_guessed(world):
    q, engine, prober, base, _ = world
    prober.serve("https://a.org/f", "notes.txt")
    row_id = q.add("https://a.org/f")
    engine.finish(q.store.get(row_id).gid, b"%PDF- but it is called txt")
    q.refresh()
    assert q.store.get(row_id).filename == "notes.txt"


def test_a_server_that_cannot_resume_is_started_over_a_few_times(world):
    q, engine, prober, base, _ = world
    prober.serve("https://a.org/f", "a.zip", resumable=False)
    row_id = q.add("https://a.org/f")
    for attempt in range(1, MAX_RESTARTS + 1):
        engine.fail(q.store.get(row_id).gid, aria2.CANNOT_RESUME)
        q.refresh()
        row = q.store.get(row_id)
        assert row.state == ACTIVE and row.restarts == attempt and "started over" in row.why
    engine.fail(q.store.get(row_id).gid, aria2.CANNOT_RESUME, "Resume not supported")
    q.refresh()
    assert q.store.get(row_id).state == ERROR and q.store.get(row_id).error == "Resume not supported"


def test_other_errors_show_the_engines_message(world):
    q, engine, prober, base, _ = world
    prober.serve("https://a.org/f", "a.zip")
    row_id = q.add("https://a.org/f")
    engine.fail(q.store.get(row_id).gid, aria2.NOT_FOUND, "Resource not found")
    q.refresh()
    assert (q.store.get(row_id).state, q.store.get(row_id).error) == (ERROR, "Resource not found")


def test_pause_resume_and_retry_with_a_new_name_when_the_old_one_was_taken(world):
    q, engine, prober, base, _ = world
    prober.serve("https://a.org/f", "a.zip")
    row_id = q.add("https://a.org/f")
    q.pause(row_id)
    assert q.store.get(row_id).state == PAUSED and engine.jobs[q.store.get(row_id).gid]["state"] == "paused"
    q.resume(row_id)
    assert q.store.get(row_id).state == ACTIVE
    row = q.store.get(row_id)
    engine.fail(row.gid, aria2.FILE_EXISTS, "File already exists")
    q.refresh()
    os.makedirs(row.folder, exist_ok=True)
    open(row.path, "wb").close()                                       # someone else's a.zip, no control file
    q.retry(row_id)
    assert os.path.exists(row.path)                                    # left alone
    assert q.store.get(row_id).filename == "a (1).zip" and q.store.get(row_id).state == ACTIVE


# ---- removing --------------------------------------------------------------------------------------------

def test_removing_an_unfinished_download_deletes_only_our_half_file(world):
    q, engine, prober, base, _ = world
    prober.serve("https://a.org/f", "a.zip")
    prober.serve("https://a.org/g", "b.zip")
    ours, theirs = q.add("https://a.org/f"), q.add("https://a.org/g")
    a, b = q.store.get(ours), q.store.get(theirs)
    os.makedirs(a.folder, exist_ok=True)
    for path in (a.path, a.path + ".aria2", b.path):                 # b has no control file: not provably ours
        open(path, "wb").close()
    q.remove(ours)
    q.remove(theirs)
    assert not os.path.exists(a.path) and not os.path.exists(a.path + ".aria2")
    assert os.path.exists(b.path) and q.rows() == []


def test_removing_a_finished_download_never_touches_the_file(world):
    q, engine, prober, base, _ = world
    prober.serve("https://a.org/f", "a.zip")
    row_id = q.add("https://a.org/f")
    engine.finish(q.store.get(row_id).gid, b"PK\x03\x04")
    q.refresh()
    path = q.store.get(row_id).path
    q.remove(row_id)
    assert os.path.isfile(path) and q.rows() == []


# ---- restarting the app ----------------------------------------------------------------------------------

def test_after_a_restart_lost_and_interrupted_rows_become_retryable_errors(tmp_path):
    store = Store(str(tmp_path / "q.db"))
    lost = store.add("https://a.org/f", state=ACTIVE, gid="gone", filename="a.zip", folder=str(tmp_path))
    probing = store.add("https://a.org/g", state=PROBING)
    Queue(store, FakeEngine(), rules.default_rules, str(tmp_path), prober=FakeProber())
    assert store.get(lost).state == ERROR and "lost" in store.get(lost).error
    assert store.get(probing).state == ERROR and "closed while checking" in store.get(probing).error


def test_finished_rows_are_forgotten_at_start_unless_history_is_on(tmp_path):
    store = Store(str(tmp_path / "q.db"))
    store.add("https://a.org/f", state=COMPLETE)
    Queue(store, FakeEngine(), rules.default_rules, str(tmp_path), prober=FakeProber(), keep_history=True)
    assert len(store.rows()) == 1
    Queue(store, FakeEngine(), rules.default_rules, str(tmp_path), prober=FakeProber())
    assert store.rows() == []


def test_the_store_survives_reopening_and_refuses_a_newer_schema(tmp_path):
    path = str(tmp_path / "q.db")
    store = Store(path)
    row_id = store.add("https://a.org/f", video={"channel": "XYZ"}, headers={"Cookie": "c"}, resumable=True)
    store.close()
    again = Store(path)
    row = again.get(row_id)
    assert row.video == {"channel": "XYZ"} and row.headers == {"Cookie": "c"} and row.resumable is True
    again._db.execute("PRAGMA user_version=99")
    again.close()
    with pytest.raises(RuntimeError, match="newer Accipio"):
        Store(path)


# ---- sniffing --------------------------------------------------------------------------------------------

def make_zip(path, names, mimetype=None):
    with zipfile.ZipFile(path, "w") as z:
        if mimetype:
            z.writestr("mimetype", mimetype)
        for n in names:
            z.writestr(n, b"x")


@pytest.mark.parametrize("content, ext", [
    (b"%PDF-1.7", "pdf"), (b"MZ\x90\x00", "exe"), (b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff\xe0", "jpg"),
    (b"7z\xbc\xaf\x27\x1c", "7z"), (b"Rar!\x1a\x07\x01", "rar"), (b"\x1f\x8b\x08", "gz"), (b"ID3\x04", "mp3"),
    (b"\xff\xfb\x90\x00", "mp3"), (b"fLaC", "flac"), (b"\x00\x00\x00\x18ftypisom", "mp4"),
    (b"\x00\x00\x00\x18ftypM4A ", "m4a"), (b"\x1a\x45\xdf\xa3....matroska", "mkv"), (b"\x1a\x45\xdf\xa3..webm", "webm"),
    (b"RIFF\x00\x00\x00\x00WAVE", "wav"), (b"just some text", None), (b"\xff\xf1\x50\x80", None),
])
def test_sniff_by_first_bytes(tmp_path, content, ext):
    path = tmp_path / "f"
    path.write_bytes(content)
    assert sniff.sniff(str(path)) == ext


def test_sniff_looks_inside_zips(tmp_path):
    cases = {"epub": (["OEBPS/a.xhtml"], "application/epub+zip"), "cbz": (["01.jpg", "02.png", "ComicInfo.xml"], None),
             "docx": (["word/document.xml", "[Content_Types].xml"], None), "zip": (["src/main.py", "README"], None)}
    for ext, (names, mimetype) in cases.items():
        path = tmp_path / ext
        make_zip(path, names, mimetype)
        assert sniff.sniff(str(path)) == ext
    assert sniff.sniff(str(tmp_path / "missing")) is None


# ---- for real, with aria2 --------------------------------------------------------------------------------

@pytest.mark.skipif(aria2.find_aria2c() is None, reason="no aria2c (run python tools/fetch_engines.py)")
def test_end_to_end_with_aria2(tmp_path, web):
    engine = aria2.Aria2(aria2.find_aria2c(), str(tmp_path / "state" / "session.txt")).start()
    try:
        q = Queue(Store(str(tmp_path / "state" / "queue.db")), engine, rules.default_rules, str(tmp_path / "Downloads"))
        ids = [q.add(web + "/redirect"), q.add(web + "/get.php?id=7"), q.add(web + "/nothing")]
        deadline = time.monotonic() + 20
        while any(r.state in (ACTIVE, PROBING) for r in q.rows()) and time.monotonic() < deadline:
            q.refresh()
            time.sleep(0.05)
        zipped, php, missing = (q.store.get(i) for i in ids)
        assert (zipped.state, zipped.path) == (COMPLETE, os.path.join(str(tmp_path / "Downloads" / "Archives"), "real.zip"))
        assert os.path.getsize(zipped.path) == 1024 * 1024
        assert (php.state, php.filename) == (COMPLETE, "get.zip") and os.path.isfile(php.path)
        assert missing.state == ERROR and "404" in missing.error
    finally:
        engine.kill()
