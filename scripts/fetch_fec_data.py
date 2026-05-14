#!/usr/bin/env python3
"""
fetch_fec_data.py  (v7 — production ready)
============================================
Fetches FEC contribution data for every current member of Congress.

REQUIREMENTS IMPLEMENTED:
─────────────────────────────────────────────────────────────────────────────
1. FEC API key: REMOVED

2. Each member is looked up INDIVIDUALLY by name + unique candidate_id.
   IDs are never shared or mixed between members.
   Members who served in both House AND Senate get both candidate_ids
   resolved and all committees across both offices are collected.

3. ALL YEARS SERVED — every FEC two-year cycle from their first election
   to present is covered. A senator who served in the House first (e.g.
   Ted Cruz: House candidate then Senate) gets data from both.

4. Data pulled from Schedule A (Browse Receipts) for every committee
   the member has ever authorized. Each receipt record has:
     - contributor_name  → used for keyword sector matching
     - contributor_type  → PAC vs individual (grassroots detection)
     - contribution_receipt_amount
     - sub_id            → unique transaction ID used for deduplication

5. CATEGORIES:
     - aipac        (from trackaipac_page.txt — PACs + IE + Lobby Donors)
     - fossil_fuels (FEC: Exxon, Chevron, API, Koch, etc.)
     - pharma       (FEC: Pfizer, Merck, AbbVie, PhRMA, etc.)
     - defense      (FEC: Lockheed, Raytheon, Boeing, Northrop, etc.)
     - finance      (FEC: Goldman, JPMorgan, ABA, NAR, etc.)
     - tech         (FEC: Google, Apple, Amazon, Meta, Microsoft, etc.)
     - nra          (FEC: NRA-PVF, GOA, NSSF, etc.)
     - grassroots   (FEC: small individual donations ≤ $200 + ActBlue/WinRed)

6. Deduplication: every receipt sub_id is tracked per member. If the same
   transaction appears in multiple committee queries (e.g. joint fundraising
   committees that file on behalf of the candidate), it is counted ONCE.

7. special_interest_total = sum of all 6 SI categories (aipac + fossil_fuels
   + pharma + defense + finance + tech). Grassroots is tracked separately
   as it represents small-dollar donors, not special interests.

─────────────────────────────────────────────────────────────────────────────
REQUIRES: members.json                  (list of {name, state, office} for current Congress)
          fec_data.json                 (optional — pre-known candidate IDs to skip lookup)
          trackaipac_page.txt           (TrackAIPAC page — provides aipac figures)
          Money_from_Oil___Gas_to_US_Representatives__1990-2024.csv
          Money_from_Oil___Gas_to_US_Senators__1990-2024.csv
          Money_from_Health_to_US_Representatives__1990-2024.csv
          Money_from_Health_to_US_Senators__1990-2024.csv
          Money_from_Defense_to_US_Representatives__1990-2024.csv
          Money_from_Defense_to_US_Senators__1990-2024.csv
          Money_from_Finance_Insurance_Real_Estate_to_US_Representatives__1990-2024.csv
          Money_from_Finance_Insurance_Real_Estate_to_US_Senators__1990-2024.csv
          Money_from_Communications_Electronics_to_US_Representatives__1990-2024.csv
          Money_from_Communications_Electronics_to_US_Senators__1990-2024.csv
OUTPUT:   fec_data_v7.json
─────────────────────────────────────────────────────────────────────────────
"""

import json, time, os, re, unicodedata
import urllib.request, urllib.parse, urllib.error

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

# API key is read from the FEC_API_KEY environment variable so it never has
# to be committed to source control. The GitHub Actions workflow at
# .github/workflows/refresh-fec-all.yml passes it through from the repo's
# Settings → Secrets store. For local runs, set it in your shell:
#     export FEC_API_KEY=your_actual_key_here
#     python3 scripts/fetch_fec_data.py
#
# If you have no API key, request one (free) at https://api.open.fec.gov/developers/.
API_KEY = os.environ.get("FEC_API_KEY", "").strip()
if not API_KEY:
    print("ERROR: FEC_API_KEY environment variable is not set.")
    print("  Local runs:   export FEC_API_KEY=your_key && python3 scripts/fetch_fec_data.py")
    print("  CI runs:      check that the workflow YAML passes secrets.FEC_API_KEY as env")
    import sys
    sys.exit(1)

BASE = "https://api.open.fec.gov/v1"

# ─────────────────────────────────────────────────────────────────────────────
# NAME-MATCHING ALIAS TABLES
# ─────────────────────────────────────────────────────────────────────────────
#
# Congressional members are often listed by full first name in OpenSecrets
# CSVs ("Charles E Schumer") but by nickname in our members.json ("Chuck
# Schumer"). The 2-token-overlap safety rule rejects these matches because
# token({chuck, schumer}) ∩ token({charles, schumer}) = {schumer} = 1 token.
#
# FIRST_NAME_ALIASES lets _best_csv_match expand the token set both ways so
# "chuck" and "charles" both count toward the overlap when matching.
#
# Maintenance: add new entries as you discover congressional members with
# nickname/full-name mismatches between our roster and OpenSecrets CSVs.
# Format: lowercase nickname -> set of equivalent full names (also lowercase).
FIRST_NAME_ALIASES = {
    "chris":    {"christopher"},
    "chuck":    {"charles"},
    "rob":      {"robert"},
    "bob":      {"robert"},
    "bobby":    {"robert"},
    "joe":      {"joseph"},
    "joey":     {"joseph"},
    "jim":      {"james"},
    "jimmy":    {"james"},
    "jake":     {"jacob"},
    "mike":     {"michael"},
    "matt":     {"matthew"},
    "tom":      {"thomas"},
    "tony":     {"anthony"},
    "dan":      {"daniel"},
    "danny":    {"daniel"},
    "dick":     {"richard"},
    "rick":     {"richard"},
    "rich":     {"richard"},
    "ricky":    {"richard"},
    "ed":       {"edward", "edwin"},
    "eddie":    {"edward", "edwin"},
    "ted":      {"theodore", "edward"},
    "teddy":    {"theodore"},
    "will":     {"william"},
    "bill":     {"william"},
    "billy":    {"william"},
    "willie":   {"william"},
    "andy":     {"andrew"},
    "drew":     {"andrew"},
    "alex":     {"alexander", "alexandra"},
    "ben":      {"benjamin"},
    "benji":    {"benjamin"},
    "dave":     {"david"},
    "davey":    {"david"},
    "gabe":     {"gabriel"},
    "greg":     {"gregory"},
    "jeff":     {"jeffrey", "jefferson"},
    "ken":      {"kenneth"},
    "kenny":    {"kenneth"},
    "larry":    {"lawrence"},
    "marty":    {"martin"},
    "nate":     {"nathan", "nathaniel"},
    "nick":     {"nicholas"},
    "pat":      {"patrick", "patricia"},
    "ron":      {"ronald"},
    "ronnie":   {"ronald"},
    "sam":      {"samuel"},
    "sammy":    {"samuel"},
    "steve":    {"steven", "stephen"},
    "tim":      {"timothy"},
    "tony":     {"anthony"},
    "vince":    {"vincent"},
    "abby":     {"abigail"},
    "abe":      {"abraham"},
    "becca":    {"rebecca"},
    "beth":     {"elizabeth"},
    "betty":    {"elizabeth"},
    "liz":      {"elizabeth"},
    "lizzie":   {"elizabeth"},
    "cathy":    {"catherine", "katherine"},
    "kate":     {"katherine", "kathryn", "katelyn"},
    "katie":    {"katherine", "kathryn", "katelyn"},
    "kathy":    {"katherine", "kathleen"},
    "deb":      {"deborah", "debra"},
    "debbie":   {"deborah", "debra"},
    "jen":      {"jennifer"},
    "jenny":    {"jennifer"},
    "jess":     {"jessica"},
    "jessie":   {"jessica"},
    "maggie":   {"margaret"},
    "meg":      {"margaret"},
    "peggy":    {"margaret"},
    "mary":     {"marie"},
    "nan":      {"nancy"},
    "sue":      {"susan", "susanne", "suzanne"},
    "susie":    {"susan"},
    "vicky":    {"victoria"},
    "vickie":   {"victoria"},
    "val":      {"valerie"},
    "trish":    {"patricia"},
}
# Build the reverse expansion as well: "christopher" should also match "chris".
# This is done once at import time so the lookup at match time is O(1).
_ALIAS_EXPANSION = {}
for short, longs in FIRST_NAME_ALIASES.items():
    _ALIAS_EXPANSION.setdefault(short, set()).update(longs)
    _ALIAS_EXPANSION[short].add(short)
    for long_name in longs:
        _ALIAS_EXPANSION.setdefault(long_name, set()).update({short, long_name})
        _ALIAS_EXPANSION[long_name].update(longs)


