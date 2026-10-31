"""Grading specific, hand-made performances where the right answer is known."""

import os
import random
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import make_sheet, perform, random_piece  # noqa: E402

from grader import Verdict, bar_tempos, grade, played_bpm, summarize  # noqa: E402
from recorder import PlayedNote  # noqa: E402

C, D, E, F, G, A, B = 60, 62, 64, 65, 67, 69, 71


def scale_sheet(bpm=60, length=1.0):
    """C D E F G A B C as quarter notes; at 60 BPM one beat = one second."""
    return make_sheet([(k * length, length, [p]) for k, p in enumerate([C, D, E, F, G, A, B, 72])], bpm=bpm)


def play(sheet, spb=None, hold=0.8, skip=(), change=None, shift=None):
    """Play a one-note-at-a-time sheet perfectly, with optional edits.
    skip: indexes not played; change: {index: pitch}; shift: {index: seconds}.
    """
    spb = spb or sheet.seconds_per_beat
    out = []
    for k, n in enumerate(sheet.notes):
        if k in skip:
            continue
        t = n.onset_beats * spb + (shift or {}).get(k, 0.0)
        out.append(PlayedNote((change or {}).get(k, n.pitch), t, n.duration_beats * spb * hold))
    return out


def verdicts(results):
    return [r.verdict for r in results if r.verdict is not Verdict.NOT_REACHED]


