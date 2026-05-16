#!/usr/bin/env python3
"""
Data Integrity Validator for The Influence Registry
Run: python3 validate.py index.html [csv_dir] [trackaipac_path]
GitHub Action runs this automatically on every push to index.html or data/.

DESIGN: TWO-TIER VALIDATION
─────────────────────────────────────────────────────────────────────────────
HARD CHECKS (deploy-blocking errors)
  These enforce invariants the site cannot ship without. They check things
  that are objectively wrong: schema gaps, broken math, format violations.
  If any of these fail, the workflow fails and the site does not deploy.

  [H1] FEC coverage:           every Senate/House member has an entry in fec.json
  [H2] AIPAC internal math:    fec.aipac == aipac_pacs + aipac_lobby_donors + aipac_ie
  [H3] SIT internal math:      special_interest_total == sum of all PAC/sector buckets
  [H4] Grassroots integrity:   no known pledge-breaker carries the grassroots badge
  [H5] Score sanity:           non-grassroots members with big PAC money have non-trivial scores
  [H6] corporate_total format: each value parseable as label, career figure, or new-member placeholder

SOFT CHECKS (warnings, never block deploys)
  These flag cross-source disagreement that's normal for a multi-source data
  pipeline: FEC vs OpenSecrets timing differences, TrackAIPAC vs FEC bundled
  donor methodology gaps, card editorial overrides vs raw FEC totals.
  Warnings print but exit 0. Run periodically for data quality review.

  [S1] AIPAC_DATA vs fec.aipac_pacs:  three-source consistency
  [S2] AIPAC_DATA vs TrackAIPAC file: source verification
  [S3] Sector vs OpenSecrets CSV:     cross-source drift
  [S4] Card vs FEC:                   editorial display vs raw data drift
─────────────────────────────────────────────────────────────────────────────
"""

import sys, re, json, csv, unicodedata
from pathlib import Path

ERRORS   = []
WARNINGS = []

def err(msg):  ERRORS.append(msg)
def warn(msg): WARNINGS.append(msg)

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS — name normalization, money parsing, name-variant fixups
# ─────────────────────────────────────────────────────────────────────────────

def normalize(n):
    n = unicodedata.normalize("NFD", n)
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    n = n.lower()
    n = re.sub(r"\b(jr|sr|ii|iii|iv)\.?\b", "", n)
    n = re.sub(r"[^a-z\s]", " ", n)
    return re.sub(r"\s+", " ", n).strip()

def parse_csv_name(raw, key):
    m = re.match(r"^([A-Za-z\'\-]+)\s+(.+?)\s*\([RDIrdi]", raw)
    if m:
        last = m.group(1).strip()
        first = re.sub(r"\s+[A-Z]\s*$", "", m.group(2).strip()).strip()
        first = re.sub(r"\s+(Jr|Sr|II|III|IV)\.?\s*$", "", first, flags=re.I).strip()
        return f"{first} {last}"
    return raw.strip()

def parse_amount(s):
    return int(s.replace("$","").replace(",","").strip()) if s else 0

def parse_money_str(s):
    """Parse '$1.6M', '$159K', '$29,184'. Returns None for ranges/labels."""
    if not s or not isinstance(s, str): return None
    s = s.strip()
    if re.search(r"\d\s*-\s*\d", s): return None
    core = s.replace("$","").replace(",","").strip()
    m = re.match(r"^([\d.]+)\s*([KMB]?)$", core)
    if not m: return None
    val = float(m.group(1))
    suffix = m.group(2)
    if suffix == "K": val *= 1_000
    elif suffix == "M": val *= 1_000_000
    elif suffix == "B": val *= 1_000_000_000
    return val

