#!/usr/bin/env python3
"""
validate_data.py
================
Cross-checks data/fec.json against itself and against the member roster, and
fails the build when something is impossible rather than merely surprising.

WHY THIS EXISTS
---------------
Every serious error found in this dataset was found by accident. The FEC
double-count surfaced because a sanity guard in an unrelated script noticed
contributions exceeding receipts. Contaminated candidate IDs surfaced because
that same guard flagged six members. Wrong primary IDs surfaced only when
someone thought to compare against an official roster. In each case the wrong
number had been sitting in the file, rendering on the site, for weeks.

A civic transparency site has exactly one asset, which is that its numbers are
right. So the checks below run on every data change rather than waiting for
someone to get suspicious.

WHAT IT CHECKS
--------------
ERRORS (arithmetically impossible; exit 1):
  - contributions (individual + PAC + party) exceed total receipts.
    Contributions are a SUBSET of receipts and cannot be larger.
  - grassroots exceeds total receipts, same reason.
  - grassroots_small + grassroots_itemized does not reconstruct grassroots.
  - small_dollar_pct does not match its own components.
  - pac_pct does not match its own components.
  - a primary candidate_id that is not present in all_candidate_ids.
  - a member in the roster with no entry in fec.json at all.
  - total_raised of exactly zero. A zero renders on the site as a claim that
    the member raised nothing, which is a stronger and more wrong statement
    than a missing value.

WARNINGS (possible but suspect; reported, exit 0):
  - grassroots and individual_total differ by MORE THAN 2x. A gap that wide
    means the two sides are counting different candidacies, which is a real
    and fixable fault: Roger Marshall showed 7.49x because his Senate
    candidate ID was missing while his Senate committee was present.

NOTES (informational; these are NOT faults):
  - grassroots and individual_total disagree by 10% to 2x. These are two
    routes to the same quantity, but they are not strictly equivalent.
    individual_total aggregates a candidacy across its whole history;
    grassroots sums the committees FEC currently links to that candidate. For
    long-serving members, committees terminate and are replaced, and the
    historical ones drop off the linked list while the candidate totals still
    count them. Among members with exactly one candidate ID and one committee,
    where no such ambiguity exists, the median ratio is 1.000 and 93% agree.
    So a moderate gap on a thirty-year incumbent is expected, not a defect,
    and reporting 60 of them as warnings only trains people to ignore the
    check.
  - a member with no committees, which means no total_raised can be computed.
  - a member with no candidate IDs, which means no PAC split can be computed.
  - a member in fec.json who is not in the current roster. This is NOT
    automatically wrong: the file deliberately carries non-incumbent
    candidates (2026 Senate challengers, for instance) alongside sitting
    members. The check exists to surface genuinely departed members hiding
    among them, so every hit needs a human glance rather than deletion.
  - total_raised present but not a number.

WHY THE ERROR/WARNING SPLIT
---------------------------
An error means the file contains a statement that cannot be true, so shipping
it means shipping a falsehood. A warning means the data is incomplete or two
sources disagree, which is worth knowing but is not itself a false claim. Only
errors fail the run, so the check stays useful instead of being permanently
red and therefore permanently ignored.

TOLERANCES
----------
Deliberately loose enough to survive rounding and cycle-boundary effects, tight
enough to catch a real fault. 5% on the receipts comparisons, 2% on component
reconstruction, 1 percentage point on stored percentages, 10% between the two
independent individual-donation measures.

USAGE
-----
  python3 scripts/validate_data.py               # full report
  python3 scripts/validate_data.py --quiet        # counts only
  python3 scripts/validate_data.py --max 5        # cap examples per check

OUTPUT
------
  data/data_audit.json   machine-readable findings, always written
  exit 1 if any ERROR    exit 0 otherwise
"""

import json
import os
import sys

