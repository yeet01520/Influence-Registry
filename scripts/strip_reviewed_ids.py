#!/usr/bin/env python3
"""
strip_reviewed_ids.py
=====================
Removes a hand-reviewed list of contaminated candidate IDs from data/fec.json.

WHY A SEPARATE SCRIPT
---------------------
audit_candidate_ids.py auto-removes only its CONFIRMED bucket, where the FEC
surname does not match the member at all, or where two members claim one ID
and the FEC name settles it. Its SUSPECT bucket is different: the surname
matches and only the given name differs. That is ambiguous by nature, because
Congress is full of members filed under a legal name that is not the name they
go by (Bernard/Bernie Sanders, Earl Leroy/Buddy Carter, Amerish/Ami Bera).
Auto-stripping those would delete correct IDs.

So the audit reports them and a human decides. This script applies that
decision, and nothing else. Every removal is listed explicitly below with the
name the FEC has on file, so the diff is readable and arguable.

WHAT THE PATTERN TURNED OUT TO BE
---------------------------------
Almost every one of these is a relative. Doris Matsui was carrying Robert
Matsui, her late husband and predecessor in the seat. Kathy Castor was
carrying Betty Castor, her mother. Linda Sanchez was carrying Loretta
Sanchez, her sister. Shomari Figures was carrying Vivian Davis Figures, his
mother. Nick Begich was carrying Mark Begich. Whatever originally built these
ID lists matched on surname, so members inherited their families' campaign
finance records.

SAFETY
------
  - Dry-run by default. Pass --commit to write.
  - Every removal is cross-checked against data/candidate_id_audit.json: if a
    pair in the list below is not present in that report's suspect findings,
    or the FEC name does not match what is written here, the removal is
    REFUSED. That way a typo cannot delete a good ID, and this list cannot
    drift out of sync with the evidence it came from.
  - A member is never left with zero candidate IDs.
  - A member's PRIMARY candidate_id is never removed by this script. Every
    entry below is an extra sitting on top of a valid primary. Primaries that
    look wrong (Durbin, Begich) are deliberately excluded and need a human to
    check them on fec.gov first.
  - Writes data/fec.json.stripreviewed.bak before saving.

USAGE
-----
  python3 scripts/strip_reviewed_ids.py            # show what would change
  python3 scripts/strip_reviewed_ids.py --commit   # apply

Also reads COMMIT_INPUT from the environment, so a workflow input reaches the
script directly rather than through shell arg building.

AFTER RUNNING
-------------
Stripping an ID does NOT recompute stored money. Re-run
"Refresh PAC vs Individual Split" and the sector refresh afterwards.
"""

import json
import os
import sys

HERE = os.path.dirname(__file__)
FEC_PATH = os.path.join(HERE, "..", "data", "fec.json")
AUDIT_PATH = os.path.join(HERE, "..", "data", "candidate_id_audit.json")

COMMIT = ("--commit" in sys.argv) or \
         (os.environ.get("COMMIT_INPUT", "").strip().lower() == "true")

# (member, candidate_id, name the FEC has on file for that ID)
# Reviewed by hand against the audit report. Edit this list, not the code.
REMOVALS = [
    ("Doris Matsui",     "S2CA00302", "MATSUI, ROBERT T"),
    ("Kathy Castor",     "S4FL00223", "CASTOR, BETTY"),
    ("Linda S\u00e1nchez", "S6CA00691", "SANCHEZ, LORETTA"),
    ("Shomari Figures",  "S8AL00282", "FIGURES, VIVIAN DAVIS"),
    ("Nick Begich",      "S8AK00090", "BEGICH, MARK"),
    ("Aaron Bean",       "S0FL00437", "BEAN, BOBBIE"),
    ("Adam Smith",       "S8WA00020", "SMITH, DOUGLAS J"),
    ("David Taylor",     "S2OH00501", "TAYLOR, CHAD A"),
    ("Dina Titus",       "S0NV00153", "TITUS, ROBIN LEE MD"),
    ("Donald Norcross",  "S6NJ00073", "NORCROSS, DAVID F"),
    ("Hank Johnson",     "S0GA00492", "JOHNSON, ARTHUR WAYNE"),
    ("Sanford Bishop",   "S8GA00057", "BISHOP, BILL (UNREG)"),
    ("Sylvia Garcia",    "S0TX00381", "GARCIA, ANNE"),
]

