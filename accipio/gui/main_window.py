"""The main window (mockup B, "compact"): a paste bar, filter chips, and one table.

Every queue operation (adding a link means a network request; refresh talks to aria2 and may move a file)
runs on ONE worker thread, in order, so the window never freezes and two operations never race. The window
only reads the store, which is safe from any thread, on a half-second timer.
"""
from __future__ import annotations

import concurrent.futures
import os
import re
import traceback
from dataclasses import dataclass
from typing import Callable, List, Optional

from PyQt6.QtCore import QModelIndex, QSettings, Qt, QTimer, QUrl
from PyQt6.QtGui import QAction, QDesktopServices, QGuiApplication, QKeySequence
from PyQt6.QtWidgets import (QAbstractItemView, QButtonGroup, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
                             QMenu, QMessageBox, QPushButton, QTableView, QVBoxLayout, QWidget)

from redactor_common.core import labels
from redactor_common.gui.menu_builder import MenuAction, Separator
from redactor_common.gui.standard_menus import AppMenu, StandardMenuSpec, build_standard_menu_bar, standard_help_items

from accipio.core import aria2, rules, settings as settings_mod
from accipio.core.queue import Queue
from accipio.core.store import ACTIVE, COMPLETE, ERROR, PAUSED, Row, Store
from accipio.core.version import APP_NAME, APP_REPO_URL, APP_VERSION
from accipio.gui.queue_model import COLUMNS, FILTERS, FOLDER, NAME, PROGRESS, ProgressDelegate, QueueFilter, QueueModel, human_size

LINK = re.compile(r"(?:https?|ftp)://\S+", re.IGNORECASE)


def links_in(text: str) -> List[str]:
    """Every http(s)/ftp link in pasted or dropped text, in order, without repeats."""
    found = [m.group(0).rstrip(".,;)>\"'") for m in LINK.finditer(text or "")]
    return list(dict.fromkeys(found))


@dataclass
class Services:
    """What the window works with; tests pass a fake engine and prober."""
    folder: str
    settings: settings_mod.Settings
    rules: List[rules.Rule]
    store: Store
    engine: object
    queue: Queue


def open_services(folder: str, engine=None, prober=None, video_engine=None, video_prober=None) -> Services:
    """Load settings and rules, open the queue database, and start aria2 and the yt-dlp engine (unless
    engines are given; tests pass fakes, and ``video_engine=False`` leaves video sites out)."""
    current = settings_mod.load_settings(folder)
    rule_list = settings_mod.load_rules(folder)
    store = Store(os.path.join(folder, "queue.db"))
    if engine is None:
        exe = aria2.find_aria2c()
        if exe is None:
            raise aria2.Aria2Error("aria2c was not found. Put aria2c.exe in an 'aria2' folder next to Accipio, "
                                   "or run python tools/fetch_engines.py in a checkout.")
        engine = aria2.Aria2(exe, os.path.join(folder, "aria2.session"), max_downloads=current.max_downloads,
                             connections=current.connections).start()
        engine.set_limits(speed_limit=current.speed_limit_kib * 1024)
    if video_engine is None:
        from accipio.core import video
        video_engine = video.VideoEngine(video.find_ffmpeg(), max_downloads=current.max_downloads,
                                         quality=current.video_quality)
        video_engine.set_limits(speed_limit=current.speed_limit_kib * 1024)
        video_prober = video_prober or video.make_prober(video_engine)
    holder = {"rules": rule_list}
    kwargs = {"prober": prober} if prober else {}
    queue = Queue(store, engine, lambda: holder["rules"], current.base_folder, keep_history=current.keep_history,
                  video_engine=video_engine or None, video_prober=video_prober, **kwargs)
    services = Services(folder, current, rule_list, store, engine, queue)
    services.video_engine = video_engine or None
    services._holder = holder
    return services


