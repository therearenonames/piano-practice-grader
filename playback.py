"""Make sound through a MIDI output: the piece itself ("listen first") and
metronome / count-in clicks.

On Windows, "Microsoft GS Wavetable Synth" is built in and needs no setup.
"""

from __future__ import annotations

import heapq
import itertools
import math
import threading
import time
from typing import Iterable, Iterator

import mido

from recorder import PERCUSSION_CHANNEL
from sheet import Sheet

Event = tuple[float, mido.Message]  # (seconds from start, message)

CLICK_CHANNEL = PERCUSSION_CHANNEL  # so the recorder can ignore clicks that loop back
ACCENT_NOTE, CLICK_NOTE = 76, 77  # high / low wood block
CLICK_LENGTH = 0.05


def count_in_beats(sheet: Sheet) -> int:
    """One bar of count-in, as long as the first bar being practised."""
    return max(1, round(sheet.bars[0].length)) if sheet.bars else 4


def piece_events(sheet: Sheet, lead_in_sec: float = 0.0) -> list[Event]:
    """The selected notes of the piece, starting at the first selected bar."""
    spb = sheet.seconds_per_beat
    origin = sheet.bars[0].start if sheet.bars else 0.0
    spans: dict[int, list[list[float]]] = {}  # pitch -> [start, end] in order
    for n in sheet.notes:
        start = lead_in_sec + (n.onset_beats - origin) * spb
        spans.setdefault(n.pitch, []).append([start, start + max(n.duration_beats * spb * 0.95, 0.05)])

    events = []
    for pitch, notes in spans.items():
        # A key can only sound once. If it's struck again while still held
        # (e.g. one hand holds a C while the other plays it), end the first
        # just before the re-strike, and let the second last as long as either.
        for k in range(len(notes) - 1):
            this, nxt = notes[k], notes[k + 1]
            if this[1] > nxt[0] - 0.005:
                nxt[1] = max(nxt[1], this[1])
                this[1] = max(this[0] + 0.01, nxt[0] - 0.005)
        for start, end in notes:
            events.append((start, mido.Message("note_on", note=pitch, velocity=70)))
            events.append((end, mido.Message("note_off", note=pitch)))
    events.sort(key=lambda e: e[0])
    return events


def click_events(sheet: Sheet, count_in: bool, during: bool, keep_going: bool = False) -> Iterator[Event]:
    """Metronome clicks on every beat, with an accent at the start of each bar.

    count_in:   one bar of clicks before the music starts.
    during:     keep clicking through the selected bars.
    keep_going: after the last bar, keep clicking until stopped (used while
                recording, since the player may be behind).
    """
    spb = sheet.seconds_per_beat
    beat = 0  # beats since the clicks started

    def click(accent: bool) -> list[Event]:
        t = beat * spb
        note = ACCENT_NOTE if accent else CLICK_NOTE
        return [
            (t, mido.Message("note_on", channel=CLICK_CHANNEL, note=note, velocity=100 if accent else 70)),
            (t + CLICK_LENGTH, mido.Message("note_off", channel=CLICK_CHANNEL, note=note)),
        ]

    if count_in:
        for k in range(count_in_beats(sheet)):
            yield from click(k == 0)
            beat += 1
    if not during:
        return

    bars = sheet.bars or []
    for bar in bars:
        for k in range(max(1, math.ceil(bar.length - 1e-9))):
            yield from click(k == 0)
            beat += 1
    if keep_going:
        per_bar = max(1, round(bars[-1].length)) if bars else 4
        for k in itertools.count():
            yield from click(k % per_bar == 0)
            beat += 1


def merge(*streams: Iterable[Event]) -> Iterator[Event]:
    return heapq.merge(*streams, key=lambda e: e[0])


class Player:
    """Sends a stream of timed MIDI messages to an output port on a background thread."""

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.started_at: float | None = None  # time.perf_counter() when playback began

    @property
    def playing(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def play(self, port_name: str, events: Iterable[Event]) -> None:
        self.stop()
        port = mido.open_output(port_name)  # opened here so errors reach the caller
        self._stop = threading.Event()
        self.started_at = time.perf_counter()
        self._thread = threading.Thread(target=self._run, args=(port, events, self._stop), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _run(self, port, events: Iterable[Event], stop: threading.Event) -> None:
        try:
            for t, msg in events:
                target = self.started_at + t
                # Short sleeps so Stop takes effect quickly; time.sleep is
                # precise to about a millisecond on Windows with Python 3.11+.
                while (remaining := target - time.perf_counter()) > 0:
                    if stop.is_set():
                        return
                    time.sleep(min(remaining, 0.02))
                if stop.is_set():
                    return
                port.send(msg)
        finally:
            port.reset()  # all notes off, so nothing hangs
            port.close()
