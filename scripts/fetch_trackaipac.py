#!/usr/bin/env python3
"""
fetch_trackaipac.py
====================
Refreshes AIPAC figures in an existing fec_data_v7.json from a new
trackaipac_page.txt, without re-running the full FEC fetch.

Use this when TrackAIPAC updates their data and you want to pull in
fresh AIPAC figures without waiting hours for fetch_fec_data.py.

NORMAL WORKFLOW (first run):
  1. Place trackaipac_page.txt in same folder as this script
  2. Run fetch_fec_data.py  → produces fec_data_v7.json (already reads
     trackaipac_page.txt at startup for AIPAC figures)

THIS SCRIPT — use when:
  - TrackAIPAC updates their data and you want to refresh AIPAC figures only
  - You already have fec_data_v7.json and don't want to re-run the FEC script
  - You want to verify how many members matched

REQUIRES: trackaipac_page.txt + fec_data_v7.json in same folder
OUTPUT:   fec_data_v7.json (updated in place)
"""

import json, os, re


SEP = '\u2028'   # U+2028 LINE SEPARATOR — field delimiter in the txt file

SI_KEYS = ["aipac", "fossil_fuels", "pharma", "defense", "finance", "tech", "nra"]


def parse_dollar(s):
    s = (s or "").replace("$","").replace(",","").replace("+","").strip()
    try: return int(float(s))
    except: return 0


def normalize(name):
    name = name.lower()
    name = re.sub(r'\b(jr|sr|ii|iii|iv|mr|ms|dr|rep|sen)\.?\b', '', name)
    name = re.sub(r'[^a-z\s]', '', name)
    return re.sub(r'\s+', ' ', name).strip()


def title_name(raw):
    name = raw.strip().title()
    for old, new in [
        ("Mcbath","McBath"), ("Mccormick","McCormick"), ("Mccaul","McCaul"),
        ("Mcclintock","McClintock"), ("Mcmorris","McMorris"), ("Mcbride","McBride"),
        ("Delauro","DeLauro"), ("Desaulnier","DeSaulnier"), ("Desjarlais","DesJarlais"),
        ("Defazio","DeFazio"), ("Degette","DeGette"), ("Lahood","LaHood"),
        ("Lamalfa","LaMalfa"), ("Mchenry","McHenry"), ("Mckinley","McKinley"),
    ]:
        name = name.replace(old, new)
    return name


def parse_trackaipac(path):
    """
    Parse trackaipac_page.txt into a dict keyed by normalized name.
    Returns {normalized_name: {total, pacs, ie, lobby_donors, sources}}.
    """
    with open(path, encoding='utf-8') as f:
        text = f.read()

    fields  = text.split(SEP)
    results = {}
    i = 0

    while i < len(fields):
        f = fields[i].strip()
        if (i + 5 < len(fields)
                and re.match(r'^[A-Z][A-Z\s\.\-\']+$', f) and len(f) > 3
                and re.match(r'^[A-Z]{2}-\S+\s+\[[RDI]\]$', fields[i+1].strip())):

            total = pacs = ie = donors = 0
            sources = ""
            for j in range(i+2, min(i+10, len(fields))):
                fj = fields[j].strip()
                if 'Israel Lobby Total:' in fj:
                    m = re.search(r'\$([\d,]+)', fj)
                    if m: total = parse_dollar(m.group(1))
                elif fj.startswith('PACs:'):
                    m = re.search(r'\$([\d,]+)', fj)
                    if m: pacs = parse_dollar(m.group(1))
                elif fj.startswith('IE:'):
                    m = re.search(r'\$([\d,]+)', fj)
                    if m: ie = parse_dollar(m.group(1))
                elif 'Lobby Donors:' in fj:
                    m = re.search(r'\$([\d,]+)', fj)
                    if m: donors = parse_dollar(m.group(1))
                elif (re.match(r'^[A-Z][A-Z0-9,\s\-\(\)\']+$', fj)
                      and ',' in fj and len(fj) < 300
                      and not any(state in fj for state in [
                          'ALABAMA','ALASKA','ARIZONA','ARKANSAS','CALIFORNIA',
                          'COLORADO','CONNECTICUT','DELAWARE','FLORIDA','GEORGIA',
                          'HAWAII','IDAHO','ILLINOIS','INDIANA','IOWA','KANSAS',
                          'KENTUCKY','LOUISIANA','MAINE','MARYLAND','MASSACHUSETTS',
                          'MICHIGAN','MINNESOTA','MISSISSIPPI','MISSOURI','MONTANA',
                          'NEBRASKA','NEVADA','NEW HAMPSHIRE','NEW JERSEY','NEW MEXICO',
                          'NEW YORK','NORTH CAROLINA','NORTH DAKOTA','OHIO','OKLAHOMA',
                          'OREGON','PENNSYLVANIA','RHODE ISLAND','SOUTH CAROLINA',
                          'SOUTH DAKOTA','TENNESSEE','TEXAS','UTAH','VERMONT',
                          'VIRGINIA','WASHINGTON','WEST VIRGINIA','WISCONSIN','WYOMING',
                          'DOWNLOAD'])):
                    sources = fj

            if total > 0 or pacs > 0 or ie > 0:
                key = normalize(title_name(f))
                results[key] = {
                    "total": total, "pacs": pacs, "ie": ie,
                    "lobby_donors": donors, "sources": sources,
                }
            i += 7
            continue
        i += 1

    return results


