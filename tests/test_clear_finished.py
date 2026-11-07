"""When finished downloads leave the list (2026-11-07, 71A + 75A): kept until cleared by hand, at the next start (the
default, as decided in 51A), a number of minutes after they finished (15 unless changed), or as soon as they finish.
Only the list: the files stay. An archive still waiting for the user's choice stays until it is answered."""
import datetime
import json

import pytest

from accipio.core import rules, settings
from accipio.core.queue import AT_ONCE_GRACE, Queue
from accipio.core.store import ACTIVE, COMPLETE, Store
from tests.test_queue import FakeEngine, FakeProber

NOW = datetime.datetime(2026, 11, 7, 12, 0, 0)


def stamp(minutes_ago):
    return (NOW - datetime.timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


def filled(tmp_path):
    store = Store(str(tmp_path / "q.db"))
    ids = {
        "old": store.add("https://a.org/old", state=COMPLETE, finished=stamp(60)),
        "recent": store.add("https://a.org/recent", state=COMPLETE, finished=stamp(5)),
        "waiting": store.add("https://a.org/w.zip", state=COMPLETE, finished=stamp(60),
                             unpack={"state": "waiting", "count": 3}),
        "running": store.add("https://a.org/run", state=ACTIVE, gid="g1"),
    }
    return store, ids


def queue(store, tmp_path, clock=lambda: NOW, **kw):
    engine = FakeEngine()
    engine.jobs["g1"] = dict(url="https://a.org/run", folder=str(tmp_path), filename="run", headers=[],
                             state="active", done=0, total=0, code="", message="")
    return Queue(store, engine, rules.default_rules, str(tmp_path), prober=FakeProber(), clock=clock, **kw)


def left(store, ids):
    have = {r.id for r in store.rows()}
    return sorted(name for name, row_id in ids.items() if row_id in have)


@pytest.mark.parametrize("how, kept", [
    (settings.CLEAR_NEVER, ["old", "recent", "running", "waiting"]),
    (settings.CLEAR_AT_START, ["running", "waiting"]),
    (settings.CLEAR_AFTER, ["recent", "running", "waiting"]),
    (settings.CLEAR_AT_ONCE, ["running", "waiting"]),
])
def test_at_start(tmp_path, how, kept):
    store, ids = filled(tmp_path)
    queue(store, tmp_path, clear_finished=how, clear_after_minutes=15)
    assert left(store, ids) == kept


def test_after_a_while_rows_go_once_their_minutes_are_up(tmp_path):
    store, ids = filled(tmp_path)
    now = {"t": NOW}
    q = queue(store, tmp_path, clock=lambda: now["t"], clear_finished=settings.CLEAR_AFTER, clear_after_minutes=15)
    assert "recent" in left(store, ids)
    now["t"] = NOW + datetime.timedelta(minutes=9)          # 14 minutes after it finished
    q.refresh()
    assert "recent" in left(store, ids)
    now["t"] = NOW + datetime.timedelta(minutes=10, seconds=1)
    q.refresh()
    assert left(store, ids) == ["running", "waiting"]


def test_at_once_a_download_leaves_the_list_moments_after_it_finishes(tmp_path):
    """A few seconds' grace, so the window sees it finish and its "finished" notice and sound come."""
    store, ids = filled(tmp_path)
    now = {"t": NOW}
    q = queue(store, tmp_path, clock=lambda: now["t"], clear_finished=settings.CLEAR_AT_ONCE)
    q.engines["direct"].finish("g1")
    q.refresh()
    assert left(store, ids) == ["running", "waiting"]       # just finished: still shown
    now["t"] = NOW + datetime.timedelta(seconds=AT_ONCE_GRACE + 1)
    q.refresh()
    assert left(store, ids) == ["waiting"]
    assert (tmp_path / "run").is_file()                     # only the list: the file stays


def test_an_answered_archive_goes_too(tmp_path):
    store, ids = filled(tmp_path)
    q = queue(store, tmp_path, clear_finished=settings.CLEAR_AT_ONCE)
    store.update(ids["waiting"], unpack={"state": "kept"})       # finished an hour ago, answered now
    q.refresh()
    assert "waiting" not in left(store, ids)


def test_clear_finished_by_hand_still_clears_everything_finished(tmp_path):
    store, ids = filled(tmp_path)
    q = queue(store, tmp_path, clear_finished=settings.CLEAR_NEVER)
    q.clear_finished()
    assert left(store, ids) == ["running"]


def test_the_choice_is_saved_and_the_old_history_switch_carries_over(tmp_path):
    folder = str(tmp_path)
    assert (settings.Settings().clear_finished, settings.Settings().clear_after_minutes) == (settings.CLEAR_AT_START, 15)
    for old, new in ((True, settings.CLEAR_NEVER), (False, settings.CLEAR_AT_START)):
        (tmp_path / "settings.json").write_text(json.dumps({"keep_history": old}), encoding="utf-8")
        assert settings.load_settings(folder).clear_finished == new
    s = settings.Settings(clear_finished=settings.CLEAR_AFTER, clear_after_minutes=30)
    settings.save_settings(folder, s)
    again = settings.load_settings(folder)
    assert (again.clear_finished, again.clear_after_minutes) == (settings.CLEAR_AFTER, 30)
    assert "keep_history" not in json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))


def test_odd_values_fall_back_to_the_default(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"clear_finished": "sometimes", "clear_after_minutes": -4}),
                                            encoding="utf-8")
    s = settings.load_settings(str(tmp_path))
    assert s.clear_finished == settings.CLEAR_AT_START and s.clear_after_minutes == 15
