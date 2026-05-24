"""
DepCast Phase 3 - Script 12
Compute V(r) for sweep candidates that lack API volatility data.

The deprecation sweep (script 07) produced 290 candidates but did not record
prior_stable_version, so script 02 could not run on them. This script:
  1. Looks up the prior stable version from the npm registry for each candidate
  2. Downloads both tarballs and extracts export symbols
  3. Computes V(r) using the same method as script 02
  4. Appends new rows to data/api_volatility.csv (resumable)

Reads:
  data/sweep_top290_candidates.csv   -- sweep candidates (from script 07)
  data/api_volatility.csv            -- existing V(r) rows (skip already done)

Outputs:
  data/api_volatility.csv            -- extended in-place

HOW TO RUN:
  python scripts/12_sweep_api_volatility.py
  python scripts/12_sweep_api_volatility.py --max 20   # test run
"""

import requests
import tarfile
import re
import os
import io
import time
import argparse
import pandas as pd
from packaging.version import Version, InvalidVersion

INPUT_SWEEP = "data/sweep_top290_candidates.csv"
OUTPUT_VOL  = "data/api_volatility.csv"
DELAY       = 0.5

# Same patterns as script 02
EXPORT_PATTERNS = [
    re.compile(r'module\.exports\.(\w+)\s*='),
    re.compile(r'(?<!\w)exports\.(\w+)\s*='),
    re.compile(r'export\s+(?:default\s+)?(?:async\s+)?'
               r'(?:function|class|const|let|var)\s+(\w+)'),
    re.compile(r'export\s+(?:interface|type|enum|abstract\s+class)\s+(\w+)'),
]
EXPORT_BLOCK = re.compile(r'export\s*\{([^}]+)\}')
EXCLUDE_PATHS = ('test', 'spec', 'node_modules', '__tests__',
                 'fixture', 'mock', 'example', 'demo', '.min.', 'vendor')


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--max", type=int, default=None)
    return p.parse_args()


def get_prior_stable_version(package, breaking_ver_str):
    """Return the highest stable version < breaking_version, or None."""
    try:
        brk = Version(breaking_ver_str)
    except InvalidVersion:
        return None

    url = f"https://registry.npmjs.org/{package}"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        versions_raw = list(data.get("versions", {}).keys())
    except Exception:
        return None

    candidates = []
    for v_str in versions_raw:
        try:
            v = Version(v_str)
            if v < brk and not v.is_prerelease and not v.is_devrelease:
                candidates.append(v)
        except InvalidVersion:
            continue

    if not candidates:
        return None
    return str(max(candidates))


def fetch_tarball(package, version):
    pkg_safe = package.split('/')[-1]
    url = f"https://registry.npmjs.org/{package}/-/{pkg_safe}-{version}.tgz"
    try:
        r = requests.get(url, timeout=30, stream=True)
        if r.status_code == 200:
            return r.content
    except Exception:
        pass
    return None


def extract_export_symbols(tarball_bytes):
    symbols = set()
    if not tarball_bytes:
        return symbols
    try:
        with tarfile.open(fileobj=io.BytesIO(tarball_bytes), mode="r:gz") as tar:
            for member in tar.getmembers():
                name = member.name.lower()
                if not any(name.endswith(ext) for ext in ('.js', '.ts', '.mjs', '.cjs')):
                    continue
                if any(exc in name for exc in EXCLUDE_PATHS):
                    continue
                try:
                    f = tar.extractfile(member)
                    if not f:
                        continue
                    content = f.read().decode('utf-8', errors='ignore')
                    for pat in EXPORT_PATTERNS:
                        for m in pat.finditer(content):
                            sym = m.group(1).strip()
                            if sym and re.match(r'^\w+$', sym):
                                symbols.add(sym)
                    for m in EXPORT_BLOCK.finditer(content):
                        for sym_raw in m.group(1).split(','):
                            parts = sym_raw.strip().split(' as ')
                            exported = parts[-1].strip()
                            if exported and re.match(r'^\w+$', exported):
                                symbols.add(exported)
                except Exception:
                    continue
    except Exception:
        pass
    return symbols


def compute_v_score(prior_syms, breaking_syms):
    if not prior_syms and not breaking_syms:
        return 0.0, 0, 0, 0, 0
    removed  = prior_syms - breaking_syms
    added    = breaking_syms - prior_syms
    n_prior  = len(prior_syms)
    n_rem    = len(removed)
    V = min(n_rem / max(n_prior, 1), 1.0)
    return V, n_prior, n_rem, len(added), len(prior_syms & breaking_syms)


