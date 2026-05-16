#!/usr/bin/env python3
"""
apply_trackaipac_parity.py

Forces the registry's AIPAC figure to be 1:1 with TrackAIPAC's PUBLISHED
per-member "Israel Lobby Total".

For every member matched to TrackAIPAC:
    aipac              = TrackAIPAC israel_lobby_total   (verbatim)
    aipac_pacs         = TrackAIPAC pacs
    aipac_ie           = TrackAIPAC ie
    aipac_lobby_donors = aipac - aipac_pacs - aipac_ie   (residual; ~0 in
                         TrackAIPAC's model, but computed so the historical
                         H2 invariant aipac==pacs+lobby+ie never breaks even
                         if a printed total differs from pacs+ie)
    special_interest_total = recomputed with the exact validate.py H3 formula

Writes (unless --dry-run):
    data/fec.json                 (patched in place)
    data/aipac.json               ({name: aipac_total} for Phase-2 AIPAC_DATA)
    index.html                    (BOTH embedded applyFECData payloads patched)
    data/raw/parity_change_log.txt (every old->new, plus unmatched report)

Members on TrackAIPAC marked "approved" (they reject AIPAC) -> aipac=0.
fec.json members NOT found on TrackAIPAC (cabinet appointees, name misses)
are PRESERVED unchanged and listed loudly for human review — we never
fabricate or zero a number we cannot source.

Usage:
    python3 apply_trackaipac_parity.py --dry-run
    python3 apply_trackaipac_parity.py            # writes changes
Paths can be overridden: --fec --index --ta --aipac-out
"""
import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

# ── normalize(): copied VERBATIM from validate.py so matching is identical ──
def normalize(n):
    n = unicodedata.normalize("NFD", n)
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    n = n.lower()
    n = re.sub(r"\b(jr|sr|ii|iii|iv)\.?\b", "", n)
    n = re.sub(r"[^a-z\s]", " ", n)
    return re.sub(r"\s+", " ", n).strip()


# Maps registry-normalized name -> TrackAIPAC-normalized key. Seeded from
# validate.py's TRACKAIPAC_NAME_FIXES; extended after the first dry run.
NAME_FIXES = {
    "james risch": "jim risch",
    "maria elvira salazar": "maria salazar",
    "mariannette miller meeks": "marianette miller meeks",
    "johnny olszewski": "john olszewski",
    "jefferson van drew": "jeff van drew",
    "joseph morelle": "joe morelle",
    "michael turner": "mike turner",
    "abraham hamadeh": "abe hamadeh",
    "jerrold nadler": "jerry nadler",
    "robert bresnahan": "rob bresnahan",
    "carlos gimenez": "carlos a gimenez",
}

SIT_KEYS = ("aipac_pacs", "aipac_lobby_donors", "aipac_ie", "fossil_fuels",
            "pharma", "defense", "finance", "tech", "nra")


def recompute_sit(rec):
    return sum(int(rec.get(k, 0) or 0) for k in SIT_KEYS)


def extract_balanced(s, start):
    """Return (json_str, end_index) for the {...} starting at s[start]=='{'."""
    depth, i, in_str, esc = 0, start, False, False
    while i < len(s):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return s[start:i + 1], i + 1
        i += 1
    raise ValueError("unbalanced braces in applyFECData payload")


