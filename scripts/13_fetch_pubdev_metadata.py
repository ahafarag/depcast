"""
DepCast Phase 3 - Script 13
Fetch metadata for curated pub.dev (Dart/Flutter) breaking releases.

Uses pub.dev JSON API to fill published_at and download_count_30d.
Outputs: data/pubdev_breaking_releases.csv

HOW TO RUN:
  python scripts/13_fetch_pubdev_metadata.py
"""

import requests
import pandas as pd
import time
import os

OUTPUT_FILE = "data/pubdev_breaking_releases.csv"
DELAY       = 0.5
os.makedirs("data", exist_ok=True)

# Curated seed list: well-known Dart/Flutter packages with confirmed breaking
# major-version releases. Verified from pub.dev changelogs and migration guides.
SEED = [
    # package, breaking_version, prior_stable_version, description
    ("http",             "1.0.0", "0.13.6", "Dart HTTP client — async API overhaul"),
    ("dio",              "5.0.0", "4.0.6",  "HTTP client — interceptor/response API changes"),
    ("bloc",             "8.0.0", "7.0.0",  "BLoC state management — closeable streams"),
    ("flutter_bloc",     "8.0.0", "7.3.3",  "Flutter BLoC — provider/context API changes"),
    ("riverpod",         "1.0.0", "0.14.0", "State management — provider syntax overhaul"),
    ("riverpod",         "2.0.0", "1.0.6",  "State management — annotations + code gen"),
    ("go_router",        "6.0.0", "5.3.0",  "Router — ShellRoute/named route changes"),
    ("go_router",        "10.0.0","9.1.3",  "Router — StatefulShellRoute API changes"),
    ("mockito",          "5.0.0", "4.1.4",  "Mocking — null safety + code generation"),
    ("provider",         "6.0.0", "5.0.0",  "State management — null safety migration"),
    ("freezed",          "2.0.0", "1.1.3",  "Code gen — new union syntax, no more @nullable"),
    ("get_it",           "7.0.0", "6.1.0",  "Service locator — async init changes"),
    ("hive",             "2.0.0", "1.4.4",  "NoSQL DB — null safety, new adapter API"),
    ("drift",            "2.0.0", "1.7.2",  "SQLite ORM (formerly moor) — breaking API rename"),
    ("sqflite",          "2.0.0", "1.3.2",  "SQLite plugin — null safety migration"),
    ("firebase_core",    "2.0.0", "1.24.0", "Firebase — new plugin architecture"),
    ("path_provider",    "2.0.0", "1.6.28", "Path provider — null safety, platform interface"),
    ("url_launcher",     "6.0.0", "5.7.10", "URL launcher — null safety, plugin interface"),
    ("image_picker",     "1.0.0", "0.8.7",  "Image picker — XFile API, null safety"),
    ("shared_preferences","2.0.0","0.5.13", "Prefs — null safety, async API changes"),
    ("intl",             "0.18.0","0.17.0", "Internationalisation — NumberFormat breaking changes"),
    ("collection",       "1.17.0","1.16.0", "Collections — QueueList/UnmodifiableSet changes"),
    ("test",             "1.22.0","1.21.7", "Test framework — expectAsync API changes"),
    ("build_runner",     "2.0.0", "1.12.2", "Build system — null safety, config changes"),
    ("json_annotation",  "4.0.0", "3.1.1",  "JSON codegen annotations — null safety"),
]


def fetch_metadata(package, version):
    """Return (published_at, download_count_30d, github_url) from pub.dev API."""
    try:
        r = requests.get(f"https://pub.dev/api/packages/{package}", timeout=10)
        if r.status_code != 200:
            return None, None, None
        data = r.json()

        # Find published_at for this specific version
        published_at = None
        for v in data.get("versions", []):
            if v["version"] == version:
                published_at = v.get("published")
                break

        # Get github_url from latest pubspec repository field
        github_url = None
        pubspec = data.get("latest", {}).get("pubspec", {})
        repo = pubspec.get("repository", "") or pubspec.get("homepage", "")
        if "github.com" in str(repo):
            github_url = repo

        return published_at, None, github_url
    except Exception:
        return None, None, None


def fetch_downloads(package):
    """Return download_count_30d from pub.dev score API."""
    try:
        r = requests.get(f"https://pub.dev/api/packages/{package}/score", timeout=10)
        if r.status_code == 200:
            return r.json().get("downloadCount30Days")
    except Exception:
        pass
    return None


def main():
    print(f"\n{'='*60}")
    print("DepCast Phase 3 - Script 13: pub.dev Metadata")
    print(f"{'='*60}")
    print(f"Packages to process: {len(SEED)}")
    print(f"{'='*60}\n")

    records = []
    for i, (pkg, brk_ver, prior_ver, desc) in enumerate(SEED, 1):
        print(f"[{i:02d}/{len(SEED)}] {pkg}=={brk_ver} ... ", end="", flush=True)

        pub_at, _, github_url = fetch_metadata(pkg, brk_ver)
        time.sleep(DELAY)

        dl = fetch_downloads(pkg)
        time.sleep(DELAY)

        print(f"published={pub_at[:10] if pub_at else 'N/A'}  "
              f"dl30d={dl if dl else 'N/A'}")

        records.append({
            "package":             pkg,
            "breaking_version":    brk_ver,
            "prior_stable_version": prior_ver,
            "published_at":        pub_at,
            "description":         desc,
            "download_count_30d":  dl,
            "github_url":          github_url,
            "label_breaking":      1,
            "ecosystem":           "pubdev",
        })

    df = pd.DataFrame(records)
    df.to_csv(OUTPUT_FILE, index=False)

    filled_pub = df["published_at"].notna().sum()
    filled_dl  = df["download_count_30d"].notna().sum()

    print(f"\n{'='*60}")
    print(f"DONE - {len(df)} releases in {OUTPUT_FILE}")
    print(f"published_at filled: {filled_pub}/{len(df)}")
    print(f"download_count_30d:  {filled_dl}/{len(df)}")
    print(f"{'='*60}\n")
    print("NEXT STEP: run script 14 (pub.dev V(r) via Dart AST)")


if __name__ == "__main__":
    main()
