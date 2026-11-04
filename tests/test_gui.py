"""The window and the rules editor, offscreen, with the fake engine and prober from the queue tests."""
import datetime
import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QFileDialog, QHeaderView, QMessageBox  # noqa: E402

from accipio.core import cron, rules, settings  # noqa: E402
from accipio.core.rules import Condition, Rule  # noqa: E402
from accipio.core.store import ACTIVE, COMPLETE, PAUSED  # noqa: E402
from accipio.gui import main_window as mw  # noqa: E402
from accipio.gui.queue_model import COLUMNS, NAME  # noqa: E402
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


@pytest.fixture
def world(app, tmp_path):
    folder = str(tmp_path / "config")
    settings.save_settings(folder, settings.Settings(base_folder=str(tmp_path / "Downloads")))
    settings.save_rules(folder, samples.sorting_rules(), samples.sorting_library())
    engine, prober = Engine(), FakeProber()
    win = mw.MainWindow(mw.open_services(folder, engine=engine, prober=prober, video_engine=False), tick_ms=60_000)
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


def test_why_here_is_the_tooltip(world):
    win, engine, prober, base = world
    add(win, prober, "https://a.org/a.zip", "a.zip")
    tip = win.proxy.index(0, NAME).data(Qt.ItemDataRole.ToolTipRole)
    assert "https://a.org/a.zip" in tip and 'Why here: rule 8 "Archives"' in tip


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
    dialog.keep_history.setChecked(True)
    win.apply_settings(dialog.result_settings())
    win.flush()
    assert engine.limits[-1] == {"max_downloads": 5, "speed_limit": 300 * 1024}
    again = settings.load_settings(win.services.folder)
    assert (again.max_downloads, again.keep_history, again.base_folder) == (5, True, base)


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

def test_the_dialog_lists_the_rules_with_stop_and_continue_marks(app, tmp_path):
    rule_list = [Rule("Work", [Condition("domain", ["work.example"])], folder="Work", stop=False), Rule("Other", subfolder="Other")]
    dialog = RulesDialog(rule_list, str(tmp_path))
    assert [dialog.list.item(i).text() for i in range(2)] == ["1  Work   →", "2  Other   ■"]
    got = dialog.result_rules()
    assert (got[0].target, got[0].folder) == ("Work", "")                   # a typed path became a named folder
    assert rules.find_named(dialog.result_library().folders, "Work").path == "Work"
    assert rule_list[0].folder == "Work" and rule_list[0].target == ""      # the caller's list is untouched until OK


def test_editing_a_rule_in_the_form(app, tmp_path):
    dialog = RulesDialog(samples.sorting_rules(), str(tmp_path), library=samples.sorting_library())
    dialog.list.setCurrentRow(7)                                          # Archives
    dialog.form.subfolder.setText("{yyyy}")
    dialog.form.go_on.setChecked(True)
    result = dialog.result_rules()
    assert result[7].subfolder == "{yyyy}" and result[7].stop is False and result[7].target == "Archives"
    assert dialog.list.item(7).text().endswith("→")


def test_the_command_field_is_hidden_until_commands_run_but_a_saved_one_is_kept(app, tmp_path):
    saved = samples.sorting_rules()
    saved[7].command = 'notify "{name}"'
    dialog = RulesDialog(saved, str(tmp_path), library=samples.sorting_library())
    dialog.list.setCurrentRow(7)
    assert dialog.form.command.isHidden() and not dialog.form.actions.isRowVisible(dialog.form.command)
    dialog.form.subfolder.setText("{yyyy}")
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
    dialog.form.subfolder.setText("{nope}")
    assert "unknown token {nope}" in dialog.form.problems.text()
    dialog.form.subfolder.setText("{yyyy}")
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
    dialog.list.setCurrentRow(4)
    dialog.form.subfolder.setText("Library")
    dialog.test_url.setText("https://example.com/Dune.epub")
    assert os.path.join(str(tmp_path), "Books", "Library", "Dune.epub") in dialog.test_result.text()


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

def test_the_window_has_the_four_tabs_in_setup_order(app, tmp_path):
    dialog = RulesDialog(samples.sorting_rules(), str(tmp_path), library=samples.sorting_library())
    assert [dialog.tabs.tabText(i) for i in range(4)] == ["1 · Folders", "2 · File Groups", "3 · Site Groups", "4 · Rules"]
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


def test_the_rule_form_picks_a_folder_and_a_site_group(app, tmp_path):
    library = samples.sorting_library()
    library.site_groups.append(rules.SiteGroup("Comic sites", ["getcomics.org"]))
    dialog = RulesDialog([Rule("Comics", [Condition("sites", ["Comic sites"])], target="Comics")], str(tmp_path),
                         library=library)
    assert dialog.form.folder.currentData() == "Comics"
    assert dialog.form.rows[0].kind.currentData() == "sites" and dialog.form.rows[0].site_values.text() == "Comic sites"
    dialog.form.folder.setCurrentIndex(dialog.form.folder.findData("Books"))
    assert dialog.result_rules()[0].target == "Books"
    dialog.form.rows[0].site_values.setText("Nope")
    assert "no site group called 'Nope'" in dialog.form.problems.text()


def test_the_renamer_tries_its_lines_on_a_name(app, tmp_path):
    rule = Rule("TV", renames=[rules.Rename(r"(.+?)[ .]S(\d\d)E(\d\d)", "{1|dots|title} S{2}E{3}.{ext}")])
    dialog = RulesDialog([rule], str(tmp_path))
    renamer = dialog.form.renamer
    renamer.sample.setText("the.expanse.s02e05.1080p.web.mkv")
    assert renamer.result.text() == "→ The Expanse S02E05.mkv   (line 1)"
    renamer.sample.setText("holiday.mkv")
    assert "name kept" in renamer.result.text()
    renamer.add_line()
    renamer.pattern.setText("holiday")
    renamer.to.setText("Holiday.{ext}")
    assert [line.pattern for line in dialog.result_rules()[0].renames] == [r"(.+?)[ .]S(\d\d)E(\d\d)", "holiday"]
    assert renamer.result.text() == "→ Holiday.mkv   (line 2)"


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