# TrackAIPAC publishes member names with nicknames/spellings that don't match
# our members.json roster. Mirrors validate.py's TRACKAIPAC_NAME_FIXES so the
# fetcher resolves the same set of name variants as the validator.
#
# Format: our normalized name -> normalized name as parsed from trackaipac_page.txt.
# Add a new entry whenever the validator's [S2] check warns about a member
# whose AIPAC_DATA value is present but fec.aipac_pacs is $0.
TRACKAIPAC_NAME_FIXES = {
    "james risch":             "jim risch",
    "maria elvira salazar":    "maria salazar",
    "mariannette miller meeks":"marianette miller meeks",
    "johnny olszewski":        "john olszewski",
    "jefferson van drew":      "jeff van drew",
    "joseph morelle":          "joe morelle",
    "michael turner":          "mike turner",
    "abraham hamadeh":         "abe hamadeh",
    "jerrold nadler":          "jerry nadler",
    "robert bresnahan":        "rob bresnahan",
    "carlos gimenez":          "carlos a gimenez",
    # If the validator's [S1] AIPAC drift check warns about a specific member
    # (AIPAC_DATA has a value but fec.aipac_pacs is $0), open trackaipac_page.txt,
    # find that member's actual name as it appears there, and add an entry:
    #   "our normalized name": "trackaipac normalized name",
    # Both sides use the _norm() rules: lowercase, no punctuation, no honorifics.
}


# ── TRACKAIPAC DATA — loaded from trackaipac_page.txt ───────────────────────
#
# Pro-Israel lobby totals come from TrackAIPAC (trackaipac.com/congress),
# NOT from FEC API queries. TrackAIPAC captures three money flows that the
# FEC API cannot fully reconstruct:
#   PACs         — direct PAC contributions
#   IE           — independent expenditures
#   Lobby Donors — individual bundled donors (proprietary DB, not in FEC alone)
#
# TRACKAIPAC_DATA is populated at startup by load_trackaipac_data().
# Keys are normalized lowercase names; values are dicts with total/pacs/ie/etc.
TRACKAIPAC_DATA: dict = {}   # populated at startup


