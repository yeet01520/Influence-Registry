#!/usr/bin/env python3
"""
run_v7_for_all.py
==================
Runs v7 fetch_fec_data.py against all current members of Congress (~535)
with resume-from-checkpoint support.

HOW CHECKPOINTING WORKS:
  1. scripts/fec_data_v7.json is symlinked to data/raw/all_fec_data.json
  2. v7 auto-saves every 25 members, so progress writes directly to the
     canonical location on the runner filesystem
  3. The workflow commits data/raw/all_fec_data.json with `if: always()`
     so partial progress gets pushed even on timeout
  4. Next workflow run sees the partial file and v7's built-in resume
     logic picks up from where it left off

Run repeatedly until all members are done. The script writes a _meta
field to track completion status; the workflow uses it to decide whether
to merge into data/fec.json or just commit the checkpoint.
"""

import os
import sys
import json
import importlib.util

SCRIPT_DIR     = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT      = os.path.dirname(SCRIPT_DIR)
DATA_DIR       = os.path.join(REPO_ROOT, "data")
DATA_RAW_DIR   = os.path.join(REPO_ROOT, "data", "raw")
V7_SCRIPT_PATH = os.path.join(SCRIPT_DIR, "fetch_fec_data.py")
HOUSE_JSON     = os.path.join(DATA_DIR, "house.json")
SENATE_JSON    = os.path.join(DATA_DIR, "senate.json")
TARGET_MEMBERS = os.path.join(SCRIPT_DIR, "members.json")
TARGET_OUTPUT  = os.path.join(SCRIPT_DIR, "fec_data_v7.json")
FINAL_OUTPUT   = os.path.join(DATA_RAW_DIR, "all_fec_data.json")

# ── Members exempted from the completion check ──────────────────────────────
# Some members are perpetually difficult to fetch (territorial Delegates whose
# committees are filed under non-standard FEC type codes, members with FEC
# filings under different identifying names, etc.). Without an exemption list,
# a single stuck member blocks the merge into data/fec.json for ALL other
# members on every run.
#
# Members listed here will NOT count against the "missing" calculation when
# deciding whether the run is "complete." If they have prior data in
# data/fec.json, it's preserved as-is by the merge step. If they don't have
# prior data, they simply won't have an entry. Refresh their entries manually
# (or fix the underlying fetch bug) when you have time.
#
# To re-enable a member: just remove them from this set and re-run.
SKIP_MEMBERS = {
    # Delegate from American Samoa. FEC API's /candidate/{id}/committees/
    # endpoint returns zero matching committees for her authoritative
    # candidate ID, even with no committee_type filter. Likely a quirk of how
    # her territorial campaign committees are registered. Existing data is
    # preserved from prior fec.json snapshots.
    "Amata Coleman Radewagen",
}

# ── Validate ────────────────────────────────────────────────────────────────
if not os.path.exists(V7_SCRIPT_PATH):
    sys.exit(f"ERROR: v7 script not found at {V7_SCRIPT_PATH}")
api_key = os.environ.get("FEC_API_KEY")
if not api_key:
    sys.exit("ERROR: FEC_API_KEY environment variable not set")

os.makedirs(DATA_RAW_DIR, exist_ok=True)

# ── Build members.json from house.json + senate.json ────────────────────────
if not os.path.exists(TARGET_MEMBERS):
    if not os.path.exists(HOUSE_JSON) or not os.path.exists(SENATE_JSON):
        sys.exit(f"ERROR: need both {HOUSE_JSON} and {SENATE_JSON}")

    with open(HOUSE_JSON) as f:
        house_data = json.load(f)
    with open(SENATE_JSON) as f:
        senate_data = json.load(f)

    members = []
    for s in senate_data:
        if s.get("retired"):
            continue
        members.append({"name": s["name"], "state": s["state"], "office": "S"})
    for h in house_data:
        if h.get("retired"):
            continue
        members.append({"name": h["name"], "state": h["state"], "office": "H"})

    with open(TARGET_MEMBERS, "w") as f:
        json.dump(members, f, indent=2)
    print(f"Built members.json with {len(members)} entries "
          f"({sum(1 for m in members if m['office']=='S')} Senate, "
          f"{sum(1 for m in members if m['office']=='H')} House)")

with open(TARGET_MEMBERS) as f:
    all_members_list = json.load(f)
member_count = len(all_members_list)
print(f"members.json: {member_count} entries")

