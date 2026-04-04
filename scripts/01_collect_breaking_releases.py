"""
DepCast Phase 1 — Script 01
Collect confirmed breaking npm releases.

WHAT THIS DOES:
  - Queries the npm registry for packages known to have breaking changes
  - Uses a seed list of well-documented breaking releases from the literature
  - Fetches version metadata, publish timestamps, and dependent counts
  - Outputs: data/breaking_releases.csv

HOW TO RUN:
  python3 scripts/01_collect_breaking_releases.py

REQUIREMENTS:
  pip install requests pandas --break-system-packages
"""

import requests
import pandas as pd
import json
import time
import os
from datetime import datetime

OUTPUT = "data/breaking_releases.csv"

# ─────────────────────────────────────────────
# SEED LIST
# Well-documented breaking npm releases from:
# - Venturini et al. [3] dataset
# - Known high-impact incidents
# - npm changelog entries tagged "breaking"
#
# Format: (package, breaking_version, prior_stable_version, notes)
# ─────────────────────────────────────────────
SEED_RELEASES = [
    # Package                   Breaking ver   Prior stable   Notes
    ("lodash",                  "4.0.0",       "3.10.1",      "Major API restructure, _.pluck removed"),
    ("express",                 "4.0.0",       "3.21.2",      "Router API breaking change"),
    ("react",                   "16.0.0",      "15.6.2",      "Lifecycle methods deprecated/changed"),
    ("react",                   "17.0.0",      "16.14.0",     "Event delegation changed"),
    ("react",                   "18.0.0",      "17.0.2",      "Concurrent rendering, ReactDOM.render deprecated"),
    ("webpack",                 "4.0.0",       "3.12.0",      "Config API breaking change"),
    ("webpack",                 "5.0.0",       "4.46.0",      "Node.js polyfills removed"),
    ("babel-core",              "7.0.0",       "6.26.3",      "Package renamed to @babel/core"),
    ("typescript",              "2.0.0",       "1.8.10",      "strictNullChecks, module resolution changes"),
    ("typescript",              "3.0.0",       "2.9.2",       "Project references, tuples breaking"),
    ("typescript",              "4.0.0",       "3.9.7",       "Variadic tuple types, breaking editor changes"),
    ("angular",                 "2.0.0",       "1.8.3",       "Complete rewrite, no backward compatibility"),
    ("vue",                     "3.0.0",       "2.6.14",      "Composition API, breaking changes to filters"),
    ("moment",                  "2.0.0",       "1.7.2",       "Locale API breaking change"),
    ("axios",                   "1.0.0",       "0.27.2",      "CommonJS/ESM breaking change"),
    ("jest",                    "27.0.0",      "26.6.3",      "Default test environment changed to node"),
    ("jest",                    "28.0.0",      "27.5.1",      "Multiple breaking config changes"),
    ("eslint",                  "6.0.0",       "5.16.0",      "Node.js 6 dropped, plugin API changes"),
    ("eslint",                  "7.0.0",       "6.8.0",       "Node.js 8 dropped, config changes"),
    ("eslint",                  "8.0.0",       "7.32.0",      "CodePathAnalyzer breaking, plugin API change"),
    ("mocha",                   "6.0.0",       "5.2.0",       "Node.js 4/5/6 dropped, CLI API changes"),
    ("mocha",                   "8.0.0",       "7.2.0",       "Root hooks breaking change"),
    ("chalk",                   "5.0.0",       "4.1.2",       "ESM-only, breaks CommonJS require()"),
    ("node-fetch",              "3.0.0",       "2.6.7",       "ESM-only, breaks CommonJS require()"),
    ("uuid",                    "8.0.0",       "7.0.3",       "Deep import paths removed"),
    ("uuid",                    "9.0.0",       "8.3.2",       "Browser crypto API requirement"),
    ("glob",                    "8.0.0",       "7.2.3",       "Removed sync methods"),
    ("glob",                    "9.0.0",       "8.1.0",       "ESM-only, API changes"),
    ("rimraf",                  "4.0.0",       "3.0.2",       "ESM-only, CLI changes"),
    ("mkdirp",                  "1.0.0",       "0.5.5",       "Now returns Promise, breaks sync usage"),
    ("semver",                  "7.0.0",       "6.3.0",       "Removed deprecated methods"),
    ("commander",               "8.0.0",       "7.2.0",       "Option handling breaking change"),
    ("commander",               "9.0.0",       "8.3.0",       "ESM support, breaking option parsing"),
    ("yargs",                   "17.0.0",      "16.2.0",      "ESM support, Node 10 dropped"),
    ("dotenv",                  "16.0.0",      "15.0.2",      "Multiline value parsing changed"),
    ("mongoose",                "6.0.0",       "5.13.7",      "Schema strict mode, query changes"),
    ("mongoose",                "7.0.0",       "6.9.1",       "Promises-only, callback API removed"),
    ("sequelize",               "6.0.0",       "5.22.4",      "Model.sync breaking, DataTypes changes"),
    ("typeorm",                 "0.3.0",       "0.2.45",      "Complete API rewrite"),
    ("graphql",                 "16.0.0",      "15.8.0",      "Execution result changes"),
    ("apollo-server",           "3.0.0",       "2.25.2",      "Plugin API breaking change"),
    ("next",                    "13.0.0",      "12.3.1",      "App directory, breaking layout changes"),
    ("next",                    "14.0.0",      "13.5.4",      "Server actions, router changes"),
    ("nuxt",                    "3.0.0",       "2.15.8",      "Complete rewrite"),
    ("gatsby",                  "4.0.0",       "3.14.6",      "GraphQL breaking, plugin API changes"),
    ("tailwindcss",             "3.0.0",       "2.2.19",      "JIT-only, class purge changes"),
    ("postcss",                 "8.0.0",       "7.0.39",      "Plugin API breaking change"),
    ("rollup",                  "3.0.0",       "2.79.1",      "ES2020 baseline, config API changes"),
    ("vite",                    "3.0.0",       "2.9.13",      "Config API, import.meta.env changes"),
    ("vite",                    "4.0.0",       "3.2.5",       "Rollup 3, plugin API changes"),
    ("prettier",                "3.0.0",       "2.8.8",       "ESM-only, breaking plugin API"),
]

