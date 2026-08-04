#!/usr/bin/env python3
"""
build_lookup.py
===============
Writes data/lookup.json: the small file behind the state lookup on the landing
page.

WHY A SEPARATE FILE
-------------------
The lookup needs figures from fec.json, scores.json and outside_spending.json,
which come to roughly 1.5MB together. That is fine inside app.html, which
already loads them, but the landing page's whole job is to load fast for
someone who has never been here before. This carries only the fields the
verdict rules actually use, for about a tenth of the size.

It also means the sentences on the landing page and anything the app shows come
from ONE derived file. Two views computing the same claim from different
sources is how the outside-spending chart and the member profile ended up
disagreeing for 252 members.

ROUNDING
--------
Percentages are rounded HALF UP, matching JavaScript's Math.round. Python's
default is banker's rounding, so round(10.5) gives 10 while the browser gives
11; that mismatch put 27 members' small-donor share off by a point.

USAGE
-----
  python3 scripts/build_lookup.py
"""

import json
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
OUT = DATA / "lookup.json"
SECTORS = ["aipac", "fossil_fuels", "pharma", "tech", "defense", "finance"]


def r0(x):
    """Round half up, so the file and the browser agree."""
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def load(name, default=None):
    p = DATA / name
    if not p.exists():
        if default is None:
            sys.exit(f"ERROR: {p} is missing")
        print(f"  WARNING: {name} missing, continuing without it")
        return default
    return json.loads(p.read_text())


def main():
    fec = load("fec.json")
    scores = load("scores.json", {})
    outside = load("outside_spending.json", {})
    bioguide = load("bioguide.json", {})
    senate = load("senate.json")
    house = load("house.json")
    senate_names = {m["name"] for m in senate}

    rows, skipped = [], []
    for m in senate + house:
        n = m["name"]
        rec = fec.get(n) or {}
        raised = rec.get("total_raised")
        # A member with no receipts on record cannot support any of the
        # sentences, so they are left out rather than shown with blanks.
        if not isinstance(raised, (int, float)) or raised <= 0:
            skipped.append(n)
            continue
        sc = scores.get(n)
        pct = sc.get("pct") if isinstance(sc, dict) else sc
        os_ = outside.get(n) or {}
        sup = os_.get("total_supporting") or 0
        opp = os_.get("total_opposing") or 0
        small = rec.get("small_dollar_pct")

        rows.append({
            "n": n,
            "p": m.get("party"),
            "st": m.get("state"),
            "d": m.get("district"),
            "c": "S" if n in senate_names else "H",
            "bid": bioguide.get(n),
            "risk": r0(pct) if isinstance(pct, (int, float)) else None,
            "pac": rec.get("pac_pct"),
            "raised": r0(raised),
            "si": r0(rec.get("special_interest_total") or 0),
            "sec": {k: r0(rec.get(k) or 0) for k in SECTORS},
            "ie": r0(sup + opp),
            "ieS": r0(sup),
            "ieO": r0(opp),
            "grass": r0(small) if isinstance(small, (int, float)) else None,
        })

    OUT.write_text(json.dumps(rows, separators=(",", ":"), ensure_ascii=False))
    size = OUT.stat().st_size
    states = len({r["st"] for r in rows})
    print(f"WROTE {OUT}")
    print(f"  members: {len(rows)}   states and territories: {states}")
    print(f"  size: {size/1024:.0f} KB")
    print(f"  skipped, no receipts on record: {len(skipped)}"
          + (f" ({', '.join(skipped[:6])}{'...' if len(skipped) > 6 else ''})" if skipped else ""))
    missing_pac = sum(1 for r in rows if not isinstance(r["pac"], int))
    missing_ie = sum(1 for r in rows if not r["ie"])
    print(f"  without a PAC share: {missing_pac}   without outside spending: {missing_ie}")


if __name__ == "__main__":
    main()
