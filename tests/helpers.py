"""Shared tools for the tests: MIDI file builders and a "virtual pianist" that
plays a sheet like a person would (timing wobble, tempo drift, rolled chords,
pedal, mistakes), so the grader can be checked without a real piano.
"""

from __future__ import annotations

import atexit
import math
import os
import random
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from itertools import groupby

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import mido  # noqa: E402

from recorder import PlayedNote  # noqa: E402
from sheet import Bar, ExpectedNote, Sheet  # noqa: E402

TWINKLE = os.path.join(ROOT, "MIDI_Files", "Twinkle Twinkle Little Star (MIDI Version).mid")
TPB = 480  # MIDI ticks per beat for generated files


# ---------------------------------------------------------------------------
# MIDI files
# ---------------------------------------------------------------------------

def temp_dir() -> str:
    """A scratch folder, deleted when the tests finish."""
    path = tempfile.mkdtemp(prefix="piano_tests_")
    atexit.register(shutil.rmtree, path, True)
    return path


def write_midi(path: str, tracks: list[list[tuple[float, float, int]]], bpm: float = 120,
               time_sig: tuple[int, int] = (4, 4), channels: list[int] | None = None,
               single_track: bool = False, extra_meta: list[tuple[float, mido.MetaMessage]] = ()) -> str:
    """Write a MIDI file. Each track is a list of (beat, length in beats, pitch).

    single_track=True writes a type-0 file with every part on its own channel,
    the way some keyboards and websites export.
    """
    channels = channels or list(range(len(tracks)))
    mid = mido.MidiFile(type=0 if single_track else 1, ticks_per_beat=TPB)

    def to_track(events):
        track, now = mido.MidiTrack(), 0
        for tick, _, msg in sorted(events, key=lambda e: (e[0], e[1])):
            track.append(msg.copy(time=tick - now))
            now = tick
        return track

    meta = [(0, 0, mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm))),
            (0, 0, mido.MetaMessage("time_signature", numerator=time_sig[0], denominator=time_sig[1]))]
    meta += [(int(b * TPB), 0, m) for b, m in extra_meta]
    note_tracks = []
    for notes, ch in zip(tracks, channels):
        events = []
        for beat, length, pitch in notes:
            events.append((int(beat * TPB), 2, mido.Message("note_on", note=pitch, velocity=80, channel=ch)))
            events.append((int((beat + length) * TPB), 1, mido.Message("note_off", note=pitch, channel=ch)))
        note_tracks.append(events)

    if single_track:
        mid.tracks.append(to_track(meta + [e for t in note_tracks for e in t]))
    else:
        mid.tracks.append(to_track(meta))
        mid.tracks += [to_track(t) for t in note_tracks]
    mid.save(path)
    return path


def melody(pitches_and_lengths: list[tuple[int, float]], start: float = 0.0) -> list[tuple[float, float, int]]:
    """[(pitch, beats), ...] -> track notes; pitch None is a rest."""
    out, beat = [], start
    for pitch, length in pitches_and_lengths:
        if pitch is not None:
            out.append((beat, length, pitch))
        beat += length
    return out


