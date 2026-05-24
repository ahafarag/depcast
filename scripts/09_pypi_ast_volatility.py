"""
DepCast Phase 3 — Script 09
Compute API Volatility V(r) for PyPI packages via Python AST diffing.

For each (package, breaking_version, prior_stable_version):
  1. Download sdist tarballs from PyPI for both versions.
  2. Extract and parse Python AST across all .py files.
  3. Collect public symbols: functions and classes not prefixed with '_',
     or the explicit __all__ list if defined in __init__.py.
  4. V(r) = removed_symbols / prior_symbols  (0 if prior_symbols = 0).

Outputs: data/pypi_api_volatility.csv

HOW TO RUN:
  python scripts/09_pypi_ast_volatility.py
"""

import ast
import tarfile
import zipfile
import gzip
import requests
import tempfile
import shutil
import os
import re
import time
import pandas as pd

INPUT_FILE  = "data/pypi_breaking_releases.csv"
OUTPUT_FILE = "data/pypi_api_volatility.csv"
DELAY       = 1.5


# ── PyPI download helpers ──────────────────────────────────────

def get_sdist_url(package, version):
    """Return the sdist (.tar.gz or .zip) download URL for a version."""
    url = f"https://pypi.org/pypi/{package}/{version}/json"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return None
        urls = r.json().get("urls", [])
        # Prefer .tar.gz sdist
        for u in urls:
            if u.get("packagetype") == "sdist" and u["filename"].endswith(".tar.gz"):
                return u["url"]
        # Fall back to any sdist
        for u in urls:
            if u.get("packagetype") == "sdist":
                return u["url"]
    except Exception:
        pass
    return None


def download_and_extract(url, dest_dir):
    """Download an sdist and extract it into dest_dir. Returns True on success."""
    try:
        r = requests.get(url, timeout=60, stream=True)
        if r.status_code != 200:
            return False
        fname = url.split("/")[-1].split("?")[0]
        local = os.path.join(dest_dir, fname)
        with open(local, "wb") as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)

        if fname.endswith(".tar.gz") or fname.endswith(".tgz"):
            with tarfile.open(local, "r:gz") as tf:
                tf.extractall(dest_dir)
        elif fname.endswith(".zip"):
            with zipfile.ZipFile(local, "r") as zf:
                zf.extractall(dest_dir)
        else:
            return False
        return True
    except Exception:
        return False


# ── Symbol extraction via AST ──────────────────────────────────

