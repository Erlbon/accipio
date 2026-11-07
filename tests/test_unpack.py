"""Unpacking archives (trial, 2026-11-05): what is inside, the suggested choice, where each file goes, safe
extraction (no path escapes, no bombs), Windows' internet mark on every file, and Undo. Zip and tar only."""
import io
import os
import tarfile
import zipfile

import pytest

from accipio.core import unpack
from accipio.core.unpack import Entry, UnpackError


def make_zip(path, files, password_flag=False):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(zipfile.ZipInfo(name), data)
    if password_flag:                       # zipfile clears the flag when writing, so set it in the bytes
        data = bytearray(open(path, "rb").read())
        for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            at = data.find(signature)
            data[at + offset] |= 0x1
        open(path, "wb").write(bytes(data))
    return str(path)


def make_tar(path, files):
    with tarfile.open(path, "w:gz") as t:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return str(path)


# ---- which files are archives (42: by the ending only) -----------------------------------------------------

@pytest.mark.parametrize("name, answer", [
    ("pack.zip", True), ("PACK.ZIP", True), ("src.tar", True), ("src.tar.gz", True), ("src.tgz", True),
    ("src.tar.bz2", True), ("src.tar.xz", True),
    ("Saga 001.cbz", False), ("book.epub", False), ("letter.docx", False), ("tool.jar", False), ("app.apk", False),
    ("pack.rar", False), ("pack.7z", False),           # need 7-Zip: later (45: A, then C)
    ("notes.gz", False),                               # one compressed file, not an archive of several
])
def test_only_real_archive_endings_are_opened(name, answer):
    assert unpack.is_archive(name) is answer


# ---- looking inside ------------------------------------------------------------------------------------------

def test_the_files_inside_a_zip_and_a_tar(tmp_path):
    z = make_zip(tmp_path / "a.zip", {"tool/setup.exe": b"MZ", "tool/readme.pdf": b"%PDF", "tool/docs/": b""})
    assert unpack.contents(z) == [Entry("tool/setup.exe", 2), Entry("tool/readme.pdf", 4)]
    t = make_tar(tmp_path / "a.tar.gz", {"x/a.txt": b"hello"})
    assert unpack.contents(t) == [Entry("x/a.txt", 5)]


def test_a_password_zip_and_a_damaged_one_say_why(tmp_path):
    locked = make_zip(tmp_path / "locked.zip", {"a.txt": b"x"}, password_flag=True)
    with pytest.raises(UnpackError, match="password"):
        unpack.contents(locked)
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"PK\x03\x04 not really")
    with pytest.raises(UnpackError, match="damaged"):
        unpack.contents(str(broken))


def test_a_zip_bomb_is_refused_before_anything_is_written(tmp_path):
    bomb = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("zeros.bin", b"\0" * (3 * 1024 * 1024))
    with pytest.raises(UnpackError, match="too large"):
        unpack.contents(str(bomb), limit=1024 * 1024)


# ---- the suggested button ------------------------------------------------------------------------------------

def test_the_suggestion_follows_the_contents():
    known = lambda name: name.endswith((".cbz", ".pdf"))
    assert unpack.suggest([Entry("tool/setup.exe", 1), Entry("tool/readme.pdf", 1)], known) == unpack.FOLDER
    assert unpack.suggest([Entry("Saga 001.cbz", 1), Entry("Saga 002.cbz", 1)], known) == unpack.SORT
    assert unpack.suggest([Entry("Saga 001.cbz", 1), Entry("info.nfo", 1)], known) == unpack.KEEP
    assert unpack.suggest([], known) == unpack.KEEP


# ---- where each file goes -------------------------------------------------------------------------------------

def test_as_a_folder_keeps_everything_together_without_doubling_the_top_folder(tmp_path):
    entries = [Entry("tool/setup.exe", 1), Entry("tool/docs/readme.pdf", 1)]
    moves = unpack.plan_folder(entries, str(tmp_path / "Programs"), "tool-1.2.zip", exists=os.path.exists)
    assert [(m.entry, m.target) for m in moves] == [
        ("tool/setup.exe", str(tmp_path / "Programs" / "tool-1.2" / "setup.exe")),
        ("tool/docs/readme.pdf", str(tmp_path / "Programs" / "tool-1.2" / "docs" / "readme.pdf"))]


