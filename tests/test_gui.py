"""The window and the rules editor, offscreen, with the fake engine and prober from the queue tests."""
import dataclasses
import datetime
import json
import os
import re
import threading
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QFileDialog, QHeaderView, QMessageBox  # noqa: E402

from accipio.core import cron, rules, settings, taskbar  # noqa: E402
from accipio.core.rules import Condition, Rule  # noqa: E402
from accipio.core.store import ACTIVE, COMPLETE, PAUSED  # noqa: E402
from PyQt6.QtGui import QCloseEvent, QColor  # noqa: E402

from accipio.gui import main_window as mw, style  # noqa: E402
from accipio.gui.queue_model import COLUMNS, NAME, STATUS  # noqa: E402
from accipio.gui.rules_dialog import ConditionRow, RulesDialog, TimeEditor, picker_state  # noqa: E402
from accipio.gui.settings_dialog import SettingsDialog  # noqa: E402
from tests import samples  # noqa: E402
from tests.test_queue import FakeEngine, FakeProber  # noqa: E402


class Engine(FakeEngine):
    def __init__(self):
        super().__init__()
        self.limits = []
        self.stopped = False

    def set_limits(self, **kw):
        self.limits.append(kw)

    def stop(self):
        self.stopped = True


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


class RefusingToaster:
    def show(self, title, text, sound, path=""):
        return False


@pytest.fixture
def world(app, tmp_path):
    folder = str(tmp_path / "config")
    settings.save_settings(folder, settings.Settings(base_folder=str(tmp_path / "Downloads")))
    settings.save_rules(folder, samples.sorting_rules(), samples.sorting_library())
    engine, prober = Engine(), FakeProber()
    win = mw.MainWindow(mw.open_services(folder, engine=engine, prober=prober, video_engine=False), tick_ms=60_000)
    win.toaster = RefusingToaster()        # never a real Windows notice from a test; the tray (a FakeTray) tells it
    win.play_sound = lambda sound: None
    yield win, engine, prober, str(tmp_path / "Downloads")
    try:
        win.flush()
    except RuntimeError:
        pass                               # the test closed the window itself
    QApplication.processEvents()           # what this test left queued goes to this window, not to a later test
    win.close()
    QApplication.sendPostedEvents(None, 0)
    QApplication.processEvents()


def add(win, prober, url, filename, **kw):
    prober.serve(url, filename, **kw)
    win.paste.setText(url)
    win.add_from_bar()
    win.flush()


def select(win, position):
    win.table.selectRow(position)


# ---- the debug log (2026-11-07, 72B) ---------------------------------------------------------------------

def test_the_debug_log_is_off_until_switched_on_and_then_tells_what_the_browser_did(world):
    from accipio.core import debug_log
    win, engine, prober, base = world
    path = os.path.join(win.services.folder, debug_log.FILE)
    win.handle_message({"type": "seen", "via": "browser", "reason": "takeover off", "url": "https://x.org/a.zip"})
    assert not os.path.exists(path)                                    # off by default
    dialog = SettingsDialog(win.services.settings, win)
    assert not dialog.debug_log.isChecked()
    dialog.debug_log.setChecked(True)
    win.apply_settings(dialog.result_settings())
    win.handle_message({"type": "seen", "via": "browser", "reason": "takeover off", "url": "https://x.org/a.zip",
                        "cookies": "sid=SECRET"})
    prober.serve("https://getcomics.org/dlds/b.zip", "b.zip")
    answer = win.handle_message({"type": "add", "via": "browser", "takeover": True,
                                 "url": "https://getcomics.org/dlds/b.zip", "cookies": "sid=SECRET"})
    win.flush()
    text = open(path, encoding="utf-8").read()
    assert answer["ok"] and "left to Chrome by the extension" in text and "takeover off" in text
    for event in ("from browser", "link check", "placed", "answer to Chrome"):
        assert f"  {event}  " in text
    assert "SECRET" not in text
    report = debug_log.LOG.report(header="h")
    assert report.startswith("h\n") and "answer to Chrome" in report
    dialog = SettingsDialog(win.services.settings, win)
    dialog.debug_log.setChecked(False)
    dialog._delete_debug_log()
    win.apply_settings(dialog.result_settings())
    assert not os.path.exists(path) and not debug_log.LOG.on


# ---- the main window -------------------------------------------------------------------------------------

def test_the_menu_bar_follows_the_family_skeleton(world):
    from redactor_common.gui.menu_lint import lint_menu_bar
    win, *_ = world
    assert lint_menu_bar(win) == []
    assert list(win.menus)[:3] == ["File", "Edit", "View"] and "Downloads" in win.menus


def test_every_column_is_resizable_and_sortable(world):
    win, *_ = world
    header = win.table.horizontalHeader()
    assert win.table.isSortingEnabled() and header.sectionsClickable()
    assert all(header.sectionResizeMode(c) == QHeaderView.ResizeMode.Interactive for c in range(len(COLUMNS) - 1))


def test_links_are_found_in_pasted_text():
    text = "see https://a.org/x.zip, and (https://b.org/y?z=1) plus ftp://c.org/f.iso. https://a.org/x.zip again; junk"
    assert mw.links_in(text) == ["https://a.org/x.zip", "https://b.org/y?z=1", "ftp://c.org/f.iso"]
    assert mw.links_in("no links here") == []


def test_the_paste_bar_adds_every_link_and_clears(world):
    win, engine, prober, base = world
    prober.serve("https://a.org/a.zip", "a.zip")
    prober.serve("https://a.org/b.epub", "b.epub")
    win.paste.setText("https://a.org/a.zip https://a.org/b.epub")
    win.add_from_bar()
    win.flush()
    assert win.paste.text() == ""
    assert [(r.filename, r.state) for r in win.model.rows] == [("a.zip", ACTIVE), ("b.epub", ACTIVE)]
    assert win.chips["All"].text() == "All 2" and win.chips["Active"].text() == "Active 2"
    assert win.proxy.index(0, 5).data() == "Archives"                 # the folder, under the base folder


def test_text_without_a_link_says_so_and_stays(world):
    win, *_ = world
    win.paste.setText("just words")
    win.add_from_bar()
    assert win.paste.text() == "just words" and "No link" in win.statusBar().currentMessage()


def test_progress_completion_and_the_filter_chips(world):
    win, engine, prober, base = world
    add(win, prober, "https://a.org/a.zip", "a.zip")
    add(win, prober, "https://a.org/b.zip", "b.zip")
    first, second = (r.gid for r in win.model.rows)
    engine.progress(first, 50, 200)
    engine.finish(second, b"PK\x03\x04")
    win.tick()
    win.flush()
    assert win.chips["Done"].text() == "Done 1" and win.chips["Active"].text() == "Active 1"
    assert win.proxy.index(0, 2).data() == "25 %" and "/s" in win.speed_label.text()
    win.set_filter("Done")
    assert win.proxy.rowCount() == 1 and win.proxy.index(0, NAME).data() == "b.zip"
    win.set_filter("All")
    assert win.proxy.rowCount() == 2
    win.actions_by_key["show_done"].trigger()             # View > Show Done: "checked" must not become the filter
    assert win.proxy.rowCount() == 1 and win.chips["Done"].isChecked()


def test_why_here_is_the_tooltip(world):
    win, engine, prober, base = world
    add(win, prober, "https://a.org/a.zip", "a.zip")
    tip = win.proxy.index(0, NAME).data(Qt.ItemDataRole.ToolTipRole)
    assert "https://a.org/a.zip" in tip and 'Why here: rule 8 "Archives"' in tip


def test_a_web_page_link_fails_with_a_way_to_open_it_in_the_browser(world, monkeypatch):
    from accipio.core import probe
    from PyQt6.QtGui import QDesktopServices
    win, engine, prober, base = world
    page = "https://vik1ngfile.site/f/bL50z2nyK9"

    def landing_page(url, headers=None):
        raise probe.WebPageError(probe.WEB_PAGE_MESSAGE)
    monkeypatch.setattr(win.queue, "prober", landing_page)
    win.paste.setText(page)
    win.add_from_bar()
    win.flush()
    row = win.queue.rows()[0]
    assert row.state == "error" and "web page, not a file" in row.error and not engine.jobs
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    select(win, 0)
    win.actions_by_key["open_in_browser"].trigger()
    assert opened == [page]


def test_space_pauses_and_resumes_the_selection(world):
    win, engine, prober, base = world
    add(win, prober, "https://a.org/a.zip", "a.zip")
    select(win, 0)
    win.pause_selected()
    win.flush()
    assert win.model.rows[0].state == PAUSED
    select(win, 0)
    win.pause_selected()
    win.flush()
    assert win.model.rows[0].state == ACTIVE


def test_removing_an_unfinished_download_asks_first(world, monkeypatch):
    win, engine, prober, base = world
    add(win, prober, "https://a.org/a.zip", "a.zip")
    select(win, 0)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    win.remove_selected()
    win.flush()
    assert len(win.model.rows) == 1
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    select(win, 0)
    win.remove_selected()
    win.flush()
    assert win.model.rows == []


