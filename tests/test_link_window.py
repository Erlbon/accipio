"""The windows of the link to other Accipios: Settings > Other Accipios, the Add box, the right-click menu. Offscreen,
everything under tmp_path, a stand-in key store (never the real keyring), and no network but 127.0.0.1."""
import concurrent.futures
import os
import socket

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from accipio.core import link, rules, settings  # noqa: E402
from accipio.core.queue import Preview  # noqa: E402
from accipio.core.rules import Condition, Folder, Placement, Rule  # noqa: E402
from accipio.core.store import WAITING  # noqa: E402
from accipio.gui import add_dialog, link_dialog, main_window as mw  # noqa: E402
from accipio.gui.settings_dialog import SettingsDialog  # noqa: E402
from tests import samples  # noqa: E402
from tests.test_gui import Engine, RefusingToaster, app  # noqa: E402,F401
from tests.test_link import MemoryKeys  # noqa: E402
from tests.test_queue import FakeProber  # noqa: E402

COMIC = "https://x.org/get/Saga%2001.cbz"
HERE = "127.0.0.1"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind((HERE, 0))
        return probe.getsockname()[1]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make_window(tmp_path, name="me"):
    folder = str(tmp_path / name / "config")
    settings.save_settings(folder, settings.Settings(base_folder=str(tmp_path / name / "Downloads")))
    settings.save_rules(folder, samples.sorting_rules(), samples.sorting_library())
    prober = FakeProber()
    win = mw.MainWindow(mw.open_services(folder, engine=Engine(), prober=prober, video_engine=False, keys=MemoryKeys()),
                        tick_ms=60_000)
    win.toaster = RefusingToaster()
    win.read_browsers = lambda: []
    win.play_sound = lambda sound: None
    win.queue.in_background = lambda fn: None          # no pump thread: what a test needs from the sender it asks for
    win.prober = prober
    return win


def close(win):
    try:
        win.flush()
    except RuntimeError:
        pass
    QApplication.processEvents()
    win.close()
    QApplication.sendPostedEvents(None, 0)
    QApplication.processEvents()


@pytest.fixture
def win(app, tmp_path):
    window = make_window(tmp_path)
    yield window
    close(window)


@pytest.fixture
def two(app, tmp_path):
    collector, sender = make_window(tmp_path, "collector"), make_window(tmp_path, "sender")
    yield collector, sender
    close(collector)
    close(sender)


def dialog_of(window, **kw):
    kw.setdefault("addresses_of", lambda: [HERE, "192.0.2.7"])
    kw.setdefault("network_names", lambda: {HERE: "This PC only", "192.0.2.7": "Test net"})
    box = link_dialog.LinkDialog(window.link, lambda: window.services.settings, window.apply_link, window, **kw)
    return box


def listen_on(window, port=None):
    """What the user does in the window: accept, tick 127.0.0.1, pick a port."""
    box = dialog_of(window)
    box.port.setValue(port or free_port())
    box.accept_box.setChecked(True)
    box.tick_address(HERE)
    return box


needs_psk = pytest.mark.skipif(not link.available(), reason="this Python's ssl module has no pre-shared keys")


# ---- starting the link -----------------------------------------------------------------------------------------

def test_nothing_listens_until_the_user_switches_it_on(win):
    assert win.link.listening == [] and win.link.listener is None
    assert win.services.settings.link_accept is False and win.services.settings.link_addresses == []
    assert win.services.node.peers() == []
    assert "Not accepting" in win.link_summary() and "No Accipio is paired" in win.link_summary()


@needs_psk
def test_ticking_in_the_window_saves_the_settings_and_starts_the_listener(win):
    box = dialog_of(win)
    assert box.accept_box.isChecked() is False and box.ticked() == []          # nothing ticked until he does
    assert not box.show_code.isEnabled()                                        # nothing listens: no code to show
    port = free_port()
    box.port.setValue(port)
    box.accept_box.setChecked(True)
    assert win.link.listening == []                                             # accepting, but no address ticked
    assert "no address is ticked" in box.status.text()
    box.tick_address(HERE)
    assert win.link.listening == [(HERE, port)] and f"port {port}" in box.status.text()
    assert box.show_code.isEnabled()
    saved = settings.load_settings(win.services.folder)
    assert (saved.link_accept, saved.link_addresses, saved.link_port) == (True, [HERE], port)
    box.tick_address(HERE, False)
    assert win.link.listening == [] and not box.show_code.isEnabled()
    box.reject()


