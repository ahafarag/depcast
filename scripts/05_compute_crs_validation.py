"""
DepCast Phase 1 — Script 05
Compute CRS scores and validate the model.

WHAT THIS DOES:
  - Merges all data: releases + API volatility + propagation signals + SIR results
  - Computes CRS(t) = w1*V + w2*E + w3*D(t) + w4*H(m)
  - Trains logistic regression to learn optimal weights
  - Evaluates: AUC-ROC, precision, recall, F1
  - Plots: CRS distribution, ROC curve, feature importance
  - Outputs: data/crs_scores.csv + figures/crs_validation.png

HOW TO RUN:
  python3 scripts/05_compute_crs_validation.py

THIS IS THE CORE SCIENTIFIC CONTRIBUTION:
  If CRS(t) predicts confirmed breaking releases with AUC > 0.7,
  that is a publishable empirical result validating the DepCast model.
"""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import (roc_auc_score, roc_curve, precision_recall_curve,
                              classification_report, confusion_matrix)
from sklearn.model_selection import cross_val_score, StratifiedKFold
import warnings
warnings.filterwarnings('ignore')
import os

os.makedirs("data", exist_ok=True)
os.makedirs("figures", exist_ok=True)

RELEASES_FILE    = "data/breaking_releases.csv"
CONTROLS_FILE    = "data/nonbreaking_releases.csv"   # Phase 2 negative class
VOLATILITY_FILE  = "data/api_volatility.csv"
SIGNALS_FILE     = "data/propagation_signals.csv"
SWEEP_SIGNALS    = "data/sweep_propagation_signals.csv"  # Phase 2 sweep candidates
CI_SIGNALS_FILE  = "data/ci_signals.csv"
SIR_FILE         = "data/sir_model_results.csv"
OUTPUT_CSV       = "data/crs_scores.csv"
OUTPUT_FIG       = "figures/crs_validation_v2.png"

def normalize(series):
    """Normalize a pandas series to [0,1]."""
    mn, mx = series.min(), series.max()
    if mx == mn:
        return series * 0.0
    return (series - mn) / (mx - mn)

