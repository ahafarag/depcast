"""
Script 17 — Fetch real dependent_count via GitHub code search
=============================================================
Fixes task 1.3: dependent_count=0 in breaking_releases.csv,
nonbreaking_releases.csv, and sweep_top290_candidates.csv.

Source: GitHub code search total_count for '"pkg":' in package.json files.
This counts public GitHub repos that declare the package as a dependency —
a reliable, consistent proxy for downstream exposure.

Requires GITHUB_TOKEN env var or .env file in repo root.
Rate limit: 10 code-search requests/min (authenticated) -> sleep 7s.

Updates three CSVs in-place and writes a cache file so re-runs are free.

Run: python scripts/17_fetch_dependent_counts.py
"""

import csv
import json
import os
import time
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

DATA = Path(__file__).parent.parent / "data"
CACHE_FILE = DATA / "dependent_count_cache.json"

BREAKING    = DATA / "breaking_releases.csv"
NONBREAKING = DATA / "nonbreaking_releases.csv"
SWEEP       = DATA / "sweep_top290_candidates.csv"

GITHUB_SEARCH = "https://api.github.com/search/code?q={q}&per_page=1"
SLEEP_BETWEEN = 7.0   # 10 req/min limit for code search
RETRY_WAIT    = 65    # seconds to wait after 429/403


# ── helpers ───────────────────────────────────────────────────────────────────

def load_cache():
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return {}


def save_cache(cache):
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def get_token():
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        env_file = Path(__file__).parent.parent.parent / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("GITHUB_TOKEN="):
                    token = line.split("=", 1)[1].strip()
                    break
    return token


def fetch_dependents(pkg, cache, token):
    if pkg in cache:
        return cache[pkg]

    # search for "pkg": in package.json files — counts repos with this dependency
    q = urllib.parse.quote(f'"{pkg}": filename:package.json', safe="")
    url = GITHUB_SEARCH.format(q=q)
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "depcast/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                count = data.get("total_count", 0) or 0
                break
        except urllib.error.HTTPError as e:
            if e.code in (429, 403):
                wait = RETRY_WAIT * (attempt + 1)
                print(f"  {e.code} rate-limited on {pkg} — waiting {wait}s...")
                time.sleep(wait)
                continue
            print(f"  HTTP {e.code} for {pkg} — using 0")
            count = 0
            break
        except Exception as e:
            print(f"  Error for {pkg}: {e} — using 0")
            count = 0
            break
    else:
        print(f"  Gave up on {pkg} after retries — using 0")
        count = 0

    cache[pkg] = count
    save_cache(cache)
    time.sleep(SLEEP_BETWEEN)
    return count


# ── collect all unique packages ───────────────────────────────────────────────

def packages_from_csv(path):
    pkgs = set()
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pkgs.add(row["package"])
    return pkgs


# ── update CSV in-place ───────────────────────────────────────────────────────

def update_csv(path, counts, add_column=False):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        fieldnames = rows[0].keys() if rows else []

    # add column to fieldnames if missing
    fieldnames = list(fieldnames)
    if "dependent_count" not in fieldnames:
        if add_column:
            fieldnames.append("dependent_count")
        else:
            return  # no-op if column absent and not adding

    for row in rows:
        pkg = row["package"]
        row["dependent_count"] = counts.get(pkg, 0)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"  Updated {path.name} ({len(rows)} rows)")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    cache = load_cache()
    print(f"Cache loaded: {len(cache)} packages already fetched")

    token = get_token()
    print(f"Token: {'found' if token else 'NOT FOUND — unauthenticated (low rate limit)'}")

    all_pkgs = (
        packages_from_csv(BREAKING) |
        packages_from_csv(NONBREAKING) |
        packages_from_csv(SWEEP)
    )
    to_fetch = sorted(all_pkgs - set(cache.keys()))
    print(f"Packages to fetch: {len(to_fetch)} new / {len(all_pkgs)} total")

    for i, pkg in enumerate(to_fetch, 1):
        count = fetch_dependents(pkg, cache, token)
        print(f"  [{i}/{len(to_fetch)}] {pkg}: {count:,}")

    # print summary
    print("\nTop 10 by dependent_count:")
    top = sorted(
        [(p, c) for p, c in cache.items() if p in all_pkgs],
        key=lambda x: x[1], reverse=True
    )[:10]
    for pkg, count in top:
        print(f"  {pkg}: {count:,}")

    # update CSVs
    print("\nUpdating CSVs...")
    update_csv(BREAKING,    cache, add_column=False)
    update_csv(NONBREAKING, cache, add_column=False)
    update_csv(SWEEP,       cache, add_column=True)

    print(f"\nDone. Cache: {len(cache)} packages -> {CACHE_FILE.name}")


if __name__ == "__main__":
    main()
