#!/usr/bin/env python3
"""
restore_own_candidate_ids.py
============================
Puts back candidate IDs that fix_primary_candidate_ids.py removed even though
FEC says they belong to the member.

WHAT WENT WRONG
---------------
fix_primary_candidate_ids.py corrected 13 members whose primary candidate ID
did not appear in the official congress-legislators FEC id list. For 5 of them
the stored id demonstrably belonged to somebody else (Dave Joyce holding Joyce
Beatty's, Mike Collins holding Paul Broun's), and removing it was right.

For 7 others the stored id was simply absent from the official list, and the
script treated absent as wrong. It is not. The official file lists a member's
CURRENT id; older ids from earlier candidacies are routinely omitted. Checking
each removed id against FEC directly:

    H2MD04232  IVEY, GLENN FREDERICK      $1,904,178 over 5 cycles
    H2TX00064  SELF, KEITH ALAN             $713,057 over 3 cycles
    H2NY04244  GILLEN, LAURA
    H4PA03117  KELLY, MIKE
    H0GA02241  ALLEN, RICK
    S8MI00372  JAMES, JOHN
    H0NJ07089  KEAN, THOMAS H JR

Every one is the member's own. Worse, the replacement ids for Ivey, Self and
Gillen have NO filings at FEC at all, so once total_raised moved to the
candidate route those three dropped to $0 raised. Removing the working id took
their money with it.

The guard in that script ("refuse if the official list also contains the old
id") could not catch this, because the official list contains neither.

WHAT THIS DOES
--------------
Restores each id below as an ADDITIONAL entry. It does not change any primary.
The candidate route sums across every id a member holds, so restoring these
returns the missing history without disturbing the corrected primaries.

Ids that belong to other people stay removed. They are listed in
DELIBERATELY_NOT_RESTORED so nobody re-adds them later without reading why.

VERIFICATION
------------
Each restoration is re-checked against FEC at runtime: the script asks
/candidate/{id}/ for the registered name and only restores when that name
matches the member it is being restored to. A mismatch is refused and
reported. Requires FEC_API_KEY.

USAGE
-----
  python3 scripts/restore_own_candidate_ids.py            # dry run
  python3 scripts/restore_own_candidate_ids.py --commit   # apply

AFTER RUNNING
-------------
Re-run refresh_total_raised.py, then fetch_pac_individual_split.py, then
validate_data.py. The three $0 members should regain real figures.
"""

import json
import os
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import urllib.error

BASE = "https://api.open.fec.gov/v1"
HERE = os.path.dirname(__file__)
FEC_PATH = os.path.join(HERE, "..", "data", "fec.json")

COMMIT = ("--commit" in sys.argv) or \
         (os.environ.get("COMMIT_INPUT", "").strip().lower() == "true")

API_KEY = os.environ.get("FEC_API_KEY", "").strip()
if not API_KEY:
    print("ERROR: FEC_API_KEY environment variable is not set.")
    sys.exit(1)

# member, id to restore, the surname FEC has registered for it
RESTORE = [
    ("Glenn Ivey",   "H2MD04232", "IVEY"),
    ("Keith Self",   "H2TX00064", "SELF"),
    ("Laura Gillen", "H2NY04244", "GILLEN"),
    ("Mike Kelly",   "H4PA03117", "KELLY"),
    ("Rick Allen",   "H0GA02241", "ALLEN"),
    ("John James",   "S8MI00372", "JAMES"),
    ("Tom Kean Jr.", "H0NJ07089", "KEAN"),
]

# Removed on purpose. FEC says these belong to other people; restoring any of
# them would put a stranger's money back into a member's record.
DELIBERATELY_NOT_RESTORED = [
    ("Dave Joyce",   "H2OH03125", "BEATTY, JOYCE"),
    ("Mike Collins", "H0GA03017", "BROUN, PAUL COLLINS"),
    ("Mike Collins", "S6GA00101", "BROUN, PAUL COLLINS"),
    ("Bobby Scott",  "H2VA10026", "BOWDEN, SCOTT R"),
    ("Bobby Scott",  "S4VA00312", "PARKINSON, SCOTT THOMAS"),
    ("Dick Durbin",  "S4IL00339", "DURBIN, JEFFREY WILLIAM"),
    ("Nick Begich",  "H4AK00024", "BEGICH, MARGARET (PEGGE)"),
    ("Andy Harris",  "H8MD01023", "no such candidate at FEC"),
]


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c if c.isalnum() else " " for c in s.upper()).split()


def get(path):
    url = BASE + path + "?" + urllib.parse.urlencode({"api_key": API_KEY, "per_page": 1})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print("      rate limited, waiting 65s")
                time.sleep(65)
                continue
            if e.code in (400, 404, 422):
                return None
            time.sleep(2 ** attempt)
        except Exception:
            time.sleep(2)
    return None


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

    print(f"Mode: {'COMMIT (WILL WRITE data/fec.json)' if COMMIT else 'DRY-RUN (no write)'}")
    print(f"Candidate IDs to restore: {len(RESTORE)}\n")

    restored, refused = [], []
    for name, cid, surname in RESTORE:
        rec = fec.get(name)
        if rec is None:
            refused.append((name, cid, "member not in fec.json"))
            continue
        have = member_ids(rec)
        if cid in have:
            refused.append((name, cid, "already present"))
            continue
        data = get(f"/candidate/{cid}/")
        res = (data or {}).get("results") or []
        if not res:
            refused.append((name, cid, "FEC has no such candidate"))
            continue
        fec_name = res[0].get("name") or ""
        # The registered surname must match the member this is restored to.
        # Absent that check we would be trusting a hand-typed list against
        # live data, which is exactly the mistake being corrected here.
        if surname.upper() not in norm(fec_name):
            refused.append((name, cid, f"FEC says {fec_name!r}, expected surname {surname}"))
            continue
        if COMMIT:
            rec["all_candidate_ids"] = have + [cid]
        restored.append((name, cid, fec_name, have))

    label = "RESTORED" if COMMIT else "WOULD RESTORE"
    print(f"{label} ({len(restored)}):")
    for name, cid, fec_name, have in restored:
        print(f"  {name:16s} + {cid}  FEC: {fec_name:28s} (had {have})")

    if refused:
        print(f"\nREFUSED ({len(refused)}):")
        for name, cid, why in refused:
            print(f"  {name:16s} {cid}  {why}")

    print(f"\nDeliberately NOT restored ({len(DELIBERATELY_NOT_RESTORED)}), "
          "these belong to other people:")
    for name, cid, owner in DELIBERATELY_NOT_RESTORED:
        print(f"  {name:16s} {cid}  {owner}")

    if COMMIT and restored:
        with open(path + ".restoreids.bak", "w") as f:
            f.write(raw)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(fec, f, indent=1, ensure_ascii=False)
        os.replace(tmp, path)
        print(f"\nWROTE {path}")
        print(f"(backup of original: {path}.restoreids.bak)")
        print("\nNEXT: refresh_total_raised.py, fetch_pac_individual_split.py, "
              "validate_data.py. Glenn Ivey, Keith Self and Laura Gillen "
              "should stop showing $0 raised.")
    elif COMMIT:
        print("\nNothing restored, so nothing written.")
    else:
        print("\nDRY-RUN complete. Re-run with commit=true to apply.")

    if refused:
        sys.exit(3)


if __name__ == "__main__":
    main()