def test_the_selection_survives_a_refresh(world):
    win, engine, prober, base = world
    add(win, prober, "https://a.org/a.zip", "a.zip")
    add(win, prober, "https://a.org/b.zip", "b.zip")
    select(win, 1)
    engine.progress(win.model.rows[1].gid, 10, 100)
    win.tick()
    win.flush()
    assert [r.filename for r in win.selected_rows()] == ["b.zip"]


def test_new_rules_apply_to_links_added_after_saving(world, tmp_path):
    win, engine, prober, base = world
    special = str(tmp_path / "Special")
    win.apply_rules([Rule("Everything", folder=special)])
    saved = json.load(open(os.path.join(win.services.folder, "rules.json"), encoding="utf-8"))
    assert saved["rules"][0]["name"] == "Everything"
    add(win, prober, "https://a.org/a.zip", "a.zip")
    assert win.model.rows[0].folder == os.path.normpath(special)


def test_settings_are_saved_and_reach_the_engine(world, tmp_path):
    win, engine, prober, base = world
    dialog = SettingsDialog(win.services.settings, win)
    dialog.max_downloads.setValue(5)
    dialog.speed_limit.setValue(300)
    assert dialog.clear_buttons[settings.CLEAR_AT_START].isChecked()          # the default (51A)
    assert not dialog.clear_after_minutes.isEnabled()
    dialog.clear_buttons[settings.CLEAR_AFTER].setChecked(True)
    assert dialog.clear_after_minutes.isEnabled()
    dialog.clear_after_minutes.setValue(20)
    win.apply_settings(dialog.result_settings())
    assert (win.queue.clear_finished_how, win.queue.clear_after_minutes) == (settings.CLEAR_AFTER, 20)
    win.flush()
    assert {"max_downloads": 5} in engine.limits and win.queue.speed_limit == 300 * 1024
    win.tick()                                         # the queue shares the total out on its next refresh
    win.flush()
    assert engine.limits[-1] == {"speed_limit": 300 * 1024}
    again = settings.load_settings(win.services.folder)
    assert (again.max_downloads, again.clear_finished, again.clear_after_minutes, again.base_folder) == \
        (5, settings.CLEAR_AFTER, 20, base)


def test_the_default_folder_is_set_in_the_folders_tab(world, tmp_path, monkeypatch):
    from accipio.gui.rules_dialog import RulesDialog as Dialog
    win, engine, prober, base = world
    elsewhere = str(tmp_path / "Elsewhere")

    def edit(dialog):
        tab = dialog.folders_tab
        tab.list.setCurrentRow(0)
        assert tab.name.text() == "Default" and tab.name.isReadOnly() and tab.path.text() == base
        tab.path.setText(elsewhere)
        return 1
    monkeypatch.setattr(Dialog, "exec", edit)
    win.open_rules()
    assert settings.load_settings(win.services.folder).base_folder == elsewhere
    add(win, prober, "https://a.org/a.zip", "a.zip")
    assert win.model.rows[0].folder == os.path.join(elsewhere, "Archives")       # %default%\Archives follows


def test_closing_stops_the_engine_and_remembers_the_columns(world):
    win, engine, prober, base = world
    win.table.horizontalHeader().resizeSection(NAME, 333)
    win.close()
    assert engine.stopped and os.path.isfile(os.path.join(win.services.folder, "window.ini"))


class FakeTray:
    def __init__(self):
        self.messages = []

    def isVisible(self):
        return True

    def showMessage(self, title, text, *a):
        self.messages.append(text)

    def hide(self):
        pass


class ClickedX(QCloseEvent):
    """A close the user made (the X, Alt+F4): Qt marks those spontaneous; close() from code is not."""
    def spontaneous(self):
        return True


def click_x(win) -> QCloseEvent:
    event = ClickedX()
    win.closeEvent(event)
    return event


def test_the_x_keeps_accipio_running_in_the_tray(world):
    """2026-11-06 (Q57A): the window's X hides Accipio to the tray, so downloads go on; the first time it says
    so. Only Exit (File menu, the tray's menu or Ctrl+Q) stops it."""
    win, engine, prober, base = world
    win.tray = FakeTray()
    win.show()
    assert not click_x(win).isAccepted()
    assert not win.isVisible() and not engine.stopped and win.timer.isActive()
    assert len(win.tray.messages) == 1 and "still running" in win.tray.messages[0]
    win.bring_to_front()
    assert win.isVisible()
    click_x(win)
    assert len(win.tray.messages) == 1                        # told once, not every time
    win.actions_by_key["exit"].trigger()
    assert engine.stopped


def test_a_close_from_code_still_quits_so_updates_and_restarts_work(world):
    """The updater (ours and redactor_common's) and Restart call close() and quit when it returns True."""
    win, engine, prober, base = world
    win.tray = FakeTray()
    win.show()
    assert win.close() and engine.stopped and not win.tray.messages


def test_without_a_tray_the_x_quits(world):
    win, engine, prober, base = world
    win.tray = None
    win.show()
    assert click_x(win).isAccepted() and engine.stopped


def test_windows_shutting_down_is_never_held_up_by_the_tray(world):
    win, engine, prober, base = world
    win.tray = FakeTray()
    win._session_ending()                    # what QApplication.commitDataRequest calls
    assert click_x(win).isAccepted() and engine.stopped and not win.tray.messages


# ---- updating yt-dlp -------------------------------------------------------------------------------------

def ask_ytdlp(win, monkeypatch, updater, answer=QMessageBox.StandardButton.No):
    """Run Tools > Update yt-dlp with a fake updater; returns the message boxes it showed."""
    from accipio.core import ytdlp_update
    shown = []
    for kind in ("information", "warning"):
        monkeypatch.setattr(QMessageBox, kind, lambda *a, kind=kind: shown.append((kind, a[2])))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: shown.append(("question", a[2])) or answer)
    restarts = []
    monkeypatch.setattr(win, "restart", lambda: restarts.append(1))
    win.ytdlp_updater = updater
    win.actions_by_key["update_ytdlp"].trigger()
    return shown, restarts, ytdlp_update


def test_update_ytdlp_says_when_it_is_the_newest(world, monkeypatch):
    from accipio.core.ytdlp_update import Result
    win, *_ = world
    shown, restarts, _ = ask_ytdlp(win, monkeypatch, lambda folder, running, cancel: Result("2026.8.19", False, False))
    assert shown[0][0] == "information" and "is the newest version" in shown[0][1] and not restarts


def test_update_ytdlp_installs_into_the_config_folder_and_offers_a_restart(world, monkeypatch):
    from accipio.core.ytdlp_update import Result
    win, *_ = world
    seen = []

    def updater(folder, running, cancel):
        seen.append(folder)
        return Result("2026.10.30", True, True)
    shown, restarts, _ = ask_ytdlp(win, monkeypatch, updater, answer=QMessageBox.StandardButton.Yes)
    assert seen == [win.services.folder]
    assert shown[0][0] == "question" and "2026.10.30 is installed" in shown[0][1] and restarts == [1]


def test_update_ytdlp_shows_a_failure_in_words(world, monkeypatch):
    from accipio.core.ytdlp_update import UpdateError
    win, *_ = world

    def updater(folder, running, cancel):
        raise UpdateError("Could not reach PyPI: no network")
    shown, restarts, _ = ask_ytdlp(win, monkeypatch, updater)
    assert shown == [("warning", "Could not reach PyPI: no network")] and not restarts


def test_update_ytdlp_cancel_says_nothing(world, monkeypatch):
    from accipio.core.ytdlp_update import Cancelled
    win, *_ = world

    def updater(folder, running, cancel):
        raise Cancelled()
    shown, restarts, _ = ask_ytdlp(win, monkeypatch, updater)
    assert shown == [] and not restarts


# ---- the rules editor ------------------------------------------------------------------------------------

def test_the_dialog_lists_the_rules_and_brings_old_ones_up_to_date(app, tmp_path):
    rule_list = [Rule("Work", [Condition("domain", ["work.example"])], folder="Work", stop=False),
                 Rule("Other", subfolder="Other")]
    dialog = RulesDialog(rule_list, str(tmp_path))
    assert [dialog.list.item(i).text() for i in range(2)] == ["1  Work  → Work", "2  Other  → Other"]
    got = dialog.result_rules()
    assert (got[0].target, got[0].folder, got[0].stop) == ("Work", "", True)    # a typed path became a named folder
    assert (got[1].target, got[1].subfolder) == ("Other", "")                  # and so did a subfolder
    assert rules.find_named(dialog.result_library().folders, "Other").path == "%default%\\Other"
    assert rule_list[0].folder == "Work" and rule_list[0].target == ""          # the caller's list is untouched until OK


