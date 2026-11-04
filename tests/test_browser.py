"""The browser link: the local pipe, the native host, registration, and the extension's fixed ID."""
import base64
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys

import pytest

from accipio import native_host
from accipio.core import browser, ipc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTENSION = os.path.join(ROOT, "extension")


@pytest.fixture
def server(tmp_path):
    seen = []

    def handler(message):
        seen.append(message)
        if message.get("type") == "boom":
            raise ValueError("handler trouble")
        return {"ok": True, "echo": message}
    running = ipc.Server(str(tmp_path), handler).start()
    yield str(tmp_path), seen
    running.stop()


# ---- the pipe --------------------------------------------------------------------------------------------

def test_messages_go_through_and_come_back(server):
    folder, seen = server
    assert ipc.send(folder, {"type": "add", "url": "https://a.org/x"}) == {"ok": True, "echo": {"type": "add", "url": "https://a.org/x"}}
    assert ipc.is_running(folder) and seen[0]["url"] == "https://a.org/x"


def test_a_handler_error_is_an_answer_not_a_crash(server):
    folder, _ = server
    assert ipc.send(folder, {"type": "boom"}) == {"ok": False, "error": "handler trouble"}
    assert ipc.send(folder, {"type": "ping"})["ok"]


def test_a_wrong_key_is_refused_and_the_server_carries_on(server, tmp_path):
    folder, seen = server
    with open(os.path.join(folder, "ipc.key"), "rb") as f:
        real = f.read()
    with open(os.path.join(folder, "ipc.key"), "wb") as f:
        f.write(b"x" * 32)
    with pytest.raises(OSError):
        ipc.send(folder, {"type": "add", "url": "https://evil/"})
    with open(os.path.join(folder, "ipc.key"), "wb") as f:
        f.write(real)
    assert ipc.send(folder, {"type": "ping"})["ok"] and all(m.get("url") != "https://evil/" for m in seen)


def test_nothing_running_means_oserror(tmp_path):
    with pytest.raises(OSError):
        ipc.send(str(tmp_path), {"type": "ping"})
    assert not ipc.is_running(str(tmp_path))


def test_each_config_folder_has_its_own_line(tmp_path):
    assert ipc.address(str(tmp_path / "a")) != ipc.address(str(tmp_path / "b"))


# ---- the native host -------------------------------------------------------------------------------------

def frame(message):
    data = json.dumps(message).encode()
    return struct.pack("=I", len(data)) + data


def test_chromes_framing_both_ways():
    out = io.BytesIO()
    native_host.write_message(out, {"ok": True, "name": "æøå"})
    assert native_host.read_message(io.BytesIO(out.getvalue())) == {"ok": True, "name": "æøå"}
    assert native_host.read_message(io.BytesIO(b"")) is None


def test_the_host_relays_to_the_running_app(server):
    folder, seen = server
    out = io.BytesIO()
    assert native_host.main(io.BytesIO(frame({"type": "add", "url": "https://a.org/f.zip"})), out, folder) == 0
    assert native_host.read_message(io.BytesIO(out.getvalue()))["ok"] and seen[-1]["url"] == "https://a.org/f.zip"
    assert seen[-1]["via"] == "browser"                  # how Accipio tells the extension from a second start


def test_a_ping_does_not_start_the_app(tmp_path):
    launched = []
    reply = native_host.relay({"type": "ping"}, str(tmp_path), launch=lambda: launched.append(1))
    assert reply == {"ok": False, "error": "Accipio is not running"} and launched == []


def test_a_download_starts_the_app_and_waits_for_it(tmp_path):
    folder = str(tmp_path)
    started = []

    def launch():
        started.append(ipc.Server(folder, lambda m: {"ok": True, "got": m["url"]}).start())
    try:
        reply = native_host.relay({"type": "add", "url": "https://a.org/f"}, folder, launch=launch, wait=5)
        assert reply == {"ok": True, "got": "https://a.org/f"} and len(started) == 1
    finally:
        for s in started:
            s.stop()


def test_an_app_that_never_comes_says_so(tmp_path):
    reply = native_host.relay({"type": "add", "url": "u"}, str(tmp_path), launch=lambda: None, wait=0.6)
    assert reply["ok"] is False and "did not start" in reply["error"]


def test_garbage_from_the_browser_gets_an_error_answer():
    out = io.BytesIO()
    assert native_host.main(io.BytesIO(struct.pack("=I", 3) + b"{x}"), out, "unused") == 1
    assert "unreadable" in native_host.read_message(io.BytesIO(out.getvalue()))["error"]


