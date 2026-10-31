"""Load the reference performance (the "answer key") from a MIDI file."""

from __future__ import annotations

import os
import tempfile
from bisect import bisect_right
from dataclasses import dataclass

import mido
from music21 import converter, pitch, stream, tempo

DEFAULT_BPM = 120.0
DEFAULT_BAR_BEATS = 4.0  # used only if the file has no bar information


@dataclass
class ExpectedNote:
    pitch: int             # MIDI note number (60 = middle C)
    onset_beats: float     # start time, in quarter-note beats from the beginning
    duration_beats: float  # written length, in quarter-note beats
    bar: int               # bar (measure) number as printed on the sheet
    beat_in_bar: float     # 1-based beat within the bar
    part: int = 0          # which part (usually: which hand) the note belongs to


@dataclass
class Bar:
    number: int    # as printed on the sheet
    start: float   # in beats from the beginning of the piece
    length: float  # in beats


@dataclass
class Sheet:
    notes: list[ExpectedNote]      # what gets graded: one per key press, sorted by (onset, pitch)
    bpm: float
    bars: list[Bar]                # the bars `notes` covers
    part_names: list[str]          # one per part in the file
    all_notes: list[ExpectedNote]  # every part's notes, before select() narrows them down
    all_bars: list[Bar]            # every bar in the piece

    @property
    def seconds_per_beat(self) -> float:
        return 60.0 / self.bpm

    def select(
        self,
        first_bar: int | None = None,
        last_bar: int | None = None,
        part: int | None = None,
        bpm: float | None = None,
    ) -> Sheet:
        """Narrow the piece down to a range of bars and/or one part (hand).

        `part=None` means all parts. `bpm` overrides the tempo, which is handy
        for practising slower than written.
        """
        lo = first_bar if first_bar is not None else self.all_bars[0].number
        hi = last_bar if last_bar is not None else self.all_bars[-1].number
        notes = [
            n for n in self.all_notes
            if lo <= n.bar <= hi and (part is None or n.part == part)
        ]
        return Sheet(
            notes=_merge_parts(notes),
            bpm=bpm or self.bpm,
            bars=[b for b in self.all_bars if lo <= b.number <= hi],
            part_names=self.part_names,
            all_notes=self.all_notes,
            all_bars=self.all_bars,
        )


def load_sheet(path: str, bpm: float | None = None) -> Sheet:
    """Parse a MIDI file into a list of expected notes.

    All parts (both hands) and every note of each chord are included. If `bpm`
    is given it overrides the tempo stored in the file.
    """
    # Check the file with mido first: it closes the file properly when it
    # fails, while music21 can leave a broken file open (and so locked, on Windows).
    try:
        mid = mido.MidiFile(path)
    except Exception as e:
        raise ValueError(f"not a readable MIDI file ({e})") from e

    split = _split_channels(mid)
    try:
        score = converter.parse(split or path)
    finally:
        if split:
            os.remove(split)
    parts = list(score.parts) or [score]

    if bpm is None:
        marks = score.flatten().getElementsByClass(tempo.MetronomeMark)
        bpm = next((m.getQuarterBPM() for m in marks if m.getQuarterBPM()), DEFAULT_BPM)

    bars = _find_bars(score, parts)
    starts = [b.start for b in bars]

    all_notes: list[ExpectedNote] = []
    names: list[str] = []
    for part in parts:
        # Within a part, the same pitch starting at the same time is one key
        # press (some MIDI files double every note).
        by_key: dict[tuple[float, int], float] = {}
        for n in part.flatten().notes:
            onset = float(n.offset)
            for p in n.pitches:  # drum parts have no pitches, so they drop out here
                key = (onset, p.midi)
                by_key[key] = max(by_key.get(key, 0.0), float(n.quarterLength))
        if not by_key:
            continue  # nothing to play in this part (e.g. a drum track)
        index = len(names)
        name = getattr(part, "partName", None)
        names.append(f"Part {index + 1}" + (f" ({name})" if name else ""))
        for (onset, midi), duration in by_key.items():
            bar = bars[max(bisect_right(starts, onset) - 1, 0)]
            all_notes.append(ExpectedNote(midi, onset, duration, bar.number, onset - bar.start + 1, index))

    return Sheet(_merge_parts(all_notes), bpm, bars, names or ["Part 1"], all_notes, bars)


def _split_channels(mid: mido.MidiFile) -> str | None:
    """Some MIDI files put both hands in one track on different channels (a
    "type 0" file). music21 would read that as a single part, so write a copy
    with one track per channel and return its path. Returns None when the
    file doesn't need it.
    """
    note_types = ("note_on", "note_off")
    if not any(len({m.channel for m in t if m.type in note_types}) > 1 for t in mid.tracks):
        return None

    meta: list[tuple[int, mido.Message]] = []
    by_channel: dict[tuple[int, int], list[tuple[int, mido.Message]]] = {}
    for t, track in enumerate(mid.tracks):
        now = 0
        for msg in track:
            now += msg.time
            if msg.is_meta:
                if msg.type != "end_of_track":
                    meta.append((now, msg))
            elif hasattr(msg, "channel"):
                by_channel.setdefault((t, msg.channel), []).append((now, msg))

    def to_track(events: list[tuple[int, mido.Message]]) -> mido.MidiTrack:
        track, now = mido.MidiTrack(), 0
        for tick, msg in sorted(events, key=lambda e: e[0]):  # stable: keeps same-tick order
            track.append(msg.copy(time=tick - now))
            now = tick
        return track

    out = mido.MidiFile(type=1, ticks_per_beat=mid.ticks_per_beat)
    out.tracks.append(to_track(meta))
    out.tracks += [to_track(by_channel[key]) for key in sorted(by_channel)]
    fd, split_path = tempfile.mkstemp(suffix=".mid")
    os.close(fd)
    out.save(split_path)
    return split_path


def _merge_parts(notes: list[ExpectedNote]) -> list[ExpectedNote]:
    """Combine parts into one sorted list. The same pitch at the same time in
    two parts (both hands in unison) is a single key press.
    """
    merged: dict[tuple[float, int], ExpectedNote] = {}
    for n in notes:
        key = (n.onset_beats, n.pitch)
        if key not in merged or n.duration_beats > merged[key].duration_beats:
            merged[key] = n
    return [merged[key] for key in sorted(merged)]


def _find_bars(score: stream.Score, parts: list) -> list[Bar]:
    """Find where each bar begins and how long it is, using the part with the most bars."""
    measures = max((list(p.getElementsByClass(stream.Measure)) for p in parts), key=len)
    if not measures:
        # No bar lines in the file: assume 4/4.
        total = float(score.highestTime)
        count = max(int(total // DEFAULT_BAR_BEATS) + 1, 1)
        return [Bar(i + 1, i * DEFAULT_BAR_BEATS, DEFAULT_BAR_BEATS) for i in range(count)]

    bars = []
    for k, m in enumerate(measures):
        start = float(m.offset)
        if k + 1 < len(measures):
            length = float(measures[k + 1].offset) - start  # real length, so pickup bars come out short
        else:
            length = float(m.barDuration.quarterLength)
        bars.append(Bar(m.number, start, length))
    return bars


def note_name(midi_number: int) -> str:
    """60 -> 'C4', 61 -> 'C#4', 70 -> 'Bb4'."""
    return pitch.Pitch(midi=midi_number).nameWithOctave.replace("-", "b")  # music21 writes flats as '-'
