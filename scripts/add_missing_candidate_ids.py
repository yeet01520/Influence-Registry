#!/usr/bin/env python3
"""
add_missing_candidate_ids.py
============================
Adds official FEC candidate IDs that are missing from a member's
all_candidate_ids in data/fec.json.

WHY
---
fix_primary_candidate_ids.py corrected 13 members whose PRIMARY id was wrong.
It deliberately left alone the members who were merely MISSING an id, because
that is a different question: whether a member's career totals should include
a prior candidacy.

The answer turned out to be yes, and validate_data.py proved it. Roger Marshall
has only his House id on file, but his committee is his Senate committee, so
his grassroots figure was 7.49x his individual_total. The two measures were
counting different careers. Six more members show the same signature.

An official FEC id for a member belongs in that member's id list. Leaving it
out does not make the data more conservative, it makes it inconsistent: the
committee side counts the candidacy and the candidate side does not.

WHAT IT DOES NOT DO
-------------------
It only ADDS. It never removes an id and never changes the primary. Ids on
file that are not in the official list are left alone, because the official
file lists current-office ids and legitimately omits things like a sitting
House member's in-progress Senate campaign.

VERIFICATION
------------
Nothing is hard-coded. The script fetches legislators-current.json at runtime
and adds only ids that file lists for that member's bioguide id. If the fetch
fails, nothing is written.

SAFETY
------
  - Dry-run by default. Pass --commit, or set COMMIT_INPUT=true, to write.
  - Writes data/fec.json.addids.bak before saving.
  - A member with no bioguide match is skipped and reported.

AFTER RUNNING
-------------
Adding an id changes what FEC attributes to the member, so run in this order:
  1. this script
  2. rebuild_committees.py   (the new id may bring its own committees)
  3. refresh_total_raised.py
  4. fetch_pac_individual_split.py
  5. validate_data.py

USAGE
-----
  python3 scripts/add_missing_candidate_ids.py            # dry run
  python3 scripts/add_missing_candidate_ids.py --commit   # apply
"""

import json
import os
import sys
import urllib.request

HERE = os.path.dirname(__file__)
FEC_PATH = os.path.join(HERE, "..", "data", "fec.json")
BIO_PATH = os.path.join(HERE, "..", "data", "bioguide.json")
LEG_URL = "https://unitedstates.github.io/congress-legislators/legislators-current.json"

COMMIT = ("--commit" in sys.argv) or \
         (os.environ.get("COMMIT_INPUT", "").strip().lower() == "true")


def member_ids(rec):
    out, seen = [], set()
    for k in ("all_candidate_ids", "candidate_id"):
        v = rec.get(k)
        vals = v if isinstance(v, list) else ([v] if isinstance(v, str) and v else [])
        for i in vals:
            if i and i not in seen:
                seen.add(i)
                out.append(i)
    return out


def main():
    path = os.path.abspath(FEC_PATH)
    raw = open(path).read()
    fec = json.loads(raw)
    bio = json.load(open(os.path.abspath(BIO_PATH)))

    print(f"Mode: {'COMMIT (WILL WRITE data/fec.json)' if COMMIT else 'DRY-RUN (no write)'}")
    print(f"Fetching official IDs from {LEG_URL}")
    try:
        with urllib.request.urlopen(LEG_URL, timeout=60) as r:
            leg = json.loads(r.read())
    except Exception as e:
        print(f"FATAL: could not fetch the official legislator file ({e}).")
        print("Refusing to add unverified IDs.")
        sys.exit(2)
    official = {m["id"]["bioguide"]: list(m["id"].get("fec") or []) for m in leg}
    print(f"Loaded {len(official)} sitting members from the official file\n")

    added, no_bio, presidential = [], [], []
    for name, rec in fec.items():
        if name == "_meta" or not isinstance(rec, dict):
            continue
        b = bio.get(name)
        if not b or b not in official:
            if b is None:
                no_bio.append(name)
            continue
        have = member_ids(rec)
        missing = [i for i in official[b] if i not in have]
        # Presidential IDs are excluded. Adding a prior HOUSE run to a
        # senator's totals is a bookkeeping fix; folding in a presidential
        # campaign is a different claim entirely, since the money is an order
        # of magnitude larger and belongs to a race for another office. These
        # are reported so the choice is visible, not silently made.
        pres = [i for i in missing if i.startswith("P")]
        missing = [i for i in missing if not i.startswith("P")]
        if pres:
            presidential.append((name, pres))
        if not missing:
            continue
        if COMMIT:
            rec["all_candidate_ids"] = have + missing
            if not rec.get("candidate_id"):
                rec["candidate_id"] = (have + missing)[0]
        added.append((name, missing, have))

    label = "ADDED" if COMMIT else "WOULD ADD"
    print(f"{label} ({len(added)} members):")
    for name, missing, have in sorted(added):
        print(f"  {name:24s} + {missing}   (already had {have})")

    if presidential:
        print(f"\nSKIPPED, presidential candidate IDs ({len(presidential)}). "
              "Adding a presidential campaign to a congressional member's career\n"
              "totals is a methodology decision, not a data fix. Add by hand if wanted:")
        for name, pres in presidential:
            print(f"  {name:24s} {pres}")

    if no_bio:
        print(f"\nNo bioguide entry, skipped ({len(no_bio)}): {sorted(no_bio)[:10]}"
              + (" ..." if len(no_bio) > 10 else ""))

    if COMMIT and added:
        with open(path + ".addids.bak", "w") as f:
            f.write(raw)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(fec, f, indent=1, ensure_ascii=False)
        os.replace(tmp, path)
        print(f"\nWROTE {path}")
        print(f"(backup of original: {path}.addids.bak)")
        print("\nNEXT: rebuild_committees.py, refresh_total_raised.py, "
              "fetch_pac_individual_split.py, then validate_data.py.")
    elif COMMIT:
        print("\nNothing to add, so nothing written.")
    else:
        print("\nDRY-RUN complete. Re-run with commit=true to apply.")


if __name__ == "__main__":
    main()
