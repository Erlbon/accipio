"""Torrents are passed on to a torrent program, never downloaded by Accipio (2026-11-17#07, core/torrents.py).
No test starts a real torrent program or touches the network: the starter and the system opener are stand-ins."""
import os

import pytest

from accipio.core import torrents
from accipio.core.queue import Queue
from accipio.core.rules import Condition, Group, Rule
from accipio.core.store import COMPLETE, ERROR, Store
from tests import samples
from tests.test_queue import TUE_1014, FakeEngine, FakeProber, under

HASH = "c12fe1c06bba254a9dc9f519b335aa7c1367a88a"
MAGNET = f"magnet:?xt=urn:btih:{HASH}&dn=Ubuntu+26.10+desktop&tr=udp%3A%2F%2Ftracker.example.org%3A1337"


def test_what_counts_as_a_magnet_link_and_what_it_is_called():
    assert torrents.is_magnet(MAGNET) and torrents.is_magnet(f"MAGNET:?dn=x&xt=urn:btih:{HASH}")
    assert torrents.is_magnet("magnet:?xt=urn:btmh:1220" + "ab" * 32)                  # a v2 torrent
    assert not torrents.is_magnet("magnet:?dn=only-a-name") and not torrents.is_magnet("https://a.org/x.torrent")
    assert not torrents.is_magnet(f"{MAGNET} and more") and not torrents.is_magnet("")
    assert torrents.magnet_name(MAGNET) == "Ubuntu 26.10 desktop"
    assert torrents.magnet_name(f"magnet:?xt=urn:btih:{HASH}") == "Magnet link c12fe1c0"
    assert torrents.is_torrent_file("Some.Name.TORRENT") and not torrents.is_torrent_file("torrent.txt")


def test_qbittorrent_gets_the_link_as_one_argument_with_the_folder(tmp_path):
    program = tmp_path / "qbittorrent.exe"
    program.write_bytes(b"")
    started = []
    folder = str(tmp_path / "Linux images")
    told = torrents.hand_over(MAGNET, torrents.QBITTORRENT, folder=folder, qbittorrent=str(program), start=started.append)
    assert started == [[os.path.normpath(str(program)), f"--save-path={folder}", MAGNET]]
    assert "qBittorrent" in told and folder in told
    # a .torrent file: its path, and no folder given means qBittorrent's own
    file = tmp_path / "x.torrent"
    file.write_bytes(b"d8:announce0:e")
    torrents.hand_over(str(file), torrents.QBITTORRENT, qbittorrent=str(program), start=started.append)
    assert started[-1] == [os.path.normpath(str(program)), str(file)]


def test_the_system_way_opens_it_and_says_who_decides_the_folder():
    opened = []
    told = torrents.hand_over(MAGNET, torrents.SYSTEM, folder="ignored", open_with_system=opened.append)
    assert opened == [MAGNET] and "decides where its files go" in told


def test_nothing_to_pass_it_to_is_said_in_words(tmp_path):
    def nobody(source):
        raise OSError("no application is associated")
    with pytest.raises(torrents.TorrentError, match="no program on this PC opens torrents"):
        torrents.hand_over(MAGNET, torrents.SYSTEM, open_with_system=nobody)
    with pytest.raises(torrents.TorrentError, match="switched off"):
        torrents.hand_over(MAGNET, torrents.OFF)
    with pytest.raises(torrents.TorrentError, match="not a magnet link"):
        torrents.hand_over("https://a.org/file.zip", torrents.SYSTEM, open_with_system=lambda s: None)
    with pytest.raises(torrents.TorrentError, match="not a magnet link"):
        torrents.hand_over(str(tmp_path / "missing.torrent"), torrents.SYSTEM, open_with_system=lambda s: None)
    program = tmp_path / "qbittorrent.exe"
    program.write_bytes(b"")

    def broken(command):
        raise OSError("access denied")
    with pytest.raises(torrents.TorrentError, match="could not be started"):
        torrents.hand_over(MAGNET, torrents.QBITTORRENT, qbittorrent=str(program), start=broken)


def test_qbittorrent_is_found_where_it_is_picked_installed_or_on_path(tmp_path):
    picked, installed, on_path = (tmp_path / n for n in ("picked.exe", "installed.exe", "path.exe"))
    for file in (picked, installed, on_path):
        file.write_bytes(b"")
    nowhere = dict(app_path=lambda: "", which=lambda name: None)
    assert torrents.find_qbittorrent(str(picked), **nowhere) == os.path.normpath(str(picked))
    assert torrents.find_qbittorrent(str(tmp_path / "gone.exe"), app_path=lambda: str(installed),
                                     which=lambda name: None) == os.path.normpath(str(installed))
    really_installed = torrents.find_qbittorrent("", **nowhere)          # "" unless this machine has one
    found = torrents.find_qbittorrent("", app_path=lambda: "", which=lambda name: str(on_path))
    assert found == (really_installed or os.path.normpath(str(on_path)))
    if not really_installed:
        with pytest.raises(torrents.TorrentError, match="qBittorrent was not found"):
            torrents.hand_over(MAGNET, torrents.QBITTORRENT, qbittorrent=str(tmp_path / "gone.exe"),
                               start=lambda command: None)