class TestNotes(unittest.TestCase):
    def test_perfect(self):
        s = scale_sheet()
        res = grade(s, play(s))
        self.assertEqual(verdicts(res), [Verdict.CORRECT] * 8)
        self.assertTrue(all(r.timing in ("", "on time") for r in res))
        self.assertTrue(all(r.hold == "good" for r in res))
        self.assertEqual(summarize(s, res).accuracy, 100.0)

    def test_wrong_note(self):
        s = scale_sheet()
        res = grade(s, play(s, change={3: F + 1}))
        self.assertEqual(res[3].verdict, Verdict.WRONG)
        self.assertEqual(res[3].played.pitch, F + 1)
        self.assertEqual(sum(r.verdict is Verdict.CORRECT for r in res), 7)

    def test_missed_note(self):
        s = scale_sheet()
        res = grade(s, play(s, skip={4}))
        self.assertEqual([r.verdict for r in res][4], Verdict.MISSED)
        self.assertEqual(res[4].expected.pitch, G)

    def test_extra_note(self):
        s = scale_sheet()
        played = play(s) + [PlayedNote(90, 2.5, 0.1)]
        res = grade(s, played)
        extras = [r for r in res if r.verdict is Verdict.EXTRA]
        self.assertEqual(len(extras), 1)
        self.assertEqual(extras[0].played.pitch, 90)
        self.assertEqual(sum(r.verdict is Verdict.CORRECT for r in res), 8)

    def test_skip_then_flub(self):
        # Skips E, then hits a wrong key just before F: missed E + extra, not "wrong".
        s = scale_sheet()
        played = play(s, skip={2}) + [PlayedNote(F + 1, 2.9, 0.05)]
        res = grade(s, played)
        self.assertEqual(next(r for r in res if r.expected and r.expected.pitch == E).verdict, Verdict.MISSED)
        self.assertEqual(sum(r.verdict is Verdict.EXTRA for r in res), 1)
        self.assertEqual(sum(r.verdict is Verdict.CORRECT for r in res), 7)

    def test_double_tap(self):
        s = scale_sheet()
        played = play(s) + [PlayedNote(G, 4.15, 0.1)]  # G struck twice
        res = grade(s, played)
        self.assertEqual(sum(r.verdict is Verdict.CORRECT for r in res), 8)
        self.assertEqual(sum(r.verdict is Verdict.EXTRA for r in res), 1)

    def test_key_bounce_same_instant(self):
        s = scale_sheet()
        played = play(s) + [PlayedNote(D, 1.0, 0.8)]  # D registered twice at once
        res = grade(s, played)
        self.assertEqual(sum(r.verdict is Verdict.CORRECT for r in res), 8)
        self.assertEqual(sum(r.verdict is Verdict.EXTRA for r in res), 1)

    def test_repeated_notes_right_one_missed(self):
        # Four Cs; the third is skipped. Timing should pin the miss on the third.
        # (With almost nothing played after the repeats it's genuinely
        # ambiguous; a phrase continuing afterwards settles it.)
        s = make_sheet([(k, 1, [C]) for k in range(4)] + [(4 + k, 1, [p]) for k, p in enumerate([D, E, F, G, A])],
                       bpm=60)
        res = grade(s, play(s, skip={2}))
        self.assertEqual([r.verdict for r in res][:4],
                         [Verdict.CORRECT, Verdict.CORRECT, Verdict.MISSED, Verdict.CORRECT])

    def test_noodling_before_starting(self):
        s = scale_sheet()
        warmup = [PlayedNote(p, -3 + k * 0.3, 0.1) for k, p in enumerate([40, 45, 50, 55])]
        res = grade(s, warmup + play(s))
        self.assertEqual(sum(r.verdict is Verdict.CORRECT for r in res), 8)
        self.assertEqual(sum(r.verdict is Verdict.EXTRA for r in res), 4)

    def test_stopping_early(self):
        s = scale_sheet()
        res = grade(s, play(s)[:3])
        self.assertEqual(verdicts(res), [Verdict.CORRECT] * 3)
        self.assertEqual(sum(r.verdict is Verdict.NOT_REACHED for r in res), 5)
        sm = summarize(s, res)
        self.assertEqual((sm.attempted, sm.accuracy), (3, 100.0))

    def test_starting_in_the_middle(self):
        # Player starts at the 5th note; the first four are missed, not "not reached".
        s = scale_sheet()
        res = grade(s, play(s)[4:])
        self.assertEqual(sum(r.verdict is Verdict.MISSED for r in res), 4)
        self.assertEqual(sum(r.verdict is Verdict.CORRECT for r in res), 4)

    def test_nothing_played(self):
        s = scale_sheet()
        res = grade(s, [])
        self.assertTrue(all(r.verdict is Verdict.NOT_REACHED for r in res))
        self.assertEqual(summarize(s, res).attempted, 0)

    def test_empty_sheet(self):
        s = make_sheet([])
        s.notes, s.all_notes = [], []
        res = grade(s, [PlayedNote(C, 0, 1)])
        self.assertEqual([r.verdict for r in res], [Verdict.EXTRA])

    def test_single_note(self):
        s = make_sheet([(0, 1, [C])])
        self.assertEqual(verdicts(grade(s, [PlayedNote(C, 0, 0.4)])), [Verdict.CORRECT])
        self.assertEqual(verdicts(grade(s, [PlayedNote(D, 0, 0.4)])), [Verdict.WRONG])

    def test_arrival_order_does_not_matter(self):
        s = random_piece(random.Random(3))
        played, _ = perform(s, random.Random(4), jitter_sec=0.02, roll_sec=0.05, shuffle=False)
        a = [(r.verdict, r.expected and r.expected.pitch) for r in grade(s, played)]
        random.Random(5).shuffle(played)
        b = [(r.verdict, r.expected and r.expected.pitch) for r in grade(s, played)]
        self.assertEqual(a, b)


