"""
DepCast Phase 2a — Script 07
Automated breaking release discovery via npm deprecation + quick-patch sweep.

PATH A STRATEGY:
  Scans top N npm packages (by popularity) for versions that show breaking
  release signals — no GitHub token required, works for any release age.

  Signal 1 — Maintainer deprecation (confidence: HIGH)
    If a maintainer deprecated a specific version AND it was a major bump,
    they almost certainly did so because it broke downstream consumers.

  Signal 2 — Quick patch (confidence: MEDIUM-HIGH)
    If a same-major patch was published within PATCH_WINDOW days of a major
    version, the maintainer likely acknowledged breakage and rushed a fix.
    Works retroactively for all historical releases.

  Signal 3 — Major bump with very high exposure (confidence: LOW-MEDIUM)
    For packages with >1M weekly downloads, a major bump is included as a
    low-confidence candidate even without explicit signals, for manual review.

TARGET: 500–2,000 candidates from top 3,000 packages
RUNTIME: ~2–4 hours (network-bound, no GitHub token needed)
RESUMABLE: saves incrementally; use --resume to skip already-processed packages

HOW TO RUN:
  python scripts/07_npm_deprecation_sweep.py
  python scripts/07_npm_deprecation_sweep.py --max-packages 5000
  python scripts/07_npm_deprecation_sweep.py --resume
  python scripts/07_npm_deprecation_sweep.py --max-packages 1000 --min-downloads 50000
"""

import requests
import pandas as pd
import time
import os
import argparse
import json
import re
from datetime import datetime, timedelta, timezone

OUTPUT_CANDIDATES = "data/deprecation_sweep_candidates.csv"
OUTPUT_PROGRESS   = "data/deprecation_sweep_progress.json"
DELAY_SEARCH      = 0.25   # seconds between npm search API calls
DELAY_REGISTRY    = 0.35   # seconds between full registry fetches
PATCH_WINDOW_DAYS = 14     # quick_patch: patch within this many days
MIN_DOWNLOADS_DEFAULT = 10_000   # skip packages below this weekly download count
HIGH_EXPOSURE_THRESHOLD = 1_000_000  # include major bumps even without signals


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--max-packages", type=int, default=3000,
                   help="Maximum number of npm packages to scan (default: 3000)")
    p.add_argument("--min-downloads", type=int, default=MIN_DOWNLOADS_DEFAULT,
                   help="Minimum weekly downloads to consider a package (default: 10000)")
    p.add_argument("--resume", action="store_true",
                   help="Skip packages already in the progress file")
    p.add_argument("--patch-window", type=int, default=PATCH_WINDOW_DAYS,
                   help="Days within which a same-major patch counts as quick_patch (default: 14)")
    return p.parse_args()


# ── npm API helpers ────────────────────────────────────────────────────────

def fetch_popular_packages(max_packages, min_downloads):
    """
    Paginate npm search API to collect popular package names.
    Returns list of (package_name, current_weekly_downloads).
    Filters by min_downloads immediately to reduce registry calls.
    """
    packages = []
    page_size = 250
    seen = set()

    # Multiple search passes with different sort signals to maximise coverage
    search_queries = [
        ("popularity:1.0",            "popularity"),
        ("quality:1.0",               "quality"),
        ("keywords:javascript",       "javascript"),
        ("keywords:typescript",       "typescript"),
        ("keywords:node",             "node"),
        ("keywords:react",            "react ecosystem"),
        ("keywords:cli",              "cli tools"),
        ("keywords:webpack",          "build tools"),
        ("keywords:testing",          "testing"),
        ("keywords:database",         "database"),
    ]

    print(f"  Collecting up to {max_packages} packages via npm search...", flush=True)

    for query_text, label in search_queries:
        if len(packages) >= max_packages:
            break
        offset = 0
        while len(packages) < max_packages:
            url = ("https://registry.npmjs.org/-/v1/search"
                   f"?text={query_text}&size={page_size}&from={offset}")
            try:
                r = requests.get(url, timeout=15)
                if r.status_code != 200:
                    break
                data = r.json()
                objects = data.get("objects", [])
                if not objects:
                    break

                added = 0
                for obj in objects:
                    name = obj.get("package", {}).get("name", "")
                    if not name or name in seen:
                        continue
                    # Use search score as rough download proxy when stats unavailable
                    downloads = int(
                        obj.get("downloads", {}).get("weekly", 0)
                        or obj.get("score", {}).get("detail", {}).get("popularity", 0) * 1_000_000
                    )
                    seen.add(name)
                    packages.append((name, downloads))
                    added += 1

                offset += page_size
                if len(objects) < page_size:
                    break
                time.sleep(DELAY_SEARCH)
            except Exception as e:
                print(f"\n  WARN search ({label}): {e}")
                time.sleep(2)
                break

    print(f"  Collected {len(packages)} unique packages from search API")
    return packages[:max_packages]


