#!/usr/bin/env python3
"""
audit_grassroots.py — Verify every grassroots-tagged member against FEC records.

For each member in tags.json -> grassroots:
  1. Resolve their authorized campaign committees (from data/fec.json
     all_candidate_ids, then /candidate/{id}/committees/).
  2. Pull ALL non-individual Schedule A receipts (is_individual=false),
     cycles 2000-present, with FULL keyset pagination.
  3. Classify each contributing committee via /committee/{id}/ using the
     FEC's own organization_type field:
        C / V / W  -> CORPORATE PAC
        T          -> TRADE ASSOCIATION PAC (corporate-interest)
        L          -> labor union PAC (not corporate)
        M          -> membership org PAC (not corporate per se)
     Party committees, candidate committees, and joint fundraisers are
     ignored. Leadership PACs are reported separately for context.
  4. Report any member with corporate or trade PAC receipts > $0.

Output: prints a report and writes audit_grassroots_report.md.
Exit code 1 if any violations found (so the Action run shows red).

Requires: FEC_API_KEY env var. Run via GitHub Actions (see
.github/workflows/audit-grassroots.yml).
"""

import json, os, sys, time
import urllib.request, urllib.parse

API = "https://api.open.fec.gov/v1"
KEY = os.environ.get("FEC_API_KEY", "")
if not KEY:
    sys.exit("FEC_API_KEY env var not set")

CYCLES = None  # computed in main from current year back to 2000


def get(path, params):
    params = dict(params)
    params["api_key"] = KEY
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(6):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 30 * (attempt + 1)
                print(f"    [rate limited, sleeping {wait}s]")
                time.sleep(wait)
                continue
            if e.code >= 500:
                time.sleep(5)
                continue
            return None
        except Exception:
            time.sleep(5)
    return None


_cmte_cache = {}

def classify_committee(cmte_id):
    """Return (category, name) for a contributing committee."""
    if cmte_id in _cmte_cache:
        return _cmte_cache[cmte_id]
    data = get(f"/committee/{cmte_id}/", {})
    cat, name = "unknown", cmte_id
    if data and data.get("results"):
        c = data["results"][0]
        name = c.get("name") or cmte_id
        org = (c.get("organization_type") or "").upper()
        ctype = (c.get("committee_type") or "").upper()
        desig = (c.get("designation") or "").upper()
        if ctype in ("X", "Y", "Z"):          # party committees
            cat = "party"
        elif ctype in ("H", "S", "P"):        # candidate committees / transfers
            cat = "candidate"
        elif desig == "J":                    # joint fundraising
            cat = "joint"
        elif org in ("C", "V", "W"):
            cat = "CORPORATE"
        elif org == "T":
            cat = "TRADE"
        elif org == "L":
            cat = "labor"
        elif org == "M":
            cat = "membership"
        elif desig == "D":                    # leadership PAC
            cat = "leadership"
        else:
            cat = "other_pac"
    _cmte_cache[cmte_id] = (cat, name)
    time.sleep(0.05)
    return cat, name


def committees_for(candidate_ids):
    out = []
    for cid in candidate_ids:
        data = get(f"/candidate/{cid}/committees/", {"per_page": 50})
        if data and data.get("results"):
            for r in data["results"]:
                if (r.get("committee_type") or "").upper() in ("H", "S", "P"):
                    cmte = r.get("committee_id")
                    if cmte and cmte not in out:
                        out.append(cmte)
        time.sleep(0.1)
    return out