class TestChords(unittest.TestCase):
    def setUp(self):
        self.s = make_sheet([(0, 2, [48, 52, 55, 60]), (2, 2, [50, 53, 57, 62]), (4, 2, [48, 52, 55, 60])], bpm=60)

    def chord_play(self, moment_pitches, offsets=None):
        out = []
        for k, pitches in enumerate(moment_pitches):
            for i, p in enumerate(pitches):
                out.append(PlayedNote(p, k * 2.0 + (offsets[i] if offsets else 0.0), 1.6))
        return out

    def test_any_order(self):
        played = self.chord_play([[60, 48, 55, 52], [62, 57, 50, 53], [55, 60, 52, 48]])
        self.assertEqual(verdicts(grade(self.s, played)), [Verdict.CORRECT] * 12)

    def test_slow_roll_top_down(self):
        played = self.chord_play([[60, 55, 52, 48]] * 3, offsets=[0.0, 0.08, 0.16, 0.24])
        # second chord with its own pitches
        played = [p for p in played if not (1.9 < p.onset_sec < 2.5)]
        played += [PlayedNote(p, 2.0 + o, 1.6) for p, o in zip([62, 57, 53, 50], [0.0, 0.08, 0.16, 0.24])]
        res = grade(self.s, played)
        self.assertEqual(verdicts(res), [Verdict.CORRECT] * 12)
        # rolling doesn't make the chord late
        self.assertTrue(all(r.timing in ("", "on time") for r in res))

    def test_one_chord_note_missed(self):
        played = self.chord_play([[48, 52, 55, 60], [50, 57, 62], [48, 52, 55, 60]])
        res = grade(self.s, played)
        missed = [r for r in res if r.verdict is Verdict.MISSED]
        self.assertEqual([r.expected.pitch for r in missed], [53])

    def test_wrong_chord_note_pairs_with_nearest_pitch(self):
        # 53 missed and 57 played as 58: the wrong note is 57->58, and 53 is the missed one.
        played = self.chord_play([[48, 52, 55, 60], [50, 58, 62], [48, 52, 55, 60]])
        res = grade(self.s, played)
        wrong = [r for r in res if r.verdict is Verdict.WRONG]
        self.assertEqual([(r.expected.pitch, r.played.pitch) for r in wrong], [(57, 58)])
        self.assertEqual([r.expected.pitch for r in res if r.verdict is Verdict.MISSED], [53])

    def test_extra_note_in_chord(self):
        played = self.chord_play([[48, 52, 55, 60, 64], [50, 53, 57, 62], [48, 52, 55, 60]])
        res = grade(self.s, played)
        self.assertEqual(sum(r.verdict is Verdict.CORRECT for r in res), 12)
        self.assertEqual([r.played.pitch for r in res if r.verdict is Verdict.EXTRA], [64])

    def test_left_hand_late(self):
        played = [PlayedNote(p, k * 2.0 + (0.12 if p < 58 else 0.0), 1.6)
                  for k, ch in enumerate([[48, 52, 55, 60], [50, 53, 57, 62], [48, 52, 55, 60]]) for p in ch]
        self.assertEqual(verdicts(grade(self.s, played)), [Verdict.CORRECT] * 12)

    def test_fast_note_mistimed_into_next_chord(self):
        # A 16th note played late, landing inside the next (rolled) chord: still "correct, late".
        s = make_sheet([(0, 0.75, [60]), (0.75, 0.25, [62]), (1, 1, [48, 55, 64])], bpm=120)
        played = [PlayedNote(60, 0.0, 0.3), PlayedNote(48, 0.5, 0.4), PlayedNote(62, 0.52, 0.1),
                  PlayedNote(55, 0.54, 0.4), PlayedNote(64, 0.56, 0.4)]
        res = grade(s, played)
        self.assertEqual(verdicts(res), [Verdict.CORRECT] * 5)