def find_match(name, ta_data):
    """Match a fec_data member name to a TrackAIPAC entry."""
    key = normalize(name)

    # 1. Exact
    if key in ta_data:
        return ta_data[key]

    # 2. Last name (if unambiguous)
    last = key.split()[-1] if key.split() else ""
    hits = {k: v for k, v in ta_data.items() if k.split()[-1] == last}
    if len(hits) == 1:
        return next(iter(hits.values()))

    return None


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    page_path  = os.path.join(script_dir, "trackaipac_page.txt")
    fec_path   = os.path.join(script_dir, "fec_data_v7.json")

    if not os.path.exists(page_path):
        print(f"ERROR: trackaipac_page.txt not found in {script_dir}")
        return
    if not os.path.exists(fec_path):
        print(f"ERROR: fec_data_v7.json not found — run fetch_fec_data.py first")
        return

    print(f"Parsing {page_path}...")
    ta_data = parse_trackaipac(page_path)
    print(f"  Parsed {len(ta_data)} members with pro-Israel lobby data")

    with open(fec_path) as f:
        fec_data = json.load(f)
    print(f"Loaded {len(fec_data)} members from fec_data_v7.json")

    matched = zeroed = 0
    for name, d in fec_data.items():
        ta = find_match(name, ta_data)
        if ta:
            d["aipac"]              = ta["total"]
            d["aipac_pacs"]         = ta["pacs"]
            d["aipac_ie"]           = ta["ie"]
            d["aipac_lobby_donors"] = ta["lobby_donors"]
            d["aipac_sources"]      = ta["sources"]
            matched += 1
        else:
            d["aipac"]              = 0
            d["aipac_pacs"]         = 0
            d["aipac_ie"]           = 0
            d["aipac_lobby_donors"] = 0
            d["aipac_sources"]      = ""
            zeroed += 1
        d["special_interest_total"] = sum(d.get(k, 0) for k in SI_KEYS)

    with open(fec_path, "w") as f:
        json.dump(fec_data, f, indent=2)

    print(f"\n{'='*55}")
    print(f"  Matched   : {matched:3d} members updated with TrackAIPAC data")
    print(f"  Unmatched : {zeroed:3d} members set to $0 (not in TrackAIPAC)")
    print(f"\nSaved updated fec_data_v7.json")

    print("\nSPOT CHECK:")
    for name in ["Tommy Tuberville", "Mark Kelly", "Adam Schiff", "Lisa Murkowski"]:
        d = fec_data.get(name, {})
        if d.get("aipac", 0) > 0:
            print(f"  {name:<22} AIPAC ${d['aipac']:>10,}  "
                  f"(PACs ${d.get('aipac_pacs',0):>7,} + "
                  f"IE ${d.get('aipac_ie',0):>8,} + "
                  f"Donors ${d.get('aipac_lobby_donors',0):>10,})")
        else:
            print(f"  {name:<22} not in output")

    total_aipac = sum(d.get("aipac", 0) for d in fec_data.values())
    has_aipac   = sum(1 for d in fec_data.values() if d.get("aipac", 0) > 0)
    print(f"\nTotal pro-Israel lobby money tracked : ${total_aipac:,.0f}")
    print(f"Members with non-zero AIPAC figure   : {has_aipac}")


if __name__ == "__main__":
    main()
