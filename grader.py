"""Compare a recorded performance against the sheet and grade each note."""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from itertools import groupby
from statistics import median
from typing import Callable

from recorder import PlayedNote
from sheet import ExpectedNote, Sheet

# Alignment costs. A missed, extra or wrong note costs 1. On top of that, a
# note played far from where the sheet puts it costs up to TIMING_COST more,
# which reaches its maximum once the note is TIMING_SCALE_BEATS away.
TIMING_COST = 0.8
TIMING_SCALE_BEATS = 1.0
# Same idea for a note of the wrong pitch, but steeper: past ~0.4 beats off,
# a wrong-pitch note reads better as a stray note plus a missed one.
WRONG_TIMING_COST = 1.2
WRONG_TIMING_SCALE_BEATS = 0.5
ANCHOR_WINDOW = 12   # nearby correct notes used to estimate the player's local tempo
OUTLIER_BEATS = 0.5  # anchors further than this from the fitted tempo line are ignored
SEARCH_RADIUS = 32   # how far (in played notes) pass 2 may stray from pass 1's matching
RESCUE_BEATS = 0.5   # a "missed" note with a stray of the same pitch this close was just mistimed
EXTRA_PER_CHORD = 2  # a chord can absorb up to this many stray notes beyond its own size


class Verdict(Enum):
    CORRECT = "correct"
    WRONG = "wrong"    # a note was played in this spot, but the wrong pitch
    MISSED = "missed"  # a note on the sheet that was never played
    EXTRA = "extra"    # a played note that isn't on the sheet
    NOT_REACHED = "not reached"  # after the point where the player stopped


@dataclass
class Tolerances:
    """How far off something can be before it gets flagged."""
    onset_beats: float = 0.25       # note early/late
    release_beats: float = 0.35     # note held too short/long
    gate: float = 0.80              # a well-played note is held for ~80% of its written length
    chord_window_sec: float = 0.08  # pass 1 only: notes this close together are sorted as a chord
    tempo_pct: float = 0.10         # a bar more than 10% off the target tempo is rushed/dragged


@dataclass
class NoteResult:
    verdict: Verdict
    expected: ExpectedNote | None
    played: PlayedNote | None
    bar: int  # for extra notes, the bar they were played in
    # Positive = late / held too long, negative = early / released too soon.
    # Timing is measured against nearby notes at the tempo the bar was played
    # at (see _judge_timing), so one mistake doesn't make every following note
    # look late, and a bar played fast or slow as a whole is reported per bar
    # instead of note by note. All notes of a chord share the chord's timing
    # (taken from its first key down), so rolling a chord isn't counted against you.
    timing_error_sec: float | None = None
    hold_error_sec: float | None = None
    timing: str = ""  # "early", "late", "on time"
    hold: str = ""    # "short", "long", "good"


@dataclass
class BarTempo:
    bar: int
    bpm: float | None  # None when too little of the bar was played to measure it
    pace: str          # "rushed", "dragged", "steady", or "" if unknown
    mistakes: int      # wrong + missed + extra notes in this bar


@dataclass
class Summary:
    correct: int
    wrong: int
    missed: int
    extra: int
    attempted: int     # sheet notes up to where the player stopped
    accuracy: float    # percent of attempted notes played correctly
    played_bpm: float | None


# ---------------------------------------------------------------------------
# Pass 1: note-by-note alignment on pitch alone
# ---------------------------------------------------------------------------