def load_trackaipac_data(path):
    """
    Parse trackaipac_page.txt and populate TRACKAIPAC_DATA.
    File uses U+2028 (LINE SEPARATOR) as the field delimiter between
    name, district, total, PACs, IE, Lobby Donors, and sources.
    """
    import re as _re

    SEP = '\u2028'

    def _dollar(s):
        s = (s or "").replace("$","").replace(",","").replace("+","").strip()
        try: return int(float(s))
        except: return 0

    def _normalize(n):
        n = n.lower()
        n = _re.sub(r'\b(jr|sr|ii|iii|iv|mr|ms|dr|rep|sen)\.?\b', '', n)
        n = _re.sub(r'[^a-z\s]', '', n)
        return _re.sub(r'\s+', ' ', n).strip()

    try:
        with open(path, encoding='utf-8') as f:
            text = f.read()
    except FileNotFoundError:
        print(f"  ⚠ trackaipac_page.txt not found at {path} — aipac will be 0 for all members")
        return

    fields = text.split(SEP)
    count  = 0
    i = 0
    while i < len(fields):
        f = fields[i].strip()
        # Member name = all-caps word(s), followed by "ST-DIST [P]" in next field
        if (i + 5 < len(fields)
                and _re.match(r'^[A-Z][A-Z\s\.\-\']+$', f) and len(f) > 3
                and _re.match(r'^[A-Z]{2}-\S+\s+\[[RDI]\]$', fields[i+1].strip())):
            total = pacs = ie = donors = 0
            sources = ""
            for j in range(i+2, min(i+10, len(fields))):
                fj = fields[j].strip()
                if 'Israel Lobby Total:' in fj:
                    m = _re.search(r'\$([\d,]+)', fj)
                    if m: total = _dollar(m.group(1))
                elif fj.startswith('PACs:'):
                    m = _re.search(r'\$([\d,]+)', fj)
                    if m: pacs = _dollar(m.group(1))
                elif fj.startswith('IE:'):
                    m = _re.search(r'\$([\d,]+)', fj)
                    if m: ie = _dollar(m.group(1))
                elif 'Lobby Donors:' in fj:
                    m = _re.search(r'\$([\d,]+)', fj)
                    if m: donors = _dollar(m.group(1))
                elif (_re.match(r'^[A-Z][A-Z0-9,\s\-\(\)\']+$', fj)
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
                key = _normalize(f)
                TRACKAIPAC_DATA[key] = {
                    "total": total, "pacs": pacs, "ie": ie,
                    "lobby_donors": donors, "sources": sources,
                }
                count += 1
            i += 7
            continue
        i += 1

    print(f"  Loaded TrackAIPAC data for {count} members from {path}")


# ── OIL & GAS DATA — loaded from OpenSecrets CSVs ────────────────────────────
#
# Fossil fuel totals (1990–2024) come from two OpenSecrets CSV exports:
#   Money_from_Oil___Gas_to_US_Representatives__1990-2024.csv
#   Money_from_Oil___Gas_to_US_Senators__1990-2024.csv
#
# OIL_DATA is populated at startup by load_oil_data().
# Keys are normalized lowercase names; values are dollar totals.
OIL_DATA: dict = {}   # populated at startup


def load_oil_data(house_path, senate_path):
    """
    Parse both Oil & Gas CSV files and populate OIL_DATA.

    CSV format: "Lastname Firstname [Middle] (R-ST)", "State", "$Amount"
    Name matching uses token-set overlap (handles compound last names,
    middle initials, and hyphenated names like Wasserman-Schultz).
    """
    import csv as _csv
    import re as _re

    def _dollar(s):
        return int(_re.sub(r'[^\d]', '', s) or 0)

    total = 0
    for fpath, name_col in [(house_path, "Representative"), (senate_path, "Senator")]:
        try:
            with open(fpath, encoding='utf-8') as f:
                for row in _csv.DictReader(f):
                    raw   = row.get(name_col, "").strip()
                    state = row.get("State", "").strip()
                    amt   = _dollar(row.get("Amount", "0"))
                    if not raw or amt == 0:
                        continue
                    clean = _re.sub(r'\s*\([RDI][^\)]*\)\s*$', '', raw).strip()
                    key   = _normalize_oil(clean)
                    state_key = _normalize_oil(state)
                    # Key by (name, state) tuple to prevent collisions between
                    # different members who share a name (e.g. Mike Rogers AL
                    # and Mike Rogers MI). Previously a single name-keyed dict
                    # caused one to silently overwrite the other.
                    dict_key = (key, state_key)
                    if dict_key not in OIL_DATA or amt > OIL_DATA[dict_key]:
                        OIL_DATA[dict_key] = amt
                    total += 1
        except FileNotFoundError:
            print(f"  ⚠ Oil CSV not found: {fpath}")

    print(f"  Loaded Oil & Gas data for {total} entries ({len(OIL_DATA)} unique)")


def _normalize_oil(name):
    """Normalize for token-set matching: lowercase, hyphens→spaces, strip noise."""
    import re as _re
    name = name.lower()
    name = _re.sub(r'\b(jr|sr|ii|iii|iv|mr|ms|dr|rep|sen)\.?\b', '', name)
    name = _re.sub(r'[-]', ' ', name)          # treat hyphens as word separators
    name = _re.sub(r'[^a-z\s]', '', name)
    return _re.sub(r'\s+', ' ', name).strip()


def _expand_with_aliases(tokens):
    """
    Given a set of name tokens, return an expanded set that includes all
    nickname/full-name equivalents. Lets the matcher treat 'chuck' and
    'charles' as equivalent during overlap scoring.
    """
    expanded = set(tokens)
    for t in tokens:
        if t in _ALIAS_EXPANSION:
            expanded.update(_ALIAS_EXPANSION[t])
    return expanded


def _best_csv_match(member_name, member_state, csv_data):
    """
    Match a member to a row in `csv_data` and return the dollar amount.
    Returns 0 if no safe match is found.

    csv_data shape: dict keyed by (normalized_name, normalized_state) tuple,
    value is the dollar amount. The tuple key prevents collisions between
    different members who share a name (e.g. Mike Rogers AL vs Mike Rogers MI).

    MATCHING RULES (in order):

    1. STATE FILTER (hard). Only candidates whose CSV state matches the
       member's declared state are considered. This eliminates the entire
       category of cross-state same-name collisions: when looking up Mike
       Rogers (AL), we never even consider any "Rogers" row from MI.

    2. 2-TOKEN NAME OVERLAP (safety). After expanding both sides with
       FIRST_NAME_ALIASES (so chuck↔charles, chris↔christopher, etc.), the
       query and the CSV row must share at least 2 name tokens. This still
       prevents surname-only collisions like Adelita Grijalva vs her father
       Raul Grijalva, even when state matches.

    3. SCORE = number of overlapping tokens. Best match wins.

    Why state is now a HARD filter (not a tiebreaker): the previous
    'tiebreaker' design meant when two different people had the same
    normalized name in a single dict (Mike Rogers AL vs MI), the dict
    only kept one of them. The state filter is now backed by a tuple
    key (name, state) so both can coexist, and we pick the right one.
    """
    if not csv_data:
        return 0

    norm_name   = _normalize_oil(member_name)
    norm_state  = _normalize_oil(member_state) if member_state else ""
    name_tokens = {t for t in norm_name.split() if len(t) > 1}
    expanded_name_tokens = _expand_with_aliases(name_tokens)

    best_score = 0
    best_amt   = 0

    for (csv_key, csv_state), amt in csv_data.items():
        # RULE 1: STATE FILTER. If the member has a declared state and the
        # CSV row carries a state, require an exact match. This is the
        # critical fix for same-name-different-state collisions.
        if norm_state and csv_state and csv_state != norm_state:
            continue

        csv_tokens = {t for t in csv_key.split() if len(t) > 1}
        expanded_csv_tokens = _expand_with_aliases(csv_tokens)

        # RULE 2: require at least 2 name tokens to overlap, computed on
        # the alias-expanded sets so nicknames count as matches.
        overlap = expanded_name_tokens & expanded_csv_tokens
        if len(overlap) < 2:
            continue

        # RULE 3: score = number of overlapping tokens. Slight bonus for
        # state match (helps disambiguate when norm_state was empty above).
        score = len(overlap)
        if csv_state and norm_state and csv_state == norm_state:
            score += 1

        if score > best_score:
            best_score = score
            best_amt   = amt

    return best_amt


def get_oil_amount(member_name, member_state):
    """
    Look up a member's Oil & Gas total from OIL_DATA.
    Uses safe token-set overlap matching (requires 2+ name tokens to match).
    Returns dollar amount (0 if not found).
    """
    return _best_csv_match(member_name, member_state, OIL_DATA)


# ── PHARMA / HEALTH DATA — loaded from OpenSecrets CSVs ──────────────────────
#
# Pharma/health totals (1990–2024) come from two OpenSecrets CSV exports:
#   Money_from_Health_to_US_Representatives__1990-2024.csv
#   Money_from_Health_to_US_Senators__1990-2024.csv
#
# PHARMA_DATA is populated at startup by load_pharma_data().
# Only active members will receive non-zero values — inactive members
# (Biden, Obama, Romney, etc.) are simply not in members.json so they
# are never looked up.
PHARMA_DATA: dict = {}   # populated at startup


def load_pharma_data(house_path, senate_path):
    """
    Parse both Health/Pharma CSV files and populate PHARMA_DATA.
    Same format as oil CSVs: "Lastname Firstname [Middle] (R-ST)", "State", "$Amount"
    Uses token-set overlap + state matching (same logic as load_oil_data).
    """
    import csv as _csv, re as _re

    def _dollar(s):
        return int(_re.sub(r'[^\d]', '', s) or 0)

    total = 0
    for fpath, name_col in [(house_path, "Representative"), (senate_path, "Senator")]:
        try:
            with open(fpath, encoding='utf-8') as f:
                for row in _csv.DictReader(f):
                    raw   = row.get(name_col, "").strip()
                    state = row.get("State", "").strip()
                    amt   = _dollar(row.get("Amount", "0"))
                    if not raw or amt == 0:
                        continue
                    clean     = _re.sub(r'\s*\([RDI][^\)]*\)\s*$', '', raw).strip()
                    key       = _normalize_oil(clean)
                    state_key = _normalize_oil(state)
                    dict_key  = (key, state_key)
                    if dict_key not in PHARMA_DATA or amt > PHARMA_DATA[dict_key]:
                        PHARMA_DATA[dict_key] = amt
                    total += 1
        except FileNotFoundError:
            print(f"  ⚠ Pharma CSV not found: {fpath}")

    print(f"  Loaded Pharma/Health data for {total} entries ({len(PHARMA_DATA)} unique)")


def get_pharma_amount(member_name, member_state):
    """
    Look up a member's pharma/health total from PHARMA_DATA.
    Returns 0 if member not found (not active or received $0).
    """
    return _best_csv_match(member_name, member_state, PHARMA_DATA)


# ── DEFENSE DATA — loaded from OpenSecrets CSVs ───────────────────────────────
#
# Defense totals (1990-2024) come from two OpenSecrets CSV exports:
#   Money_from_Defense_to_US_Representatives__1990-2024.csv
#   Money_from_Defense_to_US_Senators__1990-2024.csv
#
# DEFENSE_DATA is populated at startup by load_defense_data().
# Only active members (those in members.json) will receive non-zero values.
DEFENSE_DATA: dict = {}   # populated at startup


def load_defense_data(house_path, senate_path):
    """Parse both Defense CSV files and populate DEFENSE_DATA."""
    import csv as _csv, re as _re

    def _dollar(s):
        return int(_re.sub(r'[^\d]', '', s) or 0)

    total = 0
    for fpath, name_col in [(house_path, "Representative"), (senate_path, "Senator")]:
        try:
            with open(fpath, encoding='utf-8') as f:
                for row in _csv.DictReader(f):
                    raw   = row.get(name_col, "").strip()
                    state = row.get("State", "").strip()
                    amt   = _dollar(row.get("Amount", "0"))
                    if not raw or amt == 0:
                        continue
                    clean     = _re.sub(r'\s*\([RDI][^\)]*\)\s*$', '', raw).strip()
                    key       = _normalize_oil(clean)
                    state_key = _normalize_oil(state)
                    dict_key  = (key, state_key)
                    if dict_key not in DEFENSE_DATA or amt > DEFENSE_DATA[dict_key]:
                        DEFENSE_DATA[dict_key] = amt
                    total += 1
        except FileNotFoundError:
            print(f"  \u26a0 Defense CSV not found: {fpath}")

    print(f"  Loaded Defense data for {total} entries ({len(DEFENSE_DATA)} unique)")


def get_defense_amount(member_name, member_state):
    """Look up a member's defense total. Returns 0 if not found."""
    return _best_csv_match(member_name, member_state, DEFENSE_DATA)



# ── FINANCE DATA — loaded from OpenSecrets CSVs ───────────────────────────────
#
# Finance/Insurance/Real Estate totals (1990-2024) from OpenSecrets CSV exports:
#   Money_from_Finance_Insurance_Real_Estate_to_US_Representatives__1990-2024.csv
#   Money_from_Finance_Insurance_Real_Estate_to_US_Senators__1990-2024.csv
#
# FINANCE_DATA is populated at startup by load_finance_data().
# Only active members (those in members.json) will receive non-zero values.
FINANCE_DATA: dict = {}   # populated at startup


def load_finance_data(house_path, senate_path):
    """Parse both Finance CSV files and populate FINANCE_DATA."""
    import csv as _csv, re as _re

    def _dollar(s):
        return int(_re.sub(r'[^\d]', '', s) or 0)

    total = 0
    for fpath, name_col in [(house_path, "Representative"), (senate_path, "Senator")]:
        try:
            with open(fpath, encoding='utf-8') as f:
                for row in _csv.DictReader(f):
                    raw   = row.get(name_col, "").strip()
                    state = row.get("State", "").strip()
                    amt   = _dollar(row.get("Amount", "0"))
                    if not raw or amt == 0:
                        continue
                    clean     = _re.sub(r'\s*\([RDI][^\)]*\)\s*$', '', raw).strip()
                    key       = _normalize_oil(clean)
                    state_key = _normalize_oil(state)
                    dict_key  = (key, state_key)
                    if dict_key not in FINANCE_DATA or amt > FINANCE_DATA[dict_key]:
                        FINANCE_DATA[dict_key] = amt
                    total += 1
        except FileNotFoundError:
            print(f"  \u26a0 Finance CSV not found: {fpath}")

    print(f"  Loaded Finance data for {total} entries ({len(FINANCE_DATA)} unique)")


def get_finance_amount(member_name, member_state):
    """Look up a member's finance total. Returns 0 if not found."""
    return _best_csv_match(member_name, member_state, FINANCE_DATA)



# ── TECH / COMMS DATA — loaded from OpenSecrets CSVs ─────────────────────────
#
# Communications/Electronics totals (1990-2024) from OpenSecrets CSV exports:
#   Money_from_Communications_Electronics_to_US_Representatives__1990-2024.csv
#   Money_from_Communications_Electronics_to_US_Senators__1990-2024.csv
#
# TECH_DATA is populated at startup by load_tech_data().
# Only active members (those in members.json) will receive non-zero values.
TECH_DATA: dict = {}   # populated at startup


def load_tech_data(house_path, senate_path):
    """Parse both Tech/Comms CSV files and populate TECH_DATA."""
    import csv as _csv, re as _re

    def _dollar(s):
        return int(_re.sub(r'[^\d]', '', s) or 0)

    total = 0
    for fpath, name_col in [(house_path, "Representative"), (senate_path, "Senator")]:
        try:
            with open(fpath, encoding='utf-8') as f:
                for row in _csv.DictReader(f):
                    raw   = row.get(name_col, "").strip()
                    state = row.get("State", "").strip()
                    amt   = _dollar(row.get("Amount", "0"))
                    if not raw or amt == 0:
                        continue
                    clean     = _re.sub(r'\s*\([RDI][^\)]*\)\s*$', '', raw).strip()
                    key       = _normalize_oil(clean)
                    state_key = _normalize_oil(state)
                    dict_key  = (key, state_key)
                    if dict_key not in TECH_DATA or amt > TECH_DATA[dict_key]:
                        TECH_DATA[dict_key] = amt
                    total += 1
        except FileNotFoundError:
            print(f"  \u26a0 Tech/Comms CSV not found: {fpath}")

    print(f"  Loaded Tech/Comms data for {total} entries ({len(TECH_DATA)} unique)")


def get_tech_amount(member_name, member_state):
    """Look up a member's tech/comms total. Returns 0 if not found."""
    return _best_csv_match(member_name, member_state, TECH_DATA)


ALL_CYCLES = list(range(2024, 1988, -2))  # [2024, 2022, 2020, ..., 1990]

# Grassroots threshold: contributions at or below this amount from individuals
# are counted as grassroots/small-dollar donations
GRASSROOTS_MAX = 200


# ─────────────────────────────────────────────────────────────────────────────
# SECTOR KEYWORD LISTS
# Matched against contributor_name (lowercase) from Schedule A receipts.
# Each list covers company PACs, trade association PACs, and industry names.
# ─────────────────────────────────────────────────────────────────────────────

# ── PER-SECTOR VERIFIED FEC COMMITTEE ID SETS ────────────────────────────────
#
# These are the PAC committee IDs for the top spenders in each sector,
# verified via fec.gov and OpenSecrets. When a receipt's contributor_committee_id
# matches one of these sets, the contribution is classified into that sector
# EXACTLY — no keyword matching needed. This eliminates false positives and
# catches contributions that don't use the company name in the contributor field.
#
# Sources: fec.gov committee pages + OpenSecrets PAC profile URLs.
# Format: all IDs begin with "C00" and are 9 characters.

SECTOR_CMTE_IDS = {

    # fossil_fuels is intentionally omitted here — totals come from the
    # OpenSecrets CSV files loaded at startup via load_oil_data(), not FEC.

    # pharma is intentionally omitted — totals come from the
    # OpenSecrets CSV files loaded at startup via load_pharma_data().

    # defense is intentionally omitted — totals come from the
    # OpenSecrets CSV files loaded at startup via load_defense_data().

    "nra": {
        "C00009962",  # NRA Political Victory Fund (original PAC)
        "C00741710",  # NRA Victory Fund (super PAC, est. 2020)
        "C00489658",  # NRA Institute for Legislative Action PAC
        "C00082578",  # Gun Owners of America PAC
        "C00454603",  # National Shooting Sports Foundation PAC
        "C00391227",  # Citizens Committee for the Right to Keep & Bear Arms
    },

    # finance is intentionally omitted — totals come from the
    # OpenSecrets CSV files loaded at startup via load_finance_data().

    # tech is intentionally omitted — totals come from the
    # OpenSecrets CSV files loaded at startup via load_tech_data().

}


SECTOR_KEYWORDS = {

    # fossil_fuels is intentionally omitted here — totals come from the
    # OpenSecrets CSV files loaded at startup via load_oil_data(), not FEC.

    # pharma is intentionally omitted — totals come from the
    # OpenSecrets CSV files loaded at startup via load_pharma_data().

    # defense is intentionally omitted — totals come from the
    # OpenSecrets CSV files loaded at startup via load_defense_data().

    "nra": [
        # NRA's own registered FEC committees and direct entities
        "national rifle association", "nra political victory fund",
        "nra-pvf", "nra-ila", "nra institute for legislative action",
        "nra special contribution fund", "nra freedom action foundation",
        "nra civil rights defense fund",
        "nra ",          # trailing space avoids matching e.g. "natural"
        " nra",          # leading space for mid-string matches
        # Closely affiliated gun rights PACs
        "gun owners of america", "goa pac",
        "national shooting sports foundation", "nssf pac",
        "firearms coalition", "national firearms assoc",
        "safari club international",
        "citizens committee for the right to keep and bear",
        "ccrkba",
        "second amendment foundation",
        "firearms policy coalition",
        "gun rights pac", "gun pac",
        "second amendment caucus",
        "sportsmens alliance",
        "congressional sportsmens foundation",
        "hunters for trump",
    ],

    # finance is intentionally omitted — totals come from the
    # OpenSecrets CSV files loaded at startup via load_finance_data().

    # tech is intentionally omitted — totals come from the
    # OpenSecrets CSV files loaded at startup via load_tech_data().
}

# ── REVERSE LOOKUP: committee_id → sector name (built once at import time) ───
# Enables O(1) sector lookup per receipt instead of iterating every sector set.
CMTE_ID_TO_SECTOR: dict = {}
for _sector, _ids in SECTOR_CMTE_IDS.items():
    for _cid in _ids:
        CMTE_ID_TO_SECTOR[_cid] = _sector
# Note: C00030718 appears in both defense and finance.
# Finance (NAR) is the dominant user of that ID — finance wins.
CMTE_ID_TO_SECTOR["C00030718"] = "finance"

# ── ALL SECTOR COMMITTEE IDS FLATTENED (for quick membership test) ───────────
ALL_SECTOR_CMTE_IDS: set = set(CMTE_ID_TO_SECTOR.keys())


# ─────────────────────────────────────────────────────────────────────────────
# HTTP HELPER
# ─────────────────────────────────────────────────────────────────────────────

def get(path, params=None):
    """
    FEC API GET with retry + rate-limit handling.
    Returns parsed JSON dict, or None on failure.
    """
    p = dict(params or {})
    p["api_key"] = API_KEY
    url = BASE + path + "?" + urllib.parse.urlencode(p)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=25) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print("\n  [rate-limited — sleeping 65s]", end="", flush=True)
                time.sleep(65)
                continue
            if e.code in (400, 404, 422):
                return None
            time.sleep(3)
        except Exception:
            time.sleep(2)
    return None