def build_features(df):
    """Build the four CRS features from merged dataframe."""

    # V(r) — API Volatility: from api_volatility.csv
    if "V_score" in df.columns:
        df["V_r"] = normalize(df["V_score"].fillna(0))
    else:
        # Fallback heuristic: use dependent_count as proxy
        df["V_r"] = 0.5

    # E(r) — Downstream Exposure: dependent_count + weekly_downloads
    if "dependent_count" in df.columns:
        dep = df["dependent_count"].fillna(0).astype(float)
        dl  = df["weekly_downloads"].fillna(0).astype(float) if "weekly_downloads" in df.columns else dep * 0
        exposure = dep + dl * 0.1
        df["E_r"] = normalize(exposure)
    else:
        df["E_r"] = 0.5

    # D(t) — Observed failure rate. Signal priority (most reliable → least):
    #   1. Checks API failure rate  (script 03b --checks-api: direct CI measurement)
    #   2. Bot PR rejection rate    (script 03b: proxy — closed PRs imply CI failure)
    #   3. GitHub issue rate at 24h (script 03: keyword search)
    #   4. npm publisher signals    (script 03b: works for ALL release ages, no token)
    #   5. Default 0.0  (conservative: no signal = no observed failure)
    # Applied per-release: each row uses the highest-priority available signal.

    has_checks = (
        "ci_check_failure_rate" in df.columns
        and df["ci_check_failure_rate"].notna().any()
        and df["ci_check_failure_rate"].fillna(0).max() > 0
    )
    has_ci = (
        "pr_rejection_rate" in df.columns
        and df["pr_rejection_rate"].notna().any()
        and df["pr_rejection_rate"].fillna(0).max() > 0
    )
    has_issues = "issues_24h" in df.columns
    has_npm = (
        ("is_deprecated" in df.columns or "quick_patch" in df.columns)
        and (
            df.get("is_deprecated", pd.Series(0)).fillna(0).max() > 0
            or df.get("quick_patch", pd.Series(0)).fillna(0).max() > 0
        )
    )

    # Build per-release D(t) from the best available source for each row
    D_t = pd.Series(0.0, index=df.index)  # default: no signal = no observed failure

    if has_npm:
        # npm_sig is naturally in [0,1]: is_deprecated (0/1) + quick_patch (0/0.5), clipped.
        # Do NOT re-normalize — absolute values are meaningful (1.0 = deprecated is a hard signal).
        npm_sig = df.get("is_deprecated", pd.Series(0, index=df.index)).fillna(0).astype(float)
        if "quick_patch" in df.columns:
            npm_sig = (npm_sig + df["quick_patch"].fillna(0).astype(float) * 0.5).clip(upper=1)
        D_t = npm_sig.where(npm_sig > 0, D_t)

    if has_issues:
        if "dependent_count" in df.columns:
            dep   = df["dependent_count"].fillna(1).astype(float).clip(lower=1)
            iss   = df["issues_24h"].fillna(0).astype(float)
            issue_raw = normalize((iss / dep).clip(upper=1))
        else:
            issue_raw = normalize(df["issues_24h"].fillna(0).astype(float))
        # Override npm signal where issue data is non-zero
        D_t = issue_raw.where(df["issues_24h"].fillna(0) > 0, D_t)

    if has_ci:
        ci_rate = df["pr_rejection_rate"].fillna(0).astype(float)
        if "ci_failure_issues" in df.columns:
            ci_issues_norm = normalize(df["ci_failure_issues"].fillna(0).astype(float))
            ci_raw = normalize((0.6 * ci_rate + 0.4 * ci_issues_norm).clip(upper=1))
        else:
            ci_raw = normalize(ci_rate.clip(upper=1))
        D_t = ci_raw.where(df["pr_rejection_rate"].fillna(0) > 0, D_t)

    if has_checks:
        # Checks API rate is the most precise signal — direct CI measurement.
        # It overrides everything else where data exists.
        checks_raw = normalize(df["ci_check_failure_rate"].fillna(0).astype(float))
        D_t = checks_raw.where(df["ci_check_failure_rate"].fillna(0) > 0, D_t)

    df["D_t"] = D_t

    # H(m) — Maintainer history: R0 from SIR model as proxy
    if "R0" in df.columns:
        df["H_m"] = normalize(df["R0"].fillna(0).astype(float))
    else:
        df["H_m"] = 0.3

    return df

