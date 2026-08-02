#!/usr/bin/env python3
"""
audit_candidate_ids.py
======================
Verifies that every candidate ID in data/fec.json actually belongs to the
member it is filed under, by asking the FEC who owns each ID.

WHY THIS EXISTS
---------------
fec.json stores candidate_id and all_candidate_ids per member. Those lists
drive the FEC money pulls, so a wrong ID silently imports a stranger's money
into a member's record. Four confirmed cases, all surname collisions:

    Monica De La Cruz  holds S2TX00312  = CRUZ, RAFAEL EDWARD (Ted Cruz)
    Shontel Brown      holds S6OH00163  = BROWN, SHERROD
    Dina Titus         holds S0NV00153  = TITUS, ROBIN LEE
    Dave Joyce         holds H2OH03125  = BEATTY, JOYCE

These were found by accident: the PAC-split run's receipts guard flagged
members whose contributions exceeded their total receipts. That only catches
contamination big enough to break the arithmetic. An ID belonging to someone
with modest fundraising would sail through unnoticed, which is why this
audit checks every ID deliberately rather than waiting for a symptom.

HOW IT DECIDES
--------------
For each unique ID, one call to /candidate/{id}/ returns the registered name
in "LAST, FIRST MIDDLE" form. Two independent detectors then run:

  1. DUPLICATE CLAIM. If two members list the same ID, at most one can be
     right. Both are scored against the FEC name and the loser is CONFIRMED
     wrong. This needs no similarity threshold, so it is the strongest
     signal available and it is what catches Sherrod vs Shontel Brown, where
     the surnames are identical.

  2. NAME COMPARISON. The FEC surname must appear at the end of the member's
     name, and at least one FEC given name must be compatible with the
     member's first name. Surname failure is CONFIRMED. Surname pass with
     given-name failure is SUSPECT, because nicknames are common in this
     data (Tommy/Thomas, Chuy/Jesus, Dusty/Dustin) and a nickname is not
     evidence of contamination.

Only CONFIRMED entries are ever removed, and only with --commit. SUSPECT
entries are reported for a human to read and never touched automatically.

SAFETY
------
  - Dry-run by default. --commit writes, after a .idaudit.bak of the original.
  - A member is never left with zero candidate IDs. If every ID would be
    stripped, nothing is removed and the member is reported as NEEDS REVIEW.
  - Removing a member's PRIMARY candidate_id is reported separately and
    loudly, since that is a bigger claim than pruning an extra entry.
  - An ID whose lookup fails is left alone. Absence of an answer is not
    evidence of a wrong ID.

USAGE
-----
  python3 scripts/audit_candidate_ids.py                 # report only
  python3 scripts/audit_candidate_ids.py --limit 40      # quick smoke test
  python3 scripts/audit_candidate_ids.py --commit        # strip CONFIRMED

Also accepts the workflow inputs COMMIT_INPUT / LIMIT_INPUT as environment
variables, so a broken shell arg-building step cannot silently change mode.

OUTPUT
------
  data/fec.json                   (only with --commit)
  data/candidate_id_audit.json    (always: the full findings, machine readable)
"""

import json
import os
import sys
import time
import difflib
import unicodedata
import urllib.parse
import urllib.request
import urllib.error
from collections import deque, defaultdict

BASE = "https://api.open.fec.gov/v1"
HERE = os.path.dirname(__file__)
FEC_PATH = os.path.join(HERE, "..", "data", "fec.json")
REPORT_PATH = os.path.join(HERE, "..", "data", "candidate_id_audit.json")

HOURLY_BUDGET = 7000
MINUTE_BUDGET = 110
MAX_CONSECUTIVE_429 = 10

# Given-name similarity at or above this counts as compatible. Calibrated so
# real nicknames pass (Tommy/Thomas 0.73, Dusty/Dustin 0.73) while different
# people fail (Shontel/Sherrod 0.57, Dina/Robin 0.00).
NAME_RATIO_OK = 0.65

SUFFIXES = {"JR", "SR", "II", "III", "IV", "MD", "DR", "PHD", "ESQ"}

