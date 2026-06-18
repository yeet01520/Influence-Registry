# Methodology
## Editorial Perspective

The Influence Registry is built from a clear point of view: that the
volume of corporate and special-interest money in U.S. politics distorts
representative democracy and deserves public scrutiny. This belief shapes
which data we choose to surface, how we frame it, and what we name our
metrics.

What this means in practice:

- **The data is verifiable.** Every dollar amount, donation total, and
  voting record on this site comes from public, free, government or
  nonprofit sources (FEC, OpenSecrets, TrackAIPAC, ProPublica, Bioguide).
  Nothing is invented. Citations link back to original filings wherever
  possible. We will correct factual errors immediately when they are
  reported.

- **The framing is editorial.** Decisions like calling our composite the
  "Corporate Money Score," labeling certain ranges "high risk," and
  emphasizing the volume of corporate PAC dollars over other measures
  reflect our perspective that this money matters and the public should
  see it clearly. Reasonable people can disagree with these editorial
  choices; we believe they are defensible and we welcome the debate.

- **The data and the framing are separable.** Journalists, researchers,
  and citizens who do not share our perspective can still use the
  underlying data. All raw figures are available in their original form
  via [data downloads](#downloads), and our scoring methodology is
  documented in full below so that anyone can reconstruct or critique it.

We are not affiliated with any political party, campaign, or advocacy
organization. The maintainer is an individual citizen building this in
their spare time. See [About the maintainer](#about) for personal
disclosures.

This document describes how the Influence Registry classifies, scores, and validates
the data published in `/data/*.json`. It is written for researchers, journalists,
and other consumers of the JSON data who need to understand exactly what each field
represents and how editorial judgments are made.

The Registry currently covers all 536 voting members of the 119th US Congress,
plus the 9 sitting Supreme Court justices and 25 members of the Executive
Cabinet. Numerical totals are career-cumulative unless otherwise noted, drawing
primarily from FEC filings and OpenSecrets aggregates for cycles 1990–2024.

---

## 1. Sector classification: corporate vs ideological

We classify donations into two broad buckets that drive the editorial scoring.

### Corporate

The `corporate_total` field is the sum of OpenSecrets industry sectors
classified as **business activity**, specifically:

- Energy & Natural Resources (oil/gas, mining, electric utilities)
- Finance, Insurance & Real Estate (banking, hedge funds, private equity, insurance, real estate)
- Health (pharmaceuticals, HMOs, hospitals, health professionals)
- Communications/Electronics (tech companies, telecom, media conglomerates)
- Defense
- Agribusiness
- Transportation
- Construction

The following OpenSecrets sectors are **excluded** from `corporate_total`:

| Excluded sector            | Why |
| -------------------------- | --- |
| **Ideology / Single-Issue**| Issue advocacy (gun rights, environmental, civil liberties, Israel-related PACs); ideological motive distinct from commercial interest |
| **Lawyers & Lobbyists**    | Heterogeneous category mixing trial lawyers, public-interest law, and lobbyist-employer attribution; does not cleanly map to commercial sector influence |
| **Labor**                  | Worker organizations, structurally opposed to most corporate interests |
| **Other**                  | OpenSecrets residual catch-all; non-categorizable contributions |

### Ideological

Money tracked under the Ideology/Single-Issue umbrella is reported in dedicated
fields, not folded into `corporate_total`. The most prominent of these is AIPAC
(see §2). Other ideological sources (NRA, environmental groups, civil liberties
PACs) are tracked but not yet used in scoring.

### Why this distinction matters

The Registry's editorial position is that *commercial-sector influence* and
*ideological-issue influence* are functionally different forms of money in
politics. A senator funded by Exxon and a senator funded by AIPAC have both
taken money from organized advocacy, but the policy-purchase relationships,
disclosure regimes, and public-accountability dynamics differ enough to warrant
separate columns. Folding them together would conceal more than it reveals.

---

## 2. AIPAC treatment

AIPAC is given its own first-class field rather than being absorbed into either
the corporate sector totals or the generic ideological bucket. There are three
reasons:

1. **It is a registered PAC.** The American Israel Public Affairs Committee
   operates United Democracy Project (UDP), a Super PAC that makes direct
   independent expenditures in primaries, distinguishing it from many other
   ideological aggregators that work through bundled individual donations.

2. **The expenditure data is exceptionally trackable.** Third-party trackers
   (TrackAIPAC) maintain real-time per-member totals. We cross-validate against
   FEC filings to catch divergence (validator check 1).

3. **It is the single largest ideological PAC bloc** by independent expenditure
   in recent cycles, large enough that aggregating it into a generic
   "ideological" bucket would lose signal.

### How AIPAC is scored

AIPAC contributions count toward the `superPacTotal` field on every member
(because UDP is a registered Super PAC) and contribute equally-weighted points
to the corruption score for non-grassroots Congress members (see §4).

AIPAC is **not** counted toward `corporate_total`. A member can have
`corporate_total: "$0 corporate PAC"` and a six-figure AIPAC value without
contradiction.

### Sources

- **`AIPAC_DATA`** in `data/aipac.json`. Career totals per member, the
  display-time source of truth.
- **`FEC_V8_DATA[name].aipac`** in `data/fec.json`. Same value, cross-stored
  for validator consistency checks.
- **`trackaipac_page.txt`**. Raw scrape from TrackAIPAC; the validator
  (check 2) confirms `AIPAC_DATA` matches this source within tolerance.

---

## 3. Grassroots badge

44 members currently carry the grassroots badge (7 Senators and 37
Representatives in the 119th Congress; see `data/tags.json` →
`grassroots`). The badge signals that a member's funding base is meaningfully
small-dollar and free of corporate PAC money.

### Inclusion criteria

A member is eligible for the grassroots tag if they meet **all** of the following:

1. **No corporate PAC money taken.** Either an explicit pledge (Justice
   Democrats, no-corporate-PAC pledge signers) or de-facto absence in OpenSecrets'
   PAC contribution data.
2. **Donor base dominated by individual small-dollar contributions.**
   Operationally: ActBlue/WinRed individual contributions form the majority of
   raised money.
3. **`corporate_total` field reads `$0 corporate PAC`** or equivalent zero-label.

### What grassroots does NOT mean

The badge does not mean a member has zero pharma, tech, or defense money in their
FEC sector totals. Many grassroots members have substantial values in those fields
because **FEC sector totals are employment-categorized individual donations, not
corporate PAC checks.** Bernie Sanders shows ~$20M in pharma career donations;
those are doctors, nurses, and pharma-industry employees giving $50–$250 each
over 30 years. They are not pharmaceutical company PAC money, and treating them
as evidence of corruption would be a category error.

This is enforced by validator check 4 (`check_grassroots_integrity`), which
flags grassroots members for manual review only on extreme outliers, currently
defined as fossil-fuel donations exceeding $1M or appearance on a manually-curated
"known pledge breakers" list (presently empty).

### Maintained list

`EXPLICIT_CLEAN_MEMBERS` (in `data/tags.json` → `explicit_clean`) is a
hand-maintained allowlist of 18 members with documented public commitments to
refuse corporate PAC money. Every name on this list is also in the grassroots
list; the explicit-clean list exists to record provenance ("we know they pledged
this publicly, not just that the data looks clean").

---

## 4. Special Interest Money Score formula

The `pct` field returned by `calcScore(name, prof)` is a 0-100 score that
summarizes how much of a member's campaign money comes from non-individual
(PAC and organized) sources versus individual donors. As of the v3 ("B2")
methodology, the score is driven by the **funding mix** (the share of money
that is non-individual), not by the raw dollar amount of sector money.

### Why the formula changed

The previous formula scored members on the raw dollar total of money
classified into corporate sectors (oil/gas, pharma, finance, tech, defense).
That approach had a structural flaw: FEC and OpenSecrets sector totals are
**employer-classified individual donations**, not corporate PAC checks. A
donation from a person who works at Google is counted as "tech" money even
though it is an individual contribution capped at the personal limit. As a
result, big grassroots fundraisers were penalized for the size of their
individual donor base. Two candidates who each raised $20M could score very
differently or very similarly in ways that did not reflect how much organized
money was actually behind them.

The v3 formula fixes this by measuring the **proportion** of money that is
non-individual, so that a candidate who raised $20M with 99% from individuals
scores far lower than one who raised $20M with 50% from individuals.

### The formula

```
non_individual = max(total_raised - grassroots, 0) + aipac
denom          = total_raised + aipac
score          = round( min( (non_individual / denom) * 150, 100 ) )
```

Where:

- `total_raised` and `grassroots` (individual contributions, itemized +
  unitemized) come from the candidate's FEC committee totals.
- `aipac` is the member's AIPAC-aligned PAC and independent-expenditure total
  from TrackAIPAC. Because this is genuine organized PAC/IE money (not
  employer-classified individual donations), it is folded in as fully
  non-individual: added to both the numerator and the denominator.
- The other tracked sectors (oil/gas, pharma, finance, tech, defense, NRA) are
  **not** added as raw dollars. They are already contained within
  `total_raised`/`grassroots`, and their influence is captured through the
  individual-vs-non-individual split. They are still listed in the `reasons`
  field for transparency about which industries a member's donors work in.
- The `* 150` multiplier sets the curve so that a 50/50 individual-to-PAC split
  scores roughly 75. The result is capped at 100.

### Bucket labels

| Score   | Label          |
|---------|----------------|
| 0-19    | Clean          |
| 20-39   | Low Risk       |
| 40-59   | Some Corporate |
| 60-79   | Moderate Risk  |
| 80-100  | High Risk      |

### Special branches (unchanged in spirit)

- **Appointed members** (Cabinet, etc.) with no federal campaign history are
  reported as N/A rather than scored.
- **SCOTUS justices and Cabinet members** receive a manual editorial score
  based on documented ethics issues, since they file no campaign finance data.
  These are the only roles that still use a manual baseline.
- **Grassroots-tagged members** are scored by the standard v3 formula, the same
  as everyone else. Earlier methodology gave them a static editorial baseline to
  protect them from a formula that over-counted employer-classified individual
  money; the v3 formula already scores individual-funded members low, so that
  protective baseline has been retired and they are now scored on their actual
  funding mix.
- **Challengers** are scored by the standard v3 formula on their federal
  campaign finance, with no manual baseline. A brand-new challenger who is
  overwhelmingly individual-funded will land at a low computed score on their
  own, and a challenger backed by significant PAC money will score higher,
  exactly as the formula intends.

### What the score does and does not measure

The Special Interest Money Score measures **funding mix only**: the share of a
candidate's committee money that comes from organized/PAC sources versus
individual donors. It is not a corruption score, a character score, or a
measure of a candidate's record. Two important limits follow from this:

1. **Super PAC and dark money are not in this score.** Federal law caps
   individual contributions to a campaign committee, so the largest organized
   spending often flows through super PACs and independent-expenditure groups
   that never touch the candidate's committee. A candidate heavily backed by
   such groups can still show a low score on this metric. That outside spending
   is surfaced separately (see the outside-spending data, FEC Schedule E) and
   shown on member profiles.
2. **Ethics and legal issues are documented separately.** Impeachments,
   indictments, conflicts of interest, and similar matters live in each
   member's Controversies tab, not in this score.

## 5. Data sources and dating

| Data | File | Source | Vintage |
| ---- | ---- | ------ | ------- |
| AIPAC career totals | `aipac.json` | TrackAIPAC + FEC | through latest filing cycle |
| FEC sector totals | `fec.json` | OpenSecrets bulk + FEC API | cycles 1990–2024 |
| Editorial donation strings | `profiles.json` → `donations` | OpenSecrets per-cycle screenshots, manually compiled | per-member, dated where unclear |
| Member rosters | `senate.json`, `house.json` | 119th Congress official rolls | as of 119th Congress convening |
| SCOTUS / Cabinet | `court.json`, `cabinet.json` | Public records, manual | current as of last edit |
| Bills | `bills.json` | Congress.gov | manually tracked |
| Bioguide IDs | `bioguide.json` | bioguide.congress.gov | static |
| Photos | `photo_overrides.json` + runtime fetch from unitedstates.github.io | Wikipedia + Congressional CDN | latest available |

Source-data dating is currently inconsistent across fields. Adding explicit
`as_of` dates per record is on the roadmap (see §7).

---

## 6. Known limitations

### Numerical estimates labeled as such

The Registry favors source-verified data wherever possible. When OpenSecrets
aggregate data is incomplete (typical for newly-elected federal candidates),
we use FEC Schedule A directly as the authoritative source.

- **Wesley Bell** (D-MO-01): sector and corporate values were originally
  estimated at "$1.5M career" from a partial OpenSecrets floor (~$777K visible)
  scaled by a 2× heuristic. As of the latest update, these have been replaced
  with figures derived directly from FEC Schedule A (June 2023 to March 2026).
  The verified `corporate_total` is **$2.7M career**, with full per-sector
  breakdowns updated accordingly. Bell's `aipac` value of $3,882,993 reflects
  the current TrackAIPAC tally (which periodically recalculates as new filings
  are processed); this superseded an earlier value of $4,048,977 saved at an
  earlier scrape.

  Methodology note: Schedule A classification is performed by mapping each
  contributor's `contributor_employer` and `contributor_occupation` fields to
  OpenSecrets-equivalent sectors (Finance/Insurance/Real Estate, Health,
  Tech/Communications, Defense, Energy, Misc Business). Excluded categories
  (Lawyers/Lobbyists, Labor, Education, Government, Nonprofit) follow the
  methodology in §1. Approximately 7% of individual donations remain
  unclassified due to ambiguous employer names; the verified figure represents
  the floor.

If you find another number that looks like an estimate without being labeled
as such, please flag it (see §8). The roadmap (§7) includes adding explicit
`confidence` fields to distinguish verified, derived, and estimated values.

### Things the Registry does not currently track

- **Independent expenditures by 501(c)(4) "dark money" groups**, except where
  they appear in OpenSecrets aggregates.
- **Bundling networks** beyond what is reported on FEC filings.
- **Foreign sources** of campaign or speaking-fee income.
- **Post-office consulting and board appointments**. Only a small number of
  high-profile cases are noted in `controversies`.
- **State and local elected officials**. Federal only.

### Methodological limitations inherited from sources

- **OpenSecrets sector classifications** carry their own editorial choices
  (which company belongs to which sector) that the Registry inherits.
- **FEC categorizes individual donations by employer**, which means a
  Stanford professor donating $500 is categorized as "Education" sector
  while a Pfizer scientist donating $500 is "Pharma". Neither is corporate
  PAC money, but the FEC totals lump them.
- **Career-cumulative totals span very long timeframes** (some members have
  30+ year careers). Inflation-adjusting these would change rankings; we do
  not currently inflation-adjust.

---

## 7. Editorial process and roadmap

### What is automated

Ten validator checks run on every push (see `validate.py`):

1. AIPAC three-source consistency (`AIPAC_DATA` ↔ `FEC_V8_DATA` ↔ TrackAIPAC)
2. AIPAC totals match TrackAIPAC source file
3. FEC sector totals match OpenSecrets CSV sources
4. Grassroots badge integrity (extreme outliers flagged for manual review)
5. FEC coverage (every member has an FEC record)
6. Score sanity (high-money non-grassroots members must have proportionate scores)
7. `special_interest_total` aggregate math
8. Card-display sector strings match `FEC_V8_DATA` numbers (within 10% / $50K)
9. `corporate_total` format and arithmetic (parsed value ≥ sum of visible sectors)
10. Phase 2 marker: data files exist and parse correctly

CI rejects any commit that fails these checks. The validator is not a substitute
for editorial judgment. It catches inconsistencies, not editorial errors.

### What is manual

- Initial classification (grassroots / clean / corporate-funded) for new members
- `corruption_score` baseline values for grassroots, SCOTUS, and Cabinet
- `corruption_reasoning` text
- `controversies` list curation
- `key_votes` selection
- Inclusion or exclusion of a `top_donor` entry

### Planned upgrades (not yet implemented)

- **Data lineage fields**. Replace flat strings with `{value, source, method,
  verified, confidence}` objects so estimates and verified values are
  distinguishable in the JSON itself.
- **JSON Schema validation**. Formal schemas for every file in `/data`,
  enforced in CI.
- **Source-data versioning**. Explicit `as_of` dates per record and a
  manifest pinning the OpenSecrets cycle vintage.
- **Methodology version stamping**. This document gets a version number
  and a changelog when scoring rules change.

---

## 8. Reporting errors and contesting classifications

The Registry treats accuracy as a higher priority than any editorial
position. If you believe a number is wrong, a classification is
mistaken, or a methodological choice produces a misleading result:

1. **Open an issue on GitHub** with the member name, the field in question,
   and the source you believe contradicts our value. PRs with corrections are
   welcome.
2. **For sensitive corrections** (e.g., a controversy entry you believe to
   be defamatory or factually wrong), open a private channel via the contact
   address listed on the site rather than a public issue.
3. **For methodology disagreements** (e.g. you think labor PACs should be
   counted as corporate, or AIPAC should be folded into ideology), open an
   issue tagged `methodology`. We treat methodology as contestable; specific
   numerical errors are bugs, but methodology choices are arguments.

We commit to:

- Responding to factual corrections within a reasonable window.
- Documenting any methodology change in this file's revision history.
- Not silently rewriting historical numbers; corrections are commits with
  reasoning, not retroactive edits.

---

## Appendix: file-by-file schema reference

Brief shape of each `data/*.json` file. All keys are member full names
(e.g. `"Chuck Schumer"`, `"Marjorie Taylor Greene"`) unless noted.

| File | Type | Contents |
| ---- | ---- | -------- |
| `senate.json` | array | 100 senator records (id, name, party, state, etc.) |
| `house.json` | array | 436 representative records |
| `court.json` | array | 9 SCOTUS justice records |
| `cabinet.json` | array | 25 Cabinet member records |
| `bills.json` | array | 44 tracked bills with sponsors, votes, donor totals |
| `profiles.json` | object | 636 per-member detail records (donations, votes, controversies, corruption_score, corruption_reasoning) |
| `aipac.json` | object | name → AIPAC career $ (integer) |
| `fec.json` | object | name → FEC v8 record (sector breakdowns, special_interest_total) |
| `sectors.json` | object | bundle of seven name → $ objects (fossil, pharma, defense, finance, tech, nra, grassroots) |
| `tags.json` | object | bundle of seven name lists (pharma_names, tech_names, defense_names, finance_names, grassroots, no_stock, explicit_clean) |
| `bioguide.json` | object | name → bioguide ID |
| `birth_dates.json` | object | name → ISO date |
| `photo_overrides.json` | object | name → base64 JPEG image (used when CDN photo is missing or wrong) |
| `corporate.json` | array | top corporate entities tracked separately on the Corporate tab |
| `sector_counts.json` | object | aggregate sector totals across the body |
