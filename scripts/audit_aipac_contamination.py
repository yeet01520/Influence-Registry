#!/usr/bin/env python3
"""
audit_aipac_contamination.py
=============================
Scans data/fec.json for members likely affected by the surname-fuzzy
matching bug in get_aipac_from_trackaipac() (fetcher v7 and earlier).

THE PATTERN
-----------
The bug occurred when a member's name was not in trackaipac_page.txt (because
they take $0 from AIPAC), but ANOTHER person with the same surname was. The
old code's last-name fuzzy fallback returned that other person's record.

Such records have a distinct fingerprint:
  - aipac_lobby_donors > 0     (some dollar amount picked up from the wrong row)
  - aipac_pacs == 0            (the correct member has no direct PAC receipts)
  - aipac_ie == 0              (no IE either)
  - aipac_sources is empty     (parser didn't extract sources for the wrong row)

A legitimately AIPAC-funded member (e.g. Foushee, Latimer, Schumer) will
almost always have NONZERO aipac_pacs or aipac_ie, because real recipients
have direct PAC contributions or IE support that lobby-bundling alone can't
produce. The audit thus does not flag those.

USAGE
-----
    python3 audit_aipac_contamination.py [path/to/fec.json]

Default path is data/fec.json relative to the current directory.

OUTPUT
------
Prints one report to stdout listing contaminated entries and the
recommended action ($0 the AIPAC fields until re-fetch with patched
fetcher). Exit code 0 if clean, 1 if any contamination found.
"""
import json
import sys
from pathlib import Path


def audit(fec_path: Path) -> int:
    with fec_path.open() as f:
        data = json.load(f)

    contaminated = []

    for name, entry in data.items():
        if not isinstance(entry, dict):
            continue

        pacs = entry.get("aipac_pacs", 0) or 0
        ie = entry.get("aipac_ie", 0) or 0
        lobby = entry.get("aipac_lobby_donors", 0) or 0
        sources = (entry.get("aipac_sources") or "").strip()

        # The surname-fuzzy-fallback signature:
        # lobby donors only, no PAC, no IE, no sources string.
        if pacs == 0 and ie == 0 and lobby > 0 and not sources:
            contaminated.append({
                "name": name,
                "aipac": entry.get("aipac", 0),
                "aipac_lobby_donors": lobby,
            })

    print(f"=== AIPAC contamination audit: {fec_path} ===\n")
    print(f"Total entries scanned: {len(data)}\n")

    if not contaminated:
        print("CLEAN: No entries match the surname-fuzzy-fallback bug signature.")
        return 0

    print(f"CONTAMINATED: {len(contaminated)} entries match the bug signature.\n")
    print("These members likely have $0 in real AIPAC receipts. The displayed")
    print("dollar amount comes from a different person who shares their surname")
    print("in trackaipac_page.txt (e.g. Thomas Massie KY-04 was being matched")
    print("to a Massachusetts Senate candidate of the same surname).\n")
    print("Recommended action: zero the AIPAC fields for these members, push,")
    print("then re-fetch full dataset with the patched fetcher.\n")

    contaminated.sort(key=lambda x: -x["aipac"])
    for r in contaminated:
        print(f"  {r['name']:35s}  displayed AIPAC: ${r['aipac']:>10,}  "
              f"(all in aipac_lobby_donors)")

    return 1


if __name__ == "__main__":
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "data/fec.json")
    if not path.exists():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(2)
    sys.exit(audit(path))
