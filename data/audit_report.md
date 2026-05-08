# Candidate ID Audit Report
_Run: 2026-05-07T23:46:13.296584+00:00_

## Summary

- Members audited: 536
- Primary candidate_id changed: 12
- Cross-state contamination cleaned (primary unchanged): 265
- Already correct (no change): 203
- FEC search returned no match (left as-is): 56
- Members queued for Schedule E re-fetch: 285

## Members with primary candidate_id CHANGED

These had wrong primary IDs in fec.json — outside spending data was definitely incorrect:

- **Andy Harris**: `H6MD07442` → `H8MD01094`
- **Bob Latta**: `H6OH05022` → `H8OH05036`
- **Bob Onder**: `H8MO09146` → `H4MO03221`
- **Cleo Fields**: `H0LA08025` → `H4LA06211`
- **Dick Durbin**: `S4IL00339` → `S6IL00151`
- **Jim Jordan**: `H0OH12112` → `H6OH04082`
- **John James**: `H6MI18022` → `H2MI10150`
- **Mike Collins**: `H0GA03017` → `H4GA10071`
- **Mike Rogers**: `H6AL06119` → `H2AL03032`
- **Mike Simpson**: `H4FL01122` → `H8ID02064`
- **Nick LaLota**: `H2NY01190` → `H0NY02200`
- **Raul Ruiz**: `H0CA36177` → `H2CA36439`

## Members with contamination cleaned (primary unchanged) — 265 total

These had extra cross-state IDs polluting their candidate list. Primary was right, 
but Schedule E data may have included unrelated candidates' spending.

- **Aaron Bean**: removed `S0FL00437`
- **Adam Gray**: removed `P60013398, S2CA00344`
- **Adam Schiff**: removed `H0CA27085`
- **Addison McDowell**: removed `P60019130, S0KY00446`
- **Alex Padilla**: removed `H2NY06165`
- **Alma Adams**: removed `S8CT00105, P80001688`
- **Amy Klobuchar**: removed `P80006117`
- **Andrea Salinas**: removed `S2IL00143`
- **Andrew Clyde**: removed `P60010634, S6GA00010`
- **Andy Biggs**: removed `P80009145`
- **Andy Kim**: removed `H8NJ03206, P40014219`
- **Andy Ogles**: removed `H2TN04191`
- **Angus King**: removed `H2ME02048`
- **Anna Paulina Luna**: removed `P60021094, S4NM00159`
- **April McClain Delaney**: removed `P00006213, S6IN00019`
- **Ashley Moody**: removed `H0TX04037, P60007200`
- **Ayanna Pressley**: removed `P40003600`
- **Ben Cline**: removed `P20002226, S2KS00063`
- **Bernie Moreno**: removed `H2KY03198`
- **Betty McCollum**: removed `P00010769, S8FL00075`
- **Bill Cassidy**: removed `H0IL06029, P80007750`
- **Bill Hagerty**: removed `P60018215, H4NJ02157`
- **Bill Keating**: removed `S2OR00077`
- **Blake Moore**: removed `P40013161, S0NC00376`
- **Bonnie Watson Coleman**: removed `S2FL00672, P40012189`
- **Brad Sherman**: removed `P40014748, S6LA00086`
- **Brandon Gill**: removed `P40003865, S2WI00334`
- **Brian Jack**: removed `P60009008, S0KY00032`
- **Brian Mast**: removed `P40009995, S6NV00200`
- **Brian Schatz**: removed `P60014099, H6HI02244`
- ...and 235 more

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
2. Run `scripts/refetch_outside_spending.py` to re-pull Schedule E for the 285 affected members
3. Verify spot-check members (Durbin, Cotton, Schiff, Rick Scott) now show realistic numbers

## Backup

Original fec.json saved to `data/fec.json.before_audit` for rollback if needed.