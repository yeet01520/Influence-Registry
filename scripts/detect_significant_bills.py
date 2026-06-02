#!/usr/bin/env python3
"""
detect_significant_bills.py

Surfaces draft bills.json entries for significant roll-call votes from
the past 7 days using:
  - Congress.gov API for House votes (clean JSON)
  - senate.gov XML feeds for Senate votes (parsed XML)

Filters to "significant" votes:
  - Final-passage roll-call votes only (not motions/procedural)
  - Substantive bill types (HR, S, HJRES, SJRES — skip post-office namings,
    commemorative resolutions, simple/concurrent resolutions)
  - Margin under 100 (excludes truly bipartisan low-stakes bills)
  - Skip duplicates already in bills.json (by id)

Outputs:
  - data/bills_drafts.json — array of draft entries with editorial fields
    pre-filled with TODO hints based on vote data
  - GitHub Actions step output: a markdown summary suitable for an Issue body

USAGE (in GitHub Actions):
  Reads CONGRESS_API_KEY from environment.
  Reads/writes data/bills.json (read-only) and data/bills_drafts.json.
"""

import json
import os
import sys
import time
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode
import urllib.request
import urllib.error

# ---------- Tunables ----------
LOOKBACK_DAYS    = 7
# Skip votes with margin wider than this. 250 lets through:
#   - Partisan bills (typical margin 2-30)
#   - Bipartisan but still notable (NDAA-style, margin ~190)
# while still filtering out near-unanimous post office namings and the like.
MARGIN_CEILING   = 250
MAX_DRAFTS       = 8     # cap how many drafts to surface per run

# Bill types we care about (substantive)
SIGNIFICANT_BILL_TYPES = {"HR", "S", "HJRES", "SJRES"}

# Bill type abbreviations for ID formatting
BILL_TYPE_DISPLAY = {
    "HR": "H.R.",
    "S": "S.",
    "HJRES": "H.J.Res.",
    "SJRES": "S.J.Res.",
}

# Title keywords that indicate routine/commemorative bills (skip these)
SKIP_TITLE_KEYWORDS = [
    "post office", "postal facility", "naming", "redesignating",
    "commemorating", "recognizing the contributions",
    "expressing the sense", "honoring the life",
]

API_BASE  = "https://api.congress.gov/v3"
DATA_DIR  = Path(__file__).resolve().parent.parent / "data"
BILLS_FILE   = DATA_DIR / "bills.json"
DRAFTS_FILE  = DATA_DIR / "bills_drafts.json"

# Observability counters. Printed at the end so we can tell
# "Congress was quiet" apart from "the API call shape broke."
STATS = {
    "house_api_calls": 0,
    "house_api_failures": 0,
    "senate_xml_fetches": 0,
    "senate_xml_failures": 0,
    "house_votes_returned": 0,
    "senate_votes_in_menu": 0,
    "candidates_before_filter": 0,
    "filtered_wrong_bill_type": 0,
    "filtered_margin_too_wide": 0,
    "filtered_not_final_passage": 0,
    "filtered_routine_title": 0,
    "filtered_no_bill_id": 0,
    "filtered_duplicate": 0,
}


def get_api_key():
    key = os.environ.get("CONGRESS_API_KEY")
    if not key:
        sys.exit("ERROR: CONGRESS_API_KEY not set")
    return key


def fetch(path, params, api_key, retries=3):
    """GET from Congress.gov API."""
    STATS["house_api_calls"] += 1
    params = {**params, "api_key": api_key, "format": "json"}
    url = f"{API_BASE}{path}?{urlencode(params)}"
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "InfluenceRegistry/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print(f"  rate-limited, waiting...", flush=True)
                time.sleep(15 * attempt)
                continue
            if 500 <= e.code < 600:
                time.sleep(2 * attempt)
                continue
            print(f"  HTTP {e.code}: {url[:120]}", flush=True)
            STATS["house_api_failures"] += 1
            return None
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"  network error attempt {attempt}: {e}", flush=True)
            time.sleep(2 * attempt)
    STATS["house_api_failures"] += 1
    return None


