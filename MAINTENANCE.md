# Influence Registry — Data Maintenance Checklist

This site's credibility depends on data freshness. Stale data = users seeing wrong scores = lost trust. This checklist covers what to update, when, and how.

**Quick reference:**

| Frequency | Tasks | Auto? |
|---|---|---|
| Weekly (Mondays 1pm UTC) | Bill detection — surfaces new significant House + Senate votes as drafts | ✓ Auto |
| Monthly (2nd) | FEC totals refresh | ✓ Auto |
| Monthly (~15th, after BLS releases) | **By the Numbers** card updates (9 economic indicators) | Manual |
| Monthly (manual) | Cabinet refresh, scan for new members, validator pass, spot-check 5 profiles | Manual |
| Quarterly (5th of Jan/Apr/Jul/Oct) | Outside spending refresh | ✓ Auto |
| Quarterly (manual) | **Sector data refresh** (TrackAIPAC + OpenSecrets CSVs + run v7), member tags review | Manual |
| Annually (June) | Net worth disclosure update | Manual |
| Annually (January) | By the Numbers narrative refresh (re-anchor baselines and storylines) | Manual |
| Event-driven | New senator/rep, Cabinet change, SCOTUS change, resignation, death | Manual |

**Your minimum monthly habit:** check the Actions tab on the 3rd to confirm both auto-runs succeeded. If green, you're done with the heavy lift. The rest is judgment work that you should do as time allows.

**Important:** Sector data (AIPAC, fossil fuels, pharma, finance, tech, defense) is NOT auto-refreshed. It comes from TrackAIPAC scrapes and OpenSecrets CSV downloads which don't have public APIs. The quarterly sector refresh below requires manual file uploads.

---

## Weekly tasks

### 1. Bill detection (`detect-bills` workflow) — AUTOMATED ✓
**Runs automatically every Monday at 1pm UTC** (8-9am US Eastern). You'll get a GitHub Issue notification each Monday if any new significant votes were detected.

**What it does:** Scans the past 7 days for final-passage roll-call votes in both chambers:
- House: Congress.gov API
- Senate: senate.gov XML feeds

Filters out cloture votes, nominations, post-office namings, commemorative resolutions, and any vote with a margin >100 (uncontested). Adds new draft entries to `data/bills_drafts.json` and opens a GitHub Issue listing them.

