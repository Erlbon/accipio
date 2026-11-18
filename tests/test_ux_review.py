"""The fixes from the review of the menus and the UX (2026-11-17, docs/ux-review-2026-11-17.md): one test or more
per card, named by its number. Offscreen, everything under tmp_path, the fake engine and prober of the queue tests.
The Other Accipios window (answers 112B and 115B, card 20) is in test_link_window.py."""
import os
import re
import sqlite3

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6.QtCore import QPoint, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QPalette  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox, QPushButton, QSizePolicy, QSystemTrayIcon  # noqa: E402

from accipio.core import rules, settings  # noqa: E402
from accipio.core.store import ACTIVE, PAUSED, SCHEMA_VERSION, Row, Store  # noqa: E402
from accipio.gui import browser_help, main_window as mw, queue_model as qm, style  # noqa: E402
from accipio.gui.rules_dialog import RulesDialog  # noqa: E402
from accipio.gui.settings_dialog import PAGES, SettingsDialog  # noqa: E402
from tests import samples  # noqa: E402
from tests.test_gui import Engine, RefusingToaster, add, app, select, world  # noqa: E402,F401
from tests.test_queue import FakeProber  # noqa: E402


def palette_of(window: str, base: str, text: str) -> QPalette:
    palette = QPalette()
    for role, colour in ((QPalette.ColorRole.Window, window), (QPalette.ColorRole.Base, base),
                         (QPalette.ColorRole.Text, text), (QPalette.ColorRole.WindowText, text)):
        palette.setColor(role, QColor(colour))
    return palette


LIGHT = ("#efefef", "#ffffff", "#000000")
DARK = ("#2d2d2d", "#1e1e1e", "#e0e0e0")


def texts(menu):
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def open_window(folder):
    win = mw.MainWindow(mw.open_services(folder, engine=Engine(), prober=FakeProber(), video_engine=False),
                        tick_ms=60_000)
    win.toaster = RefusingToaster()
    win.read_browsers = lambda: []
    return win


# ---- the menus -------------------------------------------------------------------------------------------------

def test_1_clear_finished_is_in_the_downloads_menu_under_remove_and_settings_point_there(world):
    win = world[0]
    assert "Clear Fi&nished" not in texts(win.menus["File"])
    assert texts(win.menus["Downloads"])[-2:] == ["Re&move from List", "Clear Fi&nished"]
    assert "Downloads > Clear Finished" in SettingsDialog(win.services.settings, win).clear_note.text()


def test_8_a_switched_off_menu_line_is_grey_and_has_no_highlight_in_light_and_dark(app):
    for colours in (LIGHT, DARK):
        sheet = style.stylesheet(palette_of(*colours))
        grey = re.search(r"QMenu::item:disabled \{ color: (#[0-9a-f]{6}); \}", sheet).group(1)
        assert grey not in (colours[0], colours[2])                 # neither the text's colour nor invisible
        assert f"QMenu::item:disabled:selected {{ background: transparent; color: {grey}; }}" in sheet


def test_9_the_downloads_menu_follows_the_selection_and_so_does_the_right_click_menu(world, monkeypatch):
    win, engine, prober, base = world
    lines = ("pause", "resume", "retry", "open_file", "open_folder", "copy_link", "open_in_browser", "why_here", "remove")
    on = lambda: {key for key in lines if win.actions_by_key[key].isEnabled()}          # noqa: E731
    assert on() == set()                                             # nothing selected: nothing to act on
    assert all(win.actions_by_key[key].isEnabled() for key in ("pause_all", "resume_all", "clear_finished", "add_links"))
    for name in ("a.zip", "b.zip"):
        add(win, prober, f"https://a.org/{name}", name)
    assert on() == set()
    select(win, 0)                                                   # a running one
    running = {"pause", "open_folder", "copy_link", "open_in_browser", "why_here", "remove"}
    assert on() == running
    seen = []
    monkeypatch.setattr(mw.QMenu, "exec", lambda menu, *a: seen.append(
        {a.text() for a in menu.actions() if a.isEnabled() and not a.isSeparator()}))
    win.show_context_menu(QPoint(5, 5))                              # the right-click menu: the same lines on
    assert seen[0] == {win.actions_by_key[key].text() for key in running}
    assert on() == running                                           # and nothing is switched back on afterwards
    win.pause_selected()
    win.flush()                                                      # the row changed while selected
    assert on() == running | {"resume"}                              # Pause stays on: Space, its key, now resumes
    win.pause_selected()
    win.flush()
    engine.fail(win.model.rows[0].gid, "3")
    win.tick()
    win.flush()
    assert on() == {"retry", "open_folder", "copy_link", "open_in_browser", "why_here", "remove"}
    win.table.clearSelection()
    assert on() == set()
    # the one function, asked directly
    assert not any(mw.MainWindow.fitting([]).values())
    both = mw.MainWindow.fitting([Row(1, "u", state=ACTIVE), Row(2, "u", state=PAUSED)])
    assert both["pause"] and both["resume"] and not both["retry"] and not both["open_file"]


