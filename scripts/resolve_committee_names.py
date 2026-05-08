#!/usr/bin/env python3
"""
resolve_committee_names.py
==========================

Patches data/outside_spending.json by replacing "Unknown" committee_name
values with real names from FEC's /committees/ endpoint.

Why this is separate from refetch v3: the raw schedule_e records don't
reliably populate committee_name, but every record has committee_id, so
we can do a one-time lookup keyed by committee_id and patch in place.

USAGE:
  export FEC_API_KEY="..."
  python3 scripts/resolve_committee_names.py

OUTPUT:
  data/outside_spending.json (updated in place)
  data/outside_spending.json.before_names (backup)
"""

import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
import urllib.request
import urllib.error

API_BASE = "https://api.open.fec.gov/v1"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUT_FILE = DATA_DIR / "outside_spending.json"
BACKUP_FILE = DATA_DIR / "outside_spending.json.before_names"

SLEEP = 0.4
MAX_RETRIES = 5
RATE_LIMIT_WAIT = 30


def get_api_key():
    key = os.environ.get("FEC_API_KEY")
    if not key:
        sys.exit("ERROR: FEC_API_KEY not set")
    return key


def fec_get(path, params, api_key):
    params = {**params, "api_key": api_key}
    url = f"{API_BASE}{path}?{urlencode(params)}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "InfluenceRegistry/1.0"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = RATE_LIMIT_WAIT * attempt
                print(f"  rate-limited, sleep {wait}s", flush=True)
                time.sleep(wait)
                continue
            if e.code == 404:
                return None
            if 500 <= e.code < 600:
                time.sleep(2 * attempt)
                continue
            print(f"  HTTP {e.code}: {url[:120]}", flush=True)
            return None
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"  net error attempt {attempt}: {e}", flush=True)
            time.sleep(2 * attempt)
    return None


def lookup_committee_name(cid, api_key, cache):
    """Look up a committee's display name. Caches results."""
    if cid in cache:
        return cache[cid]

    data = fec_get(f"/committee/{cid}/", {}, api_key)
    if not data or not data.get("results"):
        cache[cid] = None
        return None

    result = data["results"][0]
    name = result.get("name") or result.get("committee_name")
    cache[cid] = name
    return name


def main():
    api_key = get_api_key()

    if not OUT_FILE.exists():
        sys.exit(f"ERROR: {OUT_FILE} not found")

    data = json.loads(OUT_FILE.read_text())
    BACKUP_FILE.write_text(json.dumps(data, indent=2))
    print(f"Backed up to {BACKUP_FILE.name}", flush=True)

    # Collect every unique committee_id across all members
    all_cids = set()
    for member, rec in data.items():
        for entry in rec.get("top_supporters", []) + rec.get("top_opposers", []):
            cid = entry.get("committee_id")
            if cid:
                all_cids.add(cid)

    print(f"Found {len(all_cids)} unique committees to look up", flush=True)
    print(f"Estimated time: {len(all_cids) * SLEEP / 60:.1f} minutes", flush=True)

    # Look up each committee once
    cache = {}
    for i, cid in enumerate(sorted(all_cids), 1):
        name = lookup_committee_name(cid, api_key, cache)
        if i % 50 == 0:
            print(f"  [{i}/{len(all_cids)}] resolved", flush=True)
        time.sleep(SLEEP)

    print(f"Resolved {sum(1 for v in cache.values() if v)} of {len(cache)}", flush=True)

    # Patch in place
    patched = 0
    for member, rec in data.items():
        for entry in rec.get("top_supporters", []) + rec.get("top_opposers", []):
            cid = entry.get("committee_id")
            if cid and cache.get(cid):
                entry["committee_name"] = cache[cid]
                patched += 1

    # Atomic write
    tmp = OUT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(OUT_FILE)

    print(f"\nDone. Patched {patched} committee_name fields.", flush=True)
    unresolved = [cid for cid, v in cache.items() if not v]
    if unresolved:
        print(f"Could not resolve {len(unresolved)} committees:", flush=True)
        for cid in unresolved[:10]:
            print(f"  {cid}")


if __name__ == "__main__":
    main()
