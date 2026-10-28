"""The rules engine: ordered rules, stop/continue, conditions, tokens, "why here?"."""
import datetime
import os

import pytest

from accipio.core import rules as r
from accipio.core.rules import Condition, Download, Rule

TUE_1014 = datetime.datetime(2026, 10, 27, 10, 14)      # Tuesday, ISO week 44
SAT_1014 = datetime.datetime(2026, 10, 31, 10, 14)


@pytest.fixture
def base(tmp_path):
    return str(tmp_path / "Downloads")


def dl(filename="file.bin", url=None, when=TUE_1014, **kw):
    return Download(url=url or f"https://example.com/{filename}", filename=filename, when=when, **kw)


def under(base, *parts):
    return os.path.normpath(os.path.join(base, *parts))


# ---- the walk --------------------------------------------------------------------------------------------

def test_no_rules_means_the_base_folder(base):
    p = r.place([], dl("a.zip"), base)
    assert p.folder == os.path.normpath(base) and p.filename == "a.zip" and p.steps == []
    assert p.why() == "No rule matched: the base folder."


def test_the_first_matching_rule_that_stops_wins(base):
    rules = [Rule("Zips", [Condition("type", ["zip"])], subfolder="Zips"),
             Rule("Archives", [Condition("type", ["Archives"])], subfolder="Archives")]
    p = r.place(rules, dl("a.zip"), base)
    assert p.folder == under(base, "Zips") and p.rule_numbers == "1"


def test_continue_lets_a_domain_rule_and_a_type_rule_combine(base, tmp_path):
    work = str(tmp_path / "Work")
    rules = [Rule("Work", [Condition("domain", ["knowit.no"])], folder=work, stop=False),
             Rule("Video sites", [Condition("source", ["video"])], folder="Video"),
             Rule("Archives", [Condition("type", ["Archives"])], subfolder="Archives/{yyyy}-{MM}"),
             Rule("Other", subfolder="Other")]
    p = r.place(rules, dl("release-2.4.zip", url="https://files.knowit.no/release-2.4.zip"), base)
    assert p.path == os.path.join(os.path.normpath(os.path.join(work, "Archives", "2026-10")), "release-2.4.zip")
    assert p.rule_numbers == "1+3"
    assert p.why().startswith('rule 1 "Work": set folder ') and ' → rule 3 "Archives": add subfolder ' in p.why()
    p = r.place(rules, dl("notes.txt", url="https://example.com/notes.txt"), base)
    assert p.folder == under(base, "Other") and p.rule_numbers == "4"


def test_disabled_rules_are_skipped(base):
    rules = [Rule("Off", subfolder="Off", enabled=False), Rule("On", subfolder="On")]
    assert r.place(rules, dl(), base).folder == under(base, "On")


def test_a_relative_folder_is_under_the_base_and_an_absolute_one_is_kept(base, tmp_path):
    assert r.place([Rule("x", folder="Media/Video")], dl(), base).folder == under(base, "Media", "Video")
    elsewhere = str(tmp_path / "Elsewhere")
    assert r.place([Rule("x", folder=elsewhere)], dl(), base).folder == os.path.normpath(elsewhere)
    assert r.place([Rule("x", folder="Media\\Video")], dl(), base).folder == under(base, "Media", "Video")


def test_rename_and_commands(base):
    rules = [Rule("Name", rename="{yyyy}-{MM}-{dd} {name}.{ext}", command="unpack.cmd", stop=False),
             Rule("Also", command="notify.cmd")]
    p = r.place(rules, dl("Report.PDF"), base)
    assert p.filename == "2026-10-27 Report.pdf"
    assert p.commands == ["unpack.cmd", "notify.cmd"]


# ---- conditions ------------------------------------------------------------------------------------------

def hit(condition, download):
    return r.condition_matches(condition, download, download.when)