HERE = os.path.dirname(__file__)
FEC_PATH = os.path.join(HERE, "..", "data", "fec.json")
SENATE_PATH = os.path.join(HERE, "..", "data", "senate.json")
HOUSE_PATH = os.path.join(HERE, "..", "data", "house.json")
REPORT_PATH = os.path.join(HERE, "..", "data", "data_audit.json")

QUIET = "--quiet" in sys.argv
MAX_EX = 12
if "--max" in sys.argv:
    i = sys.argv.index("--max")
    if i + 1 < len(sys.argv) and sys.argv[i + 1].isdigit():
        MAX_EX = int(sys.argv[i + 1])

RECEIPTS_TOL = 0.05      # contributions vs receipts
COMPONENT_TOL = 0.02     # parts vs whole
PCT_TOL = 1.0            # stored percentage vs recomputed, in points
CROSS_TOL = 0.10         # grassroots vs individual_total, worth noting
CROSS_SEVERE = 1.00      # a 2x gap or worse, worth acting on


def num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def money(v):
    return f"${v:,.0f}" if num(v) else repr(v)


def main():
    fec = json.load(open(os.path.abspath(FEC_PATH)))
    members = [(k, v) for k, v in fec.items() if k != "_meta" and isinstance(v, dict)]
    roster = set()
    for p in (SENATE_PATH, HOUSE_PATH):
        try:
            roster |= {m["name"] for m in json.load(open(os.path.abspath(p)))}
        except Exception as e:
            print(f"WARNING: could not read {p} ({e}); roster checks skipped.")
            roster = None
            break

    errors, warnings, notes = [], [], []

    def err(check, member, detail):
        errors.append({"check": check, "member": member, "detail": detail})

    def warn(check, member, detail):
        warnings.append({"check": check, "member": member, "detail": detail})

    def note(check, member, detail):
        notes.append({"check": check, "member": member, "detail": detail})

    for name, m in members:
        tr = m.get("total_raised")
        ind, pac, party = m.get("individual_total"), m.get("pac_total"), m.get("party_total")
        gr, grs, gri = m.get("grassroots"), m.get("grassroots_small"), m.get("grassroots_itemized")

        # -- ERRORS ---------------------------------------------------------
        if num(tr) and tr > 0:
            contrib = sum(x for x in (ind, pac, party) if num(x))
            if contrib > tr * (1 + RECEIPTS_TOL):
                err("contributions exceed receipts", name,
                    f"{money(contrib)} in contributions vs {money(tr)} raised "
                    f"({contrib / tr:.2f}x); contributions are a subset of receipts")
            if num(gr) and gr > tr * (1 + RECEIPTS_TOL):
                err("grassroots exceeds receipts", name,
                    f"{money(gr)} grassroots vs {money(tr)} raised ({gr / tr:.2f}x)")

        if num(gr) and gr > 0 and num(grs) and num(gri):
            if abs((grs + gri) / gr - 1) > COMPONENT_TOL:
                err("grassroots components do not reconstruct the total", name,
                    f"{money(grs)} small + {money(gri)} itemized = {money(grs + gri)}, "
                    f"but grassroots is {money(gr)}")

        sdp = m.get("small_dollar_pct")
        if num(sdp) and num(gr) and gr > 0 and num(grs):
            expect = grs / gr * 100
            if abs(sdp - expect) > PCT_TOL:
                err("small_dollar_pct does not match its components", name,
                    f"stored {sdp:.1f}%, components give {expect:.1f}%")

        pp = m.get("pac_pct")
        if num(pp):
            denom = sum(x for x in (ind, pac, party) if num(x))
            if denom > 0 and num(pac):
                expect = pac / denom * 100
                if abs(pp - expect) > PCT_TOL:
                    err("pac_pct does not match its components", name,
                        f"stored {pp}%, components give {expect:.1f}%")

        cid, allids = m.get("candidate_id"), m.get("all_candidate_ids")
        if cid and isinstance(allids, list) and allids and cid not in allids:
            err("primary candidate_id missing from all_candidate_ids", name,
                f"primary {cid} not in {allids}")

        # -- WARNINGS -------------------------------------------------------
        # Two independent routes to the same number. grassroots is summed from
        # the member's committees; individual_total comes from the FEC
        # candidate totals endpoint. Disagreement means one of the two ID sets
        # is wrong, which is exactly how the contaminated IDs were found.
        if num(gr) and num(ind) and gr > 0 and ind > 0:
            ratio = gr / ind
            if abs(ratio - 1) > CROSS_SEVERE:
                # A gap this wide is not explainable by committee turnover. It
                # means the two sides are counting different candidacies.
                warn("grassroots and individual_total differ by more than 2x", name,
                     f"grassroots {money(gr)} vs individual_total {money(ind)} "
                     f"(ratio {ratio:.2f}); most likely a candidate ID or committee "
                     f"belonging to a different candidacy")
            elif abs(ratio - 1) > CROSS_TOL:
                note("grassroots and individual_total disagree", name,
                     f"grassroots {money(gr)} vs individual_total {money(ind)} "
                     f"(ratio {ratio:.2f})")

        # A zero here is worse than a wrong number: the site would render
        # "raised $0" as if it were a finding. It means every candidate ID on
        # file has no filings at FEC, which is what happened when a correct
        # older ID was replaced by a newly-assigned one that had not filed yet.
        if num(tr) and tr == 0:
            err("total_raised is zero", name,
                "no FEC filings under any candidate ID on file; an ID is "
                "probably missing or newly assigned")

        if not (m.get("committees") or []):
            warn("no committees on file", name, "total_raised cannot be computed")
        if not (allids or cid):
            warn("no candidate IDs on file", name, "PAC split cannot be computed")
        if "total_raised" in m and not num(tr):
            warn("total_raised is not a number", name, repr(tr))
        if roster is not None and name not in roster:
            warn("in fec.json but not in the current roster", name,
                 "either a departed member or a non-incumbent candidate tracked "
                 "on purpose; confirm before removing anything")

    if roster is not None:
        have = {k for k, _ in members}
        for name in sorted(roster - have):
            err("roster member missing from fec.json", name, "no record at all")

    # -- report -------------------------------------------------------------
    def group(items):
        out = {}
        for it in items:
            out.setdefault(it["check"], []).append(it)
        return out

    eg, wg, ng = group(errors), group(warnings), group(notes)

    print("=" * 68)
    print(f"Checked {len(members)} members"
          + (f" against a {len(roster)}-member roster" if roster is not None else ""))
    print(f"ERRORS: {len(errors)}    WARNINGS: {len(warnings)}    NOTES: {len(notes)}")

    for title, grp, mark in (("ERRORS", eg, "!!"), ("WARNINGS", wg, "  "),
                             ("NOTES (informational, not faults)", ng, "  ")):
        if not grp:
            continue
        print(f"\n{title}")
        for check, items in sorted(grp.items(), key=lambda x: -len(x[1])):
            print(f"{mark} {len(items):>4}  {check}")
            if QUIET:
                continue
            for it in items[:MAX_EX]:
                print(f"           {it['member']}: {it['detail']}")
            if len(items) > MAX_EX:
                print(f"           ...and {len(items) - MAX_EX} more")

    if not errors:
        print("\nNo impossible values found. Every stored percentage matches its "
              "own components, and no member's contributions exceed their receipts.")

    with open(os.path.abspath(REPORT_PATH), "w") as f:
        json.dump({"members_checked": len(members),
                   "error_count": len(errors), "warning_count": len(warnings),
                   "note_count": len(notes),
                   "errors": errors, "warnings": warnings, "notes": notes}, f, indent=1, ensure_ascii=False)
    print(f"\nFindings written to {os.path.abspath(REPORT_PATH)}")

    if errors:
        print("\nFAILING: the file contains values that cannot be true. "
              "Do not publish until these are resolved.")
        sys.exit(1)


if __name__ == "__main__":
    main()
