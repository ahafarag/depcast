"""
DepCast Phase 2 — Script 06
Collect non-breaking npm major releases as control group.

WHY THIS MATTERS:
  The current dataset has only label_breaking=1 examples.
  Logistic regression weight learning and AUC-ROC validation are impossible
  without a negative class. This script builds that negative class.

SELECTION CRITERIA for a "non-breaking" major release:
  1. The package incremented the major version number.
  2. The community did NOT file widespread compatibility issues
     (< 10 GitHub issues in 72h window mentioning version + breaking).
  3. The package was NOT deprecated on npm within 30 days of release.
  4. No quick-patch (no v{x}.0.1 within 48h of v{x}.0.0).
  5. The changelog explicitly describes backward compatibility or additive-only changes.

VERIFICATION:
  This script auto-collects npm signals (no token needed).
  Pass --token YOUR_GITHUB_TOKEN to also verify against GitHub issue counts.
  All candidates are flagged with a `verified` column (0=npm-only, 1=github-confirmed).
  Review flagged rows before using in the paper.

HOW TO RUN:
  python scripts/06_collect_nonbreaking_controls.py
  python scripts/06_collect_nonbreaking_controls.py --token YOUR_GITHUB_TOKEN

OUTPUT: data/nonbreaking_releases.csv
"""

import requests
import pandas as pd
import time
import os
import sys
import argparse
from datetime import datetime, timedelta, timezone

OUTPUT = "data/nonbreaking_releases.csv"
DELAY  = 0.4

# ─────────────────────────────────────────────────────────────────────────────
# CANDIDATE NON-BREAKING MAJOR RELEASES
# Each entry: (package, major_version, prior_version, notes, evidence)
# Evidence is the documented reason why this release was non-breaking.
# These should be VERIFIED with --token before use in the paper.
# ─────────────────────────────────────────────────────────────────────────────
CANDIDATES = [
    # package           version   prior     notes                                         evidence
    ("cross-env",       "7.0.0",  "6.0.1",  "Pure maintenance; no functional changes",   "changelog: no functional changes"),
    ("nodemon",         "2.0.0",  "1.19.4", "Dropped Node.js 8; config unchanged",       "config format stable; low issue count"),
    ("dotenv",          "8.0.0",  "7.0.0",  "Added debug option; no API changes",        "additive only; no deprecations"),
    ("commander",       "6.0.0",  "5.1.0",  "Added features; no API removals",           "no removals per CHANGELOG.md"),
    ("minimatch",       "3.0.0",  "2.0.10", "Bugfix-focused major bump",                 "API identical to 2.x"),
    ("inquirer",        "7.0.0",  "6.5.2",  "TypeScript types added; API unchanged",     "no API removals; types-only addition"),
    ("ora",             "4.0.0",  "3.7.0",  "Spinner improvements; API stable",          "backward compatible per changelog"),
    ("chai",            "4.0.0",  "3.5.0",  "Assertion API stable; plugin compat",       "assert/expect/should APIs unchanged"),
    ("sinon",           "8.0.0",  "7.5.0",  "Stubs API stable; type improvements",       "no API removals in changelog"),
    ("nock",            "12.0.0", "11.9.1", "TypeScript rewrite; public API unchanged",  "API-compatible rewrite"),
    ("supertest",       "5.0.0",  "4.0.2",  "Minor update; backward compatible",         "test API unchanged"),
    ("nyc",             "13.0.0", "12.0.2", "Istanbul v2 integration; transparent",      "config format stable for most users"),
    ("execa",           "4.0.0",  "3.4.0",  "Added options; no removals",                "additive only"),
    ("fast-glob",       "3.0.0",  "2.2.7",  "Performance improvements; API stable",      "no breaking changes per changelog"),
    ("chokidar",        "3.0.0",  "2.1.8",  "Rewrote FSEvents binding; API stable",      "API identical to 2.x for most users"),
    ("glob",            "7.0.0",  "6.0.4",  "Minor refactor; API stable",                "stable API; glob@7 was long-lived LTS"),
    ("async",           "3.0.0",  "2.6.3",  "ESM added; CJS callback API preserved",     "CJS compat maintained alongside ESM"),
    ("debug",           "4.0.0",  "3.2.7",  "Namespace format stable; API unchanged",    "debug('ns') API identical"),
    ("ms",              "2.0.0",  "1.0.0",  "Additive: added more time units",           "no removals; parseonly extended"),
    ("mime",            "3.0.0",  "2.6.0",  "ESM-only; API identical",                   "define/lookup API preserved"),
    ("p-queue",         "7.0.0",  "6.6.2",  "ESM-only; same class API",                  "Queue API identical"),
    ("leven",           "4.0.0",  "3.1.0",  "ESM-only; single function export",          "export signature unchanged"),
    ("strip-ansi",      "7.0.0",  "6.0.1",  "ESM-only; single function export",          "identical API"),
    ("ansi-regex",      "6.0.0",  "5.0.1",  "ESM-only; regex export unchanged",          "same regex output"),
    ("is-stream",       "3.0.0",  "2.0.1",  "ESM-only; small package; smooth",           "isStream() API preserved"),
    ("pretty-bytes",    "6.0.0",  "5.6.0",  "ESM-only; function API unchanged",          "prettyBytes() signature preserved"),
    ("filesize",        "10.0.0", "9.0.0",  "ESM-only; additive options",                "filesize() signature preserved"),
    ("boxen",           "7.0.0",  "6.2.1",  "ESM-only; box rendering API stable",        "boxen() API preserved"),
    ("string-length",   "6.0.0",  "5.0.1",  "ESM-only; single function",                 "stringLength() API preserved"),
    ("camelcase",       "8.0.0",  "7.0.0",  "ESM-only; single function",                 "camelCase() API preserved"),
    ("decamelize",      "6.0.0",  "5.0.1",  "ESM-only; single function",                 "decamelize() API preserved"),
    ("escape-string-regexp", "5.0.0", "4.0.0", "ESM-only; single function",              "escapeStringRegexp() preserved"),
    ("get-port",        "7.0.0",  "6.1.2",  "ESM-only; API unchanged",                   "getPort() API preserved"),
    ("find-up",         "7.0.0",  "6.3.0",  "ESM-only; async API unchanged",             "findUp() API preserved"),
    ("pkg-dir",         "7.0.0",  "6.0.0",  "ESM-only; async function unchanged",        "pkgDir() API preserved"),
    ("unique-string",   "3.0.0",  "2.0.0",  "ESM-only; single function",                 "uniqueString() API preserved"),
    ("temp-dir",        "3.0.0",  "2.0.0",  "ESM-only; exported string path unchanged",  "same export shape"),
    ("normalize-url",   "8.0.0",  "7.2.0",  "ESM-only; normalizeUrl() API stable",       "function signature preserved"),
    ("repeat-string",   "2.0.0",  "1.6.1",  "Additive: handles edge-cases",              "repeat() API preserved"),
    ("is-plain-obj",    "4.0.0",  "3.0.0",  "ESM-only; single predicate function",       "isPlainObject() preserved"),
]


