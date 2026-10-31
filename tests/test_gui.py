"""Drive the real window: picking files, bad input, listening, recording,
history. Sound goes to the loopMIDI port, and notes are "played" into it, so
nothing comes out of the speakers. History goes to a temporary file.
"""

import os
import shutil
import sys
import time
import tkinter as tk
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import TWINKLE, melody, temp_dir, write_midi  # noqa: E402

import mido  # noqa: E402

import gui  # noqa: E402
import history  # noqa: E402

LOOP_IN = next((p for p in mido.get_input_names() if "PythonLoop" in p), None)
LOOP_OUT = next((p for p in mido.get_output_names() if "PythonLoop" in p), None)


class GuiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = temp_dir()
        shutil.copy(TWINKLE, os.path.join(cls.folder, "a_twinkle.mid"))
        rh = melody([(60, 1), (62, 1), (64, 1), (65, 1)] * 2)
        lh = [(0, 4, 48)]  # left hand only in bar 1
        write_midi(os.path.join(cls.folder, "b_hands.mid"), [rh, lh], bpm=120)
        write_midi(os.path.join(cls.folder, "c_UPPER.MID"), [rh])
        with open(os.path.join(cls.folder, "d_broken.mid"), "w") as f:
            f.write("not midi")
        with open(os.path.join(cls.folder, "notes.txt"), "w") as f:
            f.write("ignore me")

    def setUp(self):
        self.real_history = history.HISTORY_FILE
        history.HISTORY_FILE = os.path.join(temp_dir(), "history.json")
        self.messages = []
        self.answer = True
        self.patches = {
            "showinfo": gui.messagebox.showinfo,
            "showerror": gui.messagebox.showerror,
            "askyesno": gui.messagebox.askyesno,
        }
        gui.messagebox.showinfo = lambda title, msg, **k: self.messages.append(msg)
        gui.messagebox.showerror = lambda title, msg, **k: self.messages.append(msg)
        gui.messagebox.askyesno = lambda title, msg, **k: self.answer

        self.root = tk.Tk()
        self.root.withdraw()
        self.app = gui.PracticeApp(self.root)
        self.app.folder.set(self.folder)
        self.app.refresh_files()
        if LOOP_IN:
            self.app.in_port.set(LOOP_IN)
            self.app.out_port.set(LOOP_OUT)

    def tearDown(self):
        try:
            self.app.close()
        except tk.TclError:
            pass
        for name, fn in self.patches.items():
            setattr(gui.messagebox, name, fn)
        history.HISTORY_FILE = self.real_history

    # helpers
    def pump(self, seconds):
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            self.root.update()
            time.sleep(0.005)

    def pick(self, name):
        names = list(self.app.files.get(0, "end"))
        self.app.files.selection_clear(0, "end")
        self.app.files.selection_set(names.index(name))
        self.app.select_piece()

    def play_notes(self, pitches, gap=0.25):
        with mido.open_output(LOOP_OUT) as out:
            for p in pitches:
                out.send(mido.Message("note_on", note=p, velocity=80))
                self.pump(gap * 0.8)
                out.send(mido.Message("note_off", note=p))
                self.pump(gap * 0.2)


