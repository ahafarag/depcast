<div align="center">

# DepCast

**A Two-Sided Compatibility Intelligence Protocol for Software Package Ecosystems**

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![npm](https://img.shields.io/badge/npm-depcast--check@1.0.0-red.svg)](https://www.npmjs.com/package/depcast-check)
[![Status](https://img.shields.io/badge/status-active%20research-orange.svg)]()
[![Target](https://img.shields.io/badge/target-MSR%202027-blueviolet.svg)]()
[![DOI](https://img.shields.io/badge/DOI-10.5281%2Fzenodo.20361569-blue.svg)](https://doi.org/10.5281/zenodo.20361569)
[![Ko-fi](https://img.shields.io/badge/Support-Ko--fi-FF5E5B?logo=kofi&logoColor=white)](https://ko-fi.com/ahafarag)

*Farag, A. (2026). DepCast: A Two-Sided Compatibility Intelligence Protocol for Software Package Ecosystems. Zenodo. https://doi.org/10.5281/zenodo.20361569*

</div>

---

## Overview

Every failing build after a dependency upgrade is a signal. These signals currently evaporate — unseen, unaggregated, and unused.

DepCast proposes a **two-sided protocol** that inserts a pre-publish impact gate on the publisher side and a live-signal pre-upgrade gate on the consumer side, connected by a shared intelligence core that aggregates opt-in CI/CD failure telemetry across organizations.

The result is a continuously updated **Compatibility Risk Score (CRS)**:

```
CRS(t) = w₁·V(r) + w₂·E(r) + w₃·D(t) + w₄·H(m)
```

| Factor | Description | Weight (learned) |
|--------|-------------|-----------------|
| V(r) | API surface volatility — proportion of exported symbols removed | 0.335 |
| E(r) | Downstream exposure — weighted dependent package count | 0.035 |
| D(t) | Observed failure rate — CI failures from early adopters | 0.584 |
| H(m) | Maintainer history — exponentially weighted prior R₀ values | 0.046 |

| Rating | CRS Range | Action |
|--------|-----------|--------|
| **SAFE** | 0.00 – 0.25 | Proceed with upgrade |
| **WAIT** | 0.26 – 0.60 | Delay 24–48h, monitor telemetry |
| **AVOID** | 0.61 – 1.00 | Pin to prior version, await patch |

---

## Try It

When `chalk@5.0.0` dropped it switched to pure ESM, breaking thousands of `require()` builds overnight. No functions were deleted — static analysis tools gave it a clean pass. DepCast catches the class of change that causes this:

```bash
npx depcast-check --package chalk --version 5.0.0
```

```
  depcast-check  chalk@5.0.0  (prior: 4.1.2)
  ───────────────────────────────────────────────────
  V(r)  API volatility       0.000  ░░░░░░░░░░░░░░░░░░░░  Pattern C
  E(r)  Downstream exposure  0.611  ████████████░░░░░░░░  439M wkly downloads
  D(t)  Observed failures    0.000  ░░░░░░░░░░░░░░░░░░░░  0 issues / 24h
  H(m)  Maintainer history   0.030  ░░░░░░░░░░░░░░░░░░░░  R0 = 1.162
  ───────────────────────────────────────────────────
  ! Pattern C — no symbols removed; behaviour change possible
  ───────────────────────────────────────────────────
  CRS 0.186  ·  SAFE  ·  proceed with publish
```

Static tools: silent. DepCast: SAFE, but flags **Pattern C** — alerting you this is the class of change that causes runtime chaos even when the API surface looks unchanged. 62.4% of confirmed breaking npm releases are Pattern C.

See [`packages/depcast-check/`](packages/depcast-check/) for full CLI documentation and GitHub Actions integration.

---

## Key Empirical Findings

Empirical study of **396 confirmed breaking releases** across npm, PyPI, and pub.dev:

**Finding 1 — Propagation signals are universal and fast**
Community failure signals surface within a median of **~1 hour** across all three ecosystems (npm: 1.0h, PyPI: 1.1h, pub.dev: 1.1h). 87% of npm releases generated signals within 6 hours of publish.

**Finding 2 — Pattern C: breaking without detected symbol removal**
62.4% of breaking npm releases show V(r)=0 — they break ecosystems without removing any detected exported symbol. These are invisible to static API diff tools. Pattern C rates: npm=62.4%, PyPI=24.0%, pub.dev=72.0%.

**Finding 3 — Cross-ecosystem R₀ comparison**

| Ecosystem | n | R₀ median | Pattern C | First issue (median) |
|-----------|---|-----------|-----------|----------------------|
| npm | 306 | 1.44 | 62.4% | 1.0h |
| PyPI | 25 | 0.99 | 24.0% | 1.1h |
| pub.dev | 25 | **10.3** | 72.0% | 1.1h |

PyPI is sub-critical (R₀ < 1): pip's pinning culture absorbs shocks. pub.dev's R₀ ≈ 10 is driven by Flutter's null-safety migration forcing a simultaneous ecosystem-wide update.

**Finding 4 — AUC-ROC = 0.853 on 346-release validation set**
Logistic regression on 306 breaking + 40 non-breaking controls. D(t) is the dominant discriminating feature (w=0.584), with V(r) as a strong secondary signal (w=0.335).

**Top AVOID-rated releases:**

| Package | Version | CRS | Reason |
|---------|---------|-----|--------|
| glob | 9.0.0 | 0.631 | Complete API surface removed + high issue rate |
| semver | 7.0.0 | 0.622 | 718M weekly downloads — highest E(r) in dataset |
| moment | 2.0.0 | 0.580 | 39.5% Dependabot rejection rate |

---

## Figures

<div align="center">

| Figure 1 — SIR Propagation Curves | Figure 2 — CRS Validation Dashboard |
|-----------------------------------|--------------------------------------|
| ![SIR](figures/sir_propagation_curves_v2.png) | ![CRS](figures/crs_validation_v2.png) |

</div>

---

## Repository Structure

```
depcast-public/
├── data/
│   ├── breaking_releases.csv            # 306 confirmed breaking npm releases
│   ├── nonbreaking_releases.csv         # 40 non-breaking controls (label_breaking=0)
│   ├── pypi_breaking_releases.csv       # 25 confirmed breaking PyPI releases
│   ├── pubdev_breaking_releases.csv     # 25 confirmed breaking pub.dev releases
│   ├── combined_crs_scores.csv          # 396-release combined dataset (all ecosystems)
│   ├── sweep_top290_candidates.csv      # 290 high-confidence npm deprecation candidates
│   ├── propagation_signals.csv          # N(t) GitHub issue counts, 72h window
│   ├── ci_signals.csv                   # D(t): Dependabot PR rejection + CI failures
│   ├── api_volatility.csv               # V(r) scores (npm)
│   ├── pypi_api_volatility.csv          # V(r) scores (PyPI, AST-based)
│   ├── sir_model_results.csv            # SIR R₀ for npm releases
│   ├── pypi_sir_results.csv             # SIR R₀ for PyPI releases
│   └── pubdev_sir_results.csv           # SIR R₀ for pub.dev releases
├── scripts/
│   ├── 00_fix_npm_metadata.py           # Patch published_at + weekly_downloads
│   ├── 01_collect_breaking_releases.py  # npm seed dataset collection
│   ├── 02_compute_api_volatility.py     # V(r) via export-declaration extraction
│   ├── 03_fetch_propagation_signals.py  # N(t) via GitHub Search API
│   ├── 03b_fetch_ci_signals.py          # D(t) via Dependabot PR rejection + CI failures
│   ├── 03c_fetch_sweep_signals.py       # Propagation signals for sweep candidates
│   ├── 04_fit_sir_model.py              # SIR model fitting and R₀ estimation
│   ├── 05_compute_crs_validation.py     # CRS computation, AUC-ROC, validation figures
│   ├── 06_collect_nonbreaking_controls.py # Non-breaking release collection
│   ├── 07_npm_deprecation_sweep.py      # Automated dataset expansion
│   ├── 08_fetch_pypi_metadata.py        # PyPI seed metadata
│   ├── 09_pypi_ast_volatility.py        # PyPI V(r) via AST diffing
│   ├── 10_fetch_pypi_signals.py         # PyPI GitHub propagation signals
│   ├── 11_merge_pypi_crs.py             # Cross-ecosystem combined dataset
│   ├── 12_sweep_api_volatility.py       # V(r) for npm sweep candidates
│   ├── 13_fetch_pubdev_metadata.py      # pub.dev seed metadata
│   ├── 14_pubdev_dart_volatility.py     # pub.dev V(r) via Dart API diffing
│   ├── 15_fetch_pubdev_signals.py       # pub.dev GitHub propagation signals
│   └── 16_network_sir.py               # SIR-on-dependency-network (HMF + Monte Carlo)
├── figures/
│   ├── sir_propagation_curves_v2.png    # Figure 1: SIR propagation curves
│   ├── crs_validation_v2.png            # Figure 2: CRS validation dashboard (AUC=0.853)
│   ├── cross_ecosystem.png              # Figure 3: Cross-ecosystem R₀ comparison
│   └── network_sir.png                  # Figure 4: Network SIR propagation
├── packages/
│   ├── depcast-check/                   # Publisher gate CLI (npm: depcast-check@1.0.0)
│   ├── depcast-consumer/                # Renovate consumer gate (composite action)
│   └── depcast-aggregator/              # Telemetry aggregator (FastAPI + SQLite)
├── docs/
│   ├── publisher-gate-spec.md           # Publisher gate integration spec
│   ├── renovate-plugin-spec.md          # Renovate plugin specification
│   └── renovate-integration.md          # Renovate integration guide
├── paper/
│   └── depcast_arxiv.tex               # LaTeX paper (IEEE format, 7 pages)
├── examples/
│   ├── crs_top20.csv                   # Top 20 breaking releases by CRS
│   ├── cross_ecosystem_summary.csv     # Cross-ecosystem comparison table
│   └── sir_r0_all_ecosystems.csv       # SIR fit results across all ecosystems
└── requirements.txt                    # Pinned Python dependencies
```

---

## Replication

**Python version:** 3.10+ required.

### Requirements

```bash
pip install -r requirements.txt
```

### Environment

Copy `.env.sample` to `.env` and set your GitHub token:

```bash
cp .env.sample .env
# edit .env: GITHUB_TOKEN=your_token_here
```

Generate a token at [github.com/settings/tokens](https://github.com/settings/tokens) with `public_repo` scope.

### Pipeline

```bash
# Fix npm metadata gaps
python scripts/00_fix_npm_metadata.py

# npm breaking releases + signals
python scripts/01_collect_breaking_releases.py
python scripts/02_compute_api_volatility.py
python scripts/03_fetch_propagation_signals.py
python scripts/03b_fetch_ci_signals.py
python scripts/04_fit_sir_model.py

# Non-breaking controls + AUC-ROC
python scripts/06_collect_nonbreaking_controls.py
python scripts/05_compute_crs_validation.py

# Dataset expansion (npm deprecation sweep)
python scripts/07_npm_deprecation_sweep.py --seed-file data/top_npm_seed.txt
python scripts/03c_fetch_sweep_signals.py
python scripts/12_sweep_api_volatility.py

# PyPI ecosystem
python scripts/08_fetch_pypi_metadata.py
python scripts/09_pypi_ast_volatility.py
python scripts/10_fetch_pypi_signals.py

# pub.dev ecosystem
python scripts/13_fetch_pubdev_metadata.py
python scripts/14_pubdev_dart_volatility.py
python scripts/15_fetch_pubdev_signals.py

# Cross-ecosystem merge + network SIR
python scripts/11_merge_pypi_crs.py
python scripts/16_network_sir.py
```

**Expected outputs per script:**

| Script | Output | Runtime |
|--------|--------|---------|
| 00 | `breaking_releases.csv` patched | ~3 min |
| 01 | `breaking_releases.csv` | ~2 min |
| 02 | `api_volatility.csv` | ~5 min |
| 03 | `propagation_signals.csv` | ~20 min |
| 03b | `ci_signals.csv` | ~30 min |
| 03c | `sweep_propagation_signals.csv` | ~2 h |
| 04 | `sir_model_results.csv` + Figure 1 | ~1 min |
| 05 | `crs_scores.csv` + Figure 2 | ~1 min |
| 06 | `nonbreaking_releases.csv` | ~8 min |
| 07 | `deprecation_sweep_candidates.csv` | ~5 min |
| 08 | `pypi_breaking_releases.csv` | ~3 min |
| 09 | `pypi_api_volatility.csv` | ~10 min |
| 10 | `pypi_propagation_signals.csv` | ~30 min |
| 11 | `combined_crs_scores.csv` + Figure 3 | ~1 min |
| 12 | `api_volatility.csv` extended | ~25 min |
| 13 | `pubdev_breaking_releases.csv` | ~3 min |
| 14 | `pubdev_api_volatility.csv` | ~10 min |
| 15 | `pubdev_propagation_signals.csv` | ~30 min |
| 16 | `network_sir.png` | ~5 min |

---

## GitHub Actions Integration

```yaml
- name: DepCast compatibility risk check
  run: |
    npx depcast-check \
      --package ${{ env.PACKAGE_NAME }} \
      --version ${{ env.PACKAGE_VERSION }} \
      --threshold 0.60 \
      --fail-on avoid \
      --github-token ${{ secrets.GITHUB_TOKEN }}
```

Exit code `1` blocks the publish. Exit code `0` lets it through. Full options in [`packages/depcast-check/README.md`](packages/depcast-check/README.md).

---

## Research Status

| Phase | Task | Status | Target Venue |
|-------|------|--------|--------------|
| 1 | Empirical study: 51 breaking npm releases; SIR model; CRS scoring | **Done** | — |
| 2 | Dataset expansion (346 releases); non-breaking controls; AUC=0.853 | **Done** | — |
| 3 | Cross-ecosystem: PyPI (25) + pub.dev (25); network SIR; 396 total | **Done** | — |
| 4 | Publisher gate CLI: `depcast-check@1.0.0` on npm | **Done** | — |
| 5 | Renovate consumer gate + telemetry aggregator; PR to renovatebot/renovate | **Done (PR open)** | — |
| 6 | Paper submission to MSR 2027 | **In progress** | MSR 2027 (~Oct 2026) |
| — | EMSE 2027 (requires live D(t) from 500+ opt-in repos) | Planned | EMSE 2027 |

**Renovate PR:** [renovatebot/renovate#43563](https://github.com/renovatebot/renovate/pull/43563) — adding DepCast to the community tools list.

---

## Citation

```bibtex
@techreport{farag2026depcast,
  title  = {DepCast: A Two-Sided Compatibility Intelligence Protocol
            for Software Package Ecosystems},
  author = {Farag, Abdelrahman},
  year   = {2026},
  note   = {Zenodo. https://doi.org/10.5281/zenodo.20361569},
  url    = {https://github.com/ahafarag/depcast}
}
```

---

## Author

**Abdelrahman Farag**
AWS Cloud & DevOps Engineer — Sopra Steria
MSc Candidate, AI Research — Universidad Internacional Menéndez Pelayo (UIMP)
Financial Engineering Program — WorldQuant University

[![LinkedIn](https://img.shields.io/badge/LinkedIn-connect-0A66C2?logo=linkedin&logoColor=white)](https://www.linkedin.com/in/abdelrahman-farag-b1471221b/)
[![GitHub](https://img.shields.io/badge/GitHub-ahafarag-181717?logo=github&logoColor=white)](https://github.com/ahafarag)

---

## Support This Research

<div align="center">

If DepCast saved you debugging time or is useful for your research, consider supporting the ongoing work.

<br>

<a href="https://ko-fi.com/ahafarag" target="_blank">
  <img src="https://cdn.ko-fi.com/cdn/kofi3.png?v=3" alt="Support on Ko-fi" height="60" width="240">
</a>

<br><br>

*Funds go toward compute time, API costs, and keeping the research moving.*

</div>

---

## License

Data and scripts: [MIT License](LICENSE)
Paper: © Abdelrahman Farag, 2026. All rights reserved.
