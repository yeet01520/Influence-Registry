#!/usr/bin/env python3
"""
run_v7_for_cabinet.py
=====================
Runs your existing fetch_fec_data.py (v7) against a tiny members file
containing only Cabinet members with prior elected federal office:
Marco Rubio and JD Vance.

This wrapper:
  1. Injects the FEC API key from the FEC_API_KEY environment variable
     into the v7 script's API_KEY constant at runtime
  2. Temporarily swaps members.json for cabinet_members.json
  3. Runs the v7 main() function unchanged
  4. Restores the original members.json
  5. Renames the output to cabinet_fec_data.json so it doesn't clobber
     your main fec_data_v7.json
"""

import os
import sys
import shutil
import importlib.util

# ── Setup paths ─────────────────────────────────────────────────────────────
SCRIPT_DIR        = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT         = os.path.dirname(SCRIPT_DIR)
DATA_RAW_DIR      = os.path.join(REPO_ROOT, "data", "raw")
V7_SCRIPT_PATH    = os.path.join(SCRIPT_DIR, "fetch_fec_data.py")
CABINET_MEMBERS   = os.path.join(SCRIPT_DIR, "cabinet_members.json")
# v7 expects members.json and writes fec_data_v7.json in its OWN directory
TARGET_MEMBERS    = os.path.join(SCRIPT_DIR, "members.json")
TARGET_OUTPUT     = os.path.join(SCRIPT_DIR, "fec_data_v7.json")
CABINET_OUTPUT    = os.path.join(DATA_RAW_DIR, "cabinet_fec_data.json")

# ── Validate inputs ─────────────────────────────────────────────────────────
if not os.path.exists(V7_SCRIPT_PATH):
    sys.exit(f"ERROR: v7 script not found at {V7_SCRIPT_PATH}")
if not os.path.exists(CABINET_MEMBERS):
    sys.exit(f"ERROR: cabinet_members.json not found at {CABINET_MEMBERS}")

api_key = os.environ.get("FEC_API_KEY")
if not api_key:
    sys.exit("ERROR: FEC_API_KEY environment variable not set")

# ── Backup original members.json ────────────────────────────────────────────
backup_members = None
if os.path.exists(TARGET_MEMBERS):
    backup_members = TARGET_MEMBERS + ".backup"
    shutil.copy(TARGET_MEMBERS, backup_members)
    print(f"Backed up original members.json to {backup_members}")

backup_output = None
if os.path.exists(TARGET_OUTPUT):
    backup_output = TARGET_OUTPUT + ".backup"
    shutil.copy(TARGET_OUTPUT, backup_output)
    print(f"Backed up original fec_data_v7.json to {backup_output}")
    # Remove it so v7 doesn't think it's resuming from a partial run
    os.remove(TARGET_OUTPUT)

try:
    # ── Swap in cabinet_members.json as members.json ────────────────────────
    shutil.copy(CABINET_MEMBERS, TARGET_MEMBERS)
    print(f"Swapped in cabinet_members.json as members.json")

    # ── Symlink required data files from /data/raw/ into /scripts/ ──────────
    # v7 reads these from its own directory. They live in /data/raw/ in
    # this repo, so we make symlinks for the duration of the run.
    required_data_files = [
        "trackaipac_page.txt",
        "Money_from_Oil___Gas_to_US_Representatives__1990-2024.csv",
        "Money_from_Oil___Gas_to_US_Senators__1990-2024.csv",
        "Money_from_Health_to_US_Representatives__1990-2024.csv",
        "Money_from_Health_to_US_Senators__1990-2024.csv",
        "Money_from_Defense_to_US_Representatives__1990-2024.csv",
        "Money_from_Defense_to_US_Senators__1990-2024.csv",
        "Money_from_Finance_Insurance_Real_Estate_to_US_Representatives__1990-2024.csv",
        "Money_from_Finance_Insurance_Real_Estate_to_US_Senators__1990-2024.csv",
        "Money_from_Communications_Electronics_to_US_Representatives__1990-2024.csv",
        "Money_from_Communications_Electronics_to_US_Senators__1990-2024.csv",
    ]
    created_links = []
    for fname in required_data_files:
        src = os.path.join(DATA_RAW_DIR, fname)
        dst = os.path.join(SCRIPT_DIR, fname)
        if os.path.exists(src) and not os.path.exists(dst):
            os.symlink(src, dst)
            created_links.append(dst)
        elif not os.path.exists(src):
            print(f"  ⚠ Missing data file: {src}")
    print(f"Created {len(created_links)} temporary symlinks to data files\n")

    # ── Load v7 script as a module ──────────────────────────────────────────
    spec = importlib.util.spec_from_file_location("fetch_fec_data", V7_SCRIPT_PATH)
    v7   = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v7)

    # ── Inject API key ──────────────────────────────────────────────────────
    v7.API_KEY = api_key
    print(f"Injected FEC_API_KEY into v7 script\n")

    # ── Run v7 main() ───────────────────────────────────────────────────────
    print("=" * 70)
    print("Running v7 fetch_fec_data.main() for Rubio + Vance")
    print("=" * 70)
    v7.main()

    # ── Move output to cabinet-specific filename ────────────────────────────
    if os.path.exists(TARGET_OUTPUT):
        # Make sure /data/raw/ exists for the output
        os.makedirs(DATA_RAW_DIR, exist_ok=True)
        shutil.move(TARGET_OUTPUT, CABINET_OUTPUT)
        print(f"\nMoved output to {CABINET_OUTPUT}")
    else:
        print("\nWARNING: v7 did not produce fec_data_v7.json")

finally:
    # ── Clean up symlinks ───────────────────────────────────────────────────
    for link in created_links:
        try:
            os.unlink(link)
        except Exception:
            pass

    # ── Restore original files ──────────────────────────────────────────────
    if backup_members:
        shutil.move(backup_members, TARGET_MEMBERS)
        print("Restored original members.json")
    elif os.path.exists(TARGET_MEMBERS):
        os.remove(TARGET_MEMBERS)
    if backup_output:
        shutil.move(backup_output, TARGET_OUTPUT)
        print("Restored original fec_data_v7.json")

print("\nDone.")
