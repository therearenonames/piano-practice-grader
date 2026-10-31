"""Saving, listing and deleting practice attempts. Uses a temporary file, never
the real history.json.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import temp_dir  # noqa: E402

import history  # noqa: E402


class TestHistory(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir()
        self.real = history.HISTORY_FILE
        history.HISTORY_FILE = os.path.join(self.dir, "history.json")

    def tearDown(self):
        history.HISTORY_FILE = self.real

    def test_empty_when_no_file(self):
        self.assertEqual(history.load(), [])

    def test_add_and_list(self):
        a = history.add("song.mid", accuracy=90.0)
        b = history.add("song.mid", accuracy=95.0)
        history.add("other.mid", accuracy=50.0)
        got = history.for_piece("song.mid")
        self.assertEqual({x["id"] for x in got}, {a["id"], b["id"]})
        self.assertEqual(len(history.load()), 3)

    def test_newest_first(self):
        history.add("song.mid", accuracy=1.0)
        attempts = history.load()
        attempts[0]["when"] = "2020-01-01T00:00:00"
        history._save(attempts)
        history.add("song.mid", accuracy=2.0)
        self.assertEqual([a["accuracy"] for a in history.for_piece("song.mid")], [2.0, 1.0])

    def test_delete_only_that_one(self):
        a = history.add("song.mid", accuracy=1.0)
        b = history.add("song.mid", accuracy=2.0)
        history.delete(a["id"])
        self.assertEqual([x["id"] for x in history.load()], [b["id"]])

    def test_delete_unknown_id_is_harmless(self):
        history.add("song.mid")
        history.delete("nope")
        self.assertEqual(len(history.load()), 1)

    def test_path_case_and_form_do_not_matter(self):
        history.add(os.path.join(self.dir, "Song.mid"))
        self.assertEqual(len(history.for_piece(os.path.join(self.dir, "SONG.MID"))), 1)
        self.assertEqual(len(history.for_piece(os.path.join(self.dir, ".", "song.mid"))), 1)

    def test_survives_restart(self):
        history.add("song.mid", report="hello", played=[[60, 0.0, 0.5, 0]])
        with open(history.HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data[0]["report"], "hello")
        self.assertEqual(data[0]["played"], [[60, 0.0, 0.5, 0]])

    def test_unicode_report(self):
        history.add("song.mid", report="Bar 1 — ♩ = 95")
        self.assertEqual(history.load()[0]["report"], "Bar 1 — ♩ = 95")

    def test_damaged_file_is_kept_aside_not_crashing(self):
        with open(history.HISTORY_FILE, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(history.load(), [])
        kept = [f for f in os.listdir(self.dir) if "damaged" in f]
        self.assertEqual(len(kept), 1)
        history.add("song.mid")  # and it works again afterwards
        self.assertEqual(len(history.load()), 1)

    def test_wrong_shape_and_missing_fields(self):
        with open(history.HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump([{"id": "1", "piece": "x", "when": "2024-01-01T00:00:00"}, "junk", {"no": "id"}], f)
        attempts = history.load()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["accuracy"], 0.0)  # default filled in
        with open(history.HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump({"not": "a list"}, f)
        self.assertEqual(history.load(), [])

    def test_no_temp_file_left(self):
        history.add("song.mid")
        self.assertFalse(os.path.exists(history.HISTORY_FILE + ".tmp"))


if __name__ == "__main__":
    unittest.main()