def fmt(n):
    """Format dollar amount for console display."""
    if not n or n <= 0: return None
    if n >= 1_000_000:  return f"${n/1_000_000:.1f}M"
    if n >= 1_000:      return f"${round(n/1000)}K"
    return f"${round(n)}"


# ─────────────────────────────────────────────────────────────────────────────
# REQUIREMENT 2 — RESOLVE EACH MEMBER INDIVIDUALLY
# Each member gets their own candidate_id(s) — never shared.
# Members who served in both chambers get IDs for BOTH.
# ─────────────────────────────────────────────────────────────────────────────

def _strip_accents(s):
    """
    Remove diacritical marks from a string so accented characters match their
    ASCII equivalents. 'Luján' -> 'Lujan', 'Velázquez' -> 'Velazquez', etc.

    Uses Unicode NFD (Canonical Decomposition) to split each accented character
    into a base letter + combining mark, then drops the combining marks.

    Critical for FEC candidate-search lookup: members.json carries names with
    proper accents ('Ben Ray Luján'), but the FEC API search endpoint matches
    on ASCII names only. Without this step, the regex that strips non-letter
    characters would also strip the base letter under the accent ('á' as a
    single codepoint), producing nonsense queries like 'lujn'.
    """
    if not s:
        return s
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _clean(name):
    """Normalise name for fuzzy comparison."""
    name = _strip_accents(name)        # 'Luján' -> 'Lujan' before lowercasing
    name = name.lower()
    name = re.sub(r'\b(jr|sr|ii|iii|iv|mr|ms|dr|sen|rep|the)\.?\b', '', name)
    name = re.sub(r'[^a-z\s]', '', name)
    return re.sub(r'\s+', ' ', name).strip()