def test_editing_a_rule_as_a_sentence(app, tmp_path):
    library = samples.sorting_library()
    library.site_groups.append(rules.SiteGroup("Shops", ["gog.com"]))
    dialog = RulesDialog(samples.sorting_rules(), str(tmp_path), library=library)
    dialog.list.setCurrentRow(5)                                          # Comics
    form = dialog.form
    assert form.types.values() == ["Comics"] and form.sites.values() == [] and form.folder.currentData() == "Comics"
    form.types.pick("Ebooks")
    form.sites.pick("Shops")
    form.folder.setCurrentIndex(form.folder.findData("Books"))
    got = dialog.result_rules()[5]
    assert got.conditions == [Condition("sites", ["Shops"]), Condition("type", ["Comics", "Ebooks"])]
    assert got.target == "Books" and got.subfolder == "" and got.stop is True
    form.types.drop("Comics")
    assert dialog.result_rules()[5].conditions[1].values == ["Ebooks"]


def test_more_conditions_are_folded_away_until_used(app, tmp_path):
    rule = Rule("Big", [Condition("type", ["Video"]), Condition("size", min_size=100 * 1024 * 1024)], target="Default")
    dialog = RulesDialog([rule, Rule("Plain")], str(tmp_path))
    form = dialog.form
    assert form.more_button.isChecked() and len(form.rows) == 1 and form.rows[0].kind.currentData() == "size"
    assert [form.rows[0].kind.itemData(i) for i in range(form.rows[0].kind.count())] == \
        ["domain", "name", "size", "time", "source", "video"]                  # groups are in the sentence instead
    dialog.list.setCurrentRow(1)
    assert not form.more_button.isChecked() and form.more.isHidden()
    assert dialog.result_rules()[0].conditions == rule.conditions


def test_the_command_field_is_hidden_until_commands_run_but_a_saved_one_is_kept(app, tmp_path):
    saved = samples.sorting_rules()
    saved[7].command = 'notify "{name}"'
    dialog = RulesDialog(saved, str(tmp_path), library=samples.sorting_library())
    dialog.list.setCurrentRow(7)
    assert dialog.form.command.isHidden()
    dialog.form.name.setText("Archives!")
    assert dialog.result_rules()[7].command == 'notify "{name}"'


def test_new_duplicate_and_delete(app, tmp_path):
    dialog = RulesDialog([Rule("A"), Rule("B")], str(tmp_path))
    dialog.list.setCurrentRow(0)
    dialog.new_rule()
    assert [r.name for r in dialog.result_rules()] == ["A", "New rule", "B"]
    dialog.duplicate_rule()
    assert [r.name for r in dialog.result_rules()] == ["A", "New rule", "New rule (copy)", "B"]
    dialog.list.setCurrentRow(0)
    dialog.delete_rule()
    assert [r.name for r in dialog.result_rules()] == ["New rule", "New rule (copy)", "B"]


def test_dragging_changes_the_order(app, tmp_path):
    dialog = RulesDialog([Rule("A"), Rule("B"), Rule("C")], str(tmp_path))
    model = dialog.list.model()
    assert model.moveRow(model.index(0, 0).parent(), 2, model.index(0, 0).parent(), 0)
    assert [r.name for r in dialog.result_rules()] == ["C", "A", "B"]
    assert dialog.list.item(0).text().startswith("1  C")


def test_the_form_shows_what_is_wrong(app, tmp_path):
    dialog = RulesDialog([Rule("A")], str(tmp_path))
    row = dialog.form.renamer.add_line()
    row.pattern.setText("x")
    row.to.setText("{nope}.{ext}")
    assert "unknown token {nope}" in dialog.form.problems.text()
    row.to.setText("{yyyy}.{ext}")
    assert dialog.form.problems.text() == ""


def test_the_test_box_shows_the_path_and_the_skipped_rules(app, tmp_path):
    base = str(tmp_path / "Downloads")
    dialog = RulesDialog(samples.sorting_rules(), base, library=samples.sorting_library())
    dialog.test_url.setText("https://example.com/files/Dune.epub")
    assert os.path.join(base, "Books", "Dune.epub") in dialog.test_result.text()
    assert 'rule 5 "Ebooks"' in dialog.test_result.text() and "Skipped: rule 1, 2, 3, 4, 6" in dialog.test_result.text()
    dialog.test_video.setChecked(True)
    dialog.test_channel.setText("XYZ")
    assert os.path.join(base, "Video", "XYZ") in dialog.test_result.text()


def test_the_test_box_uses_unsaved_edits(app, tmp_path):
    dialog = RulesDialog(samples.sorting_rules(), str(tmp_path), library=samples.sorting_library())
    dialog.list.setCurrentRow(4)                                          # Ebooks
    dialog.form.folder.setCurrentIndex(dialog.form.folder.findData("Documents"))
    dialog.test_url.setText("https://example.com/Dune.epub")
    assert os.path.join(str(tmp_path), "Documents", "Dune.epub") in dialog.test_result.text()


@pytest.mark.parametrize("condition", [
    Condition("domain", ["youtube.com", "*.github.com"], where="page"),
    Condition("type", ["Video", "mkv"]),
    Condition("name", ["*S01E??*"]),
    Condition("name", [r"s\d\de\d\d, really"], regex=True),
    Condition("size", min_size=1024 * 1024 * 100),
    Condition("time", ["* 8-15 * * 1-5"]),
    Condition("source", ["video"]),
    Condition("video", ["XYZ*"], where="uploader"),
])
def test_every_kind_of_condition_survives_the_form(app, condition):
    row = ConditionRow(condition)
    got = row.condition()
    if condition.kind == "domain":
        assert got == condition
    else:
        assert (got.kind, got.values, got.regex, got.min_size, got.max_size) == \
            (condition.kind, condition.values, condition.regex, condition.min_size, condition.max_size)
        if condition.kind == "video":
            assert got.where == "uploader"


def test_the_time_picker_writes_cron_and_reads_it_back(app):
    editor = TimeEditor()
    assert editor.lines() == ["* 8-15 * * 1-5"] and editor.sentence.text() == "Weekdays, 08:00–16:00"
    editor.day_buttons[6].setChecked(True)
    editor.end.setTime(editor.end.time().addSecs(30 * 60))
    assert editor.lines() == ["* 8-15 * * 1-6", "0-29 16 * * 1-6"] and editor.sentence.text() == "Mon–Sat, 08:00–16:30"
    editor.set_lines(["*/10 * * * *"])
    assert not editor.start.isEnabled() and "(set in Advanced)" in editor.sentence.text()
    editor.advanced.setText("* 99 * * *")
    editor._from_advanced()
    assert "outside 0-23" in editor.sentence.text()


def test_picker_state_reads_back_windows_past_midnight():
    lines = cron.window_lines({5}, datetime.time(22), datetime.time(6))
    assert picker_state(lines) == ({5}, datetime.time(22), datetime.time(6))
    assert picker_state(["* 8-15 1-7 * 1"]) is None                    # days of the month: Advanced only


# ---- messages from the browser ---------------------------------------------------------------------------

def test_a_download_from_the_browser_arrives_with_its_cookies(world):
    from accipio.core import ipc
    win, engine, prober, base = world
    prober.serve("https://a.org/f.zip", "f.zip")
    reply = ipc.send(win.services.folder, {"type": "add", "url": "https://a.org/f.zip", "page_url": "https://a.org/",
                                           "referrer": "https://a.org/", "cookies": "s=1", "user_agent": "Chrome"})
    assert reply == {"ok": True}
    win.flush()
    row = win.model.rows[0]
    assert row.filename == "f.zip" and row.page_url == "https://a.org/"
    assert prober.seen_headers[-1] == {"Cookie": "s=1", "Referer": "https://a.org/", "User-Agent": "Chrome"}


def test_ping_show_and_nonsense(world):
    from accipio.core import ipc
    win, *_ = world
    assert ipc.send(win.services.folder, {"type": "ping"}) == {"ok": True, "version": mw.APP_VERSION}
    assert ipc.send(win.services.folder, {"type": "show"}) == {"ok": True}
    assert ipc.send(win.services.folder, {"type": "add", "url": "javascript:alert(1)"})["ok"] is False
    assert ipc.send(win.services.folder, {"type": "format c:"})["ok"] is False


def test_quit_from_the_command_line_stops_accipio_the_way_exit_does(world, monkeypatch):
    """Accipio.exe --quit, which the installer and uninstaller run: since the X only hides Accipio (Q57A), a
    plain close request (taskkill, Windows' installer tools) no longer ends it."""
    from accipio.core import settings as settings_mod
    from accipio.gui import app as app_mod
    win, *_ = world
    monkeypatch.setattr(settings_mod, "config_dir", lambda: win.services.folder)
    quits, waited = [], []
    monkeypatch.setattr(win, "quit_app", lambda: quits.append(1))
    monkeypatch.setattr(app_mod, "wait_for_exit", lambda pid, timeout: waited.append(pid) or True)
    assert app_mod.main(["Accipio.exe", "--quit"]) == 0
    QApplication.processEvents()
    assert quits == [1] and waited == [os.getpid()]                    # it waits for that very process to end