def patch_index_html(path, ta_by_norm, log, dry):
    """Patch BOTH applyFECData({...}) payloads. Block 1 (full v7/v8 schema)
    gets aipac/pacs/ie/lobby/SIT updated; block 2 (display schema) gets its
    'aipac' field updated. The displayed AIPAC_DATA derives from these, so
    they must match data/fec.json or the SPA shows stale numbers."""
    src = Path(path).read_text(encoding="utf-8")
    out = []
    pos = 0
    blocks = 0
    touched = 0
    # ONLY the two real inline-JSON calls: `applyFECData({ ... })`. This
    # deliberately does NOT match `function applyFECData(fecData){`,
    # `applyFECData(data)` comments, or internal references.
    for m in re.finditer(r"applyFECData\(\s*\{", src):
        brace = src.index("{", m.start())
        if brace < pos:
            continue
        try:
            payload, end = extract_balanced(src, brace)
        except ValueError:
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        # Sanity: must be a non-empty {name: {... "aipac" ...}} map.
        if (not isinstance(data, dict) or not data or
                not any(isinstance(v, dict) and "aipac" in v
                        for v in data.values())):
            continue
        blocks += 1
        for name, rec in data.items():
            if not isinstance(rec, dict) or "aipac" not in rec:
                continue
            key = normalize(name)
            ta = ta_by_norm.get(NAME_FIXES.get(key, key))
            if ta is None:
                continue
            new_aipac = ta["israel_lobby_total"]
            if int(rec.get("aipac", 0) or 0) != new_aipac:
                touched += 1
            rec["aipac"] = new_aipac
            if "aipac_pacs" in rec:
                rec["aipac_pacs"] = ta["pacs"]
                rec["aipac_ie"] = ta["ie"]
                rec["aipac_lobby_donors"] = new_aipac - ta["pacs"] - ta["ie"]
                if "special_interest_total" in rec:
                    rec["special_interest_total"] = recompute_sit(rec)
        out.append(src[pos:brace])
        out.append(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        pos = end
    out.append(src[pos:])
    new_src = "".join(out)
    log.append(f"index.html: {blocks} applyFECData block(s), "
               f"{touched} member-fields changed")
    if not dry:
        Path(path).write_text(new_src, encoding="utf-8")
    return blocks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fec", default="data/fec.json")
    ap.add_argument("--index", default="index.html")
    ap.add_argument("--ta", default="data/raw/trackaipac.json")
    ap.add_argument("--aipac-out", default="data/aipac.json")
    ap.add_argument("--log", default="data/raw/parity_change_log.txt")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    fec = json.loads(Path(a.fec).read_text(encoding="utf-8"))
    ta_raw = json.loads(Path(a.ta).read_text(encoding="utf-8"))

    # Index TrackAIPAC by normalized name. On collision keep the tracked
    # record with the larger total (defensive against name clashes).
    ta_by_norm = {}
    for m in ta_raw.values():
        k = normalize(m["name"])
        prev = ta_by_norm.get(k)
        if prev is None or m["israel_lobby_total"] >= prev["israel_lobby_total"]:
            ta_by_norm[k] = m

    log = []
    changes = []
    matched = unmatched_fec = 0
    biggest = []

    for name, rec in fec.items():
        key = normalize(name)
        ta = ta_by_norm.get(NAME_FIXES.get(key, key))
        if ta is None:
            unmatched_fec += 1
            log.append(f"  UNMATCHED (preserved): {name}  "
                       f"[fec aipac=${int(rec.get('aipac',0) or 0):,}]")
            continue
        matched += 1
        old_aipac = int(rec.get("aipac", 0) or 0)
        old_sit = int(rec.get("special_interest_total", 0) or 0)
        new_aipac = ta["israel_lobby_total"]
        rec["aipac"] = new_aipac
        rec["aipac_pacs"] = ta["pacs"]
        rec["aipac_ie"] = ta["ie"]
        rec["aipac_lobby_donors"] = new_aipac - ta["pacs"] - ta["ie"]
        rec["aipac_sources"] = (rec.get("aipac_sources") or ta.get("pac_list", ""))
        new_sit = recompute_sit(rec)
        rec["special_interest_total"] = new_sit
        if old_aipac != new_aipac:
            d = new_aipac - old_aipac
            changes.append((abs(d), name, old_aipac, new_aipac,
                            old_sit, new_sit, ta["status"]))

    changes.sort(reverse=True)
    biggest = changes[:25]

    # Patch index.html embedded payloads.
    patch_index_html(a.index, ta_by_norm, log, a.dry_run)

    # aipac.json for Phase-2 AIPAC_DATA (name -> total).
    aipac_out = {n: int(r.get("aipac", 0) or 0) for n, r in fec.items()}

    # ── Report ──
    print(f"TrackAIPAC entries:        {len(ta_raw)}")
    print(f"fec.json members:          {len(fec)}")
    print(f"Matched & set 1:1:         {matched}")
    print(f"Unmatched (preserved):     {unmatched_fec}")
    print(f"Members whose aipac moved: {len(changes)}")
    print()
    print("Largest 25 aipac changes (TrackAIPAC 1:1):")
    print(f"  {'member':26s} {'old aipac':>14s} {'new aipac':>14s} "
          f"{'delta':>14s}  status")
    for _, nm, oa, na, _, _, st in biggest:
        print(f"  {nm:26s} ${oa:>12,} ${na:>12,} ${na-oa:>+12,}  {st}")

    if unmatched_fec:
        print(f"\n⚠ {unmatched_fec} fec.json members had NO TrackAIPAC match "
              f"and were left unchanged (see log). Review these — add a "
              f"NAME_FIXES entry if it's a name-format miss, or accept the "
              f"preserved value if they're genuinely off TrackAIPAC "
              f"(e.g. cabinet appointees).")
        shown = [l for l in log if l.startswith("  UNMATCHED")][:40]
        print("\n".join(shown))

    if not a.dry_run:
        Path(a.fec).write_text(json.dumps(fec, indent=2, ensure_ascii=False),
                               encoding="utf-8")
        Path(a.aipac_out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.aipac_out).write_text(
            json.dumps(aipac_out, indent=2, ensure_ascii=False),
            encoding="utf-8")
        Path(a.log).parent.mkdir(parents=True, exist_ok=True)
        with Path(a.log).open("w", encoding="utf-8") as f:
            f.write("TrackAIPAC 1:1 parity change log\n")
            f.write(f"matched={matched} unmatched={unmatched_fec} "
                    f"changed={len(changes)}\n\n")
            for _, nm, oa, na, os_, ns_, st in changes:
                f.write(f"{nm}: aipac ${oa:,} -> ${na:,} "
                        f"(SIT ${os_:,} -> ${ns_:,}) [{st}]\n")
            f.write("\n" + "\n".join(log) + "\n")
        print(f"\nWrote {a.fec}, {a.aipac_out}, {a.index}, {a.log}")
    else:
        print("\n[dry-run] no files written")


if __name__ == "__main__":
    main()