**What you do each week:**
1. Check the GitHub Issue (notification arrives Monday morning)
2. For each draft you want to publish:
   - Open `data/bills_drafts.json` and find the entry by id
   - Copy the JSON block
   - Open `data/bills.json` and paste at the end (before `]`)
   - Fill in the TODO fields: `category`, `description`, `key_provisions`, `donors_for`, `donors_against` (and `sponsor` for Senate drafts, since Senate XML doesn't include it)
   - Remove the `_meta` field (internal-only)
   - Commit
3. Drafts you don't want to publish: leave them in `bills_drafts.json` or delete them. Either is fine.
4. Close the Issue when done with the week's review.

**Time estimate:** 5 minutes if no drafts surfaced. 15-30 minutes per draft you decide to publish (the writing/research is the time-consuming part, not the workflow itself).

**Volume expectations:** Weekly volume varies by Congressional schedule. Recess weeks: 0 drafts. Active legislative weeks: 1-3 drafts. Major legislation periods: up to 8 drafts (max set by `MAX_DRAFTS` in the script).

**Coverage caveats:**
- House drafts have full sponsor info auto-populated. Senate drafts have a "TODO: look up sponsor on Congress.gov" placeholder because Senate XML doesn't include sponsor data.
- The bill ID extraction from Senate XML is regex-based. Most votes parse cleanly but unusual vote question phrasings might be skipped silently.
- If senate.gov XML format changes, Senate drafts may break temporarily. House drafts continue working independently.

**Manually trigger:** Actions tab → "Detect Significant Bills" → Run workflow. Useful for backfilling or testing.

---

## Monthly tasks (do on 1st-3rd of each month)

The FEC publishes new monthly data dumps on the 1st. OpenSecrets refreshes around the 20th. Most of this can run unattended via GitHub Actions.

### 2. ~~Refresh FEC totals~~ — AUTOMATED ✓
**Runs automatically on the 2nd of each month at 9am UTC** (4-5am US time, completes overnight).  
**Why monitored:** Watch for failed runs in Actions tab. If FEC API quota or candidate IDs break, the run fails silently otherwise.  
**What to check:** Actions tab → "Refresh Total Raised" → most recent run shows green ✓. If red ✗, click in to see error and re-run manually after fixing.

### 3. By the Numbers — economic indicator update

**Why:** The 9 economic indicator cards on the homepage drive trust. Stale numbers make the site look unmaintained. These need monthly attention because most data sources publish on a monthly cadence.

**Where the data lives:** Search index.html for `window.NCDATA = [` (around line 4170). All 9 cards' values, sparkline data, footer chips, and context narratives are in this single JS array. Edit directly.

**For each card, where to look up the latest number:**

| # | Card | Source | Where to look | Update cadence |
|---|---|---|---|---|
| 1 | Egg prices | BLS Average Price Data | https://data.bls.gov/cgi-bin/surveymost?ap (series APU0000708111) | Monthly, ~15th |
| 2 | Gas prices | AAA / EIA | https://gasprices.aaa.com/ (national avg) | Daily — pick a snapshot date |
| 3 | Inflation (CPI YoY) | BLS CPI Release | https://www.bls.gov/cpi/ (latest table) | Monthly, ~10-15th |
| 4 | Grocery (Food at Home CPI) | BLS CPI tables | https://www.bls.gov/cpi/tables/ (Table 1, Food at home) | Monthly, ~10-15th |
| 5 | 30-yr mortgage | Freddie Mac PMMS | https://www.freddiemac.com/pmms | Weekly, Thursdays |
| 6 | Credit card APR | Fed G.19 | https://www.federalreserve.gov/releases/g19/current/ | Monthly, ~7th |
| 7 | S&P 500 since Jan 20 '25 | Calculate from Jan 20 '25 close | Yahoo Finance ^GSPC, calc % vs Jan 20 '25 baseline | Daily — pick a snapshot |
| 8 | Home price-to-income | Census + NAR | NAR median price ÷ Census median household income | Monthly, ~25th |
| 9 | Bankruptcy filings | Epiq AACER | https://www.epiqglobal.com/en-us/resource-center/news/aacer (latest report) | Monthly, ~7th |

**Update procedure:**

For each card that has a fresh data point:

1. Open `index.html`, find that card's entry in `window.NCDATA`
2. Update `val` (the big displayed number)
3. Update `sub` (date and source line)
4. Update `data` array (append new value, keep ~7-8 most recent points so the sparkline stays clean)
5. Update `labels` array (append matching date label)
6. Update `foot` array (the 3 caption chips below the chart)
7. Re-evaluate `badge` text (the colored pill in top-right)
8. **Re-read `context` narrative** — if the story changed (new peak, new trend reversal, new event), rewrite this. The narrative is what makes the card compelling. Stale narrative + fresh number = worse than no update.
9. Update the `🕐 Updated:` line in the HTML below the card (this is hardcoded outside NCDATA — search for the card's id like `ncard-eggs` and update the inline date)

**What "update narrative when story changes" means in practice:**

- Egg prices were $4.95 peak Jan '25, fell to $2.35. If they rise back to $3.50 next month, the story changes: bird flu round 2? New cause? Update the narrative.
- Gas prices currently say "Middle East conflict and Strait of Hormuz closure." If that conflict resolves, the narrative is wrong even if the number drops.
- S&P 500 card says "+13.3% Year 1." That comparison only makes sense for ~12 months from Jan 20 '25. After Jan 20 '26, this card needs reframing or removal.

**Time estimate:** 30-45 minutes monthly if you batch all 9 cards. 5 minutes per card if doing them piecemeal as data drops.

**Skip-it threshold:** If a number moved less than 2% AND the narrative is still accurate, you can skip that card for the month. Daily-cadence sources (gas, S&P) you only need to refresh monthly anyway.

**Caveat about narrative drift:** As of this writing, the cards reference "Trump tariff shock" (S&P card), "bird flu" (eggs), "Strait of Hormuz" (gas), "+25% since Jan 2021" (grocery, baselined to Biden inauguration). Some of these tie to specific political moments and will need re-anchoring eventually. Plan a "By the Numbers narrative refresh" once a year (January) to re-evaluate baselines and storylines.

### 4. Refresh Cabinet sector data (`refresh-fec` workflow)
**Why:** Cabinet members tracked separately. Trump, Vance, Rubio, Hegseth, etc. need periodic refresh. Senators who became Cabinet (Rubio, Vance) accumulate new lifetime data.  
**How:** Actions tab → "Refresh FEC Data" → Run workflow.  
**What to check:** Cabinet profiles still show updated AIPAC/sector totals. No one's `aipac` field doubled (the v7 wrapper bug).

### 5. Scan for new members
**Why:** Special elections, House members sworn in mid-term, governors becoming senators.  
**How:** Compare your `data/profiles.json` member list to current Congress.gov roster. Look for additions.  
**Where to check:**
- House: https://www.house.gov/representatives
- Senate: https://www.senate.gov/senators/
**What to do:** For each new member, add minimum profile entry (name, state, district, party, basic FEC ID, brief bio). Then run their data through the FEC fetch.

### 6. Validator pass
**Why:** Catches data integrity issues (math errors, missing fields, broken references).  
**How:** Run your validator script. Note any new warnings.  
**What to check:** Any new "math error" warnings (sector totals not summing correctly), missing `corporate_total` fields, or members whose AIPAC totals look stale vs TrackAIPAC.

### 7. Spot-check 5 random profiles
**Why:** Catches subtle display bugs, broken images, profile rendering issues.  
**How:** Open the live site, click 5 random profiles across both parties. Verify:
- Score makes sense given visible data
- AIPAC amount matches TrackAIPAC if listed
- Photo loads (Bioguide image)
- All tabs render (Donations, Votes, Education, Controversies, Contact)
- Net worth shows correctly

---

## Quarterly tasks (Jan / Apr / Jul / Oct)

### 8. ~~Refresh outside spending~~ — AUTOMATED ✓
**Runs automatically on the 5th of Jan, Apr, Jul, Oct at 1am UTC** (~5 hour run, completes overnight).  
**Why monitored:** Schedule E filings flow in, especially around primaries and elections. New IE spending changes the influence picture significantly.  
**What to check after each run:**
- Actions tab → "Refresh Outside Spending" → green ✓
- Members whose totals jumped significantly (any senator gaining $5M+ supporting/opposing is news)
- Any member showing $0/$0 (likely candidate ID bug, needs manual fix per audit script)
- Top spender names look real (no obvious typos, no "UNKNOWN COMMITTEE")

### 9. Quarterly sector data refresh (TrackAIPAC + OpenSecrets CSVs + v7 walk)

**Why:** Sector breakdowns (AIPAC, fossil fuels, pharma, defense, finance, tech) drive the score. Their source data (TrackAIPAC scrape + 10 OpenSecrets CSVs) doesn't have a public API, so this requires manual file downloads. Sector totals drift slowly (lifetime career amounts) so quarterly is enough.

**Step-by-step procedure:**

**A. Download TrackAIPAC page**
- Go to https://www.trackaipac.com/congress
- Save the full page text as `trackaipac_page.txt` (right-click → Save Page As → Text Only, or copy-paste content into a text file)
- Verify the file uses U+2028 LINE SEPARATOR characters as field delimiters (the v7 script depends on this format — if Save As doesn't preserve it, you may need to use a different browser or save the page HTML and let v7 parse that instead)

**B. Download 10 OpenSecrets industry CSVs**
- Go to https://www.opensecrets.org/industries
- Download these 10 files (5 industries × House + Senate):

| Industry | OpenSecrets path | Senators file | Reps file |
|---|---|---|---|
| Oil & Gas | /industries/indus.php?ind=E01 | `Money_from_Oil___Gas_to_US_Senators__1990-2024.csv` | `Money_from_Oil___Gas_to_US_Representatives__1990-2024.csv` |
| Health/Pharma | /industries/indus.php?ind=H | `Money_from_Health_to_US_Senators__1990-2024.csv` | `Money_from_Health_to_US_Representatives__1990-2024.csv` |
| Defense | /industries/indus.php?ind=D | `Money_from_Defense_to_US_Senators__1990-2024.csv` | `Money_from_Defense_to_US_Representatives__1990-2024.csv` |
| Finance/Insurance/Real Estate | /industries/indus.php?ind=F | `Money_from_Finance_Insurance_Real_Estate_to_US_Senators__1990-2024.csv` | `Money_from_Finance_Insurance_Real_Estate_to_US_Representatives__1990-2024.csv` |
| Communications/Electronics (Tech) | /industries/indus.php?ind=B | `Money_from_Communications_Electronics_to_US_Senators__1990-2024.csv` | `Money_from_Communications_Electronics_to_US_Representatives__1990-2024.csv` |

OpenSecrets typically updates these on the 1st of each month. Wait until 5-7 days into the new quarter so you get the freshest data.

**C. Update filename year ranges if needed**
The CSV filenames embed the cycle range (`1990-2024`). When OpenSecrets adds the 2026 cycle data, filenames will become `1990-2026`. Update file references in the v7 script if filenames change.

**D. Upload all 11 files to repo**
- Place `trackaipac_page.txt` and the 10 CSVs in the location v7 expects (likely `/scripts/` or a dedicated `/scripts/data_sources/` folder — verify by reading the v7 script's file paths)
- Commit them: `Quarterly sector data refresh: TrackAIPAC + 10 OpenSecrets CSVs (Q3 2026)` (adjust quarter)

**E. Run the v7 fetch workflow**
- Currently `refresh-fec` workflow only runs Cabinet members
- For full sector refresh, we need an "all 538 members" version of this workflow (TODO: build this)
- For now, this likely requires running v7 locally or asking Claude to help run it via a one-off workflow update

**F. Validate after run**
- Spot-check 5 random members against OpenSecrets — sector totals should match within ~10%
- Check for any members where sector totals dropped by >50% (could indicate parsing error)
- Run the validator script

**Time estimate:** 30 min for downloads + 4-6 hours for v7 walk + 15 min validation = ~half day total. Plan accordingly.

---

### 10. Member tags review (`data/tags.json`)
**Why:** Members shift category. A pledged-clean freshman might break their pledge. A previously corporate-friendly member might publicly pledge to refuse PACs.  
**How:** Manual review. Check news for any member announcing pledge changes. Re-evaluate the 45 grassroots tag entries.  
**What to check:** Anyone whose recent FEC filings now show corporate PAC money but is still tagged grassroots = needs removal. Anyone who newly pledged needs adding.

---

## Annual tasks

### 11. Net worth update (June, after May 15 disclosure deadline)
**Why:** Members file annual financial disclosure reports by May 15. June is the right time to update.  
**How:** Pull from public disclosure databases or Open Secrets PFD pages. Update `net_worth` field in profiles.json for all 538 members.  
**Sources:**
- House: https://disclosures-clerk.house.gov/PublicDisclosure/FinancialDisclosure
- Senate: https://efdsearch.senate.gov/search/

### 12. Education/biography sweep
**Why:** Members rarely get new degrees but biographical details can drift (committee chair changes, etc.).  
**How:** Annual pass through profiles, no specific workflow.

### 13. Stock trading flags review (`no_stock` tag)
**Why:** Members who pledged "no stock trading" sometimes break the pledge. The 78 members in `no_stock` tag need annual verification against Capitol Trades or similar.  
**Source:** https://www.capitoltrades.com/

---

## Event-driven tasks (do immediately when triggered)

### 14. New senator (special election, governor appointment)
- Add to profiles.json with full base entry
- Add to fec.json with candidate IDs
- Add to bioguide if available
- Run FEC fetch for them
- Run outside spending fetch for them
- Verify they appear in homepage member list

### 15. House member departure (resignation, death)
- Mark in profiles.json (don't delete; mark with `status: "departed"` for historical record)
- Update site rendering to show as former member
- Special election will trigger new member entry later

### 16. Cabinet change (resignation, new appointment)
- Update Cabinet section on homepage
- For new appointee: add Cabinet profile entry with manual `corruption_score` and reasoning
- For departed appointee: optionally archive

### 17. SCOTUS change (retirement, death, new appointment)
- Update SCOTUS section
- For new justice: add full profile with manual `corruption_score`

### 18. Major outside spending event (>$10M to single member, news-worthy)
- Run outside spending workflow for that specific member ahead of quarterly cadence
- Update share cards if applicable

---

## What to monitor passively

These don't need active updates but should trigger action when changed:

- **News mentions** of any member breaking their corporate PAC pledge → audit their data
- **TrackAIPAC** publishing new totals for senators in cycle
- **OpenSecrets** flagging new top donors for any member
- **FEC press releases** about enforcement actions (could affect specific profiles)
- **Anthropic Console** for the FEC API key — if rate limit gets bumped or you hit quota

---

## When data goes wrong (debugging guide)

**Symptom: Member shows score that contradicts their visible data**
- Check if they're in `GRASSROOTS_NAMES` tag when they shouldn't be (or vice versa)
- Check if `corporate_total` field has a stale "$0 corporate PAC" claim
- Check if their `corruption_score` field was manually set and the algorithm is overriding it (Path 3 issue)

**Symptom: Member shows $0 in outside spending despite known IE history**
- Almost certainly wrong candidate ID in `fec.json`
- Cross-check name against FEC.gov directly to find correct ID
- Patch `fec.json`, re-run outside spending workflow for that member only

**Symptom: AIPAC numbers drift between profile, share card, and TrackAIPAC**
- Run `fetch_trackaipac.py` to refresh
- Check `aipac_pacs` vs `aipac` field math (the v7 wrapper bug)
- Verify `applyFECData()` not overriding with stale value

**Symptom: New filings not showing**
- Did monthly FEC refresh run successfully?
- Did Netlify deploy succeed after the data file update?
- Browser cache — try incognito mode

---

## Backup priority list (if time is limited)

If you can only do 3 things per month:

1. Check Actions tab on the 3rd to confirm auto-runs succeeded (FEC totals + outside spending if quarterly)
2. Spot-check 5 random profiles (catches display issues fast)
3. Scan for new members (so the site doesn't fall behind reality)

If you can only do 1 thing per week:

1. Review the bill detection GitHub Issue (Mondays). Even if you don't write entries, you'll see what Congress did that week, which keeps you informed and may surface bills worth flagging for users via social.

If you can only do 1 thing per quarter:

1. **Sector data refresh** (TrackAIPAC + OpenSecrets CSVs + v7 walk). This is the quarterly task that most affects user-facing scores. Outside spending auto-runs handle themselves.

If you can only do 1 thing per year:

1. Net worth disclosure update (June, after the May 15 filing deadline). Skipping this means net worth tiles show stale data for an entire year.
