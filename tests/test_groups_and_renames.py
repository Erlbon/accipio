"""Folders, file groups and site groups that rules point at by name, extensions switched off per rule, rename
lines set off by a pattern, and moving rules between PCs (build 2026-11-04#02)."""
import datetime
import json
import os

import pytest

from accipio.core import rules as r
from accipio.core import settings
from tests import samples
from accipio.core.rules import Condition, Download, Folder, Group, Library, Rename, Rule, SiteGroup

TUE = datetime.datetime(2026, 10, 27, 10, 14)


def dl(filename, url=None, page_url="", mime=""):
    return Download(url=url or f"https://example.com/{filename}", filename=filename, page_url=page_url, mime=mime,
                    when=TUE)


def library(folders=(), groups=(), site_groups=()):
    base = samples.sorting_library()
    return Library(base.folders + list(folders), base.groups + list(groups), list(site_groups))


def folder_of(rule_list, download, lib=None, base="B"):
    return r.place(rule_list, download, base, lib).folder


# ---- folders ---------------------------------------------------------------------------------------------

def test_a_rule_points_at_a_named_folder_and_follows_it_when_it_moves():
    lib = library([Folder("Comics inbox", "D:/Comics/_Inbox/{yyyy}-{MM}")])
    rule = [Rule("Comics", [Condition("type", ["Comics"])], target="Comics inbox")]
    assert folder_of(rule, dl("Saga 01.cbz"), lib) == os.path.normpath("D:/Comics/_Inbox/2026-10")
    r.find_named(lib.folders, "comics inbox").path = "E:/Comics"
    assert folder_of(rule, dl("Saga 01.cbz"), lib) == os.path.normpath("E:/Comics")
    assert "folder Comics inbox" in r.place(rule, dl("Saga 01.cbz"), "B", lib).why()


def test_a_relative_folder_is_under_the_base_folder_and_a_subfolder_is_added():
    lib = library([Folder("Work", "Work")])
    rule = [Rule("W", target="Work", subfolder="{domain}")]
    assert folder_of(rule, dl("a.pdf", url="https://files.example.org/a.pdf"), lib) == os.path.normpath("B/Work/files.example.org")


def test_old_rules_with_typed_paths_get_named_folders():
    lib = samples.sorting_library()
    rule_list = [Rule("Work stuff", folder="D:/Work"), Rule("More work", folder="D:/Work"), Rule("Plain", subfolder="X")]
    assert r.adopt_folders(rule_list, lib)
    assert [x.target for x in rule_list] == ["Work stuff", "Work stuff", ""]
    assert r.find_named(lib.folders, "Work stuff").path == "D:/Work" and rule_list[0].folder == ""
    assert not r.adopt_folders(rule_list, lib)                                   # nothing left to move


def test_renaming_a_folder_or_group_updates_the_rules_and_used_by_says_which():
    rule_list = [Rule("A", [Condition("type", ["Comics", "cbz"]), Condition("sites", ["Comic sites"])], target="Comics"),
                 Rule("B", [Condition("type", ["Video"])], target="Video")]
    r.rename_references(rule_list, "folder", "comics", "Comics inbox")
    r.rename_references(rule_list, "type", "Comics", "Comic books")
    r.rename_references(rule_list, "sites", "Comic sites", "Link sites")
    assert rule_list[0].target == "Comics inbox"
    assert rule_list[0].conditions[0].values == ["Comic books", "cbz"]
    assert rule_list[0].conditions[1].values == ["Link sites"]
    assert r.rules_using(rule_list, "type", "comic books") == [1] and r.rules_using(rule_list, "folder", "Video") == [2]


# ---- file groups and site groups -------------------------------------------------------------------------

def test_a_file_group_of_your_own_mixes_extensions_and_names():
    lib = library(groups=[Group("Work", extensions=["xlsx"], names=["*invoice*"])])
    rule = [Rule("Work", [Condition("type", ["Work"])], folder="D:/Work")]
    assert folder_of(rule, dl("budget.xlsx"), lib) == os.path.normpath("D:/Work")
    assert folder_of(rule, dl("March INVOICE.pdf"), lib) == os.path.normpath("D:/Work")
    assert folder_of(rule, dl("song.mp3"), lib) == "B"


def test_a_site_group_catches_a_link_site_through_the_page_whatever_hosts_the_file():
    comics = SiteGroup("Comic sites", ["getcomics.org", "readcomicsonline.ru"])
    lib = library(site_groups=[comics])
    rule = [Rule("From comic sites", [Condition("sites", ["Comic sites"])], folder="D:/Comics")]
    hosted = dl("Saga 01.cbz", url="https://pixeldrain.com/api/file/abc", page_url="https://getcomics.org/saga-01/")
    assert folder_of(rule, hosted, lib) == os.path.normpath("D:/Comics")
    assert folder_of(rule, dl("Saga 01.cbz", url="https://pixeldrain.com/api/file/abc"), lib) == "B"   # pasted: no page
    comics.where = "file"
    assert folder_of(rule, hosted, lib) == "B"


