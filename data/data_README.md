# `/data` directory

This folder holds the structured data that powers the Influence Registry,
extracted from the inline JavaScript literals in `index.html`.

## Phase 1 status (current)

**`index.html` is still the source of truth.** Both the inline `const X = {...}`
literals in the HTML AND the JSON files in this folder exist, and they are
required to match exactly. The JSON files are derived from the HTML.

The site does not yet read these files at runtime — they exist so they can be
audited, version-controlled meaningfully (a 1-line diff per change instead of
a giant HTML diff), and so external tools can consume the data.

## How to update data

1. Edit the relevant `const X = {...}` literal in `index.html` as before.
2. Run `python3 regenerate_data.py` to refresh the JSON files.
3. Commit both `index.html` and the changed `data/*.json` files together.

If you forget step 2, the GitHub Action will fail with a clear message telling
you exactly which file is out of sync.

## File layout

| File                       | Source const(s)                                              | Notes                                                     |
| -------------------------- | ------------------------------------------------------------ | --------------------------------------------------------- |
| `senate.json`              | `SENATE_DATA`                                                | 100 members                                               |
| `house.json`               | `HOUSE_DATA`                                                 | 436 members                                               |
| `court.json`               | `COURT_DATA`                                                 | Supreme Court                                             |
| `cabinet.json`             | `CABINET_DATA`                                               | Executive branch                                          |
| `bills.json`               | `BILLS_DATA`                                                 | Tracked legislation                                       |
| `profiles.json`            | `PROFILES_DATA`                                              | Per-member detail (donations, votes, controversies, etc.) |
| `aipac.json`               | `AIPAC_DATA`                                                 | name → AIPAC career $                                     |
| `fec.json`                 | `FEC_V8_DATA`                                                | Per-member FEC sector totals                              |
| `sectors.json`             | `FOSSIL/PHARMA/DEFENSE/FINANCE/TECH/NRA/GRASSROOTS_DATA`     | Bundled name → $ objects                                  |
| `tags.json`                | `*_NAMES` sets + `EXPLICIT_CLEAN_MEMBERS`, `NO_STOCK_NAMES`  | Bundled name lists                                        |
| `bioguide.json`            | `BIOGUIDE`                                                   | name → bioguide ID                                        |
| `birth_dates.json`         | `BIRTH_DATES`                                                | name → ISO date                                           |
| `photo_overrides.json`     | `WIKI_PHOTO_OVERRIDES`                                       | name → base64 image                                       |
| `corporate.json`           | `CORPORATE_DATA`                                             | Top corporate entities tracked                            |
| `sector_counts.json`       | `SECTOR_COUNTS`                                              | Aggregate sector totals                                   |

## What's NOT here (intentionally)

These remain inline in `index.html` because they are application logic /
configuration, not data:

- `CAT_COLORS` — UI color mappings
- `CLEAN_TYPES`, `SUPERPAC_TYPES`, `CORPORATE_PAC_TYPES`, `CORP_KEYWORDS` —
  string-matching pattern lists used by the rendering code
- `ROW_COUNTS` — layout constants

## What changes in later phases

- **Phase 2:** `index.html` will be rewritten to `fetch()` these JSON files at
  runtime and the inline `const X = {...}` literals will be deleted. JSON
  becomes the single source of truth. The HTML shrinks to ~5% of its current
  size.
- **Phase 3:** The validator switches to reading JSON directly instead of
  regex-extracting from HTML. The `check_json_matches_html` step is replaced
  with schema validation against JSON Schema files.

### Added since this table was written

| File | Loaded as | Contents |
|---|---|---|
| `scores.json` | `SCORES_DATA` | Both scores per person: `funding_pct` / `funding_lbl` and `outside_pct` / `outside_lbl`. `pct` mirrors the funding score so older consumers keep working. Built by `scripts/compute_scores.py`; nothing else writes it. |
| `outside_spending.json` | `OUTSIDE_SPENDING_DATA` | Independent expenditure per member from FEC Schedule E, career-wide: `total_supporting`, `total_opposing`, `cycles`, `top_supporters`, `top_opposers`. Built by `scripts/fetch_outside_spending.py`. Deleting this file is what forces a clean rebuild; it doubles as the resume checkpoint. |
| `challengers.json` | `CHALLENGER_DATA` | Non-incumbent candidates above a receipts floor, capped per seat. Built by `scripts/fetch_challengers.py`, which also stubs each new name into `fec.json` so the money pipelines pick them up. |
| `president.json` | `PRESIDENT_DATA` | Inaugural committee donations and independent expenditure for the president. No PAC share: presidential committees may accept only $5,000 per PAC per election. Built by `scripts/fetch_president.py`. |
| `lookup.json` | (landing page) | Small derived file, ~146KB, powering the ZIP lookup on `index.html`. Carries only the fields the verdict rules use, so the landing page does not load the 1.5MB the app does. Built by `scripts/build_lookup.py`. |
| `zip_districts.json` | (landing page) | ZIP-to-congressional-district crosswalk, all ~33,700 ZIPs, compressed by grouping the 1,602 distinct district sets. About 22% of ZIPs span more than one district; every district a ZIP touches is listed. Fetched lazily on first keystroke. |
| `data_audit.json` | — | Output of `scripts/validate_data.py`: errors, warnings and notes from the last validation run. Committed so anyone can read the current state of the data without running anything. |
| `grassroots.json` | `GRASSROOTS_DATA` | Members who have signed a no-corporate-PAC pledge. |
| `no_stock.json` | `NO_STOCK_NAMES` | Members with no recorded stock trading. |
| `nra.json` / `foreign_ties.json` / `pardons.json` | `NRA_DATA`, `FOREIGN_TIES_DATA`, `PARDONS_DATA` | Supplementary per-member fields shown on profiles. |
| `committee_id_audit.json` | — | Findings from `scripts/audit_committee_ids.py`: which committees were stripped, repaired, or need review. |

### Fields inside `fec.json` worth knowing

| Field | Meaning |
|---|---|
| `total_raised` | Career receipts, from `/candidate/{id}/totals/`. **Not** summed from the committees array: FEC does not always link a member's older principal committee, which understated long-serving members by millions. |
| `individual_total`, `pac_total`, `party_total` | Contribution split from FEC candidate totals. These drive the funding score. |
| `pac_pct` | PAC share of contributions. |
| `aipac` | Blended AIPAC total. **No longer used for scoring**; kept for display. |
| `aipac_pacs` | AIPAC money the campaign received. Sits inside `pac_total`. |
| `aipac_ie` | AIPAC independent expenditure. Counts toward the outside score, like any other group's. |
| `ie_support_total`, `ie_oppose_total` | Legacy independent-expenditure fields built with a fixed 2018-2026 window. **Superseded by `outside_spending.json`**, which is career-wide; the two disagreed for 252 members. Safe to remove once nothing reads them. |
| `grassroots`, `grassroots_small`, `small_dollar_pct` | Individual donations and the share under $200. |
| `all_candidate_ids` | Every FEC candidate ID for that member. A member who ran and lost before winning has more than one, and outside spending is queried across all of them. |

### Data integrity

`scripts/validate_data.py` runs on every change to the data files and fails the
build on values that cannot be true: contributions exceeding total receipts, a
stored percentage disagreeing with its own components, `total_raised` of zero, a
roster member missing from `fec.json`, or a roster that has drifted from the
official congress-legislators file in either direction. Warnings and notes do
not fail the build, so the check stays readable rather than permanently red.
