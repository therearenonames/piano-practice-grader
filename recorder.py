"""Capture a live performance from a MIDI input port.

On Windows, a virtual keyboard such as VMPK can send notes into a loopMIDI
port, and this module listens on the other end of that port.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable

import mido
import rtmidi

from sheet import note_name

PERCUSSION_CHANNEL = 9  # General MIDI drums (channel 10 counting from 1)


SUSTAIN_PEDAL = 64      # MIDI control number of the sustain (damper) pedal
PEDAL_DOWN_AT = 64      # pedal values 64-127 mean down


@dataclass
class PlayedNote:
    pitch: int
    onset_sec: float           # seconds since the first key press
    duration_sec: float = 0.0  # how long the note sounded: key down until key up, or pedal up
    pedalled: bool = False     # the sustain pedal kept it sounding after the key came up


def find_port(name_fragment: str) -> str | None:
    """Return the first input port whose name contains `name_fragment`.

    mido often appends a number to port names (e.g. 'PythonLoop 0').
    """
    return next((p for p in mido.get_input_names() if name_fragment in p), None)


class Recorder:
    """Records notes from a MIDI port between start() and stop().

    This reads the port through python-rtmidi directly rather than through a
    mido callback. With a callback, closing the port while a message is
    arriving can deadlock (the close waits for the callback, the callback
    waits for Python), freezing the app. Here a background thread polls
    rtmidi's own queue, and note times come from rtmidi's timestamps, so they
    stay accurate even if the thread is briefly slow to pick them up.

    The sustain pedal is followed too: a note whose key is let go while the
    pedal is down keeps sounding until the pedal comes up (or the same key is
    struck again), and its duration covers all of that.

    `on_note` is called from the background thread, so GUI code should poll
    `len(recorder.notes)` instead of using it.
    """

    def __init__(self, on_note: Callable[[PlayedNote], None] | None = None):
        self.notes: list[PlayedNote] = []
        self._on_note = on_note
        self._ignore_channels: frozenset[int] = frozenset()
        self._held: dict[int, PlayedNote] = {}       # pitch -> note whose key is still down
        self._sustained: dict[int, PlayedNote] = {}  # pitch -> key up, but the pedal holds it
        self._pedal_down = False
        self._first: float | None = None        # rtmidi clock time of the first key press
        self._clock = 0.0                       # rtmidi clock: seconds since the port opened
        self._clock_read_at = 0.0               # time.perf_counter() when _clock was last updated
        self._midi_in: rtmidi.MidiIn | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    def start(self, port_name: str, ignore_channels: Iterable[int] = ()) -> None:
        """Start recording. Messages on `ignore_channels` (0-based) are dropped,
        e.g. metronome clicks when the sound output loops back into this port.
        """
        midi_in = rtmidi.MidiIn(queue_size_limit=4096)
        ports = midi_in.get_ports()
        if port_name not in ports:
            midi_in.delete()
            raise OSError(f"MIDI input not found: {port_name}. Is it plugged in? Try refreshing the ports.")
        try:
            midi_in.open_port(ports.index(port_name))
        except Exception as e:
            midi_in.delete()
            # On Windows only one program can use a MIDI device at a time.
            raise OSError(f"Couldn't open {port_name}. Another program (a DAW, another "
                          f"practice app, VMPK...) may be using it.\n\n({e})") from e
        midi_in.ignore_types(sysex=True, timing=True, active_sense=True)

        self._ignore_channels = frozenset(ignore_channels)
        self.notes, self._held, self._sustained, self._first = [], {}, {}, None
        self._pedal_down = False
        self._clock, self._clock_read_at = 0.0, time.perf_counter()
        self._midi_in = midi_in
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> list[PlayedNote]:
        if self._midi_in is None:
            return self.notes
        self._stopping.set()
        self._thread.join()
        self._midi_in.close_port()
        self._midi_in.delete()
        self._midi_in = None

        # Notes still sounding when recording stopped (key or pedal still
        # down) count as released right now.
        if self._first is not None:
            end = self._clock + (time.perf_counter() - self._clock_read_at) - self._first
            for note in [*self._held.values(), *self._sustained.values()]:
                note.duration_sec = end - note.onset_sec
        self._held, self._sustained = {}, {}
        return self.notes

    def _run(self) -> None:
        while not self._stopping.is_set():
            item = self._midi_in.get_message()
            if item is None:
                time.sleep(0.001)
                continue
            data, delta = item
            self._clock += delta
            self._clock_read_at = time.perf_counter()
            try:
                msg = mido.Message.from_bytes(data)
            except ValueError:
                continue  # not a message we understand
            self._handle(msg, self._clock)

    def _handle(self, msg: mido.Message, now: float) -> None:
        if getattr(msg, "channel", None) in self._ignore_channels:
            return

        if msg.type == "control_change" and msg.control == SUSTAIN_PEDAL:
            self._pedal_down = msg.value >= PEDAL_DOWN_AT
            if not self._pedal_down:
                for note in self._sustained.values():
                    self._end(note, now)
                self._sustained = {}

        elif msg.type == "note_on" and msg.velocity > 0:
            if self._first is None:
                self._first = now
            # Striking a key again cuts off its earlier sound: one held by the
            # pedal, or one still "down" because the keyboard sent no key-up.
            for earlier in (self._sustained.pop(msg.note, None), self._held.pop(msg.note, None)):
                if earlier is not None:
                    self._end(earlier, now)
            note = PlayedNote(msg.note, onset_sec=now - self._first)
            self.notes.append(note)
            self._held[msg.note] = note
            if self._on_note is not None:
                self._on_note(note)

        # A note_on with velocity 0 is a release, same as note_off.
        elif msg.type in ("note_off", "note_on"):
            note = self._held.pop(msg.note, None)
            if note is not None:
                self._end(note, now)  # provisional if the pedal keeps it going
                if self._pedal_down:
                    note.pedalled = True
                    self._sustained[msg.note] = note

    def _end(self, note: PlayedNote, now: float) -> None:
        note.duration_sec = (now - self._first) - note.onset_sec


def record(port_name: str) -> list[PlayedNote]:
    """Record from the terminal until the user presses Enter."""
    recorder = Recorder(on_note=lambda n: print(f"  heard {note_name(n.pitch):<4} ({len(recorder.notes)} notes)"))
    recorder.start(port_name)
    try:
        input("Recording... play the piece, then press ENTER to stop and grade.\n")
    finally:
        notes = recorder.stop()
    return notes
