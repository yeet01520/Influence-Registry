#!/usr/bin/env python3
"""
run_v7_for_all.py
==================
Runs the v7 fetch_fec_data.py against the full members.json (all 538 current
members of Congress). Parallel to run_v7_for_cabinet.py but without the
members.json swap.

This wrapper:
  1. Injects the FEC API key from the FEC_API_KEY environment variable
     into the v7 script's API_KEY constant at runtime
  2. Creates temporary symlinks from /data/raw/ for the CSV / text files
     that v7 expects to find in its own directory
  3. Runs the v7 main() function unchanged (uses the existing members.json)
  4. Post-processes known problematic names (e.g. JD Vance, where the
     normalize step filters "JD" away as too short)
  5. Writes output to data/raw/all_fec_data.json

The fetcher will iterate through every member in members.json and call
the FEC API to resolve their candidate IDs, pull committees, and gather
receipts. Estimated runtime: 30-90 minutes depending on FEC API latency
and how many cycles each member has filed for.
"""

import os
import sys
import json
import shutil
import importlib.util

# ── Setup paths ─────────────────────────────────────────────────────────────
SCRIPT_DIR     = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT      = os.path.dirname(SCRIPT_DIR)
DATA_RAW_DIR   = os.path.join(REPO_ROOT, "data", "raw")
V7_SCRIPT_PATH = os.path.join(SCRIPT_DIR, "fetch_fec_data.py")
# v7 expects members.json and writes fec_data_v7.json in its OWN directory
TARGET_MEMBERS = os.path.join(SCRIPT_DIR, "members.json")
TARGET_OUTPUT  = os.path.join(SCRIPT_DIR, "fec_data_v7.json")
FINAL_OUTPUT   = os.path.join(DATA_RAW_DIR, "all_fec_data.json")

# ── Validate inputs ─────────────────────────────────────────────────────────
if not os.path.exists(V7_SCRIPT_PATH):
    sys.exit(f"ERROR: v7 script not found at {V7_SCRIPT_PATH}")
if not os.path.exists(TARGET_MEMBERS):
    sys.exit(f"ERROR: members.json not found at {TARGET_MEMBERS}. "
             f"This wrapper expects the full-Congress members file at {TARGET_MEMBERS}.")

api_key = os.environ.get("FEC_API_KEY")
if not api_key:
    sys.exit("ERROR: FEC_API_KEY environment variable not set")

# Verify members.json contains a sensible number of members
try:
    with open(TARGET_MEMBERS) as f:
        members_preview = json.load(f)
    count = len(members_preview) if isinstance(members_preview, list) else len(members_preview.get("members", []))
    print(f"members.json contains {count} entries")
    if count < 100:
        print(f"  ⚠ WARNING: only {count} members. Expected ~538 for full Congress.")
        print(f"     If this is intentional, ignore. Otherwise check that members.json")
        print(f"     has not been replaced by a smaller file (e.g. cabinet_members.json).")
except Exception as e:
    sys.exit(f"ERROR: could not parse members.json: {e}")

# ── Backup any existing output so we don't confuse v7's resume logic ─────────
backup_output = None
if os.path.exists(TARGET_OUTPUT):
    backup_output = TARGET_OUTPUT + ".backup"
    shutil.copy(TARGET_OUTPUT, backup_output)
    print(f"Backed up existing fec_data_v7.json to {backup_output}")
    os.remove(TARGET_OUTPUT)

