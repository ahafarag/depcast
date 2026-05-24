"""
DepCast Phase 3 - Script 15
Fetch GitHub propagation signals for pub.dev (Dart/Flutter) breaking releases.

Uses same 72h GitHub Search API protocol as scripts 03c and 10.
Resumable: already-processed packages are skipped on re-run.

Reads:  data/pubdev_breaking_releases.csv
Writes: data/pubdev_propagation_signals.csv

HOW TO RUN:
  python scripts/15_fetch_pubdev_signals.py
  python scripts/15_fetch_pubdev_signals.py --max 5
"""

import requests
import pandas as pd
import time
import os
import argparse
from datetime import datetime, timedelta, timezone

INPUT_FILE  = "data/pubdev_breaking_releases.csv"
OUTPUT_FILE = "data/pubdev_propagation_signals.csv"
DELAY       = 2.1


def load_env_file():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for candidate in [repo_root, os.path.join(repo_root, "..")]:
        env_path = os.path.join(candidate, ".env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return

load_env_file()


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    p.add_argument("--max",   type=int, default=None)
    return p.parse_args()


def gh_headers(token):
    return {"Accept": "application/vnd.github+json",
            "Authorization": f"token {token}"}


def gh_search(q, token):
    url = "https://api.github.com/search/issues"
    for _ in range(2):
        r = requests.get(url, headers=gh_headers(token),
                         params={"q": q, "sort": "created", "order": "asc",
                                 "per_page": 100},
                         timeout=15)
        if r.status_code in (403, 429):
            reset = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
            wait  = max(reset - time.time(), 1)
            print(f"\n  [rate-limit - waiting {wait:.0f}s]", end="", flush=True)
            time.sleep(wait + 2)
            continue
        if r.status_code == 200:
            return r.json().get("items", [])
        return []
    return []


def fetch_signals(pkg, version, pub_dt, token, window_hours=72):
    """Return (first_issue_hours, counts_dict, total_issues)."""
    if not pub_dt:
        return None, {6: 0, 12: 0, 24: 0, 48: 0, 72: 0}, 0

    end_dt  = pub_dt + timedelta(hours=window_hours)
    start_s = pub_dt.strftime("%Y-%m-%dT%H:%M:%S")
    end_s   = end_dt.strftime("%Y-%m-%dT%H:%M:%S")

    # pub.dev-specific queries: package name, version, Flutter/Dart context
    queries = [
        f'"{pkg}" "{version}" is:issue created:{start_s}..{end_s}',
        f'"{pkg}" "breaking" is:issue created:{start_s}..{end_s}',
        f'"{pkg}" "migration" is:issue created:{start_s}..{end_s}',
        f'"{pkg}" "pubspec" is:issue created:{start_s}..{end_s}',
        f'"flutter pub" "{pkg}" is:issue created:{start_s}..{end_s}',
    ]

    seen = {}
    for q in queries:
        for item in gh_search(q, token):
            seen[item["html_url"]] = item
        time.sleep(DELAY)

    counts  = {h: 0 for h in [6, 12, 24, 48, 72]}
    first_h = None

    for item in seen.values():
        ts = item.get("created_at", "")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            h  = (dt - pub_dt).total_seconds() / 3600
            if 0 <= h <= window_hours:
                if first_h is None or h < first_h:
                    first_h = h
                for w in [6, 12, 24, 48, 72]:
                    if h <= w:
                        counts[w] += 1
        except Exception:
            pass

    return round(first_h, 2) if first_h is not None else None, counts, len(seen)


def parse_pub_dt(pub_str):
    if not pub_str or str(pub_str) in ("nan", "unknown", "None", ""):
        return None
    try:
        s = str(pub_str).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s[:25])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def main():
    args = parse_args()

    if not args.token:
        raise RuntimeError(
            "GITHUB_TOKEN required.\n"
            "  Add to .env file or pass --token YOUR_TOKEN"
        )

    candidates = pd.read_csv(INPUT_FILE)
    if args.max:
        candidates = candidates.head(args.max)

    done_keys = set()
    existing  = []
    if os.path.exists(OUTPUT_FILE):
        df_done = pd.read_csv(OUTPUT_FILE)
        for _, r in df_done.iterrows():
            done_keys.add((r["package"], str(r["breaking_version"])))
        existing = df_done.to_dict("records")
        print(f"Resuming: {len(done_keys)} already processed.")

    total   = len(candidates)
    records = list(existing)

    print(f"\n{'='*60}")
    print("DepCast Phase 3 - Script 15: pub.dev Propagation Signals")
    print(f"{'='*60}")
    print(f"Candidates  : {total}")
    print(f"Already done: {len(done_keys)}")
    print(f"To process  : {total - len(done_keys)}")
    print(f"{'='*60}\n")

    idx = 0
    for _, row in candidates.iterrows():
        pkg     = row["package"]
        version = str(row["breaking_version"])
        key     = (pkg, version)

        if key in done_keys:
            continue

        idx += 1
        remaining = total - len(done_keys)
        print(f"[{idx:03d}/{remaining}] {pkg}=={version} ... ", end="", flush=True)

        pub_dt = parse_pub_dt(row.get("published_at", ""))
        first_h, counts, total_issues = fetch_signals(pkg, version, pub_dt, args.token)

        record = {
            "package":             pkg,
            "breaking_version":    version,
            "published_at":        pub_dt.strftime("%Y-%m-%d %H:%M UTC") if pub_dt else None,
            "download_count_30d":  row.get("download_count_30d", 0),
            "total_in_window":     total_issues,
            "first_issue_hours":   first_h,
            "issues_6h":           counts[6],
            "issues_12h":          counts[12],
            "issues_24h":          counts[24],
            "issues_48h":          counts[48],
            "issues_72h":          counts[72],
            "label_breaking":      1,
            "ecosystem":           "pubdev",
        }
        records.append(record)
        done_keys.add(key)

        signal = "SIGNAL" if total_issues > 0 else "none"
        t_str  = f"first={first_h:.1f}h" if first_h else ""
        print(f"{signal}  total={total_issues}  "
              f"6h={counts[6]} 24h={counts[24]} 72h={counts[72]}  {t_str}")

        if len(records) % 5 == 0:
            pd.DataFrame(records).to_csv(OUTPUT_FILE, index=False)

    df = pd.DataFrame(records)
    df.to_csv(OUTPUT_FILE, index=False)

    print(f"\n{'='*60}")
    print(f"DONE - {len(df)} releases in {OUTPUT_FILE}")
    has_signal = df[df["total_in_window"] > 0]
    print(f"With signal : {len(has_signal)}/{len(df)}")
    if len(has_signal) > 0:
        med = has_signal["first_issue_hours"].dropna()
        if len(med):
            print(f"Median first_issue_h: {med.median():.1f}h")
    print(f"{'='*60}\n")
    print("NEXT STEP: run script 04 (SIR model) on pubdev_propagation_signals.csv,")
    print("           then update script 11 to include pub.dev in combined dataset.")


if __name__ == "__main__":
    main()
