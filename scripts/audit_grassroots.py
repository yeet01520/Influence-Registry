#!/usr/bin/env python3
"""
audit_grassroots.py — Flag grassroots-tagged members who took PAC money.

Uses ONLY endpoints proven to work in the FEC refresh pipeline:
  /candidate/{id}/committees/   — resolve authorized committees
  /committee/{id}/totals/       — per-cycle financials

The /committee/{id}/totals/ rows include other_political_committee_contributions
(all PAC money received that cycle) and political_party_committee_contributions.
A genuine "grassroots / no corporate PAC" member should have a near-zero PAC
total. We sum other_political_committee_contributions across recent cycles and
flag anyone above a threshold for manual review.

This is a FIRST-PASS screen, not a corporate-vs-labor classifier: it flags ALL
PAC money (corporate, trade, labor, leadership). That's appropriate for a
no-corporate-PAC pledge — union-backed progressives will show PAC money too, so
the report separates "all PAC" totals and a human makes the final call on each.
No per-receipt pagination, no fragile aggregate endpoints. ~2-4 calls/member.

Output: audit_grassroots_report.md. Exit 1 if any member exceeds the threshold.
"""

import json, os, sys, time
import urllib.request, urllib.parse

API = "https://api.open.fec.gov/v1"
KEY = os.environ.get("FEC_API_KEY", "")
if not KEY:
    sys.exit("FEC_API_KEY env var not set")

# Flag members whose summed PAC receipts (recent cycles) exceed this.
PAC_FLAG_THRESHOLD = 5000
RECENT_CYCLES = None  # set in main


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
                print(f"    [rate limited, sleeping {wait}s]", flush=True)
                time.sleep(wait)
                continue
            if e.code >= 500:
                time.sleep(5)
                continue
            return None
        except Exception:
            time.sleep(5)
    return None


def committees_for(candidate_ids):
    out = []
    for cid in candidate_ids:
        data = get(f"/candidate/{cid}/committees/", {"per_page": 50})
        if data and data.get("results"):
            for r in data["results"]:
                if (r.get("committee_type") or "").upper() in ("H", "S", "P"):
                    c = r.get("committee_id")
                    if c and c not in out:
                        out.append(c)
        time.sleep(0.1)
    return out


def pac_money(committee_ids):
    """Sum PAC + party committee receipts across recent cycles."""
    pac_total = 0.0
    party_total = 0.0
    raised_total = 0.0
    for cmte in committee_ids:
        data = get(f"/committee/{cmte}/totals/", {"per_page": 100})
        if data and data.get("results"):
            for row in data["results"]:
                if row.get("cycle") not in RECENT_CYCLES:
                    continue
                pac_total += row.get("other_political_committee_contributions") or 0
                party_total += row.get("political_party_committee_contributions") or 0
                raised_total += row.get("receipts") or 0
        time.sleep(0.1)
    return round(pac_total), round(party_total), round(raised_total)


def main():
    global RECENT_CYCLES
    import datetime
    y = datetime.date.today().year
    cur = y if y % 2 == 0 else y + 1
    RECENT_CYCLES = [cur, cur - 2, cur - 4]   # last three cycles (~6 yrs)

    tags = json.load(open("data/tags.json"))
    fec = json.load(open("data/fec.json"))
    grassroots = tags.get("grassroots", [])
    print(f"Auditing {len(grassroots)} grassroots members, cycles "
          f"{min(RECENT_CYCLES)}-{max(RECENT_CYCLES)}\n", flush=True)

    flagged, clean, skipped = [], [], []
    for i, name in enumerate(grassroots, 1):
        entry = fec.get(name) or {}
        cids = entry.get("all_candidate_ids") or (
            [entry["candidate_id"]] if entry.get("candidate_id") else [])
        if not cids:
            print(f"[{i}/{len(grassroots)}] {name}: SKIP (no candidate IDs)", flush=True)
            skipped.append(name)
            continue
        cmtes = committees_for(cids)
        if not cmtes:
            print(f"[{i}/{len(grassroots)}] {name}: SKIP (no committees)", flush=True)
            skipped.append(name)
            continue
        pac, party, raised = pac_money(cmtes)
        if pac > PAC_FLAG_THRESHOLD:
            pct = (pac / raised * 100) if raised else 0
            flagged.append((name, pac, party, raised, pct))
            print(f"[{i}/{len(grassroots)}] {name}: FLAG  PAC ${pac:,}  "
                  f"({pct:.1f}% of ${raised:,} raised)  party ${party:,}", flush=True)
        else:
            clean.append((name, pac, raised))
            print(f"[{i}/{len(grassroots)}] {name}: clean  (PAC ${pac:,})", flush=True)

    lines = ["# Grassroots Audit Report", ""]
    lines.append(f"Recent cycles checked: {sorted(RECENT_CYCLES)}")
    lines.append(f"Flag threshold: PAC receipts > ${PAC_FLAG_THRESHOLD:,}")
    lines.append("")
    lines.append(f"Audited: {len(grassroots)} | Flagged: {len(flagged)} | "
                 f"Clean: {len(clean)} | Skipped: {len(skipped)}")
    lines.append("")
    lines.append("NOTE: 'PAC' here is ALL committee money (corporate, trade, labor, "
                 "leadership). Union-backed members will appear flagged; a human must "
                 "decide whether the source breaks a no-corporate-PAC pledge. Use the "
                 "member's FEC page to see which PACs.")
    if flagged:
        lines.append("\n## Flagged for review (PAC money on FEC record)")
        for name, pac, party, raised, pct in sorted(flagged, key=lambda x: -x[1]):
            lines.append(f"- **{name}** — PAC ${pac:,} ({pct:.1f}% of ${raised:,} raised), "
                         f"party committee ${party:,}")
    if skipped:
        lines.append("\n## Skipped (no FEC mapping — verify manually)")
        for n in skipped:
            lines.append(f"- {n}")
    lines.append("\n## Clean (PAC receipts at or below threshold)")
    for name, pac, raised in sorted(clean):
        lines.append(f"- {name} (PAC ${pac:,})")
    open("audit_grassroots_report.md", "w").write("\n".join(lines))
    print(f"\nReport written to audit_grassroots_report.md", flush=True)
    print(f"RESULT: {len(flagged)} flagged, {len(clean)} clean, {len(skipped)} skipped",
          flush=True)
    sys.exit(1 if flagged else 0)


if __name__ == "__main__":
    main()
