"""One Accipio's whole link (handover.Service), the three settings that drive it, and what Forget does on each side.
Listeners are on 127.0.0.1 only; keys are a stand-in; everything is under tmp_path. Nothing here touches the real
keyring (tests/conftest.py makes that an error) or a torrent program."""
import json
import os
import socket

import pytest

from accipio.core import handover, link, settings
from accipio.core.store import ACTIVE, COMPLETE, ERROR, SENT, WAITING
from tests import samples
from tests.test_handover import COMIC, a_queue, only, two, under  # noqa: F401  (``two`` is a fixture)
from tests.test_link import MemoryKeys

needs_psk = pytest.mark.skipif(not link.available(), reason="this Python's ssl module has no pre-shared keys")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def solo(tmp_path):
    """A queue and a node of their own, and a Service that has been given nothing yet."""
    queue, _, _ = a_queue(str(tmp_path / "solo"), samples.sorting_rules(), samples.sorting_library())
    node = link.Node(str(tmp_path / "solo" / "config"), MemoryKeys())
    service = handover.Service(queue, node, timeout=3)
    yield service
    service.stop()
    queue.store.close()


# ---- the settings --------------------------------------------------------------------------------------------

def test_the_link_settings_start_off_and_an_older_settings_file_loads_unchanged(tmp_path):
    fresh = settings.Settings()
    assert (fresh.link_accept, fresh.link_addresses, fresh.link_port) == (False, [], link.DEFAULT_PORT)
    (tmp_path / "settings.json").write_text(json.dumps({"max_downloads": 5, "debug_log": True}), encoding="utf-8")
    old = settings.load_settings(str(tmp_path))
    assert (old.max_downloads, old.debug_log, old.link_accept, old.link_addresses) == (5, True, False, [])
    assert old.link_port == link.DEFAULT_PORT


def test_the_link_settings_are_saved_and_loaded_again(tmp_path):
    mine = settings.Settings(link_accept=True, link_addresses=["127.0.0.1", "100.101.102.103"], link_port=50123)
    settings.save_settings(str(tmp_path), mine)
    again = settings.load_settings(str(tmp_path))
    assert (again.link_accept, again.link_addresses, again.link_port) == (True, ["127.0.0.1", "100.101.102.103"], 50123)


@pytest.mark.parametrize("accept, addresses, port, expected", [
    ("yes", ["a", "a"], True, (False, [], link.DEFAULT_PORT)),            # not a bool, a repeat, a bool as a port
    (1, ["a", ""], 0, (False, [], link.DEFAULT_PORT)),                    # 1 is not a bool; an empty address
    (True, ["a", 5], 70000, (True, [], link.DEFAULT_PORT)),               # a number among the addresses; port too big
    (True, "127.0.0.1", "47610", (True, [], link.DEFAULT_PORT)),          # not a list; a port as text
    (True, ["a", " "], -3, (True, [], link.DEFAULT_PORT)),                # an address of spaces; a negative port
    (True, ["b", "a"], 65535, (True, ["b", "a"], 65535)),                 # good values stay, in their order
])
def test_broken_link_settings_are_repaired_not_refused(accept, addresses, port, expected):
    repaired = settings.Settings(link_accept=accept, link_addresses=addresses, link_port=port)
    assert (repaired.link_accept, repaired.link_addresses, repaired.link_port) == expected


# ---- the listener follows the settings -----------------------------------------------------------------------

@needs_psk
def test_nothing_listens_until_accepting_is_on_and_an_address_is_ticked(solo):
    assert solo.unavailable == "" and solo.listener is None and solo.listening == []
    assert solo.status_text() == "Not accepting downloads from other Accipios."
    solo.apply(False, ["127.0.0.1"], 0)
    assert solo.listener is None and solo.status_text() == "Not accepting downloads from other Accipios."
    solo.apply(True, [], 0)
    assert solo.listener is None
    assert solo.status_text() == "Accepting is on, but no address is ticked, so nothing listens."
    solo.apply(True, ["127.0.0.1"], 0)
    assert len(solo.listening) == 1 and solo.listening[0][0] == "127.0.0.1" and solo.problems == {}
    assert solo.status_text() == f"Listening on 127.0.0.1, port {solo.listening[0][1]}."