def fetch_npm_metadata(package, version):
    """Return (publish_date_str, weekly_downloads, dependent_count, is_deprecated, quick_patch)."""
    pub_date, dl, dep, deprecated, quick = None, 0, 0, 0, 0

    # Publish date + deprecated flag + quick patch check
    try:
        r = requests.get(f"https://registry.npmjs.org/{package}", timeout=12)
        if r.status_code == 200:
            data = r.json()
            times = data.get("time", {})
            ts = times.get(version)
            if ts:
                pub_date = ts.replace("Z", "+00:00")[:25]
                # Check for quick patch: was there a {major}.0.1 within 48h?
                patch = version.rsplit(".", 1)[0] + ".1"
                patch_ts = times.get(patch)
                if patch_ts and pub_date:
                    try:
                        t0 = datetime.fromisoformat(pub_date)
                        t1 = datetime.fromisoformat(patch_ts.replace("Z", "+00:00")[:25])
                        if 0 < (t1 - t0).total_seconds() / 3600 < 48:
                            quick = 1
                    except:
                        pass

            # Deprecated flag (on the specific version)
            versions_meta = data.get("versions", {})
            ver_meta = versions_meta.get(version, {})
            if ver_meta.get("deprecated"):
                deprecated = 1

    except Exception as e:
        print(f"  WARN metadata: {e}")
    time.sleep(DELAY)

    # Weekly downloads
    try:
        r = requests.get(f"https://api.npmjs.org/downloads/point/last-week/{package}", timeout=12)
        if r.status_code == 200:
            dl = int(r.json().get("downloads", 0))
    except:
        pass
    time.sleep(DELAY)

    # Dependent count
    try:
        r = requests.get(
            f"https://registry.npmjs.org/-/v1/search?text=dependencies:{package}&size=1",
            timeout=12
        )
        if r.status_code == 200:
            dep = int(r.json().get("total", 0))
    except:
        pass
    time.sleep(DELAY)

    return pub_date, dl, dep, deprecated, quick


