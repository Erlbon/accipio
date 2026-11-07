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


def test_a_call_that_changes_something_is_not_sent_twice():
    """addUri may have reached aria2 before the connection dropped: a second one could start the download twice."""
    opener = Opener([ConnectionResetError(10054, "reset")], result="gid")
    with pytest.raises(aria2.Aria2Error, match="addUri"):
        engine(opener).call("addUri", ["https://example.org/a.zip"], {})
    assert opener.methods == ["aria2.addUri"]
