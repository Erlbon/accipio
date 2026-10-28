"""Time windows: cron lines underneath, pickers and a sentence on top."""
import datetime

import pytest

from accipio.core import cron

TUE_1014 = datetime.datetime(2026, 10, 27, 10, 14)      # a Tuesday
SAT_1014 = datetime.datetime(2026, 10, 31, 10, 14)


def at(day, hour, minute=0):
    return datetime.datetime(2026, 10, day, hour, minute)   # October 2026: the 26th is a Monday


def test_a_weekday_window_matches_inside_and_not_outside():
    line = ["* 8-15 * * 1-5"]
    assert cron.matches(line, TUE_1014)
    assert not cron.matches(line, SAT_1014)
    assert cron.matches(line, at(27, 15, 59)) and not cron.matches(line, at(27, 16, 0))
    assert not cron.matches(line, at(27, 7, 59))


def test_any_of_several_lines_is_enough():
    lines = ["* 8-15 * * 1-5", "* 10-11 * * sat"]
    assert cron.matches(lines, SAT_1014) and not cron.matches(lines, at(31, 12))


def test_names_steps_lists_and_sunday_as_seven():
    assert cron.parse("*/15 * * * *").minutes == frozenset({0, 15, 30, 45})
    assert cron.parse("5/20 * * * *").minutes == frozenset({5, 25, 45})
    assert cron.parse("0-30/10 * * * *").minutes == frozenset({0, 10, 20, 30})
    assert cron.parse("* * * jan,MAR-may *").months == frozenset({1, 3, 4, 5})
    assert cron.parse("* * * * Mon-Wed").weekdays == frozenset({1, 2, 3})
    assert cron.parse("* * * * 7").weekdays == cron.parse("* * * * 0").weekdays == frozenset({0})
    assert cron.matches(["* * * * 7"], datetime.datetime(2026, 11, 1, 9))   # a Sunday


def test_day_of_month_and_weekday_must_both_match():
    first_monday = ["* * 1-7 * 1"]
    assert cron.matches(first_monday, datetime.datetime(2026, 11, 2, 9))       # Monday the 2nd
    assert not cron.matches(first_monday, datetime.datetime(2026, 11, 9, 9))   # a Monday, but the 9th
    assert not cron.matches(first_monday, datetime.datetime(2026, 11, 3, 9))   # the 3rd, but Tuesday


@pytest.mark.parametrize("line, words", [
    ("* * * *", "five fields"),
    ("60 * * * *", "outside 0-59"),
    ("* 5-2 * * *", "backwards"),
    ("*/0 * * * *", "above 0"),
    ("* * * smarch *", "not a number"),
    ("1,,2 * * * *", "empty item"),
])
def test_bad_lines_are_refused_with_a_message_for_the_editor(line, words):
    with pytest.raises(cron.CronError, match=words):
        cron.parse(line)
    assert words in cron.problems([line, "* * * * *"])[0]
    assert cron.problems(["* * * * *"]) == []


def test_the_picker_builds_one_line_for_whole_hours():
    assert cron.window_lines({1, 2, 3, 4, 5}, datetime.time(8), datetime.time(16)) == ["* 8-15 * * 1-5"]
    assert cron.window_lines(range(7), datetime.time(0), datetime.time(0)) == ["* * * * *"]


def test_the_picker_splits_half_hours_into_lines_that_cover_exactly_the_window():
    lines = cron.window_lines({1}, datetime.time(8, 30), datetime.time(16, 15))
    assert lines == ["30-59 8 * * 1", "* 9-15 * * 1", "0-14 16 * * 1"]
    assert not cron.matches(lines, at(26, 8, 29)) and cron.matches(lines, at(26, 8, 30))
    assert cron.matches(lines, at(26, 16, 14)) and not cron.matches(lines, at(26, 16, 15))
    assert cron.window_lines({1}, datetime.time(9, 10), datetime.time(9, 20)) == ["10-19 9 * * 1"]


def test_a_window_past_midnight_carries_on_into_the_next_day():
    lines = cron.window_lines({5}, datetime.time(22), datetime.time(6))     # Friday night
    assert lines == ["* 22-23 * * 5", "* 0-5 * * 6"]
    assert cron.matches(lines, at(30, 23, 30)) and cron.matches(lines, at(31, 5, 59))
    assert not cron.matches(lines, at(31, 6, 0)) and not cron.matches(lines, at(30, 21, 59))
    assert cron.window_lines({6}, datetime.time(23), datetime.time(1))[1].endswith(" 0")   # Saturday night ends on Sunday
    with pytest.raises(cron.CronError, match="split it in two"):
        cron.window_lines({5}, datetime.time(22), datetime.time(6), days=[1])


def test_the_picker_needs_a_weekday_and_limits_days_and_months():
    with pytest.raises(cron.CronError, match="weekday"):
        cron.window_lines(set(), datetime.time(8), datetime.time(9))
    assert cron.window_lines(range(7), datetime.time(8), datetime.time(9), months=[12, 1, 2], days=[1, 2, 3, 15]) == \
        ["* 8 1-3,15 1-2,12 *"]


@pytest.mark.parametrize("lines, sentence", [
    (["* 8-15 * * 1-5"], "Weekdays, 08:00–16:00"),
    (["* * * * 0,6"], "Weekends, all day"),
    (["* * * * *"], "Any time"),
    ([], "Any time"),
    (["* 9-11 * * 1,3,5"], "Mon, Wed, Fri, 09:00–12:00"),
    (["* * * * 1-4,0"], "Mon–Thu, Sun, all day"),
    (["30-59 8 * * 1", "* 9-15 * * 1", "0-14 16 * * 1"], "Mon, 08:30–16:15"),
    (["* 8-9 1-7 dec *"], "Every day, 08:00–10:00, on day 1–7, in Dec"),
    (["* 22-23 * * 5", "* 0-5 * * 6"], "Fri, 22:00–24:00; Sat, 00:00–06:00"),
    (["*/10 * * * *"], "Cron: */10 * * * *"),
    (["not cron"], "not cron"),
])
def test_lines_read_back_as_a_sentence(lines, sentence):
    assert cron.describe(lines) == sentence


def test_what_the_picker_builds_reads_back_as_the_picker_said():
    lines = cron.window_lines({1, 2, 3, 4, 5}, datetime.time(7, 45), datetime.time(17, 30))
    assert cron.describe(lines) == "Weekdays, 07:45–17:30"