class TestFilesAndSettings(GuiTest):
    def test_lists_only_midi_files(self):
        self.assertEqual(list(self.app.files.get(0, "end")),
                         ["a_twinkle.mid", "b_hands.mid", "c_UPPER.MID", "d_broken.mid"])

    def test_first_piece_loaded_with_its_settings(self):
        self.assertTrue(self.app.piece.endswith("a_twinkle.mid"))
        self.assertEqual(self.app.bpm.get(), "95")
        self.assertEqual((self.app.first_bar.get(), self.app.last_bar.get()), ("1", "21"))
        self.assertEqual(self.app.hands.get(), gui.ALL_PARTS)

    def test_two_hand_file_offers_hands(self):
        self.pick("b_hands.mid")
        menu = self.app.hands_menu["menu"]
        labels = [menu.entrycget(i, "label") for i in range(menu.index("end") + 1)]
        self.assertEqual(len(labels), 3)
        self.assertEqual(self.app.bpm.get(), "120")
        self.assertEqual(self.app.last_bar.get(), "2")

    def test_broken_file_does_not_leave_old_piece_selected(self):
        self.pick("d_broken.mid")
        self.assertIsNone(self.app.piece)
        self.assertIn("Couldn't load", self.app.status.get())
        self.app.start()
        self.assertIn("Pick a MIDI file first.", self.messages)

    def test_missing_folder(self):
        self.app.folder.set(os.path.join(self.folder, "nope"))
        self.app.refresh_files()
        self.assertIn("Folder not found", self.app.status.get())
        self.assertIsNone(self.app.piece)
        self.assertEqual(self.app.files.size(), 0)

    def test_empty_folder(self):
        self.app.folder.set(temp_dir())
        self.app.refresh_files()
        self.assertIn("No MIDI files", self.app.status.get())
        self.assertIsNone(self.app.piece)

    def test_bad_tempo_and_bars(self):
        for bpm, first, last in [("abc", "1", "2"), ("0", "1", "2"), ("-5", "1", "2"),
                                 ("100", "x", "2"), ("100", "3", "1"), ("100", "0", "2"), ("100", "1", "99")]:
            self.messages.clear()
            self.app.bpm.set(bpm)
            self.app.first_bar.set(first)
            self.app.last_bar.set(last)
            self.assertIsNone(self.app.practice_sheet(), (bpm, first, last))
            self.assertEqual(len(self.messages), 1, (bpm, first, last))

    def test_decimal_tempo_ok(self):
        self.app.bpm.set("72.5")
        self.assertEqual(self.app.practice_sheet().bpm, 72.5)

    def test_hand_with_nothing_in_those_bars(self):
        self.pick("b_hands.mid")
        sheet = self.app.sheets[self.app.piece]
        self.app.hands.set(sheet.part_names[1])  # left hand: only plays in bar 1
        self.app.first_bar.set("2")
        self.app.last_bar.set("2")
        self.assertIsNone(self.app.practice_sheet())
        self.assertIn("no notes", self.messages[-1])

    def test_device_name(self):
        self.assertEqual(gui._device_name("PythonLoop 0"), "PythonLoop")
        self.assertEqual(gui._device_name("PythonLoop 1"), "PythonLoop")
        self.assertEqual(gui._device_name("Digital Piano"), "Digital Piano")
        self.assertEqual(gui._device_name("Piano 2 Pro 3"), "Piano 2 Pro")

    @unittest.skipUnless(LOOP_IN, "needs a loopMIDI port")
    def test_refresh_ports_keeps_choice(self):
        self.app.out_port.set(LOOP_OUT)
        self.app.refresh_ports()
        self.assertEqual(self.app.out_port.get(), LOOP_OUT)

    def test_no_input_port(self):
        self.app.in_port.set("")
        self.app.start()
        self.assertTrue(any("No MIDI input" in m for m in self.messages))
        self.assertEqual(self.app.mode, "idle")

    def test_clicks_need_an_output(self):
        self.app.out_port.set("")
        self.app.count_in.set(True)
        self.app.start()
        self.assertIn("No sound output selected.", self.messages)
        self.assertEqual(self.app.mode, "idle")
        self.app.listen()
        self.assertEqual(self.app.mode, "idle")

    def test_damaged_history_file_does_not_stop_the_window(self):
        with open(history.HISTORY_FILE, "w") as f:
            f.write("garbage{")
        self.app.refresh_history()
        self.assertIn("No attempts", self.app.progress.get())


