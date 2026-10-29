"""Notifications: what changed, grouped per kind, told once."""
import os

from accipio.core import notices
from accipio.core.settings import Settings
from accipio.core.store import ACTIVE, COMPLETE, ERROR, PROBING, Row


def row(id, state=ACTIVE, filename="", folder="", url=None, error=""):
    return Row(id=id, url=url or f"https://a.org/f{id}.zip?x=1", filename=filename, folder=folder, state=state,
               error=error)


def test_the_first_look_announces_nothing():
    told = notices.Notices(gather=0)
    told.observe([row(1, COMPLETE), row(2, ERROR)], 0)
    assert told.due(10) == []


def test_one_finished_download_names_the_file_and_its_folder(tmp_path):
    base = str(tmp_path)
    told = notices.Notices(gather=0)
    told.observe([row(1)], 0)
    told.observe([row(1, COMPLETE, "a.zip", os.path.join(base, "Archives"))], 1)
    [notice] = told.due(1, base)
    assert (notice.title, notice.text) == ("Download finished", "a.zip\nin Archives")
    assert notice.path == os.path.join(base, "Archives", "a.zip")


def test_a_batch_becomes_one_notice_after_the_gathering_time():
    told = notices.Notices(gather=1.5)
    told.observe([], 0)
    told.observe([row(1)], 1.0)
    assert told.due(2.0) == []                            # still gathering
    told.observe([row(1), row(2), row(3), row(4), row(5)], 2.0)
    [notice] = told.due(2.5)
    assert notice.title == "5 downloads added" and notice.path == ""
    assert notice.text.splitlines() == ["f1.zip", "f2.zip", "f3.zip", "and 2 more"]
    assert told.due(10) == []                             # told once


def test_each_kind_is_its_own_notice_and_a_state_is_told_once():
    told = notices.Notices(gather=0)
    told.observe([row(1), row(2)], 0)
    told.observe([row(1, COMPLETE, "a.zip"), row(2, ERROR, "b.zip", error="404 Not Found")], 1)
    told.observe([row(1, COMPLETE, "a.zip"), row(2, ERROR, "b.zip", error="404 Not Found")], 2)
    finished, failed = told.due(3)
    assert finished.kind == notices.FINISHED and (failed.title, failed.text) == ("Download failed", "b.zip\n404 Not Found")
    assert told.due(4) == []


def test_a_name_not_known_yet_falls_back_to_the_address():
    told = notices.Notices(gather=0)
    told.observe([], 0)
    told.observe([row(1, PROBING, url="https://a.org/get/file.iso?token=1")], 1)
    assert told.due(1)[0].text == "file.iso"


def test_switched_off_kinds_are_never_told():
    told = notices.Notices(enabled=[notices.FAILED], gather=0)
    told.observe([row(1)], 0)
    told.observe([row(1, COMPLETE), row(2)], 1)
    assert told.due(5) == []


def test_a_download_removed_before_its_turn_is_left_out():
    told = notices.Notices(gather=1)
    told.observe([], 0)
    told.observe([row(1)], 0.1)
    told.observe([], 0.2)
    assert told.due(5) == []


def test_the_settings_choose_the_kinds():
    assert Settings().notice_kinds() == ["added", "finished", "failed"]
    assert Settings(notify_added=False, notify_failed=False).notice_kinds() == ["finished"]
