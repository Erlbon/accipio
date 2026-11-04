"""Updating yt-dlp: reading PyPI's answer, installing a wheel beside the bundled copy, and picking it up at start.
No test reaches PyPI: ``fetch`` is a fake that serves wheels built here."""
import hashlib
import io
import json
import os
import sys
import threading
import zipfile

import pytest

from accipio.core import ytdlp_update as up


def wheel(files):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return data.getvalue()


def ytdlp_wheel(version):
    return wheel({"yt_dlp/__init__.py": f"__version__ = {version!r}\n",
                  "yt_dlp/version.py": f"__version__ = {version!r}\n",
                  f"yt_dlp-{version}.dist-info/METADATA": "Name: yt-dlp\n"})


EJS = wheel({"yt_dlp_ejs/__init__.py": "", "yt_dlp_ejs/yt/solver/core.min.js": "//js\n"})


def pypi_answer(name, version, blob, requires=()):
    return json.dumps({
        "info": {"version": version, "requires_dist": list(requires)},
        "urls": [{"packagetype": "sdist", "filename": f"{name}-{version}.tar.gz", "url": "https://x/sdist",
                  "digests": {"sha256": "0"}},
                 {"packagetype": "bdist_wheel", "filename": f"{name}-{version}-py3-none-any.whl",
                  "url": f"https://files/{name}-{version}.whl", "digests": {"sha256": hashlib.sha256(blob).hexdigest()}}],
    }).encode()


class FakePyPI:
    def __init__(self, version="2026.10.30", ejs="0.9.0", blob=None):
        self.blob = blob if blob is not None else ytdlp_wheel(version)
        self.pages = {
            up.PYPI_JSON.format(name="yt-dlp"): pypi_answer(
                "yt_dlp", version, ytdlp_wheel(version), [f"yt-dlp-ejs=={ejs}; extra == 'default'", "pycryptodomex; extra == 'default'"]),
            up.PYPI_VERSION_JSON.format(name="yt-dlp-ejs", version=ejs): pypi_answer("yt_dlp_ejs", ejs, EJS),
            f"https://files/yt_dlp-{version}.whl": self.blob,
            f"https://files/yt_dlp_ejs-{ejs}.whl": EJS,
        }
        self.asked = []

    def __call__(self, url):
        self.asked.append(url)
        return self.pages[url]


@pytest.fixture
def clean_path():
    saved = list(sys.path)
    yield
    sys.path[:] = saved


def test_versions_compare_as_numbers():
    assert up.version_key("2026.08.19") == up.version_key("2026.8.19")
    assert up.version_key("2026.10.30") > up.version_key("2026.9.1")
    assert up.version_key("2026.10.30.232415") > up.version_key("2026.10.30")
    assert up.version_key(None) < up.version_key("2020.1.1")


def test_the_newest_release_and_the_ejs_it_pins():
    release = up.latest_release(FakePyPI("2026.10.30", ejs="0.9.0"))
    assert release.version == "2026.10.30"
    assert release.wheels[0].url == "https://files/yt_dlp-2026.10.30.whl"
    assert [w.url for w in release.wheels] == ["https://files/yt_dlp-2026.10.30.whl", "https://files/yt_dlp_ejs-0.9.0.whl"]


def test_installing_unpacks_both_wheels_and_switches(tmp_path):
    pypi = FakePyPI("2026.10.30")
    up.install(str(tmp_path), up.latest_release(pypi), fetch=pypi)
    target = tmp_path / "yt-dlp" / "2026.10.30"
    assert (target / "yt_dlp" / "version.py").is_file()
    assert (target / "yt_dlp_ejs" / "yt" / "solver" / "core.min.js").is_file()
    assert up.installed_version(str(tmp_path)) == "2026.10.30"


def test_a_wrong_checksum_installs_nothing(tmp_path):
    pypi = FakePyPI("2026.10.30")
    pypi.pages["https://files/yt_dlp-2026.10.30.whl"] = ytdlp_wheel("tampered")
    with pytest.raises(up.UpdateError, match="checksum"):
        up.install(str(tmp_path), up.latest_release(pypi), fetch=pypi)
    assert up.installed_version(str(tmp_path)) is None
    assert not (tmp_path / "yt-dlp" / "2026.10.30").exists()


def test_a_wheel_reaching_outside_its_folder_is_refused(tmp_path):
    bad = wheel({"yt_dlp/__init__.py": "", "../../evil.py": "boom"})
    pypi = FakePyPI("2026.10.30")
    pypi.pages["https://files/yt_dlp-2026.10.30.whl"] = bad
    pypi.pages[up.PYPI_JSON.format(name="yt-dlp")] = pypi_answer("yt_dlp", "2026.10.30", bad, ["yt-dlp-ejs==0.9.0"])
    with pytest.raises(up.UpdateError):
        up.install(str(tmp_path), up.latest_release(pypi), fetch=pypi)
    assert not (tmp_path.parent / "evil.py").exists() and up.installed_version(str(tmp_path)) is None


