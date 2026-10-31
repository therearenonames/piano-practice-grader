"""The virtual pianist plays many pieces, and the grader's verdicts are
checked against what it actually did. A smaller version of benchmark.py with
pass/fail limits.
"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import TWINKLE, Score, perform, random_piece, score, temp_dir  # noqa: E402

from grader import grade  # noqa: E402
from sheet import load_sheet  # noqa: E402


def pieces():
    found = [load_sheet(TWINKLE)]
    found += [random_piece(random.Random(k), bars=8, bpm=bpm) for k, bpm in enumerate([60, 90, 120, 160, 100, 140])]
    found += [random_piece(random.Random(50), bars=8, bar_length=3.0, bpm=110)]  # 3/4
    from benchmark import chorale_sheets
    found += chorale_sheets(3, temp_dir())  # real four-voice music
    return found


class TestAccuracy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pieces = pieces()

    def run_condition(self, seed, repeats=2, **kwargs) -> Score:
        rng, total = random.Random(seed), Score()
        for _ in range(repeats):
            for sheet in self.pieces:
                played, truth = perform(sheet, rng, **kwargs)
                total.add(score(grade(sheet, played), truth))
        return total

    def assert_clean(self, s: Score, timing_limit: float):
        self.assertEqual(s.false_flags, 0, f"{s.false_flags} correct notes flagged")
        self.assertEqual(s.extras_found, 0, "stray notes reported that weren't played")
        self.assertLessEqual(s.timing_flag_rate, timing_limit)

    def test_perfect_playing(self):
        self.assert_clean(self.run_condition(1, repeats=1), timing_limit=0.0)

    def test_careful_human(self):
        self.assert_clean(self.run_condition(2, jitter_sec=0.015, roll_sec=0.02), timing_limit=0.005)

    def test_average_human(self):
        self.assert_clean(self.run_condition(3, jitter_sec=0.03, roll_sec=0.04, drift=0.05), timing_limit=0.02)

    def test_sloppy_human(self):
        self.assert_clean(self.run_condition(4, jitter_sec=0.05, roll_sec=0.08, drift=0.1), timing_limit=0.12)

    def test_slow_practice(self):
        self.assert_clean(self.run_condition(5, tempo_scale=0.7, jitter_sec=0.03, roll_sec=0.04), timing_limit=0.02)

    def test_fast_playing(self):
        self.assert_clean(self.run_condition(6, tempo_scale=1.3, jitter_sec=0.02, roll_sec=0.02), timing_limit=0.05)

    def test_rolled_chords(self):
        self.assert_clean(self.run_condition(7, jitter_sec=0.02, roll_sec=0.15), timing_limit=0.06)

    def test_pedal(self):
        self.assert_clean(self.run_condition(8, jitter_sec=0.03, roll_sec=0.04, pedal=True), timing_limit=0.02)

    def test_few_mistakes(self):
        s = self.run_condition(9, jitter_sec=0.03, roll_sec=0.04, p_wrong=0.03, p_missed=0.02, p_extra=0.03)
        self.assertLessEqual(s.false_flag_rate, 0.002)
        self.assertGreaterEqual(s.recall, 0.98)
        self.assertGreaterEqual(s.exact_rate, 0.94)
        self.assertGreaterEqual(s.extras_found, 0.85 * s.extras_played)

    def test_many_mistakes(self):
        s = self.run_condition(10, jitter_sec=0.03, roll_sec=0.04, p_wrong=0.08, p_missed=0.07, p_extra=0.1)
        self.assertLessEqual(s.false_flag_rate, 0.005)
        self.assertGreaterEqual(s.recall, 0.96)
        self.assertGreaterEqual(s.exact_rate, 0.88)


if __name__ == "__main__":
    unittest.main()
