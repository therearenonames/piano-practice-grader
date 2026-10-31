"""Simple window for picking a MIDI file, practising it, and seeing the report.

    python gui.py
"""

from __future__ import annotations

import os
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

import mido

import history
from grader import grade, summarize
from playback import CLICK_CHANNEL, Player, click_events, count_in_beats, merge, piece_events
from recorder import Recorder
from report import format_report
from sheet import Sheet, load_sheet

DEFAULT_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "MIDI_Files")
DEFAULT_INPUT = "PythonLoop"
DEFAULT_OUTPUT = "Microsoft GS Wavetable"
MIDI_EXTENSIONS = (".mid", ".midi")
ALL_PARTS = "Both hands / all parts"
TITLE = "Piano Practice"


def _device_name(port: str) -> str:
    """'PythonLoop 0' -> 'PythonLoop'. Windows numbers each port of a device,
    so a device's input and output differ only in that number.
    """
    base, _, number = port.rpartition(" ")
    return base if number.isdigit() else port


class PracticeApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.recorder = Recorder()
        self.player = Player()
        self.sheets: dict[str, Sheet] = {}  # loaded pieces, by path
        self.piece: str | None = None       # path of the selected piece
        self.practice: Sheet | None = None  # the bars/hands being practised right now
        self.practice_piece = ""            # ...and its file and hands, as they were at Start
        self.practice_hands = ""
        self.mode = "idle"                  # "idle", "listening" or "recording"
        self.music_starts_at = 0.0          # perf_counter() time the count-in ends

        self.folder = tk.StringVar(value=DEFAULT_FOLDER)
        self.in_port = tk.StringVar()
        self.out_port = tk.StringVar()
        self.bpm = tk.StringVar()
        self.first_bar = tk.StringVar()
        self.last_bar = tk.StringVar()
        self.hands = tk.StringVar(value=ALL_PARTS)
        self.count_in = tk.BooleanVar(value=True)
        self.metronome = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Pick a piece.")

        root.title(TITLE)

        # Folder + files
        row = self._row()
        tk.Label(row, text="Folder:").pack(side="left")
        tk.Entry(row, textvariable=self.folder).pack(side="left", fill="x", expand=True)
        tk.Button(row, text="Browse...", command=self.browse).pack(side="left")
        tk.Button(row, text="Refresh", command=self.refresh_files).pack(side="left")

        self.files = tk.Listbox(root, height=6, exportselection=False)
        self.files.pack(fill="x", padx=5)
        self.files.bind("<<ListboxSelect>>", lambda e: self.select_piece())

        # Ports
        row = self._row()
        tk.Label(row, text="MIDI input:").pack(side="left")
        self.in_menu = tk.OptionMenu(row, self.in_port, "")
        self.in_menu.pack(side="left")
        tk.Label(row, text="Sound output:").pack(side="left", padx=(10, 0))
        self.out_menu = tk.OptionMenu(row, self.out_port, "")
        self.out_menu.pack(side="left")
        tk.Button(row, text="Refresh ports", command=self.refresh_ports).pack(side="left")

        # Practice settings
        row = self._row()
        tk.Label(row, text="Tempo (BPM):").pack(side="left")
        tk.Entry(row, textvariable=self.bpm, width=6).pack(side="left")
        tk.Label(row, text="Bars:").pack(side="left", padx=(10, 0))
        self.first_spin = tk.Spinbox(row, textvariable=self.first_bar, width=4, from_=1, to=1)
        self.first_spin.pack(side="left")
        tk.Label(row, text="to").pack(side="left")
        self.last_spin = tk.Spinbox(row, textvariable=self.last_bar, width=4, from_=1, to=1)
        self.last_spin.pack(side="left")
        tk.Label(row, text="Hands:").pack(side="left", padx=(10, 0))
        self.hands_menu = tk.OptionMenu(row, self.hands, ALL_PARTS)
        self.hands_menu.pack(side="left")

        # Controls
        row = self._row()
        tk.Checkbutton(row, text="Count-in", variable=self.count_in).pack(side="left")
        tk.Checkbutton(row, text="Metronome", variable=self.metronome).pack(side="left")
        self.listen_button = tk.Button(row, text="Listen", command=self.listen)
        self.listen_button.pack(side="left", padx=(20, 0))
        self.start_button = tk.Button(row, text="Start", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = tk.Button(row, text="Stop", command=self.stop, state="disabled")
        self.stop_button.pack(side="left")

        tk.Label(root, textvariable=self.status, anchor="w").pack(fill="x", padx=5)

        # Report / History tabs
        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill="both", expand=True, padx=5, pady=5)

        # Fixed-width font so the report's columns line up
        self.report = scrolledtext.ScrolledText(self.tabs, width=90, height=24, font="TkFixedFont")
        self.tabs.add(self.report, text="Report")

        tab = tk.Frame(self.tabs)
        self.tabs.add(tab, text="History")
        self.progress = tk.StringVar()
        tk.Label(tab, textvariable=self.progress, anchor="w").pack(fill="x")
        columns = ("when", "bars", "hands", "tempo", "accuracy", "mistakes")
        self.attempts = ttk.Treeview(tab, columns=columns, show="headings", selectmode="browse", height=12)
        for col, heading, width in [
            ("when", "When", 140), ("bars", "Bars", 60), ("hands", "Hands", 150),
            ("tempo", "Played / target BPM", 130), ("accuracy", "Correct", 70),
            ("mistakes", "Wrong / missed / extra", 140),
        ]:
            self.attempts.heading(col, text=heading)
            self.attempts.column(col, width=width, anchor="w")
        self.attempts.pack(fill="both", expand=True)
        self.attempts.bind("<Double-1>", lambda e: self.show_attempt())
        row = tk.Frame(tab)
        row.pack(fill="x")
        tk.Button(row, text="Show report", command=self.show_attempt).pack(side="left")
        tk.Button(row, text="Delete", command=self.delete_attempt).pack(side="left")

        self.refresh_ports()
        self.refresh_files()
        root.protocol("WM_DELETE_WINDOW", self.close)

    def _row(self) -> tk.Frame:
        row = tk.Frame(self.root)
        row.pack(fill="x", padx=5, pady=3)
        return row

    # -- folder / files -----------------------------------------------------

    def browse(self) -> None:
        if self.mode != "idle":
            return
        folder = filedialog.askdirectory(initialdir=self.folder.get())
        if folder:
            self.folder.set(folder)
            self.refresh_files()

    def refresh_files(self) -> None:
        if self.mode != "idle":
            return  # don't swap the piece out from under a recording
        self.files.delete(0, "end")
        folder = self.folder.get()
        if not os.path.isdir(folder):
            self._no_piece(f"Folder not found: {folder}")
            return
        names = sorted(f for f in os.listdir(folder) if f.lower().endswith(MIDI_EXTENSIONS))
        for name in names:
            self.files.insert("end", name)
        if names:
            self.files.selection_set(0)
            self.select_piece()
        else:
            self._no_piece("No MIDI files in this folder.")

    def _no_piece(self, message: str) -> None:
        """Nothing usable is selected; make sure Start/Listen don't use an old piece."""
        self.piece = None
        self.status.set(message)
        self.refresh_history()

    def select_piece(self) -> None:
        """Load the highlighted piece and reset the practice settings to match it."""
        selection = self.files.curselection()
        if not selection or self.mode != "idle":
            return
        path = os.path.join(self.folder.get(), self.files.get(selection[0]))
        if path not in self.sheets:
            self.status.set("Loading piece...")
            self.root.update_idletasks()
            try:
                self.sheets[path] = load_sheet(path)
            except Exception as e:  # not a valid MIDI file, etc.
                self._no_piece(f"Couldn't load {os.path.basename(path)}: {e}")
                return
        self.piece = path
        sheet = self.sheets[path]

        first, last = sheet.all_bars[0].number, sheet.all_bars[-1].number
        for spin in (self.first_spin, self.last_spin):
            spin.config(from_=first, to=last)
        self.first_bar.set(str(first))
        self.last_bar.set(str(last))
        self.bpm.set(f"{sheet.bpm:g}")

        menu = self.hands_menu["menu"]
        menu.delete(0, "end")
        for name in [ALL_PARTS] + (sheet.part_names if len(sheet.part_names) > 1 else []):
            menu.add_command(label=name, command=lambda n=name: self.hands.set(n))
        self.hands.set(ALL_PARTS)

        self.status.set(f"{os.path.basename(path)}: {len(sheet.notes)} notes, bars {first}-{last}, "
                        f"{sheet.bpm:g} BPM. Listen first, or press Start and play.")
        self.refresh_history()

    def practice_sheet(self) -> Sheet | None:
        """The piece narrowed to the chosen bars, hands and tempo, or None (with a message) if invalid."""
        if self.piece is None:
            messagebox.showinfo(TITLE, "Pick a MIDI file first.")
            return None
        sheet = self.sheets[self.piece]
        try:
            bpm = float(self.bpm.get())
            first, last = int(self.first_bar.get()), int(self.last_bar.get())
        except ValueError:
            messagebox.showinfo(TITLE, "Tempo and bars need to be numbers.")
            return None
        numbers = [b.number for b in sheet.all_bars]
        if bpm <= 0 or first not in numbers or last not in numbers or first > last:
            messagebox.showinfo(TITLE, f"Pick a tempo above 0 and bars between {numbers[0]} and {numbers[-1]}.")
            return None
        hands = self.hands.get()
        part = sheet.part_names.index(hands) if hands in sheet.part_names else None
        selected = sheet.select(first, last, part=part, bpm=bpm)
        if not selected.notes:
            messagebox.showinfo(TITLE, "There are no notes to play in those bars for that hand.")
            return None
        return selected

    # -- ports --------------------------------------------------------------

    def refresh_ports(self) -> None:
        inputs, outputs = mido.get_input_names(), mido.get_output_names()
        self._fill_menu(self.in_menu, self.in_port, inputs, DEFAULT_INPUT)
        # Default to the built-in synth, and never to the loopMIDI port by choice.
        preferred_out = next((p for p in outputs if DEFAULT_OUTPUT in p), None)
        if preferred_out is None:
            preferred_out = next((p for p in outputs if DEFAULT_INPUT not in p), "")
        self._fill_menu(self.out_menu, self.out_port, outputs, preferred_out)
        if not inputs:
            self.status.set("No MIDI inputs found. Is your piano (or loopMIDI) connected?")

    @staticmethod
    def _fill_menu(menu_button: tk.OptionMenu, var: tk.StringVar, ports: list[str], preferred: str) -> None:
        menu = menu_button["menu"]
        menu.delete(0, "end")
        for p in ports:
            menu.add_command(label=p, command=lambda p=p: var.set(p))
        if var.get() in ports:
            return  # keep what the user picked, e.g. after plugging in a piano and refreshing
        var.set(next((p for p in ports if preferred and preferred in p), ports[0] if ports else ""))

    def _need_output(self) -> bool:
        if self.out_port.get():
            return True
        messagebox.showinfo(TITLE, "No sound output selected.")
        return False

    # -- listen / record ----------------------------------------------------

    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        idle = mode == "idle"
        self.listen_button.config(state="normal" if idle else "disabled")
        self.start_button.config(state="normal" if idle else "disabled")
        self.stop_button.config(state="disabled" if idle else "normal")
        self.files.config(state="normal" if idle else "disabled")  # no switching pieces mid-take

    def _lead_in(self, sheet: Sheet) -> float:
        return count_in_beats(sheet) * sheet.seconds_per_beat if self.count_in.get() else 0.0

    def listen(self) -> None:
        sheet = self.practice_sheet()
        if sheet is None or not self._need_output():
            return
        lead = self._lead_in(sheet)
        events = merge(piece_events(sheet, lead), click_events(sheet, self.count_in.get(), self.metronome.get()))
        try:
            self.player.play(self.out_port.get(), events)
        except Exception as e:
            messagebox.showerror(TITLE, f"Couldn't play:\n{e}")
            return
        self.practice = sheet
        self.music_starts_at = time.perf_counter() + lead
        self._set_mode("listening")
        self.poll()

    def start(self) -> None:
        sheet = self.practice_sheet()
        if sheet is None:
            return
        if not self.in_port.get():
            messagebox.showinfo(TITLE, "No MIDI input selected. Is your piano (or loopMIDI) connected?")
            return
        clicks = self.count_in.get() or self.metronome.get()
        if clicks and not self._need_output():
            return
        # If the sound output feeds straight back into the input (loopMIDI),
        # the metronome's clicks would be recorded as notes, so drop them.
        # With a real piano, nothing is dropped.
        loops_back = clicks and _device_name(self.out_port.get()) == _device_name(self.in_port.get())
        try:
            self.recorder.start(self.in_port.get(), ignore_channels=[CLICK_CHANNEL] if loops_back else [])
            if clicks:
                self.player.play(self.out_port.get(),
                                 click_events(sheet, self.count_in.get(), self.metronome.get(), keep_going=True))
        except Exception as e:  # port in use, etc.
            self.recorder.stop()
            messagebox.showerror(TITLE, f"Couldn't start:\n{e}")
            return

        self.practice = sheet
        self.practice_piece, self.practice_hands = self.piece, self.hands.get()
        self.music_starts_at = time.perf_counter() + self._lead_in(sheet)
        self.report.delete("1.0", "end")
        self.tabs.select(0)
        self._set_mode("recording")
        self.poll()

    def poll(self) -> None:
        """Keep the status line up to date while listening or recording."""
        if self.mode == "idle":
            return
        countdown = self.music_starts_at - time.perf_counter()
        if countdown > 0:
            beats_left = int(countdown / self.practice.seconds_per_beat) + 1
            self.status.set(f"Count-in... {beats_left}")
        elif self.mode == "listening":
            if not self.player.playing:
                self._set_mode("idle")
                self.status.set("Finished playing. Press Start when you're ready.")
                return
            self.status.set("Playing the piece...")
        else:
            self.status.set(f"Recording at {self.practice.bpm:g} BPM... "
                            f"{len(self.recorder.notes)} notes heard. Press Stop when done.")
        self.root.after(100, self.poll)

    def stop(self) -> None:
        mode = self.mode
        self.player.stop()
        self._set_mode("idle")
        if mode != "recording":
            self.status.set("Stopped.")
            return

        played = self.recorder.stop()
        if not played:
            self.status.set("No notes were recorded.")
            return

        sheet = self.practice
        results = grade(sheet, played)
        text = format_report(sheet, results)
        self.report.delete("1.0", "end")
        self.report.insert("1.0", text)

        s = summarize(sheet, results)
        history.add(
            self.practice_piece,
            bars=f"{sheet.bars[0].number}-{sheet.bars[-1].number}",
            hands=self.practice_hands,
            target_bpm=sheet.bpm,
            played_bpm=s.played_bpm,
            accuracy=s.accuracy,
            correct=s.correct,
            attempted=s.attempted,
            wrong=s.wrong,
            missed=s.missed,
            extra=s.extra,
            report=text,
            # The raw notes, so attempts can be re-graded if the grader is tuned later:
            # [pitch, onset sec, duration sec, pedalled]
            played=[[n.pitch, round(n.onset_sec, 3), round(n.duration_sec, 3), int(n.pedalled)] for n in played],
        )
        self.refresh_history()
        self.status.set(f"Graded {len(played)} notes: {s.accuracy:.0f}% correct. Saved to history.")

    # -- history ------------------------------------------------------------

    def refresh_history(self) -> None:
        self.attempts.delete(*self.attempts.get_children())
        attempts = history.for_piece(self.piece) if self.piece else []
        for a in attempts:
            played = f"{a['played_bpm']:.0f}" if a.get("played_bpm") else "?"
            self.attempts.insert("", "end", iid=a["id"], values=(
                a["when"].replace("T", " "),
                a["bars"],
                a["hands"],
                f"{played} / {a['target_bpm']:g}",
                f"{a['accuracy']:.0f}%",
                f"{a['wrong']} / {a['missed']} / {a['extra']}",
            ))

        if not attempts:
            self.progress.set("No attempts at this piece yet.")
            return
        scores = [a["accuracy"] for a in reversed(attempts)]  # oldest first
        line = f"{len(scores)} attempts.  First: {scores[0]:.0f}%   Latest: {scores[-1]:.0f}%   Best: {max(scores):.0f}%"
        if len(scores) >= 6:
            recent, before = scores[-3:], scores[-6:-3]
            line += f"   Last 3 average: {sum(recent) / 3:.0f}% (3 before that: {sum(before) / 3:.0f}%)"
        self.progress.set(line)

    def _selected_attempt(self) -> dict | None:
        selection = self.attempts.selection()
        if not selection:
            messagebox.showinfo(TITLE, "Pick an attempt in the list first.")
            return None
        return next((a for a in history.load() if a["id"] == selection[0]), None)

    def show_attempt(self) -> None:
        attempt = self._selected_attempt()
        if attempt is None:
            return
        self.report.delete("1.0", "end")
        self.report.insert("1.0", f"Attempt from {attempt['when'].replace('T', ' ')}\n\n{attempt['report']}")
        self.tabs.select(0)

    def delete_attempt(self) -> None:
        attempt = self._selected_attempt()
        if attempt is None:
            return
        when = attempt["when"].replace("T", " ")
        if messagebox.askyesno(TITLE, f"Delete the attempt from {when} ({attempt['accuracy']:.0f}%)?"):
            history.delete(attempt["id"])
            self.refresh_history()

    def close(self) -> None:
        self.player.stop()
        self.recorder.stop()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    PracticeApp(root)
    root.mainloop()