def test_file_group_and_site_group_together():
    lib = library(site_groups=[SiteGroup("Comic sites", ["getcomics.org"])])
    rule = [Rule("Comics", [Condition("type", ["Comics"]), Condition("sites", ["Comic sites"])], folder="D:/C")]
    page = "https://getcomics.org/x/"
    assert folder_of(rule, dl("a.cbz", url="https://mega.nz/f", page_url=page), lib) == os.path.normpath("D:/C")
    assert folder_of(rule, dl("a.zip", url="https://mega.nz/f", page_url=page), lib) == "B"


def test_an_edited_built_in_group_changes_what_it_catches():
    lib = samples.sorting_library()
    r.find_named(lib.groups, "archives").extensions.remove("iso")
    lib.groups.append(Group("Disc images", extensions=["iso", "img"]))
    lib.folders.append(Folder("Discs", "Discs"))
    rule_list = [Rule("Discs", [Condition("type", ["Disc images"])], target="Discs")] + samples.sorting_rules()
    assert folder_of(rule_list, dl("ubuntu.iso"), lib) == os.path.normpath("B/Discs")
    assert folder_of(samples.sorting_rules(), dl("ubuntu.iso"), lib) == os.path.normpath("B/Other")


def test_a_mime_type_counts_only_for_an_extension_no_group_knows():
    rule = [Rule("Docs", [Condition("type", ["Documents"])], subfolder="Docs")]
    assert folder_of(rule, dl("get.php", mime="application/pdf")) == os.path.normpath("B/Docs")
    assert folder_of(rule, dl("film.mkv", mime="application/pdf")) == "B"


def test_extensions_switched_off_for_one_rule_only():
    video_but_ts = Rule("Video", [Condition("type", ["Video"], exclude=["ts", ".M2TS"])], subfolder="Video")
    assert folder_of([video_but_ts], dl("film.mkv")) == os.path.normpath("B/Video")
    assert folder_of([video_but_ts], dl("stream.ts")) == "B"
    assert folder_of([video_but_ts], dl("disc.m2ts")) == "B"
    assert folder_of(samples.sorting_rules(), dl("stream.ts"), samples.sorting_library()) == os.path.normpath("B/Video")


def test_books_and_comics_have_their_own_folders_in_the_sample_setup():
    assert folder_of(samples.sorting_rules(), dl("Dune.epub"), samples.sorting_library()) == os.path.normpath("B/Books")
    assert folder_of(samples.sorting_rules(), dl("Saga 01.cbz"), samples.sorting_library()) == os.path.normpath("B/Comics")
    assert all(r.validate(rule, samples.sorting_library()) == [] for rule in samples.sorting_rules())


def test_validate_names_missing_folders_and_groups():
    lib = samples.sorting_library()
    rule = Rule("x", [Condition("sites", ["Nope sites"]), Condition("type", ["Comix", "mkv", "video/*"])], target="Gone")
    problems = "\n".join(r.validate(rule, lib))
    assert "no folder called 'Gone'" in problems and "no site group called 'Nope sites'" in problems
    assert "'Comix' is not a file group" in problems and "mkv" not in problems


# ---- rename lines ----------------------------------------------------------------------------------------

TV = Rename(r"(.+?)[ .]S(\d\d)E(\d\d)", "{1|dots|title} S{2}E{3}.{ext}")


def test_a_rename_line_fires_only_when_its_pattern_is_in_the_name():
    rule = Rule("TV", renames=[TV])
    p = r.place([rule], dl("the.expanse.s02e05.1080p.web.mkv"), "B")
    assert p.filename == "The Expanse S02E05.mkv"
    assert "rename to The Expanse S02E05.mkv (line 1)" in p.why()
    kept = r.place([rule], dl("holiday video.mkv"), "B")
    assert kept.filename == "holiday video.mkv" and "name kept" in kept.why()


def test_the_first_line_that_fits_wins():
    rule = Rule("Two", renames=[Rename(r"draft", "DRAFT {name}.{ext}"), Rename(r"report", "Report.{ext}")])
    assert r.place([rule], dl("report draft.docx"), "B").filename == "DRAFT report draft.docx"
    assert r.place([rule], dl("report final.docx"), "B").filename == "Report.docx"


def test_named_groups_and_a_broken_pattern_is_skipped():
    rule = Rule("Named", renames=[Rename("([", "never.{ext}"), Rename(r"(?P<artist>.+) - (?P<song>.+)\.mp3$", "{song} ({artist}).{ext}")])
    assert r.place([rule], dl("Queen - Bohemian Rhapsody.mp3"), "B").filename == "Bohemian Rhapsody (Queen).mp3"


