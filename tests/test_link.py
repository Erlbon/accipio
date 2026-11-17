"""The line between two Accipios: real encrypted connections on 127.0.0.1, config folders under tmp_path and a
stand-in key store. No test here touches the real keyring or any network but this PC's own loopback."""
import json
import socket
import ssl
import struct
import threading
import time

import pytest

from accipio.core import link

pytestmark = pytest.mark.skipif(not link.available(), reason="this Python's ssl module has no pre-shared keys")


class MemoryKeys:
    """Stands in for the system keyring."""

    def __init__(self):
        self.held = {}

    def get(self, name):
        return self.held.get(name, "")

    def set(self, name, value):
        self.held[name] = value

    def delete(self, name):
        self.held.pop(name, None)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture(autouse=True)
def never_the_real_keyring(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a link test reached for the real keyring")
    for name in ("get", "set", "delete"):
        monkeypatch.setattr(link.SystemKeys, name, refuse)


@pytest.fixture
def collector(tmp_path):
    """A listening Accipio: (node, listener, port, what its handler saw)."""
    seen = []

    def handler(message, peer):
        seen.append((message, peer))
        if message["type"] == "add" and "boom" in message["url"]:
            raise ValueError("handler trouble")
        return {"ok": True, "id": len(seen), "from": peer.name}
    node = link.Node(str(tmp_path / "collector"), MemoryKeys(), clock=Clock())
    node.rename("Collector")
    listener = link.Listener(node, handler, ["127.0.0.1"], port=0).start()
    yield node, listener, listener.bound[0][1], seen
    listener.stop()


@pytest.fixture
def sender(tmp_path):
    node = link.Node(str(tmp_path / "sender"), MemoryKeys())
    node.rename("Laptop")
    return node


@pytest.fixture
def paired(collector, sender):
    node, listener, port, seen = collector
    peer = link.pair(sender, "127.0.0.1", port, node.begin_pairing())
    return sender, peer, node, seen


# ---- pairing -------------------------------------------------------------------------------------------------

WRONG = "AAAA-AAAA-AAAA"


def test_the_code_is_twelve_characters_in_three_groups_and_pairing_gives_both_the_same_long_key(collector, sender):
    node, listener, port, seen = collector
    code = node.begin_pairing()
    groups = code.split("-")
    assert [len(g) for g in groups] == [4, 4, 4] and set("".join(groups)) <= set(link.CODE_ALPHABET)
    assert not set("01ILO") & set(link.CODE_ALPHABET) and len(set(link.CODE_ALPHABET)) == len(link.CODE_ALPHABET) == 31
    assert 0 < node.pairing_left() <= link.PAIRING_SECONDS

    peer = link.pair(sender, "127.0.0.1", port, code)

    assert (peer.id, peer.name, peer.host, peer.port) == (node.id, "Collector", "127.0.0.1", port)
    assert [(p.id, p.name, p.host) for p in node.peers()] == [(sender.id, "Laptop", "")]
    key = sender.key_of(node.id)
    assert len(key) == 32 and key == node.key_of(sender.id)
    assert code.encode() not in key and key != link.code_key(code, b"")


def test_a_code_typed_with_spaces_and_dashes_still_works(collector, sender):
    node, listener, port, seen = collector
    code = node.begin_pairing()
    link.pair(sender, "127.0.0.1", port, "  " + code.replace("-", " ").lower() + " ")
    assert node.peer(sender.id) is not None
    assert link.show_code("k7qm2xrd9fhp") == "K7QM-2XRD-9FHP"


def test_a_code_works_only_once(collector, sender, tmp_path):
    node, listener, port, seen = collector
    code = node.begin_pairing()
    link.pair(sender, "127.0.0.1", port, code)
    assert node.pairing_left() == 0
    other = link.Node(str(tmp_path / "other"), MemoryKeys())
    with pytest.raises(link.WrongCode, match="not showing a pairing code"):
        link.pair(other, "127.0.0.1", port, code)
    assert node.peer(other.id) is None and other.peers() == []


def test_a_wrong_code_is_refused_and_three_tries_end_the_code(collector, sender):
    node, listener, port, seen = collector
    code = node.begin_pairing()
    wrong = WRONG if code != WRONG else "BBBB-BBBB-BBBB"
    for _ in range(link.PAIRING_TRIES):
        with pytest.raises(link.WrongCode, match="did not accept the code"):
            link.pair(sender, "127.0.0.1", port, wrong)
    assert node.pairing_left() == 0
    with pytest.raises(link.WrongCode):                    # even the right code is too late now
        link.pair(sender, "127.0.0.1", port, code)
    assert node.peers() == [] and sender.peers() == []


def test_two_wrong_tries_leave_room_for_the_right_one(collector, sender):
    node, listener, port, seen = collector
    code = node.begin_pairing()
    wrong = WRONG if code != WRONG else "BBBB-BBBB-BBBB"
    for _ in range(link.PAIRING_TRIES - 1):
        with pytest.raises(link.WrongCode):
            link.pair(sender, "127.0.0.1", port, wrong)
    assert link.pair(sender, "127.0.0.1", port, code).id == node.id


def test_a_code_runs_out_after_five_minutes(collector, sender):
    node, listener, port, seen = collector
    code = node.begin_pairing()
    node.clock.now += link.PAIRING_SECONDS + 1
    assert node.pairing_left() == 0
    with pytest.raises(link.WrongCode, match="not showing a pairing code"):
        link.pair(sender, "127.0.0.1", port, code)


def test_a_new_code_replaces_the_old_one_and_cancel_ends_it(collector, sender):
    node, listener, port, seen = collector
    old = node.begin_pairing()
    new = node.begin_pairing()
    if old != new:
        with pytest.raises(link.WrongCode):
            link.pair(sender, "127.0.0.1", port, old)
    node.cancel_pairing()
    with pytest.raises(link.WrongCode, match="not showing"):
        link.pair(sender, "127.0.0.1", port, new)


def test_no_code_shown_means_no_pairing(collector, sender):
    node, listener, port, seen = collector
    with pytest.raises(link.WrongCode, match="not showing a pairing code"):
        link.pair(sender, "127.0.0.1", port, WRONG)


def test_an_accipio_cannot_pair_with_itself(collector):
    node, listener, port, seen = collector
    with pytest.raises(link.WrongCode, match="itself"):
        link.pair(node, "127.0.0.1", port, node.begin_pairing())
    assert node.peers() == []


def test_pairing_again_replaces_the_old_key(paired, collector):
    sender, peer, node, seen = paired
    first = sender.key_of(node.id)
    link.pair(sender, "127.0.0.1", peer.port, node.begin_pairing())
    assert sender.key_of(node.id) != first and sender.key_of(node.id) == node.key_of(sender.id)
    assert len(node.peers()) == 1 and len(sender.peers()) == 1
    assert link.send(sender, peer, link.hello())["ok"]


# ---- messages over the line ----------------------------------------------------------------------------------

def test_a_paired_sender_gets_its_messages_through(paired):
    sender, peer, node, seen = paired
    reply = link.send(sender, peer, link.add("https://example.test/a.zip", page_url="https://example.test/",
                                             cookies="session=1"))
    assert reply == {"ok": True, "id": 1, "from": "Laptop"}
    message, who = seen[0]
    assert message == {"v": 1, "type": "add", "url": "https://example.test/a.zip",
                       "page_url": "https://example.test/", "cookies": "session=1"}
    assert who.id == sender.id


def test_hello_is_answered_by_the_listener_itself(paired):
    sender, peer, node, seen = paired
    reply = link.send(sender, peer, link.hello())
    assert reply["ok"] and reply["id"] == node.id and reply["name"] == "Collector" and reply["version"]
    assert seen == []


def test_several_messages_on_one_line(paired):
    sender, peer, node, seen = paired
    with link.Line(sender, peer) as line:
        assert line.ask(link.add("https://example.test/1"))["id"] == 1
        assert line.ask(link.add("https://example.test/2"))["id"] == 2
        assert line.ask(link.status([1, 2]))["ok"]
    assert [m["type"] for m, _ in seen] == ["add", "add", "status"]


def test_a_handler_error_and_a_bad_message_are_answers_not_crashes(paired):
    sender, peer, node, seen = paired
    assert link.send(sender, peer, link.add("https://example.test/boom")) == {"ok": False, "error": "handler trouble"}
    assert "unknown message" in link.send(sender, peer, {"v": 1, "type": "dance"})["error"]
    assert "version 2" in link.send(sender, peer, {"v": 2, "type": "hello"})["error"]
    assert "no link" in link.send(sender, peer, {"v": 1, "type": "add", "url": ""})["error"]
    assert link.send(sender, peer, link.hello())["ok"]     # and the listener carries on


def test_the_message_shapes():
    assert link.problem_with(link.hello()) == ""
    assert link.problem_with(link.add("https://example.test/x")) == ""
    assert link.add("u", referrer="r") == {"v": 1, "type": "add", "url": "u", "referrer": "r"}
    assert link.problem_with(link.status([3, 4])) == ""
    assert link.problem_with({"v": 1, "type": "status", "ids": ["3"]})
    assert link.problem_with({"v": 1, "type": "add", "url": "u", "cookies": 5})
    assert link.problem_with("add") == "not a message"


def test_what_travels_is_encrypted(paired):
    """A relay between the two sees every byte: the link and the cookie are not among them."""
    sender, peer, node, seen = paired
    captured = bytearray()
    relay = socket.socket()
    relay.bind(("127.0.0.1", 0))
    relay.listen(1)

    def pump(source, target):
        try:
            while True:
                data = source.recv(65536)
                if not data:
                    break
                captured.extend(data)
                target.sendall(data)
        except OSError:
            pass
        finally:
            try:
                target.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    def carry():
        inside, _ = relay.accept()
        outside = socket.create_connection(("127.0.0.1", peer.port))
        back = threading.Thread(target=pump, args=(outside, inside), daemon=True)
        back.start()
        pump(inside, outside)
        back.join(5)
        inside.close()
        outside.close()
    carrier = threading.Thread(target=carry, daemon=True)
    carrier.start()
    through = link.Peer(peer.id, peer.name, "127.0.0.1", relay.getsockname()[1])
    assert link.send(sender, through, link.add("https://example.test/secret-file.zip", cookies="session=hunter2"))["ok"]
    carrier.join(5)
    relay.close()
    assert len(captured) > 200
    assert b"secret-file" not in captured and b"hunter2" not in captured and b"example.test" not in captured
    assert sender.key_of(node.id) not in captured


def test_the_line_is_tls_1_2_with_the_forward_secret_psk_cipher(paired):
    sender, peer, node, seen = paired
    with link.Line(sender, peer) as line:
        assert line._line.version() == "TLSv1.2"
        assert line._line.cipher()[0] == "ECDHE-PSK-CHACHA20-POLY1305"


# ---- who is let in -------------------------------------------------------------------------------------------

def test_a_stranger_is_refused_and_the_listener_carries_on(paired, tmp_path):
    sender, peer, node, seen = paired
    stranger = link.Node(str(tmp_path / "stranger"), MemoryKeys())
    stranger.remember(link.Peer(node.id, "Collector", "127.0.0.1", peer.port), b"\x07" * 32)   # a key it made up
    with pytest.raises(link.Refused, match="not paired"):
        link.send(stranger, stranger.peer(node.id), link.hello())
    assert seen == []
    assert link.send(sender, peer, link.hello())["ok"]


def test_a_sender_with_the_right_name_and_the_wrong_key_is_refused(paired):
    sender, peer, node, seen = paired
    sender.keys.set(f"link-key-{node.id}", (b"\x01" * 32).hex())
    with pytest.raises(link.Refused):
        link.send(sender, peer, link.add("https://example.test/x"))
    assert seen == []


def test_a_forgotten_sender_is_refused(paired):
    sender, peer, node, seen = paired
    node.forget(sender.id)
    assert node.peers() == [] and node.keys.held == {}
    with pytest.raises(link.Refused):
        link.send(sender, peer, link.hello())


def test_without_a_pairing_nothing_is_sent(sender):
    with pytest.raises(link.Refused, match="not paired"):
        link.send(sender, link.Peer("abc", "Nobody", "127.0.0.1", 9), link.hello())


def test_a_plain_connection_gets_nothing(paired):
    sender, peer, node, seen = paired
    with socket.create_connection(("127.0.0.1", peer.port), timeout=5) as plain:
        data = json.dumps(link.add("https://example.test/x")).encode()
        plain.sendall(struct.pack(">I", len(data)) + data)
        try:
            answer = plain.recv(4096)
        except OSError:
            answer = b""
    assert b"ok" not in answer and seen == []
    assert link.send(sender, peer, link.hello())["ok"]


def test_a_client_stuck_in_the_handshake_blocks_no_one(paired):
    sender, peer, node, seen = paired
    with socket.create_connection(("127.0.0.1", peer.port), timeout=5):      # connects and says nothing
        started = time.monotonic()
        assert link.send(sender, peer, link.hello())["ok"]
        assert time.monotonic() - started < 5


def test_a_tls_client_without_a_key_is_refused(paired):
    sender, peer, node, seen = paired
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection(("127.0.0.1", peer.port), timeout=5) as plain:
        with pytest.raises(OSError):
            context.wrap_socket(plain)


# ---- away ----------------------------------------------------------------------------------------------------

def test_a_collector_that_is_not_there_is_away_not_refused(paired, collector):
    sender, peer, node, seen = paired
    collector[1].stop()
    with pytest.raises(link.Away, match="could not be reached"):
        link.send(sender, peer, link.hello(), timeout=3)


def test_a_collector_that_never_answers_is_away(paired, monkeypatch):
    sender, peer, node, seen = paired
    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen(1)
    try:
        with pytest.raises(link.Away, match="did not answer"):
            link.send(sender, link.Peer(peer.id, peer.name, "127.0.0.1", silent.getsockname()[1]), link.hello(),
                      timeout=0.5)
    finally:
        silent.close()


# ---- listening (95B) -----------------------------------------------------------------------------------------

def test_nothing_ticked_means_nothing_listens(tmp_path):
    node = link.Node(str(tmp_path), MemoryKeys())
    listener = link.Listener(node, lambda m, p: {"ok": True}, [], port=0).start()
    assert listener.bound == [] and listener.problems == {}
    listener.stop()


def test_it_listens_only_on_the_ticked_address(collector):
    node, listener, port, seen = collector
    assert listener.bound == [("127.0.0.1", port)]
    assert [s.getsockname()[0] for s in listener._sockets] == ["127.0.0.1"]      # not 0.0.0.0


def test_an_address_this_pc_does_not_have_is_named_and_the_others_still_listen(tmp_path):
    node = link.Node(str(tmp_path), MemoryKeys())
    listener = link.Listener(node, lambda m, p: {"ok": True}, ["192.0.2.77", "127.0.0.1", "not an address"],
                             port=0).start()
    try:
        assert [address for address, _ in listener.bound] == ["127.0.0.1"]
        assert set(listener.problems) == {"192.0.2.77", "not an address"}
    finally:
        listener.stop()


def test_after_stop_nothing_listens(collector, paired):
    sender, peer, node, seen = paired
    collector[1].stop()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", peer.port), timeout=2).close()


def test_this_pcs_addresses_for_the_tick_list(monkeypatch):
    def lookup(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.1.20", 0)),
                (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("fe80::1%12", 0, 0, 12)),
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.1.20", 0)),
                (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("2001:db8::5", 0, 0, 0))]
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    assert link.local_addresses() == ["127.0.0.1", "192.168.1.20", "2001:db8::5"]


# ---- what is kept where --------------------------------------------------------------------------------------

def test_the_file_holds_no_key_and_a_restart_remembers_everything(paired, tmp_path):
    sender, peer, node, seen = paired
    text = (tmp_path / "sender" / "link.json").read_text(encoding="utf-8")
    key = sender.key_of(node.id)
    assert key.hex() not in text and "half" not in text
    assert json.loads(text)["peers"] == [{"id": node.id, "name": "Collector", "host": "127.0.0.1", "port": peer.port}]
    assert sender.keys.held == {f"link-key-{node.id}": key.hex()}

    again = link.Node(str(tmp_path / "sender"), sender.keys)
    assert (again.id, again.name) == (sender.id, "Laptop")
    assert link.send(again, again.peer(node.id), link.hello())["ok"]


def test_a_new_accipio_gets_an_id_and_this_pcs_name(tmp_path):
    one, two = link.Node(str(tmp_path / "1"), MemoryKeys()), link.Node(str(tmp_path / "2"), MemoryKeys())
    assert one.id != two.id and len(one.id) == 16
    assert one.name == socket.gethostname()


def test_a_broken_file_starts_afresh_instead_of_crashing(tmp_path):
    (tmp_path / "link.json").write_text("{ not json", encoding="utf-8")
    node = link.Node(str(tmp_path), MemoryKeys())
    assert node.id and node.peers() == []


def test_a_key_that_cannot_be_kept_means_no_pairing(collector, tmp_path):
    node, listener, port, seen = collector

    class NoKeyring(MemoryKeys):
        def set(self, name, value):
            raise link.LinkUnavailable("no credential store here")
    other = link.Node(str(tmp_path / "other"), NoKeyring())
    with pytest.raises(link.LinkUnavailable):
        link.pair(other, "127.0.0.1", port, node.begin_pairing())
    assert other.peers() == []


def test_the_system_key_store_goes_through_the_keyring_and_never_to_a_file(monkeypatch, tmp_path):
    from redactor_common.core import secret_store

    class Vault:
        def __init__(self):
            self.held = {}

        def get_password(self, service, name):
            return self.held.get((service, name))

        def set_password(self, service, name, value):
            self.held[(service, name)] = value

        def delete_password(self, service, name):
            self.held.pop((service, name), None)
    monkeypatch.undo()                                      # the real SystemKeys, on a stand-in vault
    vault = Vault()
    monkeypatch.setattr(secret_store, "_backend_override", vault)
    monkeypatch.setattr(secret_store, "_fallback_dir_override", tmp_path)
    monkeypatch.setattr(secret_store, "_allow_fallback", True)   # even when the family's file is allowed
    keys = link.SystemKeys()
    keys.set("link-key-abc", "00ff")
    assert vault.held == {("redactor/accipio", "link-key-abc"): "00ff"} and keys.get("link-key-abc") == "00ff"
    keys.delete("link-key-abc")
    assert vault.held == {} and keys.get("link-key-abc") == ""

    class Broken(Vault):
        def set_password(self, service, name, value):
            raise RuntimeError("locked")
    monkeypatch.setattr(secret_store, "_backend_override", Broken())
    with pytest.raises(link.LinkUnavailable, match="credential store"):
        keys.set("link-key-abc", "00ff")
    assert list(tmp_path.iterdir()) == []


# ---- a Python without pre-shared keys ------------------------------------------------------------------------

def test_a_python_without_psk_says_so_plainly(monkeypatch, tmp_path):
    monkeypatch.setattr(ssl, "HAS_PSK", False)
    assert not link.available() and "Python 3.13 or newer" in link.why_unavailable()
    node = link.Node(str(tmp_path), MemoryKeys())           # knowing who we are still works
    with pytest.raises(link.LinkUnavailable, match="3.13"):
        node.begin_pairing()
    with pytest.raises(link.LinkUnavailable):
        link.Listener(node, lambda m, p: {}, ["127.0.0.1"], port=0).start()
    with pytest.raises(link.LinkUnavailable):
        link.pair(node, "127.0.0.1", 9, WRONG)
    node.remember(link.Peer("abc", "Collector", "127.0.0.1", 9), b"\x02" * 32)
    with pytest.raises(link.LinkUnavailable):
        link.send(node, node.peer("abc"), link.hello())
