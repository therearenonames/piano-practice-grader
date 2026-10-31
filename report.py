"""Print a graded performance as a readable report."""

from __future__ import annotations

from collections import Counter
from itertools import groupby

from grader import BarTempo, NoteResult, Tolerances, Verdict, bar_tempos, summarize
from sheet import Sheet, note_name


def _describe(r: NoteResult) -> str:
    if r.verdict is Verdict.MISSED:
        return "MISSED"
    if r.verdict is Verdict.EXTRA:
        return "EXTRA note"

    parts = ["ok" if r.verdict is Verdict.CORRECT else "WRONG NOTE"]
    if r.timing_error_sec is None:
        parts.append("first note")
    elif r.timing == "on time":
        parts.append("on time")
    else:
        parts.append(f"{r.timing} by {abs(r.timing_error_sec):.2f}s")
    if r.hold == "good":
        parts.append("good hold")
    else:
        parts.append(f"held too {r.hold} ({r.hold_error_sec:+.2f}s)")
    return ", ".join(parts)


def _bar_header(sheet: Sheet, number: int, tempo: BarTempo | None) -> list[str]:
    if tempo is None or tempo.bpm is None:
        return [f"Bar {number}"]
    lines = [f"Bar {number}   ~{tempo.bpm:.0f} BPM ({tempo.pace})"]
    if tempo.pace in ("rushed", "dragged"):
        # Each note can be close enough to the one before to count as on time,
        # while the bar as a whole still drifts off the target tempo.
        lines.append(f"  These notes were {tempo.pace} as a group "
                     f"(~{tempo.bpm:.0f} BPM vs target {sheet.bpm:.0f}).")
    return lines


def _bar_range(first: int, last: int) -> str:
    return f"Bar {first}" if first == last else f"Bars {first}-{last}"


def _trouble_spots(sheet: Sheet, tempos: list[BarTempo]) -> list[str]:
    """Runs of consecutive rushed/dragged bars, then bars with several mistakes."""
    lines = []
    for pace, run in groupby(tempos, key=lambda t: t.pace):
        run = list(run)
        if pace not in ("rushed", "dragged"):
            continue
        avg = sum(t.bpm for t in run) / len(run)
        lines.append(f"{_bar_range(run[0].bar, run[-1].bar)}: {pace} (~{avg:.0f} BPM, target {sheet.bpm:.0f})")

    for t in sorted(tempos, key=lambda t: -t.mistakes):
        if t.mistakes >= 2:
            lines.append(f"Bar {t.bar}: {t.mistakes} wrong/missed/extra notes")
    return lines


def format_report(sheet: Sheet, results: list[NoteResult], tol: Tolerances = Tolerances()) -> str:
    tempos = bar_tempos(sheet, results, tol)
    tempo_by_bar = {t.bar: t for t in tempos}
    graded = [r for r in results if r.verdict is not Verdict.NOT_REACHED]

    out = ["--- PERFORMANCE REPORT ---"]
    for bar, notes in groupby(graded, key=lambda r: r.bar):
        out += [""] + _bar_header(sheet, bar, tempo_by_bar.get(bar))
        for r in notes:
            beat = f"beat {r.expected.beat_in_bar:g}" if r.expected else ""
            expected = note_name(r.expected.pitch) if r.expected else "-"
            played = note_name(r.played.pitch) if r.played else "-"
            out.append(f"  {beat:<10} {expected:<5} {played:<5} {_describe(r)}")

    s = summarize(sheet, results)
    timing = Counter(r.timing for r in results if r.verdict is Verdict.CORRECT)
    not_reached = len(sheet.notes) - s.attempted

    if not_reached:
        out += ["", f"  ... stopped here; {not_reached} more notes weren't graded."]

    out += [
        "",
        "--- SUMMARY ---",
        f"Correct notes: {s.correct}/{s.attempted} ({s.accuracy:.0f}%)",
        f"Wrong: {s.wrong}   Missed: {s.missed}   Extra: {s.extra}",
        f"Timing (correct notes): {timing['on time']} on time, {timing['early']} early, {timing['late']} late",
    ]

    if s.played_bpm is not None:
        out.append(f"Tempo: you played at ~{s.played_bpm:.0f} BPM overall (target {sheet.bpm:.0f} BPM)")
    else:
        out.append(f"Target tempo: {sheet.bpm:.0f} BPM")

    trouble = _trouble_spots(sheet, tempos)
    out += ["", "Sections to work on:" if trouble else "No sections stood out. Nice!"]
    out += [f"  {line}" for line in trouble]
    return "\n".join(out)


def print_report(sheet: Sheet, results: list[NoteResult], tol: Tolerances = Tolerances()) -> None:
    print()
    print(format_report(sheet, results, tol))