def test_10_the_right_click_menu_has_open_link_in_browser_after_copy_link_and_open_has_a_letter(world, monkeypatch):
    win, engine, prober, base = world
    seen = []
    monkeypatch.setattr(mw.QMenu, "exec", lambda menu, *a: seen.append(texts(menu)))
    add(win, prober, "https://a.org/a.zip", "a.zip")
    select(win, 0)
    win.show_context_menu(QPoint(5, 5))
    at = seen[0].index("&Copy Link")
    assert seen[0][at + 1] == "Open Link in &Browser" and seen[0][3] == "&Open"
    assert win.actions_by_key["open_file"].text() == "&Open"


def test_11_pause_all_and_resume_all_act_on_every_download_whatever_is_selected(world, monkeypatch):
    win, engine, prober, base = world
    for name in ("a.zip", "b.zip", "c.zip"):
        add(win, prober, f"https://a.org/{name}", name)
    assert texts(win.menus["Downloads"])[3:5] == ["Pause A&ll", "Resume &All"]
    win.table.clearSelection()
    win.actions_by_key["pause_all"].trigger()
    win.flush()
    assert [r.state for r in win.model.rows] == [PAUSED] * 3
    win.actions_by_key["resume_all"].trigger()
    win.flush()
    assert [r.state for r in win.model.rows] == [ACTIVE] * 3
    # the tray's menu has the same two lines
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    tray = win._build_tray()
    try:
        assert texts(tray.contextMenu()) == ["Show Accipio", "Pause A&ll", "Resume &All", "E&xit"]
    finally:
        tray.hide()


def test_12_exit_shows_ctrl_q(world):
    assert world[0].actions_by_key["exit"].shortcut().toString() == "Ctrl+Q"


def test_113_the_word_is_settings_everywhere(world, monkeypatch):
    win = world[0]
    assert win.actions_by_key["preferences"].text() == "&Settings…" == texts(win.menus["Tools"])[-1]
    assert win.settings_button.text() == "Settings…"
    assert SettingsDialog(win.services.settings, win).windowTitle() == "Settings"
    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text, *a: told.append(text))
    win.open_debug_log()                                             # no log yet: it says where to switch it on
    assert "Tools > Settings > Program" in told[0] and "Preferences" not in told[0]


# ---- the main window's columns -----------------------------------------------------------------------------------

def test_114_a_first_start_has_status_second_and_every_column_in_sight_at_1000_px(world):
    win = world[0]
    header = win.table.horizontalHeader()
    assert [qm.COLUMNS[header.logicalIndex(v)] for v in range(header.count())] == \
        ["Name", "Status", "Progress", "Size", "Speed", "Time left", "Folder", "Rule"]
    assert qm.COLUMNS[-1] == "Status"                                # the model's own order is as it was
    assert win.width() == 1000
    win.show()
    QApplication.processEvents()
    assert win.table.horizontalScrollBar().maximum() == 0           # no sideways scroll bar
    assert header.length() <= win.table.viewport().width()
    assert all(header.sectionSize(c) >= 60 for c in range(header.count()))
    win.hide()


def test_114_a_header_arranged_before_is_kept(app, tmp_path):
    folder = str(tmp_path / "config")
    settings.save_settings(folder, settings.Settings(base_folder=str(tmp_path / "Downloads")))
    first = open_window(folder)
    header = first.table.horizontalHeader()
    for column in range(header.count()):                             # as a window.ini from before the change has it
        header.moveSection(header.visualIndex(column), column)
    header.resizeSection(qm.NAME, 333)
    first.close()
    again = open_window(folder)
    header = again.table.horizontalHeader()
    assert [header.logicalIndex(v) for v in range(header.count())] == list(range(len(qm.COLUMNS)))
    assert header.sectionSize(qm.NAME) == 333
    again.close()


