"""
DepCast Phase 1 — Script 02
Compute V(r): API Surface Volatility Score

METHOD:
  V(r) = (removed_symbols) / max(prior_symbols, 1), clipped to [0,1]

  Symbol extraction uses heuristic export-declaration extraction:
  pattern matching over JavaScript/TypeScript export declaration syntax
  within npm package tarballs. Five pattern classes are matched:

    1. module.exports.X = ...       (CommonJS named export)
    2. exports.X = ...              (CommonJS named export shorthand)
    3. export function/class/const X (ESM declaration export)
    4. export interface/type/enum X  (TypeScript declaration export)
    5. export { X, Y as Z }         (ESM named re-export block)

  Only symbols from non-test, non-spec source files are included.

  IMPORTANT LIMITATION (Pattern C):
  V(r)=0 does NOT guarantee the release is non-breaking. It means no
  export-declaration-level symbol removal was detected. Behavioral
  breaking changes — changed defaults, dropped implicit dependencies,
  modified event delegation — are invisible to this method. This is
  the Pattern C finding: 37% of confirmed breaking releases in our
  dataset show V(r)=0 yet generate substantial downstream failures.
  This limitation directly validates the necessity of D(t) runtime
  signal in the CRS model.

HOW TO RUN:
  python scripts/02_compute_api_volatility.py

OUTPUT:
  data/api_volatility.csv
"""

import requests, tarfile, re, os, io, time
import pandas as pd

INPUT  = "data/breaking_releases.csv"
OUTPUT = "data/api_volatility.csv"
os.makedirs("data", exist_ok=True)

# ── Export-declaration extraction patterns ──
# Each pattern targets a specific JavaScript/TypeScript export syntax.
# These are NOT arbitrary text regex — they match syntactic export
# declaration forms in JS/TS source files.
EXPORT_PATTERNS = [
    re.compile(r'module\.exports\.(\w+)\s*='),            # CJS named
    re.compile(r'(?<!\w)exports\.(\w+)\s*='),              # CJS shorthand
    re.compile(r'export\s+(?:default\s+)?(?:async\s+)?'
               r'(?:function|class|const|let|var)\s+(\w+)'), # ESM declaration
    re.compile(r'export\s+(?:interface|type|enum|abstract\s+class)\s+(\w+)'), # TS
]
EXPORT_BLOCK = re.compile(r'export\s*\{([^}]+)\}')  # export { X, Y as Z }

EXCLUDE_PATHS = ('test', 'spec', 'node_modules', '__tests__',
                 'fixture', 'mock', 'example', 'demo', '.min.', 'vendor')

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
    """
    Extract exported symbol names from a JS/TS npm package tarball.

    Uses heuristic export-declaration extraction: pattern matching over
    export declaration syntax in JS/TS source files. Returns a set of
    symbol strings representing the detected public API surface.

    Limitations: does not detect dynamic exports, does not execute code,
    may under-count symbols in heavily transpiled packages.
    """
    symbols = set()
    if not tarball_bytes:
        return symbols
    try:
        with tarfile.open(fileobj=io.BytesIO(tarball_bytes), mode="r:gz") as tar:
            for member in tar.getmembers():
                name = member.name.lower()
                if not any(name.endswith(ext) for ext in ('.js','.ts','.mjs','.cjs')):
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
    """
    V(r) = |removed| / max(|prior|, 1), clipped to [0,1]

    removed  = symbols in prior but absent in breaking version
    added    = symbols absent in prior but present in breaking version

    V(r) = 0.0 does NOT mean non-breaking — see Pattern C in paper Section 5.4.
    """
    if not prior_syms and not breaking_syms:
        return 0.0, 0, 0, 0, 0
    removed  = prior_syms - breaking_syms
    added    = breaking_syms - prior_syms
    retained = prior_syms & breaking_syms
    n_prior  = len(prior_syms)
    n_rem    = len(removed)
    V = min(n_rem / max(n_prior, 1), 1.0)
    return V, n_prior, n_rem, len(added), len(retained)

def main():
    if not os.path.exists(INPUT):
        print(f"ERROR: {INPUT} not found. Run script 01 first.")
        return

    df = pd.read_csv(INPUT)
    records = []

    print(f"\n{'='*60}")
    print("DepCast Phase 1 — Computing V(r)")
    print("Method: heuristic export-declaration extraction")
    print(f"n={len(df)} releases")
    print(f"{'='*60}\n")

    for i, row in df.iterrows():
        pkg, brk_ver, pri_ver = row["package"], str(row["breaking_version"]), str(row["prior_stable_version"])
        print(f"[{i+1:02d}/{len(df)}] {pkg}  {pri_ver} → {brk_ver} ... ", end="", flush=True)

        prior_b    = fetch_tarball(pkg, pri_ver);   time.sleep(0.5)
        breaking_b = fetch_tarball(pkg, brk_ver);   time.sleep(0.5)

        prior_s    = extract_export_symbols(prior_b)
        breaking_s = extract_export_symbols(breaking_b)
        V, n_prior, n_rem, n_add, n_ret = compute_v_score(prior_s, breaking_s)

        label = ""
        if V == 0.0 and n_prior > 0:
            label = " ← Pattern C candidate (no symbol removal detected)"

        records.append({
            "package": pkg, "breaking_version": brk_ver, "prior_version": pri_ver,
            "V_score": round(V,4), "n_prior_symbols": n_prior,
            "n_removed_symbols": n_rem, "n_added_symbols": n_add,
            "n_retained_symbols": n_ret,
            "prior_fetched": 1 if prior_b else 0,
            "breaking_fetched": 1 if breaking_b else 0,
            "method": "heuristic_export_declaration_extraction",
            "pattern_C_candidate": 1 if (V == 0.0 and n_prior > 0) else 0,
        })
        print(f"V={V:.3f} (prior={n_prior}, removed={n_rem}, added={n_add}){label}")

    result_df = pd.DataFrame(records)
    result_df.to_csv(OUTPUT, index=False)

    print(f"\n{'='*60}")
    print(f"DONE  Saved: {OUTPUT}")
    print(f"\nV(r) distribution:\n{result_df['V_score'].describe().round(4)}")
    print(f"\nPattern A (V=1.0):  {(result_df['V_score']==1.0).sum()} releases")
    print(f"Pattern B (0<V<1):  {((result_df['V_score']>0)&(result_df['V_score']<1)).sum()} releases")
    print(f"Pattern C (V=0.0):  {(result_df['V_score']==0.0).sum()} releases")
    print(f"\nPattern C NOTE: V=0 does NOT mean non-breaking.")
    print(f"  See paper Section 5.4 — these releases break via behavioral")
    print(f"  changes invisible to export-declaration analysis.")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
