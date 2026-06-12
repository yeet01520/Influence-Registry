#!/usr/bin/env python3
"""
generate_static_pages.py

Generates static HTML profile pages for The Influence Registry.

For each official in the people-list files (senate.json, house.json, court.json,
cabinet.json), this script writes /member/<slug>/index.html containing:

  * Rich server-rendered content: name, role, donations, controversies,
    foreign ties, pardons. Indexable by Google.
  * Per-profile meta tags (title, description, OG, Twitter Card, canonical).
  * Person + WebPage JSON-LD schema.
  * A small hydration script that, when the SPA loads, opens this person's
    modal so users get the full app experience.

Outputs are written under ./member/<slug>/index.html relative to repo root.
Designed to run in GitHub Actions on every push that changes data.

Usage:
    python3 scripts/generate_static_pages.py
    python3 scripts/generate_static_pages.py --only "Donald Trump"   # single profile
    python3 scripts/generate_static_pages.py --dry-run               # no file writes
"""
import argparse
import html as html_lib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


# ─── Configuration ──────────────────────────────────────────────────────────────

SITE_URL = "https://www.keep-dc-honest.com"
ORG_NAME = "The Influence Registry"
DATA_DIR = Path("data")
OUTPUT_DIR = Path("member")
SITEMAP_PATH = Path("sitemap.xml")

# Files we read. All optional except people-list and profiles.
PEOPLE_LIST_FILES = ["senate.json", "house.json", "court.json", "cabinet.json"]
PROFILE_FILE = "profiles.json"
FEC_FILE = "fec.json"
FOREIGN_TIES_FILE = "foreign_ties.json"
PARDONS_FILE = "pardons.json"
OUTSIDE_FILE = "outside_spending.json"
AIPAC_FILE = "aipac.json"
PHOTO_OVERRIDES_FILE = "photo_overrides.json"


# ─── Utility ────────────────────────────────────────────────────────────────────

def slugify(name: str) -> str:
    """Convert a person's name to a URL-safe slug. 'Marco Rubio' -> 'marco-rubio'."""
    s = (name or "").lower()
    s = re.sub(r"['\u2018\u2019]", "", s)          # strip apostrophes
    s = re.sub(r"[^a-z0-9]+", "-", s)              # everything else to dashes
    s = s.strip("-")
    return s


def esc(s) -> str:
    """HTML-escape a value, treating None and non-strings safely."""
    if s is None:
        return ""
    return html_lib.escape(str(s), quote=True)


def load_json(path: Path, default=None):
    """Load a JSON file; return default if missing."""
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def normalize_url(url: str) -> str:
    """Ensure a URL is absolute. Treat /-leading paths as site-relative."""
    if not url:
        return ""
    if url.startswith("http"):
        return url
    if url.startswith("/"):
        return SITE_URL + url
    return SITE_URL + "/" + url


# ─── Data loading ───────────────────────────────────────────────────────────────

def load_all_data(data_dir: Path) -> dict:
    """Load every data file we render from. Returns a dict of normalized data."""
    data = {}
    # People-list files: each is a list of records or an object keyed by name
    people = {}
    for fn in PEOPLE_LIST_FILES:
        path = data_dir / fn
        chamber_label = fn.replace(".json", "")
        contents = load_json(path, default=None)
        if contents is None:
            print(f"  [warn] {fn} not found at {path}; skipping", file=sys.stderr)
            continue
        # Support both list and dict shapes
        if isinstance(contents, dict):
            records = list(contents.values())
        else:
            records = contents
        for rec in records:
            if not isinstance(rec, dict):
                continue
            name = rec.get("name") or rec.get("full_name")
            if not name:
                continue
            people[name] = {**rec, "_chamber_file": chamber_label}
    data["people"] = people
    data["profiles"] = load_json(data_dir / PROFILE_FILE, default={}) or {}
    data["fec"] = load_json(data_dir / FEC_FILE, default={}) or {}
    data["foreign_ties"] = load_json(data_dir / FOREIGN_TIES_FILE, default={}) or {}
    data["pardons"] = load_json(data_dir / PARDONS_FILE, default={}) or {}
    data["outside_spending"] = load_json(data_dir / OUTSIDE_FILE, default={}) or {}
    data["aipac"] = load_json(data_dir / AIPAC_FILE, default={}) or {}
    data["photo_overrides"] = load_json(data_dir / PHOTO_OVERRIDES_FILE, default={}) or {}
    data["bioguide"] = load_json(data_dir / "bioguide.json", default={}) or {}
    # Pre-computed scores (from scripts/compute_scores.py). Single source of truth.
    data["scores"] = load_json(data_dir / "scores.json", default={}) or {}
    if not data["scores"]:
        print(f"  [warn] scores.json not found; static pages will not show scores. "
              f"Run scripts/compute_scores.py first.", file=sys.stderr)
    return data


