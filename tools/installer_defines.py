"""Print the build number as Inno Setup /D arguments, for build_exe.bat's installer step.

    python tools\\installer_defines.py   ->   /DAppVersion=2026.11.4.1 /DBuildTag=2026-11-04-01 "/DBuildName=2026-11-04#01"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accipio.core.version import APP_VERSION  # noqa: E402
from bump_version import pep440_from  # noqa: E402

print(f'/DAppVersion={pep440_from(APP_VERSION)} /DBuildTag={APP_VERSION.replace("#", "-")} "/DBuildName={APP_VERSION}"')
