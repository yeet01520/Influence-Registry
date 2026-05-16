#!/usr/bin/env python3
"""
scrape_trackaipac.py

Fetches https://www.trackaipac.com/congress and extracts, for every member,
TrackAIPAC's PUBLISHED per-member "Israel Lobby Total" verbatim (plus the
PACs / IE breakdown and the PAC list).

This is the authoritative source-of-truth artifact for 1:1 parity. We take
TrackAIPAC's printed total exactly as-is; we never reconstruct it from parts,
so there is zero methodology drift between the registry and TrackAIPAC.

Outputs:
  data/raw/trackaipac.json       <- authoritative {name: {...}} (used by validate.py + apply script)
  data/raw/trackaipac_page.txt   <- human-readable / legacy line format

Designed to run in GitHub Actions (open internet). No API key required.

Members shown as "Track AIPAC Approved!" (they reject AIPAC) or with no
printed figure are recorded with total/pacs/ie = 0 and status "approved".
"Vacant" seats are skipped entirely.
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

URL = "https://www.trackaipac.com/congress"
OUT_JSON = Path("data/raw/trackaipac.json")
OUT_TXT = Path("data/raw/trackaipac_page.txt")

# Squarespace serves the full member list server-side; a plain GET is enough.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
        "(+InfluenceRegistry trackaipac parity bot)"
    )
}

MONEY = r"\$([\d,]+)"


def fetch(url, retries=4):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last}")


def flatten(html):
    """HTML -> single-spaced plain text. We do NOT rely on DOM structure;
    every member's signature ('NAME ST-XX [P] Israel Lobby Total: $...')
    survives full tag removal as ordered text, which is far more robust to
    Squarespace markup churn than block parsing."""
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = (html.replace("&amp;", "&").replace("&nbsp;", " ")
                .replace("&#39;", "'").replace("&rsquo;", "'")
                .replace("\u2019", "'").replace("&ndash;", "-")
                .replace("&mdash;", "-"))
    return re.sub(r"\s+", " ", html).strip()


# The district token is the one unambiguous per-member anchor on the page.
DIST_ANCHOR = re.compile(
    r"\b([A-Z]{2})-(SEN|At Large|AL|\d{1,2})\s*\[\s*([RDI])\s*\]"
)
TOTAL_RE = re.compile(r"Israel Lobby Total:\s*" + MONEY)
PACS_RE = re.compile(r"PACs:\s*" + MONEY)
IE_RE = re.compile(r"\bIE:\s*" + MONEY)
NAME_TAIL = re.compile(
    r"((?:[A-Z][\w.'\u00C0-\u024F\-]+\s+){0,4}[A-Z][\w.'\u00C0-\u024F\-]+)\s*$"
)
_US_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "newhampshire", "newjersey", "newmexico", "newyork", "northcarolina",
    "northdakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhodeisland", "southcarolina", "southdakota", "tennessee", "texas",
    "utah", "vermont", "virginia", "washington", "westvirginia",
    "wisconsin", "wyoming", "new", "north", "south", "west", "rhode",
    "district", "hampshire", "jersey", "mexico", "york", "carolina",
    "dakota", "island",
}
_SECTION = {
    "sen", "rep", "the", "former", "us", "representative", "senator",
    "download", "graphics", "wait", "no", "congress", "running", "retiring",
    "next", "election", "for", "this", "track", "aipac", "approved",
}


def _is_stop(tok):
    t = tok.strip(".,").lower()
    if t in _US_STATES or t in _SECTION:
        return True
    # ALL-CAPS PAC tickers (AIPAC, DMFI, RJC, USI, TPOH...). Single letters
    # like the 'A.' in 'Carlos A. Gimenez' are length 1 -> not flagged.
    if re.fullmatch(r"[A-Z0-9]{2,}", tok.strip(".,")):
        return True
    return False


def to_int(s):
    return int(s.replace(",", "")) if s else 0


def _clean_name(raw):
    """Extract the actual member name from the noisy run preceding the
    district token. The page interleaves the name with state dividers,
    PAC tickers and 'Sen./Rep.' prefixes, and Squarespace sometimes repeats
    the name (image alt + heading). Cut at the last stopword, then collapse
    an immediately-repeated full name."""
    raw = re.sub(r"\b(Sen|Rep)\.\s*", "", raw).strip()
    toks = raw.split()
    last = -1
    for i, t in enumerate(toks):
        if _is_stop(t):
            last = i
    toks = toks[last + 1:]
    h = len(toks) // 2
    if h and toks[:h] == toks[h:]:
        toks = toks[:h]
    return " ".join(toks).strip()


def parse(text):
    """Scan fully-flattened text for the repeating per-member signature."""
    members = {}
    anchors = list(DIST_ANCHOR.finditer(text))
    for idx, a in enumerate(anchors):
        state, dist, party = a.group(1), a.group(2), a.group(3)
        pre = text[max(0, a.start() - 120):a.start()]
        nm = NAME_TAIL.search(pre)
        name = _clean_name(nm.group(1)) if nm else ""
        if not name or name.lower() == "vacant":
            continue
        end = anchors[idx + 1].start() if idx + 1 < len(anchors) else len(text)
        win = text[a.end():end]

        tm = TOTAL_RE.search(win)
        if tm:
            pm, iem = PACS_RE.search(win), IE_RE.search(win)
            total = to_int(tm.group(1))
            pacs = to_int(pm.group(1)) if pm else 0
            ie = to_int(iem.group(1)) if iem else 0
            status = "tracked"
        elif "Track AIPAC Approved" in win:
            total = pacs = ie = 0
            status = "approved"
        else:
            total = pacs = ie = 0
            status = "no_figure"

        plm = re.search(
            r"IE:\s*\$[\d,]+\s+([A-Z][A-Z0-9'(),. \-]{3,}?)\s+(?:\*|Sen\.|Rep\.|$)",
            win)
        members[name] = {
            "name": name, "state": state, "district": dist, "party": party,
            "israel_lobby_total": total, "pacs": pacs, "ie": ie,
            "pac_list": (plm.group(1).strip() if plm else ""),
            "status": status,
        }
    return members


def strip_tags(html):  # back-compat alias
    return flatten(html)


def main():
    html = fetch(URL)
    text = strip_tags(html)
    members = parse(text)

    tracked = sum(1 for m in members.values() if m["status"] == "tracked")
    approved = sum(1 for m in members.values() if m["status"] == "approved")
    nofig = sum(1 for m in members.values() if m["status"] == "no_figure")
    print(f"Parsed {len(members)} members "
          f"(tracked={tracked}, approved={approved}, no_figure={nofig})")

    if len(members) < 480:
        print(f"ERROR: only {len(members)} members parsed; expected ~538. "
              f"Aborting so we never commit a partial scrape.", file=sys.stderr)
        sys.exit(1)

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(members, indent=2, ensure_ascii=False),
                        encoding="utf-8")

    # Legacy / human-readable line file. One line per member, ALL-CAPS name
    # so the historical validate.py regex still parses it if ever needed.
    with OUT_TXT.open("w", encoding="utf-8") as f:
        for m in sorted(members.values(),
                        key=lambda x: (x["state"], x["district"])):
            f.write(
                f"{m['name'].upper()} {m['state']}-{m['district']} "
                f"[{m['party']}] Israel Lobby Total: "
                f"${m['israel_lobby_total']:,} PACs: ${m['pacs']:,} "
                f"IE: ${m['ie']:,} | {m['pac_list']}\n"
            )

    print(f"Wrote {OUT_JSON} and {OUT_TXT}")


if __name__ == "__main__":
    main()