def resolve_photo_url(name: str, person: dict, data: dict) -> str:
    """
    Mirror avHTML() photo-resolution logic from index.html:
      1. WIKI_PHOTO_OVERRIDES (data/photo_overrides.json): manual mapping, highest priority
      2. Bioguide-derived URL for Congress members with a bioguide ID
      3. Fallback to the generic OG preview image
    """
    overrides = data.get("photo_overrides") or {}
    bioguide  = data.get("bioguide") or {}
    if name in overrides and overrides[name]:
        url = overrides[name]
        return url if url.startswith("http") else normalize_url(url)
    bid = bioguide.get(name)
    if bid:
        return f"https://unitedstates.github.io/images/congress/225x275/{bid}.jpg"
    return f"{SITE_URL}/assets/og-preview.png"


def resolve_share_card_url(slug: str) -> str:
    """
    If a pre-rendered share card exists at assets/share-cards/{slug}.png in
    the working directory, return its public URL. Otherwise return None so
    the caller can fall back to the headshot for og:image.

    Share cards are produced by scripts/render_share_cards.js (Playwright).
    They are 1080x1080 PNGs matching the user-downloadable card visually.
    """
    card_path = Path("assets") / "share-cards" / f"{slug}.png"
    if card_path.exists():
        return f"{SITE_URL}/assets/share-cards/{slug}.png"
    return None


# ─── Rendering pieces ───────────────────────────────────────────────────────────

def role_descriptor(person: dict) -> tuple:
    """
    Return (role_label, location_label) for a person.
    Prefers the explicit 'position' field over chamber-based defaults.
    Mirrors the live-site display logic so the static page header matches the SPA.
    """
    explicit = person.get("position") or person.get("title") or person.get("role") or ""
    chamber = person.get("_chamber_file", "")
    state = person.get("state") or ""
    district = person.get("district") or person.get("district_number") or ""

    # For specific positions (President, Vice President, Speaker, etc.) — use as-is, no state.
    EXECUTIVE_OR_TITLED = ("President", "Vice President", "Speaker", "Chief Justice",
                          "Associate Justice", "Justice", "Secretary", "Attorney General",
                          "Director")
    if explicit and any(explicit.startswith(t) for t in EXECUTIVE_OR_TITLED):
        return (explicit, "")

    # Congress members: chamber-based label + state/district
    if chamber == "senate":
        return ("U.S. Senator", state)
    if chamber == "house":
        return ("U.S. Representative", f"{state}-{district}" if district else state)
    # Court: explicit title or default
    if chamber == "court":
        return (explicit or "Supreme Court Justice", "")
    # Cabinet: prefer the explicit position over generic "Cabinet Official"
    if chamber == "cabinet":
        return (explicit or "Cabinet Official", state)
    # Fallback
    return (explicit, state)


