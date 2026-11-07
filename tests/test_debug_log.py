"""The debug log (2026-11-07, 72B): off unless switched on in Settings; one line per thing Accipio saw or decided,
so an odd case (a getcomics.org link that ended up in Chrome's own downloader, an archive that asked although a rule
seemed to say what to do) can be read instead of guessed. Cookies and other header values are never written."""
import os

from accipio import native_host
from accipio.core import debug_log


def test_nothing_is_written_while_it_is_off(tmp_path):
    log = debug_log.DebugLog()
    log.note("add", url="https://a.org/f.zip")
    log.configure(str(tmp_path), on=False)
    log.note("add", url="https://a.org/f.zip")
    assert not (tmp_path / debug_log.FILE).exists()


def test_a_line_per_note_with_time_event_and_fields(tmp_path):
    log = debug_log.DebugLog()
    log.configure(str(tmp_path), on=True)
    log.note("link check", url="https://getcomics.org/dlds/x", seconds=8.25, size=None, ok=False,
             error="took too long")
    line = (tmp_path / debug_log.FILE).read_text(encoding="utf-8").splitlines()[-1]
    assert "  link check  " in line and 'url="https://getcomics.org/dlds/x"' in line
    assert "seconds=8.25" in line and "size=None" in line and 'error="took too long"' in line


def test_cookies_and_header_values_never_reach_the_log(tmp_path):
    log = debug_log.DebugLog()
    log.configure(str(tmp_path), on=True)
    log.note("from browser", cookies="session=SECRET123", headers={"Cookie": "SECRET456", "Referer": "https://p.org/"},
             authorization="Bearer SECRET789")
    text = (tmp_path / debug_log.FILE).read_text(encoding="utf-8")
    for secret in ("SECRET123", "SECRET456", "SECRET789"):
        assert secret not in text
    assert "cookies=<17 characters>" in text and "Referer" in text


def test_it_keeps_its_size_with_one_older_file(tmp_path, monkeypatch):
    monkeypatch.setattr(debug_log, "MAX_BYTES", 2000)
    log = debug_log.DebugLog()
    log.configure(str(tmp_path), on=True)
    for n in range(200):
        log.note("tick", n=n)
    assert os.path.getsize(tmp_path / debug_log.FILE) <= 2000 + 200
    assert (tmp_path / (debug_log.FILE + ".1")).is_file()
    assert not (tmp_path / (debug_log.FILE + ".2")).exists()


def test_the_report_holds_the_version_and_the_last_lines_and_delete_removes_both_files(tmp_path, monkeypatch):
    monkeypatch.setattr(debug_log, "MAX_BYTES", 2000)
    log = debug_log.DebugLog()
    log.configure(str(tmp_path), on=True)
    for n in range(200):
        log.note("tick", n=n)
    report = log.report(lines=5, header="Accipio 2026-11-07#06")
    assert report.splitlines()[0] == "Accipio 2026-11-07#06" and "n=199" in report and "n=150" not in report
    log.delete()
    assert not (tmp_path / debug_log.FILE).exists() and not (tmp_path / (debug_log.FILE + ".1")).exists()
    assert log.report(header="x") == "x\n(the debug log is empty)"


def test_a_note_from_the_extension_never_starts_accipio(tmp_path):
    """The extension tells Accipio about downloads it left to Chrome before asking; with Accipio closed that note
    is simply dropped, like a ping."""
    started = []
    answer = native_host.relay({"type": "seen", "reason": "takeover is off"}, str(tmp_path),
                               launch=lambda: started.append(1), wait=0.1, sleep=lambda s: None)
    assert started == [] and answer["ok"] is False