class TestTiming(unittest.TestCase):
    def test_single_late_note_does_not_make_next_early(self):
        s = scale_sheet()
        res = grade(s, play(s, shift={3: 0.4}))
        self.assertEqual(res[3].timing, "late")
        self.assertEqual(res[4].timing, "on time")
        self.assertEqual(sum(r.timing != "on time" for r in res[1:]), 1)

    def test_single_early_note(self):
        s = scale_sheet()
        res = grade(s, play(s, shift={5: -0.4}))
        self.assertEqual(res[5].timing, "early")
        self.assertEqual(res[6].timing, "on time")

    def test_pause_then_carry_on(self):
        # Hesitates before the 4th note and continues from there: only that note is late.
        s = scale_sheet()
        res = grade(s, play(s, shift={k: 0.5 for k in range(3, 8)}))
        self.assertEqual([r.timing for r in res[1:]],
                         ["on time", "on time", "late", "on time", "on time", "on time", "on time"])

    def test_slow_practice_is_not_every_note_late(self):
        s = scale_sheet(bpm=100)
        res = grade(s, play(s, spb=0.6 / 0.7))  # 70% speed
        self.assertTrue(all(r.timing in ("", "on time") for r in res))
        self.assertAlmostEqual(played_bpm(res), 70, delta=1)
        self.assertTrue(all(t.pace == "dragged" for t in bar_tempos(s, res)))

    def test_rushed_bars_are_reported_per_bar(self):
        s = make_sheet([(k, 1, [C + k % 12]) for k in range(16)], bpm=100)
        # bars 2-3 at 125 BPM, the rest at 100
        t, played = 0.0, []
        for k in range(16):
            played.append(PlayedNote(C + k % 12, t, 0.4))
            t += 60 / (125 if 4 <= k < 12 else 100)
        res = grade(s, played)
        paces = {b.bar: b.pace for b in bar_tempos(s, res)}
        self.assertEqual(paces, {1: "steady", 2: "rushed", 3: "rushed", 4: "steady"})
        self.assertTrue(all(r.timing in ("", "on time") for r in res))

    def test_extreme_tempos_still_match(self):
        for factor in (0.5, 2.0):
            s = random_piece(random.Random(8), bpm=100)
            played, _ = perform(s, random.Random(9), tempo_scale=factor, jitter_sec=0.01)
            res = grade(s, played)
            self.assertEqual(summarize(s, res).accuracy, 100.0, f"at {factor}x speed")

    def test_fast_sixteenths(self):
        s = make_sheet([(k * 0.25, 0.25, [60 + (k % 8)]) for k in range(64)], bpm=160)
        played, _ = perform(s, random.Random(1), jitter_sec=0.008)
        self.assertEqual(summarize(s, grade(s, played)).accuracy, 100.0)


class TestHolds(unittest.TestCase):
    def test_too_short_and_too_long(self):
        s = scale_sheet()
        played = play(s)
        played[2].duration_sec = 0.1
        played[5].duration_sec = 2.5
        res = grade(s, played)
        self.assertEqual(res[2].hold, "short")
        self.assertEqual(res[5].hold, "long")
        self.assertEqual(sum(r.hold == "good" for r in res), 6)

    def test_pedal_excuses_long_but_not_short(self):
        s = scale_sheet()
        played = play(s)
        played[1] = PlayedNote(D, 1.0, 2.5, pedalled=True)
        played[3] = PlayedNote(F, 3.0, 0.1, pedalled=True)
        res = grade(s, played)
        self.assertEqual(res[1].hold, "good")
        self.assertEqual(res[3].hold, "short")

    def test_hold_judged_at_played_tempo(self):
        s = scale_sheet(bpm=100)
        res = grade(s, play(s, spb=0.6 / 0.7))  # slow, so every note lasts longer in seconds
        self.assertTrue(all(r.hold == "good" for r in res))


class TestSpeed(unittest.TestCase):
    def test_long_piece_is_quick(self):
        s = random_piece(random.Random(11), bars=100, chord_chance=0.5)
        played, _ = perform(s, random.Random(12), jitter_sec=0.03, roll_sec=0.04, p_wrong=0.03, p_extra=0.03)
        start = time.perf_counter()
        grade(s, played)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 10.0, f"{len(s.notes)} notes took {elapsed:.1f}s")


if __name__ == "__main__":
    unittest.main()