def test_as_a_folder_never_overwrites_an_older_folder(tmp_path):
    (tmp_path / "Programs" / "tool").mkdir(parents=True)
    moves = unpack.plan_folder([Entry("setup.exe", 1)], str(tmp_path / "Programs"), "tool.zip", exists=os.path.exists)
    assert moves[0].target == str(tmp_path / "Programs" / "tool (1)" / "setup.exe")


def test_sorting_sends_each_file_by_the_rules_and_the_rest_to_default(tmp_path):
    comics, default = str(tmp_path / "Comics"), str(tmp_path / "Default")

    def place(name):
        return (comics, name, 'rule 3 "Comics"') if name.endswith(".cbz") else None
    entries = [Entry("Saga 001.cbz", 1), Entry("extras/Saga 002.cbz", 1), Entry("info.nfo", 1)]
    moves = unpack.plan_sort(entries, place, default, "pack.zip", exists=os.path.exists)
    assert [(m.entry, m.target, m.why) for m in moves] == [
        ("Saga 001.cbz", os.path.join(comics, "Saga 001.cbz"), 'rule 3 "Comics"'),
        ("extras/Saga 002.cbz", os.path.join(comics, "Saga 002.cbz"), 'rule 3 "Comics"'),
        ("info.nfo", os.path.join(default, "pack", "info.nfo"), "no rule fits")]


def test_two_files_of_one_name_never_share_a_target(tmp_path):
    comics = str(tmp_path / "Comics")
    entries = [Entry("a/cover.cbz", 1), Entry("b/cover.cbz", 1)]
    moves = unpack.plan_sort(entries, lambda n: (comics, n, "rule"), str(tmp_path), "p.zip", exists=os.path.exists)
    assert [os.path.basename(m.target) for m in moves] == ["cover.cbz", "cover (1).cbz"]


@pytest.mark.parametrize("hostile", ["../../evil.exe", "/etc/evil.exe", "C:/Windows/evil.exe", "..\\..\\evil.exe"])
def test_no_entry_ever_lands_outside_its_folder(tmp_path, hostile):
    base = tmp_path / "Programs"
    moves = unpack.plan_folder([Entry(hostile, 1)], str(base), "x.zip", exists=os.path.exists)
    target = os.path.normpath(moves[0].target)
    assert target.startswith(str(base / "x") + os.sep) and target.endswith("evil.exe")


# ---- doing it, and undoing it ----------------------------------------------------------------------------------

def test_unpacking_writes_the_files_marks_them_and_undo_takes_them_back(tmp_path):
    z = make_zip(tmp_path / "tool.zip", {"tool/setup.exe": b"MZ", "tool/docs/readme.pdf": b"%PDF"})
    moves = unpack.plan_folder(unpack.contents(z), str(tmp_path / "Programs"), "tool.zip", exists=os.path.exists)
    done = unpack.extract(z, moves, url="https://a.org/tool.zip", page_url="https://a.org/")
    setup = tmp_path / "Programs" / "tool" / "setup.exe"
    assert setup.read_bytes() == b"MZ" and (tmp_path / "Programs" / "tool" / "docs" / "readme.pdf").exists()
    if os.name == "nt":
        with open(str(setup) + ":Zone.Identifier", encoding="utf-8") as f:
            assert "ZoneId=3" in f.read()                     # as Windows' own unzip does
    unpack.undo(done)
    assert not (tmp_path / "Programs" / "tool").exists() and (tmp_path / "Programs").exists() is False
    assert (tmp_path / "tool.zip").exists()


def test_undo_never_deletes_a_file_that_was_changed_since(tmp_path):
    z = make_zip(tmp_path / "p.zip", {"a.txt": b"one"})
    moves = unpack.plan_folder(unpack.contents(z), str(tmp_path / "Out"), "p.zip", exists=os.path.exists)
    done = unpack.extract(z, moves)
    (tmp_path / "Out" / "p" / "a.txt").write_bytes(b"edited by the user")
    kept = unpack.undo(done)
    assert (tmp_path / "Out" / "p" / "a.txt").exists() and kept == [str(tmp_path / "Out" / "p" / "a.txt")]


