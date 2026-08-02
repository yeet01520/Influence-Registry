#!/usr/bin/env python3
"""
audit_committee_ids.py
======================
Verifies that every committee ID in data/fec.json belongs to the member it is
filed under, and strips the ones that do not.

WHY THIS EXISTS
---------------
audit_candidate_ids.py cleaned the candidate_id / all_candidate_ids lists.
That fixed the PAC/individual split, which is driven by candidate IDs. It did
NOT fix total_raised or grassroots, because refresh_total_raised.py and the v7
pull read the SEPARATE `committees` array, and that array is contaminated the
same way and by the same original cause.

The clearest case: Dave Joyce and Joyce Beatty both list committee C00507368.
FEC says that committee is BEATTY FOR CONGRESS. Their total_raised and
grassroots figures are consequently identical, to the dollar.

This matters beyond one field. The B2 score uses total_raised as its
denominator, so a member carrying someone else's committee has a wrong
headline score, not just a wrong money figure.

WHY THIS IS MORE RELIABLE THAN THE CANDIDATE ID AUDIT
-----------------------------------------------------
That audit had to compare names, which is fuzzy: members file under legal
names they do not go by, and relatives share surnames. This one does not
compare names at all. /committee/{id}/ returns candidate_ids, an explicit
list of the candidates the committee is authorized for. A committee either
intersects the member's candidate IDs or it does not. No thresholds, no
nickname handling, no judgement calls.

That is also why this script auto-strips its CONFIRMED bucket without a
separate human-review pass, unlike the candidate ID audit.

WHAT IT DOES NOT TOUCH
----------------------
  - Committees FEC reports with no candidate_ids at all (leadership PACs,
    joint fundraising committees). Those cannot be checked this way, so they
    are reported as UNLINKED and left alone.
  - Members left with zero committees. Reported as NEEDS REVIEW instead,
    because removal cannot fix a member who has no correct committee on file.
  - Any lookup that fails. No answer is not evidence.

SAFETY
------
  - Dry-run by default. Pass --commit, or set COMMIT_INPUT=true, to write.
  - Writes data/fec.json.committees.bak before saving.
  - Saves incrementally so a killed run keeps its progress.

USAGE
-----
  python3 scripts/audit_committee_ids.py               # report only
  python3 scripts/audit_committee_ids.py --limit 40    # smoke test
  python3 scripts/audit_committee_ids.py --commit      # strip

AFTER RUNNING
-------------
Stripping a committee does NOT recompute money. Re-run refresh_total_raised.py
and the v7 / grassroots refresh next, or the stale figures stay stale.
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from collections import deque, defaultdict

BASE = "https://api.open.fec.gov/v1"
HERE = os.path.dirname(__file__)
FEC_PATH = os.path.join(HERE, "..", "data", "fec.json")
REPORT_PATH = os.path.join(HERE, "..", "data", "committee_id_audit.json")

HOURLY_BUDGET = 7000
MINUTE_BUDGET = 110
MAX_CONSECUTIVE_429 = 10

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
                        f"{_stats['consec429']} consecutive 429s. Rate budget exhausted."
                    )
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


def member_cand_ids(rec):
    out = set()
    for k in ("all_candidate_ids", "candidate_id"):
        v = rec.get(k)
        vals = v if isinstance(v, list) else ([v] if isinstance(v, str) and v else [])
        out.update(i for i in vals if i)
    return out


def save(path, fec, raw, done):
    if not done:
        with open(path + ".committees.bak", "w") as fh:
            fh.write(raw)
        print(f"  (backup written: {path}.committees.bak)", flush=True)
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

    claims = defaultdict(list)
    for name, rec in members:
        for cid in (rec.get("committees") or []):
            claims[cid].append(name)

    ids = sorted(claims)
    if LIMIT:
        ids = ids[:LIMIT]

    print(f"Loaded {len(members)} members, {len(claims)} unique committee IDs")
    print("Flag resolution ->  argv: " + " ".join(sys.argv[1:] or ["(none)"]))
    print("                    env : COMMIT_INPUT=%r LIMIT_INPUT=%r"
          % (os.environ.get("COMMIT_INPUT"), os.environ.get("LIMIT_INPUT")))
    print(f"Mode: {'COMMIT (WILL WRITE data/fec.json)' if COMMIT else 'REPORT ONLY (no write)'}")
    print(f"Looking up {len(ids)} committees (~{len(ids)} calls)\n")

    info, failed = {}, []
    for n, cid in enumerate(ids):
        try:
            data = get(f"/committee/{cid}/", {"per_page": 1})
        except BudgetExhausted as e:
            print(f"\nFATAL: {e}\nStopped after {n} of {len(ids)}.")
            sys.exit(2)
        res = (data or {}).get("results") or []
        if not res:
            failed.append(cid)
        else:
            r = res[0]
            info[cid] = {"name": r.get("name") or "",
                         "cands": list(r.get("candidate_ids") or []),
                         "designation": r.get("designation_full") or ""}
        if (n + 1) % 50 == 0:
            print(f"  ...{n + 1}/{len(ids)}  [calls {_stats['calls']}, "
                  f"{(time.time() - started) / 60:.1f} min]", flush=True)

    confirmed, unlinked = [], []
    for cid in ids:
        meta = info.get(cid)
        if not meta:
            continue
        for m in claims[cid]:
            if not meta["cands"]:
                unlinked.append({"committee": cid, "member": m, "cmt_name": meta["name"],
                                 "designation": meta["designation"]})
                continue
            if member_cand_ids(fec[m]) & set(meta["cands"]):
                continue
            confirmed.append({"committee": cid, "member": m, "cmt_name": meta["name"],
                              "cmt_candidates": meta["cands"],
                              "member_candidates": sorted(member_cand_ids(fec[m]))})

    removals = defaultdict(list)
    for f in confirmed:
        removals[f["member"]].append(f["committee"])

    applied, blocked, backup = 0, [], False
    for m, bad in removals.items():
        cur = fec[m].get("committees") or []
        keep = [c for c in cur if c not in bad]
        if not keep:
            blocked.append((m, bad))
            continue
        if COMMIT:
            fec[m]["committees"] = keep
            backup = save(path, fec, raw, backup)
        applied += 1

    # REPAIR PHASE
    # ------------
    # A member whose every committee belongs to someone else has no correct
    # committee on file, so stripping cannot fix them: it would leave the
    # array empty and total_raised would go to zero. These are almost all the
    # members whose primary candidate ID was wrong until it was corrected;
    # fixing the ID is what exposed that their committees were wrong too.
    #
    # FEC can answer this directly. /candidate/{id}/committees/ returns the
    # committees authorized for a candidate, so we ask for the member's own
    # (now correct) candidate IDs and rebuild the array from that.
    #
    # This only ever REPLACES a set that is already known to be entirely
    # wrong, and only when FEC returns at least one committee. If the lookup
    # comes back empty the member is left exactly as they were and reported,
    # because an empty answer is not a reason to erase what is on file.
    repaired, unrepaired = [], []
    for m, bad in blocked:
        found, seen, skipped = [], set(), []
        for cid in sorted(member_cand_ids(fec[m])):
            try:
                data = get(f"/candidate/{cid}/committees/", {"per_page": 100})
            except BudgetExhausted:
                break
            for c in ((data or {}).get("results") or []):
                cc = c.get("committee_id")
                if not cc or cc in seen:
                    continue
                seen.add(cc)
                des = (c.get("designation") or "").upper()
                desf = c.get("designation_full") or ""
                # Only the member's OWN committees count toward their
                # fundraising. FEC designations:
                #   P = principal campaign committee
                #   A = authorized by the candidate
                #   J = JOINT FUNDRAISING COMMITTEE  <- excluded
                #   D = leadership PAC               <- excluded
                # A JFC pools money for many candidates at once and transfers
                # each their share, so its receipts are not this member's
                # receipts. Counting one would overstate total_raised, and
                # since total_raised is the denominator of the B2 score it
                # would quietly make the member look more independent than
                # they are. Leadership PACs are money the member GIVES to
                # others, which is not their own fundraising either.
                if des not in ("P", "A"):
                    skipped.append((cc, c.get("name") or "", desf))
                    continue
                found.append((cc, c.get("name") or "", desf))
        if not found:
            unrepaired.append((m, bad))
            continue
        found.sort(key=lambda c: (0 if 'Principal' in c[2] else 1, c[0]))
        if COMMIT:
            fec[m]["committees"] = [c[0] for c in found]
            backup = save(path, fec, raw, backup)
        repaired.append((m, bad, found, skipped))

    print("\n" + "=" * 70)
    print(f"Committees checked: {len(ids)}   lookup failed (left alone): {len(failed)}")
    print(f"API calls: {_stats['calls']}   429s: {_stats['429s']}   "
          f"total: {(time.time() - started) / 60:.1f} min")

    print(f"\n{'STRIPPED' if COMMIT else 'WOULD STRIP'} ({applied} members, "
          f"{sum(len(v) for m, v in removals.items() if m not in dict(blocked))} committees):")
    for f in confirmed:
        if f["member"] in dict(blocked):
            continue
        print(f"  {f['member']:24s} {f['committee']}  {f['cmt_name'][:38]}")
        print(f"      committee is authorized for {f['cmt_candidates']}, "
              f"member has {f['member_candidates']}")

    if repaired:
        print(f"\n{'REPAIRED' if COMMIT else 'WOULD REPAIR'} ({len(repaired)}): every "
              "committee on file belonged to someone else, so the array was rebuilt "
              "from FEC's own list for this member.")
        for m, bad, found, skipped in repaired:
            print(f"  {m}")
            print(f"      removed (not theirs): {bad}")
            for cc, nm, des in found:
                print(f"      added   {cc}  {nm[:40]:40s} {des[:26]}")
            for cc, nm, des in skipped:
                print(f"      skipped {cc}  {nm[:40]:40s} {des[:26]} (not own fundraising)")

    if unrepaired:
        print(f"\nNEEDS REVIEW ({len(unrepaired)}): every committee belongs to someone "
              "else AND FEC returned no committees for their candidate IDs. "
              "Left untouched.")
        for m, bad in unrepaired:
            print(f"  {m:24s} committees on file {bad}  "
                  f"candidate ids {sorted(member_cand_ids(fec[m]))}")

    if unlinked:
        print(f"\nUNLINKED ({len(unlinked)}): FEC reports no candidate for these, "
              "so they cannot be checked. Left alone.")
        for u in unlinked[:20]:
            print(f"  {u['member']:24s} {u['committee']}  {u['cmt_name'][:34]}  "
                  f"{u['designation'][:22]}")
        if len(unlinked) > 20:
            print(f"  ...and {len(unlinked) - 20} more")

    if failed:
        print(f"\nLookup failed ({len(failed)}): {failed[:15]}")

    if COMMIT and applied:
        save(path, fec, raw, backup)
        print(f"\nWROTE {path}")
        print("\nNOTE: this does NOT recompute money. Run refresh_total_raised.py "
              "and the v7/grassroots refresh next.")
    elif not COMMIT:
        print("\nREPORT ONLY. Nothing written. Re-run with commit=true to strip.")

    with open(os.path.abspath(REPORT_PATH), "w") as fh:
        json.dump({"confirmed": confirmed, "unlinked": unlinked,
                   "repaired": [{"member": m, "removed": bad,
                              "added": [{"id": c[0], "name": c[1]} for c in f],
                              "skipped_not_own": [{"id": c[0], "name": c[1],
                                                   "designation": c[2]} for c in sk]}
                             for m, bad, f, sk in repaired],
                   "needs_review": [m for m, _ in unrepaired],
                   "lookup_failed": failed, "committed": bool(COMMIT)},
                  fh, indent=1, ensure_ascii=False)
    print(f"Findings written to {os.path.abspath(REPORT_PATH)}")

    if confirmed and not COMMIT:
        sys.exit(3)


if __name__ == "__main__":
    main()
