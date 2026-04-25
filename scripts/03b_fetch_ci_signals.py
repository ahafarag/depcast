"""
DepCast Phase 1 — Script 03b
Fetch CI-based compatibility signals: Dependabot/Renovate PR rejection rates
and CI-failure keyword signals from GitHub.

WHY THIS IS BETTER THAN ISSUE COUNTS (script 03):
  Script 03 counts GitHub issues mentioning a package after release.
  For packages released before ~2018, GitHub search history is sparse —
  many true-positive signals return zero because the indexed history
  doesn't reach that far back.

  CI signals are more reliable and objective:
  - Dependabot/Renovate automatically open PRs for every dependency bump
  - If consumer CI fails on the PR, the PR is closed without merging
  - PR rejection rate = closed_unmerged / total_opened is a clean D(t) proxy
    that works even for releases from 2015–2020 because PR records survive
  - CI-failure keyword search ("CI failed", "build failed") adds a second
    corroborating signal in the 72h post-publish window

HOW TO RUN:
  python scripts/03b_fetch_ci_signals.py --token YOUR_GITHUB_TOKEN

  A GitHub personal access token with public_repo scope is required.
  Tokens: https://github.com/settings/tokens

OUTPUT: data/ci_signals.csv
  Columns: package, breaking_version, published_at,
           bot_prs_total, bot_prs_merged, bot_prs_rejected,
           pr_rejection_rate, ci_failure_issues
"""

import requests
import pandas as pd
import time
import os
import argparse
from datetime import datetime, timedelta, timezone

# ── Environment setup ──────────────────────────────────────────────────────

