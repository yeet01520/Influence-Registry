#!/usr/bin/env python3
"""
audit_candidate_ids.py
======================

ONE-TIME AUDIT script that fixes the v7 fetch script's name-matching bug
that contaminated ~270 of 538 members' candidate ID lists with cross-state
matches (e.g. Durbin had S4IL00339 instead of correct S6IL00151;
Mark Kelly had ND House IDs alongside his AZ Senate ID).

What it does:
  1. For every member, queries FEC `/candidates/?q={name}&office={H/S}&state={state}`
     to get the AUTHORITATIVE list of candidate IDs for that person.
  2. Compares against current fec.json contents.
  3. Identifies:
     - Members where primary candidate_id is wrong (worst case — Durbin)
     - Members with cross-state contamination (extra unrelated IDs)
     - Members with no FEC data (won't try to fix; logs warning)
  4. Writes patched fec.json with corrected IDs.
  5. Writes data/members_to_refetch.json listing affected members whose
     outside_spending data should be re-pulled (those whose primary changed
     or who currently show $0/$0 in outside_spending.json).
  6. Writes a markdown audit report for review.

USAGE:
  export FEC_API_KEY="..."
  python3 scripts/audit_candidate_ids.py

OUTPUTS:
  data/fec.json — patched in place
  data/fec.json.before_audit — backup of original
  data/members_to_refetch.json — list of names needing Schedule E re-pull
  data/audit_report.md — human-readable summary
"""

import json
import os
import sys
import time
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
import urllib.request
import urllib.error

# ---------- Tunables ----------
SLEEP_BETWEEN_CALLS = 0.6
MAX_RETRIES         = 5
RATE_LIMIT_WAIT     = 30

API_BASE  = "https://api.open.fec.gov/v1"
DATA_DIR  = Path(__file__).resolve().parent.parent / "data"
FEC_FILE          = DATA_DIR / "fec.json"
FEC_BACKUP        = DATA_DIR / "fec.json.before_audit"
SENATE_FILE       = DATA_DIR / "senate.json"
HOUSE_FILE        = DATA_DIR / "house.json"
OUTSIDE_FILE      = DATA_DIR / "outside_spending.json"
REFETCH_FILE      = DATA_DIR / "members_to_refetch.json"
REPORT_FILE       = DATA_DIR / "audit_report.md"


def get_api_key():
    key = os.environ.get("FEC_API_KEY")
    if not key:
        sys.exit("ERROR: FEC_API_KEY not set")
    return key


def fec_get(path, params, api_key):
    """GET from FEC API with retries."""
    params = {**params, "api_key": api_key}
    url = f"{API_BASE}{path}?{urlencode(params)}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "InfluenceRegistry/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = RATE_LIMIT_WAIT * attempt
                print(f"      rate-limited, sleep {wait}s", flush=True)
                time.sleep(wait)
                continue
            if 500 <= e.code < 600:
                time.sleep(2 * attempt)
                continue
            print(f"      HTTP {e.code}: {url[:100]}", flush=True)
            return None
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"      net error attempt {attempt}: {e}", flush=True)
            time.sleep(2 * attempt)
    return None


def normalize_name(name):
    """Lowercase, strip punctuation, used for fuzzy matching."""
    n = name.lower().replace(".", "").replace(",", "").replace("'", "").replace("-", " ")
    return " ".join(n.split())


def reorder_fec_name(name):
    """
    FEC returns names as 'LAST, FIRST MIDDLE' (e.g. 'DURBIN, RICHARD J.').
    Convert to 'FIRST LAST' format if a comma is present.
    Otherwise return unchanged.
    Handles compound last names like 'VAN HOLLEN, CHRIS' or 'OCASIO-CORTEZ, ALEXANDRIA'.
    """
    if "," not in name:
        return name
    parts = name.split(",", 1)  # Split on first comma only
    last_name = parts[0].strip()
    first_etc = parts[1].strip()
    # first_etc may be "RICHARD J." — drop middle initial(s)
    first_parts = first_etc.split()
    first_name = first_parts[0] if first_parts else ""
    return f"{first_name} {last_name}".strip()


