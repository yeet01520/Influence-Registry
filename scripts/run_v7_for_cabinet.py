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
import json
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

    # ── POST-PROCESS: Patch JD Vance's OpenSecrets data ────────────────────
    # v7's name matching fails for JD Vance because "JD" gets tokenized
    # away by _normalize_oil (the >1-char filter), leaving only {vance}
    # which doesn't meet the 2-token threshold.
    # Retry with name variants until we find one that matches.
    if os.path.exists(TARGET_OUTPUT):
        with open(TARGET_OUTPUT) as f:
            v7_output = json.load(f)

        if "JD Vance" in v7_output:
            entry = v7_output["JD Vance"]
            print("\n  Post-processing JD Vance OpenSecrets matches...")

            # Name variants to try (in order of likelihood)
            name_variants = [
                "James David Vance",
                "James Vance",
                "James D Vance",
                "Vance JD",
                "Vance James",
            ]

            sector_funcs = {
                "fossil_fuels": v7.get_oil_amount,
                "pharma":       v7.get_pharma_amount,
                "defense":      v7.get_defense_amount,
                "finance":      v7.get_finance_amount,
                "tech":         v7.get_tech_amount,
            }

            for sector, func in sector_funcs.items():
                if entry.get(sector, 0) == 0:
                    for variant in name_variants:
                        amt = func(variant, "OH")
                        if amt > 0:
                            entry[sector] = amt
                            print(f"    {sector}: matched '{variant}' -> ${amt:,}")
                            break
                    else:
                        print(f"    {sector}: no variant matched (still $0)")

            # Also retry AIPAC from TrackAIPAC
            if entry.get("aipac", 0) == 0:
                for variant in name_variants:
                    ta = v7.get_aipac_from_trackaipac(variant)
                    if ta and ta.get("total", 0) > 0:
                        entry["aipac"]              = ta["total"]
                        entry["aipac_pacs"]         = ta["pacs"]
                        entry["aipac_ie"]           = ta["ie"]
                        entry["aipac_lobby_donors"] = ta["lobby_donors"]
                        entry["aipac_sources"]      = ta["sources"]
                        print(f"    aipac: matched '{variant}' -> ${ta['total']:,}")
                        break
                else:
                    print(f"    aipac: no variant matched (still $0)")

            # Recompute special_interest_total with the patched values
            entry["special_interest_total"] = sum(
                entry.get(k, 0) for k in
                ["aipac", "fossil_fuels", "pharma", "defense", "finance", "tech", "nra"]
            )

            v7_output["JD Vance"] = entry

        # ── Patch Marco Rubio's AIPAC if it came back zero ─────────────────
        # Rubio's TrackAIPAC entry exists (~$1M) but may have failed name match
        if "Marco Rubio" in v7_output:
            entry = v7_output["Marco Rubio"]
            if entry.get("aipac", 0) == 0:
                print("\n  Post-processing Marco Rubio AIPAC match...")
                rubio_variants = ["Rubio Marco", "Marco A Rubio", "Marco Antonio Rubio"]
                for variant in rubio_variants:
                    ta = v7.get_aipac_from_trackaipac(variant)
                    if ta and ta.get("total", 0) > 0:
                        entry["aipac"]              = ta["total"]
                        entry["aipac_pacs"]         = ta["pacs"]
                        entry["aipac_ie"]           = ta["ie"]
                        entry["aipac_lobby_donors"] = ta["lobby_donors"]
                        entry["aipac_sources"]      = ta["sources"]
                        print(f"    aipac: matched '{variant}' -> ${ta['total']:,}")
                        # Recompute SI total
                        entry["special_interest_total"] = sum(
                            entry.get(k, 0) for k in
                            ["aipac", "fossil_fuels", "pharma", "defense", "finance", "tech", "nra"]
                        )
                        v7_output["Marco Rubio"] = entry
                        break
                else:
                    print(f"    aipac: no variant matched (still $0)")

            with open(TARGET_OUTPUT, "w") as f:
                json.dump(v7_output, f, indent=2)

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