def render_meta_block(person: dict, profile: dict, score_info: dict, slug: str) -> str:
    """The <head> meta tags specific to this profile."""
    name = person.get("name") or ""
    role_str, location_label = role_descriptor(person)
    if location_label:
        role_full = f"{role_str}, {location_label}"
    else:
        role_full = role_str

    title = f"{name} | {role_full} | {ORG_NAME}" if role_full else f"{name} | {ORG_NAME}"

    # Description: lead with role, then score (from scores.json), then net worth
    bio = profile.get("bio") or ""
    desc_parts = []
    if role_full:
        desc_parts.append(role_full)
    score_pct = (score_info or {}).get("pct")
    if (score_info or {}).get("no_campaign") or (score_info or {}).get("lbl") == "N/A":
        desc_parts.append("Special Interest Money Score: N/A (no campaign data)")
    elif score_pct is not None:
        desc_parts.append(f"Special Interest Money Score: {score_pct}/100")
    networth = profile.get("net_worth")
    if networth:
        desc_parts.append(f"Net worth: {networth}")
    description = ". ".join(desc_parts) + "." if desc_parts else (
        bio[:150] if bio else
        f"{name}'s campaign donations, voting record, ethics disclosures, and conflicts of interest from {ORG_NAME}."
    )
    description = description[:300]

    canonical_url = f"{SITE_URL}/member/{slug}"

    return f"""<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{esc(canonical_url)}">

<!-- ── Open Graph / Social Preview ── -->
<meta property="og:type"        content="profile">
<meta property="og:locale"      content="en_US">
<meta property="og:site_name"   content="{esc(ORG_NAME)}">
<meta property="og:url"         content="{esc(canonical_url)}">
<meta property="og:title"       content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:image"       content="{esc(person.get('_share_card_url', ''))}">
<meta property="og:image:alt"   content="{esc(name)}, {esc(role_full)}">
<meta property="og:image:width" content="1080">
<meta property="og:image:height" content="1080">
<meta property="profile:first_name" content="{esc(name.split(' ')[0] if name else '')}">
<meta property="profile:last_name"  content="{esc(name.split(' ')[-1] if name else '')}">

<!-- ── Twitter / X Card ── -->
<meta name="twitter:card"        content="summary_large_image">
<meta name="twitter:site"        content="@keepdchonest">
<meta name="twitter:title"       content="{esc(title)}">
<meta name="twitter:description" content="{esc(description)}">
<meta name="twitter:image"       content="{esc(person.get('_share_card_url', ''))}">
<meta name="twitter:image:alt"   content="{esc(name)}, {esc(role_full)}">

<meta name="robots" content="index, follow">
<meta name="author" content="{esc(ORG_NAME)}">"""


def render_jsonld(person: dict, profile: dict, slug: str) -> str:
    """Build Person + WebPage JSON-LD schema for this profile."""
    name = person.get("name") or ""
    role_str, _ = role_descriptor(person)
    photo_url = person.get("_resolved_photo_url") or ""
    canonical_url = f"{SITE_URL}/member/{slug}"

    person_node = {
        "@type": "Person",
        "name": name,
        "url": canonical_url,
    }
    if role_str:
        person_node["jobTitle"] = role_str
    if photo_url:
        person_node["image"] = photo_url
    if profile.get("bio"):
        person_node["description"] = profile["bio"][:500]
    person_node["worksFor"] = {
        "@type": "GovernmentOrganization",
        "name": "United States Federal Government"
    }
    party = person.get("party")
    if party:
        person_node["affiliation"] = {
            "@type": "PoliticalParty",
            "name": party
        }

    page_node = {
        "@type": "WebPage",
        "url": canonical_url,
        "name": f"{name} | {ORG_NAME}",
        "isPartOf": {"@type": "WebSite", "url": SITE_URL, "name": ORG_NAME},
        "about": {"@id": f"{canonical_url}#person"},
        "publisher": {"@type": "Organization", "name": ORG_NAME, "url": SITE_URL},
    }
    person_node["@id"] = f"{canonical_url}#person"

    graph = {"@context": "https://schema.org", "@graph": [person_node, page_node]}
    return '<script type="application/ld+json">\n' + json.dumps(graph, indent=2) + "\n</script>"