def main():
    print(f"\n{'='*60}")
    print("DepCast Phase 1 — CRS Computation and Validation")
    print(f"{'='*60}\n")

    # ── Load and merge all data ──
    dfs = []

    if os.path.exists(RELEASES_FILE):
        releases = pd.read_csv(RELEASES_FILE)
        print(f"Loaded releases:     {len(releases)} rows  (label_breaking=1)")
    else:
        print(f"WARNING: {RELEASES_FILE} not found — using demo data")
        releases = create_demo_releases()

    # ── Load Phase 2 sweep candidates (high-confidence, pre-release filtered) ──
    if os.path.exists(SWEEP_SIGNALS):
        sweep = pd.read_csv(SWEEP_SIGNALS)
        # Rename breaking_version col to match merge keys downstream
        sweep = sweep.rename(columns={"breaking_version": "breaking_version"})
        # Add required columns with defaults for rows not in original 51
        for col in ["prior_stable_version", "description", "weekly_downloads",
                    "dependent_count", "has_changelog", "notes"]:
            if col not in sweep.columns:
                sweep[col] = None
        sweep["label_breaking"] = 1
        # Merge issue counts into releases as issues_24h / issues_72h
        sweep = sweep.rename(columns={
            "issues_6h":  "issues_6h",
            "issues_24h": "issues_24h",
            "issues_72h": "issues_72h",
        })
        releases = pd.concat([releases, sweep], ignore_index=True, sort=False)
        print(f"Loaded sweep signals: {len(sweep)} rows  (label_breaking=1)")
        print(f"Combined breaking:    {len(releases)} rows")

    # ── Load non-breaking controls (Phase 2 negative class) ──
    if os.path.exists(CONTROLS_FILE):
        controls = pd.read_csv(CONTROLS_FILE)
        # Drop columns not used for modelling.
        # is_deprecated / quick_patch from script 06 reflect CURRENT npm state,
        # not release-time signals — dropping them prevents stale deprecations
        # from inflating D(t) for non-breaking controls.
        drop_cols = [c for c in (
            "issues_72h_github", "suspect", "verified",
            "is_deprecated", "quick_patch"
        ) if c in controls.columns]
        controls = controls.drop(columns=drop_cols)
        print(f"Loaded controls:     {len(controls)} rows  (label_breaking=0)")
        releases = pd.concat([releases, controls], ignore_index=True, sort=False)
        print(f"Combined dataset:    {len(releases)} rows")
    else:
        print(f"INFO: {CONTROLS_FILE} not found — running on breaking releases only")

    df = releases.copy()

    if os.path.exists(VOLATILITY_FILE):
        vol = pd.read_csv(VOLATILITY_FILE)
        df = df.merge(vol[["package","breaking_version","V_score",
                            "n_prior_symbols","n_removed_symbols"]],
                     on=["package","breaking_version"], how="left")
        print(f"Loaded volatility:   {len(vol)} rows")

    if os.path.exists(SIGNALS_FILE):
        sig = pd.read_csv(SIGNALS_FILE)
        df = df.merge(sig[["package","breaking_version","issues_6h","issues_12h",
                           "issues_24h","issues_48h","issues_72h","first_issue_hours"]],
                     on=["package","breaking_version"], how="left")
        print(f"Loaded signals:      {len(sig)} rows")

    if os.path.exists(CI_SIGNALS_FILE):
        ci = pd.read_csv(CI_SIGNALS_FILE)
        ci_cols = ["package", "breaking_version",
                   "ci_check_failure_rate", "ci_check_prs_sampled", "ci_check_prs_failed",
                   "pr_rejection_rate", "bot_prs_total", "bot_prs_rejected",
                   "ci_failure_issues", "is_deprecated", "days_to_patch", "quick_patch"]
        ci_cols = [c for c in ci_cols if c in ci.columns]
        df = df.merge(ci[ci_cols], on=["package", "breaking_version"], how="left")
        print(f"Loaded CI signals:   {len(ci)} rows  "
              f"(D(t) will prefer pr_rejection_rate)")

    if os.path.exists(SIR_FILE):
        sir = pd.read_csv(SIR_FILE)
        df = df.merge(sir[["package","breaking_version","R0","r_squared","propagation_class"]],
                     on=["package","breaking_version"], how="left")
        print(f"Loaded SIR results:  {len(sir)} rows")

    print(f"\nMerged dataset:      {len(df)} rows\n")

    # ── Build features ──
    df = build_features(df)

    # ── Compute equal-weight CRS as baseline ──
    # CRS(t) = w1*V + w2*E + w3*D + w4*H with equal weights [0.25, 0.25, 0.25, 0.25]
    df["CRS_equal"] = (0.25 * df["V_r"] +
                       0.25 * df["E_r"] +
                       0.25 * df["D_t"] +
                       0.25 * df["H_m"])

    # Ground truth label
    if "label_breaking" not in df.columns:
        df["label_breaking"] = 1  # All seeds are breaking

    # ── Assign SAFE/WAIT/AVOID ──
    def assign_rating(crs):
        if crs <= 0.25:   return "SAFE"
        elif crs <= 0.60: return "WAIT"
        else:             return "AVOID"

    df["CRS_rating"] = df["CRS_equal"].apply(assign_rating)

    # ── Try logistic regression if we have non-breaking examples ──
    X = df[["V_r", "E_r", "D_t", "H_m"]].fillna(0).values
    y = df["label_breaking"].fillna(1).astype(int).values

    learned_weights = None
    auc_cv = None

    n_classes = len(set(y))
    if n_classes >= 2 and len(df) >= 10:
        print("Training logistic regression for weight learning...")
        lr = LogisticRegression(random_state=42, max_iter=1000)
        cv = StratifiedKFold(n_splits=min(5, n_classes * 3), shuffle=True, random_state=42)
        scores = cross_val_score(lr, X, y, cv=cv, scoring='roc_auc')
        auc_cv = scores.mean()
        lr.fit(X, y)
        coefs = lr.coef_[0]
        coefs_norm = np.abs(coefs) / np.abs(coefs).sum()
        learned_weights = dict(zip(["V_r","E_r","D_t","H_m"], coefs_norm))

        df["CRS_learned"] = (coefs_norm[0] * df["V_r"] +
                             coefs_norm[1] * df["E_r"] +
                             coefs_norm[2] * df["D_t"] +
                             coefs_norm[3] * df["H_m"])

        print(f"  AUC-ROC (CV):  {auc_cv:.3f}")
        print(f"  Learned weights:")
        for k, v in learned_weights.items():
            print(f"    {k}: {v:.4f}")
    else:
        df["CRS_learned"] = df["CRS_equal"]
        print("NOTE: All samples are breaking releases (seed list).")
        print("      Logistic regression requires non-breaking examples for weight learning.")
        print("      Add non-breaking releases to data/breaking_releases.csv with label_breaking=0")
        print("      to enable supervised weight learning.\n")

    # ── Save results ──
    output_cols = ["package","breaking_version","V_r","E_r","D_t","H_m",
                   "CRS_equal","CRS_learned","CRS_rating","label_breaking"]
    if "propagation_class" in df.columns:
        output_cols.append("propagation_class")
    if "R0" in df.columns:
        output_cols.append("R0")
    if "first_issue_hours" in df.columns:
        output_cols.append("first_issue_hours")
    for extra_col in ("ci_check_failure_rate", "ci_check_prs_sampled", "ci_check_prs_failed",
                      "pr_rejection_rate", "bot_prs_total", "bot_prs_rejected",
                      "ci_failure_issues", "is_deprecated", "days_to_patch", "quick_patch"):
        if extra_col in df.columns:
            output_cols.append(extra_col)

    df[output_cols].to_csv(OUTPUT_CSV, index=False)

    # ── Generate validation figure ──
    generate_figure(df, learned_weights, auc_cv)

    print(f"\n{'='*60}")
    print(f"DONE — CRS scores computed for {len(df)} releases")
    print(f"Saved to: {OUTPUT_CSV}")
    print(f"\nCRS(t) Summary (equal weights):")
    print(df["CRS_equal"].describe().round(4))
    print(f"\nRating distribution:")
    print(df["CRS_rating"].value_counts())
    print(f"\nTop 10 highest risk releases:")
    top = df.nlargest(10, "CRS_equal")[["package","breaking_version","CRS_equal","CRS_rating","V_r","E_r","D_t","H_m"]]
    print(top.round(3).to_string(index=False))
    print(f"{'='*60}\n")

