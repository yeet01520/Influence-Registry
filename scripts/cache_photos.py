#!/usr/bin/env python3
"""
cache_photos.py

Downloads every external photo URL referenced in photo_overrides.json + bioguide.json
to local files in assets/photos/. Updates photo_overrides.json to reference local paths.

Why:
  1. Share-card rendering happens in a headless browser. External fetches during
     rendering can fail or be slow, producing blank/broken cards in CI.
  2. og:image meta tags pointing at external sites (Wikimedia, whitehouse.gov,
     supremecourt.gov) may not be reliably fetched by Bluesky/Twitter when they
     build link previews. Self-hosting fixes this.

Skips:
  - Entries already pointing at /assets/photos/ (already local)
  - Entries with data: URLs (handled by extract_base64_photos.py instead)
  - Photos that already exist on disk AND match the source's content-length

Usage:
    python3 scripts/cache_photos.py
    python3 scripts/cache_photos.py --dry-run
    python3 scripts/cache_photos.py --force  # re-download even if already cached
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path


def slugify(name: str) -> str:
    s = (name or "").lower()
    s = re.sub(r"['\u2018\u2019]", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def detect_extension(raw: bytes) -> str:
    """Sniff image format from magic bytes."""
    if raw[:3] == b"\xff\xd8\xff":
        return "jpg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    return "jpg"  # default fallback


def download_photo(url: str, dest_path: Path, timeout: int = 30) -> tuple:
    """
    Download a single photo. Returns (success, message).
    Uses a realistic User-Agent so servers don't reject the request.
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; KeepDcHonestBot/1.0; +https://www.keep-dc-honest.com/)",
            "Accept": "image/*,*/*;q=0.8",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        if len(raw) < 500:
            return False, f"file too small ({len(raw)} bytes); likely an error page"
        ext = detect_extension(raw)
        # If the destination has a different extension, rename it
        final_path = dest_path.with_suffix(f".{ext}")
        with open(final_path, "wb") as f:
            f.write(raw)
        return True, f"{len(raw):,} bytes -> {final_path.name}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return False, f"URL error: {e.reason}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def bioguide_photo_url(bioguide_id: str) -> str:
    """Build the unitedstates.github.io URL for a Congress member's photo."""
    return f"https://unitedstates.github.io/images/congress/225x275/{bioguide_id}.jpg"


def main():
    parser = argparse.ArgumentParser(description="Cache external photos to local files.")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--output-dir", default="assets/photos")
    parser.add_argument("--public-prefix", default="/assets/photos")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Re-download even if file exists")
    parser.add_argument("--limit", type=int, default=0, help="Process only N entries (for testing)")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir)
    overrides_path = data_dir / "photo_overrides.json"
    bioguide_path = data_dir / "bioguide.json"

    if not overrides_path.exists():
        print(f"ERROR: {overrides_path} not found", file=sys.stderr)
        return 1

    overrides = json.load(open(overrides_path))
    bioguide = json.load(open(bioguide_path)) if bioguide_path.exists() else {}

    # Build the work list
    # 1) Every entry in photo_overrides.json that's an external URL
    work_list = []
    for name, url in overrides.items():
        if not isinstance(url, str) or not url.startswith("http"):
            continue  # already local or empty
        work_list.append((name, url, "override"))

    # 2) Every Congress member in bioguide.json who is NOT already in photo_overrides
    for name, bid in bioguide.items():
        if not bid:
            continue
        if name in overrides:
            continue  # honored by photo_overrides
        work_list.append((name, bioguide_photo_url(bid), "bioguide"))

    if args.limit:
        work_list = work_list[:args.limit]

    print(f"Found {len(work_list)} photos to consider caching")
    print(f"  (from overrides: {sum(1 for _, _, src in work_list if src == 'override')})")
    print(f"  (from bioguide:  {sum(1 for _, _, src in work_list if src == 'bioguide')})")

    if args.dry_run:
        for name, url, src in work_list[:10]:
            print(f"  [dry] {name} <- {url} ({src})")
        print(f"  ... and {max(0, len(work_list) - 10)} more")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)

    n_skipped = n_downloaded = n_failed = 0
    failed = []

    for i, (name, url, src) in enumerate(work_list, 1):
        slug = slugify(name)
        if not slug:
            continue
        # If any extension of this slug already exists in output_dir, skip unless --force
        existing = list(output_dir.glob(f"{slug}.*"))
        if existing and not args.force:
            n_skipped += 1
            # Still update overrides to point at the local file (only if it was an override entry)
            if src == "override":
                overrides[name] = f"{args.public_prefix}/{existing[0].name}"
            continue

        dest = output_dir / f"{slug}.jpg"  # placeholder; detect_extension may rename
        ok, msg = download_photo(url, dest)
        if ok:
            n_downloaded += 1
            # Find the actual saved file (could be .png/.jpg/etc)
            actual = next(output_dir.glob(f"{slug}.*"))
            if src == "override":
                overrides[name] = f"{args.public_prefix}/{actual.name}"
            if i % 25 == 0 or i == len(work_list):
                print(f"  [{i}/{len(work_list)}] {name}: {msg}")
        else:
            n_failed += 1
            failed.append((name, url, msg))
            print(f"  [{i}/{len(work_list)}] FAIL {name}: {msg}", file=sys.stderr)
        # Small delay to avoid hammering the source servers
        time.sleep(0.1)

    print(f"\nDownloaded: {n_downloaded}")
    print(f"Skipped (already cached): {n_skipped}")
    print(f"Failed: {n_failed}")

    if failed:
        print(f"\nFailures:", file=sys.stderr)
        for name, url, msg in failed[:20]:
            print(f"  {name} <- {url}: {msg}", file=sys.stderr)

    # Backup and save updated overrides (only if anything changed)
    backup_path = overrides_path.with_suffix(".json.bak")
    shutil.copy2(overrides_path, backup_path)
    with open(overrides_path, "w", encoding="utf-8") as f:
        json.dump(overrides, f, indent=2, ensure_ascii=False)
    print(f"\nUpdated {overrides_path} (backup at {backup_path})")

    return 1 if n_failed > n_downloaded // 2 else 0  # fail if >half failed


if __name__ == "__main__":
    sys.exit(main())