def test_13_columns_are_hidden_from_view_and_the_header_and_stay_hidden(app, tmp_path, monkeypatch):
    folder = str(tmp_path / "config")
    settings.save_settings(folder, settings.Settings(base_folder=str(tmp_path / "Downloads")))
    win = open_window(folder)
    header = win.table.horizontalHeader()
    columns = next(a.menu() for a in win.menus["View"].actions() if a.menu() is not None)
    assert texts(columns) == qm.COLUMNS and all(a.isChecked() for a in columns.actions())
    assert not win.actions_by_key[f"column_{qm.NAME}"].isEnabled()          # Name always shows
    win.actions_by_key[f"column_{qm.SPEED}"].trigger()
    assert header.isSectionHidden(qm.SPEED) and not win.actions_by_key[f"column_{qm.SPEED}"].isChecked()
    win.show_column(qm.NAME, False)
    assert not header.isSectionHidden(qm.NAME)
    seen = []
    monkeypatch.setattr(mw.QMenu, "exec", lambda menu, *a: seen.append([(a.text(), a.isChecked()) for a in menu.actions()]))
    win.show_column_menu(QPoint(5, 5))                               # the header's right-click: the same ticks
    assert seen[0] == [(name, name != "Speed") for name in qm.COLUMNS]
    win.close()
    again = open_window(folder)                                      # remembered with the widths
    assert again.table.horizontalHeader().isSectionHidden(qm.SPEED)
    assert not again.actions_by_key[f"column_{qm.SPEED}"].isChecked()
    again.actions_by_key[f"column_{qm.SPEED}"].trigger()
    assert not again.table.horizontalHeader().isSectionHidden(qm.SPEED)
    again.close()


def test_17_a_header_stands_as_its_column_does(world):
    win = world[0]
    horizontal, role = Qt.Orientation.Horizontal, Qt.ItemDataRole.TextAlignmentRole
    for column in range(len(qm.COLUMNS)):
        side = Qt.AlignmentFlag.AlignRight if column in (qm.SIZE, qm.SPEED, qm.ETA) else Qt.AlignmentFlag.AlignLeft
        assert win.model.headerData(column, horizontal, role) & int(side)


# ---- the list ------------------------------------------------------------------------------------------------------

def test_14_a_paused_bar_is_grey_so_the_accent_colour_means_moving(app):
    for colours in (LIGHT, DARK):
        palette = palette_of(*colours)
        accent = palette.color(QPalette.ColorRole.Highlight)
        paused = qm.ProgressDelegate.fill(Row(1, "u", state=PAUSED, done=1, total=4), palette)
        assert paused != accent and paused.red() == paused.green() == paused.blue()     # a grey
        assert qm.ProgressDelegate.fill(Row(1, "u", state=ACTIVE, done=1, total=4), palette) == accent


def test_15_the_empty_list_says_how_to_start_and_stops_saying_it(world):
    win, engine, prober, base = world
    assert win.table.empty_text.startswith("Nothing here yet. Paste a link above")
    assert not win.table.viewport().grab().isNull()                  # it paints
    add(win, prober, "https://a.org/a.zip", "a.zip")
    assert win.table.empty_text == ""
    select(win, 0)
    win.queue.remove(win.model.rows[0].id)
    win.reload()
    assert win.table.empty_text.startswith("Nothing here yet")


def test_16_the_browser_bar_is_a_pale_wash_in_light_and_the_brown_in_dark(app):
    background, text = style.notice_colors(palette_of(*LIGHT))
    assert QColor(background).lightness() > 180 and QColor(text).lightness() < 80
    assert style.notice_colors(palette_of(*DARK)) == ("#5c4a14", "#fbe9b7")
    bar = browser_help.BrowserBar()
    now = style.notice_colors(QApplication.palette())
    assert now[0] in bar.styleSheet() and f"color:{now[1]}" in bar.text.text()


def test_18_the_rule_column_shows_the_rules_name_kept_with_the_row(world):
    win, engine, prober, base = world
    add(win, prober, "https://a.org/tool.zip", "tool.zip")
    row = win.model.rows[0]
    number = row.rules
    assert row.rule_names == "Archives" and number.isdigit()
    index = win.model.index(0, qm.RULE)
    assert win.model.data(index) == "Archives"
    assert f"Rule {number} in the list" in win.model.data(index, Qt.ItemDataRole.ToolTipRole)
    assert f'rule {number} "Archives"' in row.why                    # Why Here? still has the number
    # the rules are reordered afterwards: the row still says which rule placed it
    win.apply_rules(list(reversed(win.services.rules)))
    win.reload()
    assert win.model.data(win.model.index(0, qm.RULE)) == "Archives"
    # a row from before the names were kept shows the number it has
    assert qm.QueueModel.rule_shown(Row(1, "u", rules="4")) == "4"
    step = lambda n, name: rules.Step(n, name, [], True)             # noqa: E731
    assert rules.Placement("f", "n", [], [step(2, "Images"), step(5, "  ")]).rule_names == "Images + 5"


