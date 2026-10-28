"""The aria2 engine, run for real against the local test server (skipped where aria2c is missing)."""
import os
import time

import pytest

from accipio.core import aria2
from tests.conftest import PAYLOAD

EXE = aria2.find_aria2c()
pytestmark = pytest.mark.skipif(EXE is None, reason="no aria2c (run python tools/fetch_engines.py)")


@pytest.fixture
def engine(tmp_path):
    a = aria2.Aria2(EXE, str(tmp_path / "state" / "session.txt"), max_downloads=2, connections=4).start()
    yield a
    a.kill()


def test_stop_returns_at_once_and_aria2_exits_by_itself(tmp_path):
    a = aria2.Aria2(EXE, str(tmp_path / "session.txt")).start()
    started = time.monotonic()
    a.stop()
    assert time.monotonic() - started < 2.0              # not aria2c's ~4 s exit (2 s of headroom for a busy machine)
    assert a.process.wait(15) == 0                                       # a clean exit, not a kill


def wait_for(engine, gid, states, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = engine.status(gid)
        if s.state in states:
            return s
        time.sleep(0.05)
    raise AssertionError(f"still {engine.status(gid).state} after {timeout}s")


def test_a_download_lands_in_the_chosen_folder_under_the_chosen_name(engine, web, tmp_path):
    folder = tmp_path / "Downloads" / "Archives" / "2026-10"            # does not exist yet
    gid = engine.add(web + "/files/payload.bin", str(folder), "release.zip")
    s = wait_for(engine, gid, {"complete", "error"})
    assert s.state == "complete", s.error_message
    assert (folder / "release.zip").read_bytes() == PAYLOAD
    assert s.total == s.done == len(PAYLOAD) and s.fraction == 1.0
    assert os.path.normcase(s.path) == os.path.normcase(str(folder / "release.zip"))


def test_an_existing_file_is_never_overwritten(engine, web, tmp_path):
    (tmp_path / "taken.bin").write_bytes(b"keep me")
    gid = engine.add(web + "/files/payload.bin", str(tmp_path), "taken.bin")
    s = wait_for(engine, gid, {"complete", "error"})
    assert s.state == "error" and s.error_code == aria2.FILE_EXISTS
    assert (tmp_path / "taken.bin").read_bytes() == b"keep me"
    assert not (tmp_path / "taken.1.bin").exists()                      # and no renamed copy either


def test_a_missing_file_fails_with_a_message(engine, web, tmp_path):
    gid = engine.add(web + "/nothing", str(tmp_path), "x.bin")
    s = wait_for(engine, gid, {"error"})
    assert s.error_code == aria2.NOT_FOUND and s.error_message


def wait_for_bytes(engine, gid, at_least, timeout=20):
    deadline = time.monotonic() + timeout
    while engine.status(gid).done < at_least:
        assert time.monotonic() < deadline, "no bytes arrived"
        time.sleep(0.05)


def test_pause_resume_and_remove(engine, web, tmp_path):
    gid = engine.add(web + "/slow-resumable.bin", str(tmp_path), "slow.bin")
    wait_for_bytes(engine, gid, 100_000)
    engine.pause(gid)
    paused = wait_for(engine, gid, {"paused"})
    engine.resume(gid)
    s = wait_for(engine, gid, {"complete", "error"}, timeout=30)
    assert s.state == "complete", s.error_message
    assert (tmp_path / "slow.bin").stat().st_size == 4 * 1024 * 1024 and paused.done > 0
    engine.remove(gid)
    assert gid not in [s.gid for s in engine.all()]


def test_a_server_that_cannot_resume_fails_after_a_pause_and_restart_starts_over(engine, web, tmp_path):
    gid = engine.add(web + "/slow.bin", str(tmp_path), "once.bin")
    wait_for_bytes(engine, gid, 100_000)
    engine.pause(gid)
    wait_for(engine, gid, {"paused"})
    engine.resume(gid)
    s = wait_for(engine, gid, {"error", "complete"}, timeout=30)
    assert s.state == "error" and s.error_code == aria2.CANNOT_RESUME
    again = engine.restart(gid)
    s = wait_for(engine, again, {"complete", "error"}, timeout=30)
    assert s.state == "complete", s.error_message
    assert (tmp_path / "once.bin").stat().st_size == 4 * 1024 * 1024
    assert not (tmp_path / "once.bin.aria2").exists()


def test_paused_on_add_and_listed(engine, web, tmp_path):
    gid = engine.add(web + "/slow.bin", str(tmp_path), "later.bin", paused=True)
    assert engine.status(gid).state == "paused"
    assert gid in [s.gid for s in engine.all()]


def test_limits_change_while_running(engine):
    engine.set_limits(max_downloads=1, speed_limit=500_000)
    options = engine.call("getGlobalOption")
    assert options["max-concurrent-downloads"] == "1" and options["max-overall-download-limit"] == "500000"


def test_a_refused_call_raises_with_arias_message(engine):
    with pytest.raises(aria2.Aria2Error):
        engine.status("0000000000000000")


def test_unfinished_downloads_come_back_after_a_restart(web, tmp_path):
    session = str(tmp_path / "session.txt")
    with aria2.Aria2(EXE, session) as first:
        first.add(web + "/slow.bin", str(tmp_path), "resume.bin", paused=True)
    assert os.path.getsize(session) > 0                                  # shutdown wrote the session
    with aria2.Aria2(EXE, session) as second:
        names = [os.path.basename(s.path) for s in second.all()]
        assert "resume.bin" in names


def test_the_rpc_is_local_and_needs_the_secret(engine):
    line = " ".join(engine.command_line())
    assert "--rpc-listen-all=false" in line and f"--rpc-secret={engine.secret}" in line and len(engine.secret) == 32
    engine.secret, real = "wrong", engine.secret
    try:
        with pytest.raises(aria2.Aria2Error):
            engine.call("getVersion")
    finally:
        engine.secret = real