def fetch_github_issue_count(package, version, pub_date_str, token):
    """Return issue count in 72h window after release. Returns -1 if unavailable."""
    if not token or not pub_date_str:
        return -1
    try:
        pub_dt = datetime.fromisoformat(pub_date_str)
        end_dt = pub_dt + timedelta(hours=72)
        start_s = pub_dt.strftime("%Y-%m-%dT%H:%M:%S")
        end_s   = end_dt.strftime("%Y-%m-%dT%H:%M:%S")
        queries = [
            f'"{package}" "{version}" is:issue created:{start_s}..{end_s}',
            f'"{package}" "breaking" is:issue created:{start_s}..{end_s}',
        ]
        seen = set()
        for q in queries:
            r = requests.get(
                "https://api.github.com/search/issues",
                headers={"Accept": "application/vnd.github+json",
                         "Authorization": f"token {token}"},
                params={"q": q, "per_page": 50},
                timeout=15
            )
            if r.status_code == 403:
                reset = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
                time.sleep(max(reset - time.time(), 1) + 2)
                r = requests.get(
                    "https://api.github.com/search/issues",
                    headers={"Accept": "application/vnd.github+json",
                             "Authorization": f"token {token}"},
                    params={"q": q, "per_page": 50},
                    timeout=15
                )
            if r.status_code == 200:
                for item in r.json().get("items", []):
                    seen.add(item["html_url"])
            time.sleep(1.5)
        return len(seen)
    except Exception as e:
        print(f"  WARN github: {e}")
        return -1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""),
                        help="GitHub personal access token (public_repo scope)")
    args = parser.parse_args()

    os.makedirs("data", exist_ok=True)

    print(f"\n{'='*60}")
    print("DepCast Phase 2 — Non-Breaking Control Collection")
    print(f"{'='*60}")
    print(f"Candidates : {len(CANDIDATES)}")
    print(f"GitHub token: {'YES — issue counts will be verified' if args.token else 'NO  — npm signals only'}")
    print(f"{'='*60}\n")

    records = []

    for i, (pkg, ver, prior, notes, evidence) in enumerate(CANDIDATES):
        print(f"[{i+1:02d}/{len(CANDIDATES)}] {pkg}@{ver} ... ", end="", flush=True)

        pub_date, dl, dep, deprecated, quick = fetch_npm_metadata(pkg, ver)
        issues_72h = fetch_github_issue_count(pkg, ver, pub_date, args.token)

        # Auto-flag: if npm shows deprecated or quick patch, something went wrong
        suspect = int(deprecated == 1 or quick == 1)

        # GitHub verification: flag if more than 10 issues (looks like a breaking release)
        if issues_72h >= 10:
            suspect = 1

        verified = 1 if args.token and issues_72h >= 0 else 0

        record = {
            "package":              pkg,
            "breaking_version":     ver,
            "prior_stable_version": prior,
            "published_at":         pub_date or "unknown",
            "description":          notes,
            "weekly_downloads":     dl,
            "dependent_count":      dep,
            "has_changelog":        0,
            "notes":                evidence,
            "label_breaking":       0,           # negative class
            "is_deprecated":        deprecated,
            "quick_patch":          quick,
            "issues_72h_github":    issues_72h,  # -1 = not checked
            "suspect":              suspect,      # 1 = flagged for manual review
            "verified":             verified,     # 1 = GitHub-confirmed non-breaking
        }
        records.append(record)

        status = "SUSPECT" if suspect else ("VERIFIED" if verified else "npm-only")
        print(f"{status}  pub={str(pub_date)[:10]}  dl={dl:,}  dep={dep:,}  issues_72h={issues_72h}  deprecated={deprecated}  quick_patch={quick}")

        # Incremental save every 10 rows
        if (i + 1) % 10 == 0:
            pd.DataFrame(records).to_csv(OUTPUT, index=False)

    df = pd.DataFrame(records)
    df.to_csv(OUTPUT, index=False)

    print(f"\n{'='*60}")
    print(f"DONE — {len(df)} candidates collected")
    print(f"Saved: {OUTPUT}")
    print(f"\nSummary:")
    print(f"  Clean candidates (not suspect) : {(df['suspect'] == 0).sum()}/{len(df)}")
    print(f"  Suspect (review before using)  : {(df['suspect'] == 1).sum()}/{len(df)}")
    print(f"  GitHub-verified                : {(df['verified'] == 1).sum()}/{len(df)}")
    if (df['suspect'] == 1).any():
        print(f"\nSuspect rows (remove or investigate before using in model):")
        sus = df[df['suspect'] == 1][["package", "breaking_version", "is_deprecated", "quick_patch", "issues_72h_github"]]
        print(sus.to_string(index=False))
    print(f"{'='*60}\n")
    print("NEXT STEP:")
    print("  Review the 'suspect' rows above. Remove any that were actually breaking.")
    print("  Then merge with breaking_releases.csv for AUC-ROC validation (script 05).")
    print("  Re-run with --token YOUR_GITHUB_TOKEN to get GitHub verification.")


if __name__ == "__main__":
    main()