def test_a_second_start_hands_over_and_quits(world, monkeypatch):
    from accipio.core import settings as settings_mod
    from accipio.gui import app as app_mod
    win, *_ = world
    monkeypatch.setattr(settings_mod, "config_dir", lambda: win.services.folder)
    shown = []
    win.bridge.show_requested.connect(lambda: shown.append(1))
    assert app_mod.main(["accipio"]) == 0
    QApplication.processEvents()
    assert shown == [1]


# ---- the Add box -----------------------------------------------------------------------------------------

def open_box(win, text, answer):
    """Open the Add box on ``text``; ``answer(dialog)`` plays the user and returns True for Add."""
    from accipio.gui.add_dialog import AddDialog

    def show(dialog):
        win.flush()
        dialog.collect()
        return answer(dialog)
    win._show_add_dialog = show
    return win.open_add_dialog(text)


def test_file_add_links_opens_the_add_box_with_the_paste_bar(world):
    win = world[0]
    seen = []
    win._show_add_dialog = lambda dialog: seen.append(dialog.text.toPlainText()) or False
    win.paste.setText("https://a.org/x.zip")
    win.actions_by_key["add_links"].trigger()             # the menu passes "checked" along: it must not become the text
    assert seen == ["https://a.org/x.zip"]


def test_the_add_box_checks_every_link_and_shows_where_it_goes(world):
    win, engine, prober, base = world
    prober.serve("https://a.org/tool.zip", "tool.zip", size=88 * 1024 * 1024)
    prober.serve("https://a.org/Dune.epub", "Dune.epub")
    seen = {}

    def look(dialog):
        rows = [[dialog.table.item(r, c).text() for c in range(1, 5)] for r in range(dialog.table.rowCount())]
        seen["rows"] = rows
        seen["button"] = dialog.add_button.text()
        seen["error_checkable"] = bool(dialog.table.item(2, 0).flags() & Qt.ItemFlag.ItemIsUserCheckable)
        return False
    open_box(win, "https://a.org/tool.zip https://a.org/Dune.epub https://gone.org/x", look)
    assert seen["rows"][0] == ["tool.zip", "Archives", "8","Ready · 88.0 MB"]
    assert seen["rows"][1][:3] == ["Dune.epub", "Books", "5"]
    assert seen["rows"][2][0] == "https://gone.org/x" and "404" in seen["rows"][2][3]
    assert seen["button"] == "Add 2" and seen["error_checkable"] is False
    win.flush()
    assert win.model.rows == []                                         # Cancel adds nothing


def test_the_add_box_adds_the_ticked_links_with_their_folders(world, tmp_path):
    win, engine, prober, base = world
    for name in ("a.zip", "b.zip", "c.zip"):
        prober.serve(f"https://a.org/{name}", name)
    elsewhere = str(tmp_path / "Elsewhere")

    def choose(dialog):
        dialog.table.item(1, 0).setCheckState(Qt.CheckState.Unchecked)   # leave b out
        dialog.set_folder(2, elsewhere)                                   # c somewhere else
        return True
    open_box(win, "https://a.org/a.zip\nhttps://a.org/b.zip\nhttps://a.org/c.zip", choose)
    win.flush()
    rows = {r.filename: r for r in win.model.rows}
    assert set(rows) == {"a.zip", "c.zip"}
    assert rows["a.zip"].folder == os.path.join(base, "Archives") and not rows["a.zip"].pinned
    assert rows["c.zip"].folder == os.path.normpath(elsewhere) and rows["c.zip"].pinned


def test_put_all_in_sends_everything_to_one_folder(world, tmp_path):
    win, engine, prober, base = world
    prober.serve("https://a.org/a.zip", "a.zip")
    prober.serve("https://a.org/b.epub", "b.epub")
    today = str(tmp_path / "Today")

    def all_in(dialog):
        dialog.all_in.setText(today)
        assert dialog.table.item(0, 2).toolTip().endswith("(chosen by hand)") and dialog.table.item(0, 3).text() == ""
        return True
    open_box(win, "https://a.org/a.zip https://a.org/b.epub", all_in)
    win.flush()
    assert {r.folder for r in win.model.rows} == {os.path.normpath(today)}


def test_editing_the_text_checks_new_links_and_drops_removed_ones(world):
    win, engine, prober, base = world
    prober.serve("https://a.org/a.zip", "a.zip")
    prober.serve("https://a.org/b.zip", "b.zip")

    def edit(dialog):
        dialog.text.setPlainText("https://a.org/b.zip")
        dialog.read_links()
        win.flush()
        dialog.collect()
        assert [dialog.table.item(r, 1).text() for r in range(dialog.table.rowCount())] == ["b.zip"]
        return True
    open_box(win, "https://a.org/a.zip", edit)
    win.flush()
    assert [r.filename for r in win.model.rows] == ["b.zip"]


def test_shift_enter_in_the_bar_opens_the_box_and_clears_the_bar_on_add(world):
    win, engine, prober, base = world
    prober.serve("https://a.org/a.zip", "a.zip")
    win._show_add_dialog = lambda dialog: (win.flush(), dialog.collect(), True)[-1]
    win.paste.setText("https://a.org/a.zip")
    win.open_add_dialog(win.paste.text(), from_bar=True)
    win.flush()
    assert win.paste.text() == "" and [r.filename for r in win.model.rows] == ["a.zip"]


# ---- notifications ---------------------------------------------------------------------------------------

def test_added_and_finished_downloads_are_told(world, monkeypatch):
    win, engine, prober, base = world
    shown = []
    monkeypatch.setattr(win, "_show_notice", shown.append)
    win.notices.gather = 0
    add(win, prober, "https://a.org/a.zip", "a.zip")
    add(win, prober, "https://a.org/b.zip", "b.zip")
    engine.finish(win.model.rows[0].gid, b"PK\x03\x04")
    win.tick()
    win.flush()
    win.reload()
    assert [n.title for n in shown] == ["Download added", "Download added", "Download finished"]
    assert shown[-1].path == os.path.join(base, "Archives", "a.zip")


def test_switching_a_notification_off_in_the_settings(world, monkeypatch):
    win, engine, prober, base = world
    shown = []
    monkeypatch.setattr(win, "_show_notice", shown.append)
    win.notices.gather = 0
    dialog = SettingsDialog(win.services.settings, win)
    assert dialog.notify_added.isChecked() and dialog.notify_finished.isChecked() and dialog.notify_failed.isChecked()
    dialog.notify_added.setChecked(False)
    win.apply_settings(dialog.result_settings())
    add(win, prober, "https://a.org/a.zip", "a.zip")
    assert shown == [] and settings.load_settings(win.services.folder).notify_added is False


class FakeButton:
    def __init__(self):
        self.looks = []

    def show(self, look):
        self.looks.append(look)
        return True


def test_the_taskbar_button_and_title_follow_the_downloads(world, monkeypatch):
    win, engine, prober, base = world
    win.taskbar_button = button = FakeButton()
    monkeypatch.setattr(win, "_looking", lambda: False)
    add(win, prober, "https://a.org/a.zip", "a.zip")
    add(win, prober, "https://a.org/b.zip", "b.zip")
    engine.progress(win.model.rows[0].gid, 30, 100)
    engine.progress(win.model.rows[1].gid, 60, 100)
    win.tick()
    win.flush()
    assert button.looks[-1] == taskbar.Look(taskbar.NORMAL, 45, 2, "Accipio – 2 downloading, 45%")
    assert win.windowTitle() == "Accipio – 2 downloading, 45%"

    engine.fail(win.model.rows[1].gid, "3", "the server answered 404")
    win.tick()
    win.flush()
    assert (button.looks[-1].mode, button.looks[-1].count) == (taskbar.ERROR, 1)
    assert win.windowTitle() == "Accipio – 1 downloading, 1 failed, 30%"

    monkeypatch.setattr(win, "_looking", lambda: True)          # the user brings the window up
    win.reload()
    assert button.looks[-1].mode == taskbar.NORMAL

    engine.finish(win.model.rows[0].gid)
    win.tick()
    win.flush()
    assert button.looks[-1] == taskbar.Look(taskbar.NONE, 0, 0, "Accipio")
    assert win.windowTitle() == "Accipio"


def test_the_window_keeps_telling_the_button_until_windows_takes_it(world):
    win, engine, prober, base = world
    win.taskbar_button = button = FakeButton()
    answers = [False, False, True]
    button.show = lambda look: button.looks.append(look) or answers.pop(0)
    add(win, prober, "https://a.org/a.zip", "a.zip")
    win.reload()
    win.reload()
    win.reload()
    assert len(button.looks) == 3 and answers == []


