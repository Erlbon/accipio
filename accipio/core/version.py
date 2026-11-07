"""The single source of truth for the program's name and build number.

The build number has the same form as in the Redactor apps: ``YYYY-MM-DD#NN``, the date of the check-in and a
counter that restarts at 01 each day. It is changed only by ``python bump_version.py`` (never by hand), in the same
check-in as the code change and a matching CHANGELOG.md entry.
"""
from __future__ import annotations

APP_NAME = "Accipio"
APP_VERSION = "2026-11-07#05"
APP_REPO_URL = "https://github.com/Erlbon/accipio"


def tag_of(version: str = APP_VERSION) -> str:
    """The tag of a build (``v2026-10-28-01``): for telling builds apart, not a release."""
    return "v" + version.replace("#", "-")