@needs_psk
def test_a_change_restarts_the_listener_and_the_same_values_do_nothing(solo):
    solo.apply(True, ["127.0.0.1"], 0)
    first = solo.listener
    solo.apply(True, ["127.0.0.1"], 0)
    assert solo.listener is first                                             # nothing happened
    port = free_port()
    solo.apply(True, ["127.0.0.1"], port)
    assert solo.listener is not first and first.bound == []                   # the old one was stopped
    assert solo.listening == [("127.0.0.1", port)]
    solo.apply(False, ["127.0.0.1"], port)
    assert solo.listener is None and solo.listening == []
    assert solo.status_text() == "Not accepting downloads from other Accipios."
    with pytest.raises(OSError), socket.create_connection(("127.0.0.1", port), timeout=1):
        pass                                                                  # the door is shut


@needs_psk
def test_an_address_that_is_not_this_pcs_is_a_problem_while_the_good_one_still_listens(solo):
    solo.apply(True, ["127.0.0.1", "192.0.2.1"], 0)
    assert [address for address, _ in solo.listening] == ["127.0.0.1"]
    assert list(solo.problems) == ["192.0.2.1"] and solo.problems["192.0.2.1"]
    text = solo.status_text()
    assert text.startswith("Listening on 127.0.0.1, port ") and "Could not listen on 192.0.2.1: " in text


@needs_psk
def test_two_addresses_are_named_with_and_and_one_port(solo):
    solo.apply(True, ["127.0.0.1", "::1"], free_port())
    if len(solo.listening) == 2:                                              # a PC without IPv6 listens on one
        assert solo.status_text().startswith("Listening on 127.0.0.1 and ::1, port ")


@needs_psk
def test_a_listener_that_cannot_start_at_all_is_a_problem_and_apply_never_raises(solo, monkeypatch):
    def boom(self):
        raise link.LinkUnavailable("no keyring")
    monkeypatch.setattr(link.Listener, "start", boom)
    solo.apply(True, ["127.0.0.1"], 0)
    assert solo.listener is None and solo.listening == [] and solo.problems == {"127.0.0.1": "no keyring"}
    assert solo.status_text() == "Not listening on any address. Could not listen on 127.0.0.1: no keyring."


@needs_psk
def test_stop_closes_the_door_and_can_be_called_twice(solo):
    port = free_port()
    solo.apply(True, ["127.0.0.1"], port)
    solo.stop()
    solo.stop()
    assert solo.listener is None and solo.listening == []
    with pytest.raises(OSError), socket.create_connection(("127.0.0.1", port), timeout=1):
        pass


def test_on_a_python_without_pre_shared_keys_nothing_listens_and_the_status_says_why(solo, monkeypatch, tmp_path):
    monkeypatch.setattr(link, "available", lambda: False)
    queue, _, _ = a_queue(str(tmp_path / "plain"), samples.sorting_rules(), samples.sorting_library())
    service = handover.Service(queue, link.Node(str(tmp_path / "plain" / "config"), MemoryKeys()))
    try:
        assert service.unavailable == link.why_unavailable() != ""
        assert isinstance(service.sender, handover.Sender)                    # attached all the same (decision 2)
        service.apply(True, ["127.0.0.1"], 0)
        assert service.listener is None and service.listening == []
        assert service.status_text() == link.why_unavailable()
        service.stop()
    finally:
        queue.store.close()


# ---- two Services, end to end --------------------------------------------------------------------------------

@pytest.fixture
def services(two):
    """The ``two`` setup with each side's whole link as a Service: the collector listens where the fixture's own
    listener did (that one is stopped first)."""
    w = two
    w.listener.stop()
    w.c_service = handover.Service(w.c, w.c_node, timeout=3)
    w.c_service.apply(True, ["127.0.0.1"], w.port)
    assert w.c_service.listening == [("127.0.0.1", w.port)], w.c_service.problems
    w.s_service = handover.Service(w.s, w.s_node, timeout=3)
    w.s_service.sender.clock = w.ticks
    yield w
    w.c_service.stop()
    w.s_service.stop()


@needs_psk
def test_two_services_hand_a_comic_over_and_it_finishes(services):
    w = services
    row_id = w.s.add(COMIC)
    there = only(w.c)
    assert w.s.store.get(row_id).state == SENT and there.state == ACTIVE
    w.c_engine.finish(there.gid)
    w.c.refresh()
    w.s_service.sender.nudge()
    row = w.s.store.get(row_id)
    assert row.state == COMPLETE and handover.status_text(row) == "Finished on Collector, in Comics"
    assert os.path.isfile(only(w.c).path)