def render_donations(profile: dict, fec_record: dict) -> str:
    """Render donation breakdown as a real HTML table. FEC data takes precedence.

    IMPORTANT: FEC values of exactly $0 are authoritative, not "missing." A
    member who takes $0 from a sector must display $0, not silently fall back
    to a potentially stale value in profiles.json. The presence of the field
    in fec_record (regardless of value) means FEC is the source of truth for
    that sector; profile-level fallbacks only apply when FEC has no entry at
    all. See the Massie $14,650 incident (May 2026): the patched fec.json
    correctly showed aipac=$0, but the OLD `if v:` check treated zero as
    falsy and fell back to a stale profiles.json AIPAC value, leaving the
    bug visible on the live site for hours after the data fix shipped.
    """
    donations = profile.get("donations") or {}
    if not donations and not fec_record:
        return ""

    rows = []
    def row(label, value, source=""):
        if value is None or value == "":
            return
        rows.append(f'<tr><th scope="row">{esc(label)}</th><td>{esc(value)}</td><td class="src">{esc(source)}</td></tr>')

    # Total raised: prefer FEC fresh, then profile.
    # FEC takes precedence whenever the field exists in fec_record at all
    # (including 0), to prevent stale profile data from masking a deliberate
    # zero. Falls back to profile only if FEC has no key for this field.
    if "total_raised" in fec_record:
        row("Total raised (career)", f"${fec_record['total_raised']:,}", "FEC")
    elif donations.get("total_raised"):
        row("Total raised (career)", donations["total_raised"])

    # Detect a "data pending" member (first-term / appointed where OpenSecrets has
    # not published industry breakdowns yet, or an appointed member with no federal
    # campaign history). For these, profiles.json deliberately holds "Pending" or an
    # appointed note in the sector/corporate fields, and FEC sector values are a
    # placeholder 0. We must NOT let the FEC $0 override the profile marker, or the
    # static page will disagree with the live modal (which reads profiles.json).
    corp_marker = str(donations.get("corporate_total") or "").lower()
    pending_member = (
        "pending" in corp_marker
        or "no federal campaign" in corp_marker
        or any(str(donations.get(k)).strip().lower() == "pending"
               for k in ("oil_gas", "pharma", "defense", "wall_street", "tech"))
    )

    # Corporate / special interest total. For a pending member, the profile note
    # ("Data pending..." / appointed note) wins over a placeholder FEC $0 total.
    if pending_member and donations.get("corporate_total") and not fec_record.get("special_interest_total"):
        row("Corporate total", donations["corporate_total"])
    elif "special_interest_total" in fec_record:
        row("Corporate / special interest total", f"${fec_record['special_interest_total']:,}", "FEC + OpenSecrets")
    elif donations.get("corporate_total"):
        row("Corporate total", donations["corporate_total"])

    # Per-sector
    sectors = [
        ("AIPAC / pro-Israel",  "aipac",        "aipac"),
        ("Oil & gas / fossil",  "oil_gas",      "fossil_fuels"),
        ("Pharma / healthcare", "pharma",       "pharma"),
        ("Defense",             "defense",      "defense"),
        ("Wall Street / finance","wall_street", "finance"),
        ("Big Tech",            "tech",         "tech"),
    ]
    for label, prof_key, fec_key in sectors:
        prof_val = donations.get(prof_key)
        # For a pending member, the profile marker wins over a placeholder FEC $0
        # (but a real nonzero FEC value, e.g. AIPAC, still takes precedence).
        if pending_member and fec_key in fec_record and not fec_record[fec_key] and prof_val:
            row(label, prof_val)
            continue
        # Otherwise: if the FEC record has this sector key at all, FEC wins even if 0.
        # Only fall back to profile data when FEC has no entry whatsoever.
        if fec_key in fec_record:
            v = fec_record[fec_key]
            row(label, f"${v:,}", "FEC + OpenSecrets")
        elif prof_val:
            row(label, prof_val)

    if not rows:
        return ""

    return f"""
<section class="profile-section" id="donations">
  <h2>Donations & financial influence</h2>
  <table class="profile-table">
    <thead><tr><th scope="col">Category</th><th scope="col">Amount</th><th scope="col">Source</th></tr></thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
</section>
"""


def render_controversies(profile: dict) -> str:
    """Render documented controversies as a list."""
    items = profile.get("controversies") or []
    if not items:
        return ""
    lis = "\n      ".join(f"<li>{esc(c)}</li>" for c in items if isinstance(c, str))
    if not lis.strip():
        return ""
    return f"""
<section class="profile-section" id="controversies">
  <h2>Controversies & conflicts of interest</h2>
  <ul>
      {lis}
  </ul>
</section>
"""