# Deliberately NOT removed. Kept here so the reasoning survives, and so nobody
# re-adds them to REMOVALS later without reading why they were left out.
KEPT = [
    ("Ami Bera",            "H0CA03078", "BERA, AMERISH", "his legal name"),
    ("Bernie Sanders",      "S4VT00033", "SANDERS, BERNARD", "his legal name"),
    ("Bernie Sanders",      "H8VT01016", "SANDERS, BERNARD", "his own former House seat"),
    ("Buddy Carter",        "H4GA01039", "CARTER, EARL LEROY", "his legal name"),
    ("Buddy Carter",        "S6GA00374", "CARTER, EARL LEROY", "his own Senate run"),
    ("Chuy Garc\u00eda",    "H8IL04134", "GARCIA, JESUS", "his legal name"),
    ("Jake Ellzey",         "H8TX06266", "ELLZEY, JOHN KEVIN SR.", "his legal name"),
    ("Raja Krishnamoorthi", "S6IL00482", "KRISHNAMOORTHI, S", "initial of his legal first name"),
]

# Flagged but unresolved: these are PRIMARY ids, so getting them wrong is worse
# than an extra entry. Check on fec.gov before acting.
UNRESOLVED = [
    ("Dick Durbin", "S4IL00339", "DURBIN, JEFFREY WILLIAM"),
    ("Nick Begich", "H4AK00024", "BEGICH, MARGARET (PEGGE)"),
]


def member_ids(rec):
    out, seen = [], set()
    for k in ("all_candidate_ids", "candidate_id"):
        v = rec.get(k)
        vals = v if isinstance(v, list) else ([v] if isinstance(v, str) and v else [])
        for i in vals:
            if i and i not in seen:
                seen.add(i)
                out.append(i)
    return out


def main():
    path = os.path.abspath(FEC_PATH)
    raw = open(path).read()
    fec = json.loads(raw)

    # Cross-check source. Without it we are trusting a typed list against live data.
    try:
        audit = json.load(open(os.path.abspath(AUDIT_PATH)))
        evidence = {(f["member"], f["id"]): f["fec_name"] for f in audit.get("suspect", [])}
        have_audit = True
    except Exception as e:
        evidence, have_audit = {}, False
        print(f"WARNING: could not read {AUDIT_PATH} ({e}).")
        print("Cross-checking is DISABLED. Every removal below is unverified.")

    print(f"Mode: {'COMMIT (WILL WRITE data/fec.json)' if COMMIT else 'DRY-RUN (no write)'}")
    print(f"Cross-check against audit findings: {'ON' if have_audit else 'OFF'}")
    print(f"Proposed removals: {len(REMOVALS)}\n")

    applied, refused = [], []

    for member, cid, expect_name in REMOVALS:
        rec = fec.get(member)
        if rec is None:
            refused.append((member, cid, "member not found in fec.json"))
            continue
        ids = member_ids(rec)
        if cid not in ids:
            refused.append((member, cid, "ID not on this member (already removed?)"))
            continue
        if rec.get("candidate_id") == cid:
            refused.append((member, cid, "this is their PRIMARY id, refusing"))
            continue
        if len(ids) <= 1:
            refused.append((member, cid, "would leave member with no IDs"))
            continue
        if have_audit:
            seen = evidence.get((member, cid))
            if seen is None:
                refused.append((member, cid, "not in the audit's suspect findings"))
                continue
            if seen != expect_name:
                refused.append((member, cid,
                                f"audit says '{seen}', this list says '{expect_name}'"))
                continue
        keep = [i for i in ids if i != cid]
        if COMMIT:
            rec["all_candidate_ids"] = keep
        applied.append((member, cid, expect_name, keep))

    label = "REMOVED" if COMMIT else "WOULD REMOVE"
    print(f"{label} ({len(applied)}):")
    for member, cid, name, keep in applied:
        print(f"  {member:22s} {cid:11s} belongs to {name:26s} -> keeps {keep}")

    if refused:
        print(f"\nREFUSED ({len(refused)}):")
        for member, cid, why in refused:
            print(f"  {member:22s} {cid:11s} {why}")

    print(f"\nDeliberately kept ({len(KEPT)}), these FEC names are the member's own:")
    for member, cid, name, why in KEPT:
        print(f"  {member:22s} {cid:11s} {name:26s} {why}")

    print(f"\nUNRESOLVED ({len(UNRESOLVED)}), PRIMARY ids that look wrong. "
          "Check fec.gov by hand:")
    for member, cid, name in UNRESOLVED:
        print(f"  {member:22s} {cid:11s} FEC: {name}")

    if COMMIT and applied:
        with open(path + ".stripreviewed.bak", "w") as fh:
            fh.write(raw)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(fec, fh, indent=1, ensure_ascii=False)
        os.replace(tmp, path)
        print(f"\nWROTE {path}")
        print(f"(backup of original: {path}.stripreviewed.bak)")
        print("\nNOTE: this does NOT recompute money. Re-run Refresh PAC vs "
              "Individual Split and the sector refresh next.")
    elif COMMIT:
        print("\nNothing applied, so nothing written.")
    else:
        print("\nDRY-RUN complete. Re-run with commit=true to apply.")

    if refused:
        sys.exit(3)


if __name__ == "__main__":
    main()
