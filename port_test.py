import mido

print("Available ports:", mido.get_input_names())

# Look for the loopMIDI port we created
target_port = 'PythonLoop'

# Find the exact string that matches our target (mido sometimes adds extra numbers to the name)
actual_port = next((p for p in mido.get_input_names() if target_port in p), None)

if actual_port:
    print(f"\nSuccess! Connecting to: {actual_port}...")
    print("Click some keys in VMPK! (Press Ctrl+C to stop)")
    
    try:
        with mido.open_input(actual_port) as inport:
            for msg in inport:
                if msg.type == 'note_on' and msg.velocity > 0:
                    print(f"Hit: Note {msg.note}")
                elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                    print(f"Released: Note {msg.note}")
    except KeyboardInterrupt:
        print("\nPractice session ended.")
else:
    print(f"\nCould not find '{target_port}'. Make sure loopMIDI is running and the port is created!")