"""
DepCast Phase 3 - Script 14
Compute V(r) for pub.dev (Dart/Flutter) breaking releases.

METHOD:
  Downloads pub.dev package archives and extracts public top-level symbols
  from lib/*.dart files (the public API surface, not lib/src/).
  Public = top-level declarations whose name does NOT start with '_'.

  Symbols extracted per file:
    - class / abstract class / base class / final class / sealed class
    - mixin / mixin class
    - extension (named)
    - enum
    - typedef
    - top-level functions and getters

  V(r) = removed_symbols / max(prior_symbols, 1), clipped to [0,1]

Reads:  data/pubdev_breaking_releases.csv
Writes: data/pubdev_api_volatility.csv

HOW TO RUN:
  python scripts/14_pubdev_dart_volatility.py
  python scripts/14_pubdev_dart_volatility.py --max 5
"""

import requests
import tarfile
import re
import os
import io
import time
import argparse
import pandas as pd

INPUT_FILE  = "data/pubdev_breaking_releases.csv"
OUTPUT_FILE = "data/pubdev_api_volatility.csv"
DELAY       = 0.8
os.makedirs("data", exist_ok=True)

# Dart top-level declaration patterns — only from lib/*.dart (public API files)
# Order matters: more specific patterns before generic ones
DART_PATTERNS = [
    # class variants (captures name in group 1)
    re.compile(r'^(?:abstract\s+)?(?:base\s+)?(?:final\s+)?(?:sealed\s+)?'
               r'(?:mixin\s+)?class\s+([A-Z][A-Za-z0-9_]*)', re.MULTILINE),
    # standalone mixin
    re.compile(r'^mixin\s+([A-Z][A-Za-z0-9_]*)', re.MULTILINE),
    # extension with a name
    re.compile(r'^extension\s+([A-Z][A-Za-z0-9_]+)\s+on\b', re.MULTILINE),
    # enum
    re.compile(r'^enum\s+([A-Z][A-Za-z0-9_]*)', re.MULTILINE),
    # typedef
    re.compile(r'^typedef\s+([A-Za-z][A-Za-z0-9_]*)', re.MULTILINE),
    # top-level functions / getters (lowercase names, not starting with _)
    re.compile(r'^(?:Future<[^>]+?>|Stream<[^>]+?>|void|int|double|String|bool|'
               r'List<[^>]*?>|Map<[^>]*?>|(?:[A-Z][A-Za-z0-9_]*))\s+'
               r'([a-z][A-Za-z0-9_]*)\s*[\(\{]', re.MULTILINE),
]

EXCLUDE_PATHS = ('test/', 'example/', 'tool/', 'benchmark/', 'build/')


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--max", type=int, default=None)
    return p.parse_args()


def fetch_tarball(package, version):
    url = f"https://pub.dev/api/archives/{package}-{version}.tar.gz"
    try:
        r = requests.get(url, timeout=30, stream=True)
        if r.status_code == 200:
            return r.content
    except Exception:
        pass
    return None


def extract_dart_symbols(tarball_bytes):
    """
    Extract public top-level symbols from lib/*.dart files in a pub.dev tarball.
    Only processes the public API surface (lib/*.dart), not lib/src/.
    Returns a set of symbol name strings.
    """
    symbols = set()
    if not tarball_bytes:
        return symbols
    try:
        with tarfile.open(fileobj=io.BytesIO(tarball_bytes), mode="r:gz") as tar:
            for member in tar.getmembers():
                name = member.name
                # pub.dev tarballs have no top-level prefix — files start with lib/
                if not name.startswith("lib/"):
                    continue
                if not name.endswith(".dart"):
                    continue
                if any(name.startswith(exc) for exc in EXCLUDE_PATHS):
                    continue
                # Exclude re-export-only stub files (lib/{pkg}.dart) — they add
                # no symbols themselves; their exports are counted in lib/src/.
                # Include lib/src/**/*.dart since that is where Dart packages
                # define their actual public classes, mixins, enums, etc.
                # The lib/src/ "private by convention" rule applies to direct
                # imports, not to whether symbols are public declarations.
                try:
                    f = tar.extractfile(member)
                    if not f:
                        continue
                    content = f.read().decode("utf-8", errors="ignore")
                    # Remove single-line and block comments to avoid false matches
                    content = re.sub(r'//[^\n]*', '', content)
                    content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
                    for pat in DART_PATTERNS:
                        for m in pat.finditer(content):
                            sym = m.group(1).strip()
                            if sym and not sym.startswith("_"):
                                symbols.add(sym)
                except Exception:
                    continue
    except Exception:
        pass
    return symbols