def render_foreign_ties(name: str, foreign_ties_data: dict) -> str:
    """Render foreign-ties entries if present."""
    entries = (foreign_ties_data or {}).get(name) or []
    if not entries:
        return ""
    rows = []
    for e in entries:
        rows.append(f"""
      <article class="ft-entry">
        <h3>{esc(e.get('title', ''))}</h3>
        <dl>
          {f'<dt>Amount</dt><dd>{esc(e.get("amount", ""))}</dd>' if e.get('amount') else ''}
          {f'<dt>Period</dt><dd>{esc(e.get("period", ""))}</dd>' if e.get('period') else ''}
          {f'<dt>Counterparty</dt><dd>{esc(e.get("entity", ""))}</dd>' if e.get('entity') else ''}
          {f'<dt>Category</dt><dd>{esc(e.get("category", ""))}</dd>' if e.get('category') else ''}
        </dl>
        {f'<p>{esc(e.get("summary", ""))}</p>' if e.get('summary') else ''}
        {f'<p class="src">Source: <a href="{esc(e.get("source_url",""))}" target="_blank" rel="noopener">{esc(e.get("source", "Source"))}</a></p>' if e.get('source_url') else (f'<p class="src">Source: {esc(e.get("source",""))}</p>' if e.get('source') else '')}
      </article>""")
    return f"""
<section class="profile-section" id="foreign-ties">
  <h2>Foreign & financial conflict ties</h2>
  <p class="profile-section-note">Non-campaign-finance money flows (foreign government payments, sovereign wealth investments, crypto holdings, business deals). Distinct from FEC contributions.</p>
  {''.join(rows)}
</section>
"""


def render_pardons(name: str, pardons_data: dict) -> str:
    """Render pardon record if present."""
    entries = (pardons_data or {}).get(name) or []
    if not entries:
        return ""
    rows = []
    for p in entries:
        ptype = (p.get('type') or 'pardon').upper()
        payment = p.get('payment_amount') or ''
        rows.append(f"""
      <article class="pardon-entry">
        <h3>{esc(p.get('name', ''))} <span class="pardon-meta">({esc(p.get('date',''))} · {esc(ptype)})</span></h3>
        {f'<p><strong>Crime:</strong> {esc(p.get("crime", ""))}</p>' if p.get('crime') else ''}
        {f'<p><strong>Sentence wiped:</strong> {esc(p.get("sentence", ""))}</p>' if p.get('sentence') else ''}
        {f'<p class="pay-row"><strong>Payment to pardoner:</strong> {esc(p.get("payment_connection",""))} {f"<em>({esc(payment)})</em>" if payment and "none" not in payment.lower() else ""}</p>' if p.get('payment_connection') else ''}
        {f'<p>{esc(p.get("summary", ""))}</p>' if p.get('summary') else ''}
        {f'<p class="src">Source: <a href="{esc(p.get("source_url",""))}" target="_blank" rel="noopener">{esc(p.get("source","Source"))}</a></p>' if p.get('source_url') else (f'<p class="src">Source: {esc(p.get("source",""))}</p>' if p.get('source') else '')}
      </article>""")
    return f"""
<section class="profile-section" id="pardons">
  <h2>Pardons & commutations</h2>
  <p class="profile-section-note">Federal pardons and commutations granted under Article II of the U.S. Constitution.</p>
  {''.join(rows)}
</section>
"""


def render_outside_spending(name: str, outside: dict) -> str:
    """Render outside spending summary."""
    rec = (outside or {}).get(name)
    if not rec:
        return ""
    sup = rec.get('top_supporters') or []
    opp = rec.get('top_opposers') or []
    total_sup = sum((s.get('amount', 0) or 0) for s in sup)
    total_opp = sum((s.get('amount', 0) or 0) for s in opp)
    if total_sup == 0 and total_opp == 0:
        return ""
    sup_rows = "\n        ".join(
        f'<li>${(s.get("amount",0) or 0):,}: {esc(s.get("committee_name",""))}</li>'
        for s in sup[:5] if (s.get("amount", 0) or 0) > 0
    )
    opp_rows = "\n        ".join(
        f'<li>${(s.get("amount",0) or 0):,}: {esc(s.get("committee_name",""))}</li>'
        for s in opp[:5] if (s.get("amount", 0) or 0) > 0
    )
    return f"""
<section class="profile-section" id="outside-spending">
  <h2>Outside spending</h2>
  <p class="profile-section-note">Independent expenditures by super PACs and outside groups (FEC Schedule E). Not coordinated with the campaign.</p>
  <div class="outside-grid">
    <div>
      <h3>Top supporters (${total_sup:,} total)</h3>
      <ul>
        {sup_rows or '<li class="src">None on record</li>'}
      </ul>
    </div>
    <div>
      <h3>Top opposers (${total_opp:,} total)</h3>
      <ul>
        {opp_rows or '<li class="src">None on record</li>'}
      </ul>
    </div>
  </div>
</section>
"""