@needs_psk
def test_a_saved_address_this_pc_no_longer_has_is_listed_and_marked(win):
    win.apply_link(False, ["198.51.100.9"], 47999)
    box = dialog_of(win)
    rows = {box.addresses.item(r, link_dialog.ADDRESS).text(): box.addresses.item(r, link_dialog.NETWORK).text()
            for r in range(box.addresses.rowCount())}
    assert rows[HERE] == "This PC only" and rows["198.51.100.9"] == link_dialog.GONE
    assert box.ticked() == ["198.51.100.9"] and box.port.value() == 47999
    box.reject()


def test_the_tables_resize_and_sort_and_networks_are_named_by_range_without_qt():
    assert link_dialog.range_name("127.0.0.1") == "This PC only"
    assert link_dialog.range_name("100.101.102.103") == "Tailscale"
    assert link_dialog.range_name("fd7a:115c:a1e0::1") == "Tailscale"
    assert link_dialog.range_name("192.168.1.5") == "Local network"
    assert link_dialog.range_name("8.8.8.8") == "" and link_dialog.range_name("nonsense") == ""


def test_the_window_closing_stops_the_listener(app, tmp_path):
    if not link.available():
        pytest.skip("no pre-shared keys")
    window = make_window(tmp_path)
    window.apply_link(True, [HERE], free_port())
    assert window.link.listening
    close(window)
    assert window.link.listening == []


# ---- the pairing code and pairing ------------------------------------------------------------------------------

@needs_psk
def test_a_code_is_shown_and_counts_down_then_runs_out(win):
    clock = Clock()
    win.link.node.clock = clock
    box = listen_on(win)
    box.begin_pairing()
    shown = box.code.text()
    assert not box.code.isHidden() and link.tidy_code(shown) and len(link.tidy_code(shown)) == link.CODE_LENGTH
    assert box.code_left.text() == "valid for 5:00"
    clock.now += 61
    box.tick()
    assert box.code_left.text() == "valid for 3:59"
    clock.now += link.PAIRING_SECONDS
    box.tick()
    assert box.code.isHidden() and "run out" in box.result_label.text()
    box.reject()


@needs_psk
def test_unticking_the_address_takes_a_shown_code_away(win):
    box = listen_on(win)
    box.begin_pairing()
    box.tick_address(HERE, False)
    assert box.code.isHidden() and win.link.node.pairing_left() == 0
    box.reject()


@needs_psk
def test_a_real_pairing_through_the_pair_button_shows_up_on_both_sides(two):
    collector, sender = two
    port = free_port()
    c_box = listen_on(collector, port)
    c_box.begin_pairing()
    s_box = dialog_of(sender)
    s_box.host.setText(HERE)
    s_box.send_port.setValue(port)
    s_box.typed_code.setText(c_box.code.text().lower().replace("-", " "))      # typed any way
    s_box.pair_now()
    assert "Paired with" in s_box.result_label.text() and "Rules > Places" in s_box.result_label.text()
    assert s_box.paired.rowCount() == 1
    assert s_box.paired.item(0, link_dialog.NAME).text() == collector.link.node.name
    assert s_box.paired.item(0, link_dialog.WHERE).text() == f"{HERE}:{port}"
    assert s_box.paired.item(0, link_dialog.ROLE).text() == "collects for this PC"
    assert sender.link.node.peers()[0].id == collector.link.node.id
    c_box.tick()                                                              # the collector's window learns of it
    assert c_box.result_label.text() == f"Paired with {sender.link.node.name}." and c_box.code.isHidden()
    assert c_box.paired.rowCount() == 1 and c_box.paired.item(0, link_dialog.ROLE).text() == "sends to this PC"
    assert "1 Accipio paired" in collector.link_summary()
    # the paired one can be reached (the hello of the Places tab)
    assert sender.link.reach(collector.link.node.id) == ""
    c_box.reject()
    s_box.reject()


@needs_psk
def test_a_wrong_code_shows_its_own_sentence_and_pairs_nothing(two):
    collector, sender = two
    port = free_port()
    c_box = listen_on(collector, port)
    c_box.begin_pairing()
    s_box = dialog_of(sender)
    s_box.host.setText(HERE)
    s_box.send_port.setValue(port)
    s_box.typed_code.setText("AAAA-AAAA-AAAA")
    s_box.pair_now()
    assert "did not accept the code" in s_box.result_label.text()
    assert s_box.paired.rowCount() == 0 and sender.link.node.peers() == []
    s_box.host.setText("")
    s_box.pair_now()
    assert "Type the collector's address" in s_box.result_label.text()
    c_box.reject()
    s_box.reject()