def test_domain_plain_matches_subdomains_wildcards_and_the_page(base):
    c = Condition("domain", ["youtube.com"])
    assert hit(c, dl(url="https://www.youtube.com/watch?v=1")) and hit(c, dl(url="https://youtube.com/x"))
    assert not hit(c, dl(url="https://notyoutube.com/x"))
    assert hit(Condition("domain", ["*.github.com"]), dl(url="https://objects.github.com/a"))
    assert not hit(Condition("domain", ["*.github.com"]), dl(url="https://github.com/a"))
    assert hit(Condition("domain", ["https://github.com/Erlbon/accipio"]), dl(url="https://github.com/x"))
    from_page = dl(url="https://cdn.example.net/f.zip", page_url="https://github.com/releases")
    assert hit(Condition("domain", ["github.com"]), from_page)
    assert hit(Condition("domain", ["github.com"], where="page"), from_page)
    assert not hit(Condition("domain", ["github.com"], where="file"), from_page)


def test_type_by_group_extension_and_mime():
    assert hit(Condition("type", ["Video"]), dl("Talk.MKV"))
    assert hit(Condition("type", ["video"]), dl("talk.mkv"))
    assert hit(Condition("type", [".mkv"]), dl("talk.mkv")) and hit(Condition("type", ["mkv"]), dl("talk.mkv"))
    assert hit(Condition("type", ["Archives"]), dl("src.tar.gz")) and hit(Condition("type", ["tar.gz"]), dl("src.tar.gz"))
    assert hit(Condition("type", ["video/*"]), dl("x", mime="video/mp4; codecs=avc1"))
    assert not hit(Condition("type", ["Video", "Audio"]), dl("book.epub"))


def test_a_known_extension_wins_over_the_mime_type():
    epub_as_zip = dl("book.epub", mime="application/zip")
    assert hit(Condition("type", ["Ebooks"]), epub_as_zip)
    assert not hit(Condition("type", ["Archives"]), epub_as_zip)
    php_that_is_a_zip = dl("download.php", mime="application/zip")
    assert hit(Condition("type", ["Archives"]), php_that_is_a_zip)


def test_name_wildcards_and_regexes_ignore_case():
    assert hit(Condition("name", ["*S01E??*"]), dl("show.s01e05.mkv"))
    assert hit(Condition("name", [r"s\d\de\d\d"], regex=True), dl("Show.S01E05.mkv"))
    assert not hit(Condition("name", ["*.iso"]), dl("a.zip"))


def test_size_needs_a_known_size():
    big = Condition("size", min_size=1_000_000_000)
    assert hit(big, dl(size=2_000_000_000)) and not hit(big, dl(size=5)) and not hit(big, dl())
    small = Condition("size", max_size=10)
    assert hit(small, dl(size=10)) and not hit(small, dl(size=11))


def test_time_uses_cron_lines_and_the_start_moment():
    office = Condition("time", ["* 8-15 * * 1-5"])
    assert hit(office, dl(when=TUE_1014)) and not hit(office, dl(when=SAT_1014))
    assert not hit(Condition("time", ["not cron"]), dl())


def test_source_and_video_fields():
    video = dl("Talk.mkv", source="video", video={"channel": "XYZ Talks", "uploader": "xyz"})
    assert hit(Condition("source", ["video"]), video) and not hit(Condition("source", ["video"]), dl())
    assert hit(Condition("video", ["xyz*"], where="channel"), video)
    assert hit(Condition("video", ["^XYZ"], where="channel", regex=True), video)
    assert not hit(Condition("video", ["*"], where="playlist"), video)


def test_an_unfinished_condition_never_fires():
    assert not hit(Condition("domain", []), dl()) and not hit(Condition("name", ["  "]), dl())
    assert not hit(Condition("size"), dl(size=5))


# ---- tokens and safe names -------------------------------------------------------------------------------

def test_date_tokens_rotate_folders(base):
    rule = [Rule("Dated", subfolder="{yyyy}/{MM}/week {week} {weekday}")]
    assert r.place(rule, dl(when=TUE_1014), base).folder == under(base, "2026", "10", "week 44 Tuesday")


def test_token_values_cannot_climb_out_or_break_the_path(base):
    video = dl("x.mp4", source="video", video={"uploader": "AC/DC: Live?"})
    p = r.place([Rule("Video", subfolder="Video/{uploader}")], video, base)
    assert p.folder == under(base, "Video", "AC_DC_ Live_")
    sneaky = dl("x.mp4", source="video", video={"uploader": ".."})
    assert r.place([Rule("Video", subfolder="Video/{uploader}")], sneaky, base).folder == under(base, "Video", "unknown")
    assert r.place([Rule("Video", subfolder="Video/{uploader}")], dl("x.mp4"), base).folder == under(base, "Video", "unknown")