def compute_v_score(prior_syms, breaking_syms):
    if not prior_syms and not breaking_syms:
        return 0.0, 0, 0, 0
    removed = prior_syms - breaking_syms
    n_prior = len(prior_syms)
    n_rem   = len(removed)
    n_add   = len(breaking_syms - prior_syms)
    V = min(n_rem / max(n_prior, 1), 1.0)
    return V, n_prior, n_rem, n_add


def main():
    args = parse_args()

    df = pd.read_csv(INPUT_FILE)
    if args.max:
        df = df.head(args.max)

    # Resume support
    done_keys = set()
    existing  = []
    if os.path.exists(OUTPUT_FILE):
        done_df = pd.read_csv(OUTPUT_FILE)
        for _, r in done_df.iterrows():
            done_keys.add((r["package"], str(r["breaking_version"])))
        existing = done_df.to_dict("records")
        print(f"Resuming: {len(done_keys)} already done.")

    todo = [(row["package"], str(row["breaking_version"]),
             str(row["prior_stable_version"]))
            for _, row in df.iterrows()
            if (row["package"], str(row["breaking_version"])) not in done_keys]

    print(f"\n{'='*60}")
    print("DepCast Phase 3 - Script 14: pub.dev Dart API Volatility")
    print(f"{'='*60}")
    print(f"Total: {len(df)}  |  To process: {len(todo)}")
    print(f"{'='*60}\n")

    records = list(existing)

    for idx, (pkg, brk_ver, prior_ver) in enumerate(todo, 1):
        print(f"[{idx:02d}/{len(todo)}] {pkg}  {prior_ver} -> {brk_ver} ... ",
              end="", flush=True)

        prior_b    = fetch_tarball(pkg, prior_ver);   time.sleep(DELAY)
        breaking_b = fetch_tarball(pkg, brk_ver);     time.sleep(DELAY)

        prior_s    = extract_dart_symbols(prior_b)
        breaking_s = extract_dart_symbols(breaking_b)
        V, n_prior, n_rem, n_add = compute_v_score(prior_s, breaking_s)

        pattern_c = 1 if (V == 0.0 and n_prior > 0) else 0
        label = " <- Pattern C" if pattern_c else ""
        print(f"V={V:.3f}  prior={n_prior}  removed={n_rem}  added={n_add}{label}")

        records.append({
            "package":           pkg,
            "breaking_version":  brk_ver,
            "prior_version":     prior_ver,
            "V_score":           round(V, 4),
            "n_prior_symbols":   n_prior,
            "n_removed_symbols": n_rem,
            "n_added_symbols":   n_add,
            "prior_fetched":     1 if prior_b else 0,
            "breaking_fetched":  1 if breaking_b else 0,
            "method":            "dart_toplevel_public_symbols",
            "pattern_C_candidate": pattern_c,
        })
        done_keys.add((pkg, brk_ver))

    df_out = pd.DataFrame(records)
    df_out.to_csv(OUTPUT_FILE, index=False)

    has_prior = df_out[df_out["n_prior_symbols"] > 0]
    pat_c = df_out[(df_out["V_score"] == 0.0) & (df_out["n_prior_symbols"] > 0)]
    pat_b = df_out[(df_out["V_score"] > 0) & (df_out["V_score"] < 1)]
    pat_a = df_out[df_out["V_score"] == 1.0]

    print(f"\n{'='*60}")
    print(f"DONE - {len(df_out)} releases in {OUTPUT_FILE}")
    print(f"Mean V(r):  {df_out['V_score'].mean():.4f}")
    print(f"Pattern A (V=1.0): {len(pat_a)}")
    print(f"Pattern B (0<V<1): {len(pat_b)}")
    print(f"Pattern C (V=0):   {len(pat_c)} / {len(has_prior)} = "
          f"{len(pat_c)/max(len(has_prior),1)*100:.1f}%")
    print(f"{'='*60}\n")
    print("NEXT STEP: run script 15 (pub.dev GitHub propagation signals)")


if __name__ == "__main__":
    main()