def test_the_host_runs_as_its_own_process(server):
    """What Chrome does: start the launcher's command and talk over stdin/stdout."""
    folder, seen = server
    command = [sys.executable, "-c", f"import sys; from accipio import native_host; sys.exit(native_host.main(folder={folder!r}))"]
    done = subprocess.run(command, input=frame({"type": "add", "url": "https://a.org/p"}), capture_output=True,
                          cwd=ROOT, timeout=30)
    assert done.returncode == 0, done.stderr.decode(errors="replace")
    assert native_host.read_message(io.BytesIO(done.stdout))["ok"] and seen[-1]["url"] == "https://a.org/p"


# ---- registration ----------------------------------------------------------------------------------------

def test_registration_writes_launcher_manifest_and_registry(tmp_path):
    writes = {}

    def registry(key, value):
        changed = writes.get(key) != value
        writes[key] = value
        return changed
    folder = str(tmp_path / "config")
    changed = browser.register(folder, frozen=False, executable=sys.executable, registry=registry)
    manifest_path = os.path.join(folder, f"{browser.HOST}.json")
    manifest = json.load(open(manifest_path, encoding="utf-8"))
    assert manifest["allowed_origins"] == [f"chrome-extension://{browser.EXTENSION_ID}/"] and manifest["type"] == "stdio"
    launcher = open(manifest["path"], encoding="utf-8").read()
    assert "accipio.native_host" in launcher and sys.executable in launcher
    if sys.platform == "win32":
        assert set(writes) == set(browser.REGISTRY_KEYS) and set(writes.values()) == {manifest_path}
        assert any("Google" in c for c in changed) and any("Edge" in c for c in changed)
    assert browser.register(folder, frozen=False, executable=sys.executable, registry=registry) == []   # nothing changed


def test_a_frozen_build_points_at_the_exe(tmp_path):
    text = browser.launcher_text(True, r"C:\Apps\Accipio\Accipio.exe", "unused")
    assert "Accipio.exe" in text and "--native-host" in text and "PYTHONPATH" not in text


@pytest.mark.skipif(sys.platform != "win32", reason="pythonw exists on Windows")
def test_the_host_never_runs_under_pythonw(tmp_path):
    fake = tmp_path / "py"
    fake.mkdir()
    for name in ("python.exe", "pythonw.exe"):
        (fake / name).write_bytes(b"")
    browser.register(str(tmp_path / "c"), frozen=False, executable=str(fake / "pythonw.exe"), registry=lambda k, v: False)
    launcher = open(os.path.join(str(tmp_path / "c"), "native-host.cmd"), encoding="utf-8").read()
    assert str(fake / "python.exe") in launcher and "pythonw" not in launcher


# ---- the extension ---------------------------------------------------------------------------------------

def test_the_extensions_key_gives_the_id_the_host_allows():
    manifest = json.load(open(os.path.join(EXTENSION, "manifest.json"), encoding="utf-8"))
    digest = hashlib.sha256(base64.b64decode(manifest["key"])).hexdigest()[:32]
    assert "".join(chr(ord("a") + int(c, 16)) for c in digest) == browser.EXTENSION_ID
    assert {"downloads", "nativeMessaging", "contextMenus", "cookies"} <= set(manifest["permissions"])


def test_the_extension_talks_to_the_registered_host_name():
    with open(os.path.join(EXTENSION, "common.js"), encoding="utf-8") as f:
        assert f'"{browser.HOST}"' in f.read()


@pytest.mark.skipif(shutil.which("node") is None, reason="no node")
@pytest.mark.parametrize("name", ["background.js", "common.js", "popup.js"])
def test_the_extension_scripts_parse(name):
    done = subprocess.run(["node", "--check", os.path.join(EXTENSION, name)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_a_client_stuck_in_the_handshake_blocks_no_one(server):
    """A connection that never answers the key challenge must not hold up the next one (it once could:
    the key check ran inside accept())."""
    from multiprocessing.connection import Client
    folder, _ = server
    stuck = Client(ipc.address(folder))                 # connects, then never takes part in the handshake
    try:
        assert ipc.send(folder, {"type": "ping"}, timeout=5)["ok"]
    finally:
        stuck.close()


def test_a_silent_app_times_out_instead_of_hanging(tmp_path):
    from multiprocessing.connection import Listener
    folder = str(tmp_path)
    mute = Listener(ipc.address(folder))                # accepts nothing: like a hung Accipio
    try:
        import time
        started = time.monotonic()
        with pytest.raises(OSError):
            ipc.send(folder, {"type": "ping"}, timeout=1)
        assert time.monotonic() - started < 5
    finally:
        mute.close()
