# DepCast

**A Two-Sided Compatibility Intelligence Protocol for Software Package Ecosystems**

> Farag, A. (2026). *DepCast: A Two-Sided Compatibility Intelligence Protocol for Software Package Ecosystems.* Research Position Paper v0.5. Prepared for arXiv cs.SE and MSR 2027.

---

## Overview

DepCast proposes a two-sided protocol that inserts a **pre-publish impact gate** on the package publisher side and a **live-signal pre-upgrade gate** on the consumer side, connected by a shared intelligence core that aggregates opt-in CI/CD failure telemetry across organizations.

The core insight: every failing build after a dependency upgrade is a signal. These signals currently evaporate — unseen, unaggregated, and unused. DepCast collects them, models their propagation epidemiologically, and returns them as a continuously updated **Compatibility Risk Score (CRS)**:

```
CRS(t) = w₁·V(r) + w₂·E(r) + w₃·D(t) + w₄·H(m)
```

| Factor | Description |
|--------|-------------|
| V(r) | API surface volatility — proportion of prior exported symbols removed |
| E(r) | Downstream exposure — weighted dependent package count |
| D(t) | Observed failure rate — proportion of early adopters reporting CI failure |
| H(m) | Maintainer history — exponentially weighted prior R₀ values |

---

## Key Empirical Findings

Phase 1 empirical study of 51 confirmed breaking npm releases (2013–2023):

**Finding 1 — Propagation signals are universal and fast**
Community failure signals were detectable for all 46 releases with retrievable publish timestamps (100%). Median time-to-first-issue: **1.02 hours**. 87% of releases generated signals within 6 hours of publish.

**Finding 2 — Pattern C: breaking without detected symbol removal**
37% of confirmed breaking releases (19/51) show V(r)=0 — they break ecosystems without removing any detected exported symbol. These are invisible to static API diff tools yet generate an average of 32.1 GitHub issues within 24 hours, directly motivating the D(t) runtime signal component.

**Finding 3 — Epidemiological propagation observed in confirmed-breaking sample**
All 44 clean fitted releases exhibit R₀ > 1.0 under a SIR model (median R₀=1.42, n=44 excluding two fitting-artifact outliers). Zero releases are contained (R₀ < 1.0). In this confirmed-breaking sample, all clean fitted releases spread beyond initial adopters.

---

## Repository Structure

```
depcast/
├── README.md
├── paper/
│   └── DepCast_v0.5.docx               # Research position paper
├── data/
│   ├── breaking_releases.csv            # 51 confirmed breaking npm releases
│   ├── propagation_signals.csv          # N(t) GitHub issue counts, 72h window, n=46
│   ├── ci_signals.csv                   # D(t) signals: bot PR rejection, CI failures, npm deprecation
│   ├── api_volatility.csv               # V(r) scores for 51 releases
│   ├── sir_model_results.csv            # SIR model R₀ for 46 releases (with outlier flags)
│   └── crs_scores.csv                   # CRS(t) scores for 51 releases
├── scripts/
│   ├── 01_collect_breaking_releases.py  # Seed dataset collection from npm registry
│   ├── 02_compute_api_volatility.py     # V(r) via heuristic export-declaration extraction
│   ├── 03_fetch_propagation_signals.py  # N(t) via date-filtered GitHub Search API
│   ├── 03b_fetch_ci_signals.py          # D(t) via Dependabot/Renovate PR rejection + CI failures
│   ├── 04_fit_sir_model.py              # SIR model fitting and R₀ estimation
│   └── 05_compute_crs_validation.py     # CRS computation and validation figures
└── figures/
    ├── sir_propagation_curves_v2.png    # Figure 1: SIR propagation curves
    └── crs_validation_v2.png           # Figure 2: CRS validation dashboard
```

---

## Replication

### Requirements

```bash
pip install requests pandas scipy matplotlib seaborn numpy scikit-learn
```

### Pipeline

```bash
python scripts/01_collect_breaking_releases.py
python scripts/02_compute_api_volatility.py
python scripts/03_fetch_propagation_signals.py --token YOUR_GITHUB_TOKEN
python scripts/03b_fetch_ci_signals.py --token YOUR_GITHUB_TOKEN
# Optional: add --checks-api for direct CI measurement (adds ~2 API calls per PR)
# python scripts/03b_fetch_ci_signals.py --token YOUR_GITHUB_TOKEN --checks-api --max-prs 10
python scripts/04_fit_sir_model.py
python scripts/05_compute_crs_validation.py
```

A GitHub personal access token with `public_repo` scope is required for scripts 03 and 03b. Tokens can be generated at https://github.com/settings/tokens.

Script 03b is optional but recommended. It collects three signal types:

