#!/usr/bin/env python3
"""
extract_base64_photos.py

photo_overrides.json contains some entries with embedded base64 data URLs.
These work fine in the browser but break social previews (Bluesky/Twitter
cannot fetch data: URLs for og:image meta tags).

This script:
  1. Finds every entry in photo_overrides.json that starts with "data:"
  2. Decodes the base64 payload and saves it as /assets/photos/<slug>.jpg
  3. Updates photo_overrides.json to point at the new local path
  4. Backs up the original

Usage:
    python3 scripts/extract_base64_photos.py
    python3 scripts/extract_base64_photos.py --dry-run
"""
import argparse
import base64
import json
import re
import shutil
import sys
from pathlib import Path


def slugify(name: str) -> str:
    """Match the slug logic in generate_static_pages.py."""
    s = (name or "").lower()
    s = re.sub(r"['\u2018\u2019]", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def detect_mime_and_decode(data_url: str) -> tuple:
    """
    Given a data: URL, return (extension, raw_bytes) or (None, None) if invalid.

    photo_overrides.json claims `data:image/png;base64,...` but inspection shows
    most payloads are actually JPEG (start with /9j/ in base64, which is FFD8 in
    bytes = JPEG magic). We sniff the bytes after decoding rather than trusting
    the declared MIME.
    """
    m = re.match(r"^data:([^;]+);base64,(.+)$", data_url, re.DOTALL)
    if not m:
        return None, None
    payload = m.group(2).strip()
    try:
        raw = base64.b64decode(payload)
    except Exception as e:
        print(f"  [warn] base64 decode failed: {e}", file=sys.stderr)
        return None, None
    # Sniff the magic bytes
    if raw[:3] == b"\xff\xd8\xff":
        return "jpg", raw
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "png", raw
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "gif", raw
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp", raw
    # Unknown format; default to .jpg since most images on the web are JPEG
    print(f"  [warn] unknown image format, defaulting to .jpg", file=sys.stderr)
    return "jpg", raw


def main():
    parser = argparse.ArgumentParser(description="Decode base64 photos from photo_overrides.json.")
    parser.add_argument("--overrides", default="data/photo_overrides.json")
    parser.add_argument("--output-dir", default="assets/photos")
    parser.add_argument("--public-prefix", default="/assets/photos",
                        help="URL prefix used in the updated overrides (default: /assets/photos)")
    parser.add_argument("--backup", default="data/photo_overrides.json.bak")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    overrides_path = Path(args.overrides)
    output_dir = Path(args.output_dir)
    backup_path = Path(args.backup)

    with open(overrides_path, encoding="utf-8") as f:
        overrides = json.load(f)

    base64_names = [n for n, v in overrides.items() if isinstance(v, str) and v.startswith("data:")]
    print(f"Found {len(base64_names)} base64-encoded entries in {overrides_path}")

    if args.dry_run:
        for name in base64_names:
            slug = slugify(name)
            print(f"  [dry] would extract: {name} -> {args.public_prefix}/{slug}.jpg")
        return 0

    # Back up the overrides file
    shutil.copy2(overrides_path, backup_path)
    print(f"Backup written to: {backup_path}")

    output_dir.mkdir(parents=True, exist_ok=True)

    n_extracted = 0
    n_failed = 0
    for name in base64_names:
        slug = slugify(name)
        if not slug:
            print(f"  [skip] empty slug for {name!r}", file=sys.stderr)
            continue
        ext, raw = detect_mime_and_decode(overrides[name])
        if not raw:
            n_failed += 1
            continue
        out_path = output_dir / f"{slug}.{ext}"
        with open(out_path, "wb") as f:
            f.write(raw)
        # Update the overrides entry to point at the new file
        overrides[name] = f"{args.public_prefix}/{slug}.{ext}"
        n_extracted += 1
        print(f"  {name} -> {out_path} ({len(raw):,} bytes)")

    with open(overrides_path, "w", encoding="utf-8") as f:
        json.dump(overrides, f, indent=2, ensure_ascii=False)

    print(f"\nExtracted {n_extracted} photos to {output_dir}/")
    print(f"Updated {overrides_path} to reference local paths")
    if n_failed:
        print(f"Failed to decode {n_failed} entries (check warnings above)")
    return 0 if n_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