# ---- in the queue ----------------------------------------------------------------------------------------

@pytest.fixture
def world(tmp_path):
    base = str(tmp_path / "Downloads")
    engine, prober = FakeEngine(), FakeProber()
    store = Store(str(tmp_path / "state" / "queue.db"))
    rule_list = samples.sorting_rules()
    library = samples.sorting_library()
    library.groups.append(Group("Torrents", extensions=["torrent"]))
    library.folders.append(samples.Folder("Torrents", "%default%/Torrents"))
    rule_list.insert(0, Rule("Torrents", [Condition("type", ["Torrents"])], target="Torrents"))
    q = Queue(store, engine, lambda: rule_list, base, prober=prober, clock=lambda: TUE_1014, library_set=lambda: library)
    passed = []

    def stand_in(source, mode, *, folder="", qbittorrent=""):
        if mode == torrents.OFF:
            raise torrents.TorrentError("passing torrents on is switched off (Settings > Torrents)")
        passed.append((source, mode, folder))
        return f"Passed on ({mode})."
    q.pass_torrent = stand_in
    return q, engine, prober, base, passed


def test_a_magnet_link_is_passed_on_with_the_folder_the_rules_choose_and_never_reaches_the_engine(world):
    q, engine, prober, base, passed = world
    q.torrents = torrents.QBITTORRENT
    row = q.store.get(q.add(MAGNET, page_url="https://a.org/"))
    assert passed == [(MAGNET, torrents.QBITTORRENT, under(base, "Torrents"))]
    assert (row.state, row.filename, row.folder, row.rules) == (COMPLETE, "Ubuntu 26.10 desktop", under(base, "Torrents"), "1")
    assert "A magnet link." in row.why and row.finished and row.gid == ""
    assert engine.jobs == {} and prober.seen_headers == []              # nothing was downloaded or even asked about
    shown = q.preview(MAGNET)
    assert (shown.kind, shown.filename, shown.placement.folder) == ("torrent", "Ubuntu 26.10 desktop", under(base, "Torrents"))


def test_a_folder_chosen_by_hand_in_the_add_box_wins(world, tmp_path):
    q, engine, prober, base, passed = world
    q.torrents = torrents.QBITTORRENT
    mine = str(tmp_path / "mine")
    row = q.store.get(q.add(MAGNET, folder=mine))
    assert passed[-1][2] == os.path.normpath(mine) and row.folder == os.path.normpath(mine) and row.rules == ""


def test_the_system_way_shows_no_folder_because_the_program_decides(world):
    q, engine, prober, base, passed = world
    q.torrents = torrents.SYSTEM
    row = q.store.get(q.add(MAGNET))
    assert (row.state, row.folder, row.rules) == (COMPLETE, "", "") and passed[-1][1] == torrents.SYSTEM


def test_switched_off_a_magnet_link_fails_with_the_way_to_switch_it_on_and_retry_passes_it_on(world):
    q, engine, prober, base, passed = world
    row = q.store.get(q.add(MAGNET))
    assert row.state == ERROR and "Settings > Torrents" in row.error and passed == [] and engine.jobs == {}
    q.torrents = torrents.SYSTEM
    q.retry(row.id)
    assert q.store.get(row.id).state == COMPLETE and len(passed) == 1 and engine.jobs == {}      # never to aria2


def test_a_finished_torrent_file_is_passed_on_and_saves_beside_itself(world):
    q, engine, prober, base, passed = world
    prober.serve("https://a.org/ubuntu.torrent", "ubuntu.torrent")
    row_id = q.add("https://a.org/ubuntu.torrent")
    q.torrents = torrents.QBITTORRENT
    engine.finish(q.store.get(row_id).gid, b"d8:announce0:e")
    q.refresh()
    row = q.store.get(row_id)
    assert row.state == COMPLETE and os.path.isfile(row.path)           # the .torrent file itself stays
    assert passed == [(row.path, torrents.QBITTORRENT, under(base, "Torrents"))] and "Passed on" in row.why


def test_switched_off_a_torrent_file_is_just_a_file(world):
    q, engine, prober, base, passed = world
    prober.serve("https://a.org/ubuntu.torrent", "ubuntu.torrent")
    row_id = q.add("https://a.org/ubuntu.torrent")
    engine.finish(q.store.get(row_id).gid, b"d8:announce0:e")
    q.refresh()
    assert q.store.get(row_id).state == COMPLETE and passed == []


def test_a_torrent_file_that_cannot_be_passed_on_still_finishes_and_says_why(world):
    q, engine, prober, base, passed = world
    q.torrents = torrents.QBITTORRENT

    def nobody(source, mode, *, folder="", qbittorrent=""):
        raise torrents.TorrentError("qBittorrent was not found.")
    q.pass_torrent = nobody
    prober.serve("https://a.org/ubuntu.torrent", "ubuntu.torrent")
    row_id = q.add("https://a.org/ubuntu.torrent")
    engine.finish(q.store.get(row_id).gid, b"d8:announce0:e")
    q.refresh()
    row = q.store.get(row_id)
    assert row.state == COMPLETE and "could not be passed on" in row.why and "not found" in row.why
