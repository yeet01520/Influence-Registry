#!/usr/bin/env python3
"""
fetch_pac_individual_split.py
==============================
Adds an authoritative PAC-vs-individual money split to each member in
data/fec.json, pulled directly from the FEC API.

WHY THIS EXISTS
---------------
The sector totals in fec.json (pharma, finance, tech, defense, fossil_fuels)
come from OpenSecrets industry data, which — by OpenSecrets' deliberate
methodology — BLENDS two very different things under one number:

  1. Industry PAC money (checks from the industry's PACs = influence buying)
  2. Individual donations from people who happen to work in that industry
     (often small-dollar grassroots donations that carry no industry agenda)

For most members the blend is fine. But for small-dollar-funded members who
REFUSE corporate PAC money (Bernie Sanders, Elizabeth Warren, AOC, etc.) it is
badly misleading: their "pharma" or "tech" totals are overwhelmingly individual
donations from nurses / engineers, not drug-company or Big-Tech PAC money.
Example (verified against FEC): Bernie Sanders' Senate committee shows
~$23.8M individual contributions and $27 (twenty-seven dollars) of PAC money.

FEC reports the PAC-vs-individual split cleanly and authoritatively. This
script pulls that split so the app can show what share of a member's money is
ACTUALLY PAC money — and so features framed around influence (e.g. the
committee-oversight graph) can use the honest PAC figure instead of the
blended sector number.

WHAT IT DOES
------------
For every member in data/fec.json, for each of their candidate IDs, it calls
    /candidate/{id}/totals/
and sums, across all cycles returned:
    individual_contributions                  -> individual_total
    other_political_committee_contributions   -> pac_total   (non-party PACs)
    political_party_committee_contributions   -> party_total
It then writes these three fields plus:
    pac_pct        = pac_total / (pac_total + individual_total + party_total)
                     as a rounded percentage (0-100), or None if no receipts.
And, from FEC Schedule E (independent expenditures — outside spending NOT given
to the campaign, the same category as AIPAC's super-PAC spending):
    ie_support_total  -> outside money spent SUPPORTING the member
    ie_oppose_total   -> outside money spent OPPOSING the member
back into each member's record and saves data/fec.json.

It does NOT touch the sector totals, grassroots fields, or anything else.
Existing fields are preserved; only the four new fields are added/updated.

REQUIRES
--------
  FEC_API_KEY environment variable (same key the main fetch script uses).
    export FEC_API_KEY=your_key && python3 scripts/fetch_pac_individual_split.py

  data/fec.json  (produced by the main pipeline; must already contain
                  candidate_id / all_candidate_ids per member)

OUTPUT
------
  data/fec.json  (updated in place, with a .bak backup written first)

SAFETY
------
  - Never zeroes an existing value on API failure: if the totals call fails for
    a member, that member's split fields are left unchanged (or absent) and the
    member is listed in the REVIEW report at the end.
  - Writes data/fec.json.pacsplit.bak before saving.
  - Dry-run by default. Pass --commit to write.
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

BASE = "https://api.open.fec.gov/v1"
FEC_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "fec.json")

API_KEY = os.environ.get("FEC_API_KEY", "").strip()
if not API_KEY:
    print("ERROR: FEC_API_KEY environment variable is not set.")
    print("  Local runs:  export FEC_API_KEY=your_key && python3 scripts/fetch_pac_individual_split.py")
    print("  CI runs:     check that the workflow passes secrets.FEC_API_KEY as env")
    sys.exit(1)

COMMIT = "--commit" in sys.argv


def get(path, params=None):
    """FEC API GET with retry + rate-limit handling. Returns dict or None."""
    p = dict(params or {})
    p["api_key"] = API_KEY
    url = BASE + path + "?" + urllib.parse.urlencode(p)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print("\n  [rate-limited — sleeping 65s]", end="", flush=True)
                time.sleep(65)
                continue
            if e.code in (400, 404, 422):
                return None
            time.sleep(3)
        except Exception:
            time.sleep(2)
    return None


def candidate_ids(rec):
    """All candidate IDs for a member record, de-duplicated, order-stable."""
    ids = []
    for k in ("all_candidate_ids", "candidate_id"):
        v = rec.get(k)
        if isinstance(v, list):
            ids.extend(v)
        elif isinstance(v, str) and v:
            ids.append(v)
    seen = set()
    out = []
    for i in ids:
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def split_for_candidate(cid):
    """
    Sum PAC / individual / party contributions across all cycles the FEC
    totals endpoint returns for this candidate id.
    Returns (individual, pac, party) or None on failure.
    """
    data = get(f"/candidate/{cid}/totals/", {"per_page": 100})
    if data is None or "results" not in data:
        return None
    ind = pac = party = 0.0
    for r in data["results"]:
        ind += float(r.get("individual_contributions") or 0)
        pac += float(r.get("other_political_committee_contributions") or 0)
        party += float(r.get("political_party_committee_contributions") or 0)
    return (ind, pac, party)


def ie_for_candidate(cid):
    """
    Sum independent expenditures made SUPPORTING vs OPPOSING this candidate,
    across all cycles, from FEC Schedule E (by_candidate aggregate).

    IE is outside spending that is NOT given to the campaign and cannot legally
    be coordinated with it — the same category as AIPAC's United Democracy
    Project spending. FEC splits it by support_oppose_indicator:
        'S' = spent supporting the candidate
        'O' = spent opposing the candidate
    Returns (ie_support, ie_oppose). Returns (0.0, 0.0) if none found; returns
    None only if the endpoint call itself fails (so callers can leave existing
    values untouched rather than zeroing them).
    """
    support = oppose = 0.0
    ok = False
    # The by_candidate aggregate needs a cycle; iterate recent cycles and sum.
    for cycle in (2018, 2020, 2022, 2024, 2026):
        data = get("/schedules/schedule_e/by_candidate/",
                   {"candidate_id": cid, "cycle": cycle, "per_page": 20})
        if data is None:
            continue
        ok = True
        for r in data.get("results", []):
            amt = float(r.get("total") or 0)
            if r.get("support_oppose_indicator") == "O":
                oppose += amt
            else:
                support += amt
        time.sleep(0.15)
    if not ok:
        return None
    return (support, oppose)


def main():
    path = os.path.abspath(FEC_PATH)
    with open(path) as f:
        raw_original = f.read()
    fec = json.loads(raw_original)

    members = [(k, v) for k, v in fec.items() if k != "_meta"]
    print(f"Loaded {len(members)} members from {path}")
    print(f"Mode: {'COMMIT (will write)' if COMMIT else 'DRY-RUN (no write; pass --commit to save)'}")
    print()

    updated = 0
    no_ids = []
    api_failed = []
    examples = []  # collect a few notable low-PAC members for the report

    for idx, (name, rec) in enumerate(members):
        cids = candidate_ids(rec)
        if not cids:
            no_ids.append(name)
            continue

        tot_ind = tot_pac = tot_party = 0.0
        tot_ie_support = tot_ie_oppose = 0.0
        any_ok = False
        ie_ok = False
        for cid in cids:
            res = split_for_candidate(cid)
            if res is not None:
                any_ok = True
                i, p, pa = res
                tot_ind += i
                tot_pac += p
                tot_party += pa
            ie = ie_for_candidate(cid)
            if ie is not None:
                ie_ok = True
                tot_ie_support += ie[0]
                tot_ie_oppose += ie[1]
            time.sleep(0.2)  # be polite to the API

        if not any_ok:
            # Never overwrite/zero on failure — leave existing fields untouched.
            api_failed.append(name)
            continue

        denom = tot_ind + tot_pac + tot_party
        pac_pct = round(tot_pac / denom * 100) if denom > 0 else None

        rec["individual_total"] = round(tot_ind)
        rec["pac_total"] = round(tot_pac)
        rec["party_total"] = round(tot_party)
        rec["pac_pct"] = pac_pct
        # Independent expenditures (outside spending, not given to the campaign).
        # Only write if the IE endpoint actually responded, so a transient IE
        # failure never zeroes a real prior value.
        if ie_ok:
            rec["ie_support_total"] = round(tot_ie_support)
            rec["ie_oppose_total"] = round(tot_ie_oppose)
        updated += 1

        # Collect notable examples: members with almost no PAC money.
        if pac_pct is not None and pac_pct <= 1 and denom > 1_000_000:
            examples.append((name, round(tot_pac), round(denom), pac_pct))

        if (idx + 1) % 25 == 0:
            print(f"  ...processed {idx + 1}/{len(members)}")

    print()
    print("=" * 64)
    print(f"Updated split for: {updated} members")
    print(f"No candidate IDs (skipped): {len(no_ids)}")
    print(f"API failed (left unchanged — REVIEW): {len(api_failed)}")
    if api_failed:
        for n in api_failed[:20]:
            print(f"    REVIEW: {n}")
        if len(api_failed) > 20:
            print(f"    ...and {len(api_failed) - 20} more")

    # Show the fix working: members who take ~0% PAC money despite big receipts.
    examples.sort(key=lambda x: -x[2])
    print()
    print("Sanity check — members with <=1% PAC money (the ones the old blended")
    print("sector data made look 'industry-captured'):")
    for name, pac, denom, pct in examples[:12]:
        print(f"    {name:26s} PAC ${pac:>12,}  of  ${denom:>13,}  ({pct}%)")

    if COMMIT:
        bak = path + ".pacsplit.bak"
        with open(bak, "w") as f:
            f.write(raw_original)  # true backup of the ORIGINAL file, pre-modification
        with open(path, "w") as f:
            json.dump(fec, f, indent=1, ensure_ascii=False)
        print()
        print(f"WROTE {path}")
        print(f"(backup of original: {bak})")
    else:
        print()
        print("DRY-RUN complete. Re-run with --commit to write data/fec.json.")


if __name__ == "__main__":
    main()