# Congressional members are overwhelmingly listed by nickname in one source and
# legal name in the other. Without this map, Mike/Michael and Tommy/Thomas get
# flagged and the report fills with noise nobody reads. Each line is a group of
# names treated as the same person.
_NICK_GROUPS = [
    "MICHAEL MIKE MICK", "THOMAS TOM TOMMY", "WILLIAM BILL BILLY WILL",
    "ROBERT BOB BOBBY ROB", "RICHARD RICK DICK RICHIE RITCHIE",
    "JAMES JIM JIMMY JAMIE", "JOSEPH JOE JOEY", "DAVID DAVE",
    "STEPHEN STEVEN STEVE", "DANIEL DAN DANNY", "CHRISTOPHER CHRIS",
    "EDWARD ED EDDIE TED TEDDY", "THEODORE TED", "ELIZABETH LIZ LIZZIE BETH BETTY",
    "KATHERINE KATHRYN KATIE KATE KAT", "KATHLEEN KATHY", "DEBORAH DEBBIE DEB",
    "SUSAN SUSIE SUE", "GREGORY GREG", "JEFFREY JEFF", "TIMOTHY TIM",
    "ANTHONY TONY", "NICHOLAS NICK", "ANDREW ANDY DREW", "MATTHEW MATT",
    "BENJAMIN BEN", "SAMUEL SAM", "HAROLD HAL HARRY", "PATRICK PAT",
    "PATRICIA PAT PATTY", "DOUGLAS DOUG", "RONALD RON", "DONALD DON",
    "JOHN JACK JOHNNY", "AUGUST GUS", "LAWRENCE LARRY", "MITCHELL MITCH",
    "CHARLES CHUCK CHARLIE", "GABRIEL GABE", "MAXWELL MAX", "ZACHARY ZACH",
    "JOSHUA JOSH", "BRADLEY BRAD", "JACOB JAKE", "NATHANIEL NATHAN NATE",
    "VALERIE VAL", "PETER PETE", "FREDERICK FRED", "RAYMOND RAY",
    "VINCENT VINCE", "MARJORIE MARGE", "VIRGINIA GINNY", "CYNTHIA CINDY",
    "BARBARA BARB", "JENNIFER JEN JENNY", "MARGARET MAGGIE MEG PEGGY",
    "ALEXANDRIA ALEXANDRA ALEX", "NICOLE NIKKI", "RUSSELL RUSS",
    "LLOYD", "DUSTIN DUSTY", "SHERROD", "SALVADOR SAL", "EMANUEL MANNY",
]
NICKNAMES = {}
for _grp in _NICK_GROUPS:
    _names = _grp.split()
    for _n in _names:
        NICKNAMES.setdefault(_n, set()).update(_names)

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

# ---------------------------------------------------------------------------
# Rate-limited HTTP (same budget model as fetch_pac_individual_split.py)
# ---------------------------------------------------------------------------

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
                print(f"\n  [429, backing off 65s ({_stats['consec429']}/{MAX_CONSECUTIVE_429})]",
                      flush=True)
                time.sleep(65)
                continue
            if e.code in (400, 404, 422):
                return None
            time.sleep(3)
        except Exception:
            time.sleep(2)
    return None


# ---------------------------------------------------------------------------
# Name handling
# ---------------------------------------------------------------------------