def get_current_congress_session():
    """119th Congress: 1st session = 2025, 2nd session = 2026."""
    year = datetime.now(timezone.utc).year
    if year == 2025:
        return 119, 1
    elif year == 2026:
        return 119, 2
    elif year == 2027:
        return 120, 1
    elif year == 2028:
        return 120, 2
    return 119, 2  # safe default


def is_routine_title(title):
    """Filter out post office namings, commemorative resolutions, etc."""
    if not title:
        return False
    lower = title.lower()
    return any(kw in lower for kw in SKIP_TITLE_KEYWORDS)


def fetch_bill_details(bill_type, bill_number, congress, api_key):
    """Get sponsor and full title for a bill."""
    bt = bill_type.lower()
    data = fetch(f"/bill/{congress}/{bt}/{bill_number}", {}, api_key)
    if not data or "bill" not in data:
        return None
    bill = data["bill"]
    sponsors = bill.get("sponsors", [])
    sponsor_str = ""
    if sponsors:
        s = sponsors[0]
        name = s.get("fullName") or f"{s.get('firstName','')} {s.get('lastName','')}".strip()
        party = s.get("party", "")
        state = s.get("state", "")
        sponsor_str = f"{name} ({party}-{state})" if party and state else name
    return {
        "title": bill.get("title", ""),
        "sponsor": sponsor_str,
    }


def fetch_house_member_votes(congress, session, vote_number, api_key):
    """Get the full member-by-member roll for a House vote."""
    data = fetch(f"/house-vote/{congress}/{session}/{vote_number}/members", {}, api_key)
    if not data:
        return [], []
    yes_votes = []
    no_votes = []
    member_votes = data.get("houseRollCallVoteMemberVotes", {}).get("results", [])
    for m in member_votes:
        vote_cast = (m.get("voteCast") or "").upper()
        name = m.get("firstName", "") + " " + m.get("lastName", "")
        name = name.strip()
        party = m.get("voteParty", "")
        state = m.get("voteState", "")
        entry = {"name": name, "party": party, "state": state}
        if vote_cast in ("YEA", "AYE", "YES"):
            yes_votes.append(entry)
        elif vote_cast in ("NAY", "NO"):
            no_votes.append(entry)
    return yes_votes, no_votes


def fetch_house_votes(congress, session, since_date, api_key):
    """List House roll-call votes since the given date."""
    votes = []
    page = 1
    while page <= 5:  # cap at 5 pages = 100 votes
        data = fetch(
            f"/house-vote/{congress}/{session}",
            {"limit": 20, "offset": (page-1)*20, "format": "json"},
            api_key,
        )
        if not data:
            break
        results = data.get("houseRollCallVotes", [])
        if not results:
            break
        for v in results:
            vote_date_str = v.get("startDate", "")[:10]
            if vote_date_str < since_date:
                # We've gone past the lookback window
                return votes
            votes.append(v)
        page += 1
    return votes


def derive_status(yea, nay, vote_type):
    """Determine Passed/Failed/Pending from vote totals."""
    vt = (vote_type or "").lower()
    if "passage" in vt or "final" in vt:
        return "Passed" if yea > nay else "Failed"
    if yea > nay:
        return "Passed"
    return "Failed"


