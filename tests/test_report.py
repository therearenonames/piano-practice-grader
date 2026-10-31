"""The text report: every kind of line shows up when it should."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import make_sheet  # noqa: E402

from grader import grade  # noqa: E402
from recorder import PlayedNote  # noqa: E402
from report import format_report  # noqa: E402


def two_bars(bpm=60):
    return make_sheet([(k, 1, [60 + k]) for k in range(8)], bpm=bpm)


class TestReport(unittest.TestCase):
    def test_clean_run(self):
        s = two_bars()
        text = format_report(s, grade(s, [PlayedNote(60 + k, k, 0.8) for k in range(8)]))
        self.assertIn("Bar 1", text)
        self.assertIn("Bar 2", text)
        self.assertIn("Correct notes: 8/8 (100%)", text)
        self.assertIn("No sections stood out", text)
        self.assertIn("first note", text)
        self.assertNotIn("WRONG", text)

    def test_every_kind_of_mistake(self):
        s = two_bars()
        played = [PlayedNote(60, 0, 0.8), PlayedNote(62, 1, 0.8),          # 61 -> 62: wrong
                  PlayedNote(62, 2, 0.8), PlayedNote(90, 2.5, 0.1),        # stray 90
                  PlayedNote(64, 4, 0.8), PlayedNote(65, 5, 0.1),          # 63 missed; 65 short
                  PlayedNote(66, 6, 0.8), PlayedNote(67, 7.4, 0.8)]        # 67 late
        text = format_report(s, grade(s, played))
        for expected in ["WRONG NOTE", "MISSED", "EXTRA note", "held too short", "late by", "F#6"]:
            self.assertIn(expected, text)
        self.assertIn("Wrong: 1   Missed: 1   Extra: 1", text)
        self.assertIn("Bar 2   ~56 BPM (steady)", text)  # one late note doesn't make the bar "dragged"

    def test_stopped_early(self):
        s = two_bars()
        text = format_report(s, grade(s, [PlayedNote(60 + k, k, 0.8) for k in range(3)]))
        self.assertIn("stopped here; 5 more notes weren't graded", text)
        self.assertIn("Correct notes: 3/3", text)
        self.assertNotIn("Bar 2", text)

    def test_rushed_group_and_sections(self):
        s = make_sheet([(k, 1, [60 + k % 12]) for k in range(16)], bpm=100)
        t, played = 0.0, []
        for k in range(16):
            played.append(PlayedNote(60 + k % 12, t, 0.4))
            t += 60 / (125 if 4 <= k < 12 else 100)
        text = format_report(s, grade(s, played))
        self.assertIn("These notes were rushed as a group", text)
        self.assertIn("Bars 2-3: rushed", text)

    def test_many_mistakes_in_a_bar_listed(self):
        s = two_bars()
        played = [PlayedNote(70 + k, k, 0.8) for k in range(4)] + [PlayedNote(64 + k, 4 + k, 0.8) for k in range(4)]
        text = format_report(s, grade(s, played))
        self.assertIn("Bar 1: 4 wrong/missed/extra notes", text)

    def test_nothing_played_does_not_crash(self):
        s = two_bars()
        text = format_report(s, grade(s, []))
        self.assertIn("Correct notes: 0/0", text)

    def test_one_note_has_no_tempo(self):
        s = two_bars()
        text = format_report(s, grade(s, [PlayedNote(60, 0, 0.8)]))
        self.assertIn("Target tempo: 60 BPM", text)


if __name__ == "__main__":
    unittest.main()