def make_sheet(moments: list[tuple[float, float, list[int]]], bpm: float = 120, bar_length: float = 4.0) -> Sheet:
    """Build a Sheet directly from (beat, length, [pitches]) without a file."""
    notes = []
    end = max((b + l for b, l, _ in moments), default=bar_length)
    bars = [Bar(i + 1, i * bar_length, bar_length) for i in range(max(1, math.ceil(end / bar_length)))]
    for beat, length, pitches in moments:
        bar = bars[int(beat // bar_length)]
        for p in pitches:
            notes.append(ExpectedNote(p, beat, length, bar.number, beat - bar.start + 1))
    notes.sort(key=lambda n: (n.onset_beats, n.pitch))
    return Sheet(notes, bpm, bars, ["Part 1"], notes, bars)


def random_piece(rng: random.Random, bars: int = 8, bar_length: float = 4.0, bpm: float = 100,
                 chord_chance: float = 0.3) -> Sheet:
    """A random two-hand-ish piece: a melody on a grid of 8ths/quarters/halves,
    with chords now and then and a bass note on each downbeat.
    """
    moments, beat = [], 0.0
    total = bars * bar_length
    while beat < total - 1e-9:
        length = min(rng.choice([0.5, 0.5, 1.0, 1.0, 1.0, 2.0]), total - beat)
        top = rng.randint(60, 79)
        pitches = [top]
        if rng.random() < chord_chance:
            pitches += [top - rng.choice([3, 4]), top - 7]
        if abs(beat % bar_length) < 1e-9:
            pitches.append(rng.randint(36, 52))
        moments.append((beat, length, sorted(set(pitches))))
        beat += length
    return make_sheet(moments, bpm=bpm, bar_length=bar_length)


# ---------------------------------------------------------------------------
# Virtual pianist
# ---------------------------------------------------------------------------

@dataclass
class Truth:
    """What the virtual pianist actually did wrong, to compare with the grader."""
    wrong: set = field(default_factory=set)   # (onset_beats, sheet pitch) played as another pitch
    missed: set = field(default_factory=set)  # (onset_beats, sheet pitch) not played
    extra: int = 0                            # stray notes added


def perform(sheet: Sheet, rng: random.Random, *, tempo_scale: float = 1.0, drift: float = 0.0,
            jitter_sec: float = 0.0, roll_sec: float = 0.0, legato: float = 0.85,
            p_wrong: float = 0.0, p_missed: float = 0.0, p_extra: float = 0.0,
            pedal: bool = False, bar_tempo: dict[int, float] | None = None,
            shuffle: bool = True) -> tuple[list[PlayedNote], Truth]:
    """Play `sheet` like a person.

    tempo_scale: overall speed relative to the sheet (1.1 = 10% fast)
    drift:       slow tempo wander, e.g. 0.1 = +-10% over a few bars
    jitter_sec:  random timing error per chord/note (standard deviation)
    roll_sec:    chords are spread over up to this long, in random order
    bar_tempo:   {bar number: speed factor} to rush/drag particular bars
    pedal:       let keys go early but hold the sound with the pedal
    """
    truth = Truth()
    moments = [list(g) for _, g in groupby(sheet.notes, key=lambda n: n.onset_beats)]
    origin = moments[0][0].onset_beats if moments else 0.0

    # Map sheet beats to seconds, integrating a tempo that can change.
    def speed_at(beat: float, bar: int) -> float:
        s = tempo_scale * (1 + drift * math.sin(2 * math.pi * beat / 13.0))
        return s * (bar_tempo or {}).get(bar, 1.0)

    times, t, prev = [], 0.0, origin
    for m in moments:
        beat = m[0].onset_beats
        # integrate in small steps so tempo changes land in the right bar
        steps = max(1, int((beat - prev) / 0.25))
        for k in range(steps):
            b0 = prev + (beat - prev) * k / steps
            bar = _bar_of(sheet, b0)
            t += (beat - prev) / steps * 60.0 / (sheet.bpm * speed_at(b0, bar))
        times.append(t)
        prev = beat

    played: list[PlayedNote] = []
    previous = None  # (nominal time, actual time) of the previous chord
    for m, when in zip(moments, times):
        spb = 60.0 / (sheet.bpm * speed_at(m[0].onset_beats, m[0].bar))
        nominal = when
        when += rng.gauss(0, jitter_sec)
        if previous is not None:
            # Even a sloppy player keeps chords in order: at least 40% of the
            # written gap separates one chord from the next.
            when = max(when, previous[1] + 0.4 * (nominal - previous[0]))
        previous = (nominal, when)
        offsets = sorted(rng.uniform(0, roll_sec) for _ in m) if roll_sec else [0.0] * len(m)
        rng.shuffle(offsets)
        taken = {n.pitch for n in m}
        for n, off in zip(m, offsets):
            r = rng.random()
            if r < p_missed:
                truth.missed.add((n.onset_beats, n.pitch))
                continue
            pitch = n.pitch
            if r < p_missed + p_wrong:
                choices = [n.pitch + d for d in (-2, -1, 1, 2) if n.pitch + d not in taken]
                pitch = rng.choice(choices)
                taken.add(pitch)
                truth.wrong.add((n.onset_beats, n.pitch))
            length = n.duration_beats * spb
            if pedal:
                played.append(PlayedNote(pitch, when + off, length * 0.98, pedalled=True))
            else:
                played.append(PlayedNote(pitch, when + off, max(0.03, length * legato + rng.gauss(0, 0.02))))
        if rng.random() < p_extra:
            # a stray note between this chord and the next
            truth.extra += 1
            played.append(PlayedNote(rng.randint(48, 84), when + spb * 0.45, 0.1))

    if played:
        first = min(p.onset_sec for p in played)
        for p in played:
            p.onset_sec -= first
    if shuffle:
        rng.shuffle(played)  # arrival order must not matter
    return played, truth


def _bar_of(sheet: Sheet, beat: float) -> int:
    for bar in reversed(sheet.all_bars):
        if beat >= bar.start - 1e-9:
            return bar.number
    return sheet.all_bars[0].number


# ---------------------------------------------------------------------------
# Scoring the grader against the truth
# ---------------------------------------------------------------------------

@dataclass
class Score:
    sheet_notes: int = 0
    clean_notes: int = 0      # notes the pianist played right
    false_flags: int = 0      # ...that the grader still flagged
    mistakes: int = 0         # notes the pianist got wrong or missed
    detected: int = 0         # ...that the grader flagged (any non-correct verdict)
    exact: int = 0            # ...flagged with exactly the right verdict
    extras_played: int = 0
    extras_found: int = 0     # EXTRA verdicts
    timing_flags: int = 0     # correct notes flagged early/late
    timed_notes: int = 0

    def add(self, other: "Score") -> None:
        for k in self.__dataclass_fields__:
            setattr(self, k, getattr(self, k) + getattr(other, k))

    @property
    def false_flag_rate(self) -> float:
        return self.false_flags / self.clean_notes if self.clean_notes else 0.0

    @property
    def recall(self) -> float:
        return self.detected / self.mistakes if self.mistakes else 1.0

    @property
    def exact_rate(self) -> float:
        return self.exact / self.mistakes if self.mistakes else 1.0

    @property
    def timing_flag_rate(self) -> float:
        return self.timing_flags / self.timed_notes if self.timed_notes else 0.0


def score(results, truth: Truth) -> Score:
    from grader import Verdict
    s = Score(extras_played=truth.extra)
    for r in results:
        if r.verdict is Verdict.EXTRA:
            s.extras_found += 1
            continue
        if r.expected is None or r.verdict is Verdict.NOT_REACHED:
            continue
        key = (r.expected.onset_beats, r.expected.pitch)
        s.sheet_notes += 1
        if key in truth.wrong or key in truth.missed:
            s.mistakes += 1
            if r.verdict is not Verdict.CORRECT:
                s.detected += 1
            want = Verdict.WRONG if key in truth.wrong else Verdict.MISSED
            if r.verdict is want:
                s.exact += 1
        else:
            s.clean_notes += 1
            if r.verdict is not Verdict.CORRECT:
                s.false_flags += 1
            elif r.timing:
                s.timed_notes += 1
                if r.timing != "on time":
                    s.timing_flags += 1
    return s
