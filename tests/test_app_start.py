"""What the installer relies on when it starts Accipio: ``--register`` and ffmpeg found where winget puts it."""
import sys
from pathlib import Path

import pytest

from accipio.core import browser, settings, video
from accipio.gui import app


def test_register_points_the_browsers_at_accipio_and_quits_without_a_window(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "config_dir", lambda: str(tmp_path))
    monkeypatch.setattr(browser, "register", lambda folder: calls.append(folder) or [])
    import redactor_common.gui.app_bootstrap as bootstrap
    monkeypatch.setattr(bootstrap, "run_app", lambda *a, **k: (_ for _ in ()).throw(AssertionError("window opened")))
    assert app.main(["Accipio.exe", "--register"]) == 0
    assert calls == [str(tmp_path)]


def test_quit_with_nothing_running_ends_at_once_without_a_window(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "config_dir", lambda: str(tmp_path))
    import redactor_common.gui.app_bootstrap as bootstrap
    monkeypatch.setattr(bootstrap, "run_app", lambda *a, **k: (_ for _ in ()).throw(AssertionError("window opened")))
    assert app.main(["Accipio.exe", "--quit"]) == 0


def test_waiting_for_a_process_to_end(tmp_path):
    import subprocess
    quick = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.3)"])
    assert app.wait_for_exit(quick.pid, timeout=10) is True
    slow = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert app.wait_for_exit(slow.pid, timeout=0.3) is False
    finally:
        slow.kill()
        slow.wait(5)


def test_minimized_is_read_from_the_command_line():
    assert app.start_minimized(["Accipio.exe", "--minimized"])
    assert not app.start_minimized(["Accipio.exe"])


@pytest.mark.skipif(sys.platform != "win32", reason="winget is Windows only")
def test_ffmpeg_installed_by_winget_is_found(tmp_path, monkeypatch):
    links = tmp_path / "Microsoft" / "WinGet" / "Links"
    links.mkdir(parents=True)
    (links / video.FFMPEG_NAME).write_bytes(b"")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "none"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "none"))
    monkeypatch.setattr(video.sys, "executable", str(tmp_path / "python" / "python.exe"))
    found = video.find_ffmpeg()
    assert found is not None and Path(found).parent == links
    mine = tmp_path / "tools" / video.FFMPEG_NAME
    mine.parent.mkdir()
    mine.write_bytes(b"")
    assert video.find_ffmpeg(str(mine)) == str(mine)                        # picked under Settings: it wins
    assert video.find_ffmpeg(str(tmp_path / "gone" / "ffmpeg.exe")) == found   # picked, then deleted: search again


class FakeWinget:
    """Stands in for subprocess.Popen running winget: writes ``output`` to its stdout file, exits with ``code``."""

    def __init__(self, code=0, output=b"Successfully installed", runs_until=0):
        self.code, self.output, self.runs_until = code, output, runs_until
        self.calls, self.killed, self.polls = [], False, 0

    def __call__(self, args, stdout=None, **kwargs):
        self.calls.append(args)
        stdout.write(self.output)
        return self

    def poll(self):
        self.polls += 1
        return None if self.polls <= self.runs_until and not self.killed else self.code

    def kill(self):
        self.killed = True


def test_install_ffmpeg_runs_winget_quietly_and_reports_its_failure(tmp_path):
    ok = FakeWinget()
    video.install_ffmpeg(winget_exe="winget.exe", popen=ok)
    assert ok.calls == [["winget.exe", "install", "--id", "Gyan.FFmpeg", "--exact", "--silent",
                         "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity"]]
    with pytest.raises(video.FfmpegInstallError, match="No package found"):
        video.install_ffmpeg(winget_exe="winget.exe", popen=FakeWinget(code=1, output=b"...\nNo package found"))


def test_install_ffmpeg_stops_winget_on_cancel_and_says_when_winget_is_missing(monkeypatch):
    import threading
    cancel = threading.Event()
    cancel.set()
    slow = FakeWinget(runs_until=1000)
    with pytest.raises(video.FfmpegInstallCancelled):
        video.install_ffmpeg(cancel=cancel, winget_exe="winget.exe", popen=slow)
    assert slow.killed
    monkeypatch.setattr(video, "find_winget", lambda: None)
    with pytest.raises(video.FfmpegInstallError, match="winget"):
        video.install_ffmpeg(popen=FakeWinget())