# ─── Full page assembly ─────────────────────────────────────────────────────────

PAGE_CSS = """
:root { --bg:#f4f6f9; --surface:#fff; --text:#1a1d23; --t2:#5a6272; --t3:#9099a8; --accent:#1a3a8a; --border:#e2e8f0; }
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);line-height:1.55;font-size:15px}
.profile-static{max-width:880px;margin:0 auto;padding:24px 20px 80px}
.profile-static-noscript{background:#fff8c5;border:1px solid #e5cc70;border-radius:6px;padding:10px 14px;margin-bottom:16px;font-size:13px}
.profile-static a{color:var(--accent)}
.profile-header{display:flex;gap:18px;align-items:flex-start;padding:22px 0 18px;border-bottom:1px solid var(--border);margin-bottom:20px}
.profile-header img{width:96px;height:96px;border-radius:50%;object-fit:cover;flex-shrink:0;border:2px solid var(--border)}
.profile-header-info{flex:1;min-width:0}
.profile-header h1{font-size:28px;margin:0 0 4px;color:var(--text);line-height:1.2}
.profile-header .role{font-size:15px;color:var(--t2);margin:0 0 8px}
.profile-cta{flex-shrink:0;align-self:flex-start;display:inline-block;background:var(--accent);color:#fff !important;font-size:12px;font-weight:600;padding:8px 14px;border-radius:6px;text-decoration:none;letter-spacing:.3px;white-space:nowrap;transition:background .15s}
.profile-cta:hover{background:#13306e}
.profile-static .badges{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
.profile-static .badge{font-size:11px;padding:3px 9px;border-radius:4px;background:#eef1f6;color:var(--t2);font-weight:600;letter-spacing:.3px;text-transform:uppercase}
.profile-summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin-bottom:24px}
.profile-summary .stat{background:#fff;border:1px solid var(--border);border-radius:8px;padding:12px 14px}
.profile-summary .stat-label{font-size:10px;color:var(--t3);letter-spacing:1px;text-transform:uppercase;margin-bottom:4px;font-family:'SF Mono',ui-monospace,monospace}
.profile-summary .stat-value{font-size:18px;font-weight:700;color:var(--text)}
.profile-section{background:#fff;border:1px solid var(--border);border-radius:10px;padding:18px 22px;margin-bottom:14px}
.profile-section h2{font-size:14px;letter-spacing:1.5px;text-transform:uppercase;color:var(--t3);margin:0 0 14px;font-weight:600}
.profile-section h3{font-size:14px;margin:14px 0 6px;color:var(--text)}
.profile-section-note{font-size:12px;color:var(--t3);font-style:italic;margin-bottom:14px}
.profile-table{width:100%;border-collapse:collapse;font-size:13px}
.profile-table th,.profile-table td{padding:8px 4px;text-align:left;border-bottom:1px solid var(--border)}
.profile-table th{font-weight:600;color:var(--t2)}
.profile-table td.src{font-size:11px;color:var(--t3);text-align:right}
.profile-section ul{padding-left:20px;margin:0}
.profile-section li{margin-bottom:6px;font-size:13.5px;color:var(--t2)}
.profile-section .src{font-size:11px;color:var(--t3);margin-top:6px}
.outside-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.ft-entry,.pardon-entry{padding:10px 0;border-bottom:1px dashed var(--border)}
.ft-entry:last-child,.pardon-entry:last-child{border-bottom:none}
.ft-entry dl{display:grid;grid-template-columns:auto 1fr;gap:2px 12px;font-size:12.5px;margin:6px 0}
.ft-entry dt{color:var(--t3);font-weight:600}
.pardon-meta{font-size:11px;color:var(--t3);font-weight:400;letter-spacing:.5px}
.pay-row{background:#fef2f2;border-left:3px solid #b91c1c;padding:8px 10px;margin:8px 0;border-radius:3px}
.profile-footer-note{font-size:12px;color:var(--t3);text-align:center;margin-top:24px;padding-top:18px;border-top:1px solid var(--border)}
@media (max-width:600px){
  .profile-header{flex-direction:column;align-items:flex-start;text-align:left}
  .profile-header img{align-self:center}
  .profile-cta{align-self:stretch;text-align:center}
  .profile-summary{grid-template-columns:1fr 1fr}
  .outside-grid{grid-template-columns:1fr}
}
"""


