"""Metronome clicks, "listen first" playback, and the Player's real-time sending."""

import itertools
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import make_sheet  # noqa: E402

import mido  # noqa: E402

from playback import (ACCENT_NOTE, CLICK_CHANNEL, CLICK_NOTE, Player, click_events,  # noqa: E402
                      count_in_beats, merge, piece_events)
from sheet import Bar, ExpectedNote, Sheet  # noqa: E402

LOOP_IN = next((p for p in mido.get_input_names() if "PythonLoop" in p), None)
LOOP_OUT = next((p for p in mido.get_output_names() if "PythonLoop" in p), None)


def ons(events):
    return [(t, m) for t, m in events if m.type == "note_on"]


class TestClicks(unittest.TestCase):
    def test_count_in_length_follows_time_signature(self):
        self.assertEqual(count_in_beats(make_sheet([(0, 1, [60])], bar_length=4)), 4)
        self.assertEqual(count_in_beats(make_sheet([(0, 1, [60])], bar_length=3)), 3)

    def test_count_in_only(self):
        s = make_sheet([(0, 4, [60]), (4, 4, [62])], bpm=120)
        clicks = ons(click_events(s, count_in=True, during=False))
        self.assertEqual([round(t, 3) for t, _ in clicks], [0.0, 0.5, 1.0, 1.5])
        self.assertEqual([m.note for _, m in clicks], [ACCENT_NOTE, CLICK_NOTE, CLICK_NOTE, CLICK_NOTE])
        self.assertTrue(all(m.channel == CLICK_CHANNEL for _, m in clicks))

    def test_metronome_during_with_bar_accents(self):
        s = make_sheet([(0, 4, [60]), (4, 4, [62])], bpm=60)
        clicks = ons(click_events(s, count_in=False, during=True))
        self.assertEqual(len(clicks), 8)
        self.assertEqual([k for k, (_, m) in enumerate(clicks) if m.note == ACCENT_NOTE], [0, 4])

    def test_count_in_then_metronome_is_continuous(self):
        s = make_sheet([(0, 3, [60]), (3, 3, [62])], bpm=60, bar_length=3)
        times = [t for t, _ in ons(click_events(s, count_in=True, during=True))]
        self.assertEqual(len(times), 3 + 6)
        self.assertTrue(all(abs((b - a) - 1.0) < 1e-9 for a, b in zip(times, times[1:])))

    def test_keep_going_never_ends(self):
        s = make_sheet([(0, 4, [60])], bpm=120)
        events = list(itertools.islice(click_events(s, True, True, keep_going=True), 100))
        self.assertEqual(len(ons(events)), 50)
        self.assertGreater(events[-1][0], 20.0)  # well past the 2-bar piece

    def test_every_click_is_released(self):
        s = make_sheet([(0, 4, [60]), (4, 4, [62])], bpm=120)
        events = list(click_events(s, True, True))
        self.assertEqual(sum(m.type == "note_on" for _, m in events), sum(m.type == "note_off" for _, m in events))

    def test_times_never_go_backwards(self):
        s = make_sheet([(0, 4, [60]), (4, 4, [62])], bpm=200)
        times = [t for t, _ in merge(piece_events(s, 1.2), click_events(s, True, True))]
        self.assertEqual(times, sorted(times))