@needs_psk
def test_nobody_listening_there_is_told_as_such(two):
    _, sender = two
    s_box = dialog_of(sender)
    s_box.host.setText(HERE)
    s_box.send_port.setValue(free_port())
    s_box.typed_code.setText("AAAA-AAAA-AAAA")
    s_box.pair_now()
    assert "could not be reached" in s_box.result_label.text()
    s_box.reject()


def test_forget_asks_first_then_ends_the_pairing(win, monkeypatch):
    win.services.node.remember(link.Peer("c0ffee", "Collector", "100.101.102.103", 47610), bytes(32))
    box = dialog_of(win)
    assert box.paired.rowCount() == 1 and not box.forget_button.isEnabled()
    box.paired.selectRow(0)
    assert box.forget_button.isEnabled()
    asked = []
    monkeypatch.setattr(box, "confirm_forget", lambda peer: asked.append(peer.name) or False)
    box.forget_selected()
    assert asked == ["Collector"] and win.services.node.peers()                   # a no: nothing happens
    monkeypatch.setattr(box, "confirm_forget", lambda peer: True)
    box.forget_selected()
    win.flush()
    assert win.services.node.peers() == [] and box.paired.rowCount() == 0 and "Forgot Collector" in box.result_label.text()
    box.reject()


def test_the_yes_no_question_says_what_forgetting_does(win, monkeypatch):
    seen = []
    monkeypatch.setattr(QMessageBox, "question", lambda parent, title, text, *a: seen.append(text) or QMessageBox.StandardButton.No)
    box = dialog_of(win)
    assert box.confirm_forget(link.Peer("c0ffee", "Collector")) is False
    assert "can no longer be followed" in seen[0] and "places that use it" in seen[0]
    box.reject()


def test_the_name_is_changed_when_editing_is_finished(win):
    box = dialog_of(win)
    box.name.setText("  Work   laptop ")
    box.name.editingFinished.emit()
    assert win.link.node.name == "Work laptop" and box.name.text() == "Work laptop"
    box.name.setText("")
    box.name.editingFinished.emit()
    assert win.link.node.name == "Work laptop" and box.name.text() == "Work laptop"
    box.reject()


def test_without_encryption_the_window_says_why_and_is_disabled(app, tmp_path, monkeypatch):
    monkeypatch.setattr(link, "available", lambda: False)
    window = make_window(tmp_path)
    try:
        box = dialog_of(window)
        assert not box.unavailable_label.isHidden() and "Python 3.13" in box.unavailable_label.text()
        for part in (box.name, box.collect_group, box.send_group, box.paired_group):
            assert not part.isEnabled()
        window.apply_link(True, [HERE], free_port())
        assert window.link.listening == [] and window.link.status_text() == window.link.unavailable
        assert window.link_summary() == window.link.unavailable
        box.reject()
    finally:
        close(window)


# ---- Settings --------------------------------------------------------------------------------------------------

def test_settings_has_the_other_accipios_row_and_it_is_off_without_a_window_to_open(app):
    box = SettingsDialog(settings.Settings())
    assert not box.link_button.isEnabled() and box.link_label.text()
    box = SettingsDialog(settings.Settings(), link_status=lambda: "Fine.", open_link=lambda parent: None)
    assert box.link_button.isEnabled() and box.link_label.text() == "Fine."
    assert "Rules > Places" in "".join(w.text() for w in box.findChildren(type(box.link_label)))


@needs_psk
def test_settings_ok_does_not_undo_what_the_link_window_saved(win, monkeypatch):
    port = free_port()
    seen = {}

    def exec_settings(self):
        seen["before"] = self.link_label.text()
        monkeypatch.setattr(win, "_show_link_dialog", lambda box: (box.port.setValue(port), box.accept_box.setChecked(True),
                                                                   box.tick_address(HERE), box.reject()))
        self._set_up_link()                                      # the link window opens from Settings, changes things
        seen["after"] = self.link_label.text()
        self.max_downloads.setValue(7)                           # and the rest of Settings is changed as usual
        return True

    monkeypatch.setattr(SettingsDialog, "exec", exec_settings)
    win.open_settings()
    assert "Not accepting" in seen["before"] and f"port {port}" in seen["after"]
    now = settings.load_settings(win.services.folder)
    assert (now.link_accept, now.link_addresses, now.link_port) == (True, [HERE], port) and now.max_downloads == 7
    assert win.link.listening == [(HERE, port)]


# ---- copying an address from one Accipio to the other -------------------------------------------------------------

def select_address(box, address):
    for r in range(box.addresses.rowCount()):
        if box.addresses.item(r, link_dialog.LISTEN).data(link_dialog.ADDRESS_ROLE) == address:
            box.addresses.selectRow(r)
            return
    raise KeyError(address)


