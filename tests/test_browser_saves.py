"""Files the browser had to download itself (a blob: or data: link, or one Accipio's own request failed for) are
filed by the rules once the browser has saved them (2026-11-17; seen with OCR text built inside a loc.gov tab)."""
import os

import pytest

from accipio.core import rules
from accipio.core.queue import Queue, browser_site, named_by_hand
from accipio.core.rules import Condition, Rule, SiteGroup
from accipio.core.store import COMPLETE, Store
from tests import samples
from tests.test_queue import TUE_1014, FakeEngine, FakeProber, under


@pytest.fixture
def world(tmp_path):
    base = str(tmp_path / "Downloads")
    store = Store(str(tmp_path / "state" / "queue.db"))
    rule_list = samples.sorting_rules()
    q = Queue(store, FakeEngine(), lambda: rule_list, base, prober=FakeProber(), clock=lambda: TUE_1014,
              library_set=samples.sorting_library)
    chrome = tmp_path / "ChromeDownloads"
    chrome.mkdir()
    return q, base, chrome, rule_list


def save(folder, name, content=b"text"):
    path = folder / name
    path.write_bytes(content)
    return str(path)


def test_a_file_the_browser_built_is_moved_by_the_rules_and_listed_as_finished(world):
    q, base, chrome, _ = world
    path = save(chrome, "Sun_1884_OCR.txt", b"x" * 300)
    row = q.store.get(q.file_saved(path, url="blob:https://www.loc.gov/1234", page_url="https://www.loc.gov/item/1",
                                   mime="text/plain", suggested="Sun_1884_OCR.txt"))
    assert (row.state, row.folder, row.filename, row.rules) == (COMPLETE, under(base, "Documents"), "Sun_1884_OCR.txt", "7")
    assert os.path.isfile(row.path) and not os.path.exists(path)
    assert (row.size, row.done, row.total, row.finished != "") == (300, 300, 300, True)
    assert row.url == "https://www.loc.gov/item/1" and "browser downloaded this file itself" in row.why
    assert str(chrome) in row.why                                       # "why here?" says where it came from


def test_a_name_already_taken_in_the_target_is_never_overwritten(world):
    q, base, chrome, _ = world
    os.makedirs(under(base, "Documents"))
    with open(under(base, "Documents", "notes.txt"), "wb") as f:
        f.write(b"mine")
    row = q.store.get(q.file_saved(save(chrome, "notes.txt"), url="blob:https://a.org/1", suggested="notes.txt"))
    assert row.filename == "notes (1).txt"
    with open(under(base, "Documents", "notes.txt"), "rb") as f:
        assert f.read() == b"mine"


def test_the_site_of_a_blob_link_counts_for_site_rules(world):
    q, base, chrome, rule_list = world
    library = samples.sorting_library()
    library.site_groups.append(SiteGroup("Library of Congress", ["loc.gov"]))
    library.folders.append(rules.Folder("Newspapers", "%default%/Newspapers"))
    q.library_set = lambda: library
    rule_list.insert(0, Rule("LoC", [Condition("sites", ["Library of Congress"])], target="Newspapers"))
    row = q.store.get(q.file_saved(save(chrome, "page.txt"), url="blob:https://www.loc.gov/abcd", suggested="page.txt"))
    assert (row.folder, row.rules) == (under(base, "Newspapers"), "1")


def test_a_name_typed_in_save_as_stays_where_the_user_put_it(world):
    q, base, chrome, _ = world
    path = save(chrome, "my own name.txt")
    row = q.store.get(q.file_saved(path, url="blob:https://a.org/1", suggested="export.txt"))
    assert row.path == os.path.normpath(path) and os.path.isfile(path) and row.rules == ""
    assert "name of your own" in row.why


