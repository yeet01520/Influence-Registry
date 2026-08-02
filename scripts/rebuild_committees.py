#!/usr/bin/env python3
"""
rebuild_committees.py
=====================
Rewrites the `committees` array for EVERY member so it is exactly the set of
committees FEC authorizes for that member's candidate IDs.

WHY
---
validate_data.py compares two independent measures of the same quantity:

  grassroots        summed from the committees stored in fec.json
  individual_total  from the FEC candidate totals endpoint

63 members disagree by more than 10%. The direction of the disagreement splits
them cleanly into two causes, and one operation fixes both.

  53 members have individual_total LARGER. Their stored committee list is
  incomplete. Chuck Schumer has two candidate IDs (a Senate and a House
  career) but only three committees on file; Jack Reed has two candidate IDs
  and exactly one committee. The candidate endpoint counts everything FEC
  attributes to both candidacies, so the two measures cannot agree while the
  committee list is missing entries.

  10 members have grassroots LARGER. Roger Marshall has one candidate ID and
  one committee, yet his grassroots is 7.49x his individual_total. A single
  committee raising seven times what FEC attributes to the candidate is the
  signature of a JOINT FUNDRAISING COMMITTEE in the array. A JFC pools money
  for many candidates and transfers each their share, so its receipts are not
  one member's receipts.

Rebuilding from FEC's authorized list adds what is missing and drops what does
not belong, in one pass.

WHAT COUNTS AS THE MEMBER'S OWN
-------------------------------
FEC committee designations:
  P  principal campaign committee   KEPT
  A  authorized by the candidate    KEPT
  J  joint fundraising committee    DROPPED, pooled money, not theirs alone
  D  leadership PAC                 DROPPED, money they GIVE to others
  U  unauthorized                   DROPPED

SAFETY
------
  - Dry-run by default. Pass --commit, or set COMMIT_INPUT=true, to write.
  - A member is NEVER left with an empty committees array. If FEC returns
    nothing usable, the existing array is kept and the member is reported.
  - Members with no candidate IDs are skipped untouched; there is nothing to
    ask FEC about.
  - Writes data/fec.json.rebuild.bak before saving, and saves incrementally.
  - The report shows added and removed committees per member, so the diff is
    reviewable before it is applied.

THIS DOES NOT RECOMPUTE MONEY
-----------------------------
Run refresh_total_raised.py afterwards, then validate_data.py to confirm the
63 mismatches actually resolved. If they do not, the remaining ones are a real
finding rather than a bookkeeping artifact.

USAGE
-----
  python3 scripts/rebuild_committees.py              # report only
  python3 scripts/rebuild_committees.py --limit 25   # smoke test
  python3 scripts/rebuild_committees.py --commit     # apply
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from collections import deque

BASE = "https://api.open.fec.gov/v1"
HERE = os.path.dirname(__file__)
FEC_PATH = os.path.join(HERE, "..", "data", "fec.json")
REPORT_PATH = os.path.join(HERE, "..", "data", "committee_rebuild.json")

HOURLY_BUDGET = 7000
MINUTE_BUDGET = 110
MAX_CONSECUTIVE_429 = 10
KEEP_DESIGNATIONS = ("P", "A")

API_KEY = os.environ.get("FEC_API_KEY", "").strip()
if not API_KEY:
    print("ERROR: FEC_API_KEY environment variable is not set.")
    sys.exit(1)


def _arg(flag, cast=str, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            try:
                return cast(sys.argv[i + 1])
            except ValueError:
                return default
    return default


def _env_true(n):
    return os.environ.get(n, "").strip().lower() == "true"


def _env_int(n):
    v = os.environ.get(n, "").strip()
    return int(v) if v.isdigit() else None


COMMIT = ("--commit" in sys.argv) or _env_true("COMMIT_INPUT")
LIMIT = _arg("--limit", int) or _env_int("LIMIT_INPUT")

_min_win, _hr_win = deque(), deque()
_stats = {"calls": 0, "429s": 0, "consec429": 0}


class BudgetExhausted(Exception):
    pass


def _throttle():
    while True:
        now = time.time()
        while _min_win and now - _min_win[0] > 60:
            _min_win.popleft()
        while _hr_win and now - _hr_win[0] > 3600:
            _hr_win.popleft()
        wait = 0.0
        if len(_min_win) >= MINUTE_BUDGET:
            wait = max(wait, 60 - (now - _min_win[0]) + 0.5)
        if len(_hr_win) >= HOURLY_BUDGET:
            wait = max(wait, 3600 - (now - _hr_win[0]) + 1)
        if wait <= 0:
            _min_win.append(now)
            _hr_win.append(now)
            return
        print(f"\n  [budget throttle: waiting {wait:.0f}s]", flush=True)
        time.sleep(wait)


def get(path, params=None):
    p = dict(params or {})
    p["api_key"] = API_KEY
    url = BASE + path + "?" + urllib.parse.urlencode(p, doseq=True)
    for _ in range(5):
        _throttle()
        _stats["calls"] += 1
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                _stats["consec429"] = 0
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                _stats["429s"] += 1
                _stats["consec429"] += 1
                if _stats["consec429"] >= MAX_CONSECUTIVE_429:
                    raise BudgetExhausted(
                        f"{_stats['consec429']} consecutive 429s. Rate budget exhausted.")
                print(f"\n  [429, backing off 65s "
                      f"({_stats['consec429']}/{MAX_CONSECUTIVE_429})]", flush=True)
                time.sleep(65)
                continue
            if e.code in (400, 404, 422):
                return None
            time.sleep(3)
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


def save(path, fec, raw, done):
    if not done:
        with open(path + ".rebuild.bak", "w") as fh:
            fh.write(raw)
        print(f"  (backup written: {path}.rebuild.bak)", flush=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(fec, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path)
    return True


def main():
    started = time.time()
    path = os.path.abspath(FEC_PATH)
    raw = open(path).read()
    fec = json.loads(raw)
    members = [(k, v) for k, v in fec.items() if k != "_meta" and isinstance(v, dict)]
    if LIMIT:
        members = members[:LIMIT]

    print(f"Loaded {len(members)} members")
    print("Flag resolution ->  argv: " + " ".join(sys.argv[1:] or ["(none)"]))
    print("                    env : COMMIT_INPUT=%r LIMIT_INPUT=%r"
          % (os.environ.get("COMMIT_INPUT"), os.environ.get("LIMIT_INPUT")))
    print(f"Mode: {'COMMIT (WILL WRITE data/fec.json)' if COMMIT else 'REPORT ONLY (no write)'}")
    print(f"Keeping FEC designations {KEEP_DESIGNATIONS}; dropping joint "
          f"fundraising committees and leadership PACs\n")

    changed, unchanged, skipped, kept_existing = [], 0, [], []
    cache, backup = {}, False

    for idx, (name, rec) in enumerate(members):
        ids = member_ids(rec)
        if not ids:
            skipped.append(name)
            continue
        found, dropped, seen = [], [], set()
        failed = False
        for cid in ids:
            if cid in cache:
                data = cache[cid]
            else:
                try:
                    data = get(f"/candidate/{cid}/committees/", {"per_page": 100})
                except BudgetExhausted as e:
                    print(f"\nFATAL: {e}\nStopped at member {idx + 1}/{len(members)}.")
                    if COMMIT and changed:
                        save(path, fec, raw, backup)
                        print(f"Partial progress saved: {len(changed)} members.")
                    sys.exit(2)
                cache[cid] = data
            if data is None:
                failed = True
                continue
            for c in (data.get("results") or []):
                cc = c.get("committee_id")
                if not cc or cc in seen:
                    continue
                seen.add(cc)
                des = (c.get("designation") or "").upper()
                entry = (cc, c.get("name") or "", c.get("designation_full") or "")
                (found if des in KEEP_DESIGNATIONS else dropped).append(entry)

        old = list(rec.get("committees") or [])
        if not found:
            # No usable answer is not a reason to erase what is on file.
            if old:
                kept_existing.append((name, old, "FEC returned no authorized committees"
                                      if not failed else "lookup failed"))
            continue

        new = [c[0] for c in found]
        added = [c for c in found if c[0] not in old]
        removed = [c for c in old if c not in new]
        if not added and not removed:
            unchanged += 1
            continue

        if COMMIT:
            rec["committees"] = new
            if len(changed) % 25 == 0:
                backup = save(path, fec, raw, backup)
        changed.append({"member": name, "before": old, "after": new,
                        "added": [{"id": c[0], "name": c[1]} for c in added],
                        "removed": removed,
                        "dropped_not_own": [{"id": c[0], "name": c[1],
                                             "designation": c[2]} for c in dropped]})

        if (idx + 1) % 50 == 0:
            print(f"  ...{idx + 1}/{len(members)}  [calls {_stats['calls']}, "
                  f"{(time.time() - started) / 60:.1f} min]", flush=True)

    print("\n" + "=" * 70)
    print(f"API calls: {_stats['calls']}   429s: {_stats['429s']}   "
          f"total: {(time.time() - started) / 60:.1f} min")
    print(f"Unchanged: {unchanged}   {'Changed' if COMMIT else 'Would change'}: {len(changed)}")
    print(f"No candidate IDs (skipped): {len(skipped)}")
    print(f"Kept existing because FEC gave nothing usable: {len(kept_existing)}")

    add_only = [c for c in changed if c["added"] and not c["removed"]]
    rem_only = [c for c in changed if c["removed"] and not c["added"]]
    both = [c for c in changed if c["added"] and c["removed"]]
    print(f"\n  additions only: {len(add_only)}   removals only: {len(rem_only)}"
          f"   both: {len(both)}")

    print("\nBiggest changes:")
    for c in sorted(changed, key=lambda x: -(len(x["added"]) + len(x["removed"])))[:15]:
        print(f"  {c['member']}")
        for a in c["added"][:4]:
            print(f"      + {a['id']}  {a['name'][:44]}")
        for r in c["removed"][:4]:
            print(f"      - {r}")
        for dpp in c["dropped_not_own"][:3]:
            print(f"      (skipped {dpp['id']} {dpp['name'][:34]} \u2014 {dpp['designation']})")

    if kept_existing:
        print(f"\nKept existing array, review these ({len(kept_existing)}):")
        for n, old, why in kept_existing[:12]:
            print(f"  {n:24s} {old}  ({why})")

    if COMMIT and changed:
        save(path, fec, raw, backup)
        print(f"\nWROTE {path}")
        print("\nNEXT: run refresh_total_raised.py, then validate_data.py. If the "
              "63 grassroots/individual_total mismatches do not drop sharply, the "
              "remainder is a real finding rather than a bookkeeping artifact.")
    elif not COMMIT:
        print("\nREPORT ONLY. Nothing written. Re-run with commit=true to apply.")

    with open(os.path.abspath(REPORT_PATH), "w") as fh:
        json.dump({"changed": changed, "skipped_no_ids": skipped,
                   "kept_existing": [{"member": n, "committees": o, "reason": w}
                                     for n, o, w in kept_existing],
                   "unchanged_count": unchanged, "committed": bool(COMMIT)},
                  fh, indent=1, ensure_ascii=False)
    print(f"Findings written to {os.path.abspath(REPORT_PATH)}")


if __name__ == "__main__":
    main()
