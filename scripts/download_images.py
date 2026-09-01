#!/usr/bin/env python3
"""
Download the images listed in image-data.csv (column: image_path) from S3.

Files are saved flat as <photo_id>.jpg in OUTPUT_DIR.
Safe to re-run: files already present are skipped, so an interrupted run
just picks up where it left off.

Usage:
    python3 download_images.py                 # uses the paths configured below
    python3 download_images.py --overwrite     # re-fetch everything
"""

import argparse
import csv
import os
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# CONFIG - edit these two paths if the CSV or destination folder moves
# ---------------------------------------------------------------------------
CSV_PATH = "/Users/souveekpradhan/Downloads/images/image-data.csv"
OUTPUT_DIR = "/Users/souveekpradhan/Downloads/images/downloads"

URL_COLUMN = "image_path"     # CSV column holding the S3 URL
NAME_COLUMN = "photo_id"      # CSV column used as the filename
WORKERS = 8                   # parallel downloads
RETRIES = 3                   # attempts per file
TIMEOUT = 60                  # seconds per request
# ---------------------------------------------------------------------------

USER_AGENT = "image-downloader/1.0"


def read_rows(csv_path):
    """Return a list of (filename, url), de-duplicated by filename."""
    seen, rows = set(), []
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = {URL_COLUMN, NAME_COLUMN} - set(reader.fieldnames or [])
        if missing:
            sys.exit(f"CSV is missing column(s): {', '.join(sorted(missing))}\n"
                     f"Found: {reader.fieldnames}")
        for i, row in enumerate(reader, start=2):
            url = (row.get(URL_COLUMN) or "").strip()
            if not url:
                print(f"  ! line {i}: empty {URL_COLUMN}, skipped")
                continue
            ext = os.path.splitext(urlparse(url).path)[1] or ".jpg"
            name = f"{(row.get(NAME_COLUMN) or f'row{i}').strip()}{ext}"
            if name in seen:
                continue
            seen.add(name)
            rows.append((name, url))
    return rows


def fetch(url, dest, retries, overwrite):
    """Download one URL. Returns 'ok' or 'skipped'; raises on failure."""
    if dest.exists() and dest.stat().st_size > 0 and not overwrite:
        return "skipped"
    tmp = dest.with_suffix(dest.suffix + ".part")
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp, open(tmp, "wb") as out:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    out.write(chunk)
            if tmp.stat().st_size == 0:
                raise IOError("empty response body")
            tmp.replace(dest)
            return "ok"
        except Exception as exc:                      # noqa: BLE001
            last_err = exc
            # 403/404 won't fix themselves - don't waste retries on them
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (403, 404, 410):
                break
            if attempt < retries:
                time.sleep(2 ** (attempt - 1))
    tmp.unlink(missing_ok=True)
    raise RuntimeError(str(last_err))


def main():
    ap = argparse.ArgumentParser(description="Download images listed in a CSV.")
    ap.add_argument("--csv", default=CSV_PATH, type=Path, help=f"input CSV (default: {CSV_PATH})")
    ap.add_argument("--out", default=OUTPUT_DIR, type=Path, help=f"output folder (default: {OUTPUT_DIR})")
    ap.add_argument("--workers", type=int, default=WORKERS, help=f"parallel downloads (default: {WORKERS})")
    ap.add_argument("--retries", type=int, default=RETRIES, help=f"attempts per file (default: {RETRIES})")
    ap.add_argument("--overwrite", action="store_true", help="re-download files that already exist")
    args = ap.parse_args()

    rows = read_rows(args.csv)
    args.out.mkdir(parents=True, exist_ok=True)
    print(f"CSV : {args.csv}")
    print(f"OUT : {args.out}")
    print(f"{len(rows)} image(s) listed\n")

    ok = skipped = 0
    failures = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch, url, args.out / name, args.retries, args.overwrite):
                   (name, url) for name, url in rows}
        for n, future in enumerate(as_completed(futures), start=1):
            name, url = futures[future]
            try:
                result = future.result()
            except Exception as exc:                  # noqa: BLE001
                failures.append((name, url, str(exc)))
                print(f"[{n}/{len(rows)}] FAIL    {name}: {exc}")
            else:
                if result == "ok":
                    ok += 1
                else:
                    skipped += 1
                print(f"[{n}/{len(rows)}] {result:>7} {name}")

    print(f"\nDone. downloaded={ok}  already-present={skipped}  failed={len(failures)}")
    if failures:
        report = args.out / "failed.csv"
        with open(report, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["filename", "url", "error"])
            w.writerows(failures)
        print(f"Failed URLs written to {report} - re-run the script to retry just those.")
        sys.exit(1)


if __name__ == "__main__":
    main()
