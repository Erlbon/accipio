"""What the taskbar button shows: progress colour, percentage, the count badge and the window title."""
from accipio.core import taskbar
from accipio.core.store import ACTIVE, COMPLETE, ERROR, PAUSED, PROBING, Row


def row(i, state=ACTIVE, done=0, total=0):
    return Row(id=i, url=f"https://x.org/{i}.bin", state=state, done=done, total=total)


def test_nothing_running_shows_a_plain_button():
    look = taskbar.look([row(1, COMPLETE, 10, 10)], failed=0)
    assert look == taskbar.Look(taskbar.NONE, 0, 0, "Accipio")


def test_running_downloads_fill_green_with_the_share_of_all_bytes():
    look = taskbar.look([row(1, ACTIVE, 30, 100), row(2, ACTIVE, 60, 100), row(3, PROBING)], failed=0)
    assert look.mode == taskbar.NORMAL
    assert look.percent == 45
    assert look.count == 3
    assert look.title == "Accipio – 3 downloading, 45%"


def test_paused_ones_count_toward_the_bar_while_others_run():
    look = taskbar.look([row(1, ACTIVE, 50, 100), row(2, PAUSED, 0, 100)], failed=0)
    assert (look.mode, look.percent, look.count) == (taskbar.NORMAL, 25, 1)
    assert look.title == "Accipio – 1 downloading, 1 paused, 25%"


def test_all_paused_turns_the_bar_yellow_and_keeps_its_place():
    look = taskbar.look([row(1, PAUSED, 45, 100), row(2, PAUSED, 0, 0)], failed=0)
    assert (look.mode, look.percent, look.count) == (taskbar.PAUSED, 45, 2)
    assert look.title == "Accipio – 2 paused, 45%"


def test_an_unread_failure_turns_it_red():
    look = taskbar.look([row(1, ACTIVE, 60, 100), row(2, ACTIVE, 60, 100), row(3, ERROR)], failed=1)
    assert (look.mode, look.percent, look.count) == (taskbar.ERROR, 60, 2)
    assert look.title == "Accipio – 2 downloading, 1 failed, 60%"


def test_a_failure_with_nothing_else_running_fills_the_bar_red():
    look = taskbar.look([row(1, ERROR)], failed=1)
    assert (look.mode, look.percent, look.count) == (taskbar.ERROR, 100, 0)
    assert look.title == "Accipio – 1 failed"


def test_unknown_sizes_slide_and_say_no_percentage():
    look = taskbar.look([row(1, ACTIVE, 5000, 0)], failed=0)
    assert (look.mode, look.count) == (taskbar.SLIDING, 1)
    assert look.title == "Accipio – 1 downloading"


def test_a_known_size_wins_over_an_unknown_one():
    look = taskbar.look([row(1, ACTIVE, 5000, 0), row(2, ACTIVE, 20, 100)], failed=0)
    assert (look.mode, look.percent) == (taskbar.NORMAL, 20)


def test_the_badge_text_stops_at_nine():
    assert taskbar.badge_text(0) == ""
    assert taskbar.badge_text(3) == "3"
    assert taskbar.badge_text(12) == "9+"


def test_failures_count_only_while_nobody_is_looking():
    unread = taskbar.UnreadFailures()
    unread.observe([row(1, ACTIVE)], looking=False)
    unread.observe([row(1, ERROR)], looking=False)
    assert unread.count == 1
    unread.observe([row(1, ERROR)], looking=True)                 # the window came to the front
    assert unread.count == 0
    unread.observe([row(1, ERROR)], looking=False)                # an old failure is not news again
    assert unread.count == 0


def test_a_failure_seen_as_it_happens_is_never_unread():
    unread = taskbar.UnreadFailures()
    unread.observe([row(1, ACTIVE)], looking=True)
    unread.observe([row(1, ERROR)], looking=True)
    unread.observe([row(1, ERROR)], looking=False)
    assert unread.count == 0


def test_a_removed_or_retried_row_stops_counting():
    unread = taskbar.UnreadFailures()
    unread.observe([row(1, ACTIVE), row(2, ACTIVE)], looking=False)
    unread.observe([row(1, ERROR), row(2, ERROR)], looking=False)
    unread.observe([row(2, ACTIVE)], looking=False)               # 1 removed, 2 retried
    assert unread.count == 0


def test_failures_already_there_at_start_are_not_news():
    unread = taskbar.UnreadFailures()
    unread.observe([row(1, ERROR)], looking=False)
    assert unread.count == 0
