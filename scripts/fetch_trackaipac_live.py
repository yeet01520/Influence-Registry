#!/usr/bin/env python3
"""
fetch_trackaipac_live.py

Fetches the LIVE TrackAIPAC congress page (https://www.trackaipac.com/congress),
parses it, and updates the AIPAC fields in data/fec.json and the totals in
data/aipac.json so the registry mirrors TrackAIPAC's current published figures.

Why this exists:
  Previously AIPAC data was refreshed by hand-pasting the page into
  data/raw/trackaipac_page.txt and running fetch_trackaipac.py. That drifted
  out of date (e.g. TrackAIPAC added $1.5M in IE for Diana DeGette that the
  cached copy never captured). This script pulls the live page directly.

Methodology note (mirrors TrackAIPAC exactly):
  TrackAIPAC's current format publishes only:
    - Israel Lobby Total  (= direct PAC money + independent expenditures)
    - Donations / PACs    (direct PAC money — the site uses BOTH labels)
    - IE                  (independent expenditures)
  They no longer break out a separate "Lobby Donors" figure, so this script
  CLEARS aipac_lobby_donors. The registry shows what TrackAIPAC shows.

Run with --dry-run to preview changes without writing (used by the review
workflow before you approve a commit).
"""

import re, os, sys, json, argparse
import urllib.request

URL = "https://www.trackaipac.com/congress"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
FEC_PATH   = os.path.join(DATA_DIR, "fec.json")
AIPAC_PATH = os.path.join(DATA_DIR, "aipac.json")
RAW_PATH   = os.path.join(DATA_DIR, "raw", "trackaipac_page.txt")

SI_KEYS = ["aipac", "fossil_fuels", "pharma", "defense", "finance", "tech", "nra"]

# ── Name handling (reused from fetch_trackaipac.py) ──────────────────────────
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
        ("Mcconnell","McConnell"), ("Mcclain","McClain"), ("Mcgovern","McGovern"),
        ("Mccollum","McCollum"), ("Mcclellan","McClellan"), ("Mciver","McIver"),
        ("Mcgarvey","McGarvey"), ("Diaz-Balart","Diaz-Balart"),
    ]:
        name = name.replace(old, new)
    return name

def find_match(name, ta_data):
    key = normalize(name)
    if key in ta_data:
        return ta_data[key]
    last = key.split()[-1] if key.split() else ""
    hits = {k: v for k, v in ta_data.items() if k.split() and k.split()[-1] == last}
    if len(hits) == 1:
        return next(iter(hits.values()))
    return None

# ── Fetch ────────────────────────────────────────────────────────────────────
def fetch_page():
    req = urllib.request.Request(URL, headers={
        "User-Agent": "Mozilla/5.0 (compatible; InfluenceRegistryBot/1.0; +https://www.keep-dc-honest.com)"
    })
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8", errors="replace")

def html_to_text(html):
    """Strip HTML tags to plain text matching the format the parser expects.
    The live page is raw HTML; the parser was written for the rendered text
    (tags removed, content run together). This converts one to the other."""
    import html as _html
    # Drop script/style blocks entirely
    html = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', html)
    # Convert block-closing tags to nothing (content runs together like the
    # rendered page: "…Total: $X</div><div>Donations:…" -> "…Total: $XDonations:…")
    # but turn tags that separate NAME from previous content into a space so
    # member headers stay word-separated.
    html = re.sub(r'(?i)<br\s*/?>', ' ', html)
    # Remove all remaining tags
    html = re.sub(r'<[^>]+>', '', html)
    # Decode HTML entities (&amp; &#39; etc.)
    html = _html.unescape(html)
    # Collapse whitespace
    html = re.sub(r'[ \t\r\n\u2028\u2029\xa0]+', ' ', html)
    return html

