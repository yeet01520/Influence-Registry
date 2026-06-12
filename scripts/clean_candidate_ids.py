#!/usr/bin/env python3
"""
clean_candidate_ids.py
======================
Removes stale contaminated candidate IDs from data/fec.json and re-fetches each
affected member's committees from ONLY their clean, state-matching IDs.

Background
----------
Earlier fetches (before the current guard code existed) merged same-surname
candidates from other states and presidential candidates into members'
`all_candidate_ids` via an unfiltered name search. Example: Mike Rogers (AL),
Hal Rogers (KY), and Mark Alford (MO) all ended up with S0GA00286 (a Georgia
Senate "Rogers") and P60003563 (a presidential "Rogers"). Those bad IDs then
pulled in committees that don't belong to the member, scrambling total_raised,
sector attribution, and committee lists for ~282 members.

The live resolve_all_candidate_ids() already rejects these (P-prefix reject +
ID-prefix state check), so no new IDs get contaminated. This script just cleans
the cached bad IDs and rebuilds committees from the clean ones.

What it does, per member:
  1. Drop any candidate ID that fails the same guards the live fetcher uses:
       - P-prefix (presidential) IDs
       - H/S IDs whose encoded state (chars 3-4) != the member's state
  2. Re-fetch committees from /candidate/{id}/committees/ for the SURVIVING
     clean IDs only. Because a clean ID only returns its own committees, the
     wrong committees fall away and true ownership is restored automatically.
  3. Leave total_raised, sectors, AIPAC, etc. untouched (run the totals/sector
     refreshers separately afterward if those need recomputing from clean data).

Only members whose IDs actually change are touched and re-fetched, so the API
cost is ~1 call per affected member (~282), well within rate limits.

USAGE:
  export FEC_API_KEY=your_key
  python3 clean_candidate_ids.py            # writes data/fec.json
  python3 clean_candidate_ids.py --dry-run  # report only, no writes/API calls
"""
import argparse, json, os, sys, time, urllib.parse, urllib.request, urllib.error
from pathlib import Path

BASE = "https://api.open.fec.gov/v1"
SLEEP = 0.6
MAX_RETRIES = 5


def is_contaminated(cid, member_state):
    """Mirror the live resolve_all_candidate_ids guards: a candidate ID is
    contaminated if it's a presidential (P) ID, or an H/S ID whose encoded
    state does not match the member's declared state."""
    if not cid:
        return True
    if cid.startswith("P"):
        return True
    if len(cid) >= 4 and cid[0] in ("H", "S"):
        id_state = cid[2:4]
        if member_state and id_state and id_state != member_state:
            return True
    return False


def get(path, params=None):
    p = dict(params or {})
    p["api_key"] = os.environ["FEC_API_KEY"]
    url = BASE + path + "?" + urllib.parse.urlencode(p)
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "InfluenceRegistry/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(30 * (attempt + 1))
            elif attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
            else:
                return None
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
            else:
                return None
    return None


def fetch_committees(clean_ids):
    """Authorized campaign committees for the clean IDs only (P=Principal,
    A=Authorized; types H/S/P). Mirrors get_all_committees() in the fetcher."""
    out, seen = [], set()
    for cid in clean_ids:
        data = get(f"/candidate/{cid}/committees/", {"per_page": 50})
        time.sleep(SLEEP)
        if not data or not data.get("results"):
            continue
        for r in data["results"]:
            cmte = r.get("committee_id")
            if (cmte and cmte not in seen
                    and r.get("designation", "") in ("P", "A")
                    and r.get("committee_type", "") in ("H", "S", "P")):
                out.append(cmte)
                seen.add(cmte)
    return out


