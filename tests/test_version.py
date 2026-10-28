"""The build number: same form as the Redactor apps, bumped by one script, matched by the changelog."""
import datetime
import os
import re

import pytest

import bump_version
from accipio.core import version

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_the_build_number_has_the_redactor_form():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}#\d{2,}", version.APP_VERSION)
    datetime.date.fromisoformat(version.APP_VERSION.split("#")[0])             # a real date
    assert version.tag_of("2026-10-28#03") == "v2026-10-28-03"


def test_the_same_day_counts_up_and_a_new_day_starts_again_at_one():
    day = datetime.date(2026, 10, 28)
    assert bump_version.next_version("2026-10-28", 1, day) == "2026-10-28#02"
    assert bump_version.next_version("2026-10-27", 7, day) == "2026-10-28#01"
    assert bump_version.pep440_from("2026-10-28#03") == "2026.10.28.3"


def test_the_script_rewrites_only_the_build_line_and_keeps_pyproject_in_step(tmp_path):
    v = tmp_path / "version.py"
    v.write_text('APP_NAME = "X"\nAPP_VERSION = "2026-10-28#05"\n', encoding="utf-8")
    p = tmp_path / "pyproject.toml"
    p.write_text('[project]\nname = "x"\nversion = "2026.10.28.5"\n', encoding="utf-8")
    assert bump_version.bump(v, p, today=datetime.date(2026, 10, 28)) == ("2026-10-28#05", "2026-10-28#06")
    assert 'APP_VERSION = "2026-10-28#06"' in v.read_text(encoding="utf-8")
    assert 'version = "2026.10.28.6"' in p.read_text(encoding="utf-8")


def test_a_file_without_a_build_line_is_refused_with_a_clear_message(tmp_path):
    v = tmp_path / "version.py"
    v.write_text("APP_NAME = 'x'\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="APP_VERSION"):
        bump_version.bump(v, None)


def test_the_repository_files_agree_with_each_other():
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
        project = re.search(r'(?m)^version = "(.*)"$', f.read()).group(1)
    assert project == bump_version.pep440_from(version.APP_VERSION)


def test_the_changelog_has_a_section_for_the_current_build():
    with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
        text = f.read()
    assert re.search(rf"(?m)^## {re.escape(version.APP_VERSION)} -- \S", text), "add a CHANGELOG section for this build"
    top = re.search(r"(?m)^## (\d{4}-\d{2}-\d{2}#\d+)", text).group(1)
    assert top == version.APP_VERSION                                         # the newest section is the current build
