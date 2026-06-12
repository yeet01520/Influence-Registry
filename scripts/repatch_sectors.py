#!/usr/bin/env python3
"""
repatch_sectors.py — one-off, no FEC API required.

Recomputes the five OpenSecrets-derived sector fields (fossil_fuels, pharma,
defense, finance, tech) and special_interest_total for every member already in
data/fec.json, using the FIXED name matcher in fetch_fec_data.py (state
abbreviation canonicalization + accent transliteration + nickname aliases).

Why this exists: the FEC refresh workflow resumes from a checkpoint and skips
members already present, so a matcher fix in fetch_fec_data.py does not reach
members whose records were carried over with $0 sectors. Sector data comes from
local OpenSecrets CSVs (not the FEC API), so it can be repatched offline in
seconds without re-running the full fetch.

What it preserves: every other field in each record (aipac, aipac_pacs,
aipac_ie, aipac_lobby_donors, candidate_id, committees, total_raised, source,
grassroots, etc.). Only the 5 sector fields and special_interest_total change.

Usage:
    python3 repatch_sectors.py \
        --fec data/fec.json \
        --members data/members.json \
        --csv-dir data/raw \
        --out data/fec.json
"""
import argparse, importlib.util, json, os, sys
from pathlib import Path

# The sector matching is purely CSV-based and makes no FEC API calls, but
# fetch_fec_data.py sys.exit()s at import time if FEC_API_KEY is unset. Provide a
# placeholder so the module imports cleanly; it is never used for any request.
os.environ.setdefault("FEC_API_KEY", "not-used-csv-only")


def load_fetch_module(script_path: Path):
    """Import fetch_fec_data.py for its matcher + CSV loaders WITHOUT running
    main() (which requires FEC_API_KEY). We exec the module but tolerate the
    SystemExit/key check by importing only after stubbing argv."""
    spec = importlib.util.spec_from_file_location("fetch_fec_data", script_path)
    mod = importlib.util.module_from_spec(spec)
    old_argv = sys.argv
    sys.argv = ["fetch_fec_data.py"]  # avoid arg parsing side effects
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        # main() guards on FEC_API_KEY and may sys.exit at import if it runs;
        # the functions/constants we need are already defined by then.
        pass
    finally:
        sys.argv = old_argv
    return mod


SECTOR_GETTERS = [
    ("fossil_fuels", "get_oil_amount",     "load_oil_data",
        ("Money_from_Oil___Gas_to_US_Representatives__1990-2024.csv",
         "Money_from_Oil___Gas_to_US_Senators__1990-2024.csv")),
    ("pharma",       "get_pharma_amount",  "load_pharma_data",
        ("Money_from_Health_to_US_Representatives__1990-2024.csv",
         "Money_from_Health_to_US_Senators__1990-2024.csv")),
    ("defense",      "get_defense_amount", "load_defense_data",
        ("Money_from_Defense_to_US_Representatives__1990-2024.csv",
         "Money_from_Defense_to_US_Senators__1990-2024.csv")),
    ("finance",      "get_finance_amount", "load_finance_data",
        ("Money_from_Finance_Insurance_Real_Estate_to_US_Representatives__1990-2024.csv",
         "Money_from_Finance_Insurance_Real_Estate_to_US_Senators__1990-2024.csv")),
    ("tech",         "get_tech_amount",    "load_tech_data",
        ("Money_from_Communications_Electronics_to_US_Representatives__1990-2024.csv",
         "Money_from_Communications_Electronics_to_US_Senators__1990-2024.csv")),
]

SIT_KEYS = ["aipac", "fossil_fuels", "pharma", "defense", "finance", "tech", "nra"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fec", default="data/fec.json")
    ap.add_argument("--members", default="data/members.json")
    ap.add_argument("--csv-dir", default="data/raw")
    ap.add_argument("--script", default="fetch_fec_data.py")
    ap.add_argument("--out", default="data/fec.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    csv_dir = Path(args.csv_dir)
    mod = load_fetch_module(Path(args.script))

    # Load every sector CSV via the script's own loaders (populates its globals).
    for _field, _getter, loader_name, (rep_csv, sen_csv) in SECTOR_GETTERS:
        loader = getattr(mod, loader_name, None)
        if loader is None:
            print(f"  [warn] loader {loader_name} not found; skipping", file=sys.stderr)
            continue
        loader(str(csv_dir / rep_csv), str(csv_dir / sen_csv))

    members = {m["name"]: m for m in json.load(open(args.members))}
    fec = json.load(open(args.fec))

    changed = 0
    recovered = 0  # members who went from all-zero sectors to having data
    meta = fec.pop("_meta", None)

    for name, rec in fec.items():
        if not isinstance(rec, dict):
            continue
        mem = members.get(name)
        if not mem:
            continue  # can't look up without a state; leave record untouched
        state = mem.get("state", "")

        before_total = sum((rec.get(k) or 0) for k, *_ in SECTOR_GETTERS)
        for field, getter_name, *_ in SECTOR_GETTERS:
            getter = getattr(mod, getter_name)
            rec[field] = int(getter(name, state) or 0)
        after_total = sum((rec.get(field) or 0) for field, *_ in SECTOR_GETTERS)

        # Recompute special_interest_total exactly as fetch_fec_data.py does.
        rec["special_interest_total"] = sum(int(rec.get(k) or 0) for k in SIT_KEYS)

        if after_total != before_total:
            changed += 1
            if before_total == 0 and after_total > 0:
                recovered += 1

    if meta is not None:
        # Re-insert _meta at the end so file shape is preserved.
        fec["_meta"] = meta

    print(f"Records changed: {changed}")
    print(f"Members recovered (all-zero sectors → has data): {recovered}")

    if args.dry_run:
        print("[dry-run] not writing")
        return

    with open(args.out, "w") as f:
        json.dump(fec, f, ensure_ascii=False, indent=1)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
