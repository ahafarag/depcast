"""
DepCast Phase 3 - Script 11
Merge PyPI and pub.dev into the combined CRS dataset.

Reads:
  data/crs_scores.csv                  -- npm CRS scores from script 05
  data/pypi_breaking_releases.csv      -- PyPI seed releases (script 08)
  data/pypi_api_volatility.csv         -- PyPI V(r) (script 09)
  data/pypi_propagation_signals.csv    -- PyPI propagation signals (script 10)
  data/pypi_sir_results.csv            -- PyPI SIR / R0
  data/pubdev_breaking_releases.csv    -- pub.dev seed releases (script 13)
  data/pubdev_api_volatility.csv       -- pub.dev V(r) (script 14)
  data/pubdev_propagation_signals.csv  -- pub.dev propagation signals (script 15)
  data/pubdev_sir_results.csv          -- pub.dev SIR / R0

Outputs:
  data/combined_crs_scores.csv         -- npm + PyPI + pub.dev, labelled by ecosystem
  figures/cross_ecosystem.png          -- R0 + Pattern C three-ecosystem figure

HOW TO RUN:
  python scripts/11_merge_pypi_crs.py
"""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import os

os.makedirs("data", exist_ok=True)
os.makedirs("figures", exist_ok=True)

NPM_CRS_FILE     = "data/crs_scores.csv"
PYPI_RELEASES    = "data/pypi_breaking_releases.csv"
PYPI_VOLATILITY  = "data/pypi_api_volatility.csv"
PYPI_SIGNALS     = "data/pypi_propagation_signals.csv"
PYPI_SIR         = "data/pypi_sir_results.csv"
PUBDEV_RELEASES  = "data/pubdev_breaking_releases.csv"
PUBDEV_VOLATILITY= "data/pubdev_api_volatility.csv"
PUBDEV_SIGNALS   = "data/pubdev_propagation_signals.csv"
PUBDEV_SIR       = "data/pubdev_sir_results.csv"
OUTPUT_CSV       = "data/combined_crs_scores.csv"
OUTPUT_FIG       = "figures/cross_ecosystem.png"


def normalize(series):
    mn, mx = series.min(), series.max()
    if mx == mn:
        return pd.Series(0.0, index=series.index)
    return (series - mn) / (mx - mn)


def build_pypi_crs():
    """Build CRS features for PyPI releases using the same logic as script 05."""
    df = pd.read_csv(PYPI_RELEASES)

    # V(r) — API volatility
    vol = pd.read_csv(PYPI_VOLATILITY)
    df = df.merge(vol[["package", "breaking_version", "V_score",
                        "n_prior_symbols", "n_removed_symbols"]],
                  on=["package", "breaking_version"], how="left")
    df["V_r"] = normalize(df["V_score"].fillna(0))

    # E(r) — Downstream exposure (PyPI has no dependent_count; use weekly_downloads)
    sig = pd.read_csv(PYPI_SIGNALS)
    df = df.merge(sig[["package", "breaking_version", "weekly_downloads",
                        "issues_6h", "issues_12h", "issues_24h",
                        "issues_48h", "issues_72h", "first_issue_hours"]],
                  on=["package", "breaking_version"], how="left")

    # Prefer signals file weekly_downloads if releases file is missing it
    if "weekly_downloads_x" in df.columns:
        df["weekly_downloads"] = df["weekly_downloads_x"].fillna(df["weekly_downloads_y"])
        df = df.drop(columns=["weekly_downloads_x", "weekly_downloads_y"])

    df["E_r"] = normalize(df["weekly_downloads"].fillna(0).astype(float))

    # D(t) — Observed failure rate via GitHub issue rate at 24h
    # PyPI has no CI signals file, so we use issue rate normalized
    df["D_t"] = normalize(df["issues_24h"].fillna(0).astype(float))

    # H(m) — Maintainer history via R0 from SIR model
    sir = pd.read_csv(PYPI_SIR)
    df = df.merge(sir[["package", "breaking_version", "R0", "r_squared",
                        "propagation_class"]],
                  on=["package", "breaking_version"], how="left")
    df["H_m"] = normalize(df["R0"].fillna(0).astype(float))

    # CRS (equal weights — no logistic regression since no PyPI controls yet)
    df["CRS_equal"] = (0.25 * df["V_r"] +
                       0.25 * df["E_r"] +
                       0.25 * df["D_t"] +
                       0.25 * df["H_m"])
    df["CRS_learned"] = df["CRS_equal"]

    def assign_rating(crs):
        if crs <= 0.25:   return "SAFE"
        elif crs <= 0.60: return "WAIT"
        else:             return "AVOID"

    df["CRS_rating"] = df["CRS_equal"].apply(assign_rating)
    df["ecosystem"] = "pypi"
    df["label_breaking"] = df.get("label_breaking", pd.Series(1, index=df.index)).fillna(1).astype(int)

    return df