def extract_all_from_init(py_source):
    """Parse __all__ from a Python source string. Returns list or None."""
    try:
        tree = ast.parse(py_source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        if isinstance(node.value, (ast.List, ast.Tuple)):
                            return [
                                elt.s if isinstance(elt, ast.Constant) else
                                elt.s if hasattr(elt, 's') else None
                                for elt in node.value.elts
                                if isinstance(elt, (ast.Constant, ast.Str))
                            ]
    except Exception:
        pass
    return None


def extract_public_symbols(py_source):
    """Return set of public top-level function and class names."""
    symbols = set()
    try:
        tree = ast.parse(py_source)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not node.name.startswith("_"):
                    symbols.add(node.name)
    except Exception:
        pass
    return symbols


def find_package_dir(extract_root, package_name):
    """
    Locate the main Python package directory inside an extracted sdist.
    sdist layout: {package}-{version}/{package_name}/
    Import name is often lowercased or differs slightly (e.g. Pillow -> PIL).
    """
    candidates = []
    for entry in os.listdir(extract_root):
        top = os.path.join(extract_root, entry)
        if not os.path.isdir(top):
            continue
        # Look inside the version-stamped directory
        for sub in os.listdir(top):
            sub_path = os.path.join(top, sub)
            if os.path.isdir(sub_path) and os.path.exists(os.path.join(sub_path, "__init__.py")):
                candidates.append((sub.lower(), sub_path))
        # Also check top-level for flat layouts
        if os.path.exists(os.path.join(top, "__init__.py")):
            candidates.append((entry.lower(), top))

    pkg_lower = package_name.lower().replace("-", "_")
    # Exact match first
    for name, path in candidates:
        if name == pkg_lower:
            return path
    # Prefix match
    for name, path in candidates:
        if name.startswith(pkg_lower[:4]):
            return path
    # Return first candidate with __init__.py
    if candidates:
        return candidates[0][1]
    return None


def get_symbols_for_version(package, version, tmpdir):
    """Download, extract, and return set of public symbols for a package version."""
    url = get_sdist_url(package, version)
    if not url:
        return None, f"no sdist URL for {package}=={version}"

    pkg_dir = os.path.join(tmpdir, f"{package}-{version}")
    os.makedirs(pkg_dir, exist_ok=True)

    if not download_and_extract(url, pkg_dir):
        return None, f"download/extract failed for {url}"

    main_dir = find_package_dir(pkg_dir, package)
    if not main_dir:
        return None, f"could not locate package directory in sdist"

    # Check __all__ in __init__.py first
    init_path = os.path.join(main_dir, "__init__.py")
    symbols = set()
    if os.path.exists(init_path):
        with open(init_path, "r", encoding="utf-8", errors="ignore") as f:
            src = f.read()
        all_list = extract_all_from_init(src)
        if all_list:
            return set(x for x in all_list if x), None
        # No __all__ — collect from __init__ itself
        symbols |= extract_public_symbols(src)

    # Walk all .py files in package directory
    for root, dirs, files in os.walk(main_dir):
        # Skip private subpackages
        dirs[:] = [d for d in dirs if not d.startswith("_") and d != "tests"]
        for fname in files:
            if fname.endswith(".py") and not fname.startswith("_"):
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        src = f.read()
                    symbols |= extract_public_symbols(src)
                except Exception:
                    pass

    return symbols, None


# ── Main ──────────────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print("DepCast Phase 3 — Script 09: PyPI AST Volatility")
    print(f"{'='*60}\n")

    df = pd.read_csv(INPUT_FILE)

    # Load already processed rows
    done_keys = set()
    existing  = []
    if os.path.exists(OUTPUT_FILE):
        df_done = pd.read_csv(OUTPUT_FILE)
        for _, r in df_done.iterrows():
            done_keys.add((r["package"], str(r["breaking_version"])))
        existing = df_done.to_dict("records")
        print(f"Resuming: {len(done_keys)} already processed.")

    records = list(existing)

    with tempfile.TemporaryDirectory() as tmpdir:
        for _, row in df.iterrows():
            pkg      = row["package"]
            brk_ver  = str(row["breaking_version"])
            pri_ver  = str(row["prior_stable_version"])
            key      = (pkg, brk_ver)

            if key in done_keys:
                continue

            print(f"  {pkg}  {pri_ver} -> {brk_ver} ... ", end="", flush=True)
            time.sleep(DELAY)

            prior_syms, err = get_symbols_for_version(pkg, pri_ver, tmpdir)
            if prior_syms is None:
                print(f"SKIP (prior): {err}")
                records.append({
                    "package": pkg, "breaking_version": brk_ver,
                    "prior_version": pri_ver,
                    "n_prior_symbols": 0, "n_removed_symbols": 0,
                    "V_score": 0.0, "error": err
                })
                done_keys.add(key)
                continue

            time.sleep(DELAY)
            new_syms, err = get_symbols_for_version(pkg, brk_ver, tmpdir)
            if new_syms is None:
                print(f"SKIP (breaking): {err}")
                records.append({
                    "package": pkg, "breaking_version": brk_ver,
                    "prior_version": pri_ver,
                    "n_prior_symbols": len(prior_syms), "n_removed_symbols": 0,
                    "V_score": 0.0, "error": err
                })
                done_keys.add(key)
                continue

            removed = prior_syms - new_syms
            n_prior = len(prior_syms)
            n_removed = len(removed)
            v_score = round(n_removed / n_prior, 4) if n_prior > 0 else 0.0

            print(f"prior={n_prior}  removed={n_removed}  V={v_score:.3f}")
            records.append({
                "package": pkg, "breaking_version": brk_ver,
                "prior_version": pri_ver,
                "n_prior_symbols": n_prior, "n_removed_symbols": n_removed,
                "V_score": v_score, "error": None
            })
            done_keys.add(key)

            # Save every 5 releases
            if len(records) % 5 == 0:
                pd.DataFrame(records).to_csv(OUTPUT_FILE, index=False)

    pd.DataFrame(records).to_csv(OUTPUT_FILE, index=False)

    out = pd.DataFrame(records)
    pattern_c = (out["V_score"] == 0).sum()
    print(f"\n{'='*60}")
    print(f"DONE — {len(out)} releases in {OUTPUT_FILE}")
    print(f"  Pattern C (V=0): {pattern_c}/{len(out)} = {pattern_c/max(len(out),1)*100:.1f}%")
    print(f"  Mean V_score   : {out['V_score'].mean():.3f}")
    print(f"{'='*60}\n")
    print("NEXT STEP: run script 10 to fetch GitHub propagation signals.")


if __name__ == "__main__":
    main()
