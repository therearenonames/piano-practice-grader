import mido
import time
import threading
import os
from fastdtw import fastdtw
from music21 import converter, note, tempo, chord

# ==========================================
# 1. ESTABLISH GROUND TRUTH (SHEET MUSIC)
# ==========================================
print("Loading MIDI file...")

midi_filename = os.path.join("MIDI_Files", "Twinkle Twinkle Little Star (MIDI Version).mid")
score = converter.parse(midi_filename)

embedded_bpm = 120.0
for element in score.flatten():
    if isinstance(element, tempo.MetronomeMark):
        embedded_bpm = element.getQuarterBPM()
        break

TARGET_BPM = embedded_bpm
SECONDS_PER_BEAT = 60.0 / TARGET_BPM

perfect_data = []
perfect_pitches = []
last_offset = 0.0

for n in score.flatten().notes:
    pitch_midi = None

    if isinstance(n, note.Note):
        pitch_midi = n.pitch.midi
    elif isinstance(n, chord.Chord):
        pitch_midi = n[-1].pitch.midi

    if pitch_midi is not None:
        delta_beats = float(n.offset) - last_offset
        duration_beats = float(n.quarterLength)

        perfect_data.append({
            'pitch': pitch_midi,
            'delta_beats': delta_beats,
            'duration_beats': duration_beats
        })
        perfect_pitches.append(pitch_midi)
        last_offset = float(n.offset)

print(f"\nTarget Tempo: {TARGET_BPM:.1f} BPM ({SECONDS_PER_BEAT:.2f} sec/beat)")
print(f"Goal: Play these {len(perfect_pitches)} notes: {perfect_pitches}")

# ==========================================
# 2. CAPTURE USER PERFORMANCE (BACKGROUND THREAD)
# ==========================================
target_port = 'PythonLoop'
actual_port = next((p for p in mido.get_input_names() if target_port in p), None)

user_data = []
user_pitches = []
recording = True

def record_midi(port_name):
    global recording
    last_time = None
    active_notes = {}

    with mido.open_input(port_name) as inport:
        while recording:
            msg = inport.poll()
            if msg:
                current_time = time.time()

                # Key Pressed Down
                if msg.type == 'note_on' and msg.velocity > 0:
                    delta_sec = 0.0 if last_time is None else current_time - last_time
                    last_time = current_time

                    active_notes[msg.note] = current_time
                    user_data.append({'pitch': msg.note, 'delta_sec': delta_sec, 'duration_sec': 0.0})
                    user_pitches.append(msg.note)
                    print(f"Heard: {msg.note} (Total played: {len(user_pitches)})")

                # Key Released
                elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                    if msg.note in active_notes:
                        held_time = current_time - active_notes[msg.note]

                        for d in reversed(user_data):
                            if d['pitch'] == msg.note and d['duration_sec'] == 0.0:
                                d['duration_sec'] = held_time
                                break

                        del active_notes[msg.note]

            time.sleep(0.001)

if actual_port:
    print(f"\nConnecting to: {actual_port}...")

    midi_thread = threading.Thread(target=record_midi, args=(actual_port,), daemon=True)
    midi_thread.start()

    input("Press ENTER at any time to stop recording and grade performance...\n")
    recording = False
    midi_thread.join()

    # ==========================================
    # 3. THE GRADING ENGINE (LOOKAHEAD + GATE TIME)
    # ==========================================
    if not user_pitches:
        print("No notes were recorded!")
    else:
        print("\nCalculating grade...")

        ONSET_TOLERANCE = SECONDS_PER_BEAT * 0.25
        GATE_FACTOR = 0.80
        RELEASE_TOLERANCE = SECONDS_PER_BEAT * 0.35

        distance, path = fastdtw(perfect_pitches, user_pitches, dist=lambda x, y: abs(x - y))

        print("\n--- PERFORMANCE REPORT ---")

        idx_p = 0
        idx_u = 0

        while idx_p < len(perfect_pitches) or idx_u < len(user_pitches):
            if idx_p >= len(perfect_pitches):
                print(f"Extra Note: Accidentally played {user_pitches[idx_u]}")
                idx_u += 1
                continue

            if idx_u >= len(user_pitches):
                print(f"Missed Note: Expected {perfect_pitches[idx_p]}")
                idx_p += 1
                continue

            expected = perfect_pitches[idx_p]
            actual = user_pitches[idx_u]

            # Direct Match
            if expected == actual:
                # 1. Onset Timing Check
                if idx_p > 0 and idx_u > 0:
                    expected_time = perfect_data[idx_p]['delta_beats'] * SECONDS_PER_BEAT
                    actual_time = user_data[idx_u]['delta_sec']
                    diff = actual_time - expected_time

                    if diff > ONSET_TOLERANCE:
                        timing_msg = f"Dragged (Late by {diff:.2f}s)"
                    elif diff < -ONSET_TOLERANCE:
                        timing_msg = f"Rushed (Early by {abs(diff):.2f}s)"
                    else:
                        timing_msg = "Perfect timing"
                else:
                    timing_msg = "(Starting Note)"

                # 2. Hold Duration Check (80% Gate Factor)
                target_hold_time = (perfect_data[idx_p]['duration_beats'] * SECONDS_PER_BEAT) * GATE_FACTOR
                actual_hold_time = user_data[idx_u]['duration_sec']
                dur_diff = actual_hold_time - target_hold_time

                if dur_diff > RELEASE_TOLERANCE:
                    dur_msg = f"Held too long (+{dur_diff:.2f}s)"
                elif dur_diff < -RELEASE_TOLERANCE:
                    dur_msg = f"Released early ({dur_diff:.2f}s)"
                else:
                    dur_msg = "Perfect hold length"

                print(f"Correct: Played {actual} -- {timing_msg} | {dur_msg}")
                idx_p += 1
                idx_u += 1

            # Lookahead 1: User played an extra wrong note before the right note (Flub)
            elif idx_u + 1 < len(user_pitches) and user_pitches[idx_u + 1] == expected:
                print(f"Extra/Wrong Note: Flubbed {actual}")
                user_data[idx_u + 1]['delta_sec'] += user_data[idx_u]['delta_sec']
                idx_u += 1

            # Lookahead 2: User skipped a note entirely and played the NEXT expected note
            elif idx_p + 1 < len(perfect_pitches) and perfect_pitches[idx_p + 1] == actual:
                print(f"Missed Note: Skipped {expected}")
                idx_p += 1  # Advance sheet music index without consuming user's keypress

            # General Wrong Note
            else:
                print(f"Wrong Note: Expected {expected}, but played {actual}")
                idx_p += 1
                idx_u += 1

        print("--------------------------")
        print(f"Evaluated Target Tempo: {TARGET_BPM:.1f} BPM")
        print("--------------------------")

else:
    print(f"Could not find '{target_port}'. Make sure loopMIDI is running!")