def test_18_a_queue_file_from_before_gets_the_new_column(tmp_path):
    path = str(tmp_path / "queue.db")
    store = Store(path)
    row_id = store.add("https://a.org/a.zip", rules="4", rule_names="Images")
    store.close()
    old = sqlite3.connect(path)
    old.execute("ALTER TABLE downloads DROP COLUMN rule_names")
    old.execute("PRAGMA user_version=3")
    old.commit()
    old.close()
    store = Store(path)
    row = store.get(row_id)
    assert (row.rules, row.rule_names) == ("4", "")
    store.update(row_id, rule_names="Images")
    assert store.get(row_id).rule_names == "Images"
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 4
    store.close()


def test_19_a_failed_operation_stays_in_the_status_bar_until_clicked_and_can_be_copied(world):
    win = world[0]
    assert win.problem_button.isHidden()

    def broken():
        raise OSError("Access is denied: the folder " + "x" * 200)
    win.run(broken)
    win.flush()
    assert not win.problem_button.isHidden()
    shown = win.problem_button.text()
    assert shown.startswith("Problem: OSError: Access is denied") and shown.endswith("…") and len(shown) < 110
    win.reload()
    assert not win.problem_button.isHidden()                         # it does not go away by itself
    boxes = []
    win._show_problem_box = lambda text: boxes.append(text) or True  # the box, with Copy chosen
    win.problem_button.click()
    assert "Traceback" in boxes[0] and "x" * 200 in boxes[0]         # in full
    assert "x" * 200 in QApplication.clipboard().text() and QApplication.clipboard().text().startswith("Accipio ")
    assert win.problem_button.isHidden() and win.problem_text == ""


# ---- Settings (answer 111B, cards 2 to 7) ---------------------------------------------------------------------------

def test_111_settings_is_six_pages_with_a_list_on_the_left(world):
    win = world[0]
    dialog = SettingsDialog(win.services.settings, win)
    assert [dialog.side.item(i).text() for i in range(dialog.side.count())] == list(PAGES) == \
        ["Downloads", "Video sites", "Browser", "Torrents", "Notifications", "Program"]
    home = {"Downloads": (dialog.max_downloads, dialog.connections, dialog.speed_limit, dialog.clear_after_minutes),
            "Video sites": (dialog.video_quality, dialog.ffmpeg_label, dialog.ask_playlists),
            "Browser": (dialog.file_browser_saves, dialog.browser_note),
            "Torrents": (dialog.torrents, dialog.qbittorrent_label),
            "Notifications": (dialog.notify_added, dialog.notify_finished, dialog.notify_failed),
            "Program": (dialog.check_updates, dialog.debug_log, dialog.debug_delete)}
    for name, widgets in home.items():
        assert all(dialog.pages[name].isAncestorOf(widget) for widget in widgets), name
    assert dialog.stack.currentWidget() is dialog.pages["Downloads"]
    dialog.side.setCurrentRow(3)
    assert dialog.stack.currentWidget() is dialog.pages["Torrents"]
    dialog.show_page("Program")
    assert dialog.side.currentRow() == 5
    assert dialog.sizeHint().height() < 700                          # card 6: it fits a small screen


def test_3_number_boxes_and_drop_downs_keep_the_width_of_their_content(world):
    dialog = SettingsDialog(world[0].services.settings, world[0])
    for widget in (dialog.max_downloads, dialog.connections, dialog.speed_limit, dialog.video_quality, dialog.torrents):
        assert widget.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Fixed


def test_4_what_a_torrent_choice_means_stands_under_it(world):
    from accipio.core import torrents
    dialog = SettingsDialog(world[0].services.settings, world[0], find_qbittorrent=lambda chosen: "")
    assert "refused" in dialog.torrents_note.text()
    dialog.torrents.setCurrentIndex(dialog.torrents.findData(torrents.SYSTEM))
    assert "any torrent program" in dialog.torrents_note.text()
    dialog.torrents.setCurrentIndex(dialog.torrents.findData(torrents.QBITTORRENT))
    assert "given to qBittorrent" in dialog.torrents_note.text()
    assert not dialog.torrents.toolTip()                             # nothing is said only in a tooltip