@pytest.mark.skipif(os.name != "nt", reason="the taskbar interface is Windows'")
def test_windows_hands_out_the_taskbar_interface(app):
    from accipio.gui.taskbar_button import TaskbarButton
    TaskbarButton._call(TaskbarButton._create(), 2)            # a wrong interface id raises here


def test_the_badge_is_a_coloured_disc_with_the_count(app):
    from accipio.gui.taskbar_button import BADGE_SIZE, badge_image
    image = badge_image("3", taskbar.PAUSED)
    assert (image.width(), image.height()) == (BADGE_SIZE, BADGE_SIZE)
    assert image.pixelColor(0, 0).alpha() == 0                              # round, not a square
    assert image.pixelColor(BADGE_SIZE // 2, 4).name() == "#faeeda"         # the paused amber, above the digit


def test_the_taskbar_button_is_told_only_when_something_changes(world):
    win, engine, prober, base = world
    win.taskbar_button = button = FakeButton()
    win.reload()                                   # the idle look from the start, if the real button had no window
    del button.looks[:]
    win.reload()
    add(win, prober, "https://a.org/a.zip", "a.zip")
    win.reload()
    assert [look.count for look in button.looks] == [1]


def test_clicking_a_group_notice_brings_the_window_up(world, monkeypatch):
    win, engine, prober, base = world
    raised = []
    monkeypatch.setattr(win, "bring_to_front", lambda: raised.append(1))
    win._clicked_path = ""
    win.notice_clicked()
    assert raised == [1]


# ---- the look --------------------------------------------------------------------------------------------

def test_the_soft_style_fills_in_every_shade_for_light_and_dark(app):
    from PyQt6.QtGui import QColor, QPalette
    from accipio.gui import style
    for base, text in (("#ffffff", "#000000"), ("#1e1e1e", "#e0e0e0")):
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Base, QColor(base))
        palette.setColor(QPalette.ColorRole.Text, QColor(text))
        sheet = style.stylesheet(palette)
        assert "@" not in sheet.replace("@2x", "")              # no token left unfilled
        assert "border-radius" in sheet and "palette(mid)" not in sheet
    assert style.mix(QColor("#ffffff"), QColor("#000000"), 0.2) == "#cccccc"
    assert os.path.isfile(style.CHEVRON) and os.path.isfile(style.CHEVRON.replace(".png", "@2x.png"))


def test_the_window_wears_the_style(world):
    from accipio.gui import style
    win, engine, prober, base = world
    assert all(chip.property("chip") for chip in win.chips.values())
    assert win.table.verticalHeader().defaultSectionSize() == style.ROW_HEIGHT


def test_the_progress_bars_paint_in_every_state(world):
    win, engine, prober, base = world
    for name in ("a.zip", "b.zip", "c.zip"):
        add(win, prober, f"https://a.org/{name}", name)
    first, second, third = (r.gid for r in sorted(win.model.rows, key=lambda r: r.id))
    engine.progress(first, 50, 200)
    engine.finish(second, b"PK\x03\x04")
    engine.fail(third, "3")
    win.tick()
    win.flush()
    win.table.selectRow(0)
    win.resize(900, 300)
    assert not win.table.viewport().grab().isNull()


# ---- the rules window's tabs (2026-11-04#02) ------------------------------------------------------------

def test_the_window_has_the_five_tabs_in_setup_order(app, tmp_path):
    dialog = RulesDialog(samples.sorting_rules(), str(tmp_path), library=samples.sorting_library())
    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == [
        "1 · Folders", "2 · File Groups", "3 · Site Groups", "4 · Rules", "5 · Archives"]
    assert dialog.result_library() == samples.sorting_library()


def test_renaming_a_folder_renames_it_in_the_rules(app, tmp_path):
    dialog = RulesDialog(samples.sorting_rules(), str(tmp_path), library=samples.sorting_library())
    tab = dialog.folders_tab
    tab.list.setCurrentRow(1 + [f.name for f in tab.items()].index("Comics"))      # Default is row 0
    tab.name.setText("Comics inbox")
    tab.name.textEdited.emit("Comics inbox")
    tab.path.setText("D:/Comics/_Inbox")
    assert dialog.result_rules()[5].target == "Comics inbox"
    dialog.test_url.setText("https://example.com/Saga 01.cbz")
    assert os.path.join(os.path.normpath("D:/Comics/_Inbox"), "Saga 01.cbz") in dialog.test_result.text()


def test_a_folder_a_rule_uses_cannot_be_deleted(app, tmp_path, monkeypatch):
    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: told.append(a[2]))
    dialog = RulesDialog(samples.sorting_rules(), str(tmp_path), library=samples.sorting_library())
    tab = dialog.folders_tab
    tab.list.setCurrentRow(0)                                            # Default: never deleted
    tab.delete_item()
    assert "cannot be deleted" in told[0] and tab.list.item(0).text() == "Default"
    tab.list.setCurrentRow(1)                                            # Video sites, used by rule 1
    assert tab.used_by.text() == "Used by rule 1"
    tab.delete_item()
    assert "Rule 1 uses" in told[1] and len(tab.items()) == len(samples.FOLDERS)
    tab.new_item()
    tab.delete_item()                                                    # a new, unused one goes
    assert len(tab.items()) == len(samples.FOLDERS)


def test_a_rule_left_on_default_is_hard_to_miss(app, tmp_path):
    """2026-11-06 (Q58C): a comics rule left on Default sent comics to Default and nobody saw it. The rule list
    says where each rule goes, and the go-to row turns amber, while a rule goes to Default or a missing folder."""
    rule_list = [Rule("Comics", [Condition("type", ["Comics"])], target="Default"),
                 Rule("Books", [Condition("type", ["Books"])], target="Books"),
                 Rule("Gone", [Condition("type", ["Audio"])], target="Nowhere")]
    dialog = RulesDialog(rule_list, str(tmp_path), library=samples.sorting_library())
    texts = [dialog.list.item(i).text() for i in range(3)]
    assert texts == ["1  Comics  → Default", "2  Books  → Books", "3  Gone  → Nowhere (missing)"]
    amber = QColor(style.WARNING_COLOR)
    assert [dialog.list.item(i).foreground().color() == amber for i in range(3)] == [True, False, True]
    dialog.list.setCurrentRow(0)
    assert str(tmp_path) in dialog.form.where.text() and "same place as no rule" in dialog.form.where.text()
    assert style.WARNING_COLOR in dialog.form.where.styleSheet()
    dialog.form.folder.setCurrentIndex(dialog.form.folder.findData("Comics"))   # picking a folder clears it
    assert dialog.form.where.text() == os.path.join(str(tmp_path), "Comics") and not dialog.form.where.styleSheet()
    assert dialog.list.item(0).text() == "1  Comics  → Comics"
    assert dialog.list.item(0).foreground().color() != amber


def test_the_rule_form_picks_a_folder_and_a_site_group(app, tmp_path):
    library = samples.sorting_library()
    library.site_groups.append(rules.SiteGroup("Comic sites", ["getcomics.org"]))
    dialog = RulesDialog([Rule("Comics", [Condition("sites", ["Comic sites"])], target="Comics")], str(tmp_path),
                         library=library)
    assert dialog.form.folder.currentData() == "Comics" and dialog.form.where.text() == os.path.join(str(tmp_path), "Comics")
    assert dialog.form.sites.values() == ["Comic sites"] and dialog.form.rows == []
    dialog.form.folder.setCurrentIndex(dialog.form.folder.findData("Books"))
    assert dialog.result_rules()[0].target == "Books"
    dialog.sites_tab.list.setCurrentRow(0)
    dialog.sites_tab.delete_item = lambda: None                            # (deleting is refused while in use)
    dialog.form.sites.set_values(["Nope"])
    dialog.form.changed.emit()
    assert "no site group called 'Nope'" in dialog.form.problems.text()


PROMPTS = {"Paste a link to see which rules hit and where it would go", "clicked on page (optional)", "channel",
           "type or paste a file name"}


def test_examples_in_the_rules_window_sit_under_the_fields_never_in_them(app, tmp_path):
    """65D (2026-11-06): grey example text inside an empty field looked like real values. Fields stay empty and
    the example is a small line under them; only prompts that nobody takes for a value stay inside."""
    from PyQt6.QtWidgets import QLineEdit
    dialog = RulesDialog([Rule("R", [Condition("type", ["Comics"])], renames=[rules.Rename("a", "b")])], str(tmp_path))
    inside = {e.placeholderText() for e in dialog.findChildren(QLineEdit)} - {""}
    assert inside <= PROMPTS, inside - PROMPTS
    assert dialog.groups_tab.names_field.example_hint.text() == "Example: *invoice*, *Vol.??*"
    assert dialog.sites_tab.sites.example_hint.text().startswith("Example: getcomics.org")
    assert dialog.form.name.example_hint.text() == "Example: Comics"
    assert "S{2}E{3}" in dialog.form.renamer.example_hint.text()
    tab = dialog.folders_tab
    tab.list.setCurrentRow(0)
    assert tab.path.example_hint.text() == "Example: D:\\Downloads"                  # Default
    tab.new_item()
    assert "%default%\\Comics" in tab.path.example_hint.text()