def build_draft_from_house_vote(vote, congress, session, api_key):
    """
    Build a draft bills.json entry from a House roll-call vote.
    Returns None if vote should be skipped.
    """
    STATS["candidates_before_filter"] += 1
    bill_data = vote.get("bill") or {}
    bill_type = (bill_data.get("type") or "").upper()
    bill_number = bill_data.get("number")

    if bill_type not in SIGNIFICANT_BILL_TYPES:
        STATS["filtered_wrong_bill_type"] += 1
        return None
    if not bill_number:
        STATS["filtered_no_bill_id"] += 1
        return None

    yea = vote.get("yeaCount") or 0
    nay = vote.get("nayCount") or 0
    margin = abs(yea - nay)

    # Filter: skip if margin too wide (uncontroversial)
    if margin > MARGIN_CEILING:
        STATS["filtered_margin_too_wide"] += 1
        return None

    vote_type = vote.get("voteType", "")
    vote_question = vote.get("voteQuestion", "") or ""
    if "passage" not in vote_type.lower() and "passage" not in vote_question.lower() and "final" not in vote_type.lower():
        # Only final-passage votes
        STATS["filtered_not_final_passage"] += 1
        return None

    # Get bill details (title, sponsor)
    details = fetch_bill_details(bill_type, bill_number, congress, api_key)
    if not details:
        return None
    title = details["title"]
    if is_routine_title(title):
        STATS["filtered_routine_title"] += 1
        return None

    # Get full member votes
    vote_number = vote.get("rollCallNumber") or vote.get("number")
    yes_votes, no_votes = fetch_house_member_votes(congress, session, vote_number, api_key)

    # Identify defectors (members voting against their party majority)
    defectors_yes = []
    defectors_no = []
    party_counts_yes = {}
    party_counts_no = {}
    for m in yes_votes:
        party_counts_yes[m["party"]] = party_counts_yes.get(m["party"], 0) + 1
    for m in no_votes:
        party_counts_no[m["party"]] = party_counts_no.get(m["party"], 0) + 1

    majority_party_yes = max(party_counts_yes, key=party_counts_yes.get) if party_counts_yes else ""
    majority_party_no = max(party_counts_no, key=party_counts_no.get) if party_counts_no else ""

    if majority_party_yes:
        for m in yes_votes:
            if m["party"] != majority_party_yes:
                defectors_yes.append(m["name"])
    if majority_party_no:
        for m in no_votes:
            if m["party"] != majority_party_no:
                defectors_no.append(m["name"])

    defector_hint = ""
    if defectors_yes or defectors_no:
        parts = []
        if defectors_yes:
            parts.append(f"{majority_party_no}-side YES: {', '.join(defectors_yes[:5])}")
        if defectors_no:
            parts.append(f"{majority_party_yes}-side NO: {', '.join(defectors_no[:5])}")
        defector_hint = " | Defectors — " + "; ".join(parts)

    status = derive_status(yea, nay, vote_type)
    vote_date = vote.get("startDate", "")[:10]
    month_year = ""
    if vote_date:
        try:
            dt = datetime.strptime(vote_date, "%Y-%m-%d")
            month_year = dt.strftime("%b %Y")
        except ValueError:
            month_year = vote_date

    bill_id = f"{BILL_TYPE_DISPLAY[bill_type]}{bill_number} (House)"

    return {
        "id": bill_id,
        "title": title,
        "date": month_year,
        "status": status,
        "chamber": "House",
        "category": "TODO: Pick from existing categories — Budget/Tax, Healthcare, Immigration, Defense, Foreign Policy, Climate/Tax, Tech, etc.",
        "sponsor": details["sponsor"],
        "description": (
            f"TODO: Write 1-3 sentences with stakes/context. "
            f"Vote was {yea}-{nay} (margin {margin})."
            + defector_hint
        ),
        "key_provisions": [
            "TODO: Research and list 4-7 key provisions"
        ],
        "donors_for": [
            "TODO: Research industries/groups lobbying FOR this bill"
        ],
        "donors_against": [
            "TODO: Research industries/groups lobbying AGAINST"
        ],
        "yes_votes": yes_votes,
        "no_votes": no_votes,
        "_meta": {
            "source": "Congress.gov API",
            "vote_url": f"https://clerk.house.gov/Votes/{vote.get('startDate','')[:4]}{vote_number}",
            "bill_url": f"https://www.congress.gov/bill/{congress}th-congress/{bill_type.lower()}-bill/{bill_number}",
            "detected_at": datetime.now(timezone.utc).isoformat(),
        }
    }


def load_existing_bills():
    """Load existing bills to skip duplicates."""
    if not BILLS_FILE.exists():
        return set()
    try:
        bills = json.loads(BILLS_FILE.read_text())
        return {b.get("id") for b in bills if b.get("id")}
    except Exception as e:
        print(f"WARN: could not parse bills.json: {e}", flush=True)
        return set()