| Signal | Source | API token required | Works for old releases? |
|--------|--------|--------------------|------------------------|
| `pr_rejection_rate` | Dependabot/Renovate PRs (GitHub) | Yes | Yes — retroactive scans from 2018+ cover pre-2018 releases |
| `ci_failure_issues` | CI-failure keyword issues (GitHub) | Yes | Partially (~2018+) |
| `is_deprecated` / `quick_patch` | npm registry deprecation + patch timeline | No | Yes — all releases |

When `data/ci_signals.csv` is present, script 05 applies a per-release signal priority: CI rejection rate overrides issue counts, npm signals fill in where both GitHub sources are zero (common for pre-2019 releases where Dependabot didn't exist and GitHub search history is sparse).

### V(r) method

V(r) uses heuristic export-declaration extraction — pattern matching over five JavaScript/TypeScript export syntax forms within npm package tarballs. It cannot detect dynamic exports or build-time code generation. **V(r)=0 does not mean a release is non-breaking** — this is the Pattern C finding (paper Section 5.4).

### SIR outlier flags

Two releases in `sir_model_results.csv` are flagged via `is_R0_outlier=1` and excluded from aggregate R₀ statistics:
- `eslint@7.0.0` (R₀=38.6) — rapid N(t) saturation within 6h causes optimizer degeneracy
- `yargs@17.0.0` (R₀=9.4) — sparse propagation curve (3 issues in 72h window)

---

## CRS Protocol

**Publisher pipeline:**
```
Code → Build → Tests → [ DEPCAST PUBLISHER GATE ] → Publish
```

**Consumer pipeline:**
```
Dependency Update → [ DEPCAST CONSUMER GATE ] → Build → Tests → Deploy
```

| Rating | CRS Range | Action |
|--------|-----------|--------|
| SAFE   | 0.0 – 0.25 | Proceed |
| WAIT   | 0.26 – 0.60 | Delay 24–48h, monitor telemetry |
| AVOID  | 0.61 – 1.0  | Pin to prior version, await patch |

---

## Research Agenda

| Phase | Task | Status | Target Venue |
|-------|------|--------|--------------|
| 1 | Empirical study: 51 breaking npm releases; SIR propagation model; CRS scoring | **Done (v0.5)** | arXiv cs.SE |
| 1b | D(t) signal stack: GitHub Checks API on bot PRs; npm deprecation; retroactive Dependabot coverage | **Implemented** | — |
| 2 | Extend to 200+ releases across npm, PyPI, pub.dev; SIR-on-graph model | Planned | MSR 2027 |
| 3 | Add non-breaking releases; logistic regression weight learning; AUC-ROC validation | Planned | EMSE 2027 |
| 4 | Publisher gate prototype as npm package; false positive/negative measurement | Planned | ICSME 2027 |
| 5 | Renovate plugin for live telemetry aggregation *(see spec below)* | Planned | ICSE industry |
| 6 | Cross-ecosystem replication on PyPI and pub.dev | Planned | MSR 2028 |

### Renovate Plugin Specification (Phase 5)

The highest-leverage live-signal source is a **Renovate plugin** that emits anonymized upgrade outcome events to a DepCast aggregator endpoint. Renovate runs inside CI pipelines of hundreds of thousands of repos, making even a 1% opt-in rate sufficient for statistically significant per-release signal within hours of publish.

**Plugin contract (proposed):**
```json
POST https://api.depcast.io/v1/signal
{
  "package":   "chalk",
  "from":      "4.1.2",
  "to":        "5.0.0",
  "outcome":   "ci_failed" | "merged" | "closed_manual",
  "checks_total": 12,
  "checks_failed": 3,
  "repo_hash": "<sha256 of org/repo — never raw>",
  "ts":        1714000000
}
```

**Privacy model:** `repo_hash` is a one-way hash; no repo identity is stored. Only aggregate counts per `(package, to_version)` are surfaced via the DepCast API. Opt-in is explicit via `depcast: true` in `renovate.json`.

**Research value:** replaces the GitHub Search API approximation with ground-truth CI outcomes aggregated in real time — the data source that makes DepCast's D(t) component publishable at the precision required for ICSE industry track.

---

## Citation

```bibtex
@techreport{farag2026depcast,
  title  = {DepCast: A Two-Sided Compatibility Intelligence Protocol
            for Software Package Ecosystems},
  author = {Farag, Abdelrahman},
  year   = {2026},
  month  = {April},
  note   = {Research Position Paper v0.5. arXiv cs.SE / MSR 2027},
  url    = {https://github.com/afarag/depcast}
}
```

---

## Author

**Abdelrahman Farag**
AWS Cloud & DevOps Engineer, Sopra Steria
MSc Candidate, AI Research — Universidad Internacional Menéndez Pelayo (UIMP)
Financial Engineering Program — WorldQuant University

---

## License

Data and scripts: MIT License.
Paper (paper/): © Abdelrahman Farag, 2026. All rights reserved.