def names_match(member_name, fec_name):
    """
    Match a member name (e.g. 'Dick Durbin') against an FEC-format name
    (e.g. 'DURBIN, RICHARD J.').
    
    Handles:
    - LAST, FIRST format from FEC
    - Nicknames (Dick/Richard, Bob/Robert, etc.)
    - Middle initials
    - Compound surnames (Van Hollen, Ocasio-Cortez)
    """
    # Convert FEC's LAST,FIRST to FIRST LAST first
    fec_reordered = reorder_fec_name(fec_name)
    
    na = normalize_name(member_name)
    nb = normalize_name(fec_reordered)
    
    if na == nb:
        return True
    
    a_parts = na.split()
    b_parts = nb.split()
    
    if len(a_parts) < 2 or len(b_parts) < 2:
        return False
    
    # Check last names match (key requirement)
    # Last name is parts[-1] for simple names, but for compound surnames
    # like 'chris van hollen' we need parts[-1] OR parts[-2]+' '+parts[-1]
    a_last = a_parts[-1]
    b_last = b_parts[-1]
    a_first = a_parts[0]
    b_first = b_parts[0]
    
    # Direct first+last match (handles middle initials)
    if a_first == b_first and a_last == b_last:
        return True
    
    # Compound surname check: try last 2 parts as surname
    if len(a_parts) >= 3 and len(b_parts) >= 3:
        a_last2 = " ".join(a_parts[-2:])
        b_last2 = " ".join(b_parts[-2:])
        if a_first == b_first and a_last2 == b_last2:
            return True
    
    # Last name match + nickname check
    NICKNAME_PAIRS = [
        ('dick', 'richard'), ('bob', 'robert'), ('rob', 'robert'),
        ('jim', 'james'), ('chuck', 'charles'), ('chuck', 'charlie'),
        ('bill', 'william'), ('liz', 'elizabeth'), ('beth', 'elizabeth'),
        ('dan', 'daniel'), ('mike', 'michael'), ('ed', 'edward'),
        ('joe', 'joseph'), ('tony', 'anthony'), ('rick', 'richard'),
        ('jerry', 'gerald'), ('ron', 'ronald'), ('john', 'jonathan'),
        ('jon', 'jonathan'), ('tom', 'thomas'), ('tim', 'timothy'),
        ('pat', 'patrick'), ('greg', 'gregory'), ('matt', 'matthew'),
        ('chris', 'christopher'), ('chris', 'christine'), ('katie', 'kathryn'),
        ('katie', 'katherine'), ('kate', 'katherine'), ('andy', 'andrew'),
        ('al', 'albert'), ('al', 'alvin'), ('debbie', 'deborah'),
        ('debbie', 'debra'), ('larry', 'lawrence'), ('eddie', 'edward'),
        ('frank', 'francis'), ('nick', 'nicholas'), ('cathy', 'catherine'),
        ('kathy', 'catherine'), ('kathy', 'katherine'), ('peggy', 'margaret'),
        ('maggie', 'margaret'), ('sue', 'susan'), ('becky', 'rebecca'),
        ('tony', 'antonio'), ('alex', 'alexander'), ('alex', 'alexandra'),
        ('eli', 'elijah'), ('eli', 'elias'), ('zach', 'zachary'),
        ('hal', 'harold'), ('benny', 'benjamin'), ('ben', 'benjamin'),
        ('manny', 'manuel'), ('vinny', 'vincent'), ('vince', 'vincent'),
        ('art', 'arthur'), ('marty', 'martin'), ('don', 'donald'),
        ('phil', 'philip'), ('phil', 'phillip'),
    ]
    
    if a_last == b_last:
        # Same last name — check if first names are nickname pairs
        for nick, full in NICKNAME_PAIRS:
            if (a_first == nick and b_first == full) or (a_first == full and b_first == nick):
                return True
        # Also: one first name is a prefix of the other (e.g. "Dan" / "Daniel")
        if len(a_first) >= 3 and len(b_first) >= 3:
            if a_first.startswith(b_first) or b_first.startswith(a_first):
                return True
    
    # Compound surname version of nickname check
    if len(a_parts) >= 3 and len(b_parts) >= 3:
        a_last2 = " ".join(a_parts[-2:])
        b_last2 = " ".join(b_parts[-2:])
        if a_last2 == b_last2:
            for nick, full in NICKNAME_PAIRS:
                if (a_first == nick and b_first == full) or (a_first == full and b_first == nick):
                    return True
    
    return False


def search_candidates(name, office, state, api_key):
    """
    Query FEC `/candidates/search/` endpoint for candidates matching name+office+state.
    Returns list of candidate dicts.
    """
    params = {
        "q": name,
        "office": office,  # "H" or "S"
        "state": state,
        "per_page": 50,
    }
    data = fec_get("/candidates/search/", params, api_key)
    if not data or "results" not in data:
        return []
    return data["results"]


