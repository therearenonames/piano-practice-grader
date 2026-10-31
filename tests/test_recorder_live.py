"""Recording through loopMIDI: what a real piano would send, simulated.

Needs a loopMIDI port with "PythonLoop" in its name; skipped otherwise.
"""

import faulthandler
import os
import subprocess
import sys
import textwrap
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402,F401

import mido  # noqa: E402

from recorder import Recorder  # noqa: E402

LOOP_IN = next((p for p in mido.get_input_names() if "PythonLoop" in p), None)
LOOP_OUT = next((p for p in mido.get_output_names() if "PythonLoop" in p), None)


def on(p, ch=0, v=80):
    return mido.Message("note_on", note=p, velocity=v, channel=ch)


def off(p, ch=0):
    return mido.Message("note_off", note=p, channel=ch)


def pedal(v):
    return mido.Message("control_change", control=64, value=v)


def wait_until(t0, t):
    while time.perf_counter() - t0 < t:
        pass  # busy-wait: sleep() isn't precise enough for timing tests


@unittest.skipUnless(LOOP_IN and LOOP_OUT, "needs a loopMIDI port")
class TestRecorderLive(unittest.TestCase):
    def record(self, script, settle=0.2, **start_kwargs):
        """script: [(seconds, message)]. Returns the recorded notes."""
        rec = Recorder()
        rec.start(LOOP_IN, **start_kwargs)
        with mido.open_output(LOOP_OUT) as out:
            t0 = time.perf_counter()
            for t, msg in script:
                wait_until(t0, t)
                out.send(msg)
            wait_until(t0, (script[-1][0] if script else 0) + settle)
        return rec.stop()

    def test_timing_and_length(self):
        notes = self.record([(0.0, on(60)), (0.3, off(60)), (0.5, on(62)), (1.2, off(62))])
        self.assertEqual([n.pitch for n in notes], [60, 62])
        self.assertAlmostEqual(notes[0].onset_sec, 0.0, delta=0.005)
        self.assertAlmostEqual(notes[1].onset_sec, 0.5, delta=0.005)
        self.assertAlmostEqual(notes[0].duration_sec, 0.3, delta=0.005)
        self.assertAlmostEqual(notes[1].duration_sec, 0.7, delta=0.005)

    def test_velocity_zero_means_release(self):
        notes = self.record([(0.0, on(60)), (0.4, on(60, v=0))])
        self.assertEqual(len(notes), 1)
        self.assertAlmostEqual(notes[0].duration_sec, 0.4, delta=0.005)

    def test_big_chord(self):
        pitches = [36, 43, 48, 52, 55, 60, 64, 67, 72, 76]
        notes = self.record([(0.0, on(p)) for p in pitches] + [(0.5, off(p)) for p in pitches])
        self.assertEqual(sorted(n.pitch for n in notes), pitches)

    def test_fast_burst_nothing_lost(self):
        script = []
        for k in range(300):
            script += [(k * 0.002, on(40 + k % 40)), (k * 0.002 + 0.001, off(40 + k % 40))]
        notes = self.record(script)
        self.assertEqual(len(notes), 300)
        self.assertEqual([n.pitch for n in notes], [40 + k % 40 for k in range(300)])

    def test_other_messages_ignored(self):
        script = [
            (0.00, mido.Message("clock")),
            (0.01, mido.Message("active_sensing")),
            (0.02, mido.Message("sysex", data=[1, 2, 3])),
            (0.03, mido.Message("program_change", program=5)),
            (0.04, mido.Message("pitchwheel", pitch=1000)),
            (0.05, mido.Message("aftertouch", value=50)),
            (0.06, mido.Message("control_change", control=7, value=100)),  # volume knob
            (0.07, off(70)),                                              # release of a key never pressed
            (0.10, on(60)), (0.30, off(60)),
        ]
        notes = self.record(script)
        self.assertEqual([n.pitch for n in notes], [60])

    def test_sustain_pedal(self):
        notes = self.record([
            (0.0, on(60)), (0.1, pedal(127)), (0.3, off(60)), (1.0, pedal(0)),       # held by pedal to 1.0
            (1.2, on(62)), (1.5, off(62)),                                           # no pedal
            (1.6, pedal(127)), (1.7, on(64)), (1.8, off(64)), (2.0, on(64)), (2.1, off(64)),  # re-struck
            (2.4, pedal(0)),
        ])
        got = [(n.pitch, round(n.duration_sec, 2), n.pedalled) for n in notes]
        self.assertEqual(got, [(60, 1.0, True), (62, 0.3, False), (64, 0.3, True), (64, 0.4, True)])

    def test_pedal_threshold(self):
        # 63 is up, 64 is down (half-pedal values around the middle)
        notes = self.record([(0.0, pedal(63)), (0.0, on(60)), (0.2, off(60)), (0.5, pedal(0)),
                             (0.6, pedal(64)), (0.6, on(62)), (0.8, off(62)), (1.1, pedal(0))])
        self.assertEqual([(n.pitch, n.pedalled) for n in notes], [(60, False), (62, True)])
        self.assertAlmostEqual(notes[1].duration_sec, 0.5, delta=0.01)

    def test_key_down_twice_without_release(self):
        notes = self.record([(0.0, on(60)), (0.3, on(60)), (0.5, off(60))])
        self.assertEqual([round(n.duration_sec, 2) for n in notes], [0.3, 0.2])

    def test_keys_still_down_at_stop(self):
        notes = self.record([(0.0, on(60)), (0.1, pedal(127)), (0.1, on(62)), (0.2, off(62))], settle=0.5)
        self.assertAlmostEqual(notes[0].duration_sec, 0.7, delta=0.03)   # key still down
        self.assertAlmostEqual(notes[1].duration_sec, 0.6, delta=0.03)   # pedal still down

    def test_ignore_channels(self):
        script = [(0.0, on(60)), (0.1, off(60)), (0.2, on(76, ch=9)), (0.25, off(76, ch=9))]
        self.assertEqual([n.pitch for n in self.record(script)], [60, 76])
        self.assertEqual([n.pitch for n in self.record(script, ignore_channels=[9])], [60])

    def test_any_channel_counts(self):
        notes = self.record([(0.0, on(60, ch=0)), (0.1, on(48, ch=1)), (0.3, off(60, ch=0)), (0.3, off(48, ch=1))])
        self.assertEqual(sorted(n.pitch for n in notes), [48, 60])

    def test_on_note_callback(self):
        heard = []
        rec = Recorder(on_note=lambda n: heard.append(n.pitch))
        rec.start(LOOP_IN)
        with mido.open_output(LOOP_OUT) as out:
            out.send(on(65))
            time.sleep(0.1)
            out.send(off(65))
            time.sleep(0.1)
        rec.stop()
        self.assertEqual(heard, [65])

    def test_restart_and_double_stop(self):
        rec = Recorder()
        self.assertEqual(rec.stop(), [])  # never started
        for pitch in (60, 62):
            rec.start(LOOP_IN)
            with mido.open_output(LOOP_OUT) as out:
                out.send(on(pitch))
                time.sleep(0.1)
                out.send(off(pitch))
                time.sleep(0.1)
            self.assertEqual([n.pitch for n in rec.stop()], [pitch])  # fresh each time
            rec.stop()

    def test_nothing_played(self):
        self.assertEqual(self.record([], settle=0.2), [])

    def test_missing_port_message(self):
        with self.assertRaises(OSError) as ctx:
            Recorder().start("My Piano 3")
        self.assertIn("not found", str(ctx.exception))

    def test_timing_stays_accurate_when_computer_is_busy(self):
        """Another process plays notes at exact times while this one is kept
        busy; the timestamps come from the MIDI driver, so they should hold."""
        sender = textwrap.dedent(f"""
            import time, mido
            out = mido.open_output({LOOP_OUT!r})
            time.sleep(0.5)
            t0 = time.perf_counter()
            for k in range(20):
                while time.perf_counter() - t0 < k * 0.15: pass
                out.send(mido.Message("note_on", note=60 + k, velocity=80))
                out.send(mido.Message("note_off", note=60 + k))
            out.close()
        """)
        rec = Recorder()
        rec.start(LOOP_IN)
        proc = subprocess.Popen([sys.executable, "-c", sender])
        end = time.perf_counter() + 4.5
        x = 0
        while time.perf_counter() < end:  # hog this process's CPU (and the GIL)
            for k in range(200_000):
                x += k * k
        proc.wait(timeout=10)
        notes = rec.stop()
        self.assertEqual(len(notes), 20)
        errors = [abs(n.onset_sec - k * 0.15) for k, n in enumerate(notes)]
        self.assertLess(max(errors), 0.01, f"worst timing error {max(errors) * 1000:.1f} ms")

    def test_stop_never_freezes_while_notes_arrive(self):
        """Regression: closing the port while messages are arriving used to deadlock."""
        faulthandler.dump_traceback_later(120, exit=True)  # if it does hang, fail loudly instead of forever
        try:
            flood = textwrap.dedent(f"""
                import time, mido
                out = mido.open_output({LOOP_OUT!r})
                end = time.perf_counter() + 8
                while time.perf_counter() < end:
                    out.send(mido.Message("note_on", note=60, velocity=80))
                    out.send(mido.Message("note_off", note=60))
                    time.sleep(0.0005)
                out.close()
            """)
            proc = subprocess.Popen([sys.executable, "-c", flood])
            time.sleep(0.5)
            rec = Recorder()
            for _ in range(25):
                rec.start(LOOP_IN)
                time.sleep(0.05)
                t = time.perf_counter()
                done = threading.Event()
                threading.Thread(target=lambda: (rec.stop(), done.set()), daemon=True).start()
                self.assertTrue(done.wait(3), "stop() hung")
                self.assertLess(time.perf_counter() - t, 1.0)
            proc.wait(timeout=15)
        finally:
            faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    unittest.main()