def test_an_address_is_copied_from_the_right_click_menu_with_or_without_the_port(win):
    box = dialog_of(win)
    box.port.setValue(47611)
    menu = [a.text() for a in box.addresses.actions()]
    assert menu == ["Copy Address and Port", "Copy Address"]
    assert box.addresses.contextMenuPolicy() == Qt.ContextMenuPolicy.ActionsContextMenu
    QApplication.clipboard().setText("before")
    box.copy_address.trigger()                                   # nothing selected: nothing is copied
    assert QApplication.clipboard().text() == "before"
    select_address(box, "192.0.2.7")
    box.copy_address.trigger()
    assert QApplication.clipboard().text() == "192.0.2.7" and "Copied 192.0.2.7." in box.result_label.text()
    box.copy_endpoint.trigger()                                  # also what Ctrl+C does in the table
    assert QApplication.clipboard().text() == "192.0.2.7:47611"
    assert box.copy_endpoint.shortcut().toString() == "Ctrl+C"
    box.reject()


def test_an_address_pasted_with_its_port_fills_both_fields(win):
    box = dialog_of(win)
    box.host.setText("  100.101.102.103:47611 ")
    box.host.editingFinished.emit()
    assert box.host.text() == "100.101.102.103" and box.send_port.value() == 47611
    box.host.setText("[fd7a:115c:a1e0::5]:5000")
    box.host.editingFinished.emit()
    assert box.host.text() == "fd7a:115c:a1e0::5" and box.send_port.value() == 5000
    for plain in ("collector.tail1234.ts.net", "fd7a:115c:a1e0::5", "host:notaport", "host:70000"):
        box.host.setText(plain)                                  # no port in it: the field and the port stay
        box.host.editingFinished.emit()
        assert box.host.text() == plain and box.send_port.value() == 5000
    box.reject()


def test_what_one_window_copies_the_other_takes_apart():
    for address, port in (("100.101.102.103", 47610), ("fd7a:115c:a1e0::5", 1), ("pc.example", 65535)):
        assert link_dialog.split_address(link_dialog.with_port(address, port)) == (address, port)


def test_the_paired_table_copies_a_collectors_address_and_the_code_has_a_copy_button(win):
    win.services.node.remember(link.Peer("c0ffee", "Collector", "100.101.102.103", 47610), bytes(32))
    win.services.node.remember(link.Peer("beef01", "Laptop"), bytes(32))          # sends only: no address
    box = listen_on(win)
    rows = {box.paired.item(r, link_dialog.NAME).text(): r for r in range(box.paired.rowCount())}
    box.paired.selectRow(rows["Laptop"])
    assert not box.copy_peer.isEnabled()
    box.paired.selectRow(rows["Collector"])
    assert box.copy_peer.isEnabled()
    box.copy_peer.trigger()
    assert QApplication.clipboard().text() == "100.101.102.103:47610"
    assert box.copy_code.isHidden()
    if win.link.listening:
        box.begin_pairing()
        assert not box.copy_code.isHidden()
        box.copy_code.click()
        assert QApplication.clipboard().text() == box.code.text() != ""
        box.accept_box.setChecked(False)                         # nothing listens: the code goes, and its button
        assert box.copy_code.isHidden()
    box.reject()


# ---- the Add box and the right-click menu -----------------------------------------------------------------------

def preview_for(url, place=None, folder=""):
    placement = Placement(folder, "Saga 01.cbz", [], [], remote=place)
    return Preview(url, "Saga 01.cbz", 5000, "file", placement)


def done(value):
    future = concurrent.futures.Future()
    future.set_result(value)
    return future


def add_box(tmp_path, text, previews):
    box = add_dialog.AddDialog(text, str(tmp_path / "Downloads"), lambda fn, url: done(fn(url)),
                               lambda url: previews[url], lambda t: t.split())
    box.collect()
    return box


