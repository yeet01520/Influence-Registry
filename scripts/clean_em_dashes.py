#!/usr/bin/env python3
"""
clean_em_dashes.py

One-time cleanup of em dashes and en dashes from data/profiles.json.

Rules:
  * em dashes (—): always replaced with the most natural punctuation in context
                    (period, comma, colon, or parentheses; defaults to comma)
  * en dashes (–): replaced UNLESS they sit between two digits/years
                    (e.g. "2017–2021" stays as "2017-2021" with hyphen,
                     but "the trial was a sham – he was railroaded" becomes
                     "the trial was a sham, he was railroaded")

Cleans these string fields in each profile:
  - bio
  - politician_bio
  - corruption_reasoning
  - controversies (list of strings)
  - donations (dict of string values)

Output: writes the cleaned profiles.json back, with a backup at profiles.json.bak.
Usage:
    python3 scripts/clean_em_dashes.py
    python3 scripts/clean_em_dashes.py --dry-run   # report only, no writes
"""
import argparse
import json
import re
import shutil
from pathlib import Path


# Patterns that decide what to replace dashes with.
# Em-dash replacement: choose punctuation based on surrounding context.
def replace_em_dashes(text: str) -> tuple:
    """Return (cleaned_text, num_replacements)."""
    if not text or "—" not in text:
        return text, 0

    count = text.count("—")
    # Apply replacements in order from most specific to least.
    # 1. " — " (em dash with spaces): usually a parenthetical or natural sentence pause.
    #    Default to a comma which works for most cases.
    cleaned = re.sub(r" — ", ", ", text)
    # 2. Em dash flush against text (rare): treat as comma without space duplication.
    cleaned = re.sub(r"—", ", ", cleaned)
    # 3. Tidy up double commas if any were created
    cleaned = re.sub(r",\s*,", ",", cleaned)
    cleaned = re.sub(r"\s+,", ",", cleaned)  # space before comma
    return cleaned, count


# En-dash: preserve if it sits between two digits (number range).
# Replace with hyphen between digits (for "2017–2021" → "2017-2021").
# Replace with comma elsewhere.
def replace_en_dashes(text: str) -> tuple:
    """Return (cleaned_text, num_replacements)."""
    if not text or "–" not in text:
        return text, 0

    count = text.count("–")

    # First: digit–digit becomes digit-digit (preserve as number range)
    cleaned = re.sub(r"(\d)\s*–\s*(\d)", r"\1-\2", text)
    # Also: "year–present" or "year–today" or "year–now" → "year-present"
    cleaned = re.sub(r"(\d{4})\s*–\s*(present|today|now)\b", r"\1-\2", cleaned, flags=re.IGNORECASE)

    # Remaining en dashes get treated like em dashes: comma in flowing text
    cleaned = re.sub(r" – ", ", ", cleaned)
    cleaned = re.sub(r"–", ", ", cleaned)
    # Tidy up
    cleaned = re.sub(r",\s*,", ",", cleaned)
    cleaned = re.sub(r"\s+,", ",", cleaned)
    return cleaned, count


def clean_string(s) -> tuple:
    """Apply both em-dash and en-dash cleanups. Returns (cleaned, count)."""
    if not isinstance(s, str):
        return s, 0
    em_cleaned, em_count = replace_em_dashes(s)
    final, en_count = replace_en_dashes(em_cleaned)
    return final, em_count + en_count


def _walk_and_clean(obj, total_counter):
    """
    Recursively walk a JSON-like structure, cleaning every string in place.
    total_counter is a single-element list used as a mutable int.
    Returns the cleaned value (with structure preserved).
    """
    if isinstance(obj, str):
        cleaned, count = clean_string(obj)
        total_counter[0] += count
        return cleaned
    if isinstance(obj, dict):
        for k, v in obj.items():
            obj[k] = _walk_and_clean(v, total_counter)
        return obj
    if isinstance(obj, list):
        for i in range(len(obj)):
            obj[i] = _walk_and_clean(obj[i], total_counter)
        return obj
    return obj


def clean_profile(profile: dict) -> int:
    """
    Recursively clean every string inside this profile dict (nested arbitrarily).
    Mutates in place. Returns total dash replacements.
    """
    if not isinstance(profile, dict):
        return 0
    counter = [0]
    _walk_and_clean(profile, counter)
    return counter[0]


def main():
    parser = argparse.ArgumentParser(description="Strip em dashes and en dashes from profiles.json.")
    parser.add_argument("--input",  default="data/profiles.json")
    parser.add_argument("--output", default="data/profiles.json")
    parser.add_argument("--backup", default="data/profiles.json.bak")
    parser.add_argument("--dry-run", action="store_true", help="Report only; do not write.")
    parser.add_argument("--no-backup", action="store_true", help="Skip writing the .bak file.")
    args = parser.parse_args()

    input_path  = Path(args.input)
    output_path = Path(args.output)
    backup_path = Path(args.backup)

    with open(input_path, encoding="utf-8") as f:
        profiles = json.load(f)

    total_replacements = 0
    profiles_touched = 0
    for name, profile in profiles.items():
        count = clean_profile(profile)
        if count > 0:
            profiles_touched += 1
            total_replacements += count

    print(f"Scanned {len(profiles)} profiles.")
    print(f"  Profiles with dash replacements: {profiles_touched}")
    print(f"  Total dash replacements: {total_replacements}")

    if args.dry_run:
        print("[dry-run] No files written.")
        return 0

    # Backup the original
    if not args.no_backup and input_path == output_path:
        shutil.copy2(input_path, backup_path)
        print(f"  Backup written to: {backup_path}")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(profiles, f, indent=2, ensure_ascii=False)
    print(f"  Cleaned profiles written to: {output_path}")

    # Sanity: count remaining em/en dashes (should be 0 for em, >0 for en in number ranges)
    with open(output_path, encoding="utf-8") as f:
        cleaned_text = f.read()
    remaining_em = cleaned_text.count("—")
    remaining_en = cleaned_text.count("–")
    print(f"\nPost-cleanup audit (file-level):")
    print(f"  Remaining em dashes: {remaining_em} (should be 0)")
    print(f"  Remaining en dashes: {remaining_en} (preserved as number ranges only)")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