def test_cancelling_before_the_switch_keeps_the_old_one(tmp_path):
    pypi = FakePyPI("2026.10.30")
    stop = threading.Event()
    stop.set()
    with pytest.raises(up.Cancelled):
        up.install(str(tmp_path), up.latest_release(pypi), fetch=pypi, cancel=stop)
    assert up.installed_version(str(tmp_path)) is None


def test_a_newer_update_goes_ahead_of_the_bundled_copy(tmp_path, clean_path):
    pypi = FakePyPI("2026.10.30")
    up.install(str(tmp_path), up.latest_release(pypi), fetch=pypi)
    assert up.activate(str(tmp_path), bundled="2026.8.19") == "2026.10.30"
    assert sys.path[0] == str(tmp_path / "yt-dlp" / "2026.10.30")


def test_an_older_update_is_ignored_and_tidied_away(tmp_path, clean_path):
    """A new Accipio build may bundle a newer yt-dlp than the one updated earlier: the bundled one wins."""
    pypi = FakePyPI("2026.9.1")
    up.install(str(tmp_path), up.latest_release(pypi), fetch=pypi)
    before = list(sys.path)
    assert up.activate(str(tmp_path), bundled="2026.10.30") is None
    assert sys.path == before
    assert up.installed_version(str(tmp_path)) is None and not (tmp_path / "yt-dlp" / "2026.9.1").exists()


def test_a_broken_or_missing_update_never_stops_the_start(tmp_path, clean_path):
    before = list(sys.path)
    assert up.activate(str(tmp_path), bundled="2026.8.19") is None
    os.makedirs(tmp_path / "yt-dlp")
    (tmp_path / "yt-dlp" / "current.txt").write_text("2026.10.30")      # names a folder that is not there
    assert up.activate(str(tmp_path), bundled="2026.8.19") is None
    assert sys.path == before


def test_installing_again_removes_the_older_update(tmp_path):
    for version in ("2026.10.30", "2026.11.2"):
        pypi = FakePyPI(version)
        up.install(str(tmp_path), up.latest_release(pypi), fetch=pypi)
    assert sorted(os.listdir(tmp_path / "yt-dlp")) == ["2026.11.2", "current.txt"]


def test_check_says_up_to_date_without_downloading(tmp_path):
    pypi = FakePyPI("2026.8.19")
    result = up.update(str(tmp_path), running="2026.08.19", fetch=pypi)
    assert result.installed is False and result.latest == "2026.8.19"
    assert all("/json" in url for url in pypi.asked)


def test_check_installs_a_newer_release(tmp_path):
    pypi = FakePyPI("2026.10.30")
    result = up.update(str(tmp_path), running="2026.8.19", fetch=pypi)
    assert result.installed is True and result.latest == "2026.10.30"
    assert up.installed_version(str(tmp_path)) == "2026.10.30"


def test_the_yt_dlp_in_use_is_written_down(tmp_path, clean_path):
    """One line, rewritten at each start: which yt-dlp Python really loaded, and from where."""
    up.note_in_use(str(tmp_path))
    text = (tmp_path / "yt-dlp" / up.IN_USE).read_text(encoding="utf-8")
    import yt_dlp.version
    assert text.startswith(f"yt-dlp {yt_dlp.version.__version__} from ") and os.path.dirname(yt_dlp.__file__) in text


def test_a_folder_held_for_a_moment_is_moved_in_after_a_retry(tmp_path, monkeypatch):
    """A virus scanner holding a freshly unpacked file made Windows refuse the move (seen in a test run)."""
    work, target = tmp_path / "work", tmp_path / "target"
    work.mkdir()
    (work / "a.txt").write_text("x")
    real_replace, refusals, waits = os.replace, [2], []

    def held(src, dst):
        if refusals[0]:
            refusals[0] -= 1
            raise PermissionError(5, "Access is denied")
        real_replace(src, dst)
    monkeypatch.setattr(up.os, "replace", held)
    up._swap_in(str(work), str(target), sleep=waits.append)
    assert (target / "a.txt").is_file() and len(waits) == 2


def test_a_folder_held_for_good_still_fails_in_the_end(tmp_path, monkeypatch):
    def never(src, dst):
        raise PermissionError(5, "Access is denied")
    monkeypatch.setattr(up.os, "replace", never)
    with pytest.raises(PermissionError):
        up._swap_in(str(tmp_path / "w"), str(tmp_path / "t"), sleep=lambda s: None)