def pac_receipts(committee_ids, member_name):
    """All non-individual receipts, fully paginated."""
    rows = []
    seen = set()
    for cmte in committee_ids:
        for cycle in CYCLES:
            last_indexes = None
            while True:
                params = {
                    "committee_id": cmte,
                    "two_year_transaction_period": cycle,
                    "is_individual": "false",
                    "per_page": 100,
                    "sort": "contribution_receipt_date",
                }
                if last_indexes:
                    params.update(last_indexes)   # ALL keyset values
                data = get("/schedules/schedule_a/", params)
                if not data or not data.get("results"):
                    break
                for item in data["results"]:
                    sub = item.get("sub_id")
                    if sub and sub in seen:
                        continue
                    if sub:
                        seen.add(sub)
                    amt = item.get("contribution_receipt_amount") or 0
                    src = (item.get("contributor_committee_id") or "").strip()
                    if amt <= 0 or not src:
                        continue
                    rows.append((src, amt))
                pag = (data.get("pagination") or {}).get("last_indexes") or {}
                if not pag.get("last_index"):
                    break
                last_indexes = dict(pag)
                time.sleep(0.1)
            time.sleep(0.1)
    return rows


def main():
    global CYCLES
    import datetime
    y = datetime.date.today().year
    cur = y if y % 2 == 0 else y + 1
    CYCLES = list(range(cur, 1998, -2))

    tags = json.load(open("data/tags.json"))
    fec = json.load(open("data/fec.json"))
    grassroots = tags.get("grassroots", [])
    print(f"Auditing {len(grassroots)} grassroots members, cycles {CYCLES[-1]}-{CYCLES[0]}\n")

    violations, clean, skipped = [], [], []
    for i, name in enumerate(grassroots, 1):
        entry = fec.get(name) or {}
        cids = entry.get("all_candidate_ids") or ([entry["candidate_id"]] if entry.get("candidate_id") else [])
        if not cids:
            print(f"[{i}/{len(grassroots)}] {name}: SKIP (no candidate IDs in fec.json)")
            skipped.append(name)
            continue
        cmtes = committees_for(cids)
        if not cmtes:
            print(f"[{i}/{len(grassroots)}] {name}: SKIP (no committees)")
            skipped.append(name)
            continue
        rows = pac_receipts(cmtes, name)
        corp, trade, detail = 0, 0, {}
        for src, amt in rows:
            cat, cname = classify_committee(src)
            if cat in ("CORPORATE", "TRADE"):
                if cat == "CORPORATE":
                    corp += amt
                else:
                    trade += amt
                key = f"{cname} [{cat}]"
                detail[key] = detail.get(key, 0) + amt
        if corp or trade:
            violations.append((name, corp, trade, detail))
            print(f"[{i}/{len(grassroots)}] {name}: VIOLATION  corporate ${corp:,.0f}  trade ${trade:,.0f}")
            for k, v in sorted(detail.items(), key=lambda x: -x[1])[:8]:
                print(f"      {k}: ${v:,.0f}")
        else:
            clean.append(name)
            print(f"[{i}/{len(grassroots)}] {name}: clean")

    lines = ["# Grassroots Audit Report", ""]
    lines.append(f"Audited: {len(grassroots)} | Clean: {len(clean)} | Violations: {len(violations)} | Skipped: {len(skipped)}")
    lines.append("")
    if violations:
        lines.append("## Violations (corporate/trade PAC receipts on FEC record)")
        for name, corp, trade, detail in sorted(violations, key=lambda x: -(x[1]+x[2])):
            lines.append(f"\n### {name} — corporate ${corp:,.0f}, trade ${trade:,.0f}")
            for k, v in sorted(detail.items(), key=lambda x: -x[1]):
                lines.append(f"- {k}: ${v:,.0f}")
    if skipped:
        lines.append("\n## Skipped (no FEC mapping — verify manually)")
        for n in skipped:
            lines.append(f"- {n}")
    lines.append("\n## Clean")
    for n in clean:
        lines.append(f"- {n}")
    open("audit_grassroots_report.md", "w").write("\n".join(lines))
    print(f"\nReport written to audit_grassroots_report.md")
    print(f"RESULT: {len(violations)} violations, {len(clean)} clean, {len(skipped)} skipped")
    sys.exit(1 if violations else 0)


if __name__ == "__main__":
    main()
