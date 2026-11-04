"""A sorting setup for the tests: one folder and one rule per kind of file, the way someone might set Accipio up.

Until 2026-11-04#04 this was what a new installation started with; now a new setup has only the Default folder and
no rules, and these live here so the tests still have a realistic rule list to walk.
"""
from accipio.core import rules
from accipio.core.rules import Condition, Folder, Library, Rule

FOLDERS = [("Video sites", "%default%/Video/{uploader}"), ("Video", "%default%/Video"), ("Audio", "%default%/Audio"),
           ("Images", "%default%/Images"), ("Books", "%default%/Books"), ("Comics", "%default%/Comics"),
           ("Documents", "%default%/Documents"), ("Archives", "%default%/Archives"),
           ("Programs", "%default%/Programs"), ("Other", "%default%/Other")]


def sorting_library() -> Library:
    return Library([Folder(n, p) for n, p in FOLDERS], rules.default_groups(), [])


def sorting_rules():
    def by_type(name, groups, folder):
        return Rule(name, [Condition("type", groups)], target=folder)
    return [
        Rule("Video sites", [Condition("source", ["video"])], target="Video sites"),
        by_type("Video", ["Video"], "Video"),
        by_type("Audio", ["Audio"], "Audio"),
        by_type("Images", ["Images"], "Images"),
        by_type("Ebooks", ["Ebooks"], "Books"),
        by_type("Comics", ["Comics"], "Comics"),
        by_type("Documents", ["Documents"], "Documents"),
        by_type("Archives", ["Archives"], "Archives"),
        by_type("Programs", ["Programs"], "Programs"),
        Rule("Other", target="Other"),
    ]
