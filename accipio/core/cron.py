"""Crontab-style time windows for rule conditions.

A rule's time condition is a list of five-field cron lines (minute hour day-of-month month weekday). The
condition matches when the moment a download starts fits ANY of the lines. Nothing is scheduled from these
lines: the only question asked is "does this moment match?", so this is a small matcher, not a scheduler.

Field syntax is the usual one: ``*``, numbers, ranges ``a-b``, lists ``a,b``, steps ``*/n``, ``a-b/n`` and
``a/n`` (from a to the end), and English names for months (jan-dec) and weekdays (sun-sat). Weekday 0 and 7
are both Sunday.

One deliberate difference from classic cron: all five fields must match. Classic cron matches a day when
EITHER the day of month OR the weekday fits once both are restricted, so ``* * 1-7 * 1`` there means "the
1st to the 7th, and every Monday". Here it means "a Monday from the 1st to the 7th", which is what the
editor's pickers say and what people expect.

People rarely type cron: the editor builds lines from pickers (``window_lines``) and reads them back as a
sentence (``describe``). The raw lines are only shown in the Advanced field.
"""
from __future__ import annotations

import datetime
import functools
from dataclasses import dataclass
from typing import Iterable, List, Optional

MONTH_NAMES = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
DAY_NAMES = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
DAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

MINUTES_PER_DAY = 24 * 60


class CronError(ValueError):
    """A cron line that cannot be read, with a message fit to show in the editor."""


@dataclass(frozen=True)
class CronLine:
    text: str
    minutes: frozenset
    hours: frozenset
    days: frozenset        # day of month, 1-31
    months: frozenset      # 1-12
    weekdays: frozenset    # 0 = Sunday ... 6 = Saturday

    def matches(self, moment: datetime.datetime) -> bool:
        return (moment.minute in self.minutes and moment.hour in self.hours and moment.day in self.days
                and moment.month in self.months and moment.isoweekday() % 7 in self.weekdays)


def _value(text: str, name: str, lo: int, hi: int, names: Optional[List[str]]) -> int:
    word = text.lower()
    if names and word in names:
        return names.index(word) + (1 if names is MONTH_NAMES else 0)
    if not text.isdigit():
        raise CronError(f"{text!r} is not a number in the {name} field")
    number = int(text)
    if not lo <= number <= hi:
        raise CronError(f"{number} is outside {lo}-{hi} in the {name} field")
    return number


def _field(part: str, name: str, lo: int, hi: int, names: Optional[List[str]] = None) -> frozenset:
    values = set()
    for item in part.split(","):
        if not item:
            raise CronError(f"empty item in the {name} field: {part!r}")
        span, slash, step_text = item.partition("/")
        step = 1
        if slash:
            if not step_text.isdigit() or int(step_text) == 0:
                raise CronError(f"the step in {item!r} ({name} field) must be a whole number above 0")
            step = int(step_text)
        if span == "*":
            start, end = lo, hi
        else:
            first, dash, last = span.partition("-")
            start = _value(first, name, lo, hi, names)
            end = _value(last, name, lo, hi, names) if dash else (hi if slash else start)
            if start > end:
                raise CronError(f"the range {item!r} runs backwards in the {name} field")
        values.update(range(start, end + 1, step))
    return frozenset(values)


@functools.lru_cache(maxsize=512)
def parse(text: str) -> CronLine:
    parts = text.split()
    if len(parts) != 5:
        raise CronError(f"a cron line has five fields (minute hour day month weekday), this one has {len(parts)}: {text!r}")
    minutes = _field(parts[0], "minute", 0, 59)
    hours = _field(parts[1], "hour", 0, 23)
    days = _field(parts[2], "day of month", 1, 31)
    months = _field(parts[3], "month", 1, 12, MONTH_NAMES)
    weekdays = frozenset(d % 7 for d in _field(parts[4], "weekday", 0, 7, DAY_NAMES))
    return CronLine(" ".join(parts), minutes, hours, days, months, weekdays)


def matches(lines: Iterable[str], moment: datetime.datetime) -> bool:
    """True when the moment fits any of the lines."""
    return any(parse(line).matches(moment) for line in lines)


def problems(lines: Iterable[str]) -> List[str]:
    """What is wrong with the lines, for the editor; empty when all of them read."""
    found = []
    for line in lines:
        try:
            parse(line)
        except CronError as error:
            found.append(str(error))
    return found


# ---- building lines from the editor's pickers -------------------------------------------------------------

def _ranges(values: Iterable[int]) -> List[tuple]:
    ordered = sorted(set(values))
    runs: List[tuple] = []
    for value in ordered:
        if runs and value == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], value)
        else:
            runs.append((value, value))
    return runs


def _compress(values: Iterable[int], lo: int, hi: int) -> str:
    values = set(values)
    if values >= set(range(lo, hi + 1)):
        return "*"
    return ",".join(str(a) if a == b else f"{a}-{b}" for a, b in _ranges(values))