def fetch_npm_package_info(package, version):
    """Fetch package metadata from npm registry."""
    url = f"https://registry.npmjs.org/{package}/{version}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception as e:
        print(f"  ERROR fetching {package}@{version}: {e}")
        return None

def fetch_dependent_count(package):
    """Fetch approximate dependent count from npm registry."""
    url = f"https://registry.npmjs.org/-/v1/search?text=dependencies:{package}&size=1"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data.get("total", 0)
        return 0
    except:
        return 0

def fetch_weekly_downloads(package):
    """Fetch weekly download count from npm."""
    url = f"https://api.npmjs.org/downloads/point/last-week/{package}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return r.json().get("downloads", 0)
        return 0
    except:
        return 0

def main():
    os.makedirs("data", exist_ok=True)
    records = []

    print(f"\n{'='*60}")
    print("DepCast Phase 1 — Collecting Breaking Releases")
    print(f"{'='*60}")
    print(f"Total seed releases: {len(SEED_RELEASES)}\n")

    for i, (pkg, breaking_ver, prior_ver, notes) in enumerate(SEED_RELEASES):
        print(f"[{i+1:02d}/{len(SEED_RELEASES)}] {pkg}@{breaking_ver} ... ", end="", flush=True)

        # Fetch breaking version metadata
        breaking_info = fetch_npm_package_info(pkg, breaking_ver)
        time.sleep(0.3)  # Be polite to npm API

        # Fetch prior stable version metadata
        prior_info = fetch_npm_package_info(pkg, prior_ver)
        time.sleep(0.3)

        # Fetch dependent count
        dep_count = fetch_dependent_count(pkg)
        time.sleep(0.3)

        # Fetch weekly downloads
        downloads = fetch_weekly_downloads(pkg)
        time.sleep(0.3)

        if breaking_info:
            publish_time = breaking_info.get("dist", {})
            # npm stores publish time in the time field of the package root
            record = {
                "package":              pkg,
                "breaking_version":     breaking_ver,
                "prior_stable_version": prior_ver,
                "published_at":         breaking_info.get("time", {}).get(breaking_ver, "unknown") if isinstance(breaking_info.get("time"), dict) else breaking_info.get("_npmUser", {}).get("date", "unknown"),
                "description":          breaking_info.get("description", ""),
                "weekly_downloads":     downloads,
                "dependent_count":      dep_count,
                "has_changelog":        1 if "CHANGELOG" in str(breaking_info) or "changelog" in str(breaking_info) else 0,
                "notes":                notes,
                "label_breaking":       1,  # All seeds are confirmed breaking
            }
            records.append(record)
            print(f"OK  (deps: {dep_count:,}, downloads/week: {downloads:,})")
        else:
            # Still record with partial data
            record = {
                "package":              pkg,
                "breaking_version":     breaking_ver,
                "prior_stable_version": prior_ver,
                "published_at":         "unknown",
                "description":          "",
                "weekly_downloads":     downloads,
                "dependent_count":      dep_count,
                "has_changelog":        0,
                "notes":                notes,
                "label_breaking":       1,
            }
            records.append(record)
            print(f"PARTIAL (npm metadata unavailable)")

    # Save
    df = pd.DataFrame(records)
    df.to_csv(OUTPUT, index=False)

    print(f"\n{'='*60}")
    print(f"DONE — {len(df)} releases collected")
    print(f"Saved to: {OUTPUT}")
    print(f"\nTop packages by dependent count:")
    top = df.nlargest(10, "dependent_count")[["package", "breaking_version", "dependent_count", "weekly_downloads"]]
    print(top.to_string(index=False))
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