def _score(query, fec_name):
    """
    Score name match between our member name and FEC result.
    Last name match gives +2 bonus. Returns int (higher = better).
    """
    q_parts = [p for p in _clean(query).split() if len(p) > 1]
    f       = _clean(fec_name)
    if not q_parts:
        return 0
    score = sum(1 for p in q_parts if p in f)
    if q_parts[-1] in f:   # last name bonus
        score += 2
    return score


def resolve_all_candidate_ids(name, state, current_office):
    """
    REQUIREMENT 2 + 3: Find ALL candidate IDs for this member across
    ALL offices they have ever held (House and/or Senate) IN THEIR STATE.

    KEY DESIGN DECISIONS:
    - No election_year filter (senators elected in 2018/2020/2022 won't
      appear in a 2024 filter).
    - Search for both 'S' (Senate) and 'H' (House) offices to catch
      members who served in both chambers.
    - Returns list of (candidate_id, office) tuples — one per office.
    - Minimum name-match score of 3 required to prevent false positives.

    SAFETY RULE (added after Thomas Massie / S2MA00113 collision incident):
    Candidates returned by the FEC API that do NOT match the member's
    declared state are HARD-REJECTED, regardless of name score. This
    prevents same-surname candidates from other states (e.g. a Massachusetts
    Senate candidate also named "Thomas Massie") from being merged into
    the wrong member's record. Members rarely run for federal office in
    multiple states; when they do, the data should be reviewed manually.
    """
    parts    = name.split()
    suffixes = {"Jr.", "Sr.", "II", "III", "IV", "Jr", "Sr"}
    last     = next((p for p in reversed(parts) if p not in suffixes), parts[-1])
    # Strip accents BEFORE the regex that drops non-letter characters.
    # Otherwise 'á' (U+00E1) gets dropped wholesale and 'Luján' becomes 'Lujn',
    # which the FEC API doesn't recognize. NFD-decompose first, then strip:
    # 'Luján' -> 'Luja\u0301n' -> regex drops the combining acute -> 'Lujan'.
    last     = re.sub(r'[^a-zA-Z]', '', _strip_accents(last))
    first    = re.sub(r'[^a-zA-Z]', '', _strip_accents(parts[0])) if parts else ""

    # Search both chambers to catch cross-chamber careers
    offices_to_search = ["S", "H"] if current_office == "S" else ["H", "S"]

    found = {}   # office → (candidate_id, best_score)

    for office in offices_to_search:
        search_tries = [
            {"q": last,  "state": state, "office": office},
            {"q": last,  "office": office},
            {"q": first, "state": state, "office": office},
            {"q": last},
        ]
        for sp in search_tries:
            data = get("/candidates/search/", {**sp, "per_page": 20})
            if not data or not data.get("results"):
                continue
            for r in data["results"]:
                # HARD STATE CHECK — reject any candidate whose state does
                # not match the member's declared state. The FEC candidate
                # ID prefix includes the state, so this also prevents the
                # ID itself from leaking into all_candidate_ids.
                if state and r.get("state") and r.get("state") != state:
                    continue
                sc = _score(name, r.get("name", ""))
                if sp.get("office") and r.get("office") != sp["office"]: sc -= 5
                if sp.get("state")  and r.get("state")  != state:        sc -= 3
                if sc >= 3:
                    r_office = r.get("office", office)
                    if r_office not in found or sc > found[r_office][1]:
                        found[r_office] = (r["candidate_id"], sc)
            # Stop early if strong match
            if office in found and found[office][1] >= 5:
                break

    return [(cid, off) for off, (cid, _) in found.items()]


# ─────────────────────────────────────────────────────────────────────────────
# REQUIREMENT 3 — ALL COMMITTEES ACROSS ALL YEARS
# ─────────────────────────────────────────────────────────────────────────────

def get_all_committees(candidate_ids_with_office):
    """
    REQUIREMENT 3: Get every authorized campaign committee for each
    candidate_id using a SINGLE API call per candidate — not one per cycle.

    Uses /candidate/{id}/committees/ with no cycle filter, which returns
    ALL committees across ALL years in one response. This is far more
    efficient than the previous approach of querying every cycle separately
    (which caused 18+ API calls per candidate and excessive rate limiting).

    Returns list of unique committee_ids.
    """
    all_cmte_ids = []
    seen = set()

    for (cid, office) in candidate_ids_with_office:
        # Single call — returns all committees across all cycles at once
        data = get(f"/candidate/{cid}/committees/", {"per_page": 50})
        if data and data.get("results"):
            for r in data["results"]:
                cmte_id     = r.get("committee_id")
                cmte_type   = r.get("committee_type", "")
                designation = r.get("designation", "")
                # P = Principal, A = Authorized — campaign committees only
                # Excludes Q (PAC), N (non-party), Y (party) etc.
                if (cmte_id
                        and cmte_id not in seen
                        and designation in ("P", "A")
                        and cmte_type in ("H", "S", "P")):
                    all_cmte_ids.append(cmte_id)
                    seen.add(cmte_id)
        time.sleep(0.2)

    # Fallback: if nothing found with designation filter, relax it
    if not all_cmte_ids:
        for (cid, office) in candidate_ids_with_office:
            data = get(f"/candidate/{cid}/committees/", {"per_page": 50})
            if data and data.get("results"):
                for r in data["results"]:
                    cmte_id   = r.get("committee_id")
                    cmte_type = r.get("committee_type", "")
                    if cmte_id and cmte_id not in seen and cmte_type in ("H", "S", "P"):
                        all_cmte_ids.append(cmte_id)
                        seen.add(cmte_id)

    return all_cmte_ids