@needs_psk
def test_reach_says_nothing_when_the_other_accipio_answers_and_the_reason_when_it_does_not(services):
    w = services
    assert w.s_service.reach(w.peer.id) == ""
    assert "not paired" in w.s_service.reach("ffff")
    w.c_service.apply(False, ["127.0.0.1"], w.port)                           # the collector stops accepting
    assert "could not be reached" in w.s_service.reach(w.peer.id, timeout=2)
    w.s_node.remember(link.Peer("noaddr", "Sender only"), b"k" * 32)
    assert w.s_service.reach("noaddr") == "No address is known for Sender only."


# ---- Forget ---------------------------------------------------------------------------------------------------

@needs_psk
def test_forget_on_the_sender_ends_a_sent_row_and_retry_owes_nobody_anything(services):
    w = services
    row_id = w.s.add(COMIC)
    assert w.s.store.get(row_id).state == SENT
    w.s_service.forget(w.peer.id)
    row = w.s.store.get(row_id)
    assert w.s_node.peer(w.peer.id) is None and row.state == ERROR and "id" not in row.remote
    assert "is not paired with this Accipio any more" in row.error and "Pair the two again and retry" in row.error
    assert w.s.store.owed() == []
    w.s.retry(row_id)                                                         # nothing is owed there; it starts anew
    assert w.s.store.owed() == [] and w.s.store.get(row_id).state == ERROR
    assert "Pair the two again" in w.s.store.get(row_id).error


@needs_psk
def test_forget_on_the_sender_fails_a_waiting_row_unless_the_place_says_download_here(services):
    w = services
    w.c_service.apply(False, [], w.port)                                      # the collector is away
    waiting = w.s.add(COMIC)
    assert w.s.store.get(waiting).state == WAITING
    w.s_service.forget(w.peer.id)
    assert w.s.store.get(waiting).state == ERROR and "Pair the two again" in w.s.store.get(waiting).error


@needs_psk
def test_forget_on_the_sender_downloads_a_waiting_row_here_when_the_place_says_so(services):
    w = services
    w.place.away = "here"
    in_place = w.s.in_background
    w.s.in_background = lambda fn, *args: None                                # nothing is sent: the row just waits
    waiting = w.s.add(COMIC)
    w.s.in_background = in_place
    assert w.s.store.get(waiting).state == WAITING and w.s.store.get(waiting).remote["away"] == "here"
    w.s_service.forget(w.peer.id)
    row = w.s.store.get(waiting)
    assert row.state == ACTIVE and row.remote == {} and row.folder == under(w.s_base, "Comics")


@needs_psk
def test_forget_on_the_collector_marks_what_that_sender_sent_as_heard_so_it_is_cleared(services):
    w = services
    w.s.add(COMIC)
    there = only(w.c)
    w.c_engine.finish(there.gid)
    w.c.refresh()
    assert w.c.tidy(at_start=True) == 0 and only(w.c).state == COMPLETE      # the sender has not heard: it stays
    w.c_service.forget(w.s_node.id)
    assert w.c_node.peer(w.s_node.id) is None
    assert only(w.c).remote["heard"] is True
    assert w.c.tidy(at_start=True) == 1 and w.c.rows() == []
    assert os.path.isfile(under(w.c_base, "Comics", "Saga 01.cbz"))           # the row went, never the file


@needs_psk
def test_forget_sender_counts_what_it_marked_and_leaves_other_senders_and_own_downloads_alone(services):
    w = services
    w.c_prober.serve("https://c.org/own.epub", "own.epub")
    own = w.c.add("https://c.org/own.epub")
    w.s.add(COMIC)
    sent = [r for r in w.c.rows() if r.id != own][0]
    assert w.c.forget_sender("ffff") == 0
    assert w.c.forget_sender(w.s_node.id) == 1
    assert w.c.forget_sender(w.s_node.id) == 0                                # already heard
    assert w.c.store.get(sent.id).state == ACTIVE and w.c.store.get(sent.id).remote["heard"] is True
    assert "heard" not in w.c.store.get(own).remote