def load_env_file():
    """Load .env from repo root into os.environ (does not overwrite)."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(repo_root, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

load_env_file()

RELEASES_FILE = "data/breaking_releases.csv"
SIGNALS_FILE  = "data/propagation_signals.csv"
OUTPUT        = "data/ci_signals.csv"
os.makedirs("data", exist_ok=True)

# Seed list mirrors script 03 — used when breaking_releases.csv is absent
SEED_RELEASES = [
    ("lodash",       "4.0.0"),  ("express",      "4.0.0"),
    ("react",        "16.0.0"), ("react",        "17.0.0"), ("react",        "18.0.0"),
    ("webpack",      "4.0.0"),  ("webpack",      "5.0.0"),
    ("babel-core",   "7.0.0"),
    ("typescript",   "2.0.0"),  ("typescript",   "3.0.0"),  ("typescript",   "4.0.0"),
    ("angular",      "2.0.0"),  ("vue",          "3.0.0"),
    ("moment",       "2.0.0"),  ("axios",        "1.0.0"),
    ("jest",         "27.0.0"), ("jest",         "28.0.0"),
    ("eslint",       "6.0.0"),  ("eslint",       "7.0.0"),  ("eslint",       "8.0.0"),
    ("mocha",        "6.0.0"),  ("mocha",        "8.0.0"),
    ("chalk",        "5.0.0"),  ("node-fetch",   "3.0.0"),
    ("uuid",         "8.0.0"),  ("uuid",         "9.0.0"),
    ("glob",         "8.0.0"),  ("glob",         "9.0.0"),
    ("rimraf",       "4.0.0"),  ("mkdirp",       "1.0.0"),
    ("semver",       "7.0.0"),
    ("commander",    "8.0.0"),  ("commander",    "9.0.0"),
    ("yargs",        "17.0.0"), ("dotenv",       "16.0.0"),
    ("mongoose",     "6.0.0"),  ("mongoose",     "7.0.0"),
    ("sequelize",    "6.0.0"),  ("typeorm",      "0.3.0"),
    ("graphql",      "16.0.0"), ("apollo-server","3.0.0"),
    ("next",         "13.0.0"), ("next",         "14.0.0"),
    ("nuxt",         "3.0.0"),  ("gatsby",       "4.0.0"),
    ("tailwindcss",  "3.0.0"),  ("postcss",      "8.0.0"),
    ("rollup",       "3.0.0"),
    ("vite",         "3.0.0"),  ("vite",         "4.0.0"),
    ("prettier",     "3.0.0"),
]

# ── GitHub helpers ─────────────────────────────────────────────────────────

def get_headers(token):
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"token {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _gh_search(url, params, token, retry=True):
    """GET GitHub search with automatic rate-limit retry."""
    r = requests.get(url, headers=get_headers(token), params=params, timeout=15)
    if r.status_code in (403, 429) and retry:
        reset = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
        wait  = max(reset - time.time(), 1)
        print(f"\n  [rate-limit — waiting {wait:.0f}s]", end="", flush=True)
        time.sleep(wait + 2)
        r = requests.get(url, headers=get_headers(token), params=params, timeout=15)
    return r


def fetch_publish_date(pkg, version):
    """Return the exact UTC publish datetime for pkg@version from npm registry."""
    try:
        r = requests.get(f"https://registry.npmjs.org/{pkg}", timeout=10)
        if r.status_code == 200:
            date_str = r.json().get("time", {}).get(version)
            if date_str:
                return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except Exception:
        pass
    return None

# ── Signal collectors ──────────────────────────────────────────────────────

# Dependabot public launch date — bot PRs don't exist before this for any repo.
# For releases published before this date, we search all historical bot PRs
# (retroactive scans) rather than filtering to a post-publish window.
_DEPENDABOT_LAUNCH = datetime(2018, 6, 1, tzinfo=timezone.utc)


def search_bot_prs(pkg, version, publish_dt, token, window_days=90):
    """
    Search for Dependabot / Renovate PRs bumping pkg to version.
    Counts: total opened, merged, closed-without-merge (rejected).
    Returns (total, merged, rejected, rejection_rate_or_None).

    Window strategy:
    - Release pre-2018-06: use ALL historical bot PRs (Dependabot retroactively
      scans repos and creates PRs years after the release; these represent
      real consumer rejection signals even if created in 2020+).
    - Release post-2018-06: narrow to window_days after publish (default 90d).
    """
    # Title-specific patterns reduce false positives for large packages
    # (e.g. "react" appearing in "@types/react" or "react-dom" bump titles).
    queries = [
        f'"bump {pkg}" "to {version}" is:pr author:app/dependabot',
        f'"bump {pkg}" "{version}" is:pr author:app/dependabot',
        f'"update dependency {pkg}" "{version}" is:pr author:app/renovate',
        f'"update {pkg}" "to {version}" is:pr author:app/renovate',
    ]

    url = "https://api.github.com/search/issues"
    all_prs = {}

    for q in queries:
        try:
            r = _gh_search(url, {"q": q, "sort": "created", "order": "desc", "per_page": 100}, token)
            if r.status_code == 200:
                for pr in r.json().get("items", []):
                    all_prs[pr["html_url"]] = pr
            time.sleep(2.1)
        except Exception as e:
            print(f"\n  WARN (bot_prs): {e}", end="")
            time.sleep(2)

    if not all_prs:
        return 0, 0, 0, None, []

    prs = list(all_prs.values())

    # For post-Dependabot releases, narrow to the post-publish window.
    # For pre-Dependabot releases, keep all historical PRs — they represent
    # retroactive ecosystem rejection evidence, which is the only available signal.
    pre_dependabot = publish_dt is None or publish_dt < _DEPENDABOT_LAUNCH
    if not pre_dependabot and publish_dt:
        window_end = publish_dt + timedelta(days=window_days)
        in_window = [
            p for p in prs
            if _pr_in_window(p, publish_dt, window_end)
        ]
        prs = in_window  # empty list is correct when no PRs fall in window

    total    = len(prs)
    merged   = sum(1 for p in prs if p.get("pull_request", {}).get("merged_at"))
    rejected = sum(1 for p in prs
                   if p.get("state") == "closed"
                   and not p.get("pull_request", {}).get("merged_at"))

    rejection_rate = rejected / total if total > 0 else None
    return total, merged, rejected, rejection_rate, prs  # prs_list for Checks API reuse


def _pr_in_window(pr, start_dt, end_dt):
    created_str = pr.get("created_at", "")
    if not created_str:
        return False
    try:
        created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        return start_dt <= created_dt <= end_dt
    except Exception:
        return False


def search_ci_failure_issues(pkg, version, publish_dt, token, window_hours=72):
    """
    Count GitHub issues/PRs with CI-failure keywords created within
    window_hours after publish.  Returns 0 when publish_dt is unknown.
    """
    if not publish_dt:
        return 0

    url     = "https://api.github.com/search/issues"
    end_dt  = publish_dt + timedelta(hours=window_hours)
    start_s = publish_dt.strftime("%Y-%m-%dT%H:%M:%S")
    end_s   = end_dt.strftime("%Y-%m-%dT%H:%M:%S")

    queries = [
        f'"{pkg}" "CI failed" created:{start_s}..{end_s}',
        f'"{pkg}" "build failed" created:{start_s}..{end_s}',
        f'"{pkg}" "{version}" "failing" is:issue created:{start_s}..{end_s}',
    ]

    urls = set()
    for q in queries:
        try:
            r = _gh_search(url, {"q": q, "sort": "created", "order": "asc", "per_page": 50}, token)
            if r.status_code == 200:
                for item in r.json().get("items", []):
                    urls.add(item["html_url"])
            time.sleep(2.1)
        except Exception as e:
            print(f"\n  WARN (ci_issues): {e}", end="")
            time.sleep(2)

    return len(urls)

def fetch_npm_release_signals(pkg, version, publish_dt):
    """
    Query the npm registry for publisher-side breaking indicators.
    Requires no GitHub token and works for releases of any age.

    Returns a dict with:
      is_deprecated  — 1 if the maintainer deprecated this version (strong signal)
      days_to_patch  — days until the next same-major version was published
                       (None if no subsequent version exists)
      quick_patch    — 1 if a same-major patch landed within 14 days
                       (proxy: maintainer acknowledged breakage and rushed a fix)
    """
    result = {"is_deprecated": 0, "days_to_patch": None, "quick_patch": 0}
    try:
        r = requests.get(f"https://registry.npmjs.org/{pkg}", timeout=10)
        if r.status_code != 200:
            return result

        data     = r.json()
        versions = data.get("versions", {})
        times    = data.get("time", {})

        # Deprecation check
        v_meta = versions.get(str(version), {})
        if v_meta.get("deprecated"):
            result["is_deprecated"] = 1

        # Find fastest same-major successor
        if publish_dt:
            try:
                breaking = tuple(int(x) for x in str(version).split(".")[:3])
            except ValueError:
                return result

            best_days = None
            for v_str, v_time_str in times.items():
                if v_str in ("created", "modified"):
                    continue
                try:
                    v_tuple = tuple(int(x) for x in str(v_str).split(".")[:3])
                except ValueError:
                    continue
                if len(v_tuple) < 3 or len(breaking) < 3:
                    continue
                if v_tuple[0] != breaking[0]:  # different major — not a patch
                    continue
                if v_tuple <= breaking:
                    continue
                try:
                    v_dt = datetime.fromisoformat(v_time_str.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if v_dt <= publish_dt:
                    continue
                days = (v_dt - publish_dt).days
                if best_days is None or days < best_days:
                    best_days = days

            result["days_to_patch"] = best_days
            if best_days is not None and best_days <= 14:
                result["quick_patch"] = 1

    except Exception:
        pass
    return result


def fetch_checks_api_failure_rate(prs, publish_dt, token,
                                  max_prs=15, window_hours=72):
    """
    Query GitHub Checks API on a sample of Dependabot/Renovate PRs to
    directly measure CI failure rate within window_hours of publish_dt.

    This is more precise than PR rejection rate, which conflates CI failure
    with maintainer preference, wrong version, review delay, etc.

    Strategy: prioritise rejected PRs in the sample (they're more likely to
    hold CI failures) and call the Checks API on each PR's head commit.

    Returns (prs_checked, prs_with_ci_failure, direct_ci_failure_rate_or_None).
    Rate limiting: ~2 API calls per PR; caller should limit max_prs accordingly.
    """
    if not prs:
        return 0, 0, None

    # Prioritise closed-unmerged PRs — they're the signal we care about most
    rejected = [p for p in prs
                if p.get("state") == "closed"
                and not p.get("pull_request", {}).get("merged_at")]
    rest     = [p for p in prs if p not in rejected]
    sample   = (rejected + rest)[:max_prs]

    checked = 0
    failed  = 0
    failure_conclusions = {"failure", "cancelled", "timed_out", "action_required"}

    for pr in sample:
        # Resolve the PR's REST API URL from the search result
        pr_api_url = pr.get("pull_request", {}).get("url", "")
        if not pr_api_url:
            html = pr.get("html_url", "")
            parts = html.replace("https://github.com/", "").split("/")
            if len(parts) >= 4:
                pr_api_url = (f"https://api.github.com/repos"
                              f"/{parts[0]}/{parts[1]}/pulls/{parts[3]}")
        if not pr_api_url:
            continue

        try:
            # Step 1: get PR details → head SHA + repo URL
            r = requests.get(pr_api_url, headers=get_headers(token), timeout=15)
            time.sleep(0.6)
            if r.status_code == 403:
                reset = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
                time.sleep(max(reset - time.time(), 1) + 2)
                r = requests.get(pr_api_url, headers=get_headers(token), timeout=15)
            if r.status_code != 200:
                continue

            pr_data  = r.json()
            head_sha = pr_data.get("head", {}).get("sha", "")
            repo_url = pr_data.get("base", {}).get("repo", {}).get("url", "")
            if not head_sha or not repo_url:
                continue

            # Step 2: get check-runs for the PR head commit
            r2 = requests.get(
                f"{repo_url}/commits/{head_sha}/check-runs",
                headers=get_headers(token),
                params={"per_page": 100},
                timeout=15,
            )
            time.sleep(0.6)
            if r2.status_code != 200:
                continue

            runs = r2.json().get("check_runs", [])
            if not runs:
                continue
            checked += 1

            # A PR counts as "CI failed" if any check run concluded in failure
            # and (for dated releases) completed within window_hours of publish.
            pr_has_failure = False
            for run in runs:
                if run.get("conclusion") not in failure_conclusions:
                    continue
                if publish_dt:
                    completed_str = run.get("completed_at") or run.get("started_at", "")
                    if not completed_str:
                        continue
                    try:
                        run_dt = datetime.fromisoformat(
                            completed_str.replace("Z", "+00:00"))
                        if (run_dt - publish_dt).total_seconds() <= window_hours * 3600:
                            pr_has_failure = True
                            break
                    except Exception:
                        continue
                else:
                    pr_has_failure = True
                    break

            if pr_has_failure:
                failed += 1

        except Exception as e:
            print(f"\n  WARN (checks_api): {e}", end="")

    rate = failed / checked if checked > 0 else None
    return checked, failed, rate


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fetch CI-based compatibility signals")
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""),
                        help="GitHub personal access token (public_repo scope)")
    parser.add_argument("--checks-api", action="store_true",
                        help=("Also query GitHub Checks API on each Dependabot PR "
                              "for direct CI failure measurement (adds ~2 API calls "
                              "per PR; use --max-prs to control cost, default 10). "
                              "Outputs ci_check_failure_rate column."))
    parser.add_argument("--max-prs", type=int, default=10,
                        help="Max Dependabot PRs to check via Checks API per release "
                             "(default 10; only relevant with --checks-api)")
    args = parser.parse_args()

    if not args.token:
        raise RuntimeError(
            "GITHUB_TOKEN is required.\n"
            "  Option 1: add GITHUB_TOKEN=ghp_... to your .env file\n"
            "  Option 2: pass --token YOUR_TOKEN"
        )

    print(f"\n{'='*60}")
    print("DepCast Phase 1 — Script 03b: CI Signal Fetcher")
    print("Signals: Dependabot/Renovate PR rejection + CI failure keywords")
    print(f"{'='*60}\n")

    # ── Load release list ──
    if os.path.exists(RELEASES_FILE):
        rel_df   = pd.read_csv(RELEASES_FILE)
        releases = list(zip(rel_df["package"], rel_df["breaking_version"].astype(str)))
        print(f"Loaded {len(releases)} releases from {RELEASES_FILE}")
    else:
        releases = SEED_RELEASES
        print(f"Using built-in seed list ({len(releases)} releases)")

    # ── Pre-load publish dates from propagation_signals.csv to save npm calls ──
    pub_date_cache = {}
    if os.path.exists(SIGNALS_FILE):
        sig_df = pd.read_csv(SIGNALS_FILE)
        for _, row in sig_df.iterrows():
            ver_col = "version" if "version" in sig_df.columns else "breaking_version"
            key = (row["package"], str(row.get(ver_col, "")))
            pub_str = str(row.get("published_at", ""))
            if pub_str and pub_str not in ("", "nan", "None"):
                try:
                    pub_date_cache[key] = datetime.fromisoformat(
                        pub_str.replace(" UTC", "+00:00").replace("Z", "+00:00")
                    )
                except Exception:
                    pass
        print(f"Pre-loaded {len(pub_date_cache)} publish dates from {SIGNALS_FILE}\n")

    records = []
    n = len(releases)

    for i, item in enumerate(releases):
        pkg, version = (item[0], item[1]) if len(item) >= 2 else (item[0], "")
        print(f"[{i+1:02d}/{n}] {pkg}@{version} ... ", end="", flush=True)

        # Publish date: cache → npm
        pub_dt = pub_date_cache.get((pkg, str(version)))
        if not pub_dt:
            pub_dt = fetch_publish_date(pkg, str(version))
            time.sleep(0.3)

        # Dependabot/Renovate PR rejection signals
        # search_bot_prs returns the raw PR list via a second return path so
        # fetch_checks_api_failure_rate can reuse the same objects without a
        # second search query.  We capture prs_list here for that purpose.
        total_prs, merged_prs, rejected_prs, rejection_rate, prs_list = search_bot_prs(
            pkg, str(version), pub_dt, args.token
        )

        # Direct CI failure rate via GitHub Checks API (opt-in, costs ~2 calls/PR)
        ci_checked = ci_direct_failed = None
        ci_check_failure_rate = None
        if args.checks_api and prs_list:
            ci_checked, ci_direct_failed, ci_check_failure_rate = \
                fetch_checks_api_failure_rate(
                    prs_list, pub_dt, args.token, max_prs=args.max_prs
                )
            if ci_check_failure_rate is not None:
                ci_check_failure_rate = round(ci_check_failure_rate, 4)

        # CI-failure keyword signals in 72h window
        ci_failure_count = search_ci_failure_issues(
            pkg, str(version), pub_dt, args.token
        )

        # npm registry signals — no token required, works for all release ages
        npm_signals = fetch_npm_release_signals(pkg, str(version), pub_dt)
        time.sleep(0.3)

        records.append({
            "package":                pkg,
            "breaking_version":       version,
            "published_at":           pub_dt.strftime("%Y-%m-%d %H:%M UTC") if pub_dt else None,
            "bot_prs_total":          total_prs,
            "bot_prs_merged":         merged_prs,
            "bot_prs_rejected":       rejected_prs,
            "pr_rejection_rate":      round(rejection_rate, 4) if rejection_rate is not None else None,
            "ci_check_prs_sampled":   ci_checked,
            "ci_check_prs_failed":    ci_direct_failed,
            "ci_check_failure_rate":  ci_check_failure_rate,
            "ci_failure_issues":      ci_failure_count,
            "is_deprecated":          npm_signals["is_deprecated"],
            "days_to_patch":          npm_signals["days_to_patch"],
            "quick_patch":            npm_signals["quick_patch"],
        })

        # Incremental save
        pd.DataFrame(records).to_csv(OUTPUT, index=False)

        rr_str   = f"reject={rejection_rate:.2f}" if rejection_rate is not None else "reject=n/a"
        chk_str  = (f"  checks={ci_direct_failed}/{ci_checked}"
                    if ci_checked is not None else "")
        npm_str  = (f"deprecated={'Y' if npm_signals['is_deprecated'] else 'N'}"
                    + (f"  patch={npm_signals['days_to_patch']}d"
                       if npm_signals["days_to_patch"] is not None else ""))
        print(f"prs={total_prs}  merged={merged_prs}  {rr_str}{chk_str}  "
              f"ci_issues={ci_failure_count}  {npm_str}")

    df = pd.DataFrame(records)
    df.to_csv(OUTPUT, index=False)

    print(f"\n{'='*60}")
    print(f"DONE — {len(df)} releases processed")
    print(f"Saved: {OUTPUT}")

    has_signal = df[df["bot_prs_total"] > 0]
    print(f"Releases with bot-PR signal: {len(has_signal)}/{len(df)}")
    if len(has_signal) > 0:
        cols = ["package", "breaking_version", "bot_prs_total",
                "bot_prs_rejected", "pr_rejection_rate"]
        print(has_signal[cols].to_string(index=False))

    print(f"\nNext step: run script 05 to incorporate these signals into D(t).")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
