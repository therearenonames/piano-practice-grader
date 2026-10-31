"""The terminal version (app.py), run as a separate program."""

import os
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import ROOT, TWINKLE, temp_dir, write_midi  # noqa: E402

import mido  # noqa: E402

LOOP_IN = next((p for p in mido.get_input_names() if "PythonLoop" in p), None)
LOOP_OUT = next((p for p in mido.get_output_names() if "PythonLoop" in p), None)


def run(*args, stdin=""):
    return subprocess.run([sys.executable, "app.py", *args], cwd=ROOT, input=stdin,
                          capture_output=True, text=True, timeout=120)


class TestCli(unittest.TestCase):
    def test_list_ports(self):
        r = run("--list-ports")
        self.assertEqual(r.returncode, 0)
        if LOOP_IN:
            self.assertIn(LOOP_IN, r.stdout)

    def test_missing_file(self):
        r = run("nope.mid")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not found", r.stdout)

    def test_broken_file(self):
        path = os.path.join(temp_dir(), "broken.mid")
        with open(path, "w") as f:
            f.write("nope")
        r = run(path, "--port", LOOP_IN or "x")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Couldn't load", r.stdout)
        self.assertNotIn("Traceback", r.stderr)

    def test_empty_file(self):
        r = run(write_midi(os.path.join(temp_dir(), "empty.mid"), []), "--port", LOOP_IN or "x")
        self.assertEqual(r.returncode, 1)

    def test_bad_port(self):
        r = run(TWINKLE, "--port", "No Such Piano")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Could not find", r.stdout)

    @unittest.skipUnless(LOOP_IN and LOOP_OUT, "needs a loopMIDI port")
    def test_full_run(self):
        proc = subprocess.Popen([sys.executable, "app.py", TWINKLE, "--port", "PythonLoop", "--bpm", "120"],
                                cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        # wait for it to be listening
        line = ""
        while "Recording" not in line:
            line = proc.stdout.readline()
            self.assertNotEqual(line, "", "app.py exited early")
        with mido.open_output(LOOP_OUT) as out:
            for p in [60, 60, 67, 67, 69, 69, 67]:
                out.send(mido.Message("note_on", note=p, velocity=80))
                time.sleep(0.4)
                out.send(mido.Message("note_off", note=p))
                time.sleep(0.1)
        time.sleep(0.2)
        output, _ = proc.communicate("\n", timeout=60)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Correct notes: 7/7 (100%)", output)
        self.assertIn("target 120 BPM", output)


if __name__ == "__main__":
    unittest.main()