def render_page(name: str, person: dict, data: dict) -> str:
    """Compose the full HTML for one profile page."""
    profile = (data["profiles"] or {}).get(name) or {}
    fec_record = (data["fec"] or {}).get(name) or {}
    score_info = (data.get("scores") or {}).get(name) or {}
    slug = slugify(name)

    # Resolve photo URL using the same logic as the live SPA (bug fix #1)
    photo_url = resolve_photo_url(name, person, data)
    # Resolve share card URL: 1080x1080 branded preview image. Used for
    # og:image and twitter:image when present, since those are larger and
    # more visually compelling than just the headshot. Falls back to headshot
    # if no card has been rendered for this person yet.
    share_card_url = resolve_share_card_url(slug)
    person = dict(person)  # don't mutate input
    person["_resolved_photo_url"] = photo_url
    person["_share_card_url"] = share_card_url or photo_url  # use card if present, else photo

    # Header
    role_label, location_label = role_descriptor(person)
    party = person.get("party") or ""
    state = person.get("state") or ""
    chamber = person.get("_chamber_file", "")

    badges = []
    if party: badges.append(party)
    if chamber: badges.append(chamber.capitalize())
    if state and chamber not in ("court", "cabinet"): badges.append(state)
    # Suppress state for cabinet/court since their role isn't state-bound

    # Score & stats block (bug fix #3: use authoritative scores.json)
    score_pct = score_info.get("pct")
    score_lbl = score_info.get("lbl")
    networth = profile.get("net_worth")
    years_in_office = profile.get("years_in_office")
    bio = profile.get("bio") or profile.get("politician_bio") or ""

    stats = []
    if score_info.get("no_campaign") or score_lbl == "N/A":
        stats.append(("Score", "N/A · No campaign data"))
    elif score_pct is not None:
        stats.append(("Score", f"{score_pct}/100" + (f" · {score_lbl}" if score_lbl else "")))
    if networth:
        stats.append(("Net worth", networth))
    if years_in_office:
        stats.append(("Tenure", years_in_office))
    stats_html = "\n    ".join(
        f'<div class="stat"><div class="stat-label">{esc(l)}</div><div class="stat-value">{esc(v)}</div></div>'
        for l, v in stats
    )

    # Sections
    donations_html = render_donations(profile, fec_record)
    foreign_html   = render_foreign_ties(name, data.get("foreign_ties"))
    pardons_html   = render_pardons(name, data.get("pardons"))
    outside_html   = render_outside_spending(name, data.get("outside_spending"))
    controversies_html = render_controversies(profile)

    meta_block = render_meta_block(person, profile, score_info, slug)
    jsonld     = render_jsonld(person, profile, slug)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{meta_block}
{jsonld}
<style>{PAGE_CSS}</style>
</head>
<body>
<main class="profile-static">
  <noscript class="profile-static-noscript">
    JavaScript is disabled. You're viewing a static version of {esc(name)}'s profile. <a href="{SITE_URL}/">Visit the full Influence Registry</a> for interactive features.
  </noscript>

  <header class="profile-header">
    <img src="{esc(photo_url)}" alt="{esc(name)}" loading="eager">
    <div class="profile-header-info">
      <h1>{esc(name)}</h1>
      <p class="role">{esc(role_label)}{f' · {esc(location_label)}' if location_label else ''}</p>
      <div class="badges">
        {''.join(f'<span class="badge">{esc(b)}</span>' for b in badges)}
      </div>
    </div>
    <a class="profile-cta" href="{SITE_URL}/#member/{slug}">
      Open full profile →
    </a>
  </header>

  {f'<div class="profile-summary">{stats_html}</div>' if stats else ''}

  {f'<section class="profile-section" id="bio"><h2>About</h2><p>{esc(bio)}</p></section>' if bio else ''}

  {donations_html}
  {foreign_html}
  {pardons_html}
  {outside_html}
  {controversies_html}

  <p class="profile-footer-note">
    Explore the full Influence Registry: <a href="{SITE_URL}/">keep-dc-honest.com</a>
  </p>