def fetch_weekly_downloads_bulk(packages, chunk_size=50):
    """
    Fetch current weekly downloads from npm stats API.
    Skips scoped packages (@scope/name) in bulk requests — they must be
    fetched individually. Falls back to single fetch on any chunk error.
    Returns dict: package_name -> weekly_downloads.
    """
    result = {}
    all_names   = [p[0] for p in packages]
    scoped      = [n for n in all_names if n.startswith("@")]
    unscoped    = [n for n in all_names if not n.startswith("@")]

    # Bulk fetch for unscoped packages
    for i in range(0, len(unscoped), chunk_size):
        chunk = unscoped[i:i + chunk_size]
        url   = "https://api.npmjs.org/downloads/point/last-week/" + ",".join(chunk)
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict):
                    for k, v in data.items():
                        if isinstance(v, dict):
                            result[k] = int(v.get("downloads", 0))
        except Exception as e:
            print(f"\n  WARN bulk downloads: {e}")
        time.sleep(DELAY_SEARCH)

    # Individual fetch for scoped packages
    for name in scoped[:50]:  # cap to avoid excessive calls in smoke test
        try:
            r = requests.get(
                f"https://api.npmjs.org/downloads/point/last-week/{name}",
                timeout=10
            )
            if r.status_code == 200:
                result[name] = int(r.json().get("downloads", 0))
        except Exception:
            pass
        time.sleep(DELAY_SEARCH)

    found = sum(1 for v in result.values() if v > 0)
    print(f"  Download counts fetched: {found}/{len(all_names)} packages have data")
    return result


def fetch_registry_metadata(package):
    """
    Fetch full registry document for a package.
    Returns (versions_dict, times_dict) or (None, None) on failure.
    versions_dict: {version_str: {deprecated: ..., ...}}
    times_dict:    {version_str: iso_timestamp}
    """
    try:
        r = requests.get(f"https://registry.npmjs.org/{package}", timeout=15)
        if r.status_code == 200:
            data = r.json()
            return data.get("versions", {}), data.get("time", {})
    except Exception as e:
        print(f"\n  WARN registry ({package}): {e}")
    return None, None


# ── Version analysis ───────────────────────────────────────────────────────

def parse_semver_safe(v_str):
    """Return (major, minor, patch) tuple or None if unparseable.
    Strips pre-release suffixes (e.g. '2.0.0-beta.1' -> (2, 0, 0))."""
    try:
        # Strip pre-release / build metadata
        clean = re.split(r"[-+]", str(v_str))[0]
        parts = clean.split(".")
        if len(parts) < 2:
            return None
        nums = []
        for p in parts[:3]:
            if not p.isdigit():
                return None
            nums.append(int(p))
        # Pad to 3 elements
        while len(nums) < 3:
            nums.append(0)
        return tuple(nums)
    except Exception:
        return None