# ── Parse (tested against live data) ─────────────────────────────────────────
def parse_live(text):
    """Return {normalized_name: {total, pacs, ie, sources, approved}}."""
    results = {}
    marker_re = re.compile(r'([A-Z]{2}-(?:SEN|At Large|[0-9]+))\s+\[([RDI])\]')
    markers = list(marker_re.finditer(text))

    for i, mk in enumerate(markers):
        body_start = mk.end()
        body_end = markers[i+1].start() if i+1 < len(markers) else len(text)
        chunk = text[body_start:body_end]

        pre = text[:mk.start()].rstrip()
        nm = re.search(
            r'([A-Z][A-Za-z.\-\'"]*[a-z][A-Za-z.\-\'"]*'
            r'(?:\s+(?:[A-Z][A-Za-z.\-\'"]*|of|the|de|van|von))*)\s*$', pre)
        if not nm:
            continue
        name = nm.group(1).strip()

        # Trim the NEXT member's name off the tail of this chunk
        if i+1 < len(markers):
            chunk = re.sub(
                r'\s+[A-Z][A-Za-z.\-\'"]*[a-z][A-Za-z.\-\'"]*'
                r'(?:\s+[A-Z][A-Za-z.\-\'"]*)*\s*$', '', chunk)

        key = normalize(title_name(name))

        if 'Track AIPAC Approved' in chunk:
            results[key] = {"total":0,"pacs":0,"ie":0,"sources":"","approved":True}
            continue

        def grab(pat):
            m = re.search(pat + r'\s*\$([\d,]+)', chunk)
            return int(m.group(1).replace(',','')) if m else None

        total = grab(r'(?:Israel\s+)?Lobby\s+Total:')
        pacs  = grab(r'Donations:')
        if pacs is None:
            pacs = grab(r'PACs:')
        ie = grab(r'IE:')
        if total is None and pacs is None and ie is None:
            continue
        total, pacs, ie = total or 0, pacs or 0, ie or 0

        sources = ""
        sm = re.search(r'IE:\s*\$[\d,]+([A-Z][A-Z0-9,\s\'()\u2018\u2019\-]+)', chunk)
        if sm:
            raw = sm.group(1).strip()
            raw = re.split(r'(?<=[A-Z0-9)])\s(?=[A-Z][a-z])', raw)[0]
            sources = raw.strip(" ,")

        results[key] = {"total":total,"pacs":pacs,"ie":ie,"sources":sources,"approved":False}
    return results

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Preview changes without writing files")
    ap.add_argument("--from-file", default=None,
                    help="Parse a local saved page instead of fetching (testing)")
    args = ap.parse_args()

    if args.from_file:
        text = open(args.from_file, encoding="utf-8").read()
        print(f"Parsing local file {args.from_file}")
    else:
        print(f"Fetching {URL} ...")
        raw = fetch_page()
        print(f"  Got {len(raw):,} chars of HTML")
        text = html_to_text(raw)
        print(f"  Converted to {len(text):,} chars of text")

    ta = parse_live(text)
    print(f"  Parsed {len(ta)} members from TrackAIPAC")
    if len(ta) < 400:
        print(f"  WARNING: only {len(ta)} members parsed (expected ~540). "
              f"Page format may have changed — review before committing.")

    fec = json.load(open(FEC_PATH))
    try:
        aipac_json = json.load(open(AIPAC_PATH))
    except FileNotFoundError:
        aipac_json = {}

    changes = []
    matched = zeroed = 0
    for name, d in fec.items():
        if name == "_meta":
            continue
        m = find_match(name, ta)
        old_total = d.get("aipac", 0)
        old_ie    = d.get("aipac_ie", 0)
        if m:
            new_total = m["pacs"] + m["ie"]   # mirror TrackAIPAC "Total"
            d["aipac"]              = new_total
            d["aipac_pacs"]         = m["pacs"]
            d["aipac_ie"]           = m["ie"]
            d["aipac_lobby_donors"] = 0        # TrackAIPAC dropped this field
            d["aipac_sources"]      = m["sources"]
            aipac_json[name]        = new_total
            matched += 1
            if new_total != old_total or m["ie"] != old_ie:
                changes.append((name, old_total, new_total, old_ie, m["ie"]))
        else:
            if old_total != 0:
                changes.append((name, old_total, 0, old_ie, 0))
            d["aipac"] = d["aipac_pacs"] = d["aipac_ie"] = 0
            d["aipac_lobby_donors"] = 0
            d["aipac_sources"] = ""
            aipac_json.pop(name, None)
            zeroed += 1
        d["special_interest_total"] = sum(d.get(k, 0) for k in SI_KEYS)

    # ── Report ──
    print(f"\n{'='*60}")
    print(f"  Matched   : {matched} members")
    print(f"  Unmatched : {zeroed} members set to $0")
    print(f"  CHANGES   : {len(changes)} members whose AIPAC total or IE changed")
    print(f"{'='*60}")
    if changes:
        print(f"\n{'Member':<28}{'old total':>12}{'new total':>12}{'old IE':>12}{'new IE':>12}")
        for name, ot, nt, oie, nie in sorted(changes, key=lambda x: -abs(x[2]-x[1]))[:40]:
            print(f"{name:<28}{ot:>12,}{nt:>12,}{oie:>12,}{nie:>12,}")
        if len(changes) > 40:
            print(f"  ... and {len(changes)-40} more")

    # Spot check the case that started this
    dg = fec.get("Diana DeGette", {})
    print(f"\nSPOT CHECK — Diana DeGette: total=${dg.get('aipac',0):,} "
          f"pacs=${dg.get('aipac_pacs',0):,} ie=${dg.get('aipac_ie',0):,}")

    if args.dry_run:
        print("\n[DRY RUN] No files written. Review the changes above.")
        return

    with open(FEC_PATH, "w") as f:
        json.dump(fec, f, indent=2, ensure_ascii=False)
    with open(AIPAC_PATH, "w") as f:
        json.dump(aipac_json, f, indent=2, ensure_ascii=False)
    # Also refresh the raw cache so the legacy script stays consistent
    try:
        os.makedirs(os.path.dirname(RAW_PATH), exist_ok=True)
        with open(RAW_PATH, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception as e:
        print(f"  (could not update raw cache: {e})")

    print(f"\nWrote {FEC_PATH} and {AIPAC_PATH}")

if __name__ == "__main__":
    main()
