"""Help > Check for Updates and Credits (2026-11-04#03): the family's update controller, but the new version is
installed by running the new installer, which closes Accipio, updates it and starts it again."""
import json
import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from accipio.gui import updates  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def answer(tag="v2099-01-01-01", assets=None):
    if assets is None:
        assets = [{"name": "Accipio-2099-01-01-01-windows-x64.zip", "size": 10, "digest": "sha256:" + "a" * 64,
                   "browser_download_url": "https://github.com/Erlbon/accipio/releases/download/x/a.zip"},
                  {"name": "accipio-setup.exe", "size": 1234, "digest": "sha256:" + "b" * 64,
                   "browser_download_url": "https://github.com/Erlbon/accipio/releases/download/x/accipio-setup.exe"}]
    return json.dumps({"tag_name": tag, "html_url": "https://github.com/Erlbon/accipio/releases/tag/" + tag,
                       "body": "notes", "assets": assets}).encode()


def fake_fetch(body):
    return lambda request: body             # the family's FetchFn: a request in, the answer's bytes out


def controller(app, tmp_path, installed=True, frozen=True, launched=None, body=None):
    window = QWidget()
    exe = tmp_path / "Accipio" / "Accipio.exe"
    if installed:                      # as a real install: Inno's uninstaller sits beside the exe, which
        exe.parent.mkdir(parents=True, exist_ok=True)       # redactor_common 2026-11-06-01 takes as "installed"
        (exe.parent / "unins000.exe").write_bytes(b"MZ")
    return updates.AccipioUpdates(window, tmp_path / "accipio_updates.json", fetch=fake_fetch(body or answer()),
                                  frozen=frozen, exe=exe,
                                  installed_dir=lambda: str(exe.parent) if installed else "",
                                  launch=(launched.append if launched is not None else None)), window


def test_the_newest_release_offers_the_fixed_name_installer(app, tmp_path):
    """2026-11-06: with the family code's own installer support, an installed copy looked for
    "accipio-setup-setup.exe", found nothing it could verify, and only offered the download page."""
    ctl, _ = controller(app, tmp_path)
    release = ctl._latest()
    assert release.asset_name == "accipio-setup.exe" and release.asset_size == 1234 and release.downloadable


def test_only_an_installed_copy_updates_itself(app, tmp_path):
    ctl, _ = controller(app, tmp_path)
    assert ctl.can_update() == (True, "")
    zip_copy, _ = controller(app, tmp_path, installed=False)
    can, reason = zip_copy.can_update()
    assert not can and "installer" in reason
    source, _ = controller(app, tmp_path, frozen=False)
    assert not source.can_update()[0]


def test_updating_runs_the_new_installer_and_closes_accipio(app, tmp_path, monkeypatch):
    launched = []
    ctl, window = controller(app, tmp_path, launched=launched)
    release = ctl._latest()
    got = tmp_path / "dl" / "accipio-setup.exe"

    def fake_download(release, dest, progress=None, cancel=None):
        got.parent.mkdir(parents=True, exist_ok=True)
        got.write_bytes(b"MZ")
        return got
    monkeypatch.setattr(updates.selfupdate, "download", fake_download)
    monkeypatch.setattr(updates, "run_work_with_progress", lambda w, text, work, total, title="": work(None, None))
    closed = []
    monkeypatch.setattr(ctl, "_quit", lambda: closed.append(1))
    assert ctl.update_to(release)
    command = launched[0]
    assert command[0] == str(got) and "/SILENT" in command and "/relaunch=yes" in command
    assert closed == [1]


def test_the_installer_relaunches_after_a_quiet_update_and_keeps_its_app_id():
    script = (ROOT / "installer" / "accipio.iss").read_text(encoding="utf-8")
    assert "{param:relaunch|no}" in script
    assert updates.INSTALLER_APP_ID in script
    build = (ROOT / "build_exe.bat").read_text(encoding="utf-8")
    assert "accipio-setup.exe" in build


def test_credits_are_bundled_and_list_what_the_exe_holds():
    credits = (ROOT / "CREDITS.md").read_text(encoding="utf-8")
    for name in ("aria2", "PyQt6", "Qt", "yt-dlp", "redactor_common"):
        assert name in credits
    assert re.search(r'\("CREDITS\.md", "\."\)', (ROOT / "accipio.spec").read_text(encoding="utf-8"))
