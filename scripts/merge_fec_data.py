"""
Reads rubio_vance_fec.json (output from pull_rubio_vance.py) and merges
the entries into data/fec.json. Existing entries for these names are
overwritten with the fresh data; everything else is preserved.
"""

import json
import os
import sys

NEW_DATA_FILE = "rubio_vance_fec.json"
TARGET_FILE   = "data/fec.json"

if not os.path.exists(NEW_DATA_FILE):
    sys.exit(f"ERROR: {NEW_DATA_FILE} not found. Did the pull script run?")

with open(NEW_DATA_FILE) as f:
    new_entries = json.load(f)

with open(TARGET_FILE) as f:
    fec = json.load(f)

before = len(fec)
for name, entry in new_entries.items():
    fec[name] = entry
    print(f"  Merged: {name}")

with open(TARGET_FILE, "w") as f:
    json.dump(fec, f, indent=2)

print(f"\nDone. {TARGET_FILE} now has {len(fec)} entries (was {before}).")
