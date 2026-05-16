"""Unit test for scrape_trackaipac.parse() against a flattened-HTML fixture
built from the REAL trackaipac.com/congress content, including edge cases:
senators, At Large, accented names, name duplication (Squarespace img alt),
Sen./Rep. prefixes, 'Track AIPAC Approved' members, $0 members, IE-heavy
members, no-PAC-list members, and a poor-record (not approved) $0 member.
"""
import sys
sys.path.insert(0, "/home/claude")
from scrape_trackaipac import flatten, parse  # noqa: E402

# Simulated flattened raw HTML. Two name styles are interleaved on purpose:
#  - duplicated "Sen. X Y X Y" (when Squarespace renders the img alt as text)
#  - bare heading name (when alt is an attribute and gets stripped)
FIXTURE = """
Alabama
Sen. Tommy Tuberville Tommy Tuberville AL-SEN [R]
Israel Lobby Total: $29,184 PACs: $29,184 IE: $0
AIPAC, AMP, RJC, USI Running for Governor 2026
Katie Britt AL-SEN [R] Israel Lobby Total: $106,961
PACs: $106,961 IE: $0 AIPAC, RJC, TPOH, USI Next Election: 2028
Rep. Shomari Figures Shomari Figures AL-02 [D]
Israel Lobby Total: $63,235 PACs: $61,835 IE: $1,400
AIPAC, COPAC, DMFI, MDACC, TPOH
Alaska
Nick Begich AK-At Large [R] Israel Lobby Total: $7,599
PACs: $7,599 IE: $0 AIPAC, RJC
Arizona
Sen. Adam Schiff Adam Schiff AZ-SEN [D] Wait no
Yassamin Ansari AZ-03 [D] Israel Lobby Total: $1,161,274
PACs: $5,000 IE: $1,156,274 DMFI
Adelita Grijalva AZ-07 [D] Track AIPAC Approved!
This representative rejects AIPAC and champions a foreign policy based on
human rights and international law.
California
Sen. Adam Schiff Adam Schiff CA-SEN [D] Israel Lobby Total: $5,608,830
PACs: $581,080 IE: $5,027,750 AIPAC, BICOUNTY, DMFI, HVPAC Next Election: 2030
Vacant CA-01 Rep. Doug LaMalfa passed away on January 6, 2026.
Maxine Waters CA-43 [D] Israel Lobby Total: $4,000 PACs: $4,000 IE: $0
We encourage this representative to continue improving
Indiana
Rep. Andre Carson Andre Carson IN-07 [D] Track AIPAC Approved!
This representative rejects AIPAC
Mark Messmer IN-08 [R] Israel Lobby Total: $2,836,476
PACs: $313,632 IE: $2,522,844 AIPAC, GCSC, LAFAS, RJC
Illinois
Jesus Chuy Garcia IL-04 [D] Track AIPAC Approved!
This representative rejects AIPAC Retiring 2026
Kentucky
Thomas Massie KY-04 [R] Israel Lobby Total: $0 PACs: $0 IE: $0
We encourage this representative to continue improving their legislative
Louisiana
Cleo Fields LA-06 [D] Israel Lobby Total: $0 PACs: $0 IE: $0
This representative has a poor legislative record on Israel-Palestine issues.
Maryland
Kweisi Mfume MD-07 [D] Israel Lobby Total: $171,833 PACs: $171,833 IE: $0
AIPAC, BICPAC, COPAC, DMFI, JSTREET, NATPAC, PIA, SUNPAC, TPOH
Florida
Carlos A. Gimenez FL-28 [R] Israel Lobby Total: $124,360
PACs: $124,360 IE: $0 AIPAC, AMP, PIA, RJC, SUNPAC, USI
Debbie Wasserman-Schultz FL-25 [D] Israel Lobby Total: $1,456,782
PACs: $1,456,782 IE: $0 AIPAC, COPAC, DEVPAC
"""

EXPECT = {
    "Tommy Tuberville": (29184, 29184, 0, "tracked"),
    "Katie Britt": (106961, 106961, 0, "tracked"),
    "Shomari Figures": (63235, 61835, 1400, "tracked"),
    "Nick Begich": (7599, 7599, 0, "tracked"),
    "Yassamin Ansari": (1161274, 5000, 1156274, "tracked"),
    "Adelita Grijalva": (0, 0, 0, "approved"),
    "Adam Schiff": (5608830, 581080, 5027750, "tracked"),
    "Maxine Waters": (4000, 4000, 0, "tracked"),
    "Andre Carson": (0, 0, 0, "approved"),
    "Mark Messmer": (2836476, 313632, 2522844, "tracked"),
    "Jesus Chuy Garcia": (0, 0, 0, "approved"),
    "Thomas Massie": (0, 0, 0, "tracked"),
    "Cleo Fields": (0, 0, 0, "tracked"),
    "Kweisi Mfume": (171833, 171833, 0, "tracked"),
    "Carlos A. Gimenez": (124360, 124360, 0, "tracked"),
    "Debbie Wasserman-Schultz": (1456782, 1456782, 0, "tracked"),
}

got = parse(flatten(FIXTURE))

print(f"Parsed {len(got)} members from fixture\n")
fails = 0
for name, (et, ep, ei, es) in EXPECT.items():
    g = got.get(name)
    if not g:
        print(f"  MISS  {name!r} not parsed")
        fails += 1
        continue
    ok = (g["israel_lobby_total"] == et and g["pacs"] == ep
          and g["ie"] == ei and g["status"] == es)
    flag = "ok  " if ok else "FAIL"
    if not ok:
        fails += 1
    print(f"  {flag} {name:28s} total=${g['israel_lobby_total']:<10,} "
          f"pacs=${g['pacs']:<9,} ie=${g['ie']:<9,} {g['status']}")

# 'Vacant CA-01' must NOT appear; bogus 'Adam Schiff AZ-SEN ... Wait no' line
# must not create a phantom (no total in its window -> no_figure, but the
# real Schiff is CA-SEN with the right numbers; ensure CA value won.
assert "Vacant" not in got, "Vacant seat leaked into output"
assert got["Adam Schiff"]["state"] == "CA", "Wrong Schiff record won"

print(f"\n{'ALL TESTS PASSED' if fails == 0 else f'{fails} FAILURES'}")
sys.exit(1 if fails else 0)