def _load_resolver():
    """Import the live resolve_all_candidate_ids from fetch_fec_data.py (same
    directory) to re-resolve members whose entire ID list was contaminated.
    Returns the function, or None if it can't be imported."""
    import importlib.util
    script = Path(__file__).resolve().parent / "fetch_fec_data.py"
    if not script.exists():
        return None
    os.environ.setdefault("FEC_API_KEY", os.environ.get("FEC_API_KEY", "x"))
    try:
        spec = importlib.util.spec_from_file_location("fetch_fec_data", script)
        mod = importlib.util.module_from_spec(spec)
        old_argv = sys.argv
        sys.argv = ["fetch_fec_data.py"]
        try:
            spec.loader.exec_module(mod)
        except SystemExit:
            pass
        finally:
            sys.argv = old_argv
        return getattr(mod, "resolve_all_candidate_ids", None)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fec", default="data/fec.json")
    ap.add_argument("--members", default="data/members.json")
    ap.add_argument("--out", default="data/fec.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.dry_run and not os.environ.get("FEC_API_KEY"):
        sys.exit("ERROR: set FEC_API_KEY (or use --dry-run)")

    fec = json.load(open(args.fec))
    members = {m["name"]: m for m in json.load(open(args.members))}

    resolver = None if args.dry_run else _load_resolver()

    meta = fec.pop("_meta", None)
    id_cleaned = 0      # members whose ID list shrank
    cmte_changed = 0    # members whose committee list changed
    reresolved = 0      # members whose IDs were re-fetched from scratch
    review = []         # members still unresolved (manual review)

    names = sorted(n for n in fec if isinstance(fec[n], dict))
    for i, name in enumerate(names):
        rec = fec[name]
        state = members.get(name, {}).get("state", "")
        all_ids = rec.get("all_candidate_ids") or []
        if not all_ids:
            continue

        clean = [c for c in all_ids if not is_contaminated(c, state)]
        bad = [c for c in all_ids if is_contaminated(c, state)]
        if not bad:
            continue  # nothing to clean

        id_cleaned += 1

        if not clean:
            # Entire ID list was contaminated (even the primary). The member's
            # real ID is missing, so re-resolve it from scratch using the live
            # resolver, which applies the same guards and finds the correct
            # state-matching ID. Appointed members with no federal race (e.g.
            # Alan Armstrong) correctly resolve to nothing and go to review.
            print(f"[{i+1:3d}/{len(names)}] {name:<32} ALL ids contaminated "
                  f"{all_ids} -> re-resolving...")
            if args.dry_run or resolver is None:
                review.append((name, all_ids))
                continue
            office = members.get(name, {}).get("office", "H")
            try:
                fresh = resolver(name, state, office)  # [(cid, office), ...]
            except Exception:
                fresh = []
            fresh_ids = [c for c, _ in fresh if not is_contaminated(c, state)]
            if not fresh_ids:
                review.append((name, all_ids))
                continue
            rec["all_candidate_ids"] = fresh_ids
            rec["candidate_id"] = fresh_ids[0]
            new_cmtes = fetch_committees(fresh_ids)
            if new_cmtes:
                rec["committees"] = new_cmtes
                cmte_changed += 1
            reresolved += 1
            print(f"      re-resolved to {fresh_ids}")
            continue

        print(f"[{i+1:3d}/{len(names)}] {name:<32} ids {len(all_ids)}->{len(clean)} "
              f"(dropped {bad})")

        if args.dry_run:
            continue

        rec["all_candidate_ids"] = clean
        if rec.get("candidate_id") in bad:
            rec["candidate_id"] = clean[0]

        new_cmtes = fetch_committees(clean)
        if new_cmtes and set(new_cmtes) != set(rec.get("committees") or []):
            rec["committees"] = new_cmtes
            cmte_changed += 1

    if meta is not None:
        fec["_meta"] = meta

    print(f"\nMembers with contaminated IDs cleaned: {id_cleaned}")
    print(f"Members re-resolved from scratch:      {reresolved}")
    print(f"Members whose committee list changed:  {cmte_changed}")
    if review:
        print(f"\n[review] {len(review)} members still unresolved (likely appointed "
              f"with no federal race — verify manually):")
        for n, ids in review[:20]:
            print(f"   {n}: had {ids}")

    if args.dry_run:
        print("\n[dry-run] no writes, no API calls")
        return

    with open(args.out, "w") as f:
        json.dump(fec, f, ensure_ascii=False, indent=1)
    print(f"\nWrote {args.out}")
    print("Next: re-run refresh_total_raised.py and repatch_sectors.py so totals "
          "and sectors reflect the cleaned committee lists.")


if __name__ == "__main__":
    main()