def find_major_bumps(versions_dict, times_dict, patch_window_days):
    """
    Identify major version bumps and annotate them with breaking signals.

    Returns list of dicts, one per candidate breaking release:
      package, breaking_version, prior_stable_version, published_at,
      is_deprecated, days_to_patch, quick_patch, discovery_method, confidence
    """
    # Parse and sort all stable versions (exclude pre-releases)
    parsed = []
    for v_str, v_meta in versions_dict.items():
        t = parse_semver_safe(v_str)
        if t is None:
            continue
        # Skip pre-release versions: named tags AND numeric suffixes (e.g. 3.0.0-0, 3.0.0-1)
        v_lower = str(v_str).lower()
        if any(x in v_lower for x in ("-alpha", "-beta", "-rc", "-pre", "-next", "-canary")):
            continue
        if re.search(r"-\d+$", str(v_str)):   # numeric pre-release: 3.0.0-0, 3.0.0-2
            continue
        ts = times_dict.get(v_str, "")
        try:
            pub_dt = datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None
        except Exception:
            pub_dt = None
        deprecated = bool(v_meta.get("deprecated", False))
        parsed.append({
            "v_str":      v_str,
            "v_tuple":    t,
            "pub_dt":     pub_dt,
            "deprecated": deprecated,
        })

    if len(parsed) < 2:
        return []

    # Sort by version tuple
    parsed.sort(key=lambda x: x["v_tuple"])

    candidates = []

    for i, cur in enumerate(parsed):
        major, minor, patch = cur["v_tuple"]
        if major == 0:
            continue  # skip 0.x.x — pre-1.0 doesn't follow standard semver

        # Check if this is a major version bump (X.0.0 where X > prior major)
        if minor != 0 or patch != 0:
            continue  # only consider x.0.0 releases as potential major bumps

        # Cap to avoid noise from packages in rapid major-version churn
        if len(candidates) >= 10:
            break

        # Find the prior stable version (highest version with major-1)
        prior = None
        for j in range(i - 1, -1, -1):
            p = parsed[j]
            if p["v_tuple"][0] < major:
                prior = p
                break

        if prior is None:
            continue  # no prior major found

        # ── Signal 1: Deprecation ──
        is_deprecated = int(cur["deprecated"])

        # ── Signal 2: Quick patch ──
        days_to_patch = None
        quick_patch   = 0
        if cur["pub_dt"]:
            for other in parsed:
                ov = other["v_tuple"]
                if ov[0] != major or ov <= cur["v_tuple"]:
                    continue
                if other["pub_dt"] and other["pub_dt"] > cur["pub_dt"]:
                    days = (other["pub_dt"] - cur["pub_dt"]).days
                    if days_to_patch is None or days < days_to_patch:
                        days_to_patch = days
            # days_to_patch=0 means same-day release (likely coordinated, not emergency fix)
            if days_to_patch is not None and 1 <= days_to_patch <= patch_window_days:
                quick_patch = 1

        # ── Confidence score ──
        if is_deprecated and quick_patch:
            confidence     = 0.90
            discovery      = "deprecated+quick_patch"
        elif is_deprecated:
            confidence     = 0.70
            discovery      = "deprecated"
        elif quick_patch:
            confidence     = 0.60
            discovery      = "quick_patch"
        else:
            confidence     = 0.0   # no signal — filtered out unless very high exposure
            discovery      = "none"

        if confidence == 0.0:
            continue  # will be added later for high-exposure packages

        pub_str = cur["pub_dt"].strftime("%Y-%m-%dT%H:%M:%S+00:00") if cur["pub_dt"] else "unknown"

        candidates.append({
            "package":              None,   # filled by caller
            "breaking_version":     cur["v_str"],
            "prior_stable_version": prior["v_str"],
            "published_at":         pub_str,
            "is_deprecated":        is_deprecated,
            "days_to_patch":        days_to_patch,
            "quick_patch":          quick_patch,
            "discovery_method":     discovery,
            "confidence":           confidence,
            "label_breaking":       1,
        })

    return candidates


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    os.makedirs("data", exist_ok=True)

    print(f"\n{'='*60}")
    print("DepCast Phase 2a — npm Deprecation Sweep (Path A)")
    print(f"{'='*60}")
    print(f"Max packages  : {args.max_packages:,}")
    print(f"Min downloads : {args.min_downloads:,}/week")
    print(f"Patch window  : {args.patch_window} days")
    print(f"Resume mode   : {args.resume}")
    print(f"{'='*60}\n")

    # ── Load progress if resuming ──
    processed_pkgs = set()
    existing_candidates = []
    if args.resume and os.path.exists(OUTPUT_PROGRESS):
        with open(OUTPUT_PROGRESS, "r") as f:
            processed_pkgs = set(json.load(f))
        print(f"Resuming: {len(processed_pkgs)} packages already processed")
    if args.resume and os.path.exists(OUTPUT_CANDIDATES):
        existing_df = pd.read_csv(OUTPUT_CANDIDATES)
        existing_candidates = existing_df.to_dict("records")
        print(f"Resuming: {len(existing_candidates)} candidates already found\n")

    # ── Step 1: collect package list ──
    print("Step 1: Collecting popular packages from npm search...")
    raw_packages = fetch_popular_packages(args.max_packages, args.min_downloads)

    # ── Step 2: get accurate download counts in bulk ──
    print("\nStep 2: Fetching weekly download counts in bulk...")
    dl_map = fetch_weekly_downloads_bulk(raw_packages)

    # Filter by min_downloads and skip already-processed
    packages_to_scan = [
        name for name, _ in raw_packages
        if dl_map.get(name, 0) >= args.min_downloads
        and (not args.resume or name not in processed_pkgs)
    ]
    print(f"Packages to scan: {len(packages_to_scan):,}  "
          f"(after download filter + resume skip)\n")

    # ── Step 3: scan each package ──
    print("Step 3: Scanning registry metadata for breaking signals...\n")

    all_candidates = list(existing_candidates)
    total = len(packages_to_scan)

    for i, pkg in enumerate(packages_to_scan):
        weekly_dl = dl_map.get(pkg, 0)
        print(f"[{i+1:04d}/{total}] {pkg:<35} dl={weekly_dl:>12,}  ", end="", flush=True)

        versions_dict, times_dict = fetch_registry_metadata(pkg)
        time.sleep(DELAY_REGISTRY)

        if versions_dict is None:
            print("SKIP (registry error)")
            processed_pkgs.add(pkg)
            continue

        candidates = find_major_bumps(versions_dict, times_dict, args.patch_window)

        # Also include high-exposure packages with no explicit signal
        # (low-confidence candidates for manual review)
        if not candidates and weekly_dl >= HIGH_EXPOSURE_THRESHOLD:
            # Check if there are any major bumps at all (exclude pre-release versions)
            for v_str in versions_dict:
                v_lower = str(v_str).lower()
                if any(x in v_lower for x in ("-alpha","-beta","-rc","-pre","-next","-canary")):
                    continue
                if re.search(r"-\d+$", str(v_str)):
                    continue
                t = parse_semver_safe(v_str)
                if t and t[1] == 0 and t[2] == 0 and t[0] > 0:
                    ts = times_dict.get(v_str, "unknown")
                    candidates.append({
                        "package":              pkg,
                        "breaking_version":     v_str,
                        "prior_stable_version": "unknown",
                        "published_at":         ts[:25] if ts != "unknown" else "unknown",
                        "is_deprecated":        0,
                        "days_to_patch":        None,
                        "quick_patch":          0,
                        "discovery_method":     "high_exposure_major_bump",
                        "confidence":           0.30,
                        "label_breaking":       1,
                        "weekly_downloads":     weekly_dl,
                    })

        for c in candidates:
            c["package"]          = pkg
            c["weekly_downloads"] = weekly_dl
            all_candidates.append(c)

        status = (f"{len(candidates)} candidates  "
                  f"{[c['discovery_method'] for c in candidates]}"
                  if candidates else "no signal")
        print(status)

        # Save progress every 25 packages
        processed_pkgs.add(pkg)
        if (i + 1) % 25 == 0:
            _save(all_candidates, processed_pkgs)

    # ── Final save ──
    df = _save(all_candidates, processed_pkgs)

    print(f"\n{'='*60}")
    print(f"SWEEP COMPLETE")
    print(f"  Packages scanned : {len(processed_pkgs):,}")
    print(f"  Total candidates : {len(df):,}")

    if len(df) > 0:
        print(f"\nBreakdown by discovery method:")
        print(df["discovery_method"].value_counts().to_string())
        print(f"\nBreakdown by confidence tier:")
        bins   = [0, 0.35, 0.65, 0.75, 1.01]
        labels = ["LOW (<0.35)", "MEDIUM (0.35-0.65)", "HIGH (0.65-0.75)", "VERY HIGH (>0.75)"]
        df["conf_tier"] = pd.cut(df["confidence"], bins=bins, labels=labels)
        print(df["conf_tier"].value_counts().to_string())
        print(f"\nTop 20 candidates by confidence × downloads:")
        df["score"] = df["confidence"] * df["weekly_downloads"].fillna(0)
        top = df.nlargest(20, "score")[
            ["package", "breaking_version", "confidence", "discovery_method",
             "weekly_downloads", "days_to_patch"]]
        print(top.to_string(index=False))

    print(f"\nSaved to: {OUTPUT_CANDIDATES}")
    print(f"\nNEXT STEPS:")
    print(f"  1. Review high-confidence candidates (confidence >= 0.7)")
    print(f"     These are the most reliable for label_breaking=1")
    print(f"  2. Run scripts 02-04 on the expanded dataset")
    print(f"  3. Run 03 --token YOUR_TOKEN to get propagation signals")
    print(f"     for the top candidates by downloads")
    print(f"{'='*60}\n")


def _save(candidates, processed_pkgs):
    """Save candidates CSV and progress JSON. Returns DataFrame."""
    if not candidates:
        return pd.DataFrame()
    df = pd.DataFrame(candidates)
    # Deduplicate
    df = df.drop_duplicates(subset=["package", "breaking_version"])
    # Sort by confidence desc, then downloads desc
    df = df.sort_values(
        ["confidence", "weekly_downloads"],
        ascending=[False, False]
    ).reset_index(drop=True)
    df.to_csv(OUTPUT_CANDIDATES, index=False)

    with open(OUTPUT_PROGRESS, "w") as f:
        json.dump(list(processed_pkgs), f)

    return df


if __name__ == "__main__":
    main()
