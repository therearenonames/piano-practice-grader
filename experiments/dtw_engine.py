from fastdtw import fastdtw

# 1. The Data
# A perfect C Major sequence: C, D, E, F, G
perfect_sequence = [60, 62, 64, 65, 67] 

# The User's playing: 
# They played C and D perfectly, hit an Eb by accident, played F, and then double-tapped G.
user_sequence = [60, 62, 63, 65, 67, 67] 

# 2. Run the Alignment
# We use absolute difference to calculate the "distance" between pitches.
# A distance of 0 means the pitches match perfectly.
distance, path = fastdtw(perfect_sequence, user_sequence, dist=lambda x, y: abs(x - y))

print(f"Mathematical alignment path: {path}\n")
print("--- PERFORMANCE REPORT ---")

# 3. Parse the Path to Grade the Performance
last_i, last_j = -1, -1

for i, j in path:
    # Rule 1: We moved forward in both sequences
    if i > last_i and j > last_j:
        if perfect_sequence[i] == user_sequence[j]:
            print(f"Correct: Played {user_sequence[j]}")
        else:
            print(f"Wrong Note: Expected {perfect_sequence[i]}, but played {user_sequence[j]}")
            
    # Rule 2: The perfect sequence moved forward, but the user sequence stayed stuck
    elif i > last_i and j == last_j:
        print(f"Missed Note: Expected {perfect_sequence[i]}")
        
    # Rule 3: The user sequence moved forward, but the perfect sequence stayed stuck
    elif i == last_i and j > last_j:
        print(f"Extra Note: Accidentally played {user_sequence[j]}")
        
    last_i, last_j = i, j

print("--------------------------")