"""Reading what Chrome and Edge are set to do with a download (2026-11-17, pick B): made-up profile folders and a
made-up policy reader, never this PC's real profile or registry."""
import json
import os

from accipio.core import browser_settings as bs
from accipio.core.browser_settings import CHROME, EDGE, BrowserProfile


def no_policies(browser):
    return {}


def make_browser(root, profiles):
    """profiles: {folder name: (shown name, the "download" section of its Preferences)}"""
    os.makedirs(root)
    with open(os.path.join(root, "Local State"), "w", encoding="utf-8") as f:
        json.dump({"profile": {"info_cache": {folder: {"name": shown} for folder, (shown, _) in profiles.items()}}}, f)
    for folder, (_, download) in profiles.items():
        os.makedirs(os.path.join(root, folder))
        with open(os.path.join(root, folder, "Preferences"), "w", encoding="utf-8") as f:
            json.dump({"download": download, "savefile": {"default_directory": "elsewhere"}}, f)
    return root


def test_each_profile_tells_its_folder_and_whether_it_asks(tmp_path):
    saves_to = str(tmp_path / "Download")
    chrome = make_browser(str(tmp_path / "chrome"), {
        "Default": ("Erlend", {"default_directory": saves_to}),
        "Profile 1": ("Work", {"prompt_for_download": True})})
    found = bs.read([(CHROME, chrome), (EDGE, str(tmp_path / "no-edge"))], no_policies)
    assert found == [BrowserProfile(CHROME, "Erlend", saves_to, False), BrowserProfile(CHROME, "Work", "", True)]
    assert bs.describe(found[0]) == f"Chrome (Erlend): saves to {saves_to} and does not ask where to save."
    assert bs.describe(found[1]) == "Chrome (Work): saves to its usual Downloads folder and asks where to save each file."


def test_a_policy_beats_the_profile_and_is_named(tmp_path):
    chrome = make_browser(str(tmp_path / "chrome"), {"Default": ("Me", {"prompt_for_download": True})})
    forced = str(tmp_path / "Company")
    found = bs.read([(CHROME, chrome)], lambda browser: {"DownloadDirectory": forced, "PromptForDownloadLocation": 0,
                                                         "DownloadRestrictions": 3})
    assert (found[0].folder, found[0].asks) == (forced, False)
    line = bs.describe(found[0])
    assert "Fixed by your organisation: the download folder; that it never asks where to save; " \
           "that all downloads are blocked." in line


def test_unreadable_or_odd_files_mean_not_known_never_an_error(tmp_path):
    root = str(tmp_path / "chrome")
    assert bs.read([(CHROME, root)], no_policies) == []                    # no browser at all
    make_browser(root, {"Default": ("Me", {})})
    with open(os.path.join(root, "Default", "Preferences"), "w", encoding="utf-8") as f:
        f.write("{ not json")
    assert bs.read([(CHROME, root)], no_policies) == [BrowserProfile(CHROME, "Me")]
    with open(os.path.join(root, "Default", "Preferences"), "w", encoding="utf-8") as f:
        json.dump({"download": {"default_directory": 7, "prompt_for_download": "yes"}}, f)
    assert bs.read([(CHROME, root)], no_policies) == [BrowserProfile(CHROME, "Me")]
    with open(os.path.join(root, "Local State"), "w", encoding="utf-8") as f:
        f.write("[]")
    assert bs.read([(CHROME, root)], no_policies) == []

    def broken(browser):
        raise OSError("registry says no")
    make_browser(str(tmp_path / "edge"), {"Default": ("Me", {})})
    assert bs.read([(EDGE, str(tmp_path / "edge"))], broken) == [BrowserProfile(EDGE, "Me")]


def test_a_folder_without_preferences_or_not_listed_is_no_profile(tmp_path):
    root = make_browser(str(tmp_path / "chrome"), {"Default": ("Me", {})})
    os.makedirs(os.path.join(root, "System Profile"))
    with open(os.path.join(root, "System Profile", "Preferences"), "w", encoding="utf-8") as f:
        json.dump({"download": {"prompt_for_download": True}}, f)
    assert [p.name for p in bs.read([(CHROME, root)], no_policies)] == ["Me"]