def test_the_group_lists_offer_only_what_is_not_picked_yet(app, tmp_path):
    dialog = RulesDialog([Rule("R", [Condition("type", ["Comics"])])], str(tmp_path))
    picker = dialog.form.types
    picker._fill_menu()
    offered = [a.text().split("  —  ")[0] for a in picker.menu.actions()]
    assert "Comics" not in offered and "Ebooks" in offered and "Video" in offered
    [ebooks] = [a for a in picker.menu.actions() if a.text().startswith("Ebooks")]
    ebooks.trigger()                                     # the menu passes "checked": it must not become the name
    assert picker.values() == ["Comics", "Ebooks"]


def test_the_file_group_drop_down_under_more_conditions_adds_the_group(app):
    row = ConditionRow(Condition("type", ["mkv"]))
    menu = row.type_menu.menu()
    menu.aboutToShow.emit()
    [comics] = [a for a in menu.actions() if a.text().startswith("Comics")]
    comics.trigger()
    assert row.condition().values == ["mkv", "Comics"]


def test_the_renamer_tries_its_lines_on_a_name(app, tmp_path):
    rule = Rule("TV", renames=[rules.Rename(r"(.+?)[ .]S(\d\d)E(\d\d)", "{1|dots|title} S{2}E{3}.{ext}")])
    dialog = RulesDialog([rule], str(tmp_path))
    renamer = dialog.form.renamer
    renamer.sample.setText("the.expanse.s02e05.1080p.web.mkv")
    assert renamer.result.text() == "→ The Expanse S02E05.mkv   (line 1)"
    renamer.sample.setText("holiday.mkv")
    assert "name kept" in renamer.result.text()
    row = renamer.add_line()
    row.pattern.setText("holiday")
    row.to.setText("Holiday.{ext}")
    assert [line.pattern for line in dialog.result_rules()[0].renames] == [r"(.+?)[ .]S(\d\d)E(\d\d)", "holiday"]
    assert renamer.result.text() == "→ Holiday.mkv   (line 2)"
    row.action.emit(row, "up")                                            # tried first now
    assert [line.pattern for line in dialog.result_rules()[0].renames] == ["holiday", r"(.+?)[ .]S(\d\d)E(\d\d)"]
    assert renamer.result.text() == "→ Holiday.mkv   (line 1)"
    row.action.emit(row, "remove")
    assert len(dialog.result_rules()[0].renames) == 1


def test_importing_rules_adds_them_below_mine(app, tmp_path, monkeypatch):
    path = str(tmp_path / "theirs.json")
    theirs = samples.sorting_library()
    theirs.folders.append(rules.Folder("Work", "E:/Job"))
    settings.export_rules(path, [Rule("Theirs", target="Work")], theirs)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (path, ""))
    monkeypatch.setattr(QMessageBox, "exec", lambda box: box.setProperty("picked", True) or 0)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: box.buttons()[0])    # "Add to mine"
    dialog = RulesDialog([Rule("Mine")], str(tmp_path))
    dialog.import_rules()
    assert [r.name for r in dialog.result_rules()] == ["Mine", "Theirs"]
    assert rules.find_named(dialog.result_library().folders, "Work").path == "E:/Job"


# ---- connecting the browser (2026-11-04#02) -------------------------------------------------------------

def test_a_download_chrome_hands_over_is_checked_before_accipio_takes_it(world):
    """Chrome drops its own copy once Accipio says yes, so the yes waits until Accipio's own check worked."""
    from accipio.core import ipc
    win, engine, prober, base = world
    prober.serve("https://a.org/ok.zip", "ok.zip")
    reply = ipc.send(win.services.folder, {"type": "add", "takeover": True, "url": "https://a.org/ok.zip"})
    assert reply == {"ok": True}
    assert [r.filename for r in win.queue.rows()] == ["ok.zip"]          # already added when the yes came
    reply = ipc.send(win.services.folder, {"type": "add", "takeover": True, "url": "https://a.org/once-only.zip"})
    assert reply["ok"] is False and "404" in reply["error"]               # Chrome keeps this one
    win.flush()
    assert [r.filename for r in win.queue.rows()] == ["ok.zip"]          # and no error row is left behind


def test_a_check_that_takes_too_long_leaves_the_download_to_chrome(world, monkeypatch):
    from accipio.core import ipc
    win, engine, prober, base = world
    monkeypatch.setattr(mw, "TAKEOVER_WAIT", 0.2)
    gate = threading.Event()
    real_call = prober.__class__.__call__

    def slow_prober(self, url, headers=None):
        gate.wait(5)
        return real_call(self, url, headers)
    monkeypatch.setattr(prober.__class__, "__call__", slow_prober)
    prober.serve("https://a.org/slow.zip", "slow.zip")
    reply = ipc.send(win.services.folder, {"type": "add", "takeover": True, "url": "https://a.org/slow.zip"})
    assert reply["ok"] is False and "too long" in reply["error"]
    gate.set()
    win.flush()
    win.flush()                                                          # the late row is removed on the worker
    assert win.queue.rows() == []                                        # never a second copy beside Chrome's


def test_the_browser_bar_shows_until_the_extension_talks_to_accipio(world):
    from accipio.core import ipc
    win, *_ = world
    assert not win.browser_bar.isHidden()
    ipc.send(win.services.folder, {"type": "ping"})                     # a second Accipio start: not the browser
    QApplication.processEvents()
    assert not win.browser_bar.isHidden() and not win.services.settings.browser_connected
    ipc.send(win.services.folder, {"type": "ping", "via": "browser"})   # what the native host passes on
    QApplication.processEvents()
    assert win.browser_bar.isHidden() and win.services.settings.browser_connected
    assert settings.load_settings(win.services.folder).browser_connected   # and it stays gone


def test_the_connect_box_copies_the_address_and_the_folder(app, tmp_path):
    from accipio.gui.browser_help import ConnectDialog, EXTENSIONS_PAGE, extension_folder, help_page
    dialog = ConnectDialog(folder=str(tmp_path / "extension"))
    dialog.copy_page.click()
    assert QApplication.clipboard().text() == EXTENSIONS_PAGE and dialog.copy_page.text() == "Copied"
    dialog.copy_folder.click()
    assert QApplication.clipboard().text() == str(tmp_path / "extension")
    assert os.path.isfile(os.path.join(extension_folder(), "manifest.json")) and os.path.isfile(help_page())


# ---- Help: updates and credits (2026-11-04#03) ----------------------------------------------------------

def test_check_for_updates_and_credits_can_be_clicked(world, monkeypatch):
    win, *_ = world
    assert win.actions_by_key["check_for_updates"].isEnabled()
    assert win.actions_by_key["credits"].isEnabled()
    shown = []
    monkeypatch.setattr(mw.CreditsDialog, "exec", lambda dialog: shown.append(dialog.windowTitle()) or 0)
    win.actions_by_key["credits"].trigger()
    assert shown == ["Credits"]


def test_the_daily_update_check_is_a_preference(world, monkeypatch):
    from accipio.gui import settings_dialog
    win, *_ = world
    assert win.updates.settings.enabled is False                     # off unless switched on
    monkeypatch.setattr(settings_dialog.SettingsDialog, "exec",
                        lambda dialog: dialog.check_updates.setChecked(True) or 1)
    win.open_settings()
    assert win.updates.settings.enabled is True
    assert os.path.isfile(os.path.join(win.services.folder, mw.UPDATES_FILE))


def ask_row(win, kind, count=49, title="Conference 2026", mix=False):
    ask = {"kind": kind, "count": count, "title": title, "mix": mix, "links": []}
    video = {"title": "Song", "ask": ask} if kind == "rest" else {"ask": ask}
    return win.queue.store.add("https://www.youtube.com/watch?v=1&list=RD1", state=PAUSED, source="video",
                               video=video, filename="Song.mp4" if kind == "rest" else "")


def test_a_video_from_a_playlist_asks_with_just_this_video_as_the_default(world, monkeypatch):
    win = world[0]
    answers = []
    monkeypatch.setattr(win.queue, "answer_playlist", lambda row_id, take_all: answers.append((row_id, take_all)))
    row_id = ask_row(win, "rest")
    win.reload()
    box = win.playlist_question
    assert box.isVisible() and box.row_id == row_id
    assert "part of a playlist" in box.heading.text() and "Song" in box.detail.text()
    assert "Conference 2026: 49 videos" in box.detail.text()
    assert (box.take_all.text(), box.just_one.text()) == ("All 49 videos", "Just this video")
    assert box.just_one.isDefault() and not box.never.isHidden()
    box.just_one.click()
    win.flush()
    assert answers == [(row_id, False)] and not box.isVisible()


