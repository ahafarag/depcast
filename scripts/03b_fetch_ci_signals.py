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

def search_bot_prs(pkg, version, publish_dt, token, window_days=30):
    """
    Search for Dependabot / Renovate PRs bumping pkg to version.
    Counts: total opened, merged, closed-without-merge (rejected).
    Returns (total, merged, rejected, rejection_rate_or_None).
    """
    queries = [
        f'"{pkg}" "{version}" is:pr author:app/dependabot',
        f'"{pkg}" "{version}" is:pr author:app/renovate',
        f'"bump {pkg}" "{version}" is:pr',
        f'"update {pkg}" "to {version}" is:pr',
    ]

    url = "https://api.github.com/search/issues"
    all_prs = {}

    for q in queries:
        try:
            r = _gh_search(url, {"q": q, "sort": "created", "order": "desc", "per_page": 100}, token)
            if r.status_code == 200:
                for pr in r.json().get("items", []):
                    all_prs[pr["html_url"]] = pr
            time.sleep(1.5)
        except Exception as e:
            print(f"\n  WARN (bot_prs): {e}", end="")
            time.sleep(2)

    if not all_prs:
        return 0, 0, 0, None

    prs = list(all_prs.values())

    # Narrow to window after publish if we have the date
    if publish_dt:
        window_end = publish_dt + timedelta(days=window_days)
        in_window = []
        for pr in prs:
            created_str = pr.get("created_at", "")
            if not created_str:
                continue
            try:
                created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                if publish_dt <= created_dt <= window_end:
                    in_window.append(pr)
            except Exception:
                continue
        if in_window:
            prs = in_window

    total    = len(prs)
    merged   = sum(1 for p in prs if p.get("pull_request", {}).get("merged_at"))
    rejected = sum(1 for p in prs
                   if p.get("state") == "closed"
                   and not p.get("pull_request", {}).get("merged_at"))

    rejection_rate = rejected / total if total > 0 else None
    return total, merged, rejected, rejection_rate


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
            time.sleep(1.5)
        except Exception as e:
            print(f"\n  WARN (ci_issues): {e}", end="")
            time.sleep(2)

    return len(urls)

# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fetch CI-based compatibility signals")
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""),
                        help="GitHub personal access token (public_repo scope)")
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
        total_prs, merged_prs, rejected_prs, rejection_rate = search_bot_prs(
            pkg, str(version), pub_dt, args.token
        )

        # CI-failure keyword signals in 72h window
        ci_failure_count = search_ci_failure_issues(
            pkg, str(version), pub_dt, args.token
        )

        records.append({
            "package":           pkg,
            "breaking_version":  version,
            "published_at":      pub_dt.strftime("%Y-%m-%d %H:%M UTC") if pub_dt else None,
            "bot_prs_total":     total_prs,
            "bot_prs_merged":    merged_prs,
            "bot_prs_rejected":  rejected_prs,
            "pr_rejection_rate": round(rejection_rate, 4) if rejection_rate is not None else None,
            "ci_failure_issues": ci_failure_count,
        })

        # Incremental save
        pd.DataFrame(records).to_csv(OUTPUT, index=False)

        rr_str = f"reject={rejection_rate:.2f}" if rejection_rate is not None else "reject=n/a"
        print(f"prs={total_prs}  merged={merged_prs}  {rr_str}  ci_issues={ci_failure_count}")

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
