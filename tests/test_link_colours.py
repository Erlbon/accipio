"""The colour for downloads that belong to another Accipio (answers 105B, 106B, 107A, 108A, 109A, 110A): a tinted
row in the main list, the Add box and the Places list, and the link colour in the progress bar. Offscreen, under
tmp_path, no keyring and no network."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QBrush, QColor, QPalette  # noqa: E402
from PyQt6.QtWidgets import QHeaderView, QTableView  # noqa: E402

from accipio.core import handover, rules  # noqa: E402
from accipio.core.rules import Folder  # noqa: E402
from accipio.core.store import ACTIVE, COMPLETE, ERROR, SENT, WAITING, Row  # noqa: E402
from accipio.gui import add_dialog, style  # noqa: E402
from accipio.gui import queue_model as qm  # noqa: E402
from accipio.gui.library_tabs import REMOTE_PLACE_TIP  # noqa: E402
from tests.test_gui import app  # noqa: E402,F401
from tests.test_link_window import add_box, preview_for, win  # noqa: E402,F401
from tests.test_places import dialog_for, select  # noqa: E402

AWAY = {"peer": "c0ffee", "place": "Collector", "ref": "r1"}
CAME = {"from": "beef01", "from_name": "Laptop", "ref": "r2"}


def palette(base: str, text: str, highlight: str = "#2b6cc4") -> QPalette:
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Base, QColor(base))
    pal.setColor(QPalette.ColorRole.Text, QColor(text))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(highlight))
    return pal


DARK, LIGHT = palette("#1e1e1e", "#dcdcdc", "#5aa0f0"), palette("#ffffff", "#000000")


def rows(tmp_path):
    comics = str(tmp_path / "Comics")
    return [
        Row(1, "https://x.org/a.iso", filename="a.iso", folder=str(tmp_path / "Software"), state=ACTIVE, done=5, total=10),
        Row(2, "https://x.org/1.cbz", filename="1.cbz", state=WAITING, remote=dict(AWAY)),
        Row(3, "https://x.org/2.cbz", filename="2.cbz", state=SENT, remote={**AWAY, "id": 7}),
        Row(4, "https://x.org/3.cbz", filename="3.cbz", state=COMPLETE,
            remote={**AWAY, "id": 8, "outcome": "Finished on Collector, in Comics"}),
        Row(5, "https://x.org/4.cbz", filename="4.cbz", state=ERROR, error="Failed on Collector: 404", remote={**AWAY, "id": 9}),
        Row(6, "https://x.org/5.cbz", filename="5.cbz", folder=comics, state=COMPLETE, done=9, total=9, remote=dict(CAME)),
        Row(7, "https://x.org/6.cbz", filename="6.cbz", folder=str(tmp_path), state=ACTIVE, done=1, total=9,
            remote={"from": "beef01", "ref": "r3"}),
        Row(8, "https://x.org/b.pdf", filename="b.pdf", folder=str(tmp_path / "Manuals"), state=COMPLETE, done=3, total=3),
        Row(9, "https://x.org/c.pdf", filename="c.pdf", folder=str(tmp_path / "Manuals"), state=ERROR, error="404",
            done=1, total=3),
    ]


def model_of(tmp_path):
    model = qm.QueueModel(str(tmp_path))
    model.set_rows(rows(tmp_path), {})
    return model


def cell(model, row, column, role=Qt.ItemDataRole.DisplayRole):
    return model.data(model.index(row, column), role)


# ---- the colour itself -----------------------------------------------------------------------------------------

def test_the_tint_is_mixed_from_the_palette_and_stays_near_its_background():
    for pal in (DARK, LIGHT):
        base, tint = pal.color(QPalette.ColorRole.Base), style.link_tint(pal)
        assert tint != base
        assert max(abs(tint.red() - base.red()), abs(tint.green() - base.green()), abs(tint.blue() - base.blue())) < 48
        assert tint.blue() > tint.green()                         # violet, on both: more blue and red than green
        assert tint.red() > tint.green()
    assert style.link_tint(DARK) != style.link_tint(LIGHT)


def test_the_bar_colour_is_lighter_on_dark_and_deeper_on_light():
    link = QColor(style.LINK_COLOR)
    assert style.link_bar(DARK).lightness() > link.lightness() > style.link_bar(LIGHT).lightness()
    for pal in (DARK, LIGHT):
        bar = style.link_bar(pal)
        assert bar not in (qm.DONE_GREEN, qm.FAILED_RED, pal.color(QPalette.ColorRole.Highlight))


# ---- the main list ---------------------------------------------------------------------------------------------

def test_rows_of_another_accipio_are_tinted_in_every_column_and_the_others_are_not(app, tmp_path):
    model = model_of(tmp_path)
    wash = QBrush(style.link_tint(app.palette()))
    for row, tinted in enumerate([False, True, True, True, True, True, True, False, False]):
        for column in range(len(qm.COLUMNS)):
            got = cell(model, row, column, Qt.ItemDataRole.BackgroundRole)
            assert (got == wash) if tinted else got is None, (row, column)


def test_the_words_stay_so_the_colour_is_never_the_only_sign(app, tmp_path):
    model = model_of(tmp_path)
    assert [cell(model, r, qm.STATUS) for r in (1, 2, 3, 4)] == [
        "Waiting for Collector", "Sent to Collector", "Finished on Collector, in Comics", "Failed on Collector: 404"]
    assert [cell(model, r, qm.FOLDER) for r in (1, 2, 3, 4)] == ["Collector"] * 4


def test_the_collector_names_the_sender_after_the_folder(app, tmp_path):
    model = model_of(tmp_path)
    assert cell(model, 5, qm.FOLDER) == "Comics ← Laptop"
    assert cell(model, 6, qm.FOLDER) == "← another Accipio"        # the base folder itself, and a sender with no name
    assert cell(model, 7, qm.FOLDER) == "Manuals"
    assert cell(model, 5, qm.FOLDER, qm.SORT_ROLE) == "comics"     # sorted by the folder, not by who sent it


def test_a_row_downloaded_here_instead_is_an_ordinary_row(app, tmp_path):
    model = qm.QueueModel(str(tmp_path))
    model.set_rows([Row(1, "https://x.org/1.cbz", filename="1.cbz", folder=str(tmp_path / "Comics"), state=ACTIVE)], {})
    assert cell(model, 0, 0, Qt.ItemDataRole.BackgroundRole) is None
    assert not handover.sent_to(model.rows[0]) and not handover.came_from(model.rows[0])


def test_the_tint_is_really_painted_under_accipios_stylesheet(app, tmp_path):
    """The stylesheet styles ``::item``, and Qt then drops an item's own background: ``TintDelegate`` paints it."""
    model = model_of(tmp_path)
    table = QTableView()
    table.setStyleSheet(style.stylesheet(app.palette()))
    table.setModel(model)
    table.setItemDelegate(style.TintDelegate(table))
    table.setShowGrid(False)
    table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(style.ROW_HEIGHT)
    table.setItemDelegateForColumn(qm.PROGRESS, qm.ProgressDelegate(table))
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    table.horizontalHeader().resizeSection(qm.NAME, 300)
    table.resize(900, 320)
    image = table.viewport().grab().toImage()

    def pixel(row, column):
        rect = table.visualRect(model.index(row, column))
        return image.pixelColor(rect.right() - 4, rect.center().y())      # the cell's right edge: no text there
    tint, base = style.link_tint(app.palette()), app.palette().color(QPalette.ColorRole.Base)
    assert tint != base
    for column in (qm.NAME, qm.PROGRESS, qm.SPEED):
        assert pixel(1, column) == tint, column                          # a waiting row for the collector
        assert pixel(5, column) == tint, column                          # a row that came from the laptop
        assert pixel(0, column) == base and pixel(7, column) == base
    table.selectRow(1)                                                   # selected: the normal selection, not the wash
    image = table.viewport().grab().toImage()
    assert pixel(1, qm.NAME) not in (tint, base) and pixel(5, qm.NAME) == tint
    table.deleteLater()


