"""Help > Check for Updates is the family's update framework (redactor_common docs/update-framework.md, 2026-11-07):
Accipio only declares how each copy was put on the PC and how its releases are built. These tests hold the pieces
that are Accipio's own to the framework's contract; the gate in `python -m redactor_common.release publish` then
updates a real installed copy of the previous release before a new one becomes Latest."""
import io
import json
import re
import sys
from pathlib import Path

from redactor_common import release
from redactor_common.core import update_manifest

from accipio.core import settings
from accipio.core.version import APP_VERSION
from accipio.gui import app

ROOT = Path(__file__).resolve().parents[1]
APP_ID = "6C0E5D8A-3F7B-4B8E-9A51-2D4C7E1A9B30"


def release_config():
    """The release tool's own reading of pyproject.toml (it refuses a bad [tool.redactor-release])."""
    return release.load_config(ROOT)


def test_the_installer_follows_the_update_contract():
    script = (ROOT / "installer" / "accipio.iss").read_text(encoding="utf-8")
    assert script.rstrip().endswith("#include UpdateContract")       # at the end: after Accipio's own [Code]
    assert '#define UpdateExe "Accipio.exe"' in script and '#define UpdateQuitArgs "--quit"' in script
    assert '#define UpdateQuitCheck "InstalledKnowsQuit"' in script and "function InstalledKnowsQuit" in script
    assert "{param:relaunch" not in script and "function PrepareToInstall" not in script   # the contract's now
    assert "{{" + APP_ID + "}" in script and release_config().installer_app_id == "{" + APP_ID + "}"
    build = (ROOT / "build_exe.bat").read_text(encoding="utf-8")
    assert "redactor_common.release contract-path" in build and "/DUpdateContract=" in build
    assert "accipio-setup.exe" not in build                        # no fixed-name installer: the manifest names it


def test_the_folder_build_declares_the_zip_channel():
    spec = (ROOT / "accipio.spec").read_text(encoding="utf-8")
    assert re.search(r'os\.path\.join\(DISTPATH, "Accipio", "update-channel"\)', spec)
    assert 'channel.write("windows-zip\\n")' in spec


def test_the_release_ships_the_installer_and_the_zip():
    cfg = release_config()
    assert (cfg.app, cfg.repo, cfg.exe, cfg.version) == ("accipio", "Erlbon/accipio", "Accipio.exe", APP_VERSION)
    assert set(cfg.packages) == {update_manifest.WINDOWS_INSTALLER, update_manifest.WINDOWS_ZIP}
    build = APP_VERSION.replace("#", "-")
    assert cfg.package_paths()[update_manifest.WINDOWS_INSTALLER] == ROOT / "dist" / f"Accipio-{build}-windows-x64-setup.exe"
    assert cfg.zip_folder == "dist/Accipio"
    # 81A: the gate runs Accipio the way a user does, and stops it the way the installer does
    assert (cfg.gate_run, cfg.gate_quit, cfg.gate_crash_log) == (["--minimized"], ["--quit"], "crash.log")
    script = (ROOT / "installer" / "accipio.iss").read_text(encoding="utf-8")
    assert '#define UpdateQuitArgs "' + " ".join(cfg.gate_quit) + '"' in script


def test_the_update_command_line_is_answered_before_anything_starts(monkeypatch, tmp_path):
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(app.ipc, "is_running", lambda folder: (_ for _ in ()).throw(AssertionError("no window")))
    assert app.main(["Accipio.exe", "--version"]) == 0
    assert out.getvalue().strip() == APP_VERSION
    result = tmp_path / "check.json"

    def offline(_request):
        raise OSError("no network in tests")
    monkeypatch.setattr("redactor_common.core.lookup_client.make_default_fetch", lambda *a, **k: offline)
    assert app.main(["Accipio.exe", "--check-update", "--result-file", str(result)]) == 2
    answer = json.loads(result.read_text(encoding="utf-8"))
    assert answer["action"] == "error" and answer["channel"] == update_manifest.SOURCE


def test_a_scratch_profile_moves_all_of_accipios_files(monkeypatch, tmp_path):
    monkeypatch.setenv("REDACTOR_CONFIG_DIR", str(tmp_path / "profile"))
    assert settings.config_dir() == str(tmp_path / "profile")
    monkeypatch.delenv("REDACTOR_CONFIG_DIR")
    assert settings.config_dir().endswith("Accipio") or settings.config_dir().endswith("accipio")