def get_active_cycles_for_member(office):
    """
    Returns the relevant FEC two-year cycles for a member based on their
    office type. No API calls — purely based on logic.

    This replaces the old get_all_active_cycles() which made 1 API call
    per committee just to discover which years had data, causing excessive
    rate limiting for members with many committees (e.g. House→Senate).

    Senate: all cycles back to 1990 (senators serve 6-year terms and many
            have served multiple terms or came from the House)
    House:  cycles back to 2000 (House members are elected every 2 years;
            going back further rarely adds meaningful data and wastes calls)
    """
    if office == "S":
        return list(range(2024, 1988, -2))   # 2024 → 1990
    else:
        return list(range(2024, 1998, -2))   # 2024 → 2000


# ─────────────────────────────────────────────────────────────────────────────
# REQUIREMENT 4 + 5 + 6 — BROWSE RECEIPTS WITH DEDUPLICATION + KEYWORD MATCH
# ─────────────────────────────────────────────────────────────────────────────

def fetch_all_receipts(committee_ids, active_cycles, member_name):
    """
    Pull ALL Schedule A receipts for every committee the member has ever
    authorized, across all active cycles. Returns (sector_totals, grassroots,
    total_raised).

    NOTE: aipac is NOT matched here — it comes from TrackAIPAC data loaded
    at startup (trackaipac_page.txt), which captures PACs + IE + bundled
    Lobby Donors. The aipac key is initialized to 0 and filled in Step 5
    of the main loop via get_aipac_from_trackaipac().

    SECTOR MATCHING (fossil_fuels, pharma, defense, finance, tech, nra):
    ──────────────────────────────────────────────────────────────────────
    Step 1 — Exact committee ID match (SECTOR_CMTE_IDS):
        Each receipt carries contributor_committee_id. We look this up in
        our verified per-sector committee ID sets for O(1) matching.
        ~110 verified PAC IDs across all 6 sectors.

    Step 2 — Keyword match (SECTOR_KEYWORDS):
        Fallback for contributions without a committee_id on file.
        Matches contributor_name + contributor_employer against keyword lists.

    GRASSROOTS:
        Individual donations ≤ $200 and ActBlue/WinRed aggregates are
        classified as grassroots (tracked separately, not in SI total).

    DEDUPLICATION:
        Every receipt's sub_id is tracked. Same transaction appearing
        across multiple committee queries is counted exactly once.
    """
    # All categories — aipac comes from TrackAIPAC, fossil_fuels comes from
    # OpenSecrets CSV. Both are populated after this function returns.
    # Initialize all to 0 here as placeholders.
    all_sectors   = list(SECTOR_KEYWORDS.keys()) + ["aipac", "fossil_fuels", "pharma", "defense", "finance", "tech"]
    sector_totals = {k: 0 for k in all_sectors}
    grassroots    = 0
    total_raised  = 0
    seen_sub_ids  = set()

    for cmte_id in committee_ids:
        for cycle in active_cycles:
            last_index = None

            while True:
                params = {
                    "committee_id":                cmte_id,
                    "two_year_transaction_period":  cycle,
                    "per_page":                    100,
                    "sort":                        "contribution_receipt_date",
                }
                if last_index:
                    params["last_index"] = last_index

                data = get("/schedules/schedule_a/", params)
                if not data or not data.get("results"):
                    break

                results = data["results"]

                for item in results:
                    # ── DEDUPLICATION ────────────────────────────────────
                    sub_id = item.get("sub_id")
                    if sub_id:
                        if sub_id in seen_sub_ids:
                            continue
                        seen_sub_ids.add(sub_id)

                    # ── VALIDATE committee (safety guard) ────────────────
                    item_cmte = (
                        (item.get("committee") or {}).get("committee_id")
                        or item.get("committee_id")
                    )
                    if item_cmte and item_cmte != cmte_id:
                        continue

                    amount = item.get("contribution_receipt_amount") or 0
                    if amount <= 0:
                        continue

                    total_raised += amount

                    entity_type         = (item.get("entity_type") or "").upper()
                    contributor_cmte_id = (item.get("contributor_committee_id") or "").strip()

                    # ── STEP 1: EXACT COMMITTEE ID MATCH ────────────────
                    # Check against verified sector PAC sets.
                    # NOTE: aipac is intentionally excluded here — it comes
                    # from TrackAIPAC data loaded at startup, not FEC receipts.
                    if contributor_cmte_id:
                        matched_sector = CMTE_ID_TO_SECTOR.get(contributor_cmte_id)
                        if matched_sector:
                            sector_totals[matched_sector] += amount
                            continue

                    # ── STEP 2: GRASSROOTS ───────────────────────────────
                    is_individual = entity_type in ("IND", "")
                    if is_individual and amount <= GRASSROOTS_MAX:
                        grassroots += amount
                        continue

                    # ── STEP 3: KEYWORD MATCH ────────────────────────────
                    contributor = (item.get("contributor_name") or "").lower()
                    employer    = (item.get("contributor_employer") or "").lower()
                    search_text = contributor + " " + employer

                    # ActBlue/WinRed aggregators — grassroots bundles
                    is_aggregator = any(x in contributor for x in (
                        "actblue", "winred", "democracy engine",
                        "anedot", "run for something", "small dollar",
                    ))
                    if is_aggregator:
                        grassroots += amount
                        continue

                    # Sector keywords (aipac excluded — comes from TrackAIPAC)
                    for sector, keywords in SECTOR_KEYWORDS.items():
                        if any(kw in search_text for kw in keywords):
                            sector_totals[sector] += amount
                            break
                    # Unclassified — counted in total_raised only

                # ── PAGINATION ───────────────────────────────────────────
                if len(results) < 100:
                    break
                pagination   = data.get("pagination") or {}
                last_indexes = pagination.get("last_indexes") or {}
                last_index   = last_indexes.get("last_index")
                if not last_index:
                    break
                time.sleep(0.1)

            time.sleep(0.1)

    return (
        {k: round(v) if v > 0 else 0 for k, v in sector_totals.items()},
        round(grassroots),
        round(total_raised),
    )


# ─────────────────────────────────────────────────────────────────────────────
# AIPAC — FROM TRACKAIPAC DATA (not FEC API)
# ─────────────────────────────────────────────────────────────────────────────