def build_pubdev_crs():
    """Build CRS features for pub.dev releases using the same logic as build_pypi_crs."""
    df = pd.read_csv(PUBDEV_RELEASES)

    vol = pd.read_csv(PUBDEV_VOLATILITY)
    df = df.merge(vol[["package", "breaking_version", "V_score",
                        "n_prior_symbols", "n_removed_symbols"]],
                  on=["package", "breaking_version"], how="left")
    df["V_r"] = normalize(df["V_score"].fillna(0))

    sig = pd.read_csv(PUBDEV_SIGNALS)
    df = df.merge(sig[["package", "breaking_version", "download_count_30d",
                        "issues_6h", "issues_12h", "issues_24h",
                        "issues_48h", "issues_72h", "first_issue_hours"]],
                  on=["package", "breaking_version"], how="left")

    if "download_count_30d_x" in df.columns:
        df["download_count_30d"] = df["download_count_30d_x"].fillna(df["download_count_30d_y"])
        df = df.drop(columns=["download_count_30d_x", "download_count_30d_y"])

    df["E_r"] = normalize(df["download_count_30d"].fillna(0).astype(float))
    df["D_t"] = normalize(df["issues_24h"].fillna(0).astype(float))

    sir = pd.read_csv(PUBDEV_SIR)
    df = df.merge(sir[["package", "breaking_version", "R0", "r_squared",
                        "propagation_class"]],
                  on=["package", "breaking_version"], how="left")
    df["H_m"] = normalize(df["R0"].fillna(0).astype(float))

    df["CRS_equal"] = (0.25 * df["V_r"] +
                       0.25 * df["E_r"] +
                       0.25 * df["D_t"] +
                       0.25 * df["H_m"])
    df["CRS_learned"] = df["CRS_equal"]

    def assign_rating(crs):
        if crs <= 0.25:   return "SAFE"
        elif crs <= 0.60: return "WAIT"
        else:             return "AVOID"

    df["CRS_rating"]    = df["CRS_equal"].apply(assign_rating)
    df["ecosystem"]     = "pubdev"
    df["label_breaking"] = 1

    return df


def main():
    print(f"\n{'='*60}")
    print("DepCast Phase 3 - Script 11: Cross-Ecosystem CRS Merge")
    print(f"{'='*60}\n")

    # ── Load npm CRS scores ──
    npm = pd.read_csv(NPM_CRS_FILE)
    if "ecosystem" not in npm.columns:
        npm["ecosystem"] = "npm"
    print(f"npm releases:  {len(npm)} rows")

    # ── Build PyPI CRS ──
    pypi = build_pypi_crs()
    print(f"PyPI releases: {len(pypi)} rows")

    # ── Build pub.dev CRS ──
    pubdev = build_pubdev_crs() if os.path.exists(PUBDEV_RELEASES) else pd.DataFrame()
    if len(pubdev):
        print(f"pub.dev releases: {len(pubdev)} rows")

    # ── Align columns and combine ──
    keep = ["package", "breaking_version", "ecosystem", "label_breaking",
            "V_r", "E_r", "D_t", "H_m",
            "CRS_equal", "CRS_learned", "CRS_rating",
            "R0", "r_squared", "propagation_class", "first_issue_hours"]

    frames = [npm[[c for c in keep if c in npm.columns]],
              pypi[[c for c in keep if c in pypi.columns]]]
    if len(pubdev):
        frames.append(pubdev[[c for c in keep if c in pubdev.columns]])

    combined = pd.concat(frames, ignore_index=True, sort=False)
    combined.to_csv(OUTPUT_CSV, index=False)
    print(f"\nCombined:      {len(combined)} rows -> {OUTPUT_CSV}")

    # ── Cross-ecosystem summary ──
    print(f"\n{'-'*60}")
    print("Cross-ecosystem CRS summary")
    print(f"{'-'*60}")
    for eco, grp in combined.groupby("ecosystem"):
        breaking = grp[grp["label_breaking"] == 1]
        pattern_c = (breaking["V_r"] == 0).sum()
        pct_c = pattern_c / len(breaking) * 100 if len(breaking) else 0
        r0_vals = breaking["R0"].dropna()
        r0_med  = r0_vals.median() if len(r0_vals) else float("nan")
        crs_med = breaking["CRS_equal"].median()
        print(f"\n  {eco.upper()} ({len(breaking)} breaking)")
        print(f"    CRS median:  {crs_med:.3f}")
        print(f"    R0 median:   {r0_med:.3f}")
        print(f"    Pattern C:   {pattern_c}/{len(breaking)} = {pct_c:.1f}%")
        print(f"    Rating dist: {breaking['CRS_rating'].value_counts().to_dict()}")

    # ── Generate cross-ecosystem figure ──
    generate_figure(combined)

    print(f"\n{'='*60}")
    print(f"DONE - {len(combined)} releases in {OUTPUT_CSV}")
    print(f"Figure saved:  {OUTPUT_FIG}")
    print(f"{'='*60}\n")


