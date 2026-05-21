"""
DepCast — Script 00
Fix missing npm metadata in breaking_releases.csv.

Patches three fields that were blank after the initial data collection:
  published_at      — exact publish timestamp from the npm registry
  weekly_downloads  — current weekly download count from npm API
  dependent_count   — approximate dependent-package count via npm search API

No GitHub token required. All data comes from public npm APIs.

HOW TO RUN:
  python scripts/00_fix_npm_metadata.py

OUTPUT: data/breaking_releases.csv (updated in-place)
"""

import requests
import pandas as pd
import time
import os
from datetime import datetime, timezone

RELEASES_FILE = "data/breaking_releases.csv"
DELAY = 0.4  # seconds between API calls — polite to npm


def fetch_publish_date(package, version):
    """Return ISO publish timestamp for package@version, or None."""
    url = f"https://registry.npmjs.org/{package}"
    try:
        r = requests.get(url, timeout=12)
        if r.status_code == 200:
            times = r.json().get("time", {})
            ts = times.get(version)
            if ts:
                return ts.replace("Z", "+00:00")[:25]  # trim microseconds
    except Exception as e:
        print(f"  WARN published_at: {e}")
    return None


def fetch_weekly_downloads(package):
    """Return last-week download count from npm stats API."""
    url = f"https://api.npmjs.org/downloads/point/last-week/{package}"
    try:
        r = requests.get(url, timeout=12)
        if r.status_code == 200:
            return int(r.json().get("downloads", 0))
    except Exception as e:
        print(f"  WARN weekly_downloads: {e}")
    return 0


def fetch_dependent_count(package):
    """Return approximate number of packages that declare this as a dependency."""
    url = f"https://registry.npmjs.org/-/v1/search?text=dependencies:{package}&size=1"
    try:
        r = requests.get(url, timeout=12)
        if r.status_code == 200:
            return int(r.json().get("total", 0))
    except Exception as e:
        print(f"  WARN dependent_count: {e}")
    return 0


def main():
    if not os.path.exists(RELEASES_FILE):
        print(f"ERROR: {RELEASES_FILE} not found — run script 01 first.")
        return

    df = pd.read_csv(RELEASES_FILE)
    total = len(df)

    print(f"\n{'='*60}")
    print("DepCast — Fix npm Metadata")
    print(f"{'='*60}")
    print(f"Rows to update: {total}\n")

    # Track per-package publish times to avoid redundant full-registry calls
    pkg_times_cache = {}

    for i, row in df.iterrows():
        pkg = row["package"]
        ver = str(row["breaking_version"])

        needs_date = pd.isna(row.get("published_at")) or str(row.get("published_at", "")).strip() in ("", "unknown")
        needs_dl   = pd.isna(row.get("weekly_downloads")) or int(row.get("weekly_downloads", 0)) == 0
        needs_dep  = pd.isna(row.get("dependent_count")) or int(row.get("dependent_count", 0)) == 0

        label = f"[{i+1:02d}/{total}] {pkg}@{ver}"
        updates = []

        # published_at
        if needs_date:
            if pkg not in pkg_times_cache:
                try:
                    r = requests.get(f"https://registry.npmjs.org/{pkg}", timeout=12)
                    pkg_times_cache[pkg] = r.json().get("time", {}) if r.status_code == 200 else {}
                except:
                    pkg_times_cache[pkg] = {}
                time.sleep(DELAY)

            ts = pkg_times_cache[pkg].get(ver)
            if ts:
                df.at[i, "published_at"] = ts.replace("Z", "+00:00")[:25]
                updates.append(f"published_at={ts[:10]}")
            else:
                updates.append("published_at=NOT_FOUND")

        # weekly_downloads
        if needs_dl:
            dl = fetch_weekly_downloads(pkg)
            df.at[i, "weekly_downloads"] = dl
            updates.append(f"downloads={dl:,}")
            time.sleep(DELAY)

        # dependent_count
        if needs_dep:
            dep = fetch_dependent_count(pkg)
            df.at[i, "dependent_count"] = dep
            updates.append(f"dependents={dep:,}")
            time.sleep(DELAY)

        if updates:
            print(f"{label}  ->  {', '.join(updates)}")
        else:
            print(f"{label}  (already complete — skipped)")

        # Save incrementally every 10 rows
        if (i + 1) % 10 == 0:
            df.to_csv(RELEASES_FILE, index=False)

    df.to_csv(RELEASES_FILE, index=False)

    print(f"\n{'='*60}")
    print(f"DONE — {RELEASES_FILE} updated")
    print(f"\nCoverage after fix:")
    print(f"  published_at filled : {(df['published_at'] != 'unknown').sum()}/{total}")
    print(f"  weekly_downloads > 0: {(df['weekly_downloads'] > 0).sum()}/{total}")
    print(f"  dependent_count > 0 : {(df['dependent_count'] > 0).sum()}/{total}")
    print(f"\nTop 10 by weekly downloads:")
    top = df.nlargest(10, "weekly_downloads")[["package", "breaking_version", "weekly_downloads", "dependent_count"]]
    print(top.to_string(index=False))
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