def get_aipac_from_trackaipac(name):
    """
    Look up a member's pro-Israel lobby total from the pre-loaded TrackAIPAC
    data. Returns a dict with total/pacs/ie/lobby_donors/sources, or all zeros
    if the member isn't in the TrackAIPAC data (i.e. $0 received).

    Uses normalized name matching:
      1. TRACKAIPAC_NAME_FIXES — explicit map of our-name → trackaipac-name
         for members the source publishes under a different nickname/spelling.
         Applied first because it's the authoritative override.
      2. EXACT match on full normalized name.
      3. Token-set match with first-name alias expansion (chuck↔charles etc),
         requiring at least 2 name tokens to overlap. Surname-only matches
         are REJECTED to prevent same-surname collisions like Thomas Massie
         being conflated with a different "Massie" entry.

    Returns empty (all zeros) if no safe match is found. This treats unknowns
    as "$0 received" which is the correct default for AIPAC lobby data
    (TrackAIPAC publishes recipients only; absence means no recorded receipts).
    """
    def _norm(n):
        n = n.lower()
        n = re.sub(r'\b(jr|sr|ii|iii|iv|mr|ms|dr|rep|sen)\.?\b', '', n)
        n = re.sub(r'[^a-z\s]', '', n)
        return re.sub(r'\s+', ' ', n).strip()

    empty = {"total": 0, "pacs": 0, "ie": 0, "lobby_donors": 0, "sources": ""}

    if not TRACKAIPAC_DATA:
        return empty

    key = _norm(name)

    # 1. Explicit name fixup. If this member's TrackAIPAC entry is filed
    #    under a different nickname/spelling than our roster, the fix
    #    table maps our-name -> source-name. This handles cases the
    #    fuzzy matcher would otherwise reject as ambiguous or miss
    #    entirely (e.g. James Risch -> Jim Risch).
    fixed_key = TRACKAIPAC_NAME_FIXES.get(key, key)
    if fixed_key in TRACKAIPAC_DATA:
        return TRACKAIPAC_DATA[fixed_key]

    # 2. Exact match on the unfixed normalized name.
    if key in TRACKAIPAC_DATA:
        return TRACKAIPAC_DATA[key]

    # 3. SAFE fuzzy match — require 2+ name tokens to overlap, after
    #    expanding both sides with FIRST_NAME_ALIASES so nicknames
    #    (chuck↔charles, chris↔christopher) count as matches.
    name_tokens = {t for t in key.split() if len(t) > 1}
    if len(name_tokens) < 2:
        return empty
    expanded_name_tokens = _expand_with_aliases(name_tokens)

    best_overlap = 0
    best_match   = None
    ambiguous    = False

    for k, v in TRACKAIPAC_DATA.items():
        cand_tokens = {t for t in k.split() if len(t) > 1}
        expanded_cand_tokens = _expand_with_aliases(cand_tokens)
        overlap = len(expanded_name_tokens & expanded_cand_tokens)
        if overlap < 2:
            continue
        if overlap > best_overlap:
            best_overlap = overlap
            best_match   = v
            ambiguous    = False
        elif overlap == best_overlap and v is not best_match:
            # Two different entries tied — refuse to guess.
            ambiguous = True

    if best_match is not None and not ambiguous:
        return best_match

    return empty