def load_existing_drafts():
    """Load existing drafts to avoid re-creating them."""
    if not DRAFTS_FILE.exists():
        return []
    try:
        return json.loads(DRAFTS_FILE.read_text())
    except Exception:
        return []


def fetch_url_xml(url, retries=3):
    """Fetch a URL and parse as XML. Returns ElementTree root or None."""
    STATS["senate_xml_fetches"] += 1
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "InfluenceRegistry/1.0 (transparency tool)"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                content = resp.read()
                return ET.fromstring(content)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None  # vote doesn't exist
            print(f"  HTTP {e.code} on {url[:100]}, attempt {attempt}", flush=True)
            time.sleep(2 * attempt)
        except (urllib.error.URLError, TimeoutError, ET.ParseError) as e:
            print(f"  Error fetching {url[:100]}: {e}", flush=True)
            time.sleep(2 * attempt)
    STATS["senate_xml_failures"] += 1
    return None


def parse_senate_date(date_str, year):
    """Parse Senate menu's '18-Dec' format into ISO date."""
    if not date_str:
        return None
    try:
        dt = datetime.strptime(f"{date_str.strip()}-{year}", "%d-%b-%Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return None


def fetch_senate_vote_menu(congress, session):
    """Get the Senate's roll-call vote menu XML for the given session."""
    url = f"https://www.senate.gov/legislative/LIS/roll_call_lists/vote_menu_{congress}_{session}.xml"
    root = fetch_url_xml(url)
    if root is None:
        return [], None
    year_elem = root.find("congress_year")
    year = int(year_elem.text) if year_elem is not None and year_elem.text else datetime.now().year
    votes = []
    for vote in root.findall(".//vote"):
        # Skip en_bloc votes (multi-nomination batches we don't want individually)
        if vote.find("en_bloc") is not None:
            continue
        votes.append(vote)
    return votes, year


def fetch_senate_vote_detail(congress, session, vote_number):
    """
    Fetch a single Senate vote XML with full member-by-member roll.
    Returns dict with vote data, or None on failure.
    """
    # Senate URL: vote1191/vote_119_1_00404.xml (zero-padded vote number)
    folder = f"vote{congress}{session}"
    fname = f"vote_{congress}_{session}_{vote_number}.xml"
    url = f"https://www.senate.gov/legislative/LIS/roll_call_votes/{folder}/{fname}"
    root = fetch_url_xml(url)
    if root is None:
        return None

    def gettext(path):
        e = root.find(path)
        return (e.text or "").strip() if e is not None and e.text else ""

    # Parse member-by-member votes
    yes_votes = []
    no_votes = []
    for m in root.findall(".//members/member"):
        first = (m.findtext("first_name") or "").strip()
        last = (m.findtext("last_name") or "").strip()
        # Senate XML often has full name in <last_name> with first name in another field
        # But also has separate elements; we combine them
        name = f"{first} {last}".strip() if first else last
        party = (m.findtext("party") or "").strip()
        state = (m.findtext("state") or "").strip()
        vote_cast = (m.findtext("vote_cast") or "").strip().lower()
        entry = {"name": name, "party": party, "state": state}
        if vote_cast in ("yea", "aye", "yes"):
            yes_votes.append(entry)
        elif vote_cast in ("nay", "no"):
            no_votes.append(entry)

    return {
        "vote_number": vote_number,
        "vote_question": gettext("vote_question_text") or gettext("vote_question"),
        "vote_result": gettext("vote_result"),
        "vote_date": gettext("vote_date"),
        "document_title": gettext("document/document_title") or gettext("vote_document_text"),
        "document_short_title": gettext("document/document_short_title"),
        "amendment_purpose": gettext("amendment/amendment_purpose"),
        "yeas": int(gettext("count/yeas") or 0),
        "nays": int(gettext("count/nays") or 0),
        "yes_votes": yes_votes,
        "no_votes": no_votes,
    }


def is_senate_final_passage(vote_question):
    """Check if a Senate vote is a final passage (vs procedural/cloture)."""
    if not vote_question:
        return False
    q = vote_question.lower()
    # Skip cloture, procedural, motion-to-proceed votes
    if "cloture" in q or "motion to proceed" in q or "motion to table" in q:
        return False
    # Skip nominations (Senate-only; not legislation)
    if "nomination" in q or "nominee" in q:
        return False
    # Skip simple resolutions and amendments (not bill passage)
    if "amendment" in q and "passage" not in q:
        return False
    # Look for passage-indicating language
    passage_keywords = ["passage of", "on passage", "on the bill", "on the joint resolution",
                        "on the conference report", "on the motion (h.r.", "on the motion (s."]
    return any(kw in q for kw in passage_keywords)


def extract_senate_bill_id(vote_detail):
    """
    Extract a bill ID like 'H.R.1' or 'S.123' from a Senate vote.
    Senate XML's <document_short_title> often has this. Returns (bill_type, number) or (None, None).
    """
    text = vote_detail.get("document_short_title", "") or vote_detail.get("vote_question", "")
    # Common patterns: "H.R. 1", "S. 123", "H.J. Res. 5", "S.J. Res. 12"
    # Look for them in text
    patterns = [
        (r"\bH\.\s*R\.\s*(\d+)", "HR"),
        (r"\bS\.\s*(\d+)\b", "S"),
        (r"\bH\.\s*J\.\s*Res\.\s*(\d+)", "HJRES"),
        (r"\bS\.\s*J\.\s*Res\.\s*(\d+)", "SJRES"),
    ]
    for pat, bill_type in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return bill_type, m.group(1)
    return None, None


def build_draft_from_senate_vote(vote_detail, vote_date_iso, congress):
    """Build a draft bills.json entry from a Senate vote detail dict."""
    STATS["candidates_before_filter"] += 1
    yea = vote_detail["yeas"]
    nay = vote_detail["nays"]
    margin = abs(yea - nay)

    # Filter: only contested votes
    if margin > MARGIN_CEILING:
        STATS["filtered_margin_too_wide"] += 1
        return None

    # Filter: only final passage
    if not is_senate_final_passage(vote_detail["vote_question"]):
        STATS["filtered_not_final_passage"] += 1
        return None

    bill_type, bill_number = extract_senate_bill_id(vote_detail)
    if not bill_type or not bill_number:
        STATS["filtered_no_bill_id"] += 1
        return None
    if bill_type not in SIGNIFICANT_BILL_TYPES:
        STATS["filtered_wrong_bill_type"] += 1
        return None

    title = vote_detail.get("document_title") or vote_detail.get("document_short_title") or ""
    if is_routine_title(title):
        STATS["filtered_routine_title"] += 1
        return None

    # Defectors analysis
    yes_votes = vote_detail["yes_votes"]
    no_votes = vote_detail["no_votes"]
    party_counts_yes = {}
    party_counts_no = {}
    for m in yes_votes:
        party_counts_yes[m["party"]] = party_counts_yes.get(m["party"], 0) + 1
    for m in no_votes:
        party_counts_no[m["party"]] = party_counts_no.get(m["party"], 0) + 1
    majority_yes = max(party_counts_yes, key=party_counts_yes.get) if party_counts_yes else ""
    majority_no = max(party_counts_no, key=party_counts_no.get) if party_counts_no else ""
    defectors_yes = [m["name"] for m in yes_votes if m["party"] != majority_yes][:5]
    defectors_no = [m["name"] for m in no_votes if m["party"] != majority_no][:5]
    defector_hint = ""
    if defectors_yes or defectors_no:
        parts = []
        if defectors_yes:
            parts.append(f"{majority_no}-side YES: {', '.join(defectors_yes)}")
        if defectors_no:
            parts.append(f"{majority_yes}-side NO: {', '.join(defectors_no)}")
        defector_hint = " | Defectors — " + "; ".join(parts)

    # Status
    result = (vote_detail.get("vote_result") or "").lower()
    if "passed" in result or "agreed" in result:
        status = "Passed"
    elif "rejected" in result or "failed" in result:
        status = "Failed"
    else:
        status = "Passed" if yea > nay else "Failed"

    # Date formatting
    month_year = ""
    if vote_date_iso:
        try:
            dt = datetime.strptime(vote_date_iso, "%Y-%m-%d")
            month_year = dt.strftime("%b %Y")
        except ValueError:
            month_year = vote_date_iso

    bill_id = f"{BILL_TYPE_DISPLAY[bill_type]}{bill_number} (Senate)"

    return {
        "id": bill_id,
        "title": title,
        "date": month_year,
        "status": status,
        "chamber": "Senate",
        "category": "TODO: Pick from existing categories — Budget/Tax, Healthcare, Immigration, Defense, Foreign Policy, Climate/Tax, Tech, etc.",
        "sponsor": "TODO: Look up sponsor on Congress.gov (Senate XML doesn't include sponsor)",
        "description": (
            f"TODO: Write 1-3 sentences with stakes/context. "
            f"Vote was {yea}-{nay} (margin {margin})."
            + defector_hint
        ),
        "key_provisions": [
            "TODO: Research and list 4-7 key provisions"
        ],
        "donors_for": [
            "TODO: Research industries/groups lobbying FOR this bill"
        ],
        "donors_against": [
            "TODO: Research industries/groups lobbying AGAINST"
        ],
        "yes_votes": yes_votes,
        "no_votes": no_votes,
        "_meta": {
            "source": "senate.gov XML",
            "vote_url": f"https://www.senate.gov/legislative/LIS/roll_call_votes/vote{congress}{vote_detail.get('session','')}/vote_{congress}_{vote_detail.get('session','')}_{vote_detail['vote_number']}.htm",
            "bill_url": f"https://www.congress.gov/bill/{congress}th-congress/{bill_type.lower().replace('hjres','house-joint-resolution').replace('sjres','senate-joint-resolution').replace('hr','house-bill').replace('s','senate-bill')}/{bill_number}",
            "detected_at": datetime.now(timezone.utc).isoformat(),
        }
    }


def process_senate_votes(congress, session, since_date, existing_ids, existing_draft_ids):
    """Fetch and process Senate votes, returning new drafts."""
    drafts = []
    menu, year = fetch_senate_vote_menu(congress, session)
    if not menu:
        print("  Could not fetch Senate vote menu", flush=True)
        return drafts

    print(f"  Found {len(menu)} Senate votes total in session", flush=True)
    STATS["senate_votes_in_menu"] = len(menu)

    for vote in menu:
        if len(drafts) >= MAX_DRAFTS:
            break

        vote_date_str = vote.findtext("vote_date") or ""
        vote_date_iso = parse_senate_date(vote_date_str, year)
        if not vote_date_iso or vote_date_iso < since_date:
            continue  # outside lookback window

        vote_number = vote.findtext("vote_number") or ""
        if not vote_number:
            continue

        # Quick filter on menu-level question to skip obvious noise
        question = (vote.findtext("question") or "").lower()
        if "cloture" in question or "nomination" in question:
            continue

        # Fetch full vote detail
        try:
            detail = fetch_senate_vote_detail(congress, session, vote_number)
        except Exception as e:
            print(f"  ERROR fetching Senate vote {vote_number}: {e}", flush=True)
            continue
        if not detail:
            continue
        detail["session"] = session

        try:
            draft = build_draft_from_senate_vote(detail, vote_date_iso, congress)
        except Exception as e:
            print(f"  ERROR building Senate draft for {vote_number}: {e}", flush=True)
            continue
        if draft is None:
            continue
        if draft["id"] in existing_ids:
            STATS["filtered_duplicate"] += 1
            print(f"  SKIP Senate (already in bills.json): {draft['id']}", flush=True)
            continue
        if draft["id"] in existing_draft_ids:
            STATS["filtered_duplicate"] += 1
            print(f"  SKIP Senate (already in drafts): {draft['id']}", flush=True)
            continue
        drafts.append(draft)
        print(f"  + SENATE DRAFT: {draft['id']} — {draft['title'][:60]}", flush=True)
        time.sleep(0.5)

    return drafts


def main():
    api_key = get_api_key()
    congress, session = get_current_congress_session()
    print(f"Looking at {congress}th Congress, session {session}", flush=True)

    since = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    print(f"Lookback: votes since {since}", flush=True)

    existing_ids = load_existing_bills()
    print(f"Existing bills.json: {len(existing_ids)} entries", flush=True)

    existing_drafts = load_existing_drafts()
    existing_draft_ids = {d.get("id") for d in existing_drafts}
    print(f"Existing drafts: {len(existing_drafts)} entries", flush=True)

    # House votes
    print("\nFetching House votes...", flush=True)
    house_votes = fetch_house_votes(congress, session, since, api_key)
    STATS["house_votes_returned"] = len(house_votes)
    print(f"  Found {len(house_votes)} House votes in lookback window", flush=True)

    new_drafts = []
    for vote in house_votes:
        if len(new_drafts) >= MAX_DRAFTS:
            break
        try:
            draft = build_draft_from_house_vote(vote, congress, session, api_key)
        except Exception as e:
            print(f"  ERROR processing vote: {e}", flush=True)
            continue
        if draft is None:
            continue
        if draft["id"] in existing_ids:
            STATS["filtered_duplicate"] += 1
            print(f"  SKIP (already in bills.json): {draft['id']}", flush=True)
            continue
        if draft["id"] in existing_draft_ids:
            STATS["filtered_duplicate"] += 1
            print(f"  SKIP (already in drafts): {draft['id']}", flush=True)
            continue
        new_drafts.append(draft)
        print(f"  + DRAFT: {draft['id']} — {draft['title'][:60]}", flush=True)
        time.sleep(0.5)  # polite to API

    # ─── Senate votes (senate.gov XML) ───────────────────────────────────
    print("\nFetching Senate votes...", flush=True)
    senate_drafts = process_senate_votes(congress, session, since, existing_ids, existing_draft_ids)
    print(f"  {len(senate_drafts)} new Senate drafts", flush=True)
    new_drafts = new_drafts + senate_drafts

    # Append new drafts to existing
    all_drafts = existing_drafts + new_drafts
    DRAFTS_FILE.write_text(json.dumps(all_drafts, indent=2))

    print(f"\n{'='*60}", flush=True)
    print(f"DONE. {len(new_drafts)} new drafts surfaced.", flush=True)
    print(f"Total drafts in {DRAFTS_FILE.name}: {len(all_drafts)}", flush=True)

    # Diagnostic stats. If new_drafts is 0, this tells us why:
    #   - Was Congress actually quiet? (house_votes_returned == 0)
    #   - Or did the API call shape break? (high house_api_failures)
    #   - Or are the filters too strict? (high filtered_* counts)
    print(f"\n--- DIAGNOSTICS ---", flush=True)
    print(f"  House API calls:        {STATS['house_api_calls']} (failures: {STATS['house_api_failures']})", flush=True)
    print(f"  Senate XML fetches:     {STATS['senate_xml_fetches']} (failures: {STATS['senate_xml_failures']})", flush=True)
    print(f"  House votes returned:   {STATS['house_votes_returned']}", flush=True)
    print(f"  Senate votes in menu:   {STATS['senate_votes_in_menu']}", flush=True)
    print(f"  Candidates evaluated:   {STATS['candidates_before_filter']}", flush=True)
    print(f"  Filtered (margin>{MARGIN_CEILING}):  {STATS['filtered_margin_too_wide']}", flush=True)
    print(f"  Filtered (not passage): {STATS['filtered_not_final_passage']}", flush=True)
    print(f"  Filtered (wrong type):  {STATS['filtered_wrong_bill_type']}", flush=True)
    print(f"  Filtered (routine):     {STATS['filtered_routine_title']}", flush=True)
    print(f"  Filtered (no bill ID):  {STATS['filtered_no_bill_id']}", flush=True)
    print(f"  Filtered (duplicate):   {STATS['filtered_duplicate']}", flush=True)

    # Sanity-check: if we made API calls but got 0 votes, something likely
    # changed upstream. Make this loud so the workflow surfaces it.
    if STATS["house_api_calls"] >= 3 and STATS["house_votes_returned"] == 0 and STATS["house_api_failures"] == 0:
        print(f"\n⚠️  WARNING: Made {STATS['house_api_calls']} House API calls but got 0 votes returned.", flush=True)
        print(f"   This may mean Congress was in recess, or the API response shape changed.", flush=True)
    if STATS["senate_xml_fetches"] >= 1 and STATS["senate_votes_in_menu"] == 0:
        print(f"\n⚠️  WARNING: Senate XML fetched but 0 votes parsed from menu.", flush=True)
        print(f"   This may mean the Senate hasn't held votes this session, or XML schema changed.", flush=True)

    # Build markdown summary for GitHub Issue
    if new_drafts:
        # Group by chamber for clarity
        house_drafts = [d for d in new_drafts if d["chamber"] == "House"]
        senate_drafts = [d for d in new_drafts if d["chamber"] == "Senate"]

        summary_lines = [
            f"## 📋 {len(new_drafts)} new significant votes detected",
            "",
            f"_Lookback window: past {LOOKBACK_DAYS} days · "
            f"🏛 {len(senate_drafts)} Senate · "
            f"🏠 {len(house_drafts)} House_",
            "",
            "These were surfaced from final-passage roll-call votes ",
            f"with margins ≤ {MARGIN_CEILING}.",
            "",
        ]

        def format_draft_section(d, n):
            yea = len(d["yes_votes"])
            nay = len(d["no_votes"])
            chamber_emoji = "🏛" if d["chamber"] == "Senate" else "🏠"
            return [
                f"#### {n}. {chamber_emoji} **{d['chamber']}** — {d['id']}",
                "",
                f"**{d['title']}**",
                "",
                f"- **Date:** {d['date']}",
                f"- **Vote:** {yea}-{nay} ({d['status']})",
                f"- **Sponsor:** {d['sponsor']}",
                f"- **Bill on Congress.gov:** {d['_meta']['bill_url']}",
                f"- **Source:** {d['_meta']['source']}",
                "",
                "**To publish this:**",
                f"1. Open `data/bills_drafts.json`, find entry with id `{d['id']}`",
                "2. Copy the JSON object",
                "3. Open `data/bills.json`, paste at end of array (before closing `]`)",
                "4. Fill in TODO fields: `category`, `description`, `key_provisions`, `donors_for`, `donors_against`, and `sponsor` if Senate",
                "5. Remove the `_meta` field (internal-only)",
                "6. Commit. Optionally also delete the entry from `bills_drafts.json`.",
                "",
                "---",
                "",
            ]

        # Senate first (typically more nationally significant)
        if senate_drafts:
            summary_lines.extend([
                "## 🏛 Senate drafts",
                "",
            ])
            for i, d in enumerate(senate_drafts, 1):
                summary_lines.extend(format_draft_section(d, i))

        if house_drafts:
            summary_lines.extend([
                "## 🏠 House drafts",
                "",
            ])
            offset = len(senate_drafts)
            for i, d in enumerate(house_drafts, 1):
                summary_lines.extend(format_draft_section(d, offset + i))

        summary_lines.extend([
            "### What if I don't want any of these?",
            "Close this issue. Drafts stay in `bills_drafts.json` for future reference but won't appear on the live site.",
            "",
            "### Coverage notes",
            "- House votes: from Congress.gov API (clean structured data)",
            "- Senate votes: scraped from senate.gov XML (may have some parsing edge cases)",
            "- Sponsor field for Senate drafts is blank — Senate XML doesn't include it. Look up on Congress.gov when filling in.",
        ])
        summary = "\n".join(summary_lines)

        # Write to a file the workflow can pick up for issue body
        Path("/tmp/bills_summary.md").write_text(summary) if os.environ.get("GITHUB_ACTIONS") else None
        # Also output for non-actions runs
        print("\n" + "="*60)
        print("MARKDOWN SUMMARY (for GitHub Issue):")
        print("="*60)
        print(summary)
    else:
        print("\nNo new drafts this week.", flush=True)
        # Mark for workflow to skip issue creation
        if os.environ.get("GITHUB_ACTIONS"):
            Path("/tmp/no_drafts").touch()


if __name__ == "__main__":
    main()
