"""Grader accuracy benchmark: the virtual pianist plays many pieces under
different conditions, and we count how often the grader gets it right.

    python tests/benchmark.py            # full run (a minute or two)
    python tests/benchmark.py --quick
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import TWINKLE, Score, perform, random_piece, score  # noqa: E402

from grader import grade  # noqa: E402
from sheet import load_sheet  # noqa: E402

CONDITIONS = {
    # name: perform() keyword arguments
    "robot (perfect)":         dict(),
    "careful human":           dict(jitter_sec=0.015, roll_sec=0.02),
    "average human":           dict(jitter_sec=0.03, roll_sec=0.04, drift=0.05),
    "sloppy human":            dict(jitter_sec=0.05, roll_sec=0.08, drift=0.1),
    "slow practice (70%)":     dict(tempo_scale=0.7, jitter_sec=0.03, roll_sec=0.04),
    "rolled chords (150ms)":   dict(jitter_sec=0.02, roll_sec=0.15),
    "pedal":                   dict(jitter_sec=0.03, roll_sec=0.04, pedal=True),
    "5% mistakes":             dict(jitter_sec=0.03, roll_sec=0.04, p_wrong=0.03, p_missed=0.02, p_extra=0.03),
    "15% mistakes":            dict(jitter_sec=0.03, roll_sec=0.04, p_wrong=0.08, p_missed=0.07, p_extra=0.1),
    "15% mistakes, sloppy":    dict(jitter_sec=0.05, roll_sec=0.08, drift=0.1, p_wrong=0.08, p_missed=0.07, p_extra=0.1),
}


def chorale_sheets(count: int, folder: str) -> list:
    """Real four-voice music: Bach chorales from music21's corpus, saved as MIDI."""
    from music21 import corpus
    sheets = []
    for path in corpus.search("bach", "composer")[: count * 3]:
        if len(sheets) >= count:
            break
        try:
            score_ = path.parse()
            if len(score_.parts) != 4:
                continue
            midi = os.path.join(folder, f"chorale_{len(sheets)}.mid")
            score_.write("midi", fp=midi)
            sheets.append(load_sheet(midi))
        except Exception:
            continue
    return sheets


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    rng = random.Random(1234)

    from helpers import temp_dir
    folder = temp_dir()
    pieces = [("twinkle", load_sheet(TWINKLE))]
    pieces += [(f"random {k}", random_piece(random.Random(k), bars=8, bpm=rng.choice([70, 100, 140])))
               for k in range(4 if args.quick else 12)]
    pieces += [(f"chorale {k}", s) for k, s in enumerate(chorale_sheets(3 if args.quick else 10, folder))]
    print(f"{len(pieces)} pieces, {sum(len(s.notes) for _, s in pieces)} notes in total\n")

    header = f"{'condition':<24} {'false flags':>11} {'mistakes found':>14} {'exact verdict':>13} " \
             f"{'extras found':>12} {'timing flags':>12} {'sec':>5}"
    print(header)
    print("-" * len(header))
    repeats = 1 if args.quick else 3
    for name, kwargs in CONDITIONS.items():
        total, start = Score(), time.perf_counter()
        for _ in range(repeats):
            for _, sheet in pieces:
                played, truth = perform(sheet, rng, **kwargs)
                total.add(score(grade(sheet, played), truth))
        extras = f"{total.extras_found}/{total.extras_played}"
        print(f"{name:<24} {total.false_flag_rate:>10.2%} {total.recall:>14.1%} {total.exact_rate:>13.1%} "
              f"{extras:>12} {total.timing_flag_rate:>11.2%} {time.perf_counter() - start:>5.1f}")


if __name__ == "__main__":
    main()