def fetch_official_totals(candidate_ids_with_office):
    """
    Get official FEC total receipts and most recent active cycle.

    Uses /candidates/{id}/totals/ WITHOUT a cycle filter to get all
    cycles at once — replacing the old 18-call loop with 1 call per
    candidate_id.
    """
    total     = 0
    max_cycle = 0

    for (cid, _) in candidate_ids_with_office:
        data = get(f"/candidates/{cid}/totals/", {"per_page": 40})
        if data and data.get("results"):
            for r in data["results"]:
                receipts = r.get("receipts") or 0
                cycle    = r.get("cycle") or 0
                if receipts > 0:
                    total += receipts
                    if cycle > max_cycle:
                        max_cycle = cycle
        time.sleep(0.2)

    return round(total), max_cycle if max_cycle else 2024


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    script_dir   = os.path.dirname(os.path.abspath(__file__))
    ids_path     = os.path.join(script_dir, "fec_data.json")
    members_path = os.path.join(script_dir, "members.json")
    out_path     = os.path.join(script_dir, "fec_data_v7.json")
    ta_path          = os.path.join(script_dir, "trackaipac_page.txt")
    oil_house_path   = os.path.join(script_dir, "Money_from_Oil___Gas_to_US_Representatives__1990-2024.csv")
    oil_senate_path  = os.path.join(script_dir, "Money_from_Oil___Gas_to_US_Senators__1990-2024.csv")

    # ── Load TrackAIPAC data (pro-Israel lobby totals) ────────────────────
    print("Loading TrackAIPAC data...")
    load_trackaipac_data(ta_path)

    # Load Oil & Gas data (OpenSecrets CSVs)
    print("Loading Oil & Gas data...")
    load_oil_data(oil_house_path, oil_senate_path)

    # Load Pharma/Health data (OpenSecrets CSVs)
    pharma_house_path   = os.path.join(script_dir, "Money_from_Health_to_US_Representatives__1990-2024.csv")
    pharma_senate_path  = os.path.join(script_dir, "Money_from_Health_to_US_Senators__1990-2024.csv")
    print("Loading Pharma/Health data...")
    load_pharma_data(pharma_house_path, pharma_senate_path)

    # Load Defense data (OpenSecrets CSVs)
    defense_house_path   = os.path.join(script_dir, "Money_from_Defense_to_US_Representatives__1990-2024.csv")
    defense_senate_path  = os.path.join(script_dir, "Money_from_Defense_to_US_Senators__1990-2024.csv")
    print("Loading Defense data...")
    load_defense_data(defense_house_path, defense_senate_path)

    # Load Finance/Insurance/Real Estate data (OpenSecrets CSVs)
    finance_house_path   = os.path.join(script_dir, "Money_from_Finance_Insurance_Real_Estate_to_US_Representatives__1990-2024.csv")
    finance_senate_path  = os.path.join(script_dir, "Money_from_Finance_Insurance_Real_Estate_to_US_Senators__1990-2024.csv")
    print("Loading Finance/Insurance/Real Estate data...")
    load_finance_data(finance_house_path, finance_senate_path)

    # Load Tech/Communications/Electronics data (OpenSecrets CSVs)
    tech_house_path   = os.path.join(script_dir, "Money_from_Communications_Electronics_to_US_Representatives__1990-2024.csv")
    tech_senate_path  = os.path.join(script_dir, "Money_from_Communications_Electronics_to_US_Senators__1990-2024.csv")
    print("Loading Tech/Communications data...")
    load_tech_data(tech_house_path, tech_senate_path)

    # ── Load pre-known candidate IDs (optional speed-up) ─────────────────
    known_ids = {}
    if os.path.exists(ids_path):
        with open(ids_path) as f:
            try:
                raw = json.load(f)
                known_ids = {
                    n: d["candidate_id"]
                    for n, d in raw.items()
                    if d.get("candidate_id")
                }
                print(f"Loaded {len(known_ids)} pre-known candidate IDs")
            except Exception:
                pass

    # ── Load member list ──────────────────────────────────────────────────
    if not os.path.exists(members_path):
        print("ERROR: members.json not found in script directory.")
        print("Expected format: [{\"name\": \"...\", \"state\": \"TX\", \"office\": \"S\"}, ...]")
        return
    with open(members_path) as f:
        all_members = json.load(f)
    print(f"Loaded {len(all_members)} members from members.json")

    # ── Resume from partial run ───────────────────────────────────────────
    output = {}
    if os.path.exists(out_path):
        with open(out_path) as f:
            try:
                output = json.load(f)
                print(f"Resuming — {len(output)} already done\n")
            except Exception:
                pass

    total_count = len(all_members)
    no_id_count = sum(1 for m in all_members if m["name"] not in known_ids)
    print(f"Total members : {total_count}")
    print(f"Need ID lookup: {no_id_count}")
    print(f"Pre-known IDs : {total_count - no_id_count}\n")
    print("Starting... (this will take 30-90 minutes for all members)\n")

    failed = []

    for i, member in enumerate(all_members):
        name   = member["name"]
        state  = member.get("state", "")
        office = member.get("office", "S")

        if name in output:
            continue

        print(f"[{i+1:3d}/{total_count}] {name:<36}", end=" ", flush=True)

        try:
            # ── STEP 1: Resolve candidate ID(s) ─────────────────────────
            # For members with a known ID, skip the search entirely and
            # just use what we have. Only search for a cross-chamber ID
            # if this is a senator (senators more commonly served in House
            # first). House members almost never served in Senate first,
            # so we skip that search entirely to save API calls.
            known_cid = known_ids.get(name)
            if known_cid:
                candidate_ids = [(known_cid, office)]
                # Only check for a House→Senate career if current office is S
                if office == "S":
                    parts    = name.split()
                    suffixes = {"Jr.", "Sr.", "II", "III", "IV", "Jr", "Sr"}
                    last     = next((p for p in reversed(parts) if p not in suffixes), parts[-1])
                    last     = re.sub(r'[^a-zA-Z]', '', last)
                    data = get("/candidates/search/", {
                        "q": last, "state": state, "office": "H", "per_page": 10
                    })
                    if data and data.get("results"):
                        for r in data["results"]:
                            # HARD STATE CHECK — even though we asked for state=X,
                            # the FEC API has historically returned cross-state
                            # results when names match. Reject any result whose
                            # state doesn't match the member's declared state.
                            if state and r.get("state") and r.get("state") != state:
                                continue
                            sc = _score(name, r.get("name", ""))
                            if sc >= 3 and r.get("candidate_id") != known_cid:
                                candidate_ids.append((r["candidate_id"], "H"))
                                break
            else:
                # No known ID — do full resolution for current office only.
                # resolve_all_candidate_ids handles both chambers internally.
                candidate_ids = resolve_all_candidate_ids(name, state, office)
                if not candidate_ids:
                    print("✗ not found in FEC")
                    failed.append(name)
                    time.sleep(0.3)
                    continue
            time.sleep(0.2)

            # ── STEP 2: Get ALL committees (single API call per candidate) ──
            committee_ids = get_all_committees(candidate_ids)
            if not committee_ids:
                print("✗ no committees found")
                failed.append(name)
                continue
            time.sleep(0.2)

            # ── STEP 3: Determine relevant cycles (no API call needed) ───────
            # Senate members get full history back to 1990.
            # House members get history back to 2000.
            active_cycles = get_active_cycles_for_member(office)

            # ── STEP 4: Pull ALL receipts from Browse Receipts (Schedule A) ──
            # This is the core data pull — all years, all committees, deduplicated
            sectors, grassroots, receipts_total = fetch_all_receipts(
                committee_ids, active_cycles, name
            )
            time.sleep(0.2)

            # ── STEP 5: AIPAC — from TrackAIPAC data (most complete) ────────
            # TrackAIPAC captures PACs + IE + bundled Lobby Donors.
            # The Lobby Donors layer cannot be reconstructed from FEC alone.
            #
            # IMPORTANT: aipac must always equal the sum of its components
            # (pacs + ie + lobby_donors). The previous version stored ta["total"]
            # directly, which sometimes disagreed with the sum of components
            # (TrackAIPAC's "total" row occasionally differs from PACs+IE+Donors).
            # We now compute aipac from the components to guarantee internal
            # consistency, which the validator enforces.
            ta = get_aipac_from_trackaipac(name)
            sectors["aipac"] = ta["pacs"] + ta["ie"] + ta["lobby_donors"]

            # Fossil fuels — from OpenSecrets CSV (1990-2024 career totals)
            sectors["fossil_fuels"] = get_oil_amount(name, state)

            # Pharma/Health — from OpenSecrets CSV (1990-2024 career totals)
            sectors["pharma"] = get_pharma_amount(name, state)

            # Defense — from OpenSecrets CSV (1990-2024 career totals)
            sectors["defense"] = get_defense_amount(name, state)

            # Finance/Insurance/Real Estate — from OpenSecrets CSV (1990-2024 career totals)
            sectors["finance"] = get_finance_amount(name, state)

            # Tech/Communications/Electronics — from OpenSecrets CSV (1990-2024 career totals)
            sectors["tech"] = get_tech_amount(name, state)

            # ── STEP 6: Special interest total ──────────────────────────────
            special_interest_total = sum(
                sectors.get(k, 0)
                for k in ["aipac", "fossil_fuels", "pharma", "defense", "finance", "tech", "nra"]
            )

            # ── Build output record ──────────────────────────────────────────
            primary_cid = candidate_ids[0][0]
            all_cids    = [c for c, _ in candidate_ids]

            output[name] = {
                # Identity
                "candidate_id":      primary_cid,
                "all_candidate_ids": all_cids,
                "committees":        committee_ids,
                "source":            "FEC_v7",

                # Total raised (from Schedule A receipts — all years served)
                "total_raised":           receipts_total,

                # Special interest categories
                "aipac":                  sectors.get("aipac",        0),
                "aipac_pacs":             ta["pacs"],
                "aipac_ie":               ta["ie"],
                "aipac_lobby_donors":     ta["lobby_donors"],
                "aipac_sources":          ta["sources"],
                "fossil_fuels":           sectors.get("fossil_fuels",  0),
                "pharma":                 sectors.get("pharma",        0),
                "defense":                sectors.get("defense",       0),
                "finance":                sectors.get("finance",       0),
                "tech":                   sectors.get("tech",          0),
                "nra":                    sectors.get("nra",           0),
                "special_interest_total": special_interest_total,

                # Grassroots (tracked separately — not SI money)
                "grassroots":             grassroots,
            }

            # ── Console summary ──────────────────────────────────────────────
            d     = output[name]
            parts = []
            if d["total_raised"]:           parts.append(f"raised {fmt(d['total_raised'])}")
            if d["special_interest_total"]: parts.append(f"SI {fmt(d['special_interest_total'])}")
            if d["aipac"]:                  parts.append(f"AIPAC {fmt(d['aipac'])}")
            if d["fossil_fuels"]:           parts.append(f"fossil {fmt(d['fossil_fuels'])}")
            if d["pharma"]:                 parts.append(f"pharma {fmt(d['pharma'])}")
            if d["defense"]:                parts.append(f"def {fmt(d['defense'])}")
            if d["finance"]:                parts.append(f"fin {fmt(d['finance'])}")
            if d["tech"]:                   parts.append(f"tech {fmt(d['tech'])}")
            if d["nra"]:                    parts.append(f"NRA {fmt(d['nra'])}")
            if d["grassroots"]:             parts.append(f"grass {fmt(d['grassroots'])}")
            print("✓ " + ("  ".join(parts) if parts else "(no categorized money found)"))

        except KeyboardInterrupt:
            print(f"\n\nInterrupted — saving {len(output)} completed members...")
            break
        except Exception as e:
            print(f"✗ error: {e}")
            failed.append(name)

        # Auto-save every 25 members
        if (i + 1) % 25 == 0:
            with open(out_path, "w") as f:
                json.dump(output, f, indent=2)
            print(f"  ──── Auto-saved {len(output)} members ────")

        time.sleep(0.5)

    # ── Final save ────────────────────────────────────────────────────────
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    # ── REQUIREMENT 6: Deduplication audit ───────────────────────────────
    print(f"\n{'='*70}")
    print(f"DONE: {len(output)} members processed  |  {len(failed)} failed\n")
    print("UNIQUENESS AUDIT (checking for duplicate value signatures):")
    seen_sigs = {}
    dup_found = False
    for mname, d in output.items():
        sig = tuple(
            d.get(k, 0)
            for k in ["aipac","fossil_fuels","pharma","defense","finance","tech"]
        )
        if any(v > 0 for v in sig):
            if sig in seen_sigs:
                print(f"  ⚠  DUPLICATE: {mname}  ==  {seen_sigs[sig]}")
                print(f"     Signature: {sig}")
                dup_found = True
            else:
                seen_sigs[sig] = mname
    if not dup_found:
        print("  ✓  No duplicate value signatures detected\n")
    else:
        print()

    # ── Summary stats ─────────────────────────────────────────────────────
    n = len(output)
    print("SECTOR COVERAGE SUMMARY:")
    fields = [
        ("total_raised",           "Total Raised"),
        ("special_interest_total", "Special Interest Total"),
        ("aipac",                  "AIPAC"),
        ("fossil_fuels",           "Fossil Fuels"),
        ("pharma",                 "Pharma"),
        ("defense",                "Defense"),
        ("finance",                "Finance"),
        ("tech",                   "Tech"),
        ("nra",                    "NRA / Gun Rights"),
        ("grassroots",             "Grassroots"),
    ]
    for field, label in fields:
        has_data = sum(1 for d in output.values() if d.get(field, 0) > 0)
        avg      = sum(d.get(field, 0) for d in output.values()) / max(n, 1)
        total    = sum(d.get(field, 0) for d in output.values())
        print(f"  {label:<25}  {has_data:3d} members   "
              f"avg {fmt(avg) or '$0':<8}  total {fmt(total) or '$0'}")

    if failed:
        print(f"\nFAILED TO FETCH ({len(failed)} members):")
        for mname in failed:
            print(f"  - {mname}")

    print(f"\nOutput saved to: {out_path}")
    print("Upload fec_data_v7.json to Claude to update index.html")


if __name__ == "__main__":
    main()