def parse_corporate_total(s):
    """Parse a corporate_total value to a dollar number, tolerating 'career'
    suffix and trailing '+'. Returns None for ranges or unparseable values."""
    if not s or not isinstance(s, str): return None
    if re.search(r"\d\s*-\s*\d", s): return None
    s2 = s.strip()
    s2 = re.sub(r"\s*career\s*$", "", s2, flags=re.I).strip()
    s2 = re.sub(r"\s*\(.*\)\s*$", "", s2).strip()
    s2 = s2.replace("$","").replace(",","").strip()
    if s2.endswith("+"): s2 = s2[:-1].strip()
    m = re.match(r"^([\d.]+)\s*([KMB]?)$", s2)
    if not m: return None
    val = float(m.group(1))
    suffix = m.group(2)
    if suffix == "K": val *= 1_000
    elif suffix == "M": val *= 1_000_000
    elif suffix == "B": val *= 1_000_000_000
    return val

# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

def load_html(path):
    """Load registry data. Phase 1: inline `const X = {...}` in index.html.
    Phase 2: data/*.json files. Transparently handles both."""
    content = Path(path).read_text(encoding="utf-8")
    has_inline = "const FEC_V8_DATA" in content and "const PROFILES_DATA" in content

    if has_inline:
        def extract(pattern):
            m = re.search(pattern, content, re.DOTALL)
            return json.loads(m.group(1)) if m else {}
        def extract_list(pattern):
            m = re.search(pattern, content, re.DOTALL)
            return json.loads(m.group(1)) if m else []
        gn_idx = content.find("const GRASSROOTS_NAMES = new Set(")
        gn_end = content.find("]);", gn_idx) + 3 if gn_idx != -1 else 0
        grassroots = set(re.findall(r'"([^"]+)"', content[gn_idx:gn_end])) if gn_idx != -1 else set()
        return {
            "aipac":      extract(r"const AIPAC_DATA = (\{.*?\});"),
            "fec_v8":     extract(r"const FEC_V8_DATA = (\{.*?\});"),
            "profiles":   extract(r"const PROFILES_DATA = (\{.*?\});"),
            "senate":     extract_list(r"const SENATE_DATA = (\[.*?\]);"),
            "house":      extract_list(r"const HOUSE_DATA = (\[.*?\]);"),
            "grassroots": grassroots,
            "content":    content,
            "_phase":     1,
        }

    data_dir = Path(path).parent / "data"
    if not data_dir.exists():
        raise FileNotFoundError(
            f"index.html has no inline data and {data_dir}/ does not exist. "
            f"Cannot validate without a data source."
        )
    def load_json(name):
        return json.loads((data_dir / name).read_text(encoding="utf-8"))
    tags = load_json("tags.json")
    return {
        "aipac":      load_json("aipac.json"),
        "fec_v8":     load_json("fec.json"),
        "profiles":   load_json("profiles.json"),
        "senate":     load_json("senate.json"),
        "house":      load_json("house.json"),
        "grassroots": set(tags.get("grassroots", [])),
        "content":    content,
        "_phase":     2,
        "_data_dir":  data_dir,
    }

