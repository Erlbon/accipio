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