def test_asks_is_known_only_when_every_profile_of_that_browser_agrees():
    chrome_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"
    edge_ua = chrome_ua + " Edg/157.0.0.0"
    assert bs.browser_of(chrome_ua) == CHROME and bs.browser_of(edge_ua) == EDGE and bs.browser_of("") == ""
    assert bs.browser_of(chrome_ua + " OPR/140.0.0.0") == bs.OPERA      # as a real Opera 140 says it
    opera = [BrowserProfile(CHROME, "a", asks=False), BrowserProfile(bs.OPERA, "o", asks=True),
             BrowserProfile(bs.VIVALDI, "v", asks=True), BrowserProfile(bs.OPERA_GX, "g", asks=False)]
    assert bs.asks(opera, bs.OPERA) is True and bs.asks(opera, CHROME) is False
    assert bs.asks(opera, bs.VIVALDI) is True and bs.asks(opera, bs.OPERA_GX) is False     # named by the native host
    assert bs.asks(opera, bs.BRAVE) is None                               # no such profile here: not known
    profiles = [BrowserProfile(CHROME, "a", asks=True), BrowserProfile(CHROME, "b", asks=True),
                BrowserProfile(EDGE, "c", asks=False)]
    assert bs.asks(profiles, CHROME) is True and bs.asks(profiles, EDGE) is False
    assert bs.asks(profiles, "") is None                                   # which browser is not known
    profiles[1].asks = False
    assert bs.asks(profiles, CHROME) is None                               # the profiles differ: not known
    assert bs.asks([], CHROME) is None


def test_the_real_reader_never_fails_on_this_machine():
    """Whatever is installed here: a list comes back, and the policy reader gives a dict."""
    assert isinstance(bs.windows_policies(CHROME), dict) and bs.windows_policies(bs.OPERA) == {}
    assert {browser for browser, _ in bs.user_data_folders()} >= {CHROME, EDGE, bs.OPERA, bs.BRAVE, bs.VIVALDI}
    assert isinstance(bs.windows_policies(bs.BRAVE), dict) and bs.windows_policies(bs.VIVALDI) == {}


def test_firefox_is_read_from_its_own_files(tmp_path):
    """profiles.ini names the profiles; each one's prefs.js holds only what differs from Firefox's defaults."""
    root = tmp_path / "Firefox"
    for folder in ("Profiles/aaaa.default-release", "Profiles/bbbb.work", "Profiles/cccc.never-used"):
        (root / folder).mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    saves_to = str(tmp_path / "Download")
    (root / "profiles.ini").write_text(
        "[Install308046B0AF4A39CB]\nDefault=Profiles/aaaa.default-release\n\n"
        "[Profile0]\nName=default-release\nIsRelative=1\nPath=Profiles/aaaa.default-release\n\n"
        "[Profile1]\nName=Work\nIsRelative=1\nPath=Profiles/bbbb.work\n\n"
        "[Profile2]\nName=Never used\nIsRelative=1\nPath=Profiles/cccc.never-used\n\n"
        f"[Profile3]\nName=Elsewhere\nIsRelative=0\nPath={elsewhere}\n\n"
        "[General]\nStartWithLastProfile=1\n", encoding="utf-8")
    (root / "Profiles/aaaa.default-release/prefs.js").write_text(
        '// Mozilla User Preferences\n'
        'user_pref("browser.download.folderList", 2);\n'
        f'user_pref("browser.download.dir", {json.dumps(saves_to)});\n'
        'user_pref("browser.download.lastDir", "C:\\\\somewhere");\n'
        'user_pref("app.update.lastUpdateTime.x", 1794300000);\n', encoding="utf-8")
    (root / "Profiles/bbbb.work/prefs.js").write_text(
        'user_pref("browser.download.useDownloadDir", false);\n'
        'user_pref("browser.download.dir", "C:\\\\ignored: folderList is not 2");\n', encoding="utf-8")
    (elsewhere / "prefs.js").write_text("not a prefs file at all\n", encoding="utf-8")
    found = bs.read([(bs.FIREFOX, str(root))], no_policies)
    assert found == [BrowserProfile(bs.FIREFOX, "default-release", saves_to, False),
                     BrowserProfile(bs.FIREFOX, "Work", "", True), BrowserProfile(bs.FIREFOX, "Elsewhere")]
    assert bs.asks(found, bs.FIREFOX) is None and bs.asks(found[1:2], bs.FIREFOX) is True
    forced = bs.read([(bs.FIREFOX, str(root))], lambda browser: {"PromptForDownloadLocation": 1})
    assert all(p.asks for p in forced) and "asks where to save" in bs.describe(forced[0])
    assert bs.read([(bs.FIREFOX, str(tmp_path / "no-firefox"))], no_policies) == []
    (root / "profiles.ini").write_text("this is [not an ini\n", encoding="utf-8")
    assert bs.read([(bs.FIREFOX, str(root))], no_policies) == []


def test_opera_keeps_the_same_files_and_is_read_the_same_way(tmp_path):
    opera = make_browser(str(tmp_path / "Opera Stable"), {"Default": ("Me", {"prompt_for_download": True})})
    found = bs.read([(bs.OPERA, opera)], no_policies)
    assert found == [BrowserProfile(bs.OPERA, "Me", "", True)]
    assert bs.describe(found[0]).startswith("Opera (Me): ")
