"""The Places tab with places that are another Accipio (link stage 3, work package B), offscreen."""
import dataclasses
import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6 import sip  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from accipio.core import link, rules  # noqa: E402
from accipio.core.rules import Condition, Folder, Rule  # noqa: E402
from accipio.gui import style  # noqa: E402
from accipio.gui.library_tabs import NO_PEERS  # noqa: E402
from accipio.gui.rules_dialog import RulesDialog  # noqa: E402

PEERS = [link.Peer("p1", "Collector PC", "192.168.1.20", 47610),
         link.Peer("p2", "Laptop")]                       # a sender only: no address, so not a place to send to


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


def wait_for(condition, seconds=5.0):
    """Let the window's events run until ``condition()`` holds (never a bare sleep)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    QApplication.processEvents()
    return condition()


def library(*places):
    return rules.Library(list(places), rules.default_groups(), [])


def dialog_for(tmp_path, places=(), rule_list=(), **kw):
    kw.setdefault("peers_of", lambda: PEERS)
    return RulesDialog(list(rule_list), str(tmp_path / "Downloads"), library=library(*places), **kw)


def select(tab, name):
    tab.list.setCurrentRow([tab.list.item(i).text() for i in range(tab.list.count())].index(name))


def test_a_library_with_places_of_both_kinds_survives_open_and_ok(app, tmp_path):
    places = [Folder("Comics", "%default%\\Comics"),
              Folder("Collector", "", "accipio", "p1", False, "here"),
              Folder("Old one", "", "accipio", "gone-id", True, "wait")]      # its peer is not paired any more
    dialog = dialog_for(tmp_path, places)
    tab = dialog.places_tab
    for name in ("Collector", "Old one", "Comics", "Default"):               # visiting each must change nothing
        select(tab, name)
    got = dialog.result_library()
    assert got == library(*places)
    for mine, theirs in zip(got.folders, places):
        assert dataclasses.asdict(mine) == dataclasses.asdict(theirs)


def test_an_unpaired_peer_is_kept_and_shown_as_such(app, tmp_path):
    dialog = dialog_for(tmp_path, [Folder("Old one", "", "accipio", "gone-id", True, "wait")])
    tab = dialog.places_tab
    select(tab, "Old one")
    assert tab.machine.currentText() == "(not paired any more)" and tab.machine.currentData() == "gone-id"
    assert "not paired with this PC any more" in tab.problems.text()
    assert dialog.result_library().folders[0].peer == "gone-id"


def test_making_a_place_for_another_accipio_in_the_form(app, tmp_path):
    dialog = dialog_for(tmp_path)
    tab = dialog.places_tab
    tab.new_item()
    assert tab.name.text() == "New place" and tab.kind_combo.currentData() == rules.PLACE_FOLDER
    tab.kind_combo.setCurrentIndex(tab.kind_combo.findData(rules.PLACE_ACCIPIO))
    assert [tab.machine.itemText(i) for i in range(tab.machine.count())] == ["(pick one)", "Collector PC · 192.168.1.20"]
    assert "Choose the Accipio" in tab.problems.text()                       # nothing picked yet
    tab.machine.setCurrentIndex(tab.machine.findData("p1"))
    tab.cookies.setChecked(False)
    tab.away.setCurrentIndex(tab.away.findData(rules.AWAY_HERE))
    assert dialog.result_library().folders == [Folder("New place", "", "accipio", "p1", False, "here")]
    assert tab.problems.text() == ""


def test_the_defaults_of_a_new_place_of_that_kind_are_wait_and_cookies_on(app, tmp_path):
    dialog = dialog_for(tmp_path)
    tab = dialog.places_tab
    tab.new_item()
    tab.kind_combo.setCurrentIndex(tab.kind_combo.findData(rules.PLACE_ACCIPIO))
    tab.machine.setCurrentIndex(tab.machine.findData("p1"))
    assert dialog.result_library().folders == [Folder("New place", "", "accipio", "p1", True, "wait")]


def test_switching_the_kind_hides_and_shows_the_rows(app, tmp_path):
    dialog = dialog_for(tmp_path)
    tab = dialog.places_tab
    tab.new_item()
    folder_rows = (tab.path, tab.today)
    accipio_rows = (tab.machine, tab.state, tab.away, tab.cookies)

    def shown(widgets):
        return [w.isVisibleTo(tab) for w in widgets]          # the field may sit inside a wrapper that is hidden
    assert all(shown(folder_rows)) and not any(shown(accipio_rows))
    tab.kind_combo.setCurrentIndex(tab.kind_combo.findData(rules.PLACE_ACCIPIO))
    assert not any(shown(folder_rows)) and all(shown(accipio_rows))
    tab.kind_combo.setCurrentIndex(tab.kind_combo.findData(rules.PLACE_FOLDER))
    assert all(shown(folder_rows)) and not any(shown(accipio_rows))
    assert dialog.result_library().folders[0].kind == rules.PLACE_FOLDER


def test_default_has_no_kind_and_is_always_a_folder(app, tmp_path):
    dialog = dialog_for(tmp_path, [Folder("Collector", "", "accipio", "p1")])
    tab = dialog.places_tab
    select(tab, "Collector")
    assert tab.kind_combo.isVisibleTo(tab)
    select(tab, "Default")
    assert not tab.kind_combo.isVisibleTo(tab) and not tab.machine.isVisibleTo(tab) and tab.path.isVisibleTo(tab)
    assert tab.read_item("Default").kind == rules.PLACE_FOLDER


def test_no_paired_accipio_says_so(app, tmp_path):
    dialog = dialog_for(tmp_path, [Folder("Collector", "", "accipio", "")], peers_of=lambda: [PEERS[1]])
    tab = dialog.places_tab
    select(tab, "Collector")
    assert tab.machine.currentText() == "(none paired yet)"
    assert tab.problems.text() == NO_PEERS == "Pair with the other Accipio first: Tools > Other Accipios."


def test_a_link_problem_keeps_a_new_place_a_folder_but_an_old_one_shows(app, tmp_path):
    dialog = dialog_for(tmp_path, [Folder("Collector", "", "accipio", "p1")], link_problem="No keys here.")
    tab = dialog.places_tab
    select(tab, "Collector")
    assert tab.kind_combo.currentData() == rules.PLACE_ACCIPIO                  # still shows
    assert tab.link_note.text() == "No keys here." and tab.link_note.isVisibleTo(tab)
    tab.new_item()
    assert tab.kind_combo.currentData() == rules.PLACE_FOLDER
    assert not tab.kind_combo.model().item(tab.kind_combo.findData(rules.PLACE_ACCIPIO)).isEnabled()
    select(tab, "Default")
    assert not tab.link_note.isVisibleTo(tab)


def test_the_state_label_says_connected_or_why_not(app, tmp_path):
    answers = {"p1": ""}
    asked = []

    def reach(peer_id):
        asked.append(peer_id)
        return answers[peer_id]
    dialog = dialog_for(tmp_path, [Folder("Collector", "", "accipio", "p1")], reach=reach)
    tab = dialog.places_tab
    select(tab, "Collector")
    assert wait_for(lambda: tab.state.text() == "connected") and asked == ["p1"]
    answers["p1"] = "Connection refused."
    select(tab, "Default")
    assert tab.state.text() == ""
    select(tab, "Collector")
    assert wait_for(lambda: tab.state.text() == "not reachable right now")
    assert tab.state.toolTip() == "Connection refused." and style.WARNING_COLOR in tab.state.styleSheet()


def test_a_failing_check_is_an_answer_too(app, tmp_path):
    def reach(peer_id):
        raise OSError("boom")
    dialog = dialog_for(tmp_path, [Folder("Collector", "", "accipio", "p1")], reach=reach)
    select(dialog.places_tab, "Collector")
    assert wait_for(lambda: dialog.places_tab.state.text() == "not reachable right now")
    assert dialog.places_tab.state.toolTip() == "boom"


def test_a_late_answer_for_another_place_does_nothing(app, tmp_path):
    gate = threading.Event()

    def reach(peer_id):
        gate.wait(5)
        return ""
    dialog = dialog_for(tmp_path, [Folder("Collector", "", "accipio", "p1")], reach=reach)
    tab = dialog.places_tab
    select(tab, "Collector")
    assert tab.state.text() == "checking…"                                   # the window did not wait
    select(tab, "Default")
    gate.set()
    wait_for(lambda: False, 0.3)                                              # let the thread answer
    assert tab.state.text() == ""


def test_an_answer_after_the_window_is_deleted_raises_nothing(app, tmp_path, monkeypatch):
    gate = threading.Event()
    errors = []
    monkeypatch.setattr(threading, "excepthook", lambda args: errors.append(args))

    def reach(peer_id):
        gate.wait(5)
        return ""
    dialog = dialog_for(tmp_path, [Folder("Collector", "", "accipio", "p1")], reach=reach)
    select(dialog.places_tab, "Collector")
    sip.delete(dialog)
    gate.set()
    assert wait_for(lambda: not any(t.name == "places-reach" for t in threading.enumerate()))
    assert errors == []


def test_the_rule_sentence_and_the_test_box_for_a_rule_that_goes_to_another_accipio(app, tmp_path):
    rule_list = [Rule("Comics to the collector", [Condition("type", ["Comics"])], target="Collector")]
    dialog = dialog_for(tmp_path, [Folder("Collector", "", "accipio", "p1")], rule_list)
    assert dialog.form.where.text() == "another Accipio; its own rules choose the folder there"
    assert style.WARNING_COLOR not in dialog.form.where.styleSheet()
    assert dialog.list.item(0).text() == "1  Comics to the collector  → Collector"
    dialog.test_url.setText("https://example.com/Saga 01.cbz")
    text = dialog.test_result.text()
    assert text.startswith("→ Collector (another Accipio)\n") and "sent to Collector (another Accipio)" in text
    assert os.path.join("Downloads", "Saga 01.cbz") not in text
    dialog.test_url.setText("https://example.com/notes.txt")                  # no rule fits: Default, a path again
    assert dialog.test_result.text().startswith("→ " + os.path.join(str(tmp_path / "Downloads"), "notes.txt"))


def test_the_rules_window_leaves_the_tab_title_and_still_saves_the_place_on_ok(app, tmp_path):
    dialog = dialog_for(tmp_path, [Folder("Collector", "", "accipio", "p1", True, "wait")])
    assert dialog.tabs.tabText(0) == "1 · Places"
    dialog.accept()
    assert dialog.result_library().folders == [Folder("Collector", "", "accipio", "p1", True, "wait")]