def generate_figure(df):
    breaking = df[df["label_breaking"] == 1].copy()
    ecosystems = [e for e in ["npm", "pypi", "pubdev"] if e in breaking["ecosystem"].values]

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    fig.suptitle("DepCast: Cross-Ecosystem Comparison (npm vs PyPI vs pub.dev)",
                 fontsize=13, fontweight="bold")

    colors = {"npm": "#2E75B6", "pypi": "#C00000", "pubdev": "#375623"}
    labels = {"npm": "npm", "pypi": "PyPI", "pubdev": "pub.dev"}

    # Panel A: R0 boxplot
    ax = axes[0]
    data = [breaking[breaking["ecosystem"] == e]["R0"].dropna().values
            for e in ecosystems]
    bp = ax.boxplot(data, tick_labels=[labels[e] for e in ecosystems], patch_artist=True,
                    medianprops=dict(color="white", linewidth=2))
    for patch, eco in zip(bp["boxes"], ecosystems):
        patch.set_facecolor(colors[eco])
        patch.set_alpha(0.8)
    ax.axhline(y=1.0, color="black", linestyle="--", linewidth=1.2, alpha=0.6,
               label="R₀ = 1 (epidemic threshold)")
    ax.set_ylabel("R₀ (SIR model)", fontsize=11)
    ax.set_title("A — Propagation Speed (R₀)", fontsize=11, fontweight="bold")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3, axis="y")

    # Annotate medians
    for i, eco in enumerate(ecosystems):
        med = breaking[breaking["ecosystem"] == eco]["R0"].dropna().median()
        ax.text(i + 1, med + 0.03, f"{med:.2f}", ha="center", fontsize=9,
                fontweight="bold", color="white",
                bbox=dict(boxstyle="round,pad=0.2", facecolor=colors[eco], alpha=0.8))

    # Panel B: Pattern C comparison
    ax = axes[1]
    totals = []
    pattern_c = []
    for eco in ecosystems:
        grp = breaking[breaking["ecosystem"] == eco]
        totals.append(len(grp))
        pattern_c.append((grp["V_r"] == 0).sum())

    x = np.arange(len(ecosystems))
    bars_total = ax.bar(x, totals, color=[colors[e] for e in ecosystems],
                        alpha=0.3, label="Total breaking")
    bars_pc    = ax.bar(x, pattern_c, color=[colors[e] for e in ecosystems],
                        alpha=0.9, label="Pattern C (V(r)=0)")
    ax.set_xticks(x)
    ax.set_xticklabels([labels[e] for e in ecosystems])
    ax.set_ylabel("Release count", fontsize=11)
    ax.set_title("B — Pattern C Prevalence", fontsize=11, fontweight="bold")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3, axis="y")

    for i, (tot, pc) in enumerate(zip(totals, pattern_c)):
        pct = pc / tot * 100 if tot else 0
        ax.text(i, pc + 1, f"{pct:.1f}%", ha="center", fontsize=10,
                fontweight="bold", color=colors[ecosystems[i]])

    # Panel C: CRS distribution by ecosystem
    ax = axes[2]
    bins = np.linspace(0, 1, 16)
    for eco in ecosystems:
        grp = breaking[breaking["ecosystem"] == eco]["CRS_equal"].dropna()
        ax.hist(grp, bins=bins, alpha=0.6, color=colors[eco],
                label=f"{labels[eco]} (n={len(grp)})", density=True)
    ax.axvline(x=0.25, color="green",  linestyle="--", linewidth=1.2, alpha=0.7)
    ax.axvline(x=0.60, color="red",    linestyle="--", linewidth=1.2, alpha=0.7)
    ax.set_xlabel("CRS Score", fontsize=11)
    ax.set_ylabel("Density", fontsize=11)
    ax.set_title("C — CRS Score Distribution", fontsize=11, fontweight="bold")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    plt.savefig(OUTPUT_FIG, dpi=150, bbox_inches="tight")
    plt.close()


if __name__ == "__main__":
    main()
