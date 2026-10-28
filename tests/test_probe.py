"""The probe: the real file name, type and size before a download starts."""
import pytest

from accipio.core import probe
from tests.conftest import PAYLOAD


def test_a_plain_file_link(web):
    p = probe.probe(web + "/files/payload.bin")
    assert (p.filename, p.size, p.resumable) == ("payload.bin", len(PAYLOAD), True)
    assert p.mime == "application/octet-stream"


def test_the_servers_content_disposition_wins(web):
    assert probe.probe(web + "/cd?id=7").filename == "report final.pdf"
    assert probe.probe(web + "/cd-star").filename == "æøå.epub"


def test_a_name_with_folders_in_it_is_cut_to_the_name(web):
    assert probe.probe(web + "/cd-path").filename == "evil.exe"


def test_redirects_are_followed_to_the_real_name(web):
    p = probe.probe(web + "/redirect")
    assert p.filename == "real.zip" and p.url.endswith("/files/real.zip")


def test_a_script_link_gets_the_extension_of_what_it_serves(web):
    assert probe.probe(web + "/get.php?id=7").filename == "get.zip"
    assert probe.probe(web + "/folder/").filename == "download.exe"


def test_a_server_that_refuses_head_is_asked_for_one_byte(web):
    p = probe.probe(web + "/nohead")
    assert (p.filename, p.mime, p.size, p.resumable) == ("nohead.mp4", "video/mp4", 1234, True)


def test_a_missing_file_and_an_unreachable_server_say_so(web):
    with pytest.raises(probe.ProbeError, match="404"):
        probe.probe(web + "/nothing")
    with pytest.raises(probe.ProbeError, match="could not reach"):
        probe.probe("http://127.0.0.1:9/x", timeout=2)


@pytest.mark.parametrize("disposition, url, mime, name", [
    ("", "https://x.org/a/Report%202024.pdf", "application/pdf", "Report 2024.pdf"),
    ("", "https://x.org/report.2024", "application/pdf", "report.2024.pdf"),
    ("", "https://x.org/file.bin", "application/zip", "file.bin.zip"),
    ("", "https://x.org/movie.mkv", "application/octet-stream", "movie.mkv"),
    ("attachment; filename=data.php", "https://x.org/x", "application/zip", "data.php"),   # the server named it
    ("", "https://x.org/", "", "download"),
    ("", "https://x.org/con", "", "_con"),
])
def test_choose_filename(disposition, url, mime, name):
    assert probe.choose_filename(disposition, url, mime) == name