def test_5_the_labels_name_their_setting(world):
    from PyQt6.QtWidgets import QFormLayout
    dialog = SettingsDialog(world[0].services.settings, world[0])
    labels = []
    for page in dialog.pages.values():
        form = page.layout()
        labels += [form.itemAt(r, QFormLayout.ItemRole.LabelRole).widget().text() for r in range(form.rowCount())]
    for wanted in ("Video quality", "Finished downloads", "Notices and sounds"):
        assert wanted in labels
    assert not {"Video sites", "Finished", "Notify when", "Other Accipios"} & set(labels)


def test_2_the_two_signposts_open_rules_on_their_tab_and_ok_keeps_what_rules_changed(world, tmp_path, monkeypatch):
    win = world[0]
    opened = []
    dialog = SettingsDialog(win.services.settings, win, open_rules=lambda tab, parent: opened.append((tab, parent)),
                            base_folder_of=lambda: "D:\\Elsewhere" if opened else win.services.settings.base_folder)
    assert "Rules &gt; 1 · Places" in dialog.where.text() and win.services.settings.base_folder in dialog.where.text()
    dialog.where.linkActivated.emit("places")
    assert opened == [("places", dialog)] and "D:\\Elsewhere" in dialog.where.text()      # shown again afterwards
    dialog._go_to_rules("archives")
    assert opened[-1][0] == "archives"
    # the real thing: Rules opens on the tab asked for, over the window that asked
    seen = []
    monkeypatch.setattr(win, "_show_rules_dialog",
                        lambda box: seen.append((box.tabs.tabText(box.tabs.currentIndex()), box.parent())) or False)
    win.open_rules("archives", dialog)
    win.open_rules("places")
    win.open_rules()
    assert seen == [("5 · Archives", dialog), ("1 · Places", win), ("4 · Rules", win)]
    # OK lays Settings' fields onto the settings as they are then: the Default folder Rules changed stays changed
    dialog.max_downloads.setValue(9)
    live = settings.Settings(base_folder=str(tmp_path / "moved"), unpack_archives=True, browser_connected=True)
    result = dialog.result_settings(live)
    assert (result.base_folder, result.unpack_archives, result.browser_connected, result.max_downloads) == \
        (str(tmp_path / "moved"), True, True, 9)


# ---- Rules and Connect your browser ----------------------------------------------------------------------------------

def test_21_the_four_list_tabs_have_the_same_three_buttons_and_copy_copies(app, tmp_path):
    dialog = RulesDialog(samples.sorting_rules(), str(tmp_path), library=samples.sorting_library())
    for tab in (dialog.places_tab, dialog.groups_tab, dialog.sites_tab, dialog.tabs.widget(3)):
        names = [b.text() for b in tab.findChildren(QPushButton)]
        assert [n for n in names if n in ("+ New", "Copy", "Delete", "+", "−")] == ["+ New", "Copy", "Delete"]
    tab = dialog.groups_tab
    tab.list.setCurrentRow(1)
    original = tab.items()[1]
    count = tab.list.count()
    tab.copy_item()
    copy = tab.items()[2]
    assert tab.list.count() == count + 1 and tab.list.currentRow() == 2
    assert copy.name == f"{original.name} (copy)" and copy.extensions == original.extensions
    tab.list.setCurrentRow(1)
    tab.copy_item()                                                  # a second copy gets a name of its own
    assert len({item.name.lower() for item in tab.items()}) == tab.list.count()
    # one background for all five pages: each paints the window's colour itself
    for index in range(dialog.tabs.count()):
        page = dialog.tabs.widget(index)
        assert page.autoFillBackground() and page.backgroundRole() == QPalette.ColorRole.Window


def test_22_the_connect_window_takes_its_height_from_its_text_at_its_width(app, tmp_path):
    firefox = tmp_path / "accipio-firefox.xpi"
    firefox.write_bytes(b"PK")
    dialog = browser_help.ConnectDialog(folder=str(tmp_path / "extension"), firefox_file=str(firefox))
    layout = dialog.layout()
    # the width first (a long path that cannot wrap may widen it), then the height the text needs at THAT width
    assert dialog.width() == max(browser_help.WIDTH, layout.totalMinimumSize().width())
    assert dialog.height() == max(layout.totalHeightForWidth(dialog.width()), layout.totalMinimumSize().height())
    # "Copy file" is in the grid, in the row of its own paragraph, in the buttons' column
    grid = layout.itemAt(1).layout()
    at = grid.indexOf(dialog.copy_firefox)
    row, column, _, _ = grid.getItemPosition(at)
    assert column == 2 and grid.itemAtPosition(row, 0).widget().text().startswith("<b>Firefox</b>")