def find_correct_candidate_ids(name, office, state, api_key):
    """
    Find the correct list of candidate IDs for a member.
    Returns (primary_id, all_ids) where all_ids is sorted by most recent activity first.
    """
    # First try with state filter
    candidates = search_candidates(name, office, state, api_key)
    
    # Filter to those that match the name (handles Dick/Richard etc.)
    matching = []
    for c in candidates:
        c_name = c.get("name", "")
        if names_match(name, c_name):
            matching.append(c)
    
    if not matching:
        # Try without state (in case of weird state mismatch)
        time.sleep(SLEEP_BETWEEN_CALLS)
        candidates = search_candidates(name, office, "", api_key)
        for c in candidates:
            c_name = c.get("name", "")
            if names_match(name, c_name):
                matching.append(c)
    
    if not matching:
        return None, []
    
    # For each match, the ID and most-recent cycle
    seen_ids = {}
    for c in matching:
        cid = c.get("candidate_id")
        if not cid:
            continue
        # Verify state matches actual office state (further protection vs same-name-different-state)
        c_state = c.get("state", "")
        c_office = c.get("office", "")
        # House: id starts with H{state_code}; Senate: S{state_code}
        # The 2nd-3rd chars of candidate_id encode the state
        if len(cid) >= 3:
            id_state = cid[1:3]
            if id_state.upper() != state.upper():
                # Cross-state ID — skip even if FEC search returned it
                continue
        # Match office too (H or S in first char)
        if cid[0] != office:
            # Could be a Presidential/Senate cross — check office field
            if c_office and c_office != office:
                continue
        cycles = c.get("cycles", [])
        most_recent = max(cycles) if cycles else 0
        if cid not in seen_ids or seen_ids[cid] < most_recent:
            seen_ids[cid] = most_recent
    
    if not seen_ids:
        return None, []
    
    # Sort by most recent activity desc; primary is the one with most recent cycle
    sorted_ids = sorted(seen_ids.items(), key=lambda x: -x[1])
    primary = sorted_ids[0][0]
    all_ids = [cid for cid, _ in sorted_ids]
    return primary, all_ids