def load_csvs(base_dir="."):
    files = {
        "tech":    [("Money_from_Communications_Electronics_to_US_Representatives__1990-2024.csv","Representative"),
                    ("Money_from_Communications_Electronics_to_US_Senators__1990-2024.csv","Senator")],
        "defense": [("Money_from_Defense_to_US_Representatives__1990-2024.csv","Representative"),
                    ("Money_from_Defense_to_US_Senators__1990-2024.csv","Senator")],
        "finance": [("Money_from_Finance_Insurance_Real_Estate_to_US_Representatives__1990-2024.csv","Representative"),
                    ("Money_from_Finance_Insurance_Real_Estate_to_US_Senators__1990-2024.csv","Senator")],
        "pharma":  [("Money_from_Health_to_US_Representatives__1990-2024.csv","Representative"),
                    ("Money_from_Health_to_US_Senators__1990-2024.csv","Senator")],
        "oil":     [("Money_from_Oil___Gas_to_US_Representatives__1990-2024.csv","Representative"),
                    ("Money_from_Oil___Gas_to_US_Senators__1990-2024.csv","Senator")],
    }
    data = {s: {} for s in files}
    for sector, flist in files.items():
        for fname, key in flist:
            p = Path(base_dir) / fname
            if not p.exists():
                # CSVs are optional inputs for soft cross-source audits.
                # Their absence is informational, not a warning.
                continue
            with open(p, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    name = parse_csv_name(row[key], key)
                    data[sector][normalize(name)] = parse_amount(row["Amount"])
    return data

def load_trackaipac(path="data/raw/trackaipac.json"):
    """Return {normalized_name: israel_lobby_total}.

    Authoritative source is data/raw/trackaipac.json (scraped verbatim by
    scrape_trackaipac.py). We compare against TrackAIPAC's PUBLISHED per-member
    "Israel Lobby Total", which is the figure the registry mirrors 1:1.

    If a caller passes the legacy trackaipac_page.txt path (an older workflow
    may), we still PREFER a sibling trackaipac.json in the same directory so
    that every workflow validates against one identical source and they can
    never disagree on names or values. Only if no JSON exists anywhere do we
    fall back to parsing the legacy .txt line format.
    """
    data = {}
    p = Path(path)

    # Always prefer an authoritative trackaipac.json: the given path if it is
    # one, else a sibling next to whatever path was passed.
    json_candidates = []
    if p.suffix == ".json":
        json_candidates.append(p)
    json_candidates.append(p.parent / "trackaipac.json")
    json_candidates.append(Path("data/raw/trackaipac.json"))
    for jc in json_candidates:
        if jc.exists():
            try:
                raw = json.loads(jc.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            for m in raw.values():
                if isinstance(m, dict):
                    data[normalize(m.get("name", ""))] = int(
                        m.get("israel_lobby_total", 0) or 0)
            if data:
                return data

    legacy = p if (p.exists() and p.suffix == ".txt") else Path(
        "trackaipac_page.txt")
    if not legacy.exists():
        # TrackAIPAC source is an optional soft-check input.
        return data
    for line in legacy.read_text(encoding="utf-8").split("\n"):
        line = line.strip()
        if "Israel Lobby Total" not in line and "Lobby Total" not in line:
            continue
        m = re.match(
            r"([A-Z][A-Z\s\'\-\.]+?)\s+[A-Z]{2}-(?:[A-Z\d]+|At Large)\s*"
            r"[\[(][RDI][\])].*?Israel Lobby Total:\s*\$([\d,]+)", line)
        if not m:
            m = re.match(
                r"([A-Z][A-Z\s\'\-\.]+?)\s+[A-Z]{2}-(?:[A-Z\d]+|At Large)\s*"
                r"[\[(][RDI][\])].*?PACs:\s*\$([\d,]+)", line)
        if m:
            name = m.group(1).strip().title()
            data[normalize(name)] = int(m.group(2).replace(",", ""))
    return data

# Members documented as not being in 1990-2024 CSV (new members, name variants).
SKIP_CSV_CHECK = {
    "Beth Van Duyne","Clay Fuller","Derrick Van Orden","James Walkinshaw",
    "Jefferson Van Drew","Jimmy Patronis","Kristen McDonald Rivet",
    "Matt Van Epps","Monica De La Cruz","Pablo Hernández","Randy Fine",
    "Sydney Kamlager-Dove","Tom Kean Jr.","Ashley Moody","Alan Armstrong",
    "Jon Husted","Chris Van Hollen","Nydia Velázquez","Adelita Grijalva",
    "Eugene Vindman","Sam Liccardo","Robert Bresnahan","Nellie Pou",
    "Bob Onder","Johnny Olszewski","April McClain Delaney","Jeff Crank",
    "Katie Britt", "Wesley Bell",
}

CSV_LOOKUP_FIXES = {
    "katie britt": "katie boyd britt",
    "ben ray lujan": "ben lujan",
    "maria elvira salazar": "maria salazar",
}

TRACKAIPAC_NAME_FIXES = {
    "james risch": "jim risch",
    "maria elvira salazar": "maria salazar",
    "mariannette miller meeks": "marianette miller meeks",
    "johnny olszewski": "john olszewski",
    "jefferson van drew": "jeff van drew",
    "joseph morelle": "joe morelle",
    "michael turner": "mike turner",
    "abraham hamadeh": "abe hamadeh",
    "jerrold nadler": "jerry nadler",
    "robert bresnahan": "rob bresnahan",
    "carlos gimenez": "carlos a gimenez",
}

SECTOR_MAP = {"tech":"tech","defense":"defense","finance":"finance","pharma":"pharma","oil":"fossil_fuels"}

DONATIONS_TO_FEC = {
    "oil_gas":     "fossil_fuels",
    "wall_street": "finance",
    "pharma":      "pharma",
    "defense":     "defense",
    "tech":        "tech",
    "aipac":       "aipac_pacs",  # card aipac is direct PAC money == fec.aipac_pacs
}

# ─────────────────────────────────────────────────────────────────────────────
# HARD CHECKS — these fail the build if violated
# ─────────────────────────────────────────────────────────────────────────────

def check_fec_coverage(d):
    """[H1] Every Senate/House member must have a fec.json entry."""
    print("\n[H1] FEC coverage completeness...")
    missing = [m["name"] for m in d["senate"] + d["house"] if m["name"] not in d["fec_v8"]]
    if not missing:
        total = len(d["senate"]) + len(d["house"])
        print(f"  ✅ All {total} members have FEC entries")
        return
    for n in missing:
        err(f"No FEC entry — {n}")
    print(f"  ❌ {len(missing)} members missing FEC entries")

def check_aipac_internal_math(d):
    """[H2] fec.aipac must equal aipac_pacs + aipac_lobby_donors + aipac_ie.

    This is an invariant — fetch_fec_data.py computes aipac from these three
    components, so any disagreement indicates corruption or manual edit drift.
    Run scripts/fix_fec_consistency.py to repair."""
    print("\n[H2] AIPAC internal math (aipac == pacs + lobby + ie)...")
    issues = 0
    for name, fec in d["fec_v8"].items():
        pacs   = fec.get("aipac_pacs", 0) or 0
        lobby  = fec.get("aipac_lobby_donors", 0) or 0
        ie     = fec.get("aipac_ie", 0) or 0
        stored = fec.get("aipac", 0) or 0
        if abs((pacs + lobby + ie) - stored) > 100:
            err(f"AIPAC math broken — {name}: stored aipac=${stored:,} but pacs+lobby+ie=${pacs+lobby+ie:,}")
            issues += 1
    if issues == 0:
        print(f"  ✅ All {len(d['fec_v8'])} records pass AIPAC math invariant")
    else:
        print(f"  ❌ {issues} records violate AIPAC math invariant (run fix_fec_consistency.py)")

def check_aipac_trackaipac_parity(d, ta):
    """[H2b] fec.aipac must be 1:1 with TrackAIPAC's published per-member
    "Israel Lobby Total". This is the parity guarantee the registry commits
    to: we mirror TrackAIPAC's printed figure verbatim.

    Enforcement policy:
      • TrackAIPAC source PRESENT  -> any mismatch is a hard ERROR (blocks
        deploy). This is the actual 1:1 contract.
      • TrackAIPAC source ABSENT   -> skip with a loud WARNING. A missing
        scrape artifact is a CI/infra problem, not data corruption, and must
        not brick an otherwise-valid deploy.
    Members not present on TrackAIPAC (cabinet appointees, etc.) are reported
    but not failed — we never invent a figure we cannot source."""
    print("\n[H2b] AIPAC 1:1 parity with TrackAIPAC published total...")
    if not ta:
        warn("TrackAIPAC source missing — 1:1 parity NOT enforced this run "
             "(run scrape_trackaipac.py; check the refresh workflow)")
        print("  ⊘ skipped — TrackAIPAC source not available (warning logged)")
        return
    issues = 0
    not_in_source = 0
    for name, fec in d["fec_v8"].items():
        n = normalize(name)
        ta_val = ta.get(TRACKAIPAC_NAME_FIXES.get(n, n))
        if ta_val is None:
            not_in_source += 1
            continue
        stored = int(fec.get("aipac", 0) or 0)
        if stored != ta_val:
            err(f"AIPAC parity broken — {name}: registry=${stored:,} but "
                f"TrackAIPAC publishes ${ta_val:,} "
                f"(run apply_trackaipac_parity.py)")
            issues += 1
    checked = len(d["fec_v8"]) - not_in_source
    if issues == 0:
        print(f"  ✅ {checked} members exactly 1:1 with TrackAIPAC "
              f"({not_in_source} not on TrackAIPAC, preserved)")
    else:
        print(f"  ❌ {issues} members NOT 1:1 with TrackAIPAC "
              f"(run scripts/apply_trackaipac_parity.py)")


def check_sit_internal_math(d):
    """[H3] special_interest_total must equal sum of all additive components.

    Components: aipac_pacs + aipac_lobby_donors + aipac_ie + fossil + pharma
    + defense + finance + tech + nra. Note we sum aipac_pacs (not aipac) to
    avoid double-counting — aipac is itself the sum of pacs+lobby+ie."""
    print("\n[H3] special_interest_total internal math...")
    issues = 0
    for name, fec in d["fec_v8"].items():
        parts = ((fec.get("aipac_pacs",         0) or 0) +
                 (fec.get("aipac_lobby_donors", 0) or 0) +
                 (fec.get("aipac_ie",           0) or 0) +
                 (fec.get("fossil_fuels",       0) or 0) +
                 (fec.get("pharma",             0) or 0) +
                 (fec.get("defense",            0) or 0) +
                 (fec.get("finance",            0) or 0) +
                 (fec.get("tech",               0) or 0) +
                 (fec.get("nra",                0) or 0))
        stored = fec.get("special_interest_total", 0) or 0
        if abs(parts - stored) > 10000:
            err(f"SIT math broken — {name}: parts=${parts:,} / stored=${stored:,} (diff=${parts-stored:+,})")
            issues += 1
    if issues == 0:
        print(f"  ✅ All {len(d['fec_v8'])} records pass SIT math invariant")
    else:
        print(f"  ❌ {issues} records violate SIT math invariant (run fix_fec_consistency.py)")

def check_grassroots_integrity(d):
    """[H4] Grassroots badge integrity. Only flag known pledge breakers.
    FEC sector totals for grassroots members are individual donations
    categorized by donor employer, not corporate PAC money — high totals
    are expected and correct for long-serving grassroots members."""
    print("\n[H4] Grassroots badge integrity...")
    grassroots = d["grassroots"]
    KNOWN_PLEDGE_BREAKERS = set()  # extend manually with verified violations
    issues = 0
    for name in grassroots:
        if name in KNOWN_PLEDGE_BREAKERS:
            err(f"Known pledge breaker still has grassroots badge — {name}")
            issues += 1
        fec = d["fec_v8"].get(name, {})
        fossil = fec.get("fossil_fuels", 0) or 0
        if fossil > 1_000_000:
            warn(f"Grassroots member has high fossil fuel total (review) — {name}: ${fossil:,}")
    if issues == 0:
        print(f"  ✅ All {len(grassroots)} grassroots members pass integrity check")
    else:
        print(f"  ❌ {issues} badge integrity issues")

def check_score_sanity(d):
    """[H5] Non-grassroots members with confirmed DIRECT PAC money should
    have non-trivial scores. We deliberately count only direct contributions
    — fec.aipac_pacs + fossil — NOT the displayed AIPAC total.

    Why aipac_pacs and not aipac: since the TrackAIPAC parity change, the
    displayed `aipac` is TrackAIPAC's "Israel Lobby Total", which folds in
    independent expenditures (outside ad spending the member never received)
    and bundled large-donor money. This check's purpose is to catch a score
    that contradicts money the member actually took; attributing IE/bundling
    to the member would make the registry assert a false "received $X" claim.
    fec.aipac_pacs is the direct-contribution figure and matches what this
    check measured before the parity change (when AIPAC_DATA ~= aipac_pacs).
    Members whose large totals are IE/bundling are still fully shown on their
    cards; they just don't hard-fail the build on a soft attribution."""
    print("\n[H5] Score sanity check (direct PAC money)...")
    issues = 0
    for name, prof in d["profiles"].items():
        if name in d["grassroots"]:
            continue
        if not isinstance(prof, dict):
            continue
        score = prof.get("corruption_score", 0) or 0
        fec = d["fec_v8"].get(name, {})
        confirmed_pac = ((fec.get("aipac_pacs", 0) or 0) +
                         (fec.get("fossil_fuels", 0) or 0))
        if confirmed_pac > 500_000 and score < 50:
            err(f"Score anomaly — {name}: score={score} but confirmed PAC money=${confirmed_pac:,}")
            issues += 1
    if issues == 0:
        print(f"  ✅ All scores look sane")
    else:
        print(f"  ❌ {issues} score anomalies")

def check_corporate_total(d):
    """[H6] Validate corporate_total format. Acceptable values:
      - Grassroots members: must contain '$0'
      - Congress non-grassroots: '$0 corporate PAC' label, OR
        numeric with 'career' label (sum >= visible sectors), OR
        new-member placeholder ('first-term', 'sworn in', 'data pending')
      - SCOTUS/Cabinet (in profiles but not senate/house): no rules
      - Missing for Congress: warning only (may be intentional)"""
    print("\n[H6] corporate_total format & math...")
    grassroots = d["grassroots"]
    congress = {m["name"] for m in d["senate"] + d["house"]}
    issues = 0
    for name, prof in d["profiles"].items():
        if not isinstance(prof, dict): continue
        don = prof.get("donations", {})
        if not don: continue
        ct = don.get("corporate_total", "")
        is_g = name in grassroots
        is_c = name in congress

        if is_g:
            if "$0" not in ct:
                err(f"corporate_total — grassroots {name}: '{ct}' should contain '$0'")
                issues += 1
            continue

        if not is_c:
            continue  # SCOTUS/Cabinet — no rules

        if not ct:
            warn(f"corporate_total missing — {name} (Congress non-grassroots)")
            continue

        # '$0 corporate PAC' style label
        if "$0" in ct and "corporate" in ct.lower():
            continue

        # New-member placeholder
        ct_lower = ct.lower()
        if any(phrase in ct_lower for phrase in ("first-term", "first term", "sworn in", "data pending")):
            continue

        # 'career' label with math check
        if "career" in ct.lower():
            val = parse_corporate_total(ct)
            if val is not None:
                vsum = sum(parse_money_str(don.get(k,"")) or 0
                           for k in ["oil_gas","pharma","wall_street","defense","tech"])
                if val < vsum * 0.95:
                    err(f"corporate_total impossible — {name}: '{ct}' (${val:,.0f}) "
                        f"< visible sector sum ${vsum:,.0f}")
                    issues += 1
            continue

        err(f"corporate_total format — {name}: '{ct}' must contain 'career', "
            f"a new-member label (first-term/sworn in/data pending), "
            f"or be a '$0 corporate PAC' label")
        issues += 1

    if issues == 0:
        print(f"  ✅ All corporate_total values pass format & math checks")
    else:
        print(f"  ❌ {issues} corporate_total issues")

# ─────────────────────────────────────────────────────────────────────────────
# SOFT CHECKS — warnings only, never block deploys
# ─────────────────────────────────────────────────────────────────────────────

def check_aipac_vs_fec_pacs(d):
    """[S1] AIPAC_DATA (the displayed per-member figure) must equal
    fec.aipac (the canonical total). Post-parity these are both TrackAIPAC's
    "Israel Lobby Total" and are written together by the apply step; a
    mismatch means aipac.json and fec.json desynced — flag for review.

    (Pre-parity this compared against fec.aipac_pacs because the displayed
    number was PAC-only. The 1:1 design makes the displayed number the total,
    so the meaningful consistency check is now AIPAC_DATA == fec.aipac.)"""
    print("\n[S1] AIPAC_DATA vs fec.aipac total (cross-source)...")
    mismatches = 0
    for name, aipac_val in d["aipac"].items():
        fec_total = d["fec_v8"].get(name, {}).get("aipac", 0) or 0
        if abs(aipac_val - fec_total) > 1000:
            warn(f"AIPAC drift — {name}: AIPAC_DATA=${aipac_val:,} / "
                 f"fec.aipac=${fec_total:,}")
            mismatches += 1
    if mismatches == 0:
        print(f"  ✅ All {len(d['aipac'])} AIPAC entries consistent")
    else:
        print(f"  ⚠️  {mismatches} AIPAC drift entries (warning)")

def check_aipac_vs_trackaipac(d, ta):
    """[S2] AIPAC_DATA should match TrackAIPAC source file. Drift typically
    means TrackAIPAC was updated upstream and our aipac.json hasn't been
    refreshed yet — flag for review, don't block deploy."""
    print("\n[S2] AIPAC_DATA vs TrackAIPAC source file...")
    if not ta:
        print(f"  ⊘ skipped — TrackAIPAC source not available")
        return
    drift = 0
    not_in_source = 0
    for name, our_val in d["aipac"].items():
        n = normalize(name)
        lookup = TRACKAIPAC_NAME_FIXES.get(n, n)
        ta_val = ta.get(lookup)
        if ta_val is None:
            if our_val > 0:
                not_in_source += 1
            continue
        if abs(our_val - ta_val) > 5000:
            warn(f"TrackAIPAC drift — {name}: ours=${our_val:,} / source=${ta_val:,}")
            drift += 1
    cross_validated = len(d["aipac"]) - not_in_source
    if drift == 0:
        print(f"  ✅ {cross_validated} cross-validated, {not_in_source} not in source")
    else:
        print(f"  ⚠️  {drift} TrackAIPAC drift entries (warning)")

def check_sectors_vs_csv(d, csvs, members):
    """[S3] FEC sector totals vs OpenSecrets CSV totals. Disagreement is
    expected since the two sources use different methodologies (FEC =
    PAC-ID + employer keyword; OpenSecrets = professional industry
    classification). Flag for review, don't block."""
    print("\n[S3] Sector values vs CSV source files...")
    if not any(csvs.values()):
        print(f"  ⊘ skipped — no CSV files available")
        return
    drift = 0
    members_by_name = {m["name"]: m for m in members}
    for name, fec in d["fec_v8"].items():
        if name in SKIP_CSV_CHECK:
            continue
        if name not in members_by_name:
            continue
        n = normalize(name)
        n = CSV_LOOKUP_FIXES.get(n, n)
        for csv_sector, fec_key in SECTOR_MAP.items():
            csv_val = csvs[csv_sector].get(n, 0)
            fec_val = fec.get(fec_key, 0) or 0
            diff = abs(csv_val - fec_val)
            pct = diff/csv_val*100 if csv_val > 0 else (100 if fec_val > 0 else 0)
            if diff > 10000 and pct > 10:
                # FEC=$0 with CSV>$0 is a pipeline gap, distinct from
                # a true data conflict; surface both as warnings.
                if fec_val == 0 and csv_val > 0:
                    warn(f"FEC sector data missing — {name} [{fec_key}]: CSV=${csv_val:,} but fec.json=$0")
                else:
                    warn(f"Sector drift — {name} [{csv_sector}]: FEC=${fec_val:,} / CSV=${csv_val:,}")
                drift += 1
    if drift == 0:
        print(f"  ✅ All sector values consistent with CSVs")
    else:
        print(f"  ⚠️  {drift} sector drift entries (warning)")

def check_card_vs_fec(d):
    """[S4] Profile card display values vs fec.json. The card values are
    editorial choices: sometimes manually set to highlight current cycle
    only, sometimes rounded for readability, sometimes intentionally
    showing a different number than the FEC total. Cross-check for drift
    but never block on it."""
    print("\n[S4] Card display sectors vs fec.json...")
    drift = 0
    for name, prof in d["profiles"].items():
        if not isinstance(prof, dict): continue
        donations = prof.get("donations")
        if not donations: continue
        fec = d["fec_v8"].get(name, {})
        if not fec: continue
        for don_field, fec_field in DONATIONS_TO_FEC.items():
            display_str = donations.get(don_field)
            if not display_str: continue
            display_val = parse_money_str(display_str)
            if display_val is None: continue
            fec_val = fec.get(fec_field, 0) or 0
            if fec_val == 0 and display_val > 0:
                warn(f"FEC sector data missing — {name} [{fec_field}]: card='{display_str}' but fec.json=$0")
                drift += 1
                continue
            tolerance = max(50000, fec_val * 0.10)
            if abs(display_val - fec_val) > tolerance:
                warn(f"Card drift — {name} [{don_field}↔{fec_field}]: "
                     f"card='{display_str}' / fec.json=${fec_val:,}")
                drift += 1
    if drift == 0:
        print(f"  ✅ All card sector values consistent with fec.json")
    else:
        print(f"  ⚠️  {drift} card drift entries (warning)")

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 validate.py index.html [csv_dir] [trackaipac_path]")
        sys.exit(1)
    html_path = sys.argv[1]
    csv_dir   = sys.argv[2] if len(sys.argv) > 2 else "."
    ta_path   = sys.argv[3] if len(sys.argv) > 3 else "data/raw/trackaipac.json"

    print(f"\n=== Influence Registry Data Validator ===")
    print(f"HTML: {html_path}")

    d    = load_html(html_path)
    csvs = load_csvs(csv_dir)
    ta   = load_trackaipac(ta_path)
    all_members = d["senate"] + d["house"]

    # HARD CHECKS — must pass for deploy
    check_fec_coverage(d)
    check_aipac_internal_math(d)
    check_aipac_trackaipac_parity(d, ta)
    check_sit_internal_math(d)
    check_grassroots_integrity(d)
    check_score_sanity(d)
    check_corporate_total(d)

    # SOFT CHECKS — warnings only
    check_aipac_vs_fec_pacs(d)
    check_aipac_vs_trackaipac(d, ta)
    check_sectors_vs_csv(d, csvs, all_members)
    check_card_vs_fec(d)

    print(f"\n=== RESULTS ===")
    if ERRORS:
        print(f"❌ {len(ERRORS)} ERROR(S):")
        for e in ERRORS: print(f"   • {e}")
    if WARNINGS:
        print(f"⚠️  {len(WARNINGS)} WARNING(S):")
        for w in WARNINGS: print(f"   • {w}")
    if not ERRORS and not WARNINGS:
        print("✅ All checks passed — safe to deploy")
    elif not ERRORS:
        print(f"\n✅ No errors — safe to deploy ({len(WARNINGS)} warnings for review)")
    else:
        print(f"\n❌ {len(ERRORS)} errors must be fixed before deploy")

    sys.exit(1 if ERRORS else 0)

if __name__ == "__main__":
    main()
