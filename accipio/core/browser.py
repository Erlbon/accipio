"""Telling Chrome (and Edge) where Accipio's native host is.

Chrome finds a native messaging host through a small JSON file (its "host manifest"): on Windows the registry
key ``HKCU\\Software\\Google\\Chrome\\NativeMessagingHosts\\no.erlbon.accipio`` holds its path; on Linux the
file sits in the browser's ``NativeMessagingHosts`` folder. The manifest names the program to start (a
launcher in Accipio's config folder) and the one extension allowed to start it.

Accipio does this at every start; it only writes what changed, and only under the current user (no admin).
The extension's ID is fixed by the public key in ``extension/manifest.json``.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Callable, List, Optional

HOST = "no.erlbon.accipio"
EXTENSION_ID = "ongnefecmhlaofpaaoehcdmdinnlgble"
REGISTRY_KEYS = [rf"Software\Google\Chrome\NativeMessagingHosts\{HOST}",
                 rf"Software\Microsoft\Edge\NativeMessagingHosts\{HOST}"]
LINUX_FOLDERS = ["~/.config/google-chrome", "~/.config/chromium", "~/.config/microsoft-edge", "~/.config/BraveSoftware/Brave-Browser"]


def launcher_text(frozen: bool, executable: str, package_root: str) -> str:
    """The program Chrome starts: the exe in host mode, or this Python running ``accipio.native_host``."""
    if sys.platform == "win32":
        if frozen:
            return f'@echo off\r\n"{executable}" --native-host %*\r\n'
        return f'@echo off\r\nset "PYTHONPATH={package_root}"\r\n"{executable}" -m accipio.native_host %*\r\n'
    if frozen:
        return f'#!/bin/sh\nexec "{executable}" --native-host "$@"\n'
    return f'#!/bin/sh\nPYTHONPATH="{package_root}" exec "{executable}" -m accipio.native_host "$@"\n'


def host_manifest(launcher: str) -> dict:
    return {"name": HOST, "description": "Accipio download manager", "path": launcher, "type": "stdio",
            "allowed_origins": [f"chrome-extension://{EXTENSION_ID}/"]}


def _write_if_changed(path: str, text: str) -> bool:
    try:
        with open(path, encoding="utf-8", newline="") as f:
            if f.read() == text:
                return False
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return True


def _windows_registry(key_path: str, value: str) -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            if winreg.QueryValue(key, None) == value:
                return False
    except OSError:
        pass
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        winreg.SetValue(key, None, winreg.REG_SZ, value)          # the key's default value: the manifest path
    return True


def register(folder: str, *, frozen: Optional[bool] = None, executable: Optional[str] = None,
             registry: Callable[[str, str], bool] = None, home: Optional[str] = None) -> List[str]:
    """Write the launcher and the host manifest, and point the browsers at it. Returns what was changed."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    executable = executable or sys.executable
    if not frozen and os.path.basename(executable).lower() == "pythonw.exe":
        console = os.path.join(os.path.dirname(executable), "python.exe")
        executable = console if os.path.isfile(console) else executable    # the host talks over stdin/stdout
    package_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    changed = []
    launcher = os.path.join(folder, "native-host.cmd" if sys.platform == "win32" else "native-host.sh")
    if _write_if_changed(launcher, launcher_text(frozen, executable, package_root)):
        changed.append(launcher)
        if sys.platform != "win32":
            os.chmod(launcher, 0o755)
    manifest_text = json.dumps(host_manifest(launcher), indent=2)
    manifest = os.path.join(folder, f"{HOST}.json")
    if _write_if_changed(manifest, manifest_text):
        changed.append(manifest)
    if sys.platform == "win32":
        write = registry or _windows_registry
        changed += [f"HKCU\\{k}" for k in REGISTRY_KEYS if write(k, manifest)]
    else:
        for browser in LINUX_FOLDERS:
            base = os.path.expanduser(browser) if home is None else os.path.join(home, browser.replace("~/", ""))
            if os.path.isdir(base):
                target = os.path.join(base, "NativeMessagingHosts", f"{HOST}.json")
                if _write_if_changed(target, manifest_text):
                    changed.append(target)
    return changed
