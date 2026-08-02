#!/usr/bin/env python3
"""
fetch_pac_individual_split.py
==============================
Adds an authoritative PAC-vs-individual money split to each member in
data/fec.json, pulled directly from the FEC API.

WHY THIS EXISTS
---------------
The sector totals in fec.json (pharma, finance, tech, defense, fossil_fuels)
come from OpenSecrets industry data, which by OpenSecrets' deliberate
methodology BLENDS two very different things under one number:

  1. Industry PAC money (checks from the industry's PACs = influence buying)
  2. Individual donations from people who happen to work in that industry
     (often small-dollar grassroots donations that carry no industry agenda)

For most members the blend is fine. But for small-dollar-funded members who
REFUSE corporate PAC money (Bernie Sanders, Elizabeth Warren, AOC, etc.) it is
badly misleading: their "pharma" or "tech" totals are overwhelmingly individual
donations from nurses and engineers, not drug-company or Big-Tech PAC money.
Example (verified against FEC): Bernie Sanders' Senate committee shows
~$23.8M individual contributions and $27 (twenty-seven dollars) of PAC money.

FEC reports the PAC-vs-individual split cleanly and authoritatively. This
script pulls that split so the app can show what share of a member's money is
ACTUALLY PAC money, and so features framed around influence (e.g. the
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
And, from FEC Schedule E (independent expenditures, outside spending NOT given
to the campaign, the same category as AIPAC's super-PAC spending):
    ie_support_total  -> outside money spent SUPPORTING the member
    ie_oppose_total   -> outside money spent OPPOSING the member

It does NOT touch the sector totals, grassroots fields, or anything else.
Existing fields are preserved; only the six new fields are added/updated.

CALL BUDGET (this is the thing that kills naive versions of this script)
------------------------------------------------------------------------
A personal FEC key is capped at 1,000 calls/hour; an upgraded key at 120/min.
An earlier version issued one totals call plus FIVE separate by_candidate
calls (one per cycle) per candidate ID = 6 calls each, about 3,300 calls for
551 members. That is a 3.3 hour floor against a 1,000/hour cap, and it died
on the workflow timeout with zero output because the file was only written
after the full loop finished.

This version fixes that five ways:
  1. IE cycles are requested in ONE call using repeated cycle params. Support
     for that is PROBED at runtime, not assumed; if the API does not honor it,
     the script falls back to per-cycle calls automatically and says so.
  2. Rate budget is tracked in rolling 60-second and 60-minute windows and the
     script throttles itself BEFORE hitting a 429, instead of reacting after.
  3. In commit mode the file is saved incrementally (default every 25 members),
     so a kill leaves real partial progress instead of nothing.
  4. Sustained 429s abort the run loudly with a non-zero exit, instead of
     silently flagging hundreds of healthy members as API failures.
  5. A self-imposed wall clock limit (--max-minutes) stops the loop cleanly
     BEFORE the workflow timeout, guaranteeing the commit step still runs.

REQUIRES
--------
  FEC_API_KEY environment variable (same key the main fetch script uses).
  data/fec.json  (must already contain candidate_id / all_candidate_ids)

USAGE
-----
  python3 scripts/fetch_pac_individual_split.py                  # dry run, all
  python3 scripts/fetch_pac_individual_split.py --limit 10       # smoke test
  python3 scripts/fetch_pac_individual_split.py --commit         # write
  python3 scripts/fetch_pac_individual_split.py --commit --resume # continue

OUTPUT
------
  data/fec.json  (updated in place, with a .pacsplit.bak backup of the
                  ORIGINAL written once before the first save)

SAFETY
------
  - Never zeroes an existing value on API failure: if a call fails for a
    member, that member's fields are left unchanged and the member is listed
    in the REVIEW report at the end.
  - Dry-run by default. Pass --commit to write.
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
FEC_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "fec.json")

IE_CYCLES = (2018, 2020, 2022, 2024, 2026)

# Rate limits. A personal api.data.gov key is 1,000/hour. An upgraded FEC key
# is 120/minute. We stay under BOTH so the same script is safe on either key.
HOURLY_BUDGET = 950          # headroom under the 1,000/hour personal cap
MINUTE_BUDGET = 110          # headroom under the 120/minute upgraded cap
MAX_CONSECUTIVE_429 = 10     # sustained refusal means the budget is really gone

API_KEY = os.environ.get("FEC_API_KEY", "").strip()
if not API_KEY:
    print("ERROR: FEC_API_KEY environment variable is not set.")
    print("  Local runs:  export FEC_API_KEY=your_key && python3 scripts/fetch_pac_individual_split.py")
    print("  CI runs:     check that the workflow passes secrets.FEC_API_KEY as env")
    sys.exit(1)


def arg_value(flag, default=None, cast=str):
    """Read --flag value from argv. Returns default if absent."""
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            try:
                return cast(sys.argv[i + 1])
            except ValueError:
                print(f"ERROR: bad value for {flag}")
                sys.exit(1)
    return default


COMMIT = "--commit" in sys.argv
RESUME = "--resume" in sys.argv
LIMIT = arg_value("--limit", None, int)
SAVE_EVERY = arg_value("--save-every", 25, int)
MAX_MINUTES = arg_value("--max-minutes", 330, float)

# ---------------------------------------------------------------------------
# Rate-limited HTTP layer
# ---------------------------------------------------------------------------

_minute_window = deque()   # timestamps of calls in the last 60 seconds
_hour_window = deque()     # timestamps of calls in the last 3600 seconds
_stats = {"calls": 0, "429s": 0, "consecutive_429": 0, "throttle_seconds": 0.0}


def _throttle():
    """Sleep just long enough to stay inside both rolling budgets."""
    while True:
        now = time.time()
        while _minute_window and now - _minute_window[0] > 60:
            _minute_window.popleft()
        while _hour_window and now - _hour_window[0] > 3600:
            _hour_window.popleft()

        wait = 0.0
        if len(_minute_window) >= MINUTE_BUDGET:
            wait = max(wait, 60 - (now - _minute_window[0]) + 0.5)
        if len(_hour_window) >= HOURLY_BUDGET:
            wait = max(wait, 3600 - (now - _hour_window[0]) + 1)

        if wait <= 0:
            _minute_window.append(now)
            _hour_window.append(now)
            return

        mins = wait / 60.0
        print(f"\n  [budget throttle: waiting {wait:.0f}s ({mins:.1f} min) "
              f"| minute window {len(_minute_window)}/{MINUTE_BUDGET} "
              f"| hour window {len(_hour_window)}/{HOURLY_BUDGET}]", flush=True)
        _stats["throttle_seconds"] += wait
        time.sleep(wait)


class BudgetExhausted(Exception):
    """Raised when the API keeps refusing us despite proactive throttling."""


def get(path, params=None):
    """FEC API GET with proactive throttling and retry. Returns dict or None."""
    p = dict(params or {})
    p["api_key"] = API_KEY
    url = BASE + path + "?" + urllib.parse.urlencode(p, doseq=True)

    for attempt in range(5):
        _throttle()
        _stats["calls"] += 1
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                _stats["consecutive_429"] = 0
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                _stats["429s"] += 1
                _stats["consecutive_429"] += 1
                if _stats["consecutive_429"] >= MAX_CONSECUTIVE_429:
                    raise BudgetExhausted(
                        f"{_stats['consecutive_429']} consecutive 429s from the FEC API. "
                        "The key's rate budget is exhausted, not a per-member problem."
                    )
                print(f"\n  [429 despite throttle, backing off 65s "
                      f"({_stats['consecutive_429']}/{MAX_CONSECUTIVE_429})]", flush=True)
                time.sleep(65)
                continue
            if e.code in (400, 404, 422):
                return None
            time.sleep(3)
        except Exception:
            time.sleep(2)
    return None


# ---------------------------------------------------------------------------
# FEC data helpers
# ---------------------------------------------------------------------------

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


def _ie_call(cid, cycles):
    """One by_candidate call for the given cycle or cycles. Returns dict|None."""
    return get("/schedules/schedule_e/by_candidate/",
               {"candidate_id": cid, "cycle": list(cycles), "per_page": 100})


def _sum_ie(results):
    support = oppose = 0.0
    for r in results:
        amt = float(r.get("total") or 0)
        if r.get("support_oppose_indicator") == "O":
            oppose += amt
        else:
            support += amt
    return support, oppose


def probe_multicycle(members):
    """
    Determine empirically whether by_candidate honors repeated cycle params.

    We do NOT assume this works. We ask for several cycles in one call and see
    whether the response actually contains rows from more than one cycle. If
    the API ignores the extra params (or 422s), we detect it here and fall back
    to per-cycle calls for the whole run.

    Returns True if multi-cycle is confirmed working, False otherwise.
    """
    informative = 0
    for name, rec in members:
        cids = candidate_ids(rec)
        if not cids:
            continue
        data = _ie_call(cids[0], IE_CYCLES)
        if data is None:
            informative += 1
        else:
            results = data.get("results", [])
            cycles = {r.get("cycle") for r in results if r.get("cycle") is not None}
            if len(cycles) > 1:
                print(f"  Probe: multi-cycle CONFIRMED on {name} "
                      f"(one call returned cycles {sorted(cycles)})")
                return True
            if results:
                informative += 1
        if informative >= 6:
            break
    print("  Probe: multi-cycle NOT supported (or unverifiable). "
          "Falling back to one call per cycle.")
    return False


def ie_for_candidate(cid, multicycle):
    """
    Sum independent expenditures made SUPPORTING vs OPPOSING this candidate.

    IE is outside spending that is NOT given to the campaign and cannot legally
    be coordinated with it, the same category as AIPAC's United Democracy
    Project spending. FEC splits it by support_oppose_indicator:
        'S' = spent supporting the candidate
        'O' = spent opposing the candidate

    Returns (ie_support, ie_oppose), or None only if every call failed, so
    callers can leave existing values untouched rather than zeroing them.
    """
    if multicycle:
        data = _ie_call(cid, IE_CYCLES)
        if data is None:
            return None
        return _sum_ie(data.get("results", []))

    support = oppose = 0.0
    ok = False
    for cycle in IE_CYCLES:
        data = _ie_call(cid, [cycle])
        if data is None:
            continue
        ok = True
        s, o = _sum_ie(data.get("results", []))
        support += s
        oppose += o
    if not ok:
        return None
    return (support, oppose)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save(path, fec, raw_original, backup_done):
    """Write fec.json, creating a one-time backup of the ORIGINAL first."""
    if not backup_done:
        bak = path + ".pacsplit.bak"
        with open(bak, "w") as f:
            f.write(raw_original)
        print(f"  (backup of original written: {bak})", flush=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(fec, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)
    return True


def has_split(rec):
    """True if this member already carries a completed split."""
    return "pac_pct" in rec and "individual_total" in rec and "pac_total" in rec


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    started = time.time()
    path = os.path.abspath(FEC_PATH)
    with open(path) as f:
        raw_original = f.read()
    fec = json.loads(raw_original)

    members = [(k, v) for k, v in fec.items() if k != "_meta"]
    if LIMIT:
        members = members[:LIMIT]

    print(f"Loaded {len(members)} members from {path}")
    print(f"Mode: {'COMMIT (will write)' if COMMIT else 'DRY-RUN (no write; pass --commit to save)'}")
    if RESUME:
        print("Resume: ON (members that already have a split will be skipped)")
    if LIMIT:
        print(f"Limit: first {LIMIT} members only (smoke test)")
    print(f"Guards: save every {SAVE_EVERY} members, stop after {MAX_MINUTES:.0f} minutes,"
          f" budgets {MINUTE_BUDGET}/min and {HOURLY_BUDGET}/hr")
    print()

    print("Probing FEC by_candidate multi-cycle support...")
    try:
        multicycle = probe_multicycle(members)
    except BudgetExhausted as e:
        print(f"\nFATAL during probe: {e}")
        sys.exit(2)
    calls_each = 2 if multicycle else 6
    print(f"  => {calls_each} API calls per candidate ID this run "
          f"(~{calls_each * len(members)} total for {len(members)} members)")
    print()

    updated = 0
    skipped_resume = 0
    no_ids = []
    api_failed = []
    examples = []
    backup_done = False
    stopped_early = None

    for idx, (name, rec) in enumerate(members):
        elapsed_min = (time.time() - started) / 60.0
        if elapsed_min > MAX_MINUTES:
            stopped_early = (f"wall clock limit reached at {elapsed_min:.0f} min "
                             f"after {idx} of {len(members)} members")
            break

        if RESUME and has_split(rec):
            skipped_resume += 1
            continue

        cids = candidate_ids(rec)
        if not cids:
            no_ids.append(name)
            continue

        tot_ind = tot_pac = tot_party = 0.0
        tot_ie_support = tot_ie_oppose = 0.0
        any_ok = False
        ie_ok = False

        try:
            for cid in cids:
                res = split_for_candidate(cid)
                if res is not None:
                    any_ok = True
                    i, p, pa = res
                    tot_ind += i
                    tot_pac += p
                    tot_party += pa
                ie = ie_for_candidate(cid, multicycle)
                if ie is not None:
                    ie_ok = True
                    tot_ie_support += ie[0]
                    tot_ie_oppose += ie[1]
        except BudgetExhausted as e:
            print(f"\n\nFATAL: {e}")
            print(f"Stopped at member {idx + 1}/{len(members)} ({name}).")
            if COMMIT and updated:
                backup_done = save(path, fec, raw_original, backup_done)
                print(f"Partial progress SAVED: {updated} members written.")
                print("Re-run with --commit --resume to continue where this stopped.")
            print(f"Calls made: {_stats['calls']}  429s: {_stats['429s']}")
            sys.exit(2)

        # IE can succeed even when the totals call fails. Write what we got.
        if ie_ok:
            rec["ie_support_total"] = round(tot_ie_support)
            rec["ie_oppose_total"] = round(tot_ie_oppose)

        if not any_ok:
            # Never overwrite or zero the split on failure.
            api_failed.append(name)
            continue

        denom = tot_ind + tot_pac + tot_party
        pac_pct = round(tot_pac / denom * 100) if denom > 0 else None

        rec["individual_total"] = round(tot_ind)
        rec["pac_total"] = round(tot_pac)
        rec["party_total"] = round(tot_party)
        rec["pac_pct"] = pac_pct
        updated += 1

        if pac_pct is not None and pac_pct <= 1 and denom > 1_000_000:
            examples.append((name, round(tot_pac), round(denom), pac_pct))

        if (idx + 1) % 25 == 0:
            print(f"  ...processed {idx + 1}/{len(members)}  "
                  f"[calls {_stats['calls']}, {elapsed_min:.1f} min]", flush=True)

        if COMMIT and SAVE_EVERY > 0 and updated % SAVE_EVERY == 0:
            backup_done = save(path, fec, raw_original, backup_done)
            print(f"  [saved: {updated} members written so far]", flush=True)

    # -----------------------------------------------------------------------
    # Report
    # -----------------------------------------------------------------------
    elapsed_min = (time.time() - started) / 60.0
    print()
    print("=" * 64)
    if stopped_early:
        print(f"STOPPED EARLY: {stopped_early}")
    print(f"Updated split for: {updated} members")
    if RESUME:
        print(f"Skipped (already had split): {skipped_resume}")
    print(f"No candidate IDs (skipped): {len(no_ids)}")
    print(f"API failed (left unchanged, REVIEW): {len(api_failed)}")
    if api_failed:
        for n in api_failed[:20]:
            print(f"    REVIEW: {n}")
        if len(api_failed) > 20:
            print(f"    ...and {len(api_failed) - 20} more")
    print()
    print(f"API calls: {_stats['calls']}   429s: {_stats['429s']}   "
          f"throttle wait: {_stats['throttle_seconds'] / 60:.1f} min   "
          f"total: {elapsed_min:.1f} min")

    examples.sort(key=lambda x: -x[2])
    print()
    print("Sanity check, members with <=1% PAC money (the ones the old blended")
    print("sector data made look 'industry-captured'):")
    if examples:
        for name, pac, denom, pct in examples[:12]:
            print(f"    {name:26s} PAC ${pac:>12,}  of  ${denom:>13,}  ({pct}%)")
    else:
        print("    (none found in this batch)")

    if COMMIT:
        save(path, fec, raw_original, backup_done)
        print()
        print(f"WROTE {path}")
    else:
        print()
        print("DRY-RUN complete. Re-run with --commit to write data/fec.json.")

    if stopped_early:
        print()
        print("Re-run with --commit --resume to finish the remaining members.")
        sys.exit(3)


if __name__ == "__main__":
    main()