def test_the_add_box_names_the_other_accipio_for_a_link_that_goes_there(app, tmp_path):
    place = Folder("Collector", kind=rules.PLACE_ACCIPIO, peer="c0ffee")
    series = ["https://x.org/saga-1.cbz", "https://x.org/saga-2.cbz"]
    previews = {COMIC: preview_for(COMIC, place), "https://x.org/a.txt": preview_for("https://x.org/a.txt", None,
                                                                                    str(tmp_path / "Downloads" / "Books")),
                series[0]: preview_for(series[0], place), series[1]: preview_for(series[1], place)}
    box = add_box(tmp_path, f"{COMIC} https://x.org/a.txt", previews)
    assert box.table.item(0, add_dialog.FOLDER).text() == "Collector"
    assert box.table.item(0, add_dialog.FOLDER).toolTip() == add_dialog.REMOTE_TIP
    assert box.table.item(1, add_dialog.FOLDER).text() == "Books" and "Another Accipio" not in box.table.item(1, add_dialog.FOLDER).toolTip()
    assert box.chosen() == [(COMIC, ""), ("https://x.org/a.txt", "")]            # no folder by hand: the rules decide
    box.set_folder(0, str(tmp_path / "Mine"))                                   # a folder by hand wins: it is downloaded here
    assert box.table.item(0, add_dialog.FOLDER).text() == str(tmp_path / "Mine")
    assert "chosen by hand" in box.table.item(0, add_dialog.FOLDER).toolTip()
    assert box.chosen()[0] == (COMIC, os.path.normpath(str(tmp_path / "Mine")))
    box.done(0)


def test_a_series_row_names_the_place_too(app, tmp_path, monkeypatch):
    place = Folder("Collector", kind=rules.PLACE_ACCIPIO, peer="c0ffee")
    urls = ["https://x.org/saga-1.cbz", "https://x.org/saga-2.cbz"]
    previews = {u: preview_for(u, place) for u in urls}
    box = add_box(tmp_path, "https://x.org/saga-[1-2].cbz", previews)
    monkeypatch.setattr("accipio.core.ranges.expand", lambda link: list(urls))
    box.read_links()
    box.collect()
    assert box.table.item(0, add_dialog.FOLDER).text() == "Collector"
    assert box.table.item(0, add_dialog.FOLDER).toolTip() == add_dialog.REMOTE_TIP
    box.done(0)


def waiting_row(win, tmp_path):
    """A link whose rule sends it to a paired Accipio that is not there: a row that waits, with no folder."""
    peer = link.Peer("c0ffee", "Collector", HERE, free_port())
    win.services.node.remember(peer, bytes(32))
    library = samples.sorting_library()
    library.folders.append(Folder("Collector", kind=rules.PLACE_ACCIPIO, peer=peer.id))
    win.apply_rules([Rule("Comics to the collector", [Condition("type", ["Comics"])], target="Collector")]
                    + samples.sorting_rules(), library)
    win.prober.serve(COMIC, "Saga 01.cbz", "application/vnd.comicbook+zip", 5000)
    win.paste.setText(COMIC)
    win.add_from_bar()
    win.flush()
    [row] = win.queue.rows()
    assert row.state == WAITING and row.folder == ""
    win.table.selectRow(0)
    return row


def test_a_row_that_belongs_to_another_accipio_cannot_show_a_folder_and_why_here_names_the_place(win, tmp_path, monkeypatch):
    waiting_row(win, tmp_path)
    shown = []
    monkeypatch.setattr(mw.QMenu, "exec", lambda menu, *a: shown.append(
        [a.isEnabled() for a in menu.actions() if a is win.actions_by_key["open_folder"]]))
    win.show_context_menu(win.table.visualRect(win.proxy.index(0, 0)).center())
    assert shown == [[False]]
    assert win.actions_by_key["open_folder"].isEnabled()                         # the menu bar's own action is not left off
    told = []
    monkeypatch.setattr(mw.QMessageBox, "information", lambda parent, title, text: told.append(text))
    win.why_selected()
    assert told[0].startswith("Collector (another Accipio)\n\n") and "Collector" in told[0]


def test_the_archive_boxs_folder_list_leaves_out_places_that_are_another_accipio(win):
    library = samples.sorting_library()
    library.folders.append(Folder("Collector", kind=rules.PLACE_ACCIPIO, peer="c0ffee"))
    win.apply_rules(win.services._holder["rules"], library)
    win.queue.unpack_on = True
    from tests.test_gui import archive_arrives
    archive_arrives(win, win.services.engine, win.prober, "https://a.org/tool.zip", {"tool/setup.exe": b"MZ"})
    box = win.archive_box
    assert not box.isHidden()
    offered = [box.folder_choice.itemData(i) for i in range(box.folder_choice.count())]
    assert "Collector" not in offered and "Programs" in offered


def test_the_rules_window_gets_the_link(win, monkeypatch):
    from accipio.gui.rules_dialog import RulesDialog
    win.services.node.remember(link.Peer("c0ffee", "Collector", HERE, 47610), bytes(32))
    seen = []
    monkeypatch.setattr(RulesDialog, "exec", lambda self: (seen.append(self), False)[1])
    win.open_rules()
    places = seen[0].places_tab
    assert [p.name for p in places.peers_of()] == ["Collector"] and places.reach == win.link.reach
    assert places.link_problem == win.link.unavailable