def test_never_asking_again_is_saved_and_reaches_the_queue(world, monkeypatch):
    win = world[0]
    monkeypatch.setattr(win.queue, "answer_playlist", lambda row_id, take_all: None)
    ask_row(win, "rest")
    win.reload()
    win.playlist_question.never.setChecked(True)
    win.playlist_question.just_one.click()
    win.flush()
    assert win.services.settings.ask_playlists is False and win.queue.ask_playlists is False
    assert settings.load_settings(win.services.folder).ask_playlists is False


def test_a_playlist_link_asks_before_downloading_all_of_it(world, monkeypatch):
    win = world[0]
    answers = []
    monkeypatch.setattr(win.queue, "answer_playlist", lambda row_id, take_all: answers.append((row_id, take_all)))
    row_id = ask_row(win, "list", count=12, title="My list", mix=False)
    win.reload()
    box = win.playlist_question
    assert "whole playlist" in box.heading.text() and "My list" in box.detail.text() and "12" in box.detail.text()
    assert (box.take_all.text(), box.just_one.text()) == ("Download all 12", "Don't download")
    assert box.just_one.isDefault() and box.never.isHidden()                 # "always just the video" has no meaning here
    box.take_all.click()
    win.flush()
    assert answers == [(row_id, True)]


def test_a_playlist_added_from_the_add_box_counts_as_the_yes(world, monkeypatch):
    from accipio.core.queue import Preview
    from accipio.core.rules import Placement
    win = world[0]
    added = []
    monkeypatch.setattr(win.queue, "preview", lambda url, **_: Preview(
        url, "Playlist: 2 videos", None, "playlist", Placement(win.services.settings.base_folder, "x", [], []), 2))
    monkeypatch.setattr(win.queue, "add", lambda url, **kw: added.append((url, kw.get("whole_list"))) or 1)
    open_box(win, "https://www.youtube.com/playlist?list=P", lambda dialog: True)
    win.flush()
    assert added == [("https://www.youtube.com/playlist?list=P", True)]


def test_settings_can_switch_the_playlist_question_back_on(world):
    win = world[0]
    win.apply_settings(dataclasses.replace(win.services.settings, ask_playlists=False))
    dialog = SettingsDialog(win.services.settings, win)
    assert not dialog.ask_playlists.isChecked()
    dialog.ask_playlists.setChecked(True)
    win.apply_settings(dialog.result_settings())
    assert win.queue.ask_playlists is True


class FakeFfmpeg:
    """Where ffmpeg is "on this PC": a file picked by the user wins, else what an install put in ``installed``."""

    def __init__(self, tmp_path):
        self.installed = tmp_path / "winget" / "ffmpeg.exe"
        self.installs = 0

    def find(self, chosen=""):
        if chosen and os.path.isfile(chosen):
            return chosen
        return str(self.installed) if self.installed.is_file() else None

    def install(self, parent):
        self.installs += 1
        self.installed.parent.mkdir(parents=True, exist_ok=True)
        self.installed.write_bytes(b"MZ")
        return None                                         # no error


def ffmpeg_row(dialog):
    shown = [b.text() for b in (dialog.ffmpeg_install, dialog.ffmpeg_browse, dialog.ffmpeg_auto) if not b.isHidden()]
    return dialog.ffmpeg_label.text(), shown


def test_settings_say_when_ffmpeg_is_missing_and_offer_to_install_it(world, tmp_path):
    win = world[0]
    fake = FakeFfmpeg(tmp_path)
    dialog = SettingsDialog(win.services.settings, win, find_ffmpeg=fake.find, install_ffmpeg=fake.install)
    text, buttons = ffmpeg_row(dialog)
    assert text.startswith("Not found") and buttons == ["Install ffmpeg", "Browse…"]
    dialog.ffmpeg_install.click()
    assert fake.installs == 1
    assert ffmpeg_row(dialog) == (str(fake.installed), ["Browse…"])       # found at once, no restart
    assert dialog.result_settings().ffmpeg_path == ""                       # found by itself: nothing to remember


def test_a_browsed_ffmpeg_is_kept_until_find_automatically(world, tmp_path):
    win = world[0]
    fake = FakeFfmpeg(tmp_path)
    mine = tmp_path / "tools" / "ffmpeg.exe"
    mine.parent.mkdir()
    mine.write_bytes(b"MZ")
    dialog = SettingsDialog(win.services.settings, win, find_ffmpeg=fake.find, install_ffmpeg=fake.install)
    dialog._ask_for_ffmpeg = lambda: str(mine)
    dialog.ffmpeg_browse.click()
    assert ffmpeg_row(dialog) == (str(mine), ["Browse…", "Find automatically"])
    assert dialog.result_settings().ffmpeg_path == str(mine)
    dialog._ask_for_ffmpeg = lambda: ""                                     # Cancel in the file picker changes nothing
    dialog.ffmpeg_browse.click()
    assert dialog.result_settings().ffmpeg_path == str(mine)
    dialog.ffmpeg_auto.click()
    assert ffmpeg_row(dialog)[1] == ["Install ffmpeg", "Browse…"] and dialog.result_settings().ffmpeg_path == ""


def test_the_video_engine_uses_the_ffmpeg_from_settings_at_once(world, tmp_path):
    win = world[0]
    mine = tmp_path / "ffmpeg.exe"
    mine.write_bytes(b"MZ")
    win.services.video_engine = types.SimpleNamespace(ffmpeg=None, quality="best", set_limits=lambda **_: None)
    win.apply_settings(dataclasses.replace(win.services.settings, ffmpeg_path=str(mine)))
    assert win.services.video_engine.ffmpeg == str(mine)
    assert settings.load_settings(win.services.folder).ffmpeg_path == str(mine)


def test_a_new_folder_starts_under_default_and_default_is_a_reserved_name(app, tmp_path):
    dialog = RulesDialog([], str(tmp_path))
    tab = dialog.folders_tab
    assert [tab.list.item(i).text() for i in range(tab.list.count())] == ["Default"]    # nothing else filled in
    tab.new_item()
    assert tab.path.text() == "%default%\\New folder"
    tab.name.setText("default")
    tab.name.textEdited.emit("default")
    assert "Default" in tab.problems.text() and tab.items()[0].name == "New folder"


def test_a_folder_browsed_inside_default_is_written_under_percent_default(app, tmp_path):
    dialog = RulesDialog([], str(tmp_path / "Downloads"))
    tab = dialog.folders_tab
    tab.new_item()
    assert tab.written(str(tmp_path / "Downloads" / "Comics" / "Inbox")) == "%default%\\Comics\\Inbox"
    assert tab.written(str(tmp_path / "Downloads")) == "%default%"
    assert tab.written(str(tmp_path / "Elsewhere")) == str(tmp_path / "Elsewhere")
    tab.list.setCurrentRow(0)
    assert tab.written(str(tmp_path / "Downloads" / "Sub")) == str(tmp_path / "Downloads" / "Sub")   # Default: as is


def test_switching_rules_leaves_no_old_chips_behind(app, tmp_path):
    from PyQt6.QtWidgets import QPushButton
    library = rules.default_library()
    library.site_groups += [rules.SiteGroup("Humble", ["h.com"]), rules.SiteGroup("Comic sites", ["a.org"])]
    dialog = RulesDialog([Rule("H", [Condition("sites", ["Humble"])]), Rule("C", [Condition("sites", ["Comic sites"])])],
                         str(tmp_path), library=library)
    for row in (1, 0, 1):
        dialog.list.setCurrentRow(row)
    chips = [c for c in dialog.form.sites.findChildren(QPushButton)]
    assert [c.text() for c in chips] == ["Comic sites  ✕"]          # the old rule's chips are gone, not just queued


# ---- series of links (2026-11-05, 29A/30B) --------------------------------------------------------------

def test_a_series_in_the_paste_bar_opens_the_add_box_folded(world):
    win, engine, prober, base = world
    for n in (1, 2, 4):                                   # 3 does not exist on the server
        prober.serve(f"https://a.org/saga-00{n}.cbz", f"saga-00{n}.cbz", size=1000)
    seen = {}

    def look(dialog):
        seen["rows"] = [[dialog.table.item(r, c).text() for c in range(1, 5)] for r in range(dialog.table.rowCount())]
        seen["check"] = dialog.table.item(0, 0).checkState()
        seen["button"] = dialog.add_button.text()
        return True
    win._show_add_dialog = lambda dialog: (win.flush(), dialog.collect(), look(dialog))[-1]
    win.paste.setText("https://a.org/saga-[001-004].cbz")
    win.add_from_bar()
    win.flush()
    assert len(seen["rows"]) == 1                                        # one folded row for the series
    name, folder, rule, status = seen["rows"][0]
    assert name.startswith("▸ saga-[001-004].cbz") and "(4 links)" in name
    assert status == "3 ready · 1 failed" and seen["button"] == "Add 3"
    assert seen["check"] == Qt.CheckState.Checked                         # the failed one does not count against it
    assert [r.url for r in win.queue.rows()] == [f"https://a.org/saga-00{n}.cbz" for n in (1, 2, 4)]
    assert win.paste.text() == ""


