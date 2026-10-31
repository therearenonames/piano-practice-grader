"""Loading MIDI files into a Sheet, and narrowing it with select()."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import TWINKLE, melody, temp_dir, write_midi  # noqa: E402

import mido  # noqa: E402

from sheet import load_sheet, note_name  # noqa: E402


class TestTwinkle(unittest.TestCase):
    """The real file in MIDI_Files."""

    @classmethod
    def setUpClass(cls):
        cls.sheet = load_sheet(TWINKLE)

    def test_tempo_from_file(self):
        self.assertAlmostEqual(self.sheet.bpm, 95.0)

    def test_doubled_notes_count_once(self):
        # The file stores every note twice; it should be one key press each.
        self.assertEqual(len(self.sheet.notes), 70)
        keys = [(n.onset_beats, n.pitch) for n in self.sheet.notes]
        self.assertEqual(len(keys), len(set(keys)))

    def test_melody_starts_correctly(self):
        self.assertEqual([n.pitch for n in self.sheet.notes[:7]], [60, 60, 67, 67, 69, 69, 67])

    def test_bars(self):
        self.assertEqual(len(self.sheet.all_bars), 21)
        self.assertEqual(self.sheet.notes[4].bar, 2)
        self.assertEqual(self.sheet.notes[4].beat_in_bar, 1.0)

    def test_notes_sorted(self):
        keys = [(n.onset_beats, n.pitch) for n in self.sheet.notes]
        self.assertEqual(keys, sorted(keys))


class TestFileShapes(unittest.TestCase):
    """Generated files covering the ways MIDI files differ in the wild."""

    @classmethod
    def setUpClass(cls):
        cls.dir = temp_dir()
        cls.rh = melody([(60, 1), (62, 1), (64, 1), (65, 1), (67, 4)])
        cls.lh = [(0, 4, 48), (4, 4, 43)]

    def path(self, name):
        return os.path.join(self.dir, name)

    def test_two_tracks_are_two_hands(self):
        s = load_sheet(write_midi(self.path("hands.mid"), [self.rh, self.lh]))
        self.assertEqual(len(s.part_names), 2)
        self.assertEqual({n.part for n in s.notes if n.pitch < 55}, {1})

    def test_single_track_file_is_split_by_channel(self):
        s = load_sheet(write_midi(self.path("type0.mid"), [self.rh, self.lh], single_track=True))
        self.assertEqual(len(s.part_names), 2)
        self.assertEqual({n.part for n in s.notes if n.pitch < 55}, {1})
        self.assertEqual({n.part for n in s.notes if n.pitch >= 55}, {0})

    def test_drum_track_is_ignored(self):
        drums = [(b * 0.5, 0.25, 36) for b in range(16)]
        s = load_sheet(write_midi(self.path("drums.mid"), [self.rh, drums], channels=[0, 9]))
        self.assertEqual(len(s.part_names), 1)
        self.assertNotIn(36, {n.pitch for n in s.notes})

    def test_single_track_with_drums(self):
        drums = [(b * 0.5, 0.25, 36) for b in range(16)]
        s = load_sheet(write_midi(self.path("type0d.mid"), [self.rh, self.lh, drums],
                                  channels=[0, 1, 9], single_track=True))
        self.assertEqual(len(s.part_names), 2)
        self.assertNotIn(36, {n.pitch for n in s.notes})

    def test_no_temp_files_left_behind(self):
        import tempfile
        before = set(os.listdir(tempfile.gettempdir()))
        load_sheet(write_midi(self.path("type0b.mid"), [self.rh, self.lh], single_track=True))
        after = set(os.listdir(tempfile.gettempdir()))
        self.assertEqual({f for f in after - before if f.endswith(".mid")}, set())

    def test_unison_between_hands_is_one_key_press(self):
        s = load_sheet(write_midi(self.path("unison.mid"), [[(0, 1, 60)], [(0, 2, 60)]]))
        self.assertEqual(len(s.notes), 1)
        self.assertEqual(s.notes[0].duration_beats, 2)  # the longer one wins
        # ...but each hand on its own still has it
        self.assertEqual(len(s.select(part=0).notes), 1)
        self.assertEqual(len(s.select(part=1).notes), 1)

    def test_three_four(self):
        s = load_sheet(write_midi(self.path("waltz.mid"), [melody([(60, 1), (62, 1), (64, 1), (65, 3)])],
                                  time_sig=(3, 4)))
        self.assertEqual([b.length for b in s.all_bars], [3.0, 3.0])
        self.assertEqual([(n.bar, n.beat_in_bar) for n in s.notes], [(1, 1), (1, 2), (1, 3), (2, 1)])

    def test_six_eight(self):
        s = load_sheet(write_midi(self.path("68.mid"), [melody([(60, 0.5)] * 12)], time_sig=(6, 8)))
        self.assertEqual([b.length for b in s.all_bars], [3.0, 3.0])

    def test_no_tempo_defaults_to_120(self):
        path = self.path("notempo.mid")
        mid = mido.MidiFile(ticks_per_beat=480)
        track = mido.MidiTrack()
        track += [mido.Message("note_on", note=60, velocity=80, time=0), mido.Message("note_off", note=60, time=480)]
        mid.tracks.append(track)
        mid.save(path)
        self.assertEqual(load_sheet(path).bpm, 120.0)

    def test_tempo_override(self):
        s = load_sheet(write_midi(self.path("t.mid"), [self.rh]), bpm=60)
        self.assertEqual(s.bpm, 60)
        self.assertAlmostEqual(s.seconds_per_beat, 1.0)

    def test_first_tempo_used_when_it_changes(self):
        s = load_sheet(write_midi(self.path("tchange.mid"), [self.rh], bpm=100,
                                  extra_meta=[(4, mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(60)))]))
        self.assertAlmostEqual(s.bpm, 100, places=0)

    def test_triplets(self):
        s = load_sheet(write_midi(self.path("trip.mid"), [melody([(60, 1 / 3), (62, 1 / 3), (64, 1 / 3), (65, 1)])]))
        self.assertEqual(len(s.notes), 4)
        self.assertAlmostEqual(s.notes[1].onset_beats, 1 / 3, places=2)

    def test_empty_file(self):
        s = load_sheet(write_midi(self.path("empty.mid"), []))
        self.assertEqual(s.notes, [])
        self.assertGreaterEqual(len(s.all_bars), 1)

    def test_not_a_midi_file_raises(self):
        path = self.path("fake.mid")
        with open(path, "w") as f:
            f.write("this is not a MIDI file")
        with self.assertRaises(Exception):
            load_sheet(path)

    def test_missing_file_raises(self):
        with self.assertRaises(Exception):
            load_sheet(self.path("does_not_exist.mid"))


class TestSelect(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        d = temp_dir()
        rh = melody([(60, 1)] * 16)               # 4 bars of quarter notes
        lh = [(b * 4, 4, 48) for b in range(4)]   # a whole note per bar
        cls.sheet = load_sheet(write_midi(os.path.join(d, "s.mid"), [rh, lh]))

    def test_bar_range(self):
        s = self.sheet.select(2, 3)
        self.assertEqual({n.bar for n in s.notes}, {2, 3})
        self.assertEqual([b.number for b in s.bars], [2, 3])
        self.assertEqual(len(s.notes), 10)  # 8 quarters + 2 bass notes

    def test_one_hand(self):
        self.assertTrue(all(n.pitch == 60 for n in self.sheet.select(part=0).notes))
        self.assertTrue(all(n.pitch == 48 for n in self.sheet.select(part=1).notes))

    def test_tempo(self):
        self.assertEqual(self.sheet.select(bpm=60).bpm, 60)
        self.assertEqual(self.sheet.select().bpm, self.sheet.bpm)

    def test_select_from_a_selection_can_widen_again(self):
        narrow = self.sheet.select(2, 2)
        wide = narrow.select(1, 4)
        self.assertEqual(len(wide.notes), len(self.sheet.notes))

    def test_out_of_range_is_empty(self):
        self.assertEqual(self.sheet.select(10, 12).notes, [])

    def test_original_untouched(self):
        before = len(self.sheet.notes)
        self.sheet.select(1, 1, part=0, bpm=50)
        self.assertEqual(len(self.sheet.notes), before)


class TestNoteName(unittest.TestCase):
    def test_names(self):
        self.assertEqual(note_name(60), "C4")
        self.assertEqual(note_name(61), "C#4")
        self.assertEqual(note_name(70), "Bb4")
        self.assertEqual(note_name(21), "A0")   # lowest piano key
        self.assertEqual(note_name(108), "C8")  # highest piano key


if __name__ == "__main__":
    unittest.main()