def test_the_three_lists_have_the_delegate_that_paints_the_tint(win, tmp_path):
    assert isinstance(win.table.itemDelegate(), style.TintDelegate)
    assert isinstance(win.table.itemDelegateForColumn(qm.PROGRESS), qm.ProgressDelegate)
    box = add_box(tmp_path, "https://x.org/a.txt", {"https://x.org/a.txt": preview_for("https://x.org/a.txt")})
    assert isinstance(box.table.itemDelegate(), style.TintDelegate)
    box.done(0)
    assert isinstance(dialog_for(tmp_path).places_tab.list.itemDelegate(), style.TintDelegate)


def test_a_rounded_wash_is_painted_in_the_places_list_under_the_stylesheet(app, tmp_path):
    dialog = dialog_for(tmp_path, [Folder("Comics", "%default%\\Comics"), Folder("Collector", "", "accipio", "p1")])
    tab = dialog.places_tab
    tab.list.setStyleSheet(style.stylesheet(app.palette()))
    tab.list.resize(240, 200)
    image = tab.list.viewport().grab().toImage()

    def pixel(row):
        rect = tab.list.visualItemRect(tab.list.item(row))
        return image.pixelColor(rect.right() - 12, rect.center().y())
    assert pixel(2) == style.link_tint(tab.palette())                    # Collector
    assert pixel(1) == app.palette().color(QPalette.ColorRole.Base)      # Comics


