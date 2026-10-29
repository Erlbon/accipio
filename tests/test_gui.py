"""The window and the rules editor, offscreen, with the fake engine and prober from the queue tests."""
import datetime
import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QHeaderView, QMessageBox  # noqa: E402

from accipio.core import cron, rules, settings  # noqa: E402
from accipio.core.rules import Condition, Rule  # noqa: E402
from accipio.core.store import ACTIVE, COMPLETE, PAUSED  # noqa: E402
from accipio.gui import main_window as mw  # noqa: E402
from accipio.gui.queue_model import COLUMNS, NAME  # noqa: E402
from accipio.gui.rules_dialog import ConditionRow, RulesDialog, TimeEditor, picker_state  # noqa: E402
from accipio.gui.settings_dialog import SettingsDialog  # noqa: E402
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
    engine, prober = Engine(), FakeProber()
    win = mw.MainWindow(mw.open_services(folder, engine=engine, prober=prober, video_engine=False), tick_ms=60_000)
    yield win, engine, prober, str(tmp_path / "Downloads")
    win.close()


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
    assert "https://a.org/a.zip" in tip and 'Why here: rule 7 "Archives"' in tip


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
    dialog.base_folder.setText(str(tmp_path / "Elsewhere"))
    win.apply_settings(dialog.result_settings())
    win.flush()
    assert engine.limits[-1] == {"max_downloads": 5, "speed_limit": 300 * 1024}
    again = settings.load_settings(win.services.folder)
    assert (again.max_downloads, again.keep_history, again.base_folder) == (5, True, str(tmp_path / "Elsewhere"))
    add(win, prober, "https://a.org/a.zip", "a.zip")
    assert win.model.rows[0].folder == os.path.join(str(tmp_path / "Elsewhere"), "Archives")


def test_closing_stops_the_engine_and_remembers_the_columns(world):
    win, engine, prober, base = world
    win.table.horizontalHeader().resizeSection(NAME, 333)
    win.close()
    assert engine.stopped and os.path.isfile(os.path.join(win.services.folder, "window.ini"))


# ---- the rules editor ------------------------------------------------------------------------------------

def test_the_dialog_lists_the_rules_with_stop_and_continue_marks(app, tmp_path):
    rule_list = [Rule("Work", [Condition("domain", ["knowit.no"])], folder="Work", stop=False), Rule("Other", subfolder="Other")]
    dialog = RulesDialog(rule_list, str(tmp_path))
    assert [dialog.list.item(i).text() for i in range(2)] == ["1  Work   →", "2  Other   ■"]
    assert dialog.result_rules() == rule_list
    assert rule_list[0].name == "Work"                                    # the caller's list is untouched until OK


def test_editing_a_rule_in_the_form(app, tmp_path):
    dialog = RulesDialog(rules.default_rules(), str(tmp_path))
    dialog.list.setCurrentRow(6)                                          # Archives
    dialog.form.subfolder.setText("Archives/{yyyy}")
    dialog.form.go_on.setChecked(True)
    result = dialog.result_rules()
    assert result[6].subfolder == "Archives/{yyyy}" and result[6].stop is False
    assert dialog.list.item(6).text().endswith("→")


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
    dialog.form.folder.setText("{nope}")
    assert "unknown token {nope}" in dialog.form.problems.text()
    dialog.form.folder.setText("{yyyy}")
    assert dialog.form.problems.text() == ""


def test_the_test_box_shows_the_path_and_the_skipped_rules(app, tmp_path):
    base = str(tmp_path / "Downloads")
    dialog = RulesDialog(rules.default_rules(), base)
    dialog.test_url.setText("https://example.com/files/Dune.epub")
    assert os.path.join(base, "Books", "Dune.epub") in dialog.test_result.text()
    assert 'rule 5 "Ebooks and comics"' in dialog.test_result.text() and "Skipped: rule 1, 2, 3, 4, 6" in dialog.test_result.text()
    dialog.test_video.setChecked(True)
    dialog.test_channel.setText("XYZ")
    assert os.path.join(base, "Video", "XYZ") in dialog.test_result.text()


def test_the_test_box_uses_unsaved_edits(app, tmp_path):
    dialog = RulesDialog(rules.default_rules(), str(tmp_path))
    dialog.list.setCurrentRow(4)
    dialog.form.subfolder.setText("Library")
    dialog.test_url.setText("https://example.com/Dune.epub")
    assert os.path.join(str(tmp_path), "Library", "Dune.epub") in dialog.test_result.text()


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
    assert seen["rows"][0] == ["tool.zip", "Archives", "7", "Ready · 88.0 MB"]
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