def test_validate_checks_each_rename_line():
    problems = "\n".join(r.validate(Rule("x", renames=[Rename("", "a.{ext}"), Rename("([", "b"), Rename("(x)", "{2}.{ext}"),
                                                           Rename("x", ""), Rename("x", "a/b.{ext}")])))
    assert "line 1: give a pattern" in problems and "line 2: '([' is not a regular expression" in problems
    assert "line 3: unknown token {2}" in problems and "line 4: give the new name" in problems
    assert "line 5: a file name cannot contain" in problems
    assert r.validate(Rule("ok", renames=[TV])) == []


# ---- the rules file and moving it ------------------------------------------------------------------------

def test_an_old_rename_template_still_renames_every_file():
    old = json.dumps({"format": 1, "rules": [{"name": "Date", "rename": "{yyyy} {name}.{ext}"}]})
    rule = r.rules_from_json(old)[0]
    assert rule.renames == [Rename(r.MATCH_ANY, "{yyyy} {name}.{ext}")]
    assert r.place([rule], dl("a.txt"), "B").filename == "2026 a.txt"
    assert r.library_from_json(old) is None


def test_the_library_is_saved_beside_the_rules(tmp_path):
    folder = str(tmp_path)
    assert settings.load_library(folder) == r.default_library() and r.default_library().folders == []                  # nothing saved: built-in
    lib = library([Folder("Work", "D:/Work")], [Group("Work", names=["*invoice*"])], [SiteGroup("Shops", ["gog.com"], "file")])
    settings.save_rules(folder, samples.sorting_rules(), lib)
    assert settings.load_library(folder) == lib
    settings.save_rules(folder, [])                                               # without a library: kept
    assert settings.load_library(folder) == lib and settings.load_rules(folder) == []


def test_export_and_import_round_trip(tmp_path):
    lib = library([Folder("Work", "D:/Work")], site_groups=[SiteGroup("Shops", ["gog.com"])])
    rule_list = [Rule("Work", [Condition("sites", ["Shops"])], target="Work", renames=[TV])]
    path = str(tmp_path / "my rules.json")
    settings.export_rules(path, rule_list, lib)
    assert settings.import_rules(path) == (rule_list, lib)


@pytest.mark.parametrize("content, words", [
    ("not json at all", "not JSON"),
    ('{"something": "else"}', "has no rules"),
    ('{"rules": [{"no_name": 1}]}', "cannot be read"),
])
def test_importing_something_else_says_so(tmp_path, content, words):
    path = tmp_path / "x.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=words):
        settings.import_rules(str(path))


def test_adding_imported_rules_keeps_both_sides():
    mine = ([Rule("Mine", [Condition("sites", ["Shops"])], target="Work")],
            library([Folder("Work", "D:/Work")], site_groups=[SiteGroup("Shops", ["a.org"])]))
    theirs = ([Rule("Theirs", [Condition("sites", ["Shops"]), Condition("type", ["Video"])], target="Work")],
              library([Folder("Work", "E:/Job")], site_groups=[SiteGroup("Shops", ["b.org"])]))
    rule_list, merged = settings.merge_imported(mine, theirs)
    assert [x.name for x in rule_list] == ["Mine", "Theirs"]
    assert r.find_named(merged.folders, "Work").path == "D:/Work"
    assert r.find_named(merged.folders, "Work (imported)").path == "E:/Job"
    assert r.find_named(merged.site_groups, "Shops (imported)").sites == ["b.org"]
    assert rule_list[1].target == "Work (imported)" and rule_list[1].conditions[0].values == ["Shops (imported)"]
    assert len([g for g in merged.groups if g.name == "Video"]) == 1              # identical: not doubled
    assert mine[1].folders[-1].path == "D:/Work"                                   # mine is not changed in place


# ---- one Default folder (2026-11-04#04) -----------------------------------------------------------------

def test_a_new_setup_sends_everything_to_the_default_folder():
    assert r.default_rules() == [] and r.default_library().folders == []
    p = r.place(r.default_rules(), dl("Dune.epub"), "B", r.default_library())
    assert p.folder == "B" and p.why() == "No rule matched: the Default folder."


def test_folders_are_written_under_percent_default():
    lib = Library([Folder("Comics", "%default%/Comics"), Folder("Root", "%DEFAULT%"), Folder("Old style", "Old")],
                  r.default_groups(), [])
    for target, expected in (("Comics", "B/Comics"), ("Root", "B"), ("Old style", "B/Old"), ("Default", "B")):
        rule = Rule("x", target=target)
        assert r.place([rule], dl("a.zip"), "B", lib).folder == os.path.normpath(expected), target
        assert r.validate(rule, lib) == []
