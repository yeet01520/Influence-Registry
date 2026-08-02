#!/usr/bin/env python3
"""
fix_primary_candidate_ids.py
============================
Replaces wrong PRIMARY candidate IDs in data/fec.json with the official ones.

WHY THIS EXISTS
---------------
audit_candidate_ids.py compares the name FEC has registered for an ID against
the member's name. That catches contamination when the names disagree, which
is how Dave Joyce holding Joyce Beatty's ID was found. It cannot catch a wrong
ID that happens to carry a compatible name and the right state.

Checking every member's IDs against unitedstates/congress-legislators, which
publishes each sitting member's official FEC candidate IDs, found 13 members
with a wrong primary. Only 5 of those had been caught by the name audit. The
other 8 (Rick Allen, Andy Harris, Glenn Ivey, Tom Kean Jr., Mike Kelly, Keith
Self, Laura Gillen, and Dick Durbin's true Senate ID) passed every check we
had, because nothing about them looked wrong from the inside.

Corroborating detail: Andy Harris's stored ID H8MD01023 is the one ID whose
FEC lookup failed during the audit. FEC has no such candidate. It was not a
transient error.

WHY IT RUNS BEFORE THE COMMITTEE AUDIT
--------------------------------------
audit_committee_ids.py decides a committee belongs to a member by checking
whether the committee's authorized candidates intersect the member's candidate
IDs. If the member's candidate ID is itself wrong, a committee belonging to
that same wrong person passes the check. Dave Joyce is exactly this: he holds
Beatty's candidate ID AND her committee, so the two agree with each other and
the audit sees nothing. Fixing the candidate ID first is what exposes the
committee.

VERIFICATION
------------
The corrections below are hard-coded so the diff is readable and arguable.
But every one is re-verified at runtime against the live
legislators-current.json before it is applied:

  - the new ID must appear in that member's official FEC id list
  - if the OLD id also appears there, the correction is REFUSED, because
    removing something the official source says is theirs needs a human

A correction whose member cannot be resolved to a bioguide ID is refused too.
No network, no apply: if the official file cannot be fetched, nothing is
written.

SCOPE
-----
Only primary candidate IDs, and only IDs listed below. The same comparison
also surfaced 40 "extra" IDs and 33 "missing" official IDs across the roster,
and most of those are NOT errors: the extras are mostly 2026 Senate campaign
committees for sitting House members, too new for the official file, and the
missing ones are mostly old House IDs for sitting senators, which is a
judgement call about whether career totals should include prior service.
Neither is touched here.

This does NOT touch the `committees` array and does NOT recompute money.
Run audit_committee_ids.py and then refresh_total_raised.py afterwards.

USAGE
-----
  python3 scripts/fix_primary_candidate_ids.py            # dry run
  python3 scripts/fix_primary_candidate_ids.py --commit   # apply
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

# member, IDs to remove, correct primary to install, what the wrong ID really is
CORRECTIONS = [
    ("Dave Joyce",    ["H2OH03125"],               "H2OH14064", "BEATTY, JOYCE"),
    ("Mike Collins",  ["H0GA03017", "S6GA00101"],  "H4GA10071", "BROUN, PAUL COLLINS"),
    ("Bobby Scott",   ["H2VA10026", "S4VA00312"],  "H6VA01117", "BOWDEN / PARKINSON"),
    ("Dick Durbin",   ["S4IL00339"],               "S6IL00151", "DURBIN, JEFFREY WILLIAM"),
    ("Nick Begich",   ["H4AK00024"],               "H2AK01083", "BEGICH, MARGARET (PEGGE)"),
    ("John James",    ["S8MI00372"],               "H2MI10150", "not his official id"),
    ("Rick Allen",    ["H0GA02241"],               "H2GA12121", "not his official id"),
    ("Andy Harris",   ["H8MD01023"],               "H8MD01094", "no such candidate at FEC"),
    ("Glenn Ivey",    ["H2MD04232"],               "H2MD04315", "not his official id"),
    ("Tom Kean Jr.",  ["H0NJ07089"],               "H0NJ07261", "not his official id"),
    ("Mike Kelly",    ["H4PA03117"],               "H0PA03271", "not his official id"),
    ("Keith Self",    ["H2TX00064"],               "H2TX03290", "not his official id"),
    ("Laura Gillen",  ["H2NY04244"],               "H4NY04158", "not his official id"),
]


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
        print("Refusing to apply unverified corrections.")
        sys.exit(2)
    official = {m["id"]["bioguide"]: set(m["id"].get("fec") or []) for m in leg}
    print(f"Loaded {len(official)} sitting members from the official file\n")

    applied, refused = [], []

    for member, remove, correct, what in CORRECTIONS:
        rec = fec.get(member)
        if rec is None:
            refused.append((member, "not in fec.json"))
            continue
        b = bio.get(member)
        if not b or b not in official:
            refused.append((member, f"no bioguide match (bioguide={b})"))
            continue
        off = official[b]
        if correct not in off:
            refused.append((member, f"{correct} is not in the official list {sorted(off)}"))
            continue
        overlap = [r for r in remove if r in off]
        if overlap:
            refused.append((member, f"official list also contains {overlap}, refusing to remove"))
            continue

        cur = member_ids(rec)
        keep = [i for i in cur if i not in remove]
        new = [correct] + [i for i in keep if i != correct]
        if COMMIT:
            rec["candidate_id"] = correct
            rec["all_candidate_ids"] = new
        applied.append((member, cur, new, what))

    label = "CORRECTED" if COMMIT else "WOULD CORRECT"
    print(f"{label} ({len(applied)}):")
    for member, cur, new, what in applied:
        print(f"  {member:16s} {str(cur):34s} -> {new}")
        print(f"      removed id was: {what}")

    if refused:
        print(f"\nREFUSED ({len(refused)}):")
        for member, why in refused:
            print(f"  {member:16s} {why}")

    if COMMIT and applied:
        with open(path + ".primaryids.bak", "w") as fh:
            fh.write(raw)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(fec, fh, indent=1, ensure_ascii=False)
        os.replace(tmp, path)
        print(f"\nWROTE {path}")
        print(f"(backup of original: {path}.primaryids.bak)")
        print("\nNEXT: run audit_committee_ids.py, then refresh_total_raised.py. "
              "Correcting an ID does not recompute money, and the committees "
              "array for these members is very likely wrong too.")
    elif COMMIT:
        print("\nNothing applied, so nothing written.")
    else:
        print("\nDRY-RUN complete. Re-run with commit=true to apply.")

    if refused:
        sys.exit(3)


if __name__ == "__main__":
    main()
