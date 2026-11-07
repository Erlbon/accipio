"""aria2's RPC connection failing: 2026-11-07, the first start after installing over a running Accipio crashed in
Queue._reconcile with ConnectionResetError (aria2 had answered getVersion, then reset the connection on tellActive).
A dropped connection on a read call is tried again; one that stays down is an Aria2Error, which the window turns into
"Accipio cannot start its download engine" instead of the crash dialog."""
import io
import json

import pytest

from accipio.core import aria2


class Opener:
    """Fails with each error in `errors` in turn, then answers `result`; records the methods asked for."""

    def __init__(self, errors, result=None):
        self.errors, self.result, self.methods = list(errors), result, []

    def open(self, request, timeout=None):
        self.methods.append(json.loads(request.data)["method"])
        if self.errors:
            raise self.errors.pop(0)
        return io.BytesIO(json.dumps({"jsonrpc": "2.0", "id": 1, "result": self.result}).encode())


def engine(opener):
    a = aria2.Aria2("aria2c.exe", "session.txt")
    a.port, a.secret, a._opener = 6800, "s", opener
    a.RETRY_PAUSE = 0
    return a


def test_a_reset_connection_on_a_read_call_is_tried_again():
    opener = Opener([ConnectionResetError(10054, "reset"), ConnectionAbortedError(10053, "aborted")], result=[])
    assert engine(opener).call("tellActive") == []
    assert opener.methods == ["aria2.tellActive"] * 3


def test_a_connection_that_stays_down_is_an_aria2_error():
    opener = Opener([ConnectionResetError(10054, "reset")] * 10)
    with pytest.raises(aria2.Aria2Error, match="tellActive"):
        engine(opener).call("tellActive")


class Aria2Fake:
    """Answers by method, as aria2 does: `script` is what happens to each call in turn, "drop" (the connection is
    reset, after aria2 got the call when `got_it`), or None to answer normally."""

    def __init__(self, script, got_it=False):
        self.script, self.got_it = list(script), got_it
        self.methods, self.gids = [], set()

    def open(self, request, timeout=None):
        call = json.loads(request.data)
        method, params = call["method"], call["params"][1:]
        self.methods.append(method)
        drop = self.script.pop(0) if self.script else None
        if method == "aria2.addUri" and (drop is None or self.got_it):
            self.gids.add(params[1]["gid"])
        if drop == "drop":
            raise ConnectionResetError(10054, "An existing connection was forcibly closed by the remote host")
        if method == "aria2.tellStatus":
            if params[0] not in self.gids:
                raise urllib_error(f"GID {params[0]} is not found")
            return io.BytesIO(json.dumps({"result": {"gid": params[0], "status": "active"}}).encode())
        return io.BytesIO(json.dumps({"result": params[1]["gid"] if method == "aria2.addUri" else "OK"}).encode())


def urllib_error(message):
    import urllib.error
    body = io.BytesIO(json.dumps({"error": {"code": 1, "message": message}}).encode())
    return urllib.error.HTTPError("http://127.0.0.1/jsonrpc", 400, "Bad Request", {}, body)


def test_each_download_gets_its_own_id_from_accipio():
    fake = Aria2Fake([None])
    gid = engine(fake).add("https://example.org/a.zip", "D:\\d", "a.zip")
    assert len(gid) == 16 and int(gid, 16) and fake.gids == {gid}


def test_an_add_dropped_before_aria2_got_it_is_sent_again():
    """2026-11-07: aria2 reset the connection during addUri, so Accipio answered Chrome "not taken" and the
    download went to Chrome's own window. With its own id Accipio can ask whether aria2 has it."""
    fake = Aria2Fake(["drop"])
    gid = engine(fake).add("https://example.org/a.zip", "D:\\d", "a.zip")
    assert fake.methods == ["aria2.addUri", "aria2.tellStatus", "aria2.addUri"] and fake.gids == {gid}


def test_an_add_dropped_after_aria2_got_it_is_not_sent_twice():
    fake = Aria2Fake(["drop"], got_it=True)
    gid = engine(fake).add("https://example.org/a.zip", "D:\\d", "a.zip")
    assert fake.methods == ["aria2.addUri", "aria2.tellStatus"] and fake.gids == {gid}


def test_an_add_that_keeps_dropping_is_an_aria2_error():
    fake = Aria2Fake(["drop"] * 20)
    with pytest.raises(aria2.Aria2Error, match="addUri"):
        engine(fake).add("https://example.org/a.zip", "D:\\d", "a.zip")
    assert fake.methods.count("aria2.addUri") == 1 + aria2.Aria2.RETRIES


def test_starting_over_adds_the_same_safe_way(monkeypatch):
    fake = Aria2Fake([None])
    a = engine(fake)
    answers = {"tellStatus": {"files": [{"uris": [{"uri": "https://example.org/a.zip"}], "path": ""}], "dir": "D:\\d"},
               "getOption": {"dir": "D:\\d", "out": "a.zip"}}
    monkeypatch.setattr(a, "call", lambda method, *p, **k: answers.get(method))     # the old download's details
    gid = a.restart("old")
    assert fake.methods == ["aria2.addUri"] and fake.gids == {gid}


def test_a_call_that_changes_something_is_not_sent_twice():
    """addUri may have reached aria2 before the connection dropped: a second one could start the download twice."""
    opener = Opener([ConnectionResetError(10054, "reset")], result="gid")
    with pytest.raises(aria2.Aria2Error, match="addUri"):
        engine(opener).call("addUri", ["https://example.org/a.zip"], {})
    assert opener.methods == ["aria2.addUri"]