class MainWindow(QMainWindow):
    def __init__(self, services: Services, tick_ms: int = 500):
        super().__init__()
        self.services = services
        self.queue = services.queue
        self.worker = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="accipio-queue")
        self.failures: List[str] = []
        self._refreshing: Optional[concurrent.futures.Future] = None
        self.setWindowTitle(APP_NAME)
        self.resize(1000, 560)
        self.setAcceptDrops(True)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 4)

        bar = QHBoxLayout()
        self.paste = QLineEdit()
        self.paste.setPlaceholderText("Paste links or drop files here")
        self.paste.returnPressed.connect(self.add_from_bar)
        self.add_button = QPushButton("Add")
        self.add_button.clicked.connect(self.add_from_bar)
        self.rules_button = QPushButton("Rules…")
        self.rules_button.clicked.connect(self.open_rules)
        self.settings_button = QPushButton("Settings…")
        self.settings_button.clicked.connect(self.open_settings)
        bar.addWidget(self.paste, 1)
        for button in (self.add_button, self.rules_button, self.settings_button):
            bar.addWidget(button)
        layout.addLayout(bar)

        chips = QHBoxLayout()
        self.chip_group = QButtonGroup(self)
        self.chips = {}
        for name in FILTERS:
            chip = QPushButton(name)
            chip.setCheckable(True)
            chip.setFlat(True)
            chip.clicked.connect(lambda _=False, n=name: self.set_filter(n))
            self.chip_group.addButton(chip)
            self.chips[name] = chip
            chips.addWidget(chip)
        self.chips["All"].setChecked(True)
        chips.addStretch(1)
        self.speed_label = QLabel("")
        chips.addWidget(self.speed_label)
        layout.addLayout(chips)

        self.model = QueueModel(services.settings.base_folder, self)
        self.proxy = QueueFilter(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(-1, Qt.SortOrder.AscendingOrder)          # queue order until a header is clicked
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setItemDelegateForColumn(PROGRESS, ProgressDelegate(self.table))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)   # every column can be resized...
        header.setSectionsClickable(True)                                 # ...and sorted
        header.setStretchLastSection(True)
        for column, width in ((NAME, 300), (PROGRESS, 120), (FOLDER, 180)):
            header.resizeSection(column, width)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        self.table.doubleClicked.connect(self.open_item)
        layout.addWidget(self.table, 1)
        self.setCentralWidget(central)

        self.build_label = QLabel(f"Build {APP_VERSION}")
        self.statusBar().addPermanentWidget(self.build_label)
        self._build_menus()
        self._restore_layout()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(tick_ms)
        self.reload()

    # ---- menus -----------------------------------------------------------------------------------------------

    def _build_menus(self) -> None:
        spec = StandardMenuSpec(
            file=[MenuAction("add_links", "&Add Links", self.focus_paste_bar, shortcut="Ctrl+N"),
                  Separator(),
                  MenuAction("clear_finished", "&Clear Finished", self.clear_finished),
                  Separator(),
                  MenuAction("exit", labels.EXIT, self.close, shortcut=QKeySequence.StandardKey.Quit)],
            edit=[MenuAction("paste_links", "&Paste Links", self.paste_links, shortcut="Ctrl+Shift+V"),
                  Separator(),
                  MenuAction("select_all", "Select &All", self.table.selectAll, shortcut="Ctrl+A")],
            view=[MenuAction(f"show_{name.lower()}", label, lambda n=name: self.set_filter(n), shortcut=f"Ctrl+{i}")
                  for i, (name, label) in enumerate(zip(FILTERS, ("Show A&ll", "Show A&ctive", "Show &Done", "Show &Failed")), 1)],
            app_menus=[AppMenu("&Downloads", [
                MenuAction("pause", "&Pause", self.pause_selected, shortcut="Space"),
                MenuAction("resume", "&Resume", self.resume_selected),
                MenuAction("retry", "Re&try", self.retry_selected, shortcut="Ctrl+R"),
                Separator(),
                MenuAction("open_folder", "Show in &Folder", self.open_folder_of_selected, shortcut="Ctrl+Shift+F"),
                MenuAction("copy_link", "&Copy Link", self.copy_link_of_selected, shortcut="Ctrl+C"),
                MenuAction("why_here", "&Why Here?", self.why_selected, shortcut="Ctrl+I"),
                Separator(),
                MenuAction("remove", labels.REMOVE_FROM_LIST, self.remove_selected, shortcut="Delete"),
            ])],
            tools=[MenuAction("rules", "&Rules…", self.open_rules, shortcut="Ctrl+Shift+R"),
                   Separator(),
                   MenuAction("preferences", labels.PREFERENCES, self.open_settings, shortcut="Ctrl+,")],
            help=standard_help_items(APP_NAME, self.open_changelog, None, self.about),
        )
        self.menus = build_standard_menu_bar(self, spec)
        self.actions_by_key = self.action_registry          # key -> QAction, attached by build_standard_menu_bar

    # ---- the worker ------------------------------------------------------------------------------------------

    def run(self, fn: Callable, *args, **kwargs) -> concurrent.futures.Future:
        """Run a queue operation on the worker thread; a failure shows in the status bar, never kills the app."""
        future = self.worker.submit(fn, *args, **kwargs)

        def done(f):
            error = f.exception()
            if error is not None:
                self.failures.append("".join(traceback.format_exception_only(type(error), error)).strip())
        future.add_done_callback(done)
        return future

    def flush(self) -> None:
        """Wait until the worker has done everything asked of it, then show the result (tests, and closing)."""
        self.worker.submit(lambda: None).result()
        self.reload()

    def tick(self) -> None:
        if self._refreshing is None or self._refreshing.done():
            self._refreshing = self.run(self.queue.refresh)
        self.reload()

    def reload(self) -> None:
        selected = {r.id for r in self.selected_rows()}
        self.model.set_rows(self.queue.rows(), dict(self.queue.speeds))
        if selected and not {r.id for r in self.selected_rows()}:
            self.select_ids(selected)
        counts = self.model.counts()
        for name, chip in self.chips.items():
            chip.setText(f"{name} {counts[name]}")
        speed = self.model.total_speed()
        self.speed_label.setText(f"↓ {human_size(speed)}/s" if speed else "")
        while self.failures:
            self.statusBar().showMessage(self.failures.pop(0), 10000)

    # ---- adding ----------------------------------------------------------------------------------------------

    def focus_paste_bar(self) -> None:
        self.paste.setFocus()
        self.paste.selectAll()

    def add_links(self, text: str, page_url: str = "") -> int:
        links = links_in(text)
        for link in links:
            self.run(self.queue.add, link, page_url=page_url)
        if links:
            self.statusBar().showMessage(f"Adding {len(links)} link{'s' if len(links) > 1 else ''}…", 4000)
        return len(links)

    def add_from_bar(self) -> None:
        text = self.paste.text()
        if not text.strip():
            return
        if self.add_links(text):
            self.paste.clear()
        else:
            self.statusBar().showMessage("No link there: paste an address that starts with http://, https:// or ftp://", 6000)

    def paste_links(self) -> None:
        if not self.add_links(QGuiApplication.clipboard().text()):
            self.statusBar().showMessage("The clipboard holds no link", 4000)

    def dragEnterEvent(self, event):
        mime = event.mimeData()
        if mime.hasUrls() or mime.hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        mime = event.mimeData()
        text = "\n".join(u.toString() for u in mime.urls()) if mime.hasUrls() else mime.text()
        if self.add_links(text):
            event.acceptProposedAction()

    # ---- the selection ---------------------------------------------------------------------------------------

    def selected_rows(self) -> List[Row]:
        rows = []
        for index in self.table.selectionModel().selectedRows() if self.table.selectionModel() else []:
            row = self.model.row_at(self.proxy.mapToSource(index))
            if row:
                rows.append(row)
        return rows

    def select_ids(self, ids) -> None:
        selection = self.table.selectionModel()
        for position, row in enumerate(self.model.rows):
            if row.id in ids:
                index = self.proxy.mapFromSource(self.model.index(position, 0))
                if index.isValid():
                    selection.select(index, selection.SelectionFlag.Select | selection.SelectionFlag.Rows)

    def _each(self, fn: Callable[[int], None], states=None) -> None:
        for row in self.selected_rows():
            if states is None or row.state in states:
                self.run(fn, row.id)

    def pause_selected(self) -> None:
        rows = self.selected_rows()
        if rows and all(r.state == PAUSED for r in rows):          # Space toggles
            self._each(self.queue.resume, (PAUSED,))
        else:
            self._each(self.queue.pause, (ACTIVE,))

    def resume_selected(self) -> None:
        self._each(self.queue.resume, (PAUSED,))

    def retry_selected(self) -> None:
        self._each(self.queue.retry, (ERROR,))

    def remove_selected(self) -> None:
        rows = self.selected_rows()
        unfinished = [r for r in rows if r.state != COMPLETE]
        if unfinished:
            answer = QMessageBox.question(
                self, "Remove from list",
                f"Stop {len(unfinished)} unfinished download{'s' if len(unfinished) > 1 else ''} and delete "
                "what was downloaded so far? Finished files are never deleted.")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._each(self.queue.remove)

    def clear_finished(self) -> None:
        self.run(self.queue.clear_finished)

    def open_folder_of_selected(self) -> None:
        rows = self.selected_rows()
        if rows and rows[0].folder and os.path.isdir(rows[0].folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(rows[0].folder))

    def open_item(self, index: QModelIndex) -> None:
        row = self.model.row_at(self.proxy.mapToSource(index))
        if row and row.state == COMPLETE and os.path.isfile(row.path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(row.path))

    def copy_link_of_selected(self) -> None:
        rows = self.selected_rows()
        if rows:
            QGuiApplication.clipboard().setText("\n".join(r.url for r in rows))

    def why_selected(self) -> None:
        rows = self.selected_rows()
        if rows:
            row = rows[0]
            text = f"{row.path or row.url}\n\n{row.why or 'Not placed yet.'}"
            if row.state == ERROR and row.error:
                text += f"\n\nError: {row.error}"
            QMessageBox.information(self, "Why here?", text)

    def show_context_menu(self, point) -> None:
        rows = self.selected_rows()
        if not rows:
            return
        states = {r.state for r in rows}
        menu = QMenu(self)
        for key, enabled in (("pause", ACTIVE in states), ("resume", PAUSED in states), ("retry", ERROR in states),
                             (None, True), ("open_folder", True), ("copy_link", True), ("why_here", True),
                             (None, True), ("remove", True)):
            if key is None:
                menu.addSeparator()
                continue
            action = self.actions_by_key[key]
            action.setEnabled(enabled)
            menu.addAction(action)
        menu.exec(self.table.viewport().mapToGlobal(point))
        for key in ("pause", "resume", "retry"):
            self.actions_by_key[key].setEnabled(True)

    def set_filter(self, name: str) -> None:
        self.chips[name].setChecked(True)
        self.proxy.set_filter(name)

    # ---- dialogs ---------------------------------------------------------------------------------------------

    def open_rules(self) -> None:
        from accipio.gui.rules_dialog import RulesDialog
        dialog = RulesDialog(self.services._holder["rules"], self.services.settings.base_folder, self)
        if dialog.exec():
            self.apply_rules(dialog.result_rules())

    def apply_rules(self, new_rules: List[rules.Rule]) -> None:
        self.services._holder["rules"] = new_rules
        self.services.rules = new_rules
        settings_mod.save_rules(self.services.folder, new_rules)
        self.statusBar().showMessage("Rules saved. They apply to downloads added from now on.", 5000)

    def open_settings(self) -> None:
        from accipio.gui.settings_dialog import SettingsDialog
        dialog = SettingsDialog(self.services.settings, self)
        if dialog.exec():
            self.apply_settings(dialog.result_settings())

    def apply_settings(self, new: settings_mod.Settings) -> None:
        self.services.settings = new
        settings_mod.save_settings(self.services.folder, new)
        self.queue.base_folder = new.base_folder
        self.model.base_folder = new.base_folder
        self.queue.keep_history = new.keep_history
        for engine in (self.services.engine, self.services.video_engine):
            if engine is not None:
                self.run(engine.set_limits, max_downloads=new.max_downloads, speed_limit=new.speed_limit_kib * 1024)
        if self.services.video_engine is not None:
            self.services.video_engine.quality = new.video_quality
        self.statusBar().showMessage("Settings saved.", 4000)

    def open_changelog(self) -> None:
        QDesktopServices.openUrl(QUrl(APP_REPO_URL + "/blob/main/CHANGELOG.md"))

    def about(self) -> None:
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<h3>{APP_NAME}</h3><p>Build {APP_VERSION}</p><p>A download manager that puts each file "
                          f"where your rules say.</p><p><a href='{APP_REPO_URL}'>{APP_REPO_URL}</a></p>"
                          "<p>MIT licence. Downloads by aria2 (GPL-2.0), run as a separate program.</p>")

    # ---- layout memory and closing ---------------------------------------------------------------------------

    def _layout_store(self) -> QSettings:
        """Window size and column widths, in an ini file in Accipio's own folder (never the registry)."""
        return QSettings(os.path.join(self.services.folder, "window.ini"), QSettings.Format.IniFormat)

    def _restore_layout(self) -> None:
        stored = self._layout_store()
        geometry = stored.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        header = stored.value("window/header")
        if header is not None:
            self.table.horizontalHeader().restoreState(header)

    def closeEvent(self, event):
        stored = self._layout_store()
        stored.setValue("window/geometry", self.saveGeometry())
        stored.setValue("window/header", self.table.horizontalHeader().saveState())
        self.timer.stop()
        self.worker.shutdown(wait=True, cancel_futures=True)
        for engine in (self.services.engine, getattr(self.services, "video_engine", None)):
            stop = getattr(engine, "stop", None)
            if stop:
                stop()
        self.services.store.close()
        super().closeEvent(event)
