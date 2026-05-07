#!/usr/bin/env python3
"""
merge_fec_data.py
=================
Merges Cabinet member entries from cabinet_fec_data.json (v7 output)
into data/fec.json. Existing entries for these names are overwritten
with the fresh v7 data; everything else is preserved.
"""

import json
import os
import sys

SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT     = os.path.dirname(SCRIPT_DIR)
CABINET_DATA  = os.path.join(REPO_ROOT, "data", "raw", "cabinet_fec_data.json")
TARGET_FILE   = os.path.join(REPO_ROOT, "data", "fec.json")

if not os.path.exists(CABINET_DATA):
    sys.exit(f"ERROR: {CABINET_DATA} not found. Did the v7 wrapper run?")

if not os.path.exists(TARGET_FILE):
    sys.exit(f"ERROR: {TARGET_FILE} not found.")

with open(CABINET_DATA) as f:
    new_entries = json.load(f)

with open(TARGET_FILE) as f:
    fec = json.load(f)

before = len(fec)

for name, entry in new_entries.items():
    fec[name] = entry
    print(f"  Merged: {name}")
    # Print key fields so the workflow log shows what got merged
    print(f"    total_raised:  ${entry.get('total_raised', 0):>14,}")
    print(f"    aipac:         ${entry.get('aipac', 0):>14,}")
    print(f"    fossil_fuels:  ${entry.get('fossil_fuels', 0):>14,}")
    print(f"    pharma:        ${entry.get('pharma', 0):>14,}")
    print(f"    defense:       ${entry.get('defense', 0):>14,}")
    print(f"    finance:       ${entry.get('finance', 0):>14,}")
    print(f"    tech:          ${entry.get('tech', 0):>14,}")
    print(f"    grassroots:    ${entry.get('grassroots', 0):>14,}")

with open(TARGET_FILE, "w") as f:
    json.dump(fec, f, indent=2)

print(f"\nDone. {TARGET_FILE} now has {len(fec)} entries (was {before}).")