def align(
    expected: list[int],
    played: list[int],
    timing_penalty: Callable[[int, int], float] | None = None,
) -> list[tuple[int | None, int | None]]:
    """Line up two pitch sequences with the lowest total cost (edit distance).

    Returns (expected_index, played_index) pairs in order. A None on either
    side means the note was missed (None played) or extra (None expected).
    `timing_penalty(i, j)` adds a cost for pairing sheet note i with played
    note j, so that between equally good pitch matches the one closest in
    time wins.

    The player may stop before the end of the piece, so skipping the rest of
    the sheet is free: the alignment ends at whichever sheet position fits the
    played notes best, and the remaining sheet notes come back as trailing
    (index, None) pairs.
    """
    n, m = len(expected), len(played)
    DIAG, UP, LEFT = 0, 1, 2  # matched / missed sheet note / extra played note
    cost = [[0.0] * (m + 1) for _ in range(n + 1)]
    step = [[LEFT] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        cost[i][0], step[i][0] = float(i), UP
    for j in range(1, m + 1):
        cost[0][j] = float(j)

    for i in range(1, n + 1):
        row, prev = cost[i], cost[i - 1]
        for j in range(1, m + 1):
            match = prev[j - 1] + (expected[i - 1] != played[j - 1])
            if timing_penalty is not None:
                match += timing_penalty(i - 1, j - 1)
            best, move = match, DIAG
            if prev[j] + 1 < best:
                best, move = prev[j] + 1, UP
            if row[j - 1] + 1 < best:
                best, move = row[j - 1] + 1, LEFT
            row[j], step[i][j] = best, move

    # Best place on the sheet to have stopped; on ties, prefer having played further.
    stop = min(range(n + 1), key=lambda i: (cost[i][m], -i))

    pairs: list[tuple[int | None, int | None]] = [(i, None) for i in range(n - 1, stop - 1, -1)]
    i, j = stop, m
    while i > 0 or j > 0:
        move = step[i][j]
        if move == DIAG:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif move == UP:
            pairs.append((i - 1, None))
            i -= 1
        else:
            pairs.append((None, j - 1))
            j -= 1
    pairs.reverse()
    return pairs


def _chord_order(played: list[PlayedNote], window_sec: float) -> list[PlayedNote]:
    """Sort played notes by time, but notes struck together by pitch, to match
    how the sheet orders chords. Only good enough for pass 1's rough tempo
    estimate; pass 2 handles chords properly.
    """
    result: list[PlayedNote] = []
    cluster: list[PlayedNote] = []
    for note in sorted(played, key=lambda n: n.onset_sec):
        if cluster and note.onset_sec - cluster[0].onset_sec > window_sec:
            result += sorted(cluster, key=lambda n: n.pitch)
            cluster = []
        cluster.append(note)
    return result + sorted(cluster, key=lambda n: n.pitch)


def _estimate_beats(times: list[float], anchors: list[tuple[float, float]]) -> list[float] | None:
    """Estimate where on the sheet (in beats) the player was at each time.

    `anchors` are (played seconds, sheet beats) pairs for notes we're fairly
    sure about. Each estimate is a straight-line fit through the nearest
    anchors, so it follows the player's tempo as it speeds up and slows down.
    """
    if len(anchors) < 2:
        return None
    anchors = sorted(anchors)
    anchor_times = [t for t, _ in anchors]
    window = min(ANCHOR_WINDOW, len(anchors))

    estimates = []
    for t in times:
        lo = min(max(bisect_left(anchor_times, t) - window // 2, 0), len(anchors) - window)
        near = anchors[lo:lo + window]
        # Pass 1 can pair notes with the wrong spot on the sheet (e.g. which of
        # several repeated notes was skipped). A robust fit ignores those;
        # then a normal fit through the anchors that agree with it is more precise.
        line = _robust_line(near)
        inliers = [(a, b) for a, b in near if abs(line(a) - b) <= OUTLIER_BEATS]
        if len(inliers) >= 2:
            line = _fit_line(inliers)
        estimates.append(line(t))
    return estimates


def _robust_line(points: list[tuple[float, float]]) -> Callable[[float], float]:
    """Theil-Sen line: the median of the slopes between every pair of points,
    so a few bad points can't drag it, wherever they sit.
    """
    slopes = sorted(
        (b2 - b1) / (t2 - t1)
        for k, (t1, b1) in enumerate(points)
        for t2, b2 in points[k + 1:]
        if t2 - t1 > 1e-6
    )
    if not slopes:
        return _fit_line(points)
    slope = slopes[len(slopes) // 2]
    offsets = sorted(b - slope * t for t, b in points)
    offset = offsets[len(offsets) // 2]
    return lambda t: offset + slope * t


def _fit_line(points: list[tuple[float, float]]) -> Callable[[float], float]:
    """Least-squares straight line through (seconds, beats) points."""
    mean_t = sum(t for t, _ in points) / len(points)
    mean_b = sum(b for _, b in points) / len(points)
    var_t = sum((t - mean_t) ** 2 for t, _ in points)
    if var_t < 1e-9:  # all points at the same instant (one chord)
        return lambda t: mean_b
    slope = sum((t - mean_t) * (b - mean_b) for t, b in points) / var_t
    return lambda t: mean_b + slope * (t - mean_t)


# ---------------------------------------------------------------------------
# Pass 2: chord-by-chord alignment
# ---------------------------------------------------------------------------

def _align_moments(
    moments: list[list[ExpectedNote]],
    played: list[PlayedNote],
    estimated: list[float] | None,
    centers: list[int],
) -> tuple[list[tuple[int | None, list[int]]], int]:
    """Match each sheet moment (all notes starting together) to a group of
    consecutive played notes, in any order.

    Returns the steps in order: (moment index, played indices) for each moment
    up to where the player stopped (an empty list means the whole moment was
    missed), and (None, [j]) for stray played notes. Also returns how many
    moments were reached.

    Grouping is decided here rather than by a fixed time window, so a chord
    rolled slowly, top to bottom, or with one hand late still lands on the
    right moment. `centers[i]` is roughly where pass 1 put moment i among the
    played notes; only nearby groupings are considered, which keeps this fast
    on long pieces.
    """
    n, m = len(moments), len(played)
    inf = float("inf")

    # Search band per row (row i = moments before i have been dealt with).
    lo = [0] * (n + 1)
    hi = [m] * (n + 1)
    for i in range(n):
        lo[i] = 0 if i == 0 else max(lo[i - 1], centers[i] - SEARCH_RADIUS)
        hi[i] = min(m, centers[i] + SEARCH_RADIUS + len(moments[i]))
    lo[n] = lo[n - 1] if n else 0
    for i in range(n - 1, -1, -1):  # every row must reach the next one's band
        hi[i] = max(hi[i], lo[i + 1], lo[i])

    def timing(i: int, j: int, scale: float, most: float) -> float:
        if estimated is None:
            return 0.0
        return most * min(abs(estimated[j] - moments[i][0].onset_beats) / scale, 1.0)

    cost = [dict() for _ in range(n + 1)]   # cost[i][j]
    back = [dict() for _ in range(n + 1)]   # back[i][j] = (prev i, prev j)
    cost[0][0] = 0.0

    def relax(i: int, j: int, c: float, came_from: tuple[int, int]) -> None:
        if lo[i] <= j <= hi[i] and c < cost[i].get(j, inf):
            cost[i][j] = c
            back[i][j] = came_from

    for i in range(n + 1):
        for j in range(lo[i], hi[i] + 1):
            c = cost[i].get(j)
            if c is None:
                continue
            if j < m:  # played note j is a stray
                relax(i, j + 1, c + 1, (i, j))
            if i == n:
                continue
            moment = moments[i]
            relax(i + 1, j, c + len(moment), (i, j))  # whole moment missed

            # Moment i takes played notes j .. j+k-1. Missing and extra pitches
            # pair up as wrong notes, so the cost is the larger of the two.
            # A wrong note is normally played at the right time, so a
            # wrong-pitch note pays a steeper price for being off in time:
            # far enough off, "missed + stray note" is the better reading.
            remaining = Counter(note.pitch for note in moment)
            matched, time_cost = 0, 0.0
            for k in range(1, min(len(moment) + EXTRA_PER_CHORD, m - j) + 1):
                p = played[j + k - 1].pitch
                if remaining[p] > 0:
                    remaining[p] -= 1
                    matched += 1
                    time_cost += timing(i, j + k - 1, TIMING_SCALE_BEATS, TIMING_COST)
                else:
                    time_cost += timing(i, j + k - 1, WRONG_TIMING_SCALE_BEATS, WRONG_TIMING_COST)
                mismatch = max(len(moment) - matched, k - matched)
                relax(i + 1, j + k, c + mismatch + time_cost, (i, j))

    # Best place on the sheet to have stopped; on ties, prefer having played further.
    stop = min((i for i in range(n + 1) if m in cost[i]), key=lambda i: (cost[i][m], -i))

    steps: list[tuple[int | None, list[int]]] = []
    i, j = stop, m
    while (i, j) != (0, 0):
        pi, pj = back[i][j]
        if pi == i:
            steps.append((None, [pj]))
        else:
            steps.append((pi, list(range(pj, j))))
        i, j = pi, pj
    steps.reverse()
    return steps, stop


def _pair_up(moment: list[ExpectedNote], group: list[PlayedNote]):
    """Within one moment: same pitches are correct; leftovers on both sides
    pair up as wrong notes, closest pitches first (a wrong note is usually a
    neighbouring key); the rest are missed/extra.
    """
    unmatched = list(group)
    correct, missing = [], []
    for exp in moment:
        ply = next((p for p in unmatched if p.pitch == exp.pitch), None)
        if ply is not None:
            unmatched.remove(ply)
            correct.append((exp, ply))
        else:
            missing.append(exp)

    wrong = []
    while missing and unmatched:
        exp, ply = min(((e, p) for e in missing for p in unmatched), key=lambda ep: abs(ep[0].pitch - ep[1].pitch))
        missing.remove(exp)
        unmatched.remove(ply)
        wrong.append((exp, ply))
    return correct, wrong, missing, unmatched


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

def _classify(error: float, tolerance: float, low: str, high: str, ok: str) -> str:
    if error > tolerance:
        return high
    if error < -tolerance:
        return low
    return ok


def grade(sheet: Sheet, played: list[PlayedNote], tol: Tolerances = Tolerances()) -> list[NoteResult]:
    if not sheet.notes:
        return [NoteResult(Verdict.EXTRA, None, p, sheet.bars[0].number if sheet.bars else 0) for p in played]

    # Pass 1: match note by note on pitch alone, to get a rough idea of the
    # player's tempo and where each part of the sheet was played.
    rough = _chord_order(played, tol.chord_window_sec)
    pairs = align([n.pitch for n in sheet.notes], [n.pitch for n in rough])
    anchors = [
        (rough[j].onset_sec, sheet.notes[i].onset_beats)
        for i, j in pairs
        if i is not None and j is not None and sheet.notes[i].pitch == rough[j].pitch
    ]
    position = [0] * len(sheet.notes)  # how many played notes came before sheet note i
    consumed = 0
    for i, j in pairs:
        if i is not None:
            position[i] = consumed
        if j is not None:
            consumed += 1

    # Pass 2: match chord by chord, preferring notes close in time. This sorts
    # out rolled chords and cases pitch alone can't, like a skipped note
    # followed by a wrong one, or which of two repeated notes was the extra.
    played = sorted(played, key=lambda n: n.onset_sec)
    estimated = _estimate_beats([n.onset_sec for n in played], anchors)
    moments: list[list[ExpectedNote]] = []
    centers: list[int] = []
    index = 0
    for _, group in groupby(sheet.notes, key=lambda n: n.onset_beats):
        group = list(group)
        moments.append(group)
        centers.append(position[index])
        index += len(group)
    steps, reached = _align_moments(moments, played, estimated, centers)

    entries = []  # per step: [moment index or None, correct, wrong, missed, extra]
    for i, group_indices in steps:
        group = [played[j] for j in group_indices]
        if i is None:
            entries.append([None, [], [], [], group])
        else:
            entries.append([i, *_pair_up(moments[i], group)])
    beat_of = {id(p): b for p, b in zip(played, estimated)} if estimated is not None else {}
    _rescue_stragglers(entries, moments, beat_of)

    results: list[NoteResult] = []
    hits: list[tuple[float, float, int, list[NoteResult]]] = []  # (beat, first key down, bar, results)
    bar = sheet.bars[0].number

    for i, correct, wrong, missed, extra in entries:
        if i is None:
            results += [NoteResult(Verdict.EXTRA, None, p, bar) for p in extra]
            continue

        moment = moments[i]
        bar = moment[0].bar
        moment_results = (
            [NoteResult(Verdict.CORRECT, e, p, bar) for e, p in correct]
            + [NoteResult(Verdict.WRONG, e, p, bar) for e, p in wrong]
            + [NoteResult(Verdict.MISSED, e, None, bar) for e in missed]
        )
        moment_results.sort(key=lambda r: r.expected.pitch)
        results += moment_results
        results += [NoteResult(Verdict.EXTRA, None, p, bar) for p in extra]

        played_here = [r for r in moment_results if r.played is not None]
        if played_here:
            when = min(r.played.onset_sec for r in played_here)  # a chord counts from its first key down
            hits.append((moment[0].onset_beats, when, bar, played_here))

    # Moments after the point where the player stopped weren't missed, they
    # just weren't reached yet. Same for moments missed at the very end.
    results += [NoteResult(Verdict.NOT_REACHED, e, None, e.bar) for m in moments[reached:] for e in m]
    for result in reversed(results):
        if result.verdict is Verdict.NOT_REACHED:
            continue
        if result.verdict is not Verdict.MISSED:
            break
        result.verdict = Verdict.NOT_REACHED

    # Timing and hold lengths are judged at the tempo each bar was actually
    # played at. Playing a whole bar fast or slow is reported once, per bar
    # ("rushed as a group"); here we only look at evenness within that tempo.
    local_bpm = {t.bar: t.bpm for t in bar_tempos(sheet, results, tol) if t.bpm}
    _judge_timing(hits, local_bpm, sheet.bpm, tol)

    for result in results:
        if result.expected is None or result.played is None:
            continue
        bar_spb = 60.0 / local_bpm.get(result.bar, sheet.bpm)
        target_hold = result.expected.duration_beats * bar_spb * tol.gate
        result.hold_error_sec = result.played.duration_sec - target_hold
        result.hold = _classify(result.hold_error_sec, tol.release_beats * bar_spb, "short", "long", "good")
        if result.hold == "long" and result.played.pedalled:
            # Letting the pedal carry a note past its written length is a
            # pedalling choice, not a mistake with the fingers.
            result.hold = "good"

    return results


def _rescue_stragglers(entries: list[list], moments: list[list[ExpectedNote]], beat_of: dict[int, float]) -> None:
    """Fix "missed X + extra X" right next to each other.

    Notes must be matched in time order, so a quick note played late can end
    up inside the next chord (especially a rolled one) and get reported as
    missed in its own spot plus a stray note in the next. If a missed note has
    a stray of the same pitch within RESCUE_BEATS nearby, it was really
    played, just late or early, and the timing check will say so.
    """
    for k, (i, correct, _, missed, _) in enumerate(entries):
        if i is None:
            continue
        for exp in list(missed):
            for other in entries[max(0, k - 2):k + 3]:
                stray = next((p for p in other[4] if p.pitch == exp.pitch
                              and abs(beat_of.get(id(p), exp.onset_beats) - exp.onset_beats) <= RESCUE_BEATS), None)
                if stray is not None:
                    other[4].remove(stray)
                    missed.remove(exp)
                    correct.append((exp, stray))
                    break


def _judge_timing(hits, local_bpm: dict[int, float], target_bpm: float, tol: Tolerances) -> None:
    """Mark each played chord/note early, late or on time.

    A chord is on time if its distance from the previous chord fits the
    local tempo, OR its distance from the last chord that was on time does.
    The second check stops one late note from making the next one look
    early when the player simply gets back on the beat; the first lets the
    player settle into a new pulse after a pause instead of every following
    note being flagged.
    """
    previous = steady = None  # (beat, time, bar) of the previous chord / the last on-time chord
    for beat, when, bar, notes in hits:
        error = label = None
        if previous is not None:
            # The gap before this chord was played at the tempo of the bar it
            # started in, so that's the tempo to judge it by.
            spb = 60.0 / local_bpm.get(previous[2], target_bpm)
            limit = tol.onset_beats * spb
            error = (when - previous[1]) - (beat - previous[0]) * spb
            label = _classify(error, limit, "early", "late", "on time")
            if label != "on time" and steady is not None and steady != previous:
                from_steady = (when - steady[1]) - (beat - steady[0]) * spb
                if abs(from_steady) <= limit:
                    error, label = from_steady, "on time"
        for r in notes:
            r.timing_error_sec = error
            r.timing = label or ""
        previous = (beat, when, bar)
        if label in (None, "on time"):
            steady = previous


def summarize(sheet: Sheet, results: list[NoteResult]) -> Summary:
    counts = Counter(r.verdict for r in results)
    attempted = len(sheet.notes) - counts[Verdict.NOT_REACHED]
    return Summary(
        correct=counts[Verdict.CORRECT],
        wrong=counts[Verdict.WRONG],
        missed=counts[Verdict.MISSED],
        extra=counts[Verdict.EXTRA],
        attempted=attempted,
        accuracy=100.0 * counts[Verdict.CORRECT] / attempted if attempted else 0.0,
        played_bpm=played_bpm(results),
    )


# ---------------------------------------------------------------------------
# Tempo
# ---------------------------------------------------------------------------

def _beat_times(results: list[NoteResult]) -> dict[float, float]:
    """Sheet beat -> when the player hit it (earliest note of that beat)."""
    times: dict[float, float] = {}
    for r in results:
        if r.expected is not None and r.played is not None:
            beat = r.expected.onset_beats
            times[beat] = min(times.get(beat, r.played.onset_sec), r.played.onset_sec)
    return times


def played_bpm(results: list[NoteResult]) -> float | None:
    """Estimate the player's overall tempo from the first and last beats played."""
    times = _beat_times(results)
    if len(times) < 2:
        return None
    first, last = min(times), max(times)
    seconds = times[last] - times[first]
    return (last - first) / seconds * 60.0 if seconds > 0 else None


def bar_tempos(sheet: Sheet, results: list[NoteResult], tol: Tolerances = Tolerances()) -> list[BarTempo]:
    """Work out how fast each bar was played, for bars the player reached.

    Measured over the bar's notes plus the next bar's first note, so it
    includes the time spent on the bar's last note.
    """
    times = _beat_times(results)
    beats = sorted(times)
    graded = [r for r in results if r.verdict is not Verdict.NOT_REACHED]
    reached = {r.bar for r in graded}
    mistakes: dict[int, int] = {}
    for r in graded:
        if r.verdict is not Verdict.CORRECT:
            mistakes[r.bar] = mistakes.get(r.bar, 0) + 1

    tempos: list[BarTempo] = []
    for bar in sheet.bars:
        if bar.number not in reached:
            continue
        end = bar.start + bar.length
        span = [b for b in beats if bar.start <= b < end]
        # Extend to the next bar's first beat, if the player got there without skipping ahead.
        following = next((b for b in beats if b >= end), None)
        if following is not None and following < end + bar.length:
            span.append(following)

        bpm = None
        if len(span) >= 2 and span[-1] - span[0] >= 1.0:
            # The median of the seconds-per-beat between every pair of notes,
            # so one late or early note can't swing the whole bar's tempo.
            spb = median(
                (times[b2] - times[b1]) / (b2 - b1)
                for k, b1 in enumerate(span) for b2 in span[k + 1:]
            )
            if spb > 0:
                bpm = 60.0 / spb

        pace = ""
        if bpm is not None:
            pace = _classify(bpm / sheet.bpm - 1.0, tol.tempo_pct, "dragged", "rushed", "steady")
        tempos.append(BarTempo(bar.number, bpm, pace, mistakes.get(bar.number, 0)))
    return tempos