def zip_bytes(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buffer.getvalue()


# ---- in the queue ------------------------------------------------------------------------------------------------

@pytest.fixture
def world(tmp_path):
    from accipio.core.queue import Queue
    from accipio.core.store import Store
    from tests import samples
    from tests.test_queue import TUE_1014, FakeEngine, FakeProber
    base = str(tmp_path / "Downloads")
    engine, prober = FakeEngine(), FakeProber()
    rule_list = samples.sorting_rules()
    q = Queue(Store(str(tmp_path / "state" / "queue.db")), engine, lambda: rule_list, base, prober=prober,
              clock=lambda: TUE_1014, library_set=samples.sorting_library)
    q.unpack_on = True
    return q, engine, prober, base, rule_list


def arrive(world, url, files):
    q, engine, prober, base, _ = world
    prober.serve(url, url.rsplit("/", 1)[1])
    row_id = q.add(url, page_url="https://a.org/")
    engine.finish(q.store.get(row_id).gid, zip_bytes(files))
    q.refresh()
    return row_id


def test_with_the_trial_off_an_archive_is_just_a_file(world):
    q = world[0]
    q.unpack_on = False
    row_id = arrive(world, "https://a.org/tool.zip", {"tool/setup.exe": b"MZ"})
    assert q.store.get(row_id).unpack == {}


def test_a_finished_archive_waits_for_a_choice_with_a_suggestion(world):
    q, engine, prober, base, _ = world
    row_id = arrive(world, "https://a.org/tool.zip", {"tool/setup.exe": b"MZ", "tool/readme.pdf": b"%PDF"})
    row = q.store.get(row_id)
    assert row.unpack == {"state": "waiting", "count": 2, "suggest": "folder"}
    plan = q.archive_plan(row_id, "folder")
    assert [m.target for m in plan] == [os.path.join(base, "Archives", "tool", "setup.exe"),
                                        os.path.join(base, "Archives", "tool", "readme.pdf")]
    sorted_plan = q.archive_plan(row_id, "sort")
    assert [m.target for m in sorted_plan] == [os.path.join(base, "Programs", "setup.exe"),
                                               os.path.join(base, "Documents", "readme.pdf")]


def test_choosing_unpacks_and_undo_puts_it_back_to_choose_again(world):
    q, engine, prober, base, _ = world
    row_id = arrive(world, "https://a.org/tool.zip", {"tool/setup.exe": b"MZ", "tool/readme.pdf": b"%PDF"})
    q.unpack_archive(row_id, "folder", folder=os.path.join(base, "Programs"))
    row = q.store.get(row_id)
    assert row.unpack["state"] == "done" and row.unpack["count"] == 2
    assert os.path.isfile(os.path.join(base, "Programs", "tool", "readme.pdf"))
    assert "unpacked 2 files as a folder" in row.why
    assert q.undo_unpack(row_id) == []
    assert not os.path.exists(os.path.join(base, "Programs", "tool"))
    assert q.store.get(row_id).unpack["state"] == "waiting" and os.path.isfile(row.path)


def test_keep_packed_ends_the_question(world):
    q = world[0]
    row_id = arrive(world, "https://a.org/pack.zip", {"a.cbz": b"PK"})
    q.keep_packed(row_id)
    assert q.store.get(row_id).unpack == {"state": "kept"}


def test_a_rule_that_says_sort_unpacks_without_asking(world):
    q, engine, prober, base, rule_list = world
    rule_list.insert(0, rules_mod().remembered_rule("Archives", "", "sort", "Archives"))
    row_id = arrive(world, "https://a.org/pack.zip", {"Saga 001.cbz": b"PK", "Saga 002.cbz": b"PK"})
    row = q.store.get(row_id)
    assert row.unpack["state"] == "done" and row.unpack["mode"] == "sort"
    assert os.path.isfile(os.path.join(base, "Comics", "Saga 001.cbz"))


# 2026-11-07, 76C + 77A + 78A: a zip whose own name no rule with an archive choice takes follows the rule its
# files would land under (getcomics.org packs: a .zip of .cbz files, and a Comics rule that says "unpacked as a
# folder there"), when that rule's files are most of the zip by size; files under other rules make Accipio ask
# once they are more than a small part (a cover or an .nfo goes along).

def comics_unpack(rule_list, choice="folder"):
    comics = next(r for r in rule_list if r.name == "Comics")
    comics.unpack = choice
    return comics


def test_a_zip_of_comics_follows_the_comics_rules_archive_choice(world):
    q, engine, prober, base, rule_list = world
    comics_unpack(rule_list)
    row_id = arrive(world, "https://getcomics.org/dlds/Saga.Vol.1-6.zip",
                    {"Saga 001.cbz": b"PK" * 5000, "Saga 002.cbz": b"PK" * 5000, "cover.jpg": b"\xff\xd8" * 200,
                     "info.nfo": b"x" * 50})
    row = q.store.get(row_id)
    assert row.unpack["state"] == "done" and row.unpack["mode"] == "folder" and row.unpack["by_rule"]
    folder = os.path.join(base, "Comics", "Saga.Vol.1-6")
    for name in ("Saga 001.cbz", "Saga 002.cbz", "cover.jpg", "info.nfo"):    # the extras go along
        assert os.path.isfile(os.path.join(folder, name))


def test_a_pack_with_its_own_top_folder_is_not_wrapped_in_a_second_one(world):
    """79C: "as a folder" never repeats the folder the zip already has inside."""
    q, engine, prober, base, rule_list = world
    comics_unpack(rule_list)
    arrive(world, "https://getcomics.org/dlds/Saga.zip", {"Saga/Saga 001.cbz": b"PK" * 100, "Saga/Saga 002.cbz": b"PK"})
    assert os.path.isfile(os.path.join(base, "Comics", "Saga", "Saga 001.cbz"))
    assert not os.path.exists(os.path.join(base, "Comics", "Saga", "Saga"))


def test_sorted_by_the_rules_puts_the_comics_straight_in_the_comics_folder(world):
    q, engine, prober, base, rule_list = world
    comics_unpack(rule_list, "sort")
    arrive(world, "https://getcomics.org/dlds/Saga.zip", {"Saga/Saga 001.cbz": b"PK" * 100})
    assert os.path.isfile(os.path.join(base, "Comics", "Saga 001.cbz"))


def test_a_zip_mostly_of_something_else_still_asks(world):
    q, engine, prober, base, rule_list = world
    comics_unpack(rule_list)
    row_id = arrive(world, "https://a.org/mixed.zip", {"one.cbz": b"PK" * 10, "big.bin": b"x" * 5000})
    assert q.store.get(row_id).unpack["state"] == "waiting"


def test_files_under_two_rules_make_accipio_ask(world):
    """78A: comics and a real share of ebooks: which rule's choice is meant is a guess, so Accipio asks."""
    q, engine, prober, base, rule_list = world
    comics_unpack(rule_list)
    row_id = arrive(world, "https://a.org/both.zip", {"one.cbz": b"PK" * 3000, "book.epub": b"PK" * 1000})
    assert q.store.get(row_id).unpack["state"] == "waiting"


def test_the_zips_own_rule_choice_still_comes_first(world):
    q, engine, prober, base, rule_list = world
    comics_unpack(rule_list, "folder")
    rule_list.insert(0, rules_mod().remembered_rule("Archives", "", "sort", "Archives"))
    row_id = arrive(world, "https://a.org/pack.zip", {"Saga 001.cbz": b"PK"})
    assert q.store.get(row_id).unpack["mode"] == "sort"


def test_the_archive_can_go_to_the_recycle_bin_after_unpacking(world, monkeypatch):
    q, engine, prober, base, _ = world
    trashed = []
    monkeypatch.setattr("accipio.core.queue.trash", lambda path: trashed.append(path))
    q.after_unpack = "delete"
    row_id = arrive(world, "https://a.org/p.zip", {"a.txt": b"x"})
    path = q.store.get(row_id).path
    q.unpack_archive(row_id, "folder")
    assert trashed == [path] and q.store.get(row_id).unpack["trashed"] is True


def test_a_password_zip_says_so_on_its_row(world):
    q, engine, prober, base, _ = world
    prober.serve("https://a.org/l.zip", "l.zip")
    row_id = q.add("https://a.org/l.zip")
    gid = q.store.get(row_id).gid
    engine.finish(gid, b"PK\x03\x04 broken")
    q.refresh()
    row = q.store.get(row_id)
    assert row.state == "complete" and row.unpack["state"] == "failed" and "damaged" in row.unpack["message"]


def test_an_older_queue_file_gets_the_new_column(tmp_path):
    import sqlite3
    from accipio.core.store import Store
    path = str(tmp_path / "queue.db")
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE downloads (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, page_url TEXT NOT NULL "
                "DEFAULT '', filename TEXT NOT NULL DEFAULT '', folder TEXT NOT NULL DEFAULT '', mime TEXT NOT NULL "
                "DEFAULT '', size INTEGER, source TEXT NOT NULL DEFAULT 'direct', video TEXT NOT NULL DEFAULT '{}', "
                "headers TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL, gid TEXT NOT NULL DEFAULT '', done INTEGER "
                "NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0, resumable INTEGER NOT NULL DEFAULT 0, restarts "
                "INTEGER NOT NULL DEFAULT 0, pinned INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', rules "
                "TEXT NOT NULL DEFAULT '', why TEXT NOT NULL DEFAULT '', added TEXT NOT NULL, finished TEXT NOT NULL "
                "DEFAULT '')")
    old.execute("INSERT INTO downloads (url, state, added) VALUES ('https://a.org/f', 'complete', '2026-11-01T10:00:00')")
    old.execute("PRAGMA user_version=1")
    old.commit()
    old.close()
    store = Store(path)
    assert store.rows()[0].unpack == {}
    store.update(1, unpack={"state": "kept"})
    assert store.get(1).unpack == {"state": "kept"}


def test_a_rule_remembers_the_archive_choice_and_says_so():
    rules = rules_mod()
    rule = rules.remembered_rule("Archives", "Comic sites", "sort", "Comics")
    assert [(c.kind, c.values) for c in rule.conditions] == [("sites", ["Comic sites"]), ("type", ["Archives"])]
    assert (rule.name, rule.target, rule.unpack) == ("Archives from Comic sites", "Comics", "sort")
    assert rules.remembered_rule("Archives", "", "keep", "").unpack == ""
    library = rules.Library([rules.Folder("Comics", "%default%/Comics")], rules.default_groups(),
                            [rules.SiteGroup("Comic sites", ["getcomics.org"])])
    d = rules.Download("https://getcomics.org/x/pack.zip", "pack.zip")
    placement = rules.place([rule], d, "D:/Downloads", library)
    assert placement.unpack == "sort" and "archives sorted by the rules" in placement.why()
    again = rules.rules_from_json(rules.rules_to_json([rule], library))[0]
    assert again.unpack == "sort"
    assert rules.validate(rules.Rule("x", unpack="explode"), library) == [
        "archives: 'explode' is not a choice (keep, folder or sort)"]


def rules_mod():
    from accipio.core import rules
    return rules


def test_a_failure_halfway_leaves_nothing_behind(tmp_path, monkeypatch):
    z = make_zip(tmp_path / "p.zip", {"a.txt": b"one", "b.txt": b"two"})
    moves = unpack.plan_folder(unpack.contents(z), str(tmp_path / "Out"), "p.zip", exists=os.path.exists)
    real = unpack._copy_member
    calls = []

    def flaky(*args):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("disk full")
        return real(*args)
    monkeypatch.setattr(unpack, "_copy_member", flaky)
    with pytest.raises(UnpackError, match="disk full"):
        unpack.extract(z, moves)
    assert not (tmp_path / "Out").exists()