def _segments(start: int, end: int) -> List[tuple]:
    """Minutes-of-day [start, end) as (minute field, hour field) pairs, 0 <= start < end <= 1440."""
    first_hour, first_minute = divmod(start, 60)
    last_hour, last_minute = divmod(end, 60)
    if first_hour == last_hour:
        return [(_compress(range(first_minute, last_minute), 0, 59), str(first_hour))]
    out = []
    if first_minute:
        out.append((_compress(range(first_minute, 60), 0, 59), str(first_hour)))
        first_hour += 1
    if first_hour < last_hour:
        out.append(("*", _compress(range(first_hour, last_hour), 0, 23)))
    if last_minute:
        out.append((_compress(range(0, last_minute), 0, 59), str(last_hour)))
    return out


def window_lines(weekdays: Iterable[int], start: datetime.time, end: datetime.time,
                 months: Optional[Iterable[int]] = None, days: Optional[Iterable[int]] = None) -> List[str]:
    """Cron lines for "these weekdays (0 = Sunday), from start to end", as the time picker sets it.

    ``end`` is not included: 08:00-16:00 ends at 15:59. ``start == end`` means the whole day. A window that
    ends before it starts runs past midnight, and its part after midnight belongs to the NEXT day
    (Friday 22:00-06:00 covers Saturday 00:00-05:59). Such a window cannot also be limited to days of the
    month; split it into two windows instead.
    """
    weekdays = {d % 7 for d in weekdays}
    if not weekdays:
        raise CronError("pick at least one weekday")
    day_field = _compress(days, 1, 31) if days else "*"
    month_field = _compress(months, 1, 12) if months else "*"
    first = start.hour * 60 + start.minute
    last = end.hour * 60 + end.minute
    if first == last:
        parts = [(0, MINUTES_PER_DAY, weekdays)]
    elif first < last:
        parts = [(first, last, weekdays)]
    else:
        if days:
            raise CronError("a window past midnight cannot be limited to days of the month; split it in two")
        parts = [(first, MINUTES_PER_DAY, weekdays), (0, last, {(d + 1) % 7 for d in weekdays})]
    lines = []
    for lo, hi, these_days in parts:
        if hi <= lo:
            continue
        weekday_field = _compress(these_days, 0, 6)
        for minute_field, hour_field in _segments(lo, hi):
            lines.append(f"{minute_field} {hour_field} {day_field} {month_field} {weekday_field}")
    return lines


# ---- reading lines back as a sentence ---------------------------------------------------------------------

def _clock(minute_of_day: int) -> str:
    return "%02d:%02d" % divmod(minute_of_day, 60)


def _day_phrase(weekdays: frozenset) -> str:
    if len(weekdays) == 7:
        return "Every day"
    if weekdays == frozenset(range(1, 6)):
        return "Weekdays"
    if weekdays == frozenset({0, 6}):
        return "Weekends"
    # Monday first, as on the picker; Sunday last
    order = [1, 2, 3, 4, 5, 6, 0]
    positions = [order.index(d) for d in weekdays]
    labels = []
    for a, b in _ranges(positions):
        labels.append(DAY_LABELS[order[a]] if a == b else f"{DAY_LABELS[order[a]]}–{DAY_LABELS[order[b]]}")
    return ", ".join(labels)


def _list_phrase(values: frozenset, labels: Optional[List[str]] = None, offset: int = 0) -> str:
    def show(v: int) -> str:
        return labels[v - offset] if labels else str(v)
    return ", ".join(show(a) if a == b else f"{show(a)}–{show(b)}" for a, b in _ranges(values))


def describe(lines: Iterable[str]) -> str:
    """A sentence for the lines ("Weekdays, 08:00–16:00"), or the raw text when no plain sentence fits."""
    lines = list(lines)
    if not lines:
        return "Any time"
    try:
        parsed = [parse(line) for line in lines]
    except CronError:
        return "; ".join(lines)
    groups: dict = {}
    for line in parsed:
        key = (line.weekdays, line.days, line.months)
        covered = groups.setdefault(key, set())
        covered.update(h * 60 + m for h in line.hours for m in line.minutes)
    sentences = []
    for (weekdays, days, months), covered in groups.items():
        spans = [(a, b + 1) for a, b in _ranges(covered)]
        if len(spans) > 3:
            return "Cron: " + "; ".join(lines)
        if spans == [(0, MINUTES_PER_DAY)]:
            when = "all day"
        else:
            when = ", ".join(f"{_clock(a)}–{_clock(b)}" for a, b in spans)
        sentence = f"{_day_phrase(weekdays)}, {when}"
        if len(days) < 31:
            sentence += f", on day {_list_phrase(days)}"
        if len(months) < 12:
            sentence += f", in {_list_phrase(months, MONTH_LABELS, 1)}"
        sentences.append(sentence)
    text = "; ".join(sentences)
    return "Any time" if text == "Every day, all day" else text
