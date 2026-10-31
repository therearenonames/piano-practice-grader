"""Piano practice grader.

Listens to what you play on a MIDI port and compares it to a MIDI file of
the piece (one or both hands, chords included). It reports wrong, missed and
extra notes, whether each note was early, late, or held for the right
length, and which bars were rushed or dragged.

    python gui.py                       # the window version
    python app.py                       # default piece, 'PythonLoop' port
    python app.py song.mid --bpm 90     # practise slower than written
    python app.py --list-ports
"""

from __future__ import annotations

import argparse
import os
import sys

import mido

from grader import grade
from recorder import find_port, record
from report import print_report
from sheet import load_sheet

DEFAULT_MIDI = os.path.join("MIDI_Files", "Twinkle Twinkle Little Star (MIDI Version).mid")
DEFAULT_PORT = "PythonLoop"


def main() -> int:
    parser = argparse.ArgumentParser(description="Grade a piano performance against a MIDI file.")
    parser.add_argument("midi_file", nargs="?", default=DEFAULT_MIDI, help="the piece to practise")
    parser.add_argument("--port", default=DEFAULT_PORT, help="MIDI input port name (or part of it)")
    parser.add_argument("--bpm", type=float, help="override the tempo stored in the MIDI file")
    parser.add_argument("--list-ports", action="store_true", help="show available MIDI inputs and exit")
    args = parser.parse_args()

    if args.list_ports:
        print("\n".join(mido.get_input_names()) or "No MIDI input ports found.")
        return 0

    if not os.path.exists(args.midi_file):
        print(f"MIDI file not found: {args.midi_file}")
        return 1

    port = find_port(args.port)
    if port is None:
        print(f"Could not find a MIDI input matching '{args.port}'. Is loopMIDI running?")
        print("Available inputs:", mido.get_input_names() or "none")
        return 1

    print(f"Loading {args.midi_file}...")
    try:
        sheet = load_sheet(args.midi_file, bpm=args.bpm)
    except Exception as e:
        print(f"Couldn't load {args.midi_file}: {e}")
        return 1
    if not sheet.notes:
        print("That file has no notes to play.")
        return 1
    print(f"Target tempo: {sheet.bpm:.0f} BPM, {len(sheet.notes)} notes in {len(sheet.bars)} bars")

    print(f"\nListening on {port}")
    try:
        played = record(port)
    except OSError as e:
        print(e)
        return 1

    if not played:
        print("No notes were recorded.")
        return 1

    print_report(sheet, grade(sheet, played))
    return 0


if __name__ == "__main__":
    sys.exit(main())