class TestPieceEvents(unittest.TestCase):
    def test_starts_after_lead_in(self):
        s = make_sheet([(0, 1, [60]), (1, 1, [62])], bpm=60)
        evs = ons(piece_events(s, lead_in_sec=2.0))
        self.assertEqual([(t, m.note) for t, m in evs], [(2.0, 60), (3.0, 62)])

    def test_bar_selection_starts_at_the_bar_not_the_first_note(self):
        # bar 2 starts with a rest; the first note is on beat 2 of that bar
        s = make_sheet([(0, 1, [60]), (5, 1, [62])], bpm=60).select(2, 2)
        evs = ons(piece_events(s))
        self.assertEqual([(t, m.note) for t, m in evs], [(1.0, 62)])

    def test_chord_notes_start_together(self):
        s = make_sheet([(0, 1, [48, 52, 55])], bpm=60)
        self.assertEqual({t for t, _ in ons(piece_events(s))}, {0.0})

    def test_same_key_struck_while_held(self):
        notes = [ExpectedNote(60, 0, 4, 1, 1, 1), ExpectedNote(60, 1, 0.5, 1, 2, 0)]
        bars = [Bar(1, 0, 4)]
        s = Sheet(notes, 60, bars, ["S", "A"], notes, bars)
        evs = piece_events(s)
        # the key sounds continuously: off just before the re-strike, then on until the long note's end
        sounding, last = 0, []
        for t, m in evs:
            sounding += 1 if m.type == "note_on" else -1
            self.assertIn(sounding, (0, 1))
            last.append((round(t, 2), m.type))
        self.assertEqual(last[-1], (3.8, "note_off"))

    def test_every_note_released(self):
        s = make_sheet([(k * 0.5, 0.5, [60 + k % 5, 48]) for k in range(16)], bpm=140)
        evs = piece_events(s)
        self.assertEqual(sum(m.type == "note_on" for _, m in evs), sum(m.type == "note_off" for _, m in evs))


@unittest.skipUnless(LOOP_IN and LOOP_OUT, "needs a loopMIDI port")
class TestPlayerLive(unittest.TestCase):
    """Send through loopMIDI and listen on the other end."""

    def capture(self, seconds, action):
        import rtmidi
        midi_in = rtmidi.MidiIn()
        midi_in.open_port(midi_in.get_ports().index(LOOP_IN))
        midi_in.ignore_types(sysex=True, timing=True, active_sense=True)
        got, clock = [], 0.0
        start = time.perf_counter()
        action()
        while time.perf_counter() - start < seconds:
            item = midi_in.get_message()
            if item is None:
                time.sleep(0.001)
                continue
            clock += item[1]
            got.append((clock, mido.Message.from_bytes(item[0])))
        midi_in.close_port()
        midi_in.delete()
        return got

    def test_timing_is_accurate(self):
        s = make_sheet([(k * 0.5, 0.25, [60 + k]) for k in range(12)], bpm=120)  # 8ths at 120 = 0.25 s apart
        player = Player()
        got = self.capture(4.0, lambda: player.play(LOOP_OUT, piece_events(s)))
        times = [t for t, m in got if m.type == "note_on"]
        self.assertEqual(len(times), 12)
        gaps = [b - a for a, b in zip(times, times[1:])]
        self.assertLess(max(abs(g - 0.25) for g in gaps), 0.01, f"gaps {gaps}")

    def test_stop_is_quick_and_silences_everything(self):
        s = make_sheet([(0, 8, [48, 52, 55])], bpm=60)  # a long held chord
        player = Player()
        got = []

        def go():
            player.play(LOOP_OUT, piece_events(s))
            time.sleep(0.3)
            t = time.perf_counter()
            player.stop()
            got.append(time.perf_counter() - t)

        msgs = self.capture(1.0, go)
        self.assertLess(got[0], 0.2)
        self.assertFalse(player.playing)
        # all-notes-off (control 123) went out on every channel
        offs = {m.channel for _, m in msgs if m.type == "control_change" and m.control == 123}
        self.assertEqual(offs, set(range(16)))

    def test_finishes_on_its_own(self):
        player = Player()
        player.play(LOOP_OUT, piece_events(make_sheet([(0, 0.2, [60])], bpm=120)))
        time.sleep(0.6)
        self.assertFalse(player.playing)

    def test_play_again_replaces_previous(self):
        player = Player()
        s = make_sheet([(0, 8, [60])], bpm=60)
        player.play(LOOP_OUT, piece_events(s))
        player.play(LOOP_OUT, piece_events(s))
        self.assertTrue(player.playing)
        player.stop()
        self.assertFalse(player.playing)

    def test_bad_port_raises(self):
        with self.assertRaises(Exception):
            Player().play("No Such Port 9", [])

    def test_stop_when_not_playing_is_harmless(self):
        Player().stop()


if __name__ == "__main__":
    unittest.main()
