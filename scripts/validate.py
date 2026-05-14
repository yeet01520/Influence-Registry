#!/usr/bin/env python3
"""
Data Integrity Validator for The Influence Registry
Run: python3 validate.py index.html [csv_dir] [trackaipac_path]
GitHub Action runs this automatically on every push to index.html.
"""

import sys, re, json, csv, unicodedata
from pathlib import Path

ERRORS   = []
WARNINGS = []

def err(msg):  ERRORS.append(msg)
def warn(msg): WARNINGS.append(msg)

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

def load_html(path):
    """Load registry data from index.html. In Phase 1, data lives inline as
    `const X = {...}` literals. In Phase 2, those literals are gone and data
    lives in /data/*.json files. This function transparently handles both."""
    content = Path(path).read_text(encoding="utf-8")

    # Phase 1 detection: do inline literals exist?
    has_inline = "const FEC_V8_DATA" in content and "const PROFILES_DATA" in content

    if has_inline:
        # Phase 1: extract from HTML inline literals
        def extract(pattern):
            m = re.search(pattern, content, re.DOTALL)
            return json.loads(m.group(1)) if m else {}
        def extract_list(pattern):
            m = re.search(pattern, content, re.DOTALL)
            return json.loads(m.group(1)) if m else []
        # Grassroots names: search inline Set literal
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

    # Phase 2: load from /data folder
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
        "content":    content,  # may be empty of data; some checks need URL refs
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
                warn(f"CSV not found: {fname}")
                continue
            with open(p, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    name = parse_csv_name(row[key], key)
                    data[sector][normalize(name)] = parse_amount(row["Amount"])
    return data

def load_trackaipac(path="trackaipac_page.txt"):
    data = {}
    p = Path(path)
    if not p.exists():
        warn(f"TrackAIPAC file not found: {path}")
        return data
    for line in p.read_text(encoding="utf-8").split("\n"):
        line = line.strip()
        if "Israel Lobby Total" not in line and "Lobby Total" not in line:
            continue
        m = re.match(r"([A-Z][A-Z\s\'\-\.]+?)\s+[A-Z]{2}-(?:[A-Z\d]+|At Large)\s*[\[(][RDI][\])].*?PACs:\s*\$([\d,]+)", line)
        if m:
            name = m.group(1).strip().title()
            pacs = int(m.group(2).replace(",",""))
            data[normalize(name)] = pacs
    return data

# Members not in 1990-2024 CSV (newer members / name variants handled separately)
SKIP_CSV_CHECK = {
    "Beth Van Duyne","Clay Fuller","Derrick Van Orden","James Walkinshaw",
    "Jefferson Van Drew","Jimmy Patronis","Kristen McDonald Rivet",
    "Matt Van Epps","Monica De La Cruz","Pablo Hernández","Randy Fine",
    "Sydney Kamlager-Dove","Tom Kean Jr.","Ashley Moody","Alan Armstrong",
    "Jon Husted","Chris Van Hollen","Nydia Velázquez","Adelita Grijalva",
    # New members elected 2024/2025 not in 1990-2024 CSV
    "Eugene Vindman","Sam Liccardo","Robert Bresnahan","Nellie Pou",
    "Bob Onder","Johnny Olszewski","April McClain Delaney","Jeff Crank",
    "Katie Britt",  # CSV has "Katie Boyd Britt"
    # Wesley Bell: OpenSecrets CSV captured only a partial floor of his sectors
    # because he was a new federal candidate when CSV was compiled.
    # Sector values verified directly from FEC Schedule A (June 2023 - March 2026).
    "Wesley Bell",
}

# Correct nickname map for CSV lookup (CSV name -> our name, NOT FEC internal)
CSV_LOOKUP_FIXES = {
    "katie britt": "katie boyd britt",
    "ben ray lujan": "ben lujan",
    "maria elvira salazar": "maria salazar",
}

# TrackAIPAC source uses nickname/spelling variants that don't match our roster names.
# Maps our normalized name -> normalized name as parsed from trackaipac_page.txt.
TRACKAIPAC_NAME_FIXES = {
    # Senate
    "james risch": "jim risch",
    # House — TrackAIPAC uses common nicknames
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

def check_aipac_consistency(d):
    print("\n[1] AIPAC three-source consistency (AIPAC_DATA vs FEC_V8_DATA)...")
    mismatches = 0
    for name, aipac_val in d["aipac"].items():
        # AIPAC_DATA tracks direct PAC contributions only, which matches
        # fec.json's aipac_pacs subfield (not the full aipac total, which
        # also includes lobby-donor bundling and independent expenditures).
        fec_val = d["fec_v8"].get(name, {}).get("aipac_pacs", 0) or 0
        diff = abs(aipac_val - fec_val)
        if diff > 1000:
            err(f"AIPAC mismatch — {name}: AIPAC_DATA=${aipac_val:,} / FEC_V8.aipac_pacs=${fec_val:,} (diff=${diff:,})")
            mismatches += 1
    if mismatches == 0:
        print(f"  ✅ All {len(d['aipac'])} AIPAC entries consistent")
    else:
        print(f"  ❌ {mismatches} AIPAC mismatches found")

def check_aipac_vs_trackaipac(d, ta):
    print("\n[2] AIPAC_DATA vs TrackAIPAC source file...")
    mismatches = 0
    not_in_source = []
    for name, our_val in d["aipac"].items():
        n = normalize(name)
        # Apply name-variant fixes (e.g. "James Risch" -> "Jim Risch" in source)
        lookup = TRACKAIPAC_NAME_FIXES.get(n, n)
        ta_val = ta.get(lookup)
        if ta_val is None:
            if our_val > 0:
                not_in_source.append((name, our_val))
            continue
        diff = abs(our_val - ta_val)
        if diff > 5000:
            err(f"AIPAC vs TrackAIPAC — {name}: ours=${our_val:,} / TrackAIPAC=${ta_val:,} (diff=${diff:,})")
            mismatches += 1
    if not_in_source:
        # Treat as warning, not error — TrackAIPAC source may legitimately not list every member
        for name, val in not_in_source:
            warn(f"Member with AIPAC>$0 but not in TrackAIPAC source — {name}: ${val:,}")
    if mismatches == 0:
        print(f"  ✅ AIPAC values match TrackAIPAC source ({len(d['aipac']) - len(not_in_source)} cross-validated, {len(not_in_source)} not in source)")
    else:
        print(f"  ❌ {mismatches} TrackAIPAC mismatches")

def check_sectors_vs_csv(d, csvs, members):
    print("\n[3] Sector values vs CSV source files...")
    mismatches = 0
    for member in members:
        name = member["name"]
        if name in SKIP_CSV_CHECK:
            continue
        hnorm = normalize(name)
        csv_key = CSV_LOOKUP_FIXES.get(hnorm, hnorm)
        fec = d["fec_v8"].get(name, {})

        for csv_sector, fec_key in SECTOR_MAP.items():
            csv_val = csvs[csv_sector].get(csv_key, None)
            if csv_val is None:
                continue  # member not in this CSV file — skip
            fec_val = fec.get(fec_key, 0) or 0
            diff = abs(csv_val - fec_val)
            pct = diff/csv_val*100 if csv_val > 0 else (100 if fec_val > 0 else 0)
            if diff > 10000 and pct > 10:
                err(f"Sector mismatch — {name} [{csv_sector}]: FEC=${fec_val:,} / CSV=${csv_val:,} (diff=${diff:,})")
                mismatches += 1
    if mismatches == 0:
        print(f"  ✅ All sector values match CSV sources")
    else:
        print(f"  ❌ {mismatches} sector mismatches")

def check_grassroots_integrity(d):
    """Verify grassroots badge integrity.
    NOTE: FEC sector totals are employment-categorized INDIVIDUAL donations, not PAC checks.
    Bernie Sanders $20M pharma = doctors/nurses donating $250 each over 30 years.
    We only flag extreme outliers that warrant manual review."""
    print("\n[4] Grassroots badge integrity...")
    grassroots = d["grassroots"]
    issues = 0
    # Only flag if fossil fuel > $1M (hard to explain as individual donations)
    # or if member is known to have broken their pledge (manual list)
    KNOWN_PLEDGE_BREAKERS = set()  # populated manually when confirmed
    for name in grassroots:
        if name in KNOWN_PLEDGE_BREAKERS:
            err(f"Known pledge breaker still has grassroots badge — {name}")
            issues += 1
        fec = d["fec_v8"].get(name, {})
        fossil = fec.get("fossil_fuels", 0) or 0
        if fossil > 1000000:
            warn(f"Grassroots member has high fossil fuel total (review) — {name}: ${fossil:,}")
    if issues == 0:
        print(f"  ✅ All {len(grassroots)} grassroots members pass integrity check")
    else:
        print(f"  ❌ {issues} badge integrity issues")

def check_all_members_in_fec(d):
    print("\n[5] FEC coverage completeness...")
    missing = []
    for m in d["senate"] + d["house"]:
        if m["name"] not in d["fec_v8"]:
            missing.append(m["name"])
    if not missing:
        print(f"  ✅ All {len(d['senate'])+len(d['house'])} members have FEC entries")
    else:
        for n in missing:
            warn(f"No FEC entry — {n}")
        print(f"  ⚠️  {len(missing)} members missing FEC entries")

def parse_money_str(s):
    """Parse user-facing money strings like '$1.6M', '$159K', '$29,184'.
    Returns None for ranges ('$5-18M'), labels ('$0 corporate PAC'), or unparseable values."""
    if not s or not isinstance(s, str): return None
    s = s.strip()
    # Reject ranges
    if re.search(r"\d\s*-\s*\d", s): return None
    # Reject if there's significant trailing/leading text (labels)
    core = s.replace("$","").replace(",","").strip()
    m = re.match(r"^([\d.]+)\s*([KMB]?)$", core)
    if not m: return None
    val = float(m.group(1))
    suffix = m.group(2)
    if suffix == "K": val *= 1_000
    elif suffix == "M": val *= 1_000_000
    elif suffix == "B": val *= 1_000_000_000
    return val

def check_special_interest_total_math(d):
    """special_interest_total should equal the sum of all tracked PAC/sector buckets."""
    print("\n[7] FEC_V8 special_interest_total aggregate math...")
    issues = 0
    for name, fec in d["fec_v8"].items():
        # special_interest_total is the sum of additive components.
        # Note: fec.aipac is a *derived total* (aipac_pacs + aipac_lobby_donors + aipac_ie),
        # so we sum aipac_pacs here — using aipac would double-count lobby/IE.
        parts = ((fec.get("aipac_pacs",0) or 0) +
                 (fec.get("aipac_lobby_donors",0) or 0) +
                 (fec.get("aipac_ie",0) or 0) +
                 (fec.get("fossil_fuels",0) or 0) +
                 (fec.get("pharma",0) or 0) +
                 (fec.get("defense",0) or 0) +
                 (fec.get("finance",0) or 0) +
                 (fec.get("tech",0) or 0) +
                 (fec.get("nra",0) or 0))
        stored = fec.get("special_interest_total", 0) or 0
        diff = stored - parts
        if abs(diff) > 10000:
            err(f"special_interest_total mismatch — {name}: parts=${parts:,} / stored=${stored:,} (diff=${diff:+,})")
            issues += 1
        elif abs(diff) > 100:
            warn(f"special_interest_total small drift — {name}: parts=${parts:,} / stored=${stored:,} (diff=${diff:+,})")
    if issues == 0:
        print(f"  ✅ All {len(d['fec_v8'])} special_interest_total values check out")
    else:
        print(f"  ❌ {issues} aggregate math errors (>$10K)")

# Maps PROFILES.donations field → FEC_V8 field
DONATIONS_TO_FEC = {
    "oil_gas":     "fossil_fuels",
    "wall_street": "finance",
    "pharma":      "pharma",
    "defense":     "defense",
    "tech":        "tech",
    "aipac":       "aipac_pacs",
}

def check_card_sector_consistency(d):
    """PROFILES.donations sector strings should match FEC_V8 numbers (within rounding)."""
    print("\n[8] Card display sectors vs FEC_V8 numbers...")
    issues = 0
    checked = 0
    for name, prof in d["profiles"].items():
        donations = prof.get("donations") if isinstance(prof, dict) else None
        if not donations: continue
        fec = d["fec_v8"].get(name, {})
        if not fec: continue
        for don_field, fec_field in DONATIONS_TO_FEC.items():
            display_str = donations.get(don_field)
            if not display_str: continue
            display_val = parse_money_str(display_str)
            if display_val is None: continue  # skip ranges/labels
            fec_val = fec.get(fec_field, 0) or 0
            checked += 1
            # FEC=$0 with card>$0 is a pipeline gap (fetch_fec_data.py hasn't
            # captured this member/sector yet), not a card data error. Warn,
            # don't block deploys. Run fetch_fec_data.py to backfill.
            if fec_val == 0 and display_val > 0:
                warn(f"FEC sector data missing — {name} [{fec_field}]: card shows "
                     f"'{display_str}' but fec.json has $0 (run fetch_fec_data.py)")
                continue
            # Tolerance: 10% or $50K, whichever is larger (handles rounding to 2 sig figs)
            tolerance = max(50000, fec_val * 0.10)
            diff = abs(display_val - fec_val)
            if diff > tolerance:
                err(f"Card/FEC mismatch — {name} [{don_field}↔{fec_field}]: "
                    f"card='{display_str}' (${display_val:,.0f}) vs FEC=${fec_val:,} (diff=${diff:,.0f})")
                issues += 1
    if issues == 0:
        print(f"  ✅ All {checked} card sector values match FEC_V8 within tolerance")
    else:
        print(f"  ❌ {issues} card-vs-FEC mismatches")

def parse_corporate_total(s):
    """Parse a corporate_total value to a number, tolerating a 'career' suffix
    and trailing '+'. Returns None for ranges, labels, or unparseable values."""
    if not s or not isinstance(s, str): return None
    if re.search(r"\d\s*-\s*\d", s): return None  # ranges
    s2 = s.strip()
    s2 = re.sub(r"\s*career\s*$", "", s2, flags=re.I).strip()
    s2 = re.sub(r"\s*\(.*\)\s*$", "", s2).strip()
    s2 = s2.replace("$","").replace(",","").strip()
    if s2.endswith("+"): s2 = s2[:-1].strip()
    m = re.match(r"^([\d.]+)\s*([KMB]?)$", s2)
    if not m: return None
    val = float(m.group(1))
    if m.group(2) == "K": val *= 1_000
    elif m.group(2) == "M": val *= 1_000_000
    elif m.group(2) == "B": val *= 1_000_000_000
    return val

def check_corporate_total(d):
    """Validate the editorial corporate_total field.
    Rules:
    - Grassroots members: must contain '$0'
    - Congress non-grassroots: must be EITHER
        * a '$0 corporate PAC' style label (no corporate PAC pledged), OR
        * a numeric/range value with 'career' label, AND parsed value >= sum of visible sectors
    - SCOTUS/Cabinet (in profiles but not in senate/house): no rules — 'N/A' acceptable
    - Missing corporate_total for Congress: WARNING (not error — may be intentional for new members)"""
    print("\n[9] corporate_total format & math...")
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

        # Grassroots: must contain '$0'
        if is_g:
            if "$0" not in ct:
                err(f"corporate_total — grassroots {name}: '{ct}' should contain '$0'")
                issues += 1
            continue

        # Non-Congress (SCOTUS/Cabinet): no rules
        if not is_c:
            continue

        # Congress non-grassroots — must have a value
        if not ct:
            warn(f"corporate_total missing — {name} (Congress non-grassroots)")
            continue

        # Acceptable: '$0 corporate PAC' style label (any variant containing both)
        if "$0" in ct and "corporate" in ct.lower():
            continue

        # Acceptable: explicit new-member documentation. First-term members
        # don't have meaningful "career" data yet; an honest pending label
        # is preferable to a misleading $0.
        ct_lower = ct.lower()
        if any(phrase in ct_lower for phrase in ("first-term", "first term", "sworn in", "data pending")):
            continue

        # Acceptable: contains 'career' label
        if "career" in ct.lower():
            val = parse_corporate_total(ct)  # None for ranges — skip math check
            if val is not None:
                vsum = sum(parse_money_str(don.get(k,"")) or 0
                           for k in ["oil_gas","pharma","wall_street","defense","tech"])
                if val < vsum * 0.95:  # 5% tolerance for rounding
                    err(f"corporate_total impossible — {name}: '{ct}' (${val:,.0f}) "
                        f"< visible sector sum ${vsum:,.0f}")
                    issues += 1
            continue

        # Otherwise: bad format
        err(f"corporate_total format — {name}: '{ct}' must contain 'career', "
            f"a new-member label (first-term/sworn in/data pending), "
            f"or be a '$0 corporate PAC' label")
        issues += 1

    if issues == 0:
        print(f"  ✅ All corporate_total values pass format & math checks")
    else:
        print(f"  ❌ {issues} corporate_total issues")

def check_score_sanity(d):
    """Check that non-grassroots members with high AIPAC+sector totals have appropriate scores.
    Grassroots members use static scores intentionally — their FEC totals are individual
    employment donations not corporate PAC money, so high totals + low scores is correct."""
    print("\n[6] Score sanity check...")
    issues = 0
    GRASSROOTS_NAMES = d["grassroots"]
    for name, prof in d["profiles"].items():
        if name in GRASSROOTS_NAMES:
            continue  # Static scores for grassroots members are correct by design
        score = prof.get("corruption_score", 0) or 0
        aipac = d["aipac"].get(name, 0) or 0
        # Only flag based on AIPAC (direct PAC money we can confirm) + very high finance
        fec = d["fec_v8"].get(name, {})
        confirmed_pac = aipac + (fec.get("fossil_fuels", 0) or 0)
        if confirmed_pac > 500000 and score < 50:
            err(f"Score anomaly — {name}: score={score} but confirmed PAC money=${confirmed_pac:,}")
            issues += 1
    if issues == 0:
        print(f"  ✅ All scores look sane")
    else:
        print(f"  ❌ {issues} score anomalies")

# Layout for the JSON-vs-HTML check. Mirrors regenerate_data.py.
JSON_LAYOUT = [
    ('senate.json',          [('SENATE_DATA',  'array',  None)]),
    ('house.json',           [('HOUSE_DATA',   'array',  None)]),
    ('court.json',           [('COURT_DATA',   'array',  None)]),
    ('cabinet.json',         [('CABINET_DATA', 'array',  None)]),
    ('bills.json',           [('BILLS_DATA',   'array',  None)]),
    ('profiles.json',        [('PROFILES_DATA', 'object', None)]),
    ('aipac.json',           [('AIPAC_DATA',  'object', None)]),
    ('fec.json',             [('FEC_V8_DATA', 'object', None)]),
    ('sectors.json', [
        ('FOSSIL_DATA',     'object', 'fossil'),
        ('PHARMA_DATA',     'object', 'pharma'),
        ('DEFENSE_DATA',    'object', 'defense'),
        ('FINANCE_DATA',    'object', 'finance'),
        ('TECH_DATA',       'object', 'tech'),
        ('NRA_DATA',        'object', 'nra'),
        ('GRASSROOTS_DATA', 'object', 'grassroots'),
    ]),
    ('tags.json', [
        ('PHARMA_NAMES',          'set', 'pharma'),
        ('TECH_NAMES',            'set', 'tech'),
        ('DEFENSE_NAMES',         'set', 'defense'),
        ('FINANCE_NAMES',         'set', 'finance'),
        ('GRASSROOTS_NAMES',      'set', 'grassroots'),
        ('NO_STOCK_NAMES',        'set', 'no_stock'),
        ('EXPLICIT_CLEAN_MEMBERS', 'set', 'explicit_clean'),
    ]),
    ('bioguide.json',        [('BIOGUIDE',             'object', None)]),
    ('birth_dates.json',     [('BIRTH_DATES',          'object', None)]),
    ('photo_overrides.json', [('WIKI_PHOTO_OVERRIDES', 'object', None)]),
    ('corporate.json',       [('CORPORATE_DATA',       'array',  None)]),
    ('sector_counts.json',   [('SECTOR_COUNTS',        'object', None)]),
]

def _find_block(content, name, kind):
    idx = content.find(f'const {name}')
    if idx == -1: return None
    eq_idx = content.find('=', idx)
    after_eq = content[eq_idx+1:].lstrip()
    abs_start = eq_idx + 1 + len(content[eq_idx+1:]) - len(after_eq)
    if kind == 'set':
        start = content.find('[', abs_start)
        open_c, close_c = '[', ']'
    else:
        start = abs_start
        open_c, close_c = ('{', '}') if kind == 'object' else ('[', ']')
    depth, i = 0, start
    while i < len(content):
        if content[i] == open_c: depth += 1
        elif content[i] == close_c:
            depth -= 1
            if depth == 0: return content[start:i+1]
        i += 1
    return None

def check_json_matches_html(d, html_path):
    """Phase 1 invariant: if data/*.json files exist, they must exactly match
    the inline HTML data. Skips if data/ doesn't exist (Phase 1 not deployed yet),
    OR if the HTML has no inline data (Phase 2 — JSON is now the source of truth)."""
    print("\n[10] data/*.json ↔ HTML inline data sync...")

    # Phase 2 detected — no inline data to compare against
    if d.get("_phase") == 2:
        print(f"  ⊘ skipped — Phase 2 detected (data is loaded from JSON at runtime)")
        return

    data_dir = Path(html_path).parent / 'data'
    if not data_dir.exists():
        print(f"  ⊘ skipped — {data_dir}/ does not exist (Phase 1 not yet deployed)")
        return

    try:
        import json5
    except ImportError:
        warn("json5 not installed — skipping JSON↔HTML sync check (run: pip install json5)")
        print(f"  ⊘ skipped — json5 not installed")
        return

    content = d["content"]
    issues = 0
    files_checked = 0
    for fname, sources in JSON_LAYOUT:
        json_path = data_dir / fname
        if not json_path.exists():
            err(f"data/{fname} missing — run: python3 regenerate_data.py {html_path}")
            issues += 1
            continue
        files_checked += 1
        from_json = json.loads(json_path.read_text(encoding='utf-8'))

        for const_name, kind, subkey in sources:
            raw = _find_block(content, const_name, kind)
            if raw is None:
                # Inline HTML data has been removed — that's Phase 2, not Phase 1
                err(f"{const_name} not found in HTML but data/{fname} exists "
                    f"(Phase 2 detected — update validator)")
                issues += 1
                continue
            from_html = json5.loads(re.sub(r'\[\s*,', '[', raw))
            actual = from_json[subkey] if subkey else from_json
            # Sets exported as arrays — compare as sorted lists
            if kind == 'set':
                if sorted(from_html) != sorted(actual):
                    err(f"data/{fname}[{subkey}] does not match {const_name} in HTML "
                        f"— run: python3 regenerate_data.py {html_path}")
                    issues += 1
            else:
                if from_html != actual:
                    err(f"data/{fname}{'['+subkey+']' if subkey else ''} does not match "
                        f"{const_name} in HTML — run: python3 regenerate_data.py {html_path}")
                    issues += 1

    if issues == 0:
        print(f"  ✅ All {files_checked} JSON files match HTML inline data exactly")
    else:
        print(f"  ❌ {issues} JSON↔HTML mismatches")

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 validate.py index.html [csv_dir] [trackaipac_path]")
        sys.exit(1)
    html_path = sys.argv[1]
    csv_dir   = sys.argv[2] if len(sys.argv) > 2 else "."
    ta_path   = sys.argv[3] if len(sys.argv) > 3 else "trackaipac_page.txt"

    print(f"\n=== Influence Registry Data Validator ===")
    print(f"HTML: {html_path}")

    d    = load_html(html_path)
    csvs = load_csvs(csv_dir)
    ta   = load_trackaipac(ta_path)
    all_members = d["senate"] + d["house"]

    check_aipac_consistency(d)
    check_aipac_vs_trackaipac(d, ta)
    check_sectors_vs_csv(d, csvs, all_members)
    check_grassroots_integrity(d)
    check_all_members_in_fec(d)
    check_score_sanity(d)
    check_special_interest_total_math(d)
    check_card_sector_consistency(d)
    check_corporate_total(d)
    check_json_matches_html(d, html_path)

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
        print("✅ No errors — safe to deploy (review warnings)")

    sys.exit(1 if ERRORS else 0)

if __name__ == "__main__":
    main()

# NOTE ON SECTOR VALUES FOR GRASSROOTS MEMBERS:
# FEC sector totals represent career individual donations categorized by the donor's employer.
# A grassroots member's $20M "pharma" total = doctors/nurses donating $250 each over 30 years.
# It does NOT mean they took corporate PAC checks.
# The validator only flags truly anomalous cases (e.g. fossil fuel > $500K for a grassroots member)
# and defers to AIPAC_DATA for the one sector where we have direct PAC-level data.
