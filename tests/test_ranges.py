"""Link patterns that stand for many links (2026-11-05, picked 29A)."""
import pytest

from accipio.core import ranges


@pytest.mark.parametrize("pattern, first, last, total", [
    ("https://x.org/page[1-20].jpg", "https://x.org/page1.jpg", "https://x.org/page20.jpg", 20),
    ("https://x.org/img[001-150].png", "https://x.org/img001.png", "https://x.org/img150.png", 150),
    ("https://x.org/v[1-20:5].zip", "https://x.org/v1.zip", "https://x.org/v16.zip", 4),
    ("https://x.org/[a-e].pdf", "https://x.org/a.pdf", "https://x.org/e.pdf", 5),
    ("https://x.org/[A-C].pdf", "https://x.org/A.pdf", "https://x.org/C.pdf", 3),
    ("https://x.org/{intro,main,outro}.mp3", "https://x.org/intro.mp3", "https://x.org/outro.mp3", 3),
    ("https://x.org/n[10-8].txt", "https://x.org/n10.txt", "https://x.org/n8.txt", 3),
])
def test_each_kind_of_piece(pattern, first, last, total):
    links = ranges.expand(pattern)
    assert ranges.has_pattern(pattern) and ranges.count(pattern) == total == len(links)
    assert (links[0], links[-1]) == (first, last)


def test_pieces_combine_with_the_leftmost_changing_slowest():
    links = ranges.expand("https://x.org/s[1-2]/e[01-03].mkv")
    assert links == ["https://x.org/s1/e01.mkv", "https://x.org/s1/e02.mkv", "https://x.org/s1/e03.mkv",
                     "https://x.org/s2/e01.mkv", "https://x.org/s2/e02.mkv", "https://x.org/s2/e03.mkv"]
    assert ranges.count("https://x.org/[1-1000]/[1-1000].jpg") == 1_000_000           # counted, not made


def test_zero_padding_follows_the_first_number():
    assert ranges.expand("https://x.org/[08-11]")[:2] == ["https://x.org/08", "https://x.org/09"]
    assert ranges.expand("https://x.org/[8-11]")[:2] == ["https://x.org/8", "https://x.org/9"]


@pytest.mark.parametrize("link", [
    "http://[::1]:8080/file.zip",                  # an IPv6 host
    "https://x.org/get?ids[]=4&ids[]=5",           # a PHP-style query
    "https://x.org/{nothing}.txt",                 # braces without a comma
    "https://x.org/[1-a].txt",                     # mixed range
    "https://x.org/plain.zip",
])
def test_ordinary_links_stay_one_link(link):
    assert not ranges.has_pattern(link) and ranges.expand(link) == [link] and ranges.count(link) == 1


def test_a_step_of_zero_is_refused():
    with pytest.raises(ranges.PatternError):
        ranges.expand("https://x.org/[1-5:0].jpg")
