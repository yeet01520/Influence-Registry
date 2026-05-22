# Candidate ID Audit Report
_Run: 2026-05-22T04:50:09.293597+00:00_

## Summary

- Members audited: 538
- Primary candidate_id changed: 16
- Cross-state contamination cleaned (primary unchanged): 306
- Already correct (no change): 159
- FEC search returned no match (left as-is): 57
- Members queued for Schedule E re-fetch: 332

## Members with primary candidate_id CHANGED

These had wrong primary IDs in fec.json — outside spending data was definitely incorrect:

- **Andy Harris**: `H6MD07442` → `H8MD01094`
- **Bob Good**: `H6VA05068` → `H0VA05160`
- **Bob Latta**: `H6OH05022` → `H8OH05036`
- **Bob Onder**: `H8MO09146` → `H4MO03221`
- **Cleo Fields**: `H0LA08025` → `H4LA06211`
- **Dick Durbin**: `S4IL00339` → `S6IL00151`
- **Jim Jordan**: `H0OH12112` → `H6OH04082`
- **Joe Courtney**: `H2NC13185` → `H2CT02112`
- **John James**: `H6MI18022` → `H2MI10150`
- **Mike Collins**: `H0GA03017` → `H4GA10071`
- **Mike Rogers**: `H6AL06119` → `H2AL03032`
- **Mike Simpson**: `H4FL01122` → `H8ID02064`
- **Nick Begich**: `H4AK00024` → `H2AK01083`
- **Nick LaLota**: `H2NY01190` → `H0NY02200`
- **Raul Ruiz**: `H0CA36177` → `H2CA36439`
- **Rick Allen**: `H0GA02241` → `H2GA12121`

## Members with contamination cleaned (primary unchanged) — 306 total

These had extra cross-state IDs polluting their candidate list. Primary was right, 
but Schedule E data may have included unrelated candidates' spending.

- **Aaron Bean**: removed `S0FL00437`
- **Adam Gray**: removed `P60013398, S2CA00344`
- **Adam Schiff**: removed `H0CA27085`
- **Addison McDowell**: removed `S0KY00446, P60019130`
- **Adrian Smith**: removed `P00005819, S8FL00216`
- **Alex Padilla**: removed `H2NY06165`
- **Alma Adams**: removed `S8CT00105, P80001688`
- **Amy Klobuchar**: removed `P80006117`
- **Andrea Salinas**: removed `S2IL00143`
- **Andrew Clyde**: removed `P60010634, S6GA00010`
- **Andy Biggs**: removed `P80009145`
- **Andy Kim**: removed `H8NJ03206, P40014219`
- **Angus King**: removed `P60023470, H4GA05014`
- **Ann Wagner**: removed `P60010451`
- **Anna Paulina Luna**: removed `P60021094, S4NM00159`
- **April McClain Delaney**: removed `S6IN00019, P00006213`
- **Ashley Moody**: removed `H0TX04037, P60007200`
- **Ayanna Pressley**: removed `P40003600`
- **Ben Cline**: removed `S2KS00063, P20002226`
- **Bennie Thompson**: removed `P40003451, S6OR00391`
- **Bernie Moreno**: removed `H2KY03198`
- **Betty McCollum**: removed `S8FL00075, P00010769`
- **Bill Cassidy**: removed `H0IL06029, P80007750`
- **Bill Hagerty**: removed `H4NJ02157, P60018215`
- **Bill Keating**: removed `S2OR00077`
- **Blake Moore**: removed `S0NC00376, P40013161`
- **Bonnie Watson Coleman**: removed `S2FL00672, P40012189`
- **Brad Schneider**: removed `S0RI00018`
- **Brad Sherman**: removed `P40014748, S6LA00086`
- **Brandon Gill**: removed `P40003865, S2WI00334`
- ...and 276 more

## Members where FEC search returned no match

These are unusual — could be Cabinet members, non-voting delegates, recent appointees, 
or rare data issues. Their fec.json entries were left unchanged. Manual review needed:

- Adam Smith
- Al Green
- Alan Armstrong
- Amata Coleman Radewagen
- Ami Bera
- André Carson
- Andy Barr
- Angie Craig
- Ashley Hinson
- Austin Scott
- Barry Moore
- Becca Balint
- Ben Ray Luján
- Bernie Sanders
- Bill Foster
- Bobby Scott
- Brett Guthrie
- Buddy Carter
- Carlos Giménez
- Chuy García
- Dave Joyce
- Eugene Vindman
- French Hill
- Gabe Amo
- Gabe Evans
- Gabe Vasquez
- Hank Johnson
- Jack Bergman
- Jack Reed
- Jake Ellzey
- John Boozman
- Jon Ossoff
- Linda Sánchez
- Lizzie Fletcher
- Lucy McBath
- María Elvira Salazar
- Mike Ezell
- Mike Johnson
- Morgan Griffith
- Nanette Barragán
- Nellie Pou
- Nydia Velázquez
- Pablo Hernández
- Raja Krishnamoorthi
- Randy Feenstra
- Rick Crawford
- Ro Khanna
- Shontel Brown
- Steve Womack
- Tammy Duckworth
- Ted Budd
- Ted Cruz
- Tom Kean Jr.
- Tommy Tuberville
- Tony Gonzales
- Trent Kelly
- Troy Balderson

## Next steps

1. Review `data/audit_report.md` (this file)
2. Run `scripts/refetch_outside_spending.py` to re-pull Schedule E for the 332 affected members
3. Verify spot-check members (Durbin, Cotton, Schiff, Rick Scott) now show realistic numbers

## Backup

Original fec.json saved to `data/fec.json.before_audit` for rollback if needed.