# ── Set up the checkpoint symlink ───────────────────────────────────────────
# scripts/fec_data_v7.json -> data/raw/all_fec_data.json
# v7 will write through the symlink, so all auto-saves persist at the
# canonical location.
#
# v7's resume logic loads the output file and skips any member already in
# it. We strip the _meta key first so v7 doesn't try to treat it as a
# member name.
existing_output = {}
if os.path.exists(FINAL_OUTPUT):
    try:
        with open(FINAL_OUTPUT) as f:
            existing_output = json.load(f)
        existing_output.pop("_meta", None)
        with open(FINAL_OUTPUT, "w") as f:
            json.dump(existing_output, f, indent=2)
        print(f"Resuming from {FINAL_OUTPUT}: {len(existing_output)} members already done")
    except Exception as e:
        print(f"⚠ Could not parse {FINAL_OUTPUT} ({e}) — starting fresh")
        existing_output = {}
        with open(FINAL_OUTPUT, "w") as f:
            json.dump({}, f)
else:
    with open(FINAL_OUTPUT, "w") as f:
        json.dump({}, f)
    print(f"Created empty {FINAL_OUTPUT} as checkpoint baseline")

# Replace any existing file/symlink at TARGET_OUTPUT with a fresh symlink
if os.path.lexists(TARGET_OUTPUT):
    os.unlink(TARGET_OUTPUT)
os.symlink(FINAL_OUTPUT, TARGET_OUTPUT)
print(f"Symlinked {TARGET_OUTPUT} -> {FINAL_OUTPUT}\n")

# ── Symlink CSVs and TrackAIPAC data ────────────────────────────────────────
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

v7_module = None
v7_failed = False

# ── Write _meta after each v7 auto-save ─────────────────────────────────────
# v7.main() can run for hours and may be killed by SIGTERM/timeout. To keep
# the workflow summary informative even on timeout, we monkey-patch the
# json.dump call inside v7's module namespace so that after each auto-save
# we also update _meta. This avoids race conditions from background threads.

def compute_meta():
    """Compute _meta dict based on current state of FINAL_OUTPUT.
    Skipped members (see SKIP_MEMBERS) are excluded from the "expected" set."""
    try:
        with open(FINAL_OUTPUT) as f:
            d = json.load(f)
        d.pop("_meta", None)
        done = set(d.keys())
        expected = {m["name"] for m in all_members_list} - SKIP_MEMBERS
        miss = sorted(expected - done)
        return {
            "complete":      len(miss) == 0 and not v7_failed,
            "members_done":  len(done),
            "members_total": member_count,
            "missing":       miss[:50],
        }, d
    except Exception:
        return None, None

def write_baseline_meta(status):
    """Write _meta reflecting current state, used at start and end."""
    meta, d = compute_meta()
    if meta is None:
        return
    meta["status"] = status
    d["_meta"] = meta
    try:
        with open(FINAL_OUTPUT, "w") as f:
            json.dump(d, f, indent=2)
    except Exception as ex:
        print(f"  ⚠ write_baseline_meta failed: {ex}")

write_baseline_meta("starting")

try:
    # ── Load v7 script ──────────────────────────────────────────────────────
    spec = importlib.util.spec_from_file_location("fetch_fec_data", V7_SCRIPT_PATH)
    v7_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v7_module)
    v7_module.API_KEY = api_key
    print("Injected FEC_API_KEY into v7\n")

    # ── Monkey-patch v7's json.dump to write _meta after each auto-save ─────
    # v7 calls json.dump(output, f, indent=2) inside its own namespace. We
    # wrap that so that after v7 writes its output, we append a _meta field
    # reflecting current progress. This keeps the workflow summary accurate
    # even if v7 is killed mid-run by a SIGTERM/timeout.
    _real_json_dump = v7_module.json.dump

    def _wrapped_dump(obj, fp, *args, **kwargs):
        # Inject _meta into the object being dumped, if it's the output dict.
        # Heuristic: if obj is a dict with member-shaped values, add _meta.
        try:
            if isinstance(obj, dict) and len(obj) > 0:
                # Check if it looks like the output dict (member names as keys,
                # dicts with sector fields as values). Look at first non-meta value.
                sample = next((v for k, v in obj.items() if k != "_meta"), None)
                is_output = (isinstance(sample, dict) and
                             any(k in sample for k in
                                 ("aipac", "fossil_fuels", "pharma",
                                  "defense", "finance", "tech")))
                if is_output:
                    obj.pop("_meta", None)
                    done = set(obj.keys())
                    expected = {m["name"] for m in all_members_list} - SKIP_MEMBERS
                    miss = sorted(expected - done)
                    obj["_meta"] = {
                        "complete":      False,  # mid-run, always incomplete
                        "members_done":  len(done),
                        "members_total": member_count,
                        "missing":       miss[:50],
                        "status":        "running",
                    }
        except Exception:
            pass  # never let _meta logic break v7's actual save
        return _real_json_dump(obj, fp, *args, **kwargs)

    v7_module.json.dump = _wrapped_dump
    print("Patched v7.json.dump to track progress via _meta\n")

    print("=" * 70)
    print(f"Running v7 fetch_fec_data.main() for {member_count} members")
    print(f"Resuming from {len(existing_output)} already done")
    print("v7 auto-saves every 25 members; safe to time out and retry")
    print("=" * 70)
    v7_module.main()

