"""
DepCast Phase 3 — Script 08
Fetch PyPI metadata: published_at and weekly_downloads for pypi_breaking_releases.csv

APIs used:
  - https://pypi.org/pypi/{package}/json          (release timestamps, GitHub URL)
  - https://pypistats.org/api/packages/{pkg}/recent  (weekly download counts)

HOW TO RUN:
  python scripts/08_fetch_pypi_metadata.py
"""

import requests
import pandas as pd
import time
import os

INPUT_FILE  = "data/pypi_breaking_releases.csv"
OUTPUT_FILE = "data/pypi_breaking_releases.csv"
DELAY       = 1.2


def fetch_pypi_release_info(package, version):
    """Return (published_at, github_url) for a specific package version."""
    url = f"https://pypi.org/pypi/{package}/json"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return None, None
        data    = r.json()
        info    = data.get("info", {})
        releases = data.get("releases", {})

        # published_at: first file upload time for that version
        pub_at = None
        files  = releases.get(version, [])
        if files:
            pub_at = files[0].get("upload_time", None)

        # GitHub URL: check project_urls then home_page
        github_url = None
        project_urls = info.get("project_urls") or {}
        for key, val in project_urls.items():
            if val and "github.com" in val:
                github_url = val.rstrip("/")
                break
        if not github_url:
            hp = info.get("home_page", "") or ""
            if "github.com" in hp:
                github_url = hp.rstrip("/")

        return pub_at, github_url
    except Exception:
        return None, None


def fetch_weekly_downloads(package):
    """Return last-week download count from pypistats.org."""
    url = f"https://pypistats.org/api/packages/{package.lower()}/recent"
    try:
        r = requests.get(url, timeout=15, headers={"Accept": "application/json"})
        if r.status_code == 200:
            return r.json().get("data", {}).get("last_week", 0)
    except Exception:
        pass
    return 0


def main():
    print(f"\n{'='*60}")
    print("DepCast Phase 3 — Script 08: PyPI Metadata Fetch")
    print(f"{'='*60}\n")

    df = pd.read_csv(INPUT_FILE)
    print(f"Loaded {len(df)} releases from {INPUT_FILE}")

    unknown_pub  = (df["published_at"].astype(str).isin(["unknown", "nan", ""])).sum()
    zero_dl      = (df["weekly_downloads"].fillna(0) == 0).sum()
    print(f"  published_at unknown : {unknown_pub}/{len(df)}")
    print(f"  weekly_downloads = 0: {zero_dl}/{len(df)}\n")

    if "github_url" not in df.columns:
        df["github_url"] = None

    for i, row in df.iterrows():
        pkg     = row["package"]
        version = str(row["breaking_version"])
        needs_pub = str(row["published_at"]) in ("unknown", "nan", "")
        needs_dl  = row["weekly_downloads"] == 0

        if not needs_pub and not needs_dl:
            print(f"  [{pkg}=={version}] already complete, skipping")
            continue

        print(f"  [{pkg}=={version}] fetching...", end=" ", flush=True)

        if needs_pub or pd.isna(row.get("github_url")):
            pub_at, gh_url = fetch_pypi_release_info(pkg, version)
            if needs_pub and pub_at:
                df.at[i, "published_at"] = pub_at
            if gh_url:
                df.at[i, "github_url"] = gh_url
        else:
            pub_at = row["published_at"]

        if needs_dl:
            dl = fetch_weekly_downloads(pkg)
            df.at[i, "weekly_downloads"] = dl
        else:
            dl = row["weekly_downloads"]

        pub_str = df.at[i, "published_at"]
        print(f"pub={pub_str}  dl={dl:,}")
        time.sleep(DELAY)

    df.to_csv(OUTPUT_FILE, index=False)

    filled_pub = (~df["published_at"].astype(str).isin(["unknown", "nan", ""])).sum()
    filled_dl  = (df["weekly_downloads"].fillna(0) > 0).sum()
    print(f"\n{'='*60}")
    print(f"DONE — saved {OUTPUT_FILE}")
    print(f"  published_at filled : {filled_pub}/{len(df)}")
    print(f"  weekly_downloads > 0: {filled_dl}/{len(df)}")
    print(f"{'='*60}\n")
    print("NEXT STEP: run script 09 to compute V(r) via Python AST diffing.")


if __name__ == "__main__":
    main()
