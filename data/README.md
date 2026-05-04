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