def test_domain_token_drops_www(base):
    assert r.place([Rule("d", subfolder="{domain}")], dl(url="https://www.example.org/f"), base).folder == \
        under(base, "example.org")


def test_safe_part_handles_windows_trouble():
    assert r.safe_part("con") == "_con" and r.safe_part("Aux.txt") == "_Aux.txt"
    assert r.safe_part("name. ") == "name" and r.safe_part("") == "unknown" and r.safe_part("...") == "unknown"
    assert r.safe_part('a<b>c:"d|e') == "a_b_c__d_e"


def test_a_bad_server_file_name_is_made_safe(base):
    assert r.place([], dl('re:port?.pdf'), base).filename == "re_port_.pdf"


def test_split_name():
    assert r.split_name("release-2.4.tar.gz") == ("release-2.4", "tar.gz")
    assert r.split_name("Movie.MKV") == ("Movie", "mkv")
    assert r.split_name("README") == ("README", "") and r.split_name(".bashrc") == (".bashrc", "")


def test_free_name_never_overwrites(tmp_path):
    folder = str(tmp_path)
    taken = {os.path.join(folder, n) for n in ("a.zip", "a (1).zip", "src.tar.gz", "README")}
    exists = taken.__contains__
    assert r.free_name(folder, "b.zip", exists) == "b.zip"
    assert r.free_name(folder, "a.zip", exists) == "a (2).zip"
    assert r.free_name(folder, "src.tar.gz", exists) == "src (1).tar.gz"
    assert r.free_name(folder, "README", exists) == "README (1)"


# ---- editor checks and storage ---------------------------------------------------------------------------

def test_validate_reports_problems_in_words():
    bad = Rule("Bad", [Condition("time", ["* 25 * * *"]), Condition("name", ["("], regex=True),
                       Condition("domain", []), Condition("size"), Condition("video", ["x"]),
                       Condition("source", ["torrent"]), Condition("colour", ["red"])],
               folder="{yyyy}/{nope}", rename="a/b")
    problems = r.validate(bad)
    text = "\n".join(problems)
    assert "unknown token {nope}" in text and "cannot contain" in text
    assert "outside 0-23" in text and "not a regular expression" in text and "no values yet" in text
    assert "smallest or a largest" in text and "pick a video field" in text and "'torrent' is not" in text
    assert "unknown kind" in text
    assert r.validate(Rule("Good", [Condition("type", ["Video"])], subfolder="Video/{yyyy}")) == []


def test_rules_survive_a_round_trip_and_unknown_keys_are_skipped():
    rules = r.default_rules() + [Rule("Night", [Condition("time", ["* 22-23 * * *"])], folder="Night", stop=False)]
    again = r.rules_from_json(r.rules_to_json(rules))
    assert again == rules
    newer = '{"format": 2, "rules": [{"name": "x", "colour": "red", "conditions": [{"kind": "name", "values": ["*"], "fuzzy": true}]}]}'
    assert r.rules_from_json(newer) == [Rule("x", [Condition("name", ["*"])])]


@pytest.mark.parametrize("download, sub", [
    (dl("Talk.mp4", source="video", video={"uploader": "Someone"}), ("Video", "Someone")),
    (dl("clip.webm"), ("Video",)),
    (dl("song.flac"), ("Audio",)),
    (dl("photo.JPG"), ("Images",)),
    (dl("Dune.epub"), ("Books",)),
    (dl("Saga 01.cbz"), ("Books",)),
    (dl("report.pdf"), ("Documents",)),
    (dl("src.tar.gz"), ("Archives",)),
    (dl("setup.exe"), ("Programs",)),
    (dl("mystery.xyz"), ("Other",)),
    (dl("get.php", mime="application/x-msdownload"), ("Programs",)),
])
def test_the_default_rules_sort_by_kind(base, download, sub):
    rules = r.default_rules()
    assert all(r.validate(rule) == [] for rule in rules)
    assert r.place(rules, download, base).folder == under(base, *sub)