@unittest.skipUnless(LOOP_IN and LOOP_OUT, "needs a loopMIDI port")
class TestPractice(GuiTest):
    def setUp(self):
        super().setUp()
        self.pick("b_hands.mid")
        self.app.bpm.set("240")  # quick tests: a beat is 0.25 s

    def record(self, pitches, count_in=False, metronome=False):
        self.app.count_in.set(count_in)
        self.app.metronome.set(metronome)
        self.app.start()
        self.assertEqual(self.app.mode, "recording", self.messages)
        if count_in:
            self.pump(1.0)  # one bar of 4 at 240 BPM
        self.play_notes(pitches)
        self.app.stop()

    def test_record_and_grade(self):
        self.record([48, 60, 62, 64, 65, 60, 62, 64, 65])
        self.assertEqual(self.app.mode, "idle")
        text = self.app.report.get("1.0", "end")
        self.assertIn("Correct notes: 9/9 (100%)", text)
        self.assertEqual(len(history.load()), 1)

    def test_count_in_and_metronome_clicks_not_recorded(self):
        self.record([48, 60, 62, 64, 65], count_in=True, metronome=True)
        self.assertEqual(len(self.app.recorder.notes), 5)
        self.assertIn("Correct notes: 5/5", self.app.report.get("1.0", "end"))

    def test_count_in_shows_in_status(self):
        self.app.count_in.set(True)
        self.app.start()
        self.pump(0.1)
        self.assertIn("Count-in", self.app.status.get())
        self.pump(1.2)
        self.assertIn("Recording", self.app.status.get())
        self.app.stop()

    def test_stop_without_playing(self):
        self.app.count_in.set(False)
        self.app.metronome.set(False)
        self.app.start()
        self.pump(0.2)
        self.app.stop()
        self.assertEqual(self.app.status.get(), "No notes were recorded.")
        self.assertEqual(history.load(), [])

    def test_one_hand_and_bar_range(self):
        sheet = self.app.sheets[self.app.piece]
        self.app.hands.set(sheet.part_names[0])
        self.app.first_bar.set("2")
        self.app.last_bar.set("2")
        self.record([60, 62, 64, 65])
        self.assertIn("Correct notes: 4/4", self.app.report.get("1.0", "end"))
        attempt = history.load()[0]
        self.assertEqual((attempt["bars"], attempt["hands"]), ("2-2", sheet.part_names[0]))

    def test_settings_changed_mid_recording_do_not_leak_into_history(self):
        sheet = self.app.sheets[self.app.piece]
        self.app.count_in.set(False)
        self.app.metronome.set(False)
        self.app.start()
        self.app.hands.set(sheet.part_names[1])  # fiddled with during the take
        self.app.refresh_files()                 # ignored while recording
        self.play_notes([48, 60, 62])
        self.app.stop()
        attempt = history.load()[0]
        self.assertEqual(attempt["hands"], gui.ALL_PARTS)
        self.assertTrue(attempt["piece"].endswith("b_hands.mid"))

    def test_cannot_switch_piece_while_recording(self):
        self.app.count_in.set(False)
        self.app.metronome.set(False)
        self.app.start()
        self.assertEqual(str(self.app.files["state"]), "disabled")
        self.app.stop()
        self.assertEqual(str(self.app.files["state"]), "normal")

    def test_listen_then_stop(self):
        self.app.count_in.set(True)
        self.app.metronome.set(True)
        self.app.listen()
        self.assertEqual(self.app.mode, "listening")
        self.pump(0.5)
        self.app.stop()
        self.assertEqual(self.app.mode, "idle")
        self.assertFalse(self.app.player.playing)

    def test_listen_finishes_by_itself(self):
        self.app.count_in.set(False)
        self.app.metronome.set(False)
        self.app.first_bar.set("2")
        self.app.last_bar.set("2")
        self.app.listen()
        self.pump(1.6)  # one bar at 240 BPM, plus margin
        self.assertEqual(self.app.mode, "idle")
        self.assertIn("Finished", self.app.status.get())

    def test_history_list_show_and_delete(self):
        for pitches in ([48, 60, 62, 64, 65], [48, 60, 61, 64, 65]):
            self.record(pitches)
        rows = self.app.attempts.get_children()
        self.assertEqual(len(rows), 2)
        self.assertIn("2 attempts", self.app.progress.get())

        self.app.attempts.selection_set(rows[1])
        self.app.show_attempt()
        self.assertIn("Attempt from", self.app.report.get("1.0", "2.0"))

        self.answer = False  # "No" keeps it
        self.app.attempts.selection_set(rows[0])
        self.app.delete_attempt()
        self.assertEqual(len(self.app.attempts.get_children()), 2)
        self.answer = True
        self.app.delete_attempt()
        self.assertEqual(len(self.app.attempts.get_children()), 1)
        self.assertEqual(len(history.load()), 1)

    def test_progress_line_with_many_attempts(self):
        for _ in range(6):
            history.add(self.app.piece, accuracy=80.0, bars="1-2", hands=gui.ALL_PARTS, target_bpm=120,
                        played_bpm=118, wrong=0, missed=0, extra=0, report="r")
        self.app.refresh_history()
        self.assertIn("Last 3 average", self.app.progress.get())

    def test_close_while_recording(self):
        self.app.metronome.set(True)
        self.app.start()
        self.play_notes([48, 60])
        start = time.perf_counter()
        self.app.close()
        self.assertLess(time.perf_counter() - start, 2.0)
        self.assertFalse(self.app.player.playing)

    def test_close_while_listening(self):
        self.app.listen()
        self.pump(0.3)
        self.app.close()
        self.assertFalse(self.app.player.playing)


if __name__ == "__main__":
    unittest.main()