def test_every_header_of_the_main_list_still_sorts(app, tmp_path):
    model = model_of(tmp_path)
    proxy = qm.QueueFilter()
    proxy.setSourceModel(model)
    for column in range(len(qm.COLUMNS)):
        proxy.sort(column)
        assert proxy.rowCount() == len(model.rows)


# ---- the progress bar ------------------------------------------------------------------------------------------

def test_the_bar_of_a_download_for_another_accipio(tmp_path):
    active, waiting, sent, there, failed_there, came, _, here, failed_here = rows(tmp_path)
    outline, fill = qm.ProgressDelegate.outline, qm.ProgressDelegate.fill
    for pal in (DARK, LIGHT):
        link = style.link_bar(pal)
        assert outline(waiting, pal) is None                              # still waiting: the empty bar of today
        assert outline(sent, pal) == link                                 # sent, not finished: hollow, in the link colour
        assert outline(there, pal) is None and fill(there, pal) == link   # finished there: filled, not green
        assert outline(failed_there, pal) == qm.FAILED_RED                # failed there: hollow, red
        assert fill(here, pal) == qm.DONE_GREEN and fill(came, pal) == qm.DONE_GREEN   # green: the file is on this PC
        assert fill(failed_here, pal) == qm.FAILED_RED and outline(failed_here, pal) is None
        assert fill(active, pal) == pal.color(QPalette.ColorRole.Highlight) and outline(active, pal) is None


# ---- the Add box and the Places list (110A) ----------------------------------------------------------------------

def test_the_add_box_tints_a_link_that_goes_to_another_accipio(app, tmp_path):
    place = Folder("Collector", kind=rules.PLACE_ACCIPIO, peer="c0ffee")
    there, here = "https://x.org/saga.cbz", "https://x.org/a.txt"
    previews = {there: preview_for(there, place), here: preview_for(here, None, str(tmp_path / "Downloads" / "Books"))}
    box = add_box(tmp_path, f"{there} {here}", previews)
    wash, plain = QBrush(style.link_tint(box.palette())), QBrush()
    for column in range(len(add_dialog.COLUMNS)):
        assert box.table.item(0, column).background() == wash, column
        assert box.table.item(1, column).background() == plain, column
    assert box.table.item(0, add_dialog.FOLDER).text() == "Collector"            # the words are still there
    box.set_folder(0, str(tmp_path / "Mine"))                                   # by hand: downloaded here, so no tint
    assert box.table.item(0, add_dialog.NAME).background() == plain
    box.done(0)


def test_a_series_row_is_tinted_when_all_its_links_go_there(app, tmp_path, monkeypatch):
    place = Folder("Collector", kind=rules.PLACE_ACCIPIO, peer="c0ffee")
    urls = ["https://x.org/saga-1.cbz", "https://x.org/saga-2.cbz"]
    box = add_box(tmp_path, "https://x.org/saga-[1-2].cbz", {u: preview_for(u, place) for u in urls})
    monkeypatch.setattr("accipio.core.ranges.expand", lambda link: list(urls))
    box.read_links()
    box.collect()
    assert box.table.item(0, add_dialog.NAME).background() == QBrush(style.link_tint(box.palette()))
    box.done(0)


def test_the_places_list_tints_a_place_that_is_another_accipio(app, tmp_path):
    dialog = dialog_for(tmp_path, [Folder("Comics", "%default%\\Comics"), Folder("Collector", "", "accipio", "p1")])
    tab = dialog.places_tab
    wash, plain = QBrush(style.link_tint(tab.palette())), QBrush()
    looks = {tab.list.item(i).text(): (tab.list.item(i).background(), tab.list.item(i).toolTip())
             for i in range(tab.list.count())}
    assert looks == {"Default": (plain, ""), "Comics": (plain, ""), "Collector": (wash, REMOTE_PLACE_TIP)}
    select(tab, "Comics")                                                      # the mark follows the kind at once
    tab.kind_combo.setCurrentIndex(tab.kind_combo.findData(rules.PLACE_ACCIPIO))
    assert tab.list.currentItem().background() == wash and tab.list.currentItem().toolTip() == REMOTE_PLACE_TIP
    tab.kind_combo.setCurrentIndex(tab.kind_combo.findData(rules.PLACE_FOLDER))
    assert tab.list.currentItem().background() == plain and tab.list.currentItem().toolTip() == ""
