import sys

server_path = '/Users/brucenguyen/vr_games_ai_agent/mcp_server/server.py'
append_path = '/Users/brucenguyen/vr_games_ai_agent/scratch_append_messaging.py'

with open(server_path, 'r') as f:
    server_lines = f.readlines()

with open(append_path, 'r') as f:
    append_lines = f.readlines()

# Search backwards for "Entry point"
entry_idx = -1
for i in range(len(server_lines) - 1, -1, -1):
    if "Entry point" in server_lines[i]:
        entry_idx = i - 1  # Get the line before (# ----------------)
        break

if entry_idx != -1:
    new_server_lines = server_lines[:entry_idx] + ["\n"] + append_lines + ["\n"] + server_lines[entry_idx:]
    with open(server_path, 'w') as f:
        f.writelines(new_server_lines)
    print("Successfully appended messaging tools.")
else:
    print("Could not find entry point.")
