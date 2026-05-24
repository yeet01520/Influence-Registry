#!/usr/bin/env python3
"""
compute_scores.py

Compute the Special Interest Money Score for every official in The Influence Registry,
matching the live-site logic exactly. Output: data/scores.json.

This script is the single source of truth for scores. Both the live site (index.html)
and the static page generator (generate_static_pages.py) read this file. When you want
to change scoring logic, change it here, regenerate scores.json, and both surfaces
update automatically.

Mirrors two pieces of the JS in index.html:
  1. The FEC merger (loadFecData + applyFECData) that mutates profile.donations
  2. The calcScore() function

Output schema:
  {
    "_meta": { "generated_at": "...", "schema_version": 1 },
    "Marco Rubio":  { "pct": 94, "lbl": "High Risk", "col": "#b91c1c",
                      "super_pac_total": 0, "any_corporate": true,
                      "reasons": ["AIPAC","Oil/Gas",...] },
    ...
  }

Usage:
    python3 scripts/compute_scores.py
    python3 scripts/compute_scores.py --data-dir data
    python3 scripts/compute_scores.py --verify "Donald Trump=94,Marco Rubio=94,Alexandria Ocasio-Cortez=8"
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


# ─── Helpers (mirrors helpers in index.html) ────────────────────────────────────

def parse_dollar(v):
    """Mirror parseDollar() in index.html: '$1.3M' -> 1300000, '$500K' -> 500000."""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return 0
    # Strip dollar signs, commas, spaces
    s_clean = re.sub(r"[\$,\s]", "", s)
    # Match number + optional suffix
    m = re.match(r"^([\d.]+)\s*([KMB]?)", s_clean, re.IGNORECASE)
    if not m:
        return 0
    try:
        n = float(m.group(1))
    except ValueError:
        return 0
    u = (m.group(2) or "").upper()
    if u == "B":
        return n * 1_000_000_000
    if u == "M":
        return n * 1_000_000
    if u == "K":
        return n * 1_000
    return n


def no_data(v) -> bool:
    """Mirror noData() in index.html."""
    s = str(v or "").strip().lower()
    return (not v) or s in ("", "none on record", "none", "n/a", "minimal", "0", "$0")


def safe_dict(d):
    return d if isinstance(d, dict) else {}


# ─── FEC merger (Phase A) ───────────────────────────────────────────────────────
# Mirrors the FEC-authoritative merger logic shipped earlier this session.
# Specifically the preferFEC + preferFECTopLevel + corporate_total + total_raised
# blocks in loadFecData() in index.html.

def merge_fec_into_profile(name: str, profile: dict, fec_record: dict, grassroots_names: set) -> dict:
    """
    Return a new donations dict that reflects FEC-authoritative merging.
    Does NOT mutate the input profile.
    """
    d = dict(safe_dict(profile.get("donations")))
    if not fec_record:
        return d

    is_grassroots = name in grassroots_names

    # total_raised: FEC value is authoritative
    if fec_record.get("total_raised") and float(fec_record["total_raised"]) > 0:
        d["total_raised"] = "$" + format(int(fec_record["total_raised"]), ",")

    # Grassroots members: skip ALL sector data updates (same guard as JS)
    if is_grassroots:
        return d

    # AIPAC and Oil/Gas: prefer FEC, but preserve "$0 corporate" pledges
    def prefer_fec_top_level(field: str, fec_val):
        if not fec_val:
            return
        existing = str(d.get(field) or "").strip().lower()
        if existing == "$0" or "$0 corporate" in existing:
            return
        d[field] = "$" + format(int(fec_val), ",")

    prefer_fec_top_level("aipac",   fec_record.get("aipac"))
    prefer_fec_top_level("oil_gas", fec_record.get("fossil_fuels"))

    # Per-category amounts: prefer FEC
    def prefer_fec(field: str, fec_val):
        if not fec_val:
            return
        existing = str(d.get(field) or "").strip().lower()
        if existing == "$0" or "$0 corporate" in existing:
            return
        d[field] = "$" + format(int(fec_val), ",")

    prefer_fec("pharma",      fec_record.get("pharma"))
    prefer_fec("defense",     fec_record.get("defense"))
    prefer_fec("wall_street", fec_record.get("finance"))
    prefer_fec("tech",        fec_record.get("tech"))

    # corporate_total: FEC special_interest_total is authoritative, except for "$0 corporate" pledges
    sit = fec_record.get("special_interest_total")
    if sit:
        existing_ct = str(d.get("corporate_total") or "")
        is_pledge = "$0 corporate" in existing_ct.lower()
        if not is_pledge:
            d["corporate_total"] = "$" + format(int(sit), ",")

    # Fallback: if no special_interest_total, compute from sum of per-category amounts
    if no_data(d.get("corporate_total")):
        local_total = (
            (fec_record.get("fossil_fuels") or 0)
            + (fec_record.get("pharma") or 0)
            + (fec_record.get("finance") or 0)
            + (fec_record.get("defense") or 0)
            + (fec_record.get("tech") or 0)
        )
        if local_total > 0:
            d["corporate_total"] = "$" + format(int(local_total), ",")

    return d


# ─── Score calculation (Phase B) ────────────────────────────────────────────────
# Mirrors calcScore() in index.html, including all branches:
#   1. Grassroots-tagged members → use profile.corruption_score as-is (capped)
#   2. SCOTUS/Cabinet → use profile.corruption_score as-is
#   3. Otherwise → compute from corporate_total + per-sector amounts

CLEAN_TYPES = [
    "grassroots", "small-dollar", "small donor", "small donors",
    "fundraising platform", "actblue", "winred", "bundled",
    "labor union", "civil rights", "women's pac",
    "progressive pac", "his own pac", "her own pac", "presidential campaign",
]
SUPERPAC_TYPES = [
    "super pac", "superpac", "dark money", "527", "501(c)(4)",
    "corporate pac", "industry pac", "trade association pac", "leadership pac",
]
CORPORATE_PAC_TYPES = SUPERPAC_TYPES  # JS uses these together
CORP_KEYWORDS = [
    "corp", "corporation", "inc.", "llc", "industry", "industries",
    "oil", "gas", "pharma", "bank", "financial", "investment", "hedge",
    "private equity", "defense", "aerospace", "energy", "mining",
    "tobacco", "casino", "real estate", "media conglomerate", "tech company", "telecom",
]


def is_clean_type(t: str) -> bool:
    tl = (t or "").lower()
    return any(c in tl for c in CLEAN_TYPES)


def is_super_pac(td: dict) -> bool:
    t = (td.get("type") or "").lower()
    if is_clean_type(t):
        return False
    return any(p in t for p in SUPERPAC_TYPES)


def is_corporate_pac(td: dict) -> bool:
    t = (td.get("type") or "").lower()
    if is_clean_type(t):
        return False
    return any(p in t for p in CORPORATE_PAC_TYPES)


def is_corporate_donor(td: dict) -> bool:
    t = (td.get("type") or "").lower()
    if is_clean_type(t):
        return False
    return any(k in t for k in CORP_KEYWORDS)


def sector_points(amt: float) -> int:
    """Mirror sectorPoints() in index.html: small/medium/large brackets."""
    if amt > 2_000_000:
        return 9
    if amt > 500_000:
        return 6
    if amt > 0:
        return 3
    return 0


def calc_score(name: str, profile: dict, merged_donations: dict,
               grassroots_names: set, aipac_lookup: dict, sector_lookups: dict) -> dict:
    """
    Compute the score for one person. Mirrors calcScore() in index.html.

    Returns: { pct, lbl, vto, col, super_pac_total, any_corporate, reasons }
    """
    # Branch 1: Grassroots members — donation-based baseline
    if name in grassroots_names:
        base = profile.get("corruption_score") or 12
        col = "#16a34a" if base < 20 else "#65a30d" if base < 35 else "#ca8a04"
        lbl = "Clean" if base < 20 else "Low Risk" if base < 35 else "Moderate"
        return {
            "pct": base, "lbl": lbl, "vto": False, "col": col,
            "super_pac_total": 0, "any_corporate": False, "reasons": [],
        }

    # Branch 2: SCOTUS justices and Cabinet members — manual score, use directly
    if profile.get("scotus_data") or profile.get("cabinet_industry"):
        s = profile.get("corruption_score") or 30
        col = "#b91c1c" if s >= 70 else "#ea580c" if s >= 40 else "#16a34a"
        lbl = "High Risk" if s >= 70 else "Moderate" if s >= 40 else "Low Risk"
        return {
            "pct": s, "lbl": lbl, "vto": False, "col": col,
            "super_pac_total": 0, "any_corporate": False, "reasons": [],
        }

    # Branch 3: Everyone else (Congress members with PAC data)
    d = merged_donations or {}
    aipac_data    = aipac_lookup.get(name, 0)
    fossil_data   = sector_lookups.get("fossil",  {}).get(name, 0)
    pharma_dt     = sector_lookups.get("pharma",  {}).get(name, 0)
    defense_dt    = sector_lookups.get("defense", {}).get(name, 0)
    finance_dt    = sector_lookups.get("finance", {}).get(name, 0)
    tech_dt       = sector_lookups.get("tech",    {}).get(name, 0)
    nra_dt        = sector_lookups.get("nra",     {}).get(name, 0)

    corp_total = parse_dollar(d.get("corporate_total"))
    # SCORING uses ONLY manually-verified PROFILES_DATA donation values.
    # Per the JS comment, FEC sector data counts employer-based individual contributions,
    # not just corporate PACs — so we use the profile values for scoring.
    # (But note: the FEC merger Phase A above DID overwrite profile values with FEC
    #  amounts for non-grassroots. This matches the live site behavior: applyFECData
    #  mutates profile.donations, then calcScore reads those mutated values.)
    pharma  = parse_dollar(d.get("pharma"))
    wall_st = parse_dollar(d.get("wall_street"))
    defense = parse_dollar(d.get("defense"))
    tech    = parse_dollar(d.get("tech"))
    nra     = parse_dollar(d.get("nra")) or 0
    oil_gas = parse_dollar(d.get("oil_gas"))
    # AIPAC is verified PAC data — use max of profile value and aipac.json lookup
    aipac_amt = max(parse_dollar(d.get("aipac")), aipac_data)

    top_donors = d.get("top_donors") or []

    # Super PAC total: explicitly-labeled Super PACs + AIPAC
    super_pac_total = 0
    for td in top_donors:
        if is_super_pac(td):
            super_pac_total += parse_dollar(td.get("amount"))
    if aipac_amt > 0:
        super_pac_total += aipac_amt

    # Corporate money detection
    explicitly_clean = "$0" in str(d.get("corporate_total") or "").lower()
    corp_fields_positive = not explicitly_clean and (pharma > 0 or wall_st > 0 or defense > 0 or tech > 0)
    corp_total_positive = corp_total > 0 and not explicitly_clean
    has_corporate_donor = any(is_corporate_pac(td) or is_corporate_donor(td) for td in top_donors)
    oil_gas_triggers = oil_gas > 0 and not explicitly_clean
    any_corporate = corp_fields_positive or corp_total_positive or has_corporate_donor or (aipac_amt > 0) or oil_gas_triggers

    # ─── Log-weighted formula (v2) ────────────────────────────────────
    # Score scales with total tracked corporate-sector money on a log curve.
    # Members who explicitly reject corporate PAC money ("explicitly_clean")
    # have their score halved and capped at 30 to honor the pledge.
    #
    # Formula:   raw = 33 * log10(1 + total / 100_000), capped at 100
    # Bands:     0-19 Clean | 20-39 Low Risk | 40-59 Some Corporate
    #            60-79 Moderate Risk | 80-100 High Risk
    import math

    # Build reasons list (which sectors triggered) for display
    reasons = []
    if aipac_amt > 0:                       reasons.append("AIPAC")
    if oil_gas > 0 and not explicitly_clean: reasons.append("Oil/Gas")
    if pharma > 0 and not explicitly_clean:  reasons.append("Pharma")
    if wall_st > 0 and not explicitly_clean: reasons.append("Finance")
    if defense > 0 and not explicitly_clean: reasons.append("Defense")
    if tech > 0 and not explicitly_clean:    reasons.append("Tech")
    if nra > 0 and not explicitly_clean:     reasons.append("NRA")

    # Sum all tracked sectors. AIPAC always counts (pledge applies to corporate
    # PACs only). Other sectors count only when not explicitly_clean, otherwise
    # they represent employer-aligned individual contributions, not direct PAC money.
    total_money = aipac_amt
    if not explicitly_clean:
        total_money += oil_gas + pharma + wall_st + defense + tech + nra

    if total_money <= 0:
        # Truly no tracked money — minimum score
        return {
            "pct": 0, "lbl": "Clean", "vto": False, "col": "#16a34a",
            "super_pac_total": 0, "any_corporate": False, "reasons": [],
            "score_basis": "No tracked special interest money",
        }

    raw = 33 * math.log10(1 + total_money / 100_000)
    raw = min(raw, 100)
    if explicitly_clean:
        score = min(30, int(round(raw / 2)))
    else:
        score = int(round(raw))

    # Band labels & colors
    if score >= 80:   lbl, col = "High Risk",      "#b91c1c"
    elif score >= 60: lbl, col = "Moderate Risk",  "#ea580c"
    elif score >= 40: lbl, col = "Some Corporate", "#d97706"
    elif score >= 20: lbl, col = "Low Risk",       "#ca8a04"
    else:             lbl, col = "Clean",          "#16a34a"

    return {
        "pct": score, "lbl": lbl, "vto": False, "col": col,
        "super_pac_total": int(super_pac_total),
        "any_corporate": any_corporate, "reasons": reasons,
        "score_basis": (
            "Tracked corporate sectors: AIPAC, oil/gas, pharma, finance, defense, tech, NRA. "
            "Does not include labor, single-issue, or non-corporate PACs."
        ),
    }


# ─── Main pipeline ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Compute Special Interest Money Scores.")
    parser.add_argument("--data-dir", default="data", help="Path to data directory")
    parser.add_argument("--output", default="data/scores.json", help="Where to write scores")
    parser.add_argument("--verify", help='Expected scores: "Name=PCT,Name2=PCT2,..." for testing')
    parser.add_argument("--dry-run", action="store_true", help="Don't write the output file")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    profiles = json.load(open(data_dir / "profiles.json"))
    fec      = json.load(open(data_dir / "fec.json"))
    tags     = json.load(open(data_dir / "tags.json"))
    sectors  = json.load(open(data_dir / "sectors.json"))
    aipac_lookup = json.load(open(data_dir / "aipac.json"))

    grassroots_names = set(tags.get("grassroots") or [])
    # Sector lookups: sectors.json has top-level keys (fossil, pharma, etc.)
    sector_lookups = {
        "fossil":  sectors.get("fossil",  {}) or {},
        "pharma":  sectors.get("pharma",  {}) or {},
        "defense": sectors.get("defense", {}) or {},
        "finance": sectors.get("finance", {}) or {},
        "tech":    sectors.get("tech",    {}) or {},
        "nra":     sectors.get("nra",     {}) or {},
    }

    print(f"Loaded {len(profiles)} profiles, {len(fec)} FEC records, "
          f"{len(grassroots_names)} grassroots names, {len(aipac_lookup)} AIPAC entries",
          file=sys.stderr)

    # Compute scores for everyone in profiles.json
    scores = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": 1,
            "description": "Pre-computed Special Interest Money Scores. Source of truth for index.html and the static page generator.",
        }
    }
    n_branch_grassroots = n_branch_scotus_cabinet = n_branch_corp = n_branch_clean = 0
    for name, profile in profiles.items():
        if not isinstance(profile, dict):
            continue
        fec_record = fec.get(name) or {}
        merged_donations = merge_fec_into_profile(name, profile, fec_record, grassroots_names)
        result = calc_score(name, profile, merged_donations,
                            grassroots_names, aipac_lookup, sector_lookups)
        scores[name] = result
        # Stats
        if name in grassroots_names:
            n_branch_grassroots += 1
        elif profile.get("scotus_data") or profile.get("cabinet_industry"):
            n_branch_scotus_cabinet += 1
        elif result.get("any_corporate"):
            n_branch_corp += 1
        else:
            n_branch_clean += 1

    total = n_branch_grassroots + n_branch_scotus_cabinet + n_branch_corp + n_branch_clean
    print(f"  Computed {total} scores: "
          f"{n_branch_grassroots} grassroots, {n_branch_scotus_cabinet} SCOTUS/cabinet, "
          f"{n_branch_corp} corporate, {n_branch_clean} clean/low",
          file=sys.stderr)

    # Verify expected scores match (for testing the port)
    if args.verify:
        print("\n=== Verifying expected scores ===", file=sys.stderr)
        any_fail = False
        for pair in args.verify.split(","):
            if "=" not in pair:
                continue
            name, expected = pair.rsplit("=", 1)
            name = name.strip()
            expected = int(expected.strip())
            got = scores.get(name, {}).get("pct")
            ok = (got == expected)
            symbol = "PASS" if ok else "FAIL"
            print(f"  [{symbol}] {name}: expected {expected}, got {got}", file=sys.stderr)
            if not ok:
                any_fail = True
        if any_fail:
            print("\nVerification FAILED. Score computation does not match live-site behavior.", file=sys.stderr)
            return 2

    if args.dry_run:
        print(f"[dry-run] Would write {args.output}", file=sys.stderr)
        return 0

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(scores, f, indent=2)
    print(f"Wrote {output_path} ({len(scores)-1} scored people)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
