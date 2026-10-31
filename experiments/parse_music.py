from music21 import corpus, note, chord

print("Loading sheet music...")
score = corpus.parse('bach/bwv295')

perfect_sequence = []

# We now use .flatten().notes instead of .flat.notes
for element in score.flatten().notes:
    
    if isinstance(element, note.Note):
        perfect_sequence.append({
            'pitch': element.pitch.midi,
            'start_time': float(element.offset), # Converted to standard float for easier math later
            'duration': float(element.quarterLength)
        })
        
    elif isinstance(element, chord.Chord):
        for p in element.pitches:
            perfect_sequence.append({
                'pitch': p.midi,
                'start_time': float(element.offset),
                'duration': float(element.quarterLength)
            })

# Sort the sequence by start time
perfect_sequence = sorted(perfect_sequence, key=lambda x: x['start_time'])

print("\nThe Answer Key (First 10 Notes):")
for i, n in enumerate(perfect_sequence[:10]):
    print(f"Note {i+1}: MIDI Pitch {n['pitch']} | Starts at beat {n['start_time']} | Lasts {n['duration']} beats")