def main():
    api_key = get_api_key()
    
    # Load all needed files
    if not FEC_FILE.exists():
        sys.exit(f"ERROR: {FEC_FILE} not found")
    if not SENATE_FILE.exists() or not HOUSE_FILE.exists():
        sys.exit(f"ERROR: senate.json and/or house.json missing")
    
    fec_data = json.loads(FEC_FILE.read_text())
    senate_data = json.loads(SENATE_FILE.read_text())
    house_data = json.loads(HOUSE_FILE.read_text())
    outside_data = {}
    if OUTSIDE_FILE.exists():
        outside_data = json.loads(OUTSIDE_FILE.read_text())
    
    # Build name -> (office, state) map
    member_info = {}
    for s in senate_data:
        member_info[s["name"]] = ("S", s["state"])
    for h in house_data:
        member_info[h["name"]] = ("H", h["state"])
    
    print(f"Loaded {len(senate_data)} senators + {len(house_data)} reps = {len(member_info)} members", flush=True)
    print(f"FEC.json has {len(fec_data)} entries", flush=True)
    
    # Backup fec.json before changes
    FEC_BACKUP.write_text(json.dumps(fec_data, indent=2))
    print(f"Backed up original fec.json to {FEC_BACKUP.name}", flush=True)
    
    # Audit each member
    fixed_primary = []      # primary candidate_id changed
    cleaned_extra = []      # cross-state IDs removed but primary same
    no_change = []          # already correct
    not_found = []          # FEC search returned nothing
    
    members_to_audit = sorted(set(fec_data.keys()) & set(member_info.keys()))
    print(f"\nAuditing {len(members_to_audit)} members (those in both fec.json and senate.json/house.json)...", flush=True)
    
    for i, name in enumerate(members_to_audit, 1):
        office, state = member_info[name]
        existing = fec_data[name]
        old_primary = existing.get("candidate_id", "")
        old_all = existing.get("all_candidate_ids", [])
        
        # Verify by querying FEC
        primary, all_ids = find_correct_candidate_ids(name, office, state, api_key)
        time.sleep(SLEEP_BETWEEN_CALLS)
        
        if not primary:
            print(f"[{i:4d}/{len(members_to_audit)}] {name:<35} (no FEC match found, leaving as-is)", flush=True)
            not_found.append(name)
            continue
        
        # Compare
        primary_changed = (primary != old_primary)
        ids_set_old = set(old_all)
        ids_set_new = set(all_ids)
        contamination_removed = ids_set_old - ids_set_new
        legitimate_added = ids_set_new - ids_set_old
        
        if primary_changed:
            print(f"[{i:4d}/{len(members_to_audit)}] {name:<35} PRIMARY {old_primary} -> {primary}", flush=True)
            fixed_primary.append({
                "name": name,
                "old_primary": old_primary,
                "new_primary": primary,
                "old_all": list(old_all),
                "new_all": all_ids,
            })
        elif contamination_removed or legitimate_added:
            print(f"[{i:4d}/{len(members_to_audit)}] {name:<35} cleaned {len(contamination_removed)} bad ID(s)", flush=True)
            cleaned_extra.append({
                "name": name,
                "removed": list(contamination_removed),
                "added": list(legitimate_added),
            })
        else:
            no_change.append(name)
        
        # Patch fec.json entry in place
        existing["candidate_id"] = primary
        existing["all_candidate_ids"] = all_ids
    
    # Write patched fec.json
    FEC_FILE.write_text(json.dumps(fec_data, indent=2))
    print(f"\nWrote patched {FEC_FILE.name}", flush=True)
    
    # Determine which members need Schedule E re-fetch
    refetch_names = set()
    
    # 1. Anyone whose primary changed
    for entry in fixed_primary:
        refetch_names.add(entry["name"])
    
    # 2. Anyone with cleaned contamination IF the contamination contributed bogus data
    #    Conservative approach: refetch them all to ensure clean data
    for entry in cleaned_extra:
        refetch_names.add(entry["name"])
    
    # 3. Anyone currently showing $0/$0 in outside_spending.json
    for name, d in outside_data.items():
        sup = d.get("total_supporting", 0)
        opp = d.get("total_opposing", 0)
        if sup == 0 and opp == 0:
            refetch_names.add(name)
    
    # 4. The Luttrell anomaly specifically
    if "Morgan Luttrell" in fec_data:
        refetch_names.add("Morgan Luttrell")
    
    refetch_list = sorted(refetch_names)
    REFETCH_FILE.write_text(json.dumps(refetch_list, indent=2))
    print(f"Wrote {REFETCH_FILE.name} with {len(refetch_list)} members to re-fetch", flush=True)
    
    # Build audit report
    report_lines = [
        "# Candidate ID Audit Report",
        f"_Run: {datetime.now(timezone.utc).isoformat()}_",
        "",
        "## Summary",
        "",
        f"- Members audited: {len(members_to_audit)}",
        f"- Primary candidate_id changed: {len(fixed_primary)}",
        f"- Cross-state contamination cleaned (primary unchanged): {len(cleaned_extra)}",
        f"- Already correct (no change): {len(no_change)}",
        f"- FEC search returned no match (left as-is): {len(not_found)}",
        f"- Members queued for Schedule E re-fetch: {len(refetch_list)}",
        "",
    ]
    
    if fixed_primary:
        report_lines.append("## Members with primary candidate_id CHANGED")
        report_lines.append("")
        report_lines.append("These had wrong primary IDs in fec.json — outside spending data was definitely incorrect:")
        report_lines.append("")
        for entry in fixed_primary:
            report_lines.append(f"- **{entry['name']}**: `{entry['old_primary']}` → `{entry['new_primary']}`")
        report_lines.append("")
    
    if cleaned_extra:
        report_lines.append(f"## Members with contamination cleaned (primary unchanged) — {len(cleaned_extra)} total")
        report_lines.append("")
        report_lines.append("These had extra cross-state IDs polluting their candidate list. Primary was right, ")
        report_lines.append("but Schedule E data may have included unrelated candidates' spending.")
        report_lines.append("")
        # Show first 30
        for entry in cleaned_extra[:30]:
            removed = ", ".join(entry["removed"]) if entry["removed"] else "(none)"
            report_lines.append(f"- **{entry['name']}**: removed `{removed}`")
        if len(cleaned_extra) > 30:
            report_lines.append(f"- ...and {len(cleaned_extra) - 30} more")
        report_lines.append("")
    
    if not_found:
        report_lines.append("## Members where FEC search returned no match")
        report_lines.append("")
        report_lines.append("These are unusual — could be Cabinet members, non-voting delegates, recent appointees, ")
        report_lines.append("or rare data issues. Their fec.json entries were left unchanged. Manual review needed:")
        report_lines.append("")
        for n in not_found:
            report_lines.append(f"- {n}")
        report_lines.append("")
    
    report_lines.append("## Next steps")
    report_lines.append("")
    report_lines.append(f"1. Review `data/audit_report.md` (this file)")
    report_lines.append(f"2. Run `scripts/refetch_outside_spending.py` to re-pull Schedule E for the {len(refetch_list)} affected members")
    report_lines.append(f"3. Verify spot-check members (Durbin, Cotton, Schiff, Rick Scott) now show realistic numbers")
    report_lines.append("")
    report_lines.append("## Backup")
    report_lines.append(f"")
    report_lines.append(f"Original fec.json saved to `data/fec.json.before_audit` for rollback if needed.")
    
    REPORT_FILE.write_text("\n".join(report_lines))
    print(f"Wrote {REPORT_FILE.name}", flush=True)
    
    print(f"\n{'='*60}")
    print(f"AUDIT COMPLETE")
    print(f"{'='*60}")
    print(f"  Primary changed: {len(fixed_primary)}")
    print(f"  Contamination cleaned: {len(cleaned_extra)}")
    print(f"  No change needed: {len(no_change)}")
    print(f"  Not found in FEC: {len(not_found)}")
    print(f"  → Re-fetch queue: {len(refetch_list)} members")


if __name__ == "__main__":
    main()
