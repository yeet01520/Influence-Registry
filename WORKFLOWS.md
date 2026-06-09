# Influence Registry — Workflow Reference

A quick reference for what to run and in what order to accomplish common tasks.

---

## 1. Update FEC Sector Data (routine refresh)

Run periodically to keep donation totals current.

1. Actions: **Refresh FEC Data (All Members)**
   - Takes multiple 6-hour runs with resume-from-checkpoint until STATUS: COMPLETE
   - Re-run it until you see "STATUS: COMPLETE" in the log
2. Actions: **Update index.html from FEC Data**
   - Embeds the new `fec.json` into `index.html` automatically
   - Commits and pushes — site updates on next Netlify deploy

---

## 2. Fix Contaminated FEC Data (one-time or after pipeline bug)

Run when member sector data is wrong or showing all zeros.

1. Upload fixed `fetch_fec_data.py` to `scripts/`
2. Upload clean `all_fec_data.json` to `data/raw/` (with bad members evicted)
   - Or upload clean `fec.json` to `data/` if no checkpoint exists
3. Add unfetchable members to `scripts/skip_members.json`
   - Currently: `["Amata Coleman Radewagen", "Alan Armstrong"]`
4. Actions: **Refresh FEC Data (All Members)**
   - Re-run until STATUS: COMPLETE
5. Actions: **Update index.html from FEC Data**

---

## 3. Deploy a Code Change to index.html

When you've edited `index.html` directly (UI fixes, label changes, new features).

1. Upload the new `index.html` to the repo root
2. Actions: **Generate Static Profile Pages**
   - Rebuilds share card PNGs and static member pages
   - Commits and pushes automatically
3. Netlify deploys automatically on push

> Note: If you also have a fresh FEC refresh pending, run the FEC workflows BEFORE uploading a hand-edited index.html, otherwise the Update Index workflow will overwrite your manual changes.

---

## 4. Update Economic Data (By The Numbers tab)

When BLS, AAA, Freddie Mac, or other sources publish new numbers.

1. Upload `index.html` to Claude with the new values
2. Claude patches the card values and NCDATA JSON block
3. Upload the patched `index.html` to the repo root
4. Netlify deploys automatically

> Key sources and cadence:
> - Eggs / Inflation / Grocery: BLS CPI, released ~12th of each month
> - Gas: AAA, updates daily — gasprices.aaa.com
> - Mortgage: Freddie Mac PMMS, every Thursday — freddiemac.com/pmms
> - Credit card APR: Federal Reserve G.19, released monthly
> - S&P 500: MacroTrends, real-time

---

## 5. Add a New Bill to the Registry

When the detect-bills script surfaces a new draft or you want to add one manually.

1. Actions: **Detect Significant Bills** (runs automatically every Monday)
   - Check the GitHub Issue it opens, or check `data/bills_drafts.json`
2. Upload `bills_drafts.json` and `bills.json` to Claude
3. Claude fills in the TODO fields and appends to `bills.json`
4. Upload the updated `bills.json` to `data/`
5. Actions: **Update index.html from FEC Data** (if sector data also changed)
   - OR just upload a patched `index.html` if only bills changed

---

## 6. Update Sector Counts (At A Glance tab)

When member counts or dollar totals for sectors change significantly.

1. Upload `sector_counts.json` to Claude with the changes needed
2. Claude patches the values
3. Upload updated `sector_counts.json` to `data/`
4. Dollar totals in the summary cards (Fossil Fuel, Finance, Pharma, Defense) now compute live from `fec.json` — no manual update needed for those
5. Super PAC total ($2.6B) is still hardcoded — update manually if OpenSecrets publishes new cycle data

---

## 7. Session Tracker Update (At A Glance tab)

When the 2026 row gets stale (update roughly monthly during session).

1. Check current House legislative day count: govinfo.gov House Calendars
2. Check current Senate days of session: govinfo.gov Senate Calendars (cover page)
3. Upload `index.html` to Claude with the new numbers
4. Claude patches the 2026 row and disclaimer text
5. Upload patched `index.html` to the repo root

---

## Workflow Quick Reference

| Workflow | Trigger | What it does |
|---|---|---|
| Refresh FEC Data (All Members) | Manual | Fetches sector donation data for all 538 members, saves checkpoint |
| Update index.html from FEC Data | Manual (after FEC refresh) | Embeds `fec.json` into `index.html`, commits |
| Generate Static Profile Pages | Manual or on push | Renders share card PNGs, builds static pages, updates sitemap |
| Detect Significant Bills | Auto every Monday | Scans Congress.gov for significant votes, writes `bills_drafts.json` |
| Evict Contaminated FEC Checkpoint | Manual (one-off fix) | Removes bad entries from `all_fec_data.json` checkpoint |

---

## File Locations Reference

| File | Path | Updated by |
|---|---|---|
| Main site | `index.html` | Manual upload or Update Index workflow |
| FEC donation data | `data/fec.json` | FEC refresh workflow |
| FEC checkpoint | `data/raw/all_fec_data.json` | FEC refresh workflow (auto-save) |
| Sector counts | `data/sector_counts.json` | Manual or refresh_sector_counts.py |
| Bills | `data/bills.json` | Manual upload after Claude review |
| Bill drafts | `data/bills_drafts.json` | Detect Significant Bills workflow |
| FEC fetch script | `scripts/fetch_fec_data.py` | Manual upload when pipeline changes |
| Skip list | `scripts/skip_members.json` | Manual (add unfetchable members) |

---

## Common Gotchas

- **FEC refresh runs but nothing changes**: Checkpoint is stale. Evict bad members from `data/raw/all_fec_data.json` first.
- **index.html upload overwrites pipeline data**: Always run Update Index workflow AFTER uploading a hand-edited index.html — or better, run FEC workflows first then edit.
- **Detect Bills returns empty**: Check the DIAGNOSTICS block in the log. If `house_votes_returned: 0`, Congress was likely in recess.
- **Share card shows wrong data**: `index.html` has FEC data embedded inline — the live `fec.json` file alone won't update the site. Must run Update Index workflow.
- **Score shows 0% for appointed member**: Expected — Armstrong and Husted have no FEC candidate history. The site shows "No FEC Data" in their profile modal by design.