def test_a_browser_that_asks_where_to_save_means_the_user_chose_the_place(world):
    """Pick B: the browser's own setting is read (browser_settings); here the answer is put in directly."""
    q, base, chrome, _ = world
    asked_about = []

    def asks(browser):
        asked_about.append(browser)
        return True
    q.browser_asks = asks
    path = save(chrome, "notes.txt")
    row = q.store.get(q.file_saved(path, url="blob:https://a.org/1", suggested="notes.txt", browser="Vivaldi"))
    assert row.path == os.path.normpath(path) and os.path.isfile(path) and row.rules == ""
    assert "set to ask where to save" in row.why and asked_about == ["Vivaldi"]

    q.browser_asks = lambda browser: False                              # it does not ask: the rules file it
    row = q.store.get(q.file_saved(save(chrome, "more.txt"), url="blob:https://a.org/2", suggested="more.txt"))
    assert row.folder == under(base, "Documents")


def test_browser_settings_that_cannot_be_read_never_stop_the_filing(world):
    q, base, chrome, _ = world

    def broken(browser):
        raise ValueError("odd file")
    q.browser_asks = broken
    row = q.store.get(q.file_saved(save(chrome, "notes.txt"), url="blob:https://a.org/1", suggested="notes.txt"))
    assert row.folder == under(base, "Documents")


@pytest.mark.parametrize("suggested, saved, by_hand", [
    ("notes.txt", "notes.txt", False),
    ("notes.txt", "notes (1).txt", False),              # the browser's own number for a taken name
    ("book.tar.gz", "book (2).tar.gz", False),
    ("NOTES.TXT", "notes.txt", False),
    ("sub/notes.txt", "notes.txt", False),
    ("", "anything.txt", False),                        # an older extension: nothing to compare with
    ("export.txt", "mine.txt", True),
    ("notes.txt", "notes (final).txt", True),
])
def test_named_by_hand(suggested, saved, by_hand):
    assert named_by_hand(suggested, saved) is by_hand


def test_a_file_already_where_the_rules_put_it_is_only_listed(world):
    q, base, _, _ = world
    os.makedirs(under(base, "Documents"))
    path = under(base, "Documents", "notes.txt")
    with open(path, "wb") as f:
        f.write(b"text")
    row = q.store.get(q.file_saved(path, url="blob:https://a.org/1", suggested="notes.txt"))
    assert row.path == path and row.rules == "7" and os.path.isfile(path) and "already in place" in row.why


def test_a_file_that_cannot_be_moved_stays_and_the_row_says_why(world, monkeypatch):
    q, base, chrome, _ = world
    path = save(chrome, "locked.txt")

    def refuse(source, target):
        raise PermissionError("in use by another program")
    monkeypatch.setattr("accipio.core.queue.shutil.move", refuse)
    row = q.store.get(q.file_saved(path, url="blob:https://a.org/1", suggested="locked.txt"))
    assert row.state == COMPLETE and row.path == os.path.normpath(path) and os.path.isfile(path)
    assert "could not be moved" in row.why and "in use by another program" in row.why and row.rules == ""


def test_a_file_that_is_not_there_is_not_listed(world, tmp_path):
    q, *_ = world
    assert q.file_saved(str(tmp_path / "gone.txt")) is None
    assert q.file_saved("relative.txt") is None and q.rows() == []


def test_a_data_link_is_never_kept_and_has_no_site(world):
    q, base, chrome, _ = world
    assert browser_site("data:text/plain;base64") == "" and browser_site("https://a.org/f") == "https://a.org/f"
    row = q.store.get(q.file_saved(save(chrome, "made.txt"), url="data:text/plain;base64", suggested="made.txt"))
    assert row.url == "" and row.folder == under(base, "Documents")


def test_an_archive_the_browser_saved_goes_through_the_archive_trial(world):
    import zipfile
    q, base, chrome, _ = world
    q.unpack_on = True
    path = str(chrome / "pack.zip")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("a.txt", "a")
    row = q.store.get(q.file_saved(path, url="blob:https://a.org/1", suggested="pack.zip"))
    assert row.folder == under(base, "Archives") and row.unpack.get("state") == "waiting"
