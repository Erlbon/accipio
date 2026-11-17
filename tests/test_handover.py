"""One Accipio hands its downloads to another: two queues with stand-in engines and probers, two link nodes with a
stand-in key store, and a real listener on 127.0.0.1 (port 0). Everything under tmp_path; no test here touches
the real keyring, any network but this PC's own loopback, or a torrent program."""
import datetime
import os
import sqlite3
import types

import pytest

from accipio.core import handover, link, notices, rules, taskbar, torrents
from accipio.core.queue import Queue
from accipio.core.rules import Condition, Folder, Library, Rule
from accipio.core.store import (ACTIVE, COMPLETE, ERROR, OWE_REMOVE, SENT, UNFINISHED, WAITING, Store)
from tests import samples
from tests.test_link import MemoryKeys
from tests.test_queue import FakeEngine, FakeProber

pytestmark = pytest.mark.skipif(not link.available(), reason="this Python's ssl module has no pre-shared keys")

NOON = datetime.datetime(2026, 11, 17, 12, 0)
COMIC = "https://x.org/get/Saga%2001.cbz"
MAGNET = "magnet:?xt=urn:btih:c12fe1c06bba254a9dc9f519b335aa7c1367a88a&dn=Ubuntu+26.10+desktop"


@pytest.fixture(autouse=True)
def never_the_real_keyring(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a hand-over test reached for the real keyring")
    for name in ("get", "set", "delete"):
        monkeypatch.setattr(link.SystemKeys, name, refuse)


class Ticks:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def in_place(fn, *args):
    fn(*args)


def a_queue(folder, rule_list, library):
    engine, prober = FakeEngine(), FakeProber()
    queue = Queue(Store(os.path.join(folder, "state", "queue.db")), engine, lambda: rule_list,
                  os.path.join(folder, "Downloads"), prober=prober, clock=lambda: NOON, library_set=lambda: library)
    queue.in_background = in_place                        # the pump runs where it is asked for: nothing to wait for
    return queue, engine, prober


@pytest.fixture
def two(tmp_path):
    """A sender ("Laptop") paired with a listening collector ("Collector"). On the sender, comics and torrents go
    to the place "Collector" (wait for it, cookies along); everything else is sorted as in ``samples``."""
    w = types.SimpleNamespace()
    # the collector: the plain sorting setup, its own rules
    w.c_node = link.Node(str(tmp_path / "collector" / "config"), MemoryKeys())
    w.c_node.rename("Collector")
    w.c_rules, w.c_library = samples.sorting_rules(), samples.sorting_library()
    w.c, w.c_engine, w.c_prober = a_queue(str(tmp_path / "collector"), w.c_rules, w.c_library)
    w.c_base = w.c.base_folder
    w.handler = handover.Collector(w.c)
    w.listener = link.Listener(w.c_node, w.handler, ["127.0.0.1"], port=0).start()
    w.port = w.listener.bound[0][1]
    # the sender
    w.s_node = link.Node(str(tmp_path / "laptop" / "config"), MemoryKeys())
    w.s_node.rename("Laptop")
    w.peer = link.pair(w.s_node, "127.0.0.1", w.port, w.c_node.begin_pairing())
    w.place = Folder("Collector", kind=rules.PLACE_ACCIPIO, peer=w.peer.id)
    w.s_library = samples.sorting_library()
    w.s_library.folders.append(w.place)
    w.s_rules = [Rule("Comics to the collector", [Condition("type", ["Comics"])], target="Collector"),
                 Rule("Torrents to the collector", [Condition("type", ["torrent"])], target="Collector")] \
        + samples.sorting_rules()
    w.s, w.s_engine, w.s_prober = a_queue(str(tmp_path / "laptop"), w.s_rules, w.s_library)
    w.s_base = w.s.base_folder
    w.ticks = Ticks()
    w.sender = handover.Sender(w.s, w.s_node, timeout=3, clock=w.ticks)
    for prober in (w.s_prober, w.c_prober):
        prober.serve(COMIC, "Saga 01.cbz", "application/vnd.comicbook+zip", 5000)

    def away():
        w.listener.stop()

    def back():
        w.listener = link.Listener(w.c_node, w.handler, ["127.0.0.1"], port=w.port).start()
        assert w.listener.bound, w.listener.problems
    w.away, w.back = away, back
    yield w
    w.listener.stop()
    w.s.store.close()
    w.c.store.close()


def under(base, *parts):
    return os.path.normpath(os.path.join(base, *parts))


def only(queue):
    rows = queue.rows()
    assert len(rows) == 1, rows
    return rows[0]


# ---- places and rules ----------------------------------------------------------------------------------------

def test_a_place_is_a_folder_unless_it_says_another_accipio_and_older_files_still_load():
    old = '{"format": 3, "rules": [], "folders": [{"name": "Comics", "path": "%default%/Comics"}]}'
    comics = rules.library_from_json(old).folders[0]
    assert (comics.kind, comics.remote, comics.peer, comics.cookies, comics.away) == ("folder", False, "", True, "wait")
    library = Library([comics, Folder("Collector", kind="accipio", peer="abc", cookies=False, away="here")], [], [])
    again = rules.library_from_json(rules.rules_to_json([], library)).folders
    assert again == library.folders and again[1].remote and again[1].path == ""
    # a file from a newer Accipio, with a key this one does not know: read, the key skipped
    newer = '{"format": 3, "rules": [], "folders": [{"name": "C", "kind": "accipio", "peer": "abc", "colour": "red"}]}'
    assert rules.library_from_json(newer).folders[0] == Folder("C", kind="accipio", peer="abc")


def test_the_rules_can_end_at_another_accipio_and_download_here_passes_those_rules_over():
    library = samples.sorting_library()
    library.folders.append(Folder("Collector", kind="accipio", peer="abc"))
    rule_list = [Rule("Comics away", [Condition("type", ["Comics"])], target="Collector")] + samples.sorting_rules()
    comic = rules.Download(url=COMIC, filename="Saga 01.cbz", when=NOON)
    sent = rules.place(rule_list, comic, "D:\\Get", library)
    assert sent.remote is library.folders[-1] and sent.rule_numbers == "1"
    assert 'rule 1 "Comics away": sent to Collector (another Accipio)' == sent.why()
    here = rules.place(rule_list, comic, "D:\\Get", library, local_only=True)             # 102A: the next rule that fits
    assert here.remote is None and here.rule_numbers == "7" and here.folder == os.path.normpath("D:\\Get\\Comics")
    alone = rules.place(rule_list[:1], comic, "D:\\Get", library, local_only=True)          # else the Default folder
    assert alone.remote is None and alone.steps == [] and alone.folder == os.path.normpath("D:\\Get")
    book = rules.place(rule_list, rules.Download(url="https://x.org/a.epub", filename="a.epub", when=NOON), "D:\\Get", library)
    assert book.remote is None and book.rule_numbers == "6"                                # other files: as before


def test_the_editor_is_told_about_a_place_with_no_accipio_chosen():
    library = Library([Folder("Collector", kind="accipio")], rules.default_groups(), [])
    assert any("none is chosen" in p for p in rules.validate(Rule("r", target="Collector"), library))
    library.folders[0].peer = "abc"
    assert rules.validate(Rule("r", target="Collector"), library) == []


def test_a_queue_file_from_before_gets_the_new_column_and_the_new_states_count_as_nothing_running(tmp_path):
    path = str(tmp_path / "queue.db")
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE downloads (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, page_url TEXT NOT NULL "
               "DEFAULT '', filename TEXT NOT NULL DEFAULT '', folder TEXT NOT NULL DEFAULT '', mime TEXT NOT NULL "
               "DEFAULT '', size INTEGER, source TEXT NOT NULL DEFAULT 'direct', video TEXT NOT NULL DEFAULT '{}', "
               "headers TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL, gid TEXT NOT NULL DEFAULT '', done INTEGER NOT "
               "NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0, resumable INTEGER NOT NULL DEFAULT 0, restarts INTEGER "
               "NOT NULL DEFAULT 0, pinned INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', rules TEXT NOT "
               "NULL DEFAULT '', why TEXT NOT NULL DEFAULT '', added TEXT NOT NULL, finished TEXT NOT NULL DEFAULT '', "
               "unpack TEXT NOT NULL DEFAULT '{}')")
    db.execute("INSERT INTO downloads (url, state, added) VALUES ('https://a.org/f', 'complete', '2026-11-01T10:00:00')")
    db.execute("PRAGMA user_version=2")
    db.commit()
    db.close()
    store = Store(path)
    assert store.get(1).remote == {} and store.owed() == []
    row_id = store.add("https://a.org/g", state=WAITING, remote={"peer": "abc", "place": "Collector"})
    assert store.get(row_id).remote["place"] == "Collector"
    assert WAITING not in UNFINISHED and SENT not in UNFINISHED
    assert taskbar.look(store.rows(), 0).mode == taskbar.NONE                 # no endless progress on the taskbar
    assert store.get(row_id).path == ""                                       # nothing of it is on this PC
    store.close()


def test_the_new_messages_are_checked_before_a_handler_sees_them():
    assert link.problem_with(link.add(COMIC, ref="7-ab")) == "" and link.add(COMIC, ref="7-ab")["ref"] == "7-ab"
    assert link.problem_with(link.status([1], heard=[2])) == "" and "heard" not in link.status([1])
    assert link.problem_with(link.remove([1, 2])) == ""
    assert "ids" in link.problem_with({"v": 1, "type": "remove", "ids": ["1"]})
    assert "heard" in link.problem_with({"v": 1, "type": "status", "ids": [], "heard": [True]})
    assert "ref" in link.problem_with({"v": 1, "type": "add", "url": COMIC, "ref": 7})


# ---- handing over --------------------------------------------------------------------------------------------

def test_a_download_for_the_collector_is_sent_and_the_collectors_own_rules_place_it(two):
    w = two
    row_id = w.s.add(COMIC, page_url="https://x.org/comics", headers={"Cookie": "login=1", "Referer": "https://x.org/"})
    row = w.s.store.get(row_id)
    assert (row.state, row.filename, row.folder, row.rules, row.gid) == (SENT, "Saga 01.cbz", "", "1", "")
    assert handover.status_text(row) == "Sent to Collector" and "sent to Collector (another Accipio)" in row.why
    assert w.s_engine.jobs == {}                                              # nothing is downloaded on the sender
    there = only(w.c)
    assert row.remote["id"] == there.id
    assert (there.state, there.folder, there.rules, there.page_url) == (ACTIVE, under(w.c_base, "Comics"), "6",
                                                                        "https://x.org/comics")
    assert "Sent from Laptop." in there.why and there.remote["from"] == w.s_node.id
    job = w.c_engine.jobs[there.gid]
    assert job["url"] == COMIC and sorted(job["headers"]) == ["Cookie: login=1", "Referer: https://x.org/"]


def test_without_the_cookie_tick_the_sites_cookies_stay_on_the_sender(two):
    w = two
    w.place.cookies = False
    w.s.add(COMIC, headers={"Cookie": "login=1", "Referer": "https://x.org/"})
    assert w.c_engine.jobs[only(w.c).gid]["headers"] == ["Referer: https://x.org/"]


def test_a_folder_chosen_by_hand_keeps_the_download_on_this_pc(two, tmp_path):
    w = two
    row = w.s.store.get(w.s.add(COMIC, folder=str(tmp_path / "mine")))
    assert row.state == ACTIVE and row.remote == {} and w.c.rows() == [] and len(w.s_engine.jobs) == 1


def test_the_sender_that_cannot_check_a_link_itself_still_hands_it_over_by_its_name(two):
    w = two
    blocked = "https://blocked.example/files/Lazarus%2003.cbz"
    w.c_prober.serve(blocked, "Lazarus 03.cbz")
    row = w.s.store.get(w.s.add(blocked))
    assert row.state == SENT and row.filename == "Lazarus 03.cbz" and "could not check the link itself" in row.why
    assert only(w.c).state == ACTIVE
    assert w.s.store.get(w.s.add("https://blocked.example/setup.exe")).state == ERROR      # not for the collector: as before


# ---- the outcome (98B) ---------------------------------------------------------------------------------------

def test_finished_on_the_collector_is_recorded_without_a_notice_and_the_collector_keeps_its_row_until_then(two):
    w = two
    seen = notices.Notices(gather=0)
    seen.observe(w.s.rows(), 0)
    row_id = w.s.add(COMIC)
    there = only(w.c)
    w.c_engine.finish(there.gid)
    w.c.refresh()
    assert w.c.tidy(at_start=True) == 0 and only(w.c).state == COMPLETE      # the sender has not heard yet: it stays
    w.sender.nudge()
    row = w.s.store.get(row_id)
    assert (row.state, row.folder, row.headers, row.error) == (COMPLETE, "", {}, "")
    assert handover.status_text(row) == "Finished on Collector, in Comics"
    assert row.why.endswith("Finished on Collector, in Comics.") and row.path == ""
    seen.observe(w.s.rows(), 1)
    assert [n.kind for n in seen.due(5)] == [notices.ADDED]                   # no "finished" notice for it
    assert only(w.c).remote["heard"] is True and w.s.store.owed() == []       # told in the same go
    assert os.path.isfile(only(w.c).path)
    assert w.c.tidy(at_start=True) == 1 and w.c.rows() == []                  # now the collector may clear its row
    assert os.path.isfile(under(w.c_base, "Comics", "Saga 01.cbz"))           # the row went, never the file


def test_failed_on_the_collector_is_told_with_its_reason_and_retry_sends_it_again(two):
    w = two
    seen = notices.Notices(gather=0)
    seen.observe(w.s.rows(), 0)
    row_id = w.s.add(COMIC)
    first = only(w.c)
    w.c_engine.fail(first.gid, "9", "the disk is full")
    w.c.refresh()
    w.sender.nudge()
    row = w.s.store.get(row_id)
    assert (row.state, row.error) == (ERROR, "Failed on Collector: the disk is full")
    seen.observe(w.s.rows(), 1)
    assert notices.FAILED in [n.kind for n in seen.due(5)]                    # a failure does get its notice
    w.s.retry(row_id)
    again = only(w.c)                                                          # the failed one there went, a new one came
    assert again.id != first.id and again.state == ACTIVE
    assert w.s.store.get(row_id).state == SENT and w.s.store.get(row_id).remote["id"] == again.id
    assert w.s.store.owed() == []


def test_there_is_no_live_percent_and_the_timer_asks_again_only_when_it_is_due(two):
    w = two
    row_id = w.s.add(COMIC)
    there = only(w.c)
    w.c_engine.progress(there.gid, 2500, 5000)
    w.c.refresh()
    w.sender.nudge()
    assert (w.s.store.get(row_id).state, w.s.store.get(row_id).done) == (SENT, 0)
    w.c_engine.finish(there.gid)
    w.c.refresh()
    w.s.refresh()                                                             # the sender's own timer: not due yet
    assert w.s.store.get(row_id).state == SENT
    w.ticks.now += handover.POLL + 1
    w.s.refresh()
    assert w.s.store.get(row_id).state == COMPLETE


def test_a_row_removed_by_hand_on_the_collector_is_reported_as_no_longer_listed(two):
    w = two
    row_id = w.s.add(COMIC)
    w.c.remove(only(w.c).id)
    w.sender.nudge()
    row = w.s.store.get(row_id)
    assert row.state == ERROR and "no longer lists this download" in row.error and "id" not in row.remote
    w.s.remove(row_id)
    assert w.s.store.owed() == []                                             # nothing there to remove any more


# ---- the collector is away (97C) -----------------------------------------------------------------------------

def test_while_the_collector_is_away_the_row_waits_and_goes_over_when_it_is_back(two):
    w = two
    w.away()
    row_id = w.s.add(COMIC, headers={"Cookie": "login=1"})
    row = w.s.store.get(row_id)
    assert row.state == WAITING and handover.status_text(row) == "Waiting for Collector"
    assert row.headers == {"Cookie": "login=1"} and w.s_engine.jobs == {}
    assert taskbar.look(w.s.rows(), 0).mode == taskbar.NONE
    w.s.refresh()                                                             # too soon to try again: nothing happens
    assert w.s.store.get(row_id).state == WAITING
    w.back()
    w.ticks.now += handover.RETRY + 1
    w.s.refresh()
    assert w.s.store.get(row_id).state == SENT and only(w.c).state == ACTIVE


def test_a_place_that_says_download_here_uses_the_senders_other_rules_when_the_collector_is_away(two):
    w = two
    w.place.away = rules.AWAY_HERE
    w.away()
    row = w.s.store.get(w.s.add(COMIC))
    assert (row.state, row.folder, row.rules, row.remote) == (ACTIVE, under(w.s_base, "Comics"), "8", {})
    assert "so it is downloaded on this PC instead." in row.why and "could not be reached" in row.why
    assert w.s_engine.jobs[row.gid]["folder"] == under(w.s_base, "Comics")
    del w.s_rules[2:]                                                         # no other rule fits: the Default folder
    w.s_prober.serve("https://x.org/b.cbz", "b.cbz")
    other = w.s.store.get(w.s.add("https://x.org/b.cbz"))
    assert (other.state, other.folder, other.rules) == (ACTIVE, os.path.normpath(w.s_base), "")


def test_download_here_also_steps_in_when_the_collector_cannot_fetch_the_link(two):
    w = two
    w.place.away = rules.AWAY_HERE
    row_id = w.s.add(COMIC)
    there = only(w.c)
    w.c_engine.fail(there.gid, "3", "404 Not Found")
    w.c.refresh()
    w.sender.nudge()
    row = w.s.store.get(row_id)
    assert (row.state, row.folder, row.remote) == (ACTIVE, under(w.s_base, "Comics"), {})
    assert "Collector could not download it (404 Not Found), so it is downloaded on this PC instead." in row.why
    assert w.c.rows() == [] and w.s.store.owed() == []                        # its failed row there was removed


def test_an_accipio_that_is_no_longer_paired_fails_the_row_instead_of_waiting_for_ever(two):
    w = two
    w.s_node.forget(w.peer.id)
    row = w.s.store.get(w.s.add(COMIC))
    assert row.state == ERROR and "Pair the two again" in row.error and w.c.rows() == []


# ---- torrents: always wait, and never to aria2 ---------------------------------------------------------------

def test_magnet_links_and_torrent_files_always_wait_and_a_magnet_never_reaches_aria2_on_either_side(two):
    w = two
    passed = []

    def stand_in(source, mode, *, folder="", qbittorrent=""):
        passed.append((source, mode))
        return "Passed on."
    w.c.torrents, w.c.pass_torrent = torrents.SYSTEM, stand_in
    w.s.pass_torrent = lambda *a, **k: pytest.fail("the sender passed a torrent on itself")
    w.place.away = rules.AWAY_HERE                                            # even so: torrents are never done here
    w.away()
    w.s_prober.serve("https://a.org/u.torrent", "u.torrent")
    magnet, file = w.s.store.get(w.s.add(MAGNET)), w.s.store.get(w.s.add("https://a.org/u.torrent"))
    assert (magnet.state, file.state) == (WAITING, WAITING) and magnet.remote["away"] == file.remote["away"] == "wait"
    assert magnet.filename == "Ubuntu 26.10 desktop" and w.s_engine.jobs == {}
    w.c_prober.serve("https://a.org/u.torrent", "u.torrent")
    w.back()
    w.sender.nudge()                                                          # sent...
    w.sender.nudge()                                                          # ...and asked how it went
    magnet = w.s.store.get(magnet.id)
    assert magnet.state == COMPLETE and handover.status_text(magnet) == "Finished on Collector"
    assert passed == [(MAGNET, torrents.SYSTEM)]                              # the collector's own torrent setting
    assert w.s.store.get(file.id).state == SENT
    assert all(not torrents.is_magnet(j["url"]) for e in (w.s_engine, w.c_engine) for j in e.jobs.values())
    assert w.s_engine.jobs == {} and len(w.c_engine.jobs) == 1                # only the .torrent file, on the collector


# ---- Remove on the sender (103A) -----------------------------------------------------------------------------

def test_remove_on_the_sender_stops_and_removes_the_unfinished_download_on_the_collector(two):
    w = two
    row_id = w.s.add(COMIC)
    gid = only(w.c).gid
    w.s.remove(row_id)
    assert w.s.rows() == [] and w.c.rows() == [] and w.c_engine.removed == [gid] and w.s.store.owed() == []


def test_removed_while_the_collector_is_away_the_row_goes_at_once_and_the_collector_is_told_when_back(two):
    w = two
    row_id = w.s.add(COMIC)
    there = only(w.c)
    w.away()
    w.s.remove(row_id)
    assert w.s.rows() == []
    assert [(o.peer, o.kind, o.remote_id) for o in w.s.store.owed()] == [(w.peer.id, OWE_REMOVE, there.id)]
    w.back()
    w.ticks.now += handover.RETRY + 1
    w.s.refresh()
    assert w.c.rows() == [] and w.s.store.owed() == []
    waiting = w.s.add(COMIC)                                                  # and one that never got there: just removed
    assert only(w.c).state == ACTIVE


def test_a_row_still_waiting_is_just_removed(two):
    w = two
    w.away()
    w.s.remove(w.s.add(COMIC))
    assert w.s.rows() == [] and w.s.store.owed() == []


def test_a_file_the_collector_has_finished_is_never_touched_from_the_sender(two):
    w = two
    row_id = w.s.add(COMIC)
    there = only(w.c)
    w.c_engine.finish(there.gid)
    w.c.refresh()
    w.s.remove(row_id)                                                        # the sender had not even heard yet
    kept = only(w.c)
    assert kept.state == COMPLETE and os.path.isfile(kept.path) and kept.remote["heard"] is True
    again = w.s.add(COMIC)
    w.c_engine.finish(w.c.store.get(w.s.store.get(again).remote["id"]).gid)
    w.c.refresh()
    w.sender.nudge()
    w.s.remove(again)                                                         # heard, finished: only the sender's row goes
    assert len(w.c.rows()) == 2 and w.s.store.owed() == []


# ---- the collector's door ------------------------------------------------------------------------------------

def test_an_accipio_can_ask_about_and_remove_only_what_it_sent_itself(two):
    w = two
    w.c_prober.serve("https://c.org/own.epub", "own.epub")
    own = w.c.add("https://c.org/own.epub")
    w.s.add(COMIC)
    sent = [r for r in w.c.rows() if r.id != own][0]
    stranger = link.Peer("ffff", "Other")
    assert w.handler(link.status([own, sent.id]), stranger)["downloads"] == [
        {"id": own, "state": "gone"}, {"id": sent.id, "state": "gone"}]
    assert w.handler(link.remove([own, sent.id]), stranger) == {"ok": True}
    assert {r.id for r in w.c.rows()} == {own, sent.id}
    mine = link.Peer(w.s_node.id, "Laptop")
    assert w.handler(link.status([own, sent.id]), mine)["downloads"] == [
        {"id": own, "state": "gone"}, {"id": sent.id, "state": "working", "filename": "Saga 01.cbz"}]


def test_the_same_download_asked_twice_is_queued_once_and_only_links_are_taken(two):
    w = two
    mine = link.Peer(w.s_node.id, "Laptop")
    first = w.handler(link.add(COMIC, ref="4-abcd"), mine)
    assert first["ok"] and w.handler(link.add(COMIC, ref="4-abcd"), mine) == first and len(w.c.rows()) == 1
    assert w.handler(link.add(COMIC, ref="5-abcd"), mine)["id"] != first["id"]
    for bad in ("C:\\Windows\\notepad.exe", "file:///etc/passwd", "javascript:alert(1)", COMIC + " and more"):
        assert w.handler(link.add(bad), mine) == {"ok": False, "error": "not a link Accipio can download"}
    assert len(w.c.rows()) == 2


def test_what_came_from_another_accipio_is_never_sent_on_to_a_third(two):
    w = two
    w.c_library.folders.append(Folder("Elsewhere", kind=rules.PLACE_ACCIPIO, peer=w.s_node.id))
    w.c_rules.insert(0, Rule("Comics on", [Condition("type", ["Comics"])], target="Elsewhere"))
    w.s.add(COMIC)
    there = only(w.c)
    assert (there.state, there.folder, there.rules) == (ACTIVE, under(w.c_base, "Comics"), "7")


def test_a_file_only_the_browser_could_fetch_stays_on_the_sender(two, tmp_path):
    w = two
    saved = tmp_path / "browser" / "Saga 02.cbz"
    saved.parent.mkdir()
    saved.write_bytes(b"PK")
    row = w.s.store.get(w.s.file_saved(str(saved), url="blob:https://x.org/1", suggested="Saga 02.cbz"))
    assert (row.state, row.folder, row.remote) == (COMPLETE, under(w.s_base, "Comics"), {}) and w.c.rows() == []


def test_the_list_shows_the_two_new_states_under_active():
    from accipio.gui import queue_model
    assert {WAITING, SENT} <= set(queue_model.FILTERS["Active"])
    assert queue_model.STATE_TEXT[WAITING] and queue_model.STATE_TEXT[SENT]