except Exception as e:
    v7_failed = True
    print(f"\n⚠ v7.main() raised an exception: {e}")
    print("Continuing to write _meta and exit cleanly so workflow can commit partial output.")

finally:
    # Clean up CSV symlinks (always, even on exception/timeout)
    for link in created_links:
        try:
            os.unlink(link)
        except Exception:
            pass

# ── Compute status and write _meta ──────────────────────────────────────────
# Even if v7 raised, the file at FINAL_OUTPUT still has whatever the last
# auto-save wrote. Members in SKIP_MEMBERS are excluded from the "expected"
# set so they don't block the completion check; their existing fec.json
# entries (if any) are preserved by the merge step.
with open(FINAL_OUTPUT) as f:
    final_data = json.load(f)
final_data.pop("_meta", None)

done_names    = set(final_data.keys())
expected_names = {m["name"] for m in all_members_list} - SKIP_MEMBERS
missing        = sorted(expected_names - done_names)
complete       = len(missing) == 0 and not v7_failed

print("\n" + "=" * 70)
print(f"STATUS: {'COMPLETE' if complete else 'PARTIAL'}")
print(f"  Processed: {len(done_names)} / {member_count}")
if SKIP_MEMBERS:
    print(f"  Skipped (excluded from completion check): {sorted(SKIP_MEMBERS)}")
if missing:
    print(f"  Missing:   {len(missing)} (showing first 10: {missing[:10]})")
if v7_failed:
    print(f"  v7 exited with an exception (see above)")
print("=" * 70)

# ── Post-processing for known problematic names (only when complete) ────────
if complete and v7_module is not None:
    KNOWN_VARIANTS = {
        "JD Vance": (["James David Vance", "James Vance", "James D Vance",
                      "Vance JD", "Vance James"], "Ohio"),
    }
    for display_name, (variants, full_state) in KNOWN_VARIANTS.items():
        if display_name not in final_data:
            continue
        entry = final_data[display_name]
        print(f"\nPost-processing {display_name} ({full_state})...")
        sector_funcs = {
            "fossil_fuels": v7_module.get_oil_amount,
            "pharma":       v7_module.get_pharma_amount,
            "defense":      v7_module.get_defense_amount,
            "finance":      v7_module.get_finance_amount,
            "tech":         v7_module.get_tech_amount,
        }
        for sector, func in sector_funcs.items():
            if entry.get(sector, 0) == 0:
                for variant in variants:
                    amt = func(variant, full_state)
                    if amt > 0:
                        entry[sector] = amt
                        print(f"  {sector}: matched '{variant}' -> ${amt:,}")
                        break
        if entry.get("aipac", 0) == 0:
            for variant in variants:
                ta = v7_module.get_aipac_from_trackaipac(variant)
                if ta and ta.get("total", 0) > 0:
                    # Compute aipac from components (NOT ta["total"]) to satisfy
                    # the validator's H2 invariant: aipac must equal the sum of
                    # aipac_pacs + aipac_lobby_donors + aipac_ie. TrackAIPAC's
                    # "Israel Lobby Total" row sometimes disagrees with the
                    # sum of the breakdown by small rounding amounts, which
                    # would otherwise produce a math-inconsistent record.
                    entry["aipac_pacs"]         = ta["pacs"]
                    entry["aipac_ie"]           = ta["ie"]
                    entry["aipac_lobby_donors"] = ta["lobby_donors"]
                    entry["aipac"]              = ta["pacs"] + ta["ie"] + ta["lobby_donors"]
                    entry["aipac_sources"]      = ta["sources"]
                    print(f"  aipac: matched '{variant}' -> ${entry['aipac']:,}")
                    break
        entry["special_interest_total"] = sum(
            entry.get(k, 0) for k in
            ["aipac", "fossil_fuels", "pharma", "defense", "finance", "tech", "nra"]
        )
        final_data[display_name] = entry

# ── Write final state with _meta marker ─────────────────────────────────────
final_data["_meta"] = {
    "complete":      complete,
    "members_done":  len(done_names),
    "members_total": member_count,
    "missing":       missing[:50],
}
with open(FINAL_OUTPUT, "w") as f:
    json.dump(final_data, f, indent=2)

print(f"\nWrote {FINAL_OUTPUT} with {len(done_names)} members + _meta")
print(f"Done.")
# Always exit 0 so the workflow's commit step always runs.
sys.exit(0)