def main():
    args = parse_args()

    sweep = pd.read_csv(INPUT_SWEEP)
    if args.max:
        sweep = sweep.head(args.max)

    # Load existing V(r) records to skip already-done pairs
    existing_records = []
    done_keys = set()
    if os.path.exists(OUTPUT_VOL):
        existing_df = pd.read_csv(OUTPUT_VOL)
        for _, row in existing_df.iterrows():
            done_keys.add((row["package"], str(row["breaking_version"])))
        existing_records = existing_df.to_dict("records")

    # Filter to sweep candidates not yet in api_volatility.csv
    todo = []
    for _, row in sweep.iterrows():
        key = (row["package"], str(row["breaking_version"]))
        if key not in done_keys:
            todo.append(row)

    total = len(todo)
    print(f"\n{'='*60}")
    print("DepCast Phase 3 - Script 12: Sweep API Volatility")
    print(f"{'='*60}")
    print(f"Sweep candidates : {len(sweep)}")
    print(f"Already done     : {len(sweep) - total}")
    print(f"To process       : {total}")
    print(f"{'='*60}\n")

    records = list(existing_records)

    for idx, row in enumerate(todo, 1):
        pkg     = row["package"]
        brk_ver = str(row["breaking_version"])
        print(f"[{idx:03d}/{total}] {pkg}=={brk_ver} ... ", end="", flush=True)

        # Find prior stable version from npm registry
        prior_ver = get_prior_stable_version(pkg, brk_ver)
        time.sleep(DELAY)

        if not prior_ver:
            print("SKIP (no prior stable version found)")
            records.append({
                "package": pkg, "breaking_version": brk_ver,
                "prior_version": None, "V_score": 0.0,
                "n_prior_symbols": 0, "n_removed_symbols": 0,
                "n_added_symbols": 0, "n_retained_symbols": 0,
                "prior_fetched": 0, "breaking_fetched": 0,
                "method": "heuristic_export_declaration_extraction",
                "pattern_C_candidate": 0,
                "error": "no_prior_version",
            })
            done_keys.add((pkg, brk_ver))
            continue

        prior_b    = fetch_tarball(pkg, prior_ver);   time.sleep(DELAY)
        breaking_b = fetch_tarball(pkg, brk_ver);     time.sleep(DELAY)

        prior_s    = extract_export_symbols(prior_b)
        breaking_s = extract_export_symbols(breaking_b)
        V, n_prior, n_rem, n_add, n_ret = compute_v_score(prior_s, breaking_s)

        pattern_c = 1 if (V == 0.0 and n_prior > 0) else 0
        label = " <- Pattern C" if pattern_c else ""
        print(f"V={V:.3f}  prior={n_prior}  removed={n_rem}  prior_ver={prior_ver}{label}")

        records.append({
            "package": pkg, "breaking_version": brk_ver,
            "prior_version": prior_ver, "V_score": round(V, 4),
            "n_prior_symbols": n_prior, "n_removed_symbols": n_rem,
            "n_added_symbols": n_add, "n_retained_symbols": n_ret,
            "prior_fetched": 1 if prior_b else 0,
            "breaking_fetched": 1 if breaking_b else 0,
            "method": "heuristic_export_declaration_extraction",
            "pattern_C_candidate": pattern_c,
            "error": None,
        })
        done_keys.add((pkg, brk_ver))

        # Save every 10 records
        if idx % 10 == 0:
            pd.DataFrame(records).to_csv(OUTPUT_VOL, index=False)
            print(f"  [checkpoint saved — {idx}/{total}]")

    df_out = pd.DataFrame(records)
    df_out.to_csv(OUTPUT_VOL, index=False)

    new_rows = df_out[df_out["package"].isin([r["package"] for r in todo[:]])]
    pattern_c_new = df_out[
        (df_out["V_score"] == 0.0) &
        (df_out["n_prior_symbols"] > 0) &
        (df_out["package"].isin([r["package"] for r in todo]))
    ]

    print(f"\n{'='*60}")
    print(f"DONE - {OUTPUT_VOL} now has {len(df_out)} rows")
    sweep_done = df_out[df_out["error"].isna() | (df_out["error"] == "nan")]
    pattern_c_all = df_out[(df_out["V_score"] == 0.0) & (df_out["n_prior_symbols"] > 0)]
    pattern_b_all = df_out[(df_out["V_score"] > 0) & (df_out["V_score"] < 1)]
    pattern_a_all = df_out[df_out["V_score"] == 1.0]
    print(f"Pattern A (V=1.0): {len(pattern_a_all)}")
    print(f"Pattern B (0<V<1): {len(pattern_b_all)}")
    print(f"Pattern C (V=0):   {len(pattern_c_all)}")
    print(f"{'='*60}\n")
    print("NEXT STEP: re-run script 05 (CRS validation) then script 11 (cross-ecosystem merge)")


if __name__ == "__main__":
    main()