</main>
</body>
</html>
"""


# ─── Sitemap ────────────────────────────────────────────────────────────────────

def write_sitemap(slugs: list, output_path: Path):
    """Generate the full sitemap including homepage + all profile pages."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    urls = []
    urls.append(("/", "1.0", "daily", today))
    for slug in sorted(slugs):
        urls.append((f"/member/{slug}", "0.7", "weekly", today))

    body = "\n".join(
        f"""  <url>
    <loc>{SITE_URL}{path}</loc>
    <lastmod>{lastmod}</lastmod>
    <changefreq>{cf}</changefreq>
    <priority>{prio}</priority>
  </url>"""
        for (path, prio, cf, lastmod) in urls
    )
    sitemap = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{body}
</urlset>
"""
    output_path.write_text(sitemap, encoding="utf-8")


# ─── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate static profile pages for The Influence Registry.")
    parser.add_argument("--only", help="Only generate this person's page (for testing)")
    parser.add_argument("--dry-run", action="store_true", help="Don't write any files")
    parser.add_argument("--data-dir", default=str(DATA_DIR), help="Path to data directory")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="Path to output directory")
    parser.add_argument("--sitemap", default=str(SITEMAP_PATH), help="Path to sitemap.xml")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir)
    sitemap_path = Path(args.sitemap)

    print(f"Loading data from {data_dir}/", file=sys.stderr)
    data = load_all_data(data_dir)
    people = data["people"]
    if not people:
        print("ERROR: No people loaded. Check that senate.json/house.json/court.json/cabinet.json exist.", file=sys.stderr)
        return 1
    print(f"  Loaded {len(people)} people from people-list files", file=sys.stderr)
    print(f"  {len(data['profiles'])} profiles, {len(data['fec'])} FEC records", file=sys.stderr)
    print(f"  {len(data['foreign_ties'])} foreign-ties entries, {len(data['pardons'])} pardons entries", file=sys.stderr)

    # Filter to a single person if requested
    targets = {args.only: people[args.only]} if (args.only and args.only in people) else people

    if args.only and args.only not in people:
        print(f"ERROR: '{args.only}' not found in people-list files. Available examples:", file=sys.stderr)
        for n in sorted(list(people.keys())[:10]):
            print(f"  {n}", file=sys.stderr)
        return 1

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    slugs = []
    count = 0
    for name, person in targets.items():
        slug = slugify(name)
        if not slug:
            print(f"  [skip] no valid slug for {name!r}", file=sys.stderr)
            continue
        slugs.append(slug)
        page_html = render_page(name, person, data)
        page_dir = output_dir / slug
        page_file = page_dir / "index.html"
        if args.dry_run:
            print(f"  [dry] would write {page_file} ({len(page_html):,} bytes)", file=sys.stderr)
        else:
            page_dir.mkdir(parents=True, exist_ok=True)
            page_file.write_text(page_html, encoding="utf-8")
        count += 1

    print(f"  Generated {count} profile pages", file=sys.stderr)

    if not args.only:
        if args.dry_run:
            print(f"  [dry] would write sitemap with {len(slugs)+1} URLs to {sitemap_path}", file=sys.stderr)
        else:
            write_sitemap(slugs, sitemap_path)
            print(f"  Wrote sitemap with {len(slugs)+1} URLs to {sitemap_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
