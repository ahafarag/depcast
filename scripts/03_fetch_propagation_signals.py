"""
DepCast Phase 1 — Script 03 FIXED
Fetch propagation signals with DATE-FILTERED GitHub search.

THE PROBLEM WITH THE ORIGINAL SCRIPT:
  The original searched GitHub for issues mentioning the package,
  but found issues from ALL TIME — not just the 72h window after release.
  lodash@4.0.0 had 396 issues found, but 0 within 72h of publish date.
  This is because lodash@4.0.0 was published in 2016 — GitHub's search
  API returns results but the date filtering was not working correctly.

THE FIX:
  Use GitHub's 'created:' date operator to restrict search to
  the 72-hour window immediately after the npm publish date.
  e.g., created:2016-01-28..2016-01-31

HOW TO RUN:
  python scripts/03_fetch_propagation_signals.py --token YOUR_GITHUB_TOKEN

OUTPUT: data/propagation_signals.csv
"""

import requests
import pandas as pd
import time
import os
from datetime import datetime, timedelta, timezone

def load_env_file():
    """Load environment variables from the repo root .env file if present."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(repo_root, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)

load_env_file()
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
if not GITHUB_TOKEN:
    raise RuntimeError("Missing GITHUB_TOKEN. Add it to .env before running this script.")

OUTPUT = "data/propagation_signals.csv"
os.makedirs("data", exist_ok=True)

SEED_RELEASES = [
    ("lodash",       "4.0.0",  "Major API restructure, _.pluck removed"),
    ("express",      "4.0.0",  "Router API breaking change"),
    ("react",        "16.0.0", "Lifecycle methods deprecated/changed"),
    ("react",        "17.0.0", "Event delegation changed"),
    ("react",        "18.0.0", "Concurrent rendering, ReactDOM.render deprecated"),
    ("webpack",      "4.0.0",  "Config API breaking change"),
    ("webpack",      "5.0.0",  "Node.js polyfills removed"),
    ("babel-core",   "7.0.0",  "Package renamed to @babel/core"),
    ("typescript",   "2.0.0",  "strictNullChecks breaking"),
    ("typescript",   "3.0.0",  "Project references breaking"),
    ("typescript",   "4.0.0",  "Variadic tuple types breaking"),
    ("angular",      "2.0.0",  "Complete rewrite"),
    ("vue",          "3.0.0",  "Composition API breaking"),
    ("moment",       "2.0.0",  "Locale API breaking"),
    ("axios",        "1.0.0",  "CommonJS/ESM breaking"),
    ("jest",         "27.0.0", "Default env changed to node"),
    ("jest",         "28.0.0", "Multiple breaking config changes"),
    ("eslint",       "6.0.0",  "Node.js 6 dropped, plugin API changes"),
    ("eslint",       "7.0.0",  "Node.js 8 dropped"),
    ("eslint",       "8.0.0",  "CodePathAnalyzer breaking"),
    ("mocha",        "6.0.0",  "Node.js 4/5/6 dropped"),
    ("mocha",        "8.0.0",  "Root hooks breaking"),
    ("chalk",        "5.0.0",  "ESM-only, breaks CommonJS"),
    ("node-fetch",   "3.0.0",  "ESM-only, breaks CommonJS"),
    ("uuid",         "8.0.0",  "Deep import paths removed"),
    ("uuid",         "9.0.0",  "Browser crypto API requirement"),
    ("glob",         "8.0.0",  "Removed sync methods"),
    ("glob",         "9.0.0",  "ESM-only"),
    ("rimraf",       "4.0.0",  "ESM-only, CLI changes"),
    ("mkdirp",       "1.0.0",  "Now returns Promise"),
    ("semver",       "7.0.0",  "Removed deprecated methods"),
    ("commander",    "8.0.0",  "Option handling breaking"),
    ("commander",    "9.0.0",  "ESM support breaking"),
    ("yargs",        "17.0.0", "ESM support, Node 10 dropped"),
    ("dotenv",       "16.0.0", "Multiline value parsing changed"),
    ("mongoose",     "6.0.0",  "Schema strict mode"),
    ("mongoose",     "7.0.0",  "Promises-only, callbacks removed"),
    ("sequelize",    "6.0.0",  "Model.sync breaking"),
    ("typeorm",      "0.3.0",  "Complete API rewrite"),
    ("graphql",      "16.0.0", "Execution result changes"),
    ("apollo-server","3.0.0",  "Plugin API breaking"),
    ("next",         "13.0.0", "App directory breaking"),
    ("next",         "14.0.0", "Server actions breaking"),
    ("nuxt",         "3.0.0",  "Complete rewrite"),
    ("gatsby",       "4.0.0",  "GraphQL breaking"),
    ("tailwindcss",  "3.0.0",  "JIT-only"),
    ("postcss",      "8.0.0",  "Plugin API breaking"),
    ("rollup",       "3.0.0",  "ES2020 baseline"),
    ("vite",         "3.0.0",  "Config API changes"),
    ("vite",         "4.0.0",  "Rollup 3 breaking"),
    ("prettier",     "3.0.0",  "ESM-only, plugin API breaking"),
]

def get_headers():
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"token {GITHUB_TOKEN}"
    }

def fetch_publish_date(pkg, version):
    """Get exact npm publish date for a package version."""
    try:
        r = requests.get(f"https://registry.npmjs.org/{pkg}", timeout=10)
        if r.status_code == 200:
            times = r.json().get("time", {})
            date_str = times.get(version)
            if date_str:
                return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except:
        pass
    return None

def search_issues_in_window(pkg, version, publish_dt, window_hours=72):
    """
    Search GitHub issues mentioning the package AND version,
    created within window_hours after publish_dt.
    Uses the 'created:' date filter for precision.
    """
    if not publish_dt:
        return [], None

    # Define search window
    start = publish_dt
    end   = publish_dt + timedelta(hours=window_hours)

    # Format dates for GitHub search API
    start_str = start.strftime("%Y-%m-%dT%H:%M:%S")
    end_str   = end.strftime("%Y-%m-%dT%H:%M:%S")

    # Search query with date filter
    # Look for issues mentioning the package name near the release
    queries = [
        f'"{pkg}" "{version}" is:issue created:{start_str}..{end_str}',
        f'"{pkg}" "breaking" is:issue created:{start_str}..{end_str}',
        f'"{pkg}" "broke" is:issue created:{start_str}..{end_str}',
        f'"{pkg}" "regression" is:issue created:{start_str}..{end_str}',
    ]

    all_issues = {}
    for q in queries:
        try:
            r = requests.get(
                "https://api.github.com/search/issues",
                headers=get_headers(),
                params={"q": q, "sort": "created", "order": "asc", "per_page": 100},
                timeout=15
            )
            if r.status_code == 403:
                reset = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
                wait = max(reset - time.time(), 1)
                print(f"\n  [Rate limit — waiting {wait:.0f}s]", end="", flush=True)
                time.sleep(wait + 2)
                r = requests.get(
                    "https://api.github.com/search/issues",
                    headers=get_headers(),
                    params={"q": q, "sort": "created", "order": "asc", "per_page": 100},
                    timeout=15
                )
            if r.status_code == 200:
                for issue in r.json().get("items", []):
                    all_issues[issue["html_url"]] = issue
            time.sleep(1.5)
        except Exception as e:
            print(f"\n  ERROR: {e}", end="")
            time.sleep(2)

    all_issues = list(all_issues.values())

    # Count by time window
    windows = [6, 12, 24, 48, 72]
    counts = {h: 0 for h in windows}
    first_h = None

    for issue in all_issues:
        created_str = issue.get("created_at", "")
        if not created_str:
            continue
        try:
            created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            hours_after = (created_dt - publish_dt).total_seconds() / 3600
            if 0 <= hours_after <= window_hours:
                if first_h is None or hours_after < first_h:
                    first_h = hours_after
                for w in windows:
                    if hours_after <= w:
                        counts[w] += 1
        except:
            continue

    return counts, first_h, len(all_issues)

def main():
    print("\n" + "="*60)
    print("DepCast Phase 1 — Propagation Signals (FIXED with date filter)")
    print("="*60)
    print("This version uses GitHub's 'created:' date operator")
    print("to restrict search to 72h window after each release.")
    print("="*60 + "\n")

    records = []

    for i, (pkg, version, notes) in enumerate(SEED_RELEASES):
        print(f"[{i+1:02d}/{len(SEED_RELEASES)}] {pkg}@{version} ... ", end="", flush=True)

        # Get exact publish date
        publish_dt = fetch_publish_date(pkg, version)
        time.sleep(0.3)

        if not publish_dt:
            print("SKIP — no publish date found")
            records.append({
                "package": pkg, "version": version,
                "published_at": None, "total_in_window": 0,
                "first_issue_hours": None,
                "issues_6h": 0, "issues_12h": 0, "issues_24h": 0,
                "issues_48h": 0, "issues_72h": 0,
                "notes": notes, "label_breaking": 1
            })
            continue

        pub_str = publish_dt.strftime("%Y-%m-%d %H:%M UTC")

        # Search with date filter
        counts, first_h, total = search_issues_in_window(pkg, version, publish_dt)

        record = {
            "package":           pkg,
            "version":           version,
            "published_at":      pub_str,
            "total_in_window":   total,
            "first_issue_hours": round(first_h, 2) if first_h else None,
            "issues_6h":         counts[6],
            "issues_12h":        counts[12],
            "issues_24h":        counts[24],
            "issues_48h":        counts[48],
            "issues_72h":        counts[72],
            "notes":             notes,
            "label_breaking":    1,
        }
        records.append(record)

        # Save incrementally
        pd.DataFrame(records).to_csv(OUTPUT, index=False)

        signal = "✓ SIGNAL" if total > 0 else "○ none"
        t_str = f"first={first_h:.1f}h" if first_h else ""
        print(f"{signal}  total={total}  6h={counts[6]} 12h={counts[12]} 24h={counts[24]} 48h={counts[48]} 72h={counts[72]}  pub={pub_str[:10]}  {t_str}")

    df = pd.DataFrame(records)
    df.to_csv(OUTPUT, index=False)

    print(f"\n{'='*60}")
    print(f"DONE — {len(df)} releases processed")
    print(f"Saved: {OUTPUT}")

    has_signal = df[df["total_in_window"] > 0]
    print(f"\nReleases with signal: {len(has_signal)}/{len(df)}")
    if len(has_signal) > 0:
        print("\nReleases with propagation signal:")
        cols = ["package","version","published_at","total_in_window","first_issue_hours","issues_24h","issues_72h"]
        print(has_signal[cols].to_string(index=False))

    print(f"\n{'='*60}")
    print("If most are still 0, it means these old releases (2015-2022)")
    print("predate GitHub search API's indexed history.")
    print("In that case, use the V(r) findings as your primary empirical")
    print("contribution and frame propagation study as future work.")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