created_links = []
try:
    # ── Symlink required data files from /data/raw/ into /scripts/ ──────────
    # v7 reads these from its own directory. They live in /data/raw/ in this
    # repo, so we make symlinks for the duration of the run.
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

    # ── Run v7 main() against the full members list ─────────────────────────
    print("=" * 70)
    print(f"Running v7 fetch_fec_data.main() for all {count} members")
    print("Expected runtime: 30-90 minutes")
    print("=" * 70)
    v7.main()

    # ── POST-PROCESSING: Fix known names where the >=2 token rule fails ────
    # The fixed matcher in fetch_fec_data.py requires 2 name-token overlap to
    # prevent surname-only collisions (Adelita/Raul Grijalva style). This is
    # safe but means single-char first names like "JD Vance" can't match
    # because "JD" is filtered out by the >1-char token rule, leaving only
    # {"vance"} which is 1 token.
    #
    # The post-processing logic from run_v7_for_cabinet.py is applied here
    # for the same set of edge-case names. Add more entries to KNOWN_VARIANTS
    # as you encounter them.
    KNOWN_VARIANTS = {
        # FEC display name -> [variants to retry, full state name]
        "JD Vance": (["James David Vance", "James Vance", "James D Vance",
                      "Vance JD", "Vance James"], "Ohio"),
        # Add other tricky names here. Example:
        # "AOC": (["Alexandria Ocasio-Cortez", "Ocasio-Cortez Alexandria"], "New York"),
    }

    if os.path.exists(TARGET_OUTPUT):
        with open(TARGET_OUTPUT) as f:
            v7_output = json.load(f)

        for display_name, (variants, full_state) in KNOWN_VARIANTS.items():
            if display_name not in v7_output:
                continue
            entry = v7_output[display_name]
            print(f"\n  Post-processing {display_name} ({full_state})...")

            sector_funcs = {
                "fossil_fuels": v7.get_oil_amount,
                "pharma":       v7.get_pharma_amount,
                "defense":      v7.get_defense_amount,
                "finance":      v7.get_finance_amount,
                "tech":         v7.get_tech_amount,
            }

            for sector, func in sector_funcs.items():
                if entry.get(sector, 0) == 0:
                    matched = False
                    for variant in variants:
                        # Use full state name, not abbreviation — v7's state-
                        # matching does `csv_state in norm_state` substring
                        # check and CSVs store full state names.
                        amt = func(variant, full_state)
                        if amt > 0:
                            entry[sector] = amt
                            print(f"    {sector}: matched '{variant}' -> ${amt:,}")
                            matched = True
                            break
                    if not matched:
                        print(f"    {sector}: no variant matched (still $0)")

            # AIPAC from TrackAIPAC
            if entry.get("aipac", 0) == 0:
                matched = False
                for variant in variants:
                    ta = v7.get_aipac_from_trackaipac(variant)
                    if ta and ta.get("total", 0) > 0:
                        entry["aipac"]              = ta["total"]
                        entry["aipac_pacs"]         = ta["pacs"]
                        entry["aipac_ie"]           = ta["ie"]
                        entry["aipac_lobby_donors"] = ta["lobby_donors"]
                        entry["aipac_sources"]      = ta["sources"]
                        print(f"    aipac: matched '{variant}' -> ${ta['total']:,}")
                        matched = True
                        break
                if not matched:
                    print(f"    aipac: no variant matched (still $0)")

            # Recompute special_interest_total
            entry["special_interest_total"] = sum(
                entry.get(k, 0) for k in
                ["aipac", "fossil_fuels", "pharma", "defense", "finance", "tech", "nra"]
            )
            v7_output[display_name] = entry

        # Save patched output
        with open(TARGET_OUTPUT, "w") as f:
            json.dump(v7_output, f, indent=2)

    # ── Move output to its final destination ────────────────────────────────
    if os.path.exists(TARGET_OUTPUT):
        os.makedirs(DATA_RAW_DIR, exist_ok=True)
        shutil.move(TARGET_OUTPUT, FINAL_OUTPUT)
        print(f"\nMoved output to {FINAL_OUTPUT}")
    else:
        print("\nWARNING: v7 did not produce fec_data_v7.json")

finally:
    # ── Clean up symlinks ───────────────────────────────────────────────────
    for link in created_links:
        try:
            os.unlink(link)
        except Exception:
            pass

    # ── Restore previous fec_data_v7.json if we backed one up ───────────────
    if backup_output and os.path.exists(backup_output):
        shutil.move(backup_output, TARGET_OUTPUT)
        print("Restored previous fec_data_v7.json")

print("\nDone.")