def test_a_series_unfolds_and_single_links_can_be_left_out(world):
    win, engine, prober, base = world
    for n in range(1, 4):
        prober.serve(f"https://a.org/p{n}.jpg", f"p{n}.jpg")
    prober.serve("https://b.org/one.zip", "one.zip")

    def choose(dialog):
        assert dialog.table.rowCount() == 2                               # the series, folded, and the plain link
        dialog._clicked(0, 2 - 1)                                         # click the series' name: unfold
        assert dialog.table.rowCount() == 5 and dialog.table.item(0, 1).text().startswith("▾")
        dialog.table.item(2, 0).setCheckState(Qt.CheckState.Unchecked)    # leave p2 out
        assert dialog.table.item(0, 0).checkState() == Qt.CheckState.PartiallyChecked
        dialog.set_folder(0, os.path.join(base, "Pictures"))              # the whole series somewhere else
        assert dialog.add_button.text() == "Add 3"
        return True
    open_box(win, "https://a.org/p[1-3].jpg\nhttps://b.org/one.zip", choose)
    win.flush()
    rows = {r.url: r for r in win.queue.rows()}
    assert set(rows) == {"https://a.org/p1.jpg", "https://a.org/p3.jpg", "https://b.org/one.zip"}
    assert rows["https://a.org/p1.jpg"].folder == os.path.join(base, "Pictures")


def test_a_huge_series_is_only_expanded_after_asking(world):
    win, engine, prober, base = world
    from accipio.gui import add_dialog
    asked = []
    original = add_dialog.AddDialog.confirm_many
    add_dialog.AddDialog.confirm_many = lambda self, link, total: asked.append(total) or False
    try:
        dialog = open_box(win, "https://a.org/[1-100]/[1-100].jpg", lambda d: False)
    finally:
        add_dialog.AddDialog.confirm_many = original
    assert asked == [10_000] and dialog.entries == {}                     # nothing checked against the site
    assert dialog.table.item(0, 4).text() == "10,000 links: not expanded" and dialog.chosen() == []


# ---- archives (trial, 2026-11-05, decision 43E) ----------------------------------------------------------------

def archive_arrives(win, engine, prober, url, files):
    from tests.test_unpack import zip_bytes
    add(win, prober, url, url.rsplit("/", 1)[1])
    row = win.queue.rows()[-1]
    engine.finish(row.gid, zip_bytes(files))
    win.queue.refresh()
    win.reload()
    return win.queue.store.get(row.id)


def test_the_archive_trial_is_switched_on_in_the_rules_window(world, monkeypatch):
    from accipio.gui.rules_dialog import RulesDialog as Dialog
    win, *_ = world
    assert not hasattr(SettingsDialog(win.services.settings, win), "unpack_archives")   # moved out of Settings

    def edit(dialog):
        tab = dialog.archives_tab
        assert dialog.tabs.tabText(dialog.tabs.indexOf(tab)) == "5 · Archives"
        assert not tab.unpack.isChecked() and not tab.after.isEnabled()            # off until he tries it
        tab.unpack.setChecked(True)
        tab.after.setCurrentIndex(tab.after.findData("delete"))
        return 1
    monkeypatch.setattr(Dialog, "exec", edit)
    win.open_rules()
    assert win.queue.unpack_on and win.queue.after_unpack == "delete"
    again = settings.load_settings(win.services.folder)
    assert (again.unpack_archives, again.after_unpack) == (True, "delete")


def test_cancel_in_the_rules_window_leaves_the_archive_trial_alone(world, monkeypatch):
    from accipio.gui.rules_dialog import RulesDialog as Dialog
    win, *_ = world

    def edit(dialog):
        dialog.archives_tab.unpack.setChecked(True)
        return 0
    monkeypatch.setattr(Dialog, "exec", edit)
    win.open_rules()
    assert not win.queue.unpack_on and not settings.load_settings(win.services.folder).unpack_archives


def test_the_archives_switch_shows_the_rule_forms_archive_row_at_once(app, tmp_path):
    dialog = RulesDialog(samples.sorting_rules(), str(tmp_path), library=samples.sorting_library())
    dialog.list.setCurrentRow(0)
    assert dialog.form.archives.isHidden()
    dialog.archives_tab.unpack.setChecked(True)
    assert not dialog.form.archives.isHidden()
    dialog.archives_tab.unpack.setChecked(False)
    assert dialog.form.archives.isHidden()


def test_the_archives_tab_lists_the_rules_that_unpack(app, tmp_path):
    rule_list = samples.sorting_rules()
    dialog = RulesDialog(rule_list, str(tmp_path), library=samples.sorting_library(), archives=True)
    assert "No rule says yet" in dialog.archives_tab.by_rules.text()
    dialog.list.setCurrentRow(2)
    dialog.form.archives.setCurrentIndex(dialog.form.archives.findData("folder"))
    dialog.tabs.setCurrentWidget(dialog.archives_tab)
    assert f"3  {rule_list[2].name}: unpacked as a folder there" in dialog.archives_tab.by_rules.text()


def test_a_waiting_archive_shows_its_box_with_the_suggestion_first(world):
    win, engine, prober, base = world
    win.queue.unpack_on = True
    row = archive_arrives(win, engine, prober, "https://a.org/tool.zip", {"tool/setup.exe": b"MZ", "tool/readme.pdf": b"%PDF"})
    assert win.model.data(win.model.index(0, STATUS)) == "2 files inside: what now?"
    box = win.archive_box
    assert not box.isHidden() and box.row_id == row.id
    assert box.title.text() == "tool.zip: 2 files inside. What now?"
    assert box.buttons[0] is box.folder_button and box.folder_button.isDefault()   # a program inside
    assert box.folder_choice.currentData() == "Programs"               # where the program itself would go
    assert "setup.exe → Programs\\tool" in re.sub(r"\s+→\s+", " → ", box.preview.text().replace("/", "\\"))


def test_hovering_a_button_previews_where_the_files_would_go(world):
    win, engine, prober, base = world
    win.queue.unpack_on = True
    archive_arrives(win, engine, prober, "https://a.org/tool.zip", {"tool/setup.exe": b"MZ", "tool/readme.pdf": b"%PDF"})
    box = win.archive_box
    box.show_plan("sort")
    text = re.sub(r"\s+→\s+", " → ", box.preview.text())                 # names are padded into a column
    assert "setup.exe → Programs" in text and "readme.pdf → Documents" in text


def test_choosing_unpacks_and_the_tick_writes_a_rule_on_top(world):
    win, engine, prober, base = world
    win.queue.unpack_on = True
    row = archive_arrives(win, engine, prober, "https://a.org/tool.zip", {"tool/setup.exe": b"MZ"})
    box = win.archive_box
    box.remember.setChecked(True)
    box.folder_button.click()
    win.flush()
    assert os.path.isfile(os.path.join(base, "Programs", "tool", "setup.exe"))
    assert win.queue.store.get(row.id).unpack["state"] == "done"
    first = win.services._holder["rules"][0]
    assert (first.name, first.target, first.unpack) == ("Archives", "Programs", "folder")
    saved = settings.load_rules(win.services.folder)
    assert saved[0].unpack == "folder"
    assert box.isHidden()


def test_a_rule_that_unpacked_by_itself_offers_undo(world):
    win, engine, prober, base = world
    win.queue.unpack_on = True
    win.services._holder["rules"].insert(0, rules.remembered_rule("Archives", "", "sort", "Archives"))
    row = archive_arrives(win, engine, prober, "https://a.org/pack.zip", {"Saga 001.cbz": b"PK"})
    box = win.archive_box
    assert not box.isHidden() and "its rule unpacked 1 file" in box.title.text()
    box.undo_button.click()
    win.flush()
    assert not os.path.exists(os.path.join(base, "Comics", "Saga 001.cbz"))
    assert win.queue.store.get(row.id).unpack["state"] == "waiting"


def test_keep_packed_closes_the_box(world):
    win, engine, prober, base = world
    win.queue.unpack_on = True
    row = archive_arrives(win, engine, prober, "https://a.org/pack.zip", {"a.nfo": b"x"})
    win.archive_box.keep_button.click()
    win.flush()
    assert win.queue.store.get(row.id).unpack == {"state": "kept"} and win.archive_box.isHidden()


def test_a_rule_can_say_what_happens_to_archives(world):
    win, *_ = world
    dialog = RulesDialog(win.services._holder["rules"], win.services.settings.base_folder, win,
                         library=win.services._holder["library"], archives=True)
    form = dialog.form
    form.load(rules.Rule("Packs", [Condition("type", ["Archives"])], target="Comics", unpack="sort"))
    assert not form.archives.isHidden() and form.archives.currentData() == "sort"
    form.archives.setCurrentIndex(form.archives.findData("folder"))
    assert form.rule().unpack == "folder"