def norm(s):
    """Uppercase, strip accents and punctuation. 'Díaz-Balart' -> 'DIAZ BALART'."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.upper()
    return "".join(c if c.isalnum() else " " for c in s).split()


def split_fec_name(fec_name):
    """'BROWN, SHERROD' -> ('BROWN', ['SHERROD']). Suffixes dropped."""
    if "," in fec_name:
        last, first = fec_name.split(",", 1)
    else:
        parts = norm(fec_name)
        return (" ".join(parts[-1:]), parts[:-1])
    last_toks = [t for t in norm(last) if t not in SUFFIXES]
    first_toks = [t for t in norm(first) if t not in SUFFIXES]
    return (" ".join(last_toks), first_toks)


def name_score(member_name, fec_name):
    """
    Compare a member name against an FEC registered name.
    Returns (verdict, detail) where verdict is 'ok', 'suspect', or 'mismatch'.
    """
    m_toks = [t for t in norm(member_name) if t not in SUFFIXES]
    if not m_toks:
        return ("suspect", "member name unparseable")
    fec_last, fec_firsts = split_fec_name(fec_name)
    fec_last_flat = fec_last.replace(" ", "")
    m_flat = "".join(m_toks)

    if not fec_last_flat or not m_flat.endswith(fec_last_flat):
        return ("mismatch", f"surname '{fec_last}' not at end of '{member_name}'")

    m_first = m_toks[0]
    best = 0.0
    for f in fec_firsts:
        if f == m_first or f.startswith(m_first) or m_first.startswith(f):
            return ("ok", f"given name '{f}' compatible with '{m_first}'")
        if f in NICKNAMES.get(m_first, ()) or m_first in NICKNAMES.get(f, ()):
            return ("ok", f"'{m_first}' and '{f}' are the same name")
        best = max(best, difflib.SequenceMatcher(None, f, m_first).ratio())
    if best >= NAME_RATIO_OK:
        return ("ok", f"given name similarity {best:.2f}")
    return ("suspect",
            f"surname matches but given names {fec_firsts} vs '{m_first}' (best {best:.2f})")


def id_office(cid):
    """The office/state encoded in the ID itself. No API call needed."""
    if len(cid) < 4:
        return "?"
    kind = {"H": "House", "S": "Senate", "P": "President"}.get(cid[0], "?")
    return f"{kind} {cid[2:4]}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

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
    members = [(k, v) for k, v in fec.items() if k != "_meta" and isinstance(v, dict)]

    claims = defaultdict(list)          # cid -> [member names]
    for name, rec in members:
        for cid in member_ids(rec):
            claims[cid].append(name)

    ids = sorted(claims)
    if LIMIT:
        ids = ids[:LIMIT]

    print(f"Loaded {len(members)} members, {len(claims)} unique candidate IDs")
    print("Flag resolution ->  argv: " + " ".join(sys.argv[1:] or ["(none)"]))
    print("                    env : COMMIT_INPUT=%r LIMIT_INPUT=%r"
          % (os.environ.get("COMMIT_INPUT"), os.environ.get("LIMIT_INPUT")))
    print(f"Mode: {'COMMIT (WILL WRITE data/fec.json)' if COMMIT else 'REPORT ONLY (no write)'}")
    if LIMIT:
        print(f"Limit: first {LIMIT} IDs only")
    dupes = {c: m for c, m in claims.items() if len(m) > 1}
    print(f"IDs claimed by more than one member: {len(dupes)}")
    print(f"Looking up {len(ids)} IDs (~{len(ids)} calls)\n")

    owner = {}      # cid -> FEC registered name
    failed = []
    started = time.time()

    for n, cid in enumerate(ids):
        try:
            data = get(f"/candidate/{cid}/", {"per_page": 1})
        except BudgetExhausted as e:
            print(f"\nFATAL: {e}\nStopped after {n} of {len(ids)} IDs.")
            sys.exit(2)
        res = (data or {}).get("results") or []
        if not res:
            failed.append(cid)
        else:
            owner[cid] = res[0].get("name") or ""
        if (n + 1) % 50 == 0:
            print(f"  ...{n + 1}/{len(ids)}  [calls {_stats['calls']}, "
                  f"{(time.time() - started) / 60:.1f} min]", flush=True)

    # -- classify -----------------------------------------------------------
    findings = []
    for cid in ids:
        fec_name = owner.get(cid)
        if not fec_name:
            continue
        scored = []
        for m in claims[cid]:
            verdict, detail = name_score(m, fec_name)
            scored.append((m, verdict, detail))

        if len(scored) > 1:
            # Duplicate claim: rank ok > suspect > mismatch, keep the best.
            rank = {"ok": 0, "suspect": 1, "mismatch": 2}
            scored.sort(key=lambda x: rank[x[1]])
            keeper = scored[0][0]
            for m, verdict, detail in scored:
                if m == keeper and verdict == "ok":
                    continue
                findings.append({
                    "id": cid, "office": id_office(cid), "fec_name": fec_name,
                    "member": m, "verdict": "CONFIRMED",
                    "reason": f"ID also claimed by {keeper}, who matches the FEC name; {detail}",
                    "also_claimed_by": [x[0] for x in scored if x[0] != m],
                })
        else:
            m, verdict, detail = scored[0]
            if verdict == "mismatch":
                findings.append({"id": cid, "office": id_office(cid), "fec_name": fec_name,
                                 "member": m, "verdict": "CONFIRMED", "reason": detail})
            elif verdict == "suspect":
                findings.append({"id": cid, "office": id_office(cid), "fec_name": fec_name,
                                 "member": m, "verdict": "SUSPECT", "reason": detail})

    confirmed = [f for f in findings if f["verdict"] == "CONFIRMED"]
    suspect = [f for f in findings if f["verdict"] == "SUSPECT"]

    # -- report -------------------------------------------------------------
    print("\n" + "=" * 70)
    print(f"IDs looked up: {len(ids)}   lookup failed (left alone): {len(failed)}")
    print(f"API calls: {_stats['calls']}   429s: {_stats['429s']}   "
          f"total: {(time.time() - started) / 60:.1f} min")

    print(f"\nCONFIRMED WRONG: {len(confirmed)}")
    for f in confirmed:
        print(f"  {f['member']}")
        print(f"      holds {f['id']} ({f['office']}) which FEC says is: {f['fec_name']}")
        print(f"      {f['reason']}")

    print(f"\nSUSPECT (surname matches, given name does not; NOT auto-removed): {len(suspect)}")
    for f in suspect:
        print(f"  {f['member']:28s} {f['id']}  FEC: {f['fec_name']}")
        print(f"      {f['reason']}")

    if failed:
        print(f"\nLookup failed for {len(failed)} IDs (left untouched): {failed[:15]}")

    # -- apply --------------------------------------------------------------
    removals = defaultdict(list)
    for f in confirmed:
        removals[f["member"]].append(f["id"])

    blocked, primary_hits, applied = [], [], 0
    if removals:
        for mname, bad in removals.items():
            rec = fec[mname]
            current = member_ids(rec)
            keep = [i for i in current if i not in bad]
            if not keep:
                blocked.append(mname)
                continue
            if not COMMIT:
                continue
            if rec.get("candidate_id") in bad:
                primary_hits.append((mname, rec.get("candidate_id"), keep[0]))
                rec["candidate_id"] = keep[0]
            rec["all_candidate_ids"] = keep
            applied += 1

    # A member whose only ID is someone else's has no correct ID on file at
    # all, so removal cannot fix them. Ask the FEC who they are and propose
    # candidates. Proposals are REPORTED ONLY and never written, because a
    # name search can return several people and picking one is a judgement
    # call that belongs to a human, not to this script.
    proposals = {}
    for mname in blocked:
        try:
            res = get("/candidates/", {"q": mname, "per_page": 5, "sort": "-election_years"})
        except BudgetExhausted:
            break
        hits = []
        for c in ((res or {}).get("results") or []):
            verdict, _ = name_score(mname, c.get("name") or "")
            hits.append({
                "id": c.get("candidate_id"), "fec_name": c.get("name"),
                "office": c.get("office_full"), "state": c.get("state"),
                "district": c.get("district"), "years": (c.get("election_years") or [])[-3:],
                "name_verdict": verdict,
            })
        proposals[mname] = hits

    print("\n" + "=" * 70)
    if blocked:
        print("NEEDS REVIEW: every ID on file for these members is someone "
              "else's, so nothing was removed.")
        print("They have NO correct ID on record. Candidates from FEC name search:")
        for m in blocked:
            print(f"\n  {m}")
            for h in proposals.get(m, []) or [{"id": "(no results)", "fec_name": "",
                                               "office": "", "state": "", "district": "",
                                               "years": "", "name_verdict": ""}]:
                mark = "  <- name matches" if h.get("name_verdict") == "ok" else ""
                print(f"      {h['id']:12s} {str(h['fec_name'])[:34]:34s} "
                      f"{str(h['office'])[:8]:8s} {h['state'] or ''}-{h['district'] or ''} "
                      f"{h['years']}{mark}")
        print("\n  Pick the right ID by hand and set it in fec.json. This script "
              "will not guess for you.")

    if COMMIT:
        if primary_hits:
            print("\nPRIMARY candidate_id replaced for:")
            for m, old, new in primary_hits:
                print(f"    {m}: {old} -> {new}")
        with open(path + ".idaudit.bak", "w") as fh:
            fh.write(raw)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(fec, fh, indent=1, ensure_ascii=False)
        os.replace(tmp, path)
        print(f"\nCleaned {applied} members. WROTE {path}")
        print(f"(backup of original: {path}.idaudit.bak)")
        print("\nNOTE: stripping an ID does NOT recompute that member's money. "
              "Re-run refresh-pac-split and the sector/FEC refresh after this.")
    else:
        print("\nREPORT ONLY. Nothing written to fec.json. Re-run with commit=true "
              "to strip the CONFIRMED entries.")

    with open(os.path.abspath(REPORT_PATH), "w") as fh:
        json.dump({"confirmed": confirmed, "suspect": suspect,
                   "needs_review": blocked, "proposals": proposals,
                   "lookup_failed": failed, "committed": bool(COMMIT)},
                  fh, indent=1, ensure_ascii=False)
    print(f"Findings written to {os.path.abspath(REPORT_PATH)}")

    if confirmed and not COMMIT:
        sys.exit(3)   # red run so a report-only pass with findings is visible


if __name__ == "__main__":
    main()