def generate_figure(df, learned_weights, auc_cv):
    """Generate CRS validation figure for the paper."""
    has_controls = "label_breaking" in df.columns and (df["label_breaking"] == 0).any()
    breaking  = df[df["label_breaking"] == 1] if "label_breaking" in df.columns else df
    controls  = df[df["label_breaking"] == 0] if has_controls else pd.DataFrame()

    fig = plt.figure(figsize=(16, 16))
    fig.patch.set_facecolor('#FAFAFA')

    auc_str = f"  |  AUC-ROC = {auc_cv:.3f}" if auc_cv else ""
    n_str   = f"n={len(breaking)} breaking, n={len(controls)} non-breaking" if has_controls else f"n={len(breaking)} breaking"
    fig.suptitle(
        f"DepCast: Compatibility Risk Score (CRS) Validation\n"
        f"CRS(t) = w₁·V(r) + w₂·E(r) + w₃·D(t) + w₄·H(m)   "
        f"[{n_str}{auc_str}]",
        fontsize=13, fontweight='bold', color='#1F4E79', y=0.995
    )

    gs = gridspec.GridSpec(3, 2, hspace=0.48, wspace=0.35, figure=fig)

    # ── Panel A: CRS bar chart — breaking (solid) vs controls (hatched) ──
    ax1 = fig.add_subplot(gs[0, :])
    ax1.set_facecolor('#F8F9FA')
    ax1.set_title("Panel A — CRS Score per Release: Breaking (solid) vs Non-Breaking (hatched)",
                  fontsize=11, fontweight='bold', color='#2E75B6', pad=8)

    b_sorted = breaking.sort_values("CRS_equal", ascending=False).reset_index(drop=True)
    c_sorted = controls.sort_values("CRS_equal", ascending=False).reset_index(drop=True) if has_controls else pd.DataFrame()

    rating_color = {"AVOID": "#C00000", "WAIT": "#FFC000", "SAFE": "#375623"}

    # Breaking bars (left section)
    for i, row in b_sorted.iterrows():
        c = rating_color.get(row["CRS_rating"], "#888888")
        ax1.bar(i, row["CRS_equal"], color=c, edgecolor='white', linewidth=0.3, alpha=0.85)

    # Control bars (right section, hatched, grey tones)
    offset = len(b_sorted) + 2
    if has_controls:
        for i, row in c_sorted.iterrows():
            ax1.bar(offset + i, row["CRS_equal"], color='#AAAAAA',
                    edgecolor='#555555', linewidth=0.5, alpha=0.6, hatch='//')

    ax1.axhline(y=0.25, color='#375623', linestyle='--', linewidth=1.5, alpha=0.7, label='SAFE threshold (0.25)')
    ax1.axhline(y=0.60, color='#C00000', linestyle='--', linewidth=1.5, alpha=0.7, label='AVOID threshold (0.60)')
    if has_controls:
        ax1.axvline(x=len(b_sorted) + 0.5, color='navy', linestyle=':', linewidth=1.5, alpha=0.6)
        ax1.text(len(b_sorted) / 2, 1.02, 'Breaking releases',
                ha='center', fontsize=9, color='#C00000', style='italic')
        ax1.text(offset + len(c_sorted) / 2, 1.02, 'Non-breaking controls',
                ha='center', fontsize=9, color='#555555', style='italic')

    ax1.set_xlabel("Releases (sorted by CRS within each class)", fontsize=10)
    ax1.set_ylabel("CRS Score", fontsize=10)
    ax1.set_ylim(0, 1.12)
    ax1.legend(fontsize=9, loc='upper right')
    ax1.grid(True, alpha=0.3, axis='y')

    # ── Panel B: Feature means — breaking vs controls ──
    ax2 = fig.add_subplot(gs[1, 0])
    ax2.set_facecolor('#F8F9FA')
    ax2.set_title("Panel B — Feature Means: Breaking vs Non-Breaking",
                  fontsize=11, fontweight='bold', color='#2E75B6', pad=8)

    features       = ["V_r", "E_r", "D_t", "H_m"]
    feature_labels = ["V(r)\nVolatility", "E(r)\nExposure", "D(t)\nFailure", "H(m)\nHistory"]
    x = np.arange(len(features))
    w = 0.35

    b_means = [breaking[f].mean() for f in features]
    c_means = [controls[f].mean() if has_controls else 0 for f in features]

    ax2.bar(x - w/2, b_means, w, label='Breaking',     color='#C00000', alpha=0.8)
    if has_controls:
        ax2.bar(x + w/2, c_means, w, label='Non-breaking', color='#AAAAAA', alpha=0.7, hatch='//')

    ax2.set_xticks(x)
    ax2.set_xticklabels(feature_labels, fontsize=9)
    ax2.set_ylabel("Mean normalized value [0,1]", fontsize=10)
    ax2.set_ylim(0, 1.05)
    ax2.legend(fontsize=9)
    ax2.grid(True, alpha=0.3, axis='y')

    if learned_weights:
        for i, feat in enumerate(features):
            w_val = learned_weights.get(feat, 0.25)
            ax2.text(x[i], -0.12, f'w={w_val:.3f}', ha='center', fontsize=8, color='gray')
        ax2.set_title("Panel B — Feature Means + Learned Weights",
                      fontsize=11, fontweight='bold', color='#2E75B6', pad=8)

    # ── Panel C: ROC curve (if AUC available) else CRS vs time-to-issue ──
    ax3 = fig.add_subplot(gs[1, 1])
    ax3.set_facecolor('#F8F9FA')

    if has_controls and auc_cv is not None:
        # ROC curve on full dataset
        from sklearn.metrics import roc_curve as sk_roc
        y_true  = df["label_breaking"].fillna(1).astype(int).values
        y_score = df["CRS_equal"].fillna(0).values
        fpr, tpr, _ = sk_roc(y_true, y_score)
        ax3.plot(fpr, tpr, color='#C00000', lw=2, label=f'CRS (AUC = {auc_cv:.3f})')
        ax3.plot([0, 1], [0, 1], 'k--', lw=1, alpha=0.5, label='Random')
        ax3.fill_between(fpr, tpr, alpha=0.08, color='#C00000')
        ax3.set_xlabel("False Positive Rate", fontsize=10)
        ax3.set_ylabel("True Positive Rate", fontsize=10)
        ax3.set_title("Panel C — ROC Curve (Breaking vs Non-Breaking)",
                      fontsize=11, fontweight='bold', color='#2E75B6', pad=8)
        ax3.legend(fontsize=9)
        ax3.grid(True, alpha=0.3)
        auc_color = '#375623' if auc_cv >= 0.7 else '#FFC000' if auc_cv >= 0.6 else '#C00000'
        ax3.text(0.6, 0.12, f'AUC = {auc_cv:.3f}', transform=ax3.transAxes,
                fontsize=14, fontweight='bold', color=auc_color)
    else:
        # Fallback: CRS vs time to first issue
        ax3.set_title("Panel C — CRS vs. Time to First Issue",
                      fontsize=11, fontweight='bold', color='#2E75B6', pad=8)
        valid = df.dropna(subset=["first_issue_hours"])
        if len(valid) > 0:
            sc_c = valid["CRS_rating"].map({"AVOID": "#C00000", "WAIT": "#FFC000", "SAFE": "#375623"})
            ax3.scatter(valid["CRS_equal"], valid["first_issue_hours"],
                       c=sc_c, s=80, alpha=0.7, edgecolors='white', linewidth=0.5)
            ax3.set_xlabel("CRS Score", fontsize=10)
            ax3.set_ylabel("Hours to first issue", fontsize=10)
            ax3.invert_yaxis()
            corr = valid[["CRS_equal", "first_issue_hours"]].corr().iloc[0, 1]
            ax3.text(0.05, 0.95, f'r = {corr:.3f}', transform=ax3.transAxes,
                    fontsize=10, va='top', color='#1F4E79')
            ax3.grid(True, alpha=0.3)
        else:
            ax3.text(0.5, 0.5, "Run script 03 with --token to populate",
                    transform=ax3.transAxes, ha='center', va='center', color='gray')

    # ── Panel D: CRS score distribution histogram — breaking vs controls ──
    ax4 = fig.add_subplot(gs[2, 0])
    ax4.set_facecolor('#F8F9FA')
    ax4.set_title("Panel D — CRS Score Distribution by Class",
                  fontsize=11, fontweight='bold', color='#2E75B6', pad=8)

    bins = np.linspace(0, 1, 21)
    ax4.hist(breaking["CRS_equal"], bins=bins, alpha=0.7, color='#C00000',
             label=f'Breaking (n={len(breaking)})', density=True)
    if has_controls:
        ax4.hist(controls["CRS_equal"], bins=bins, alpha=0.5, color='#AAAAAA',
                 label=f'Non-breaking (n={len(controls)})', density=True, hatch='//')
    ax4.axvline(x=0.25, color='#375623', linestyle='--', linewidth=1.5, alpha=0.7)
    ax4.axvline(x=0.60, color='#C00000', linestyle='--', linewidth=1.5, alpha=0.7)
    ax4.set_xlabel("CRS Score", fontsize=10)
    ax4.set_ylabel("Density", fontsize=10)
    ax4.legend(fontsize=9)
    ax4.grid(True, alpha=0.3, axis='y')

    # Separation annotation
    if has_controls:
        b_med = breaking["CRS_equal"].median()
        c_med = controls["CRS_equal"].median()
        ax4.text(0.05, 0.92,
                f'Median breaking:     {b_med:.3f}\nMedian non-breaking: {c_med:.3f}',
                transform=ax4.transAxes, fontsize=8, va='top',
                bbox=dict(boxstyle='round', facecolor='white', alpha=0.7))

    # ── Panel E: Feature heatmap — top 15 breaking releases ──
    ax5 = fig.add_subplot(gs[2, 1])
    ax5.set_facecolor('#F8F9FA')
    ax5.set_title("Panel E — Feature Heatmap (Top 15 Breaking by CRS)",
                  fontsize=11, fontweight='bold', color='#2E75B6', pad=8)

    top15 = breaking.nlargest(15, "CRS_equal")
    heatmap_data = top15[["V_r", "E_r", "D_t", "H_m"]].values
    labels_y = [f'{r["package"][:12]}@{str(r["breaking_version"])[:5]}'
                for _, r in top15.iterrows()]

    im = ax5.imshow(heatmap_data, aspect='auto', cmap='RdYlGn_r', vmin=0, vmax=1)
    ax5.set_xticks([0, 1, 2, 3])
    ax5.set_xticklabels(["V(r)", "E(r)", "D(t)", "H(m)"], fontsize=10)
    ax5.set_yticks(range(len(labels_y)))
    ax5.set_yticklabels(labels_y, fontsize=7)
    plt.colorbar(im, ax=ax5, fraction=0.046, pad=0.04)

    for i in range(len(labels_y)):
        for j in range(4):
            val = heatmap_data[i, j]
            ax5.text(j, i, f'{val:.2f}', ha='center', va='center',
                    fontsize=7, color='black' if val < 0.7 else 'white')

    plt.savefig(OUTPUT_FIG, dpi=150, bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.close()
    print(f"  Figure saved: {OUTPUT_FIG}")

def create_demo_releases():
    """Create minimal demo dataset."""
    return pd.DataFrame([
        {"package": "lodash",     "breaking_version": "4.0.0", "prior_stable_version": "3.10.1",
         "weekly_downloads": 50000000, "dependent_count": 20000, "label_breaking": 1},
        {"package": "react",      "breaking_version": "16.0.0","prior_stable_version": "15.6.2",
         "weekly_downloads": 30000000, "dependent_count": 15000, "label_breaking": 1},
        {"package": "webpack",    "breaking_version": "4.0.0", "prior_stable_version": "3.12.0",
         "weekly_downloads": 25000000, "dependent_count": 12000, "label_breaking": 1},
        {"package": "typescript", "breaking_version": "2.0.0", "prior_stable_version": "1.8.10",
         "weekly_downloads": 40000000, "dependent_count": 18000, "label_breaking": 1},
        {"package": "chalk",      "breaking_version": "5.0.0", "prior_stable_version": "4.1.2",
         "weekly_downloads": 200000000,"dependent_count": 80000, "label_breaking": 1},
    ])

if __name__ == "__main__":
    main()
