"""Save practice attempts so progress can be tracked over time.

Attempts are stored in history.json next to this file. Each attempt keeps its
summary numbers and the full report text.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime

HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.json")

# Filled in for attempts that lack them (older or hand-edited entries).
DEFAULTS = {"bars": "?", "hands": "?", "target_bpm": 0, "played_bpm": None, "accuracy": 0.0,
            "wrong": 0, "missed": 0, "extra": 0, "report": "(no report saved)"}


def _key(piece: str) -> str:
    return os.path.normcase(os.path.abspath(piece))


def load() -> list[dict]:
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except (ValueError, UnicodeDecodeError):
        # Damaged (or hand-edited) file: keep it under another name rather
        # than crash or throw it away, and start a fresh history.
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        os.replace(HISTORY_FILE, f"{HISTORY_FILE[:-5]}.damaged-{stamp}.json")
        return []
    if not isinstance(data, list):
        return []
    attempts = [a for a in data if isinstance(a, dict) and {"id", "piece", "when"} <= a.keys()]
    for a in attempts:
        for key, value in DEFAULTS.items():
            a.setdefault(key, value)
    return attempts


def _save(attempts: list[dict]) -> None:
    tmp = HISTORY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(attempts, f, indent=1)
    os.replace(tmp, HISTORY_FILE)  # never leaves a half-written file behind


def add(piece: str, **fields) -> dict:
    """Record an attempt. `piece` is the MIDI file's path; other fields are
    whatever the caller wants to keep (accuracy, tempo, report, ...).
    """
    attempt = {
        "id": uuid.uuid4().hex,
        "piece": _key(piece),
        "when": datetime.now().isoformat(timespec="seconds"),
        **fields,
    }
    attempts = load()
    attempts.append(attempt)
    _save(attempts)
    return attempt


def delete(attempt_id: str) -> None:
    _save([a for a in load() if a["id"] != attempt_id])


def for_piece(piece: str) -> list[dict]:
    """Attempts at one piece, newest first."""
    key = _key(piece)
    return sorted((a for a in load() if a["piece"] == key), key=lambda a: a["when"], reverse=True)
