# DepCast — Example Outputs

This folder contains representative outputs from the DepCast pipeline.
Use these to verify that your replication run produces consistent results.

---

## Files

### `crs_top20.csv`
Top 20 breaking npm releases by CRS score (equal weights).

Key numbers to verify:
- `glob@9.0.0` — highest CRS (≈0.631, AVOID): complete API surface removed + high issue rate
- `semver@7.0.0` — second highest (≈0.622, AVOID): highest E(r) in dataset (718M weekly downloads)
- `eslint@7.0.0` — Pattern C example: V(r)=0 yet D(t)=1.0 and H(m)=1.0 (R₀ outlier)
- Only 2 releases rated AVOID; most breaking releases score WAIT or SAFE

### `cross_ecosystem_summary.csv`
Cross-ecosystem comparison across npm, PyPI, and pub.dev.

Key numbers to verify:

| Ecosystem | n  | R₀ median | Pattern C | First issue (median) |
|-----------|-----|-----------|-----------|----------------------|
| npm       | 306 | 1.435     | 62.4%     | 1.0h                 |
| pypi      | 25  | 0.993     | 24.0%     | 1.1h                 |
| pubdev    | 25  | 10.304    | 72.0%     | 1.1h                 |

Notable findings:
- PyPI is **sub-critical** (R₀ < 1): propagation is self-limiting
- npm is **moderately super-critical** (R₀ > 1): self-amplifying
- pub.dev is **massively super-critical** (R₀ ≈ 10): driven by Flutter's null-safety migration forcing simultaneous ecosystem-wide updates
- All three ecosystems surface a community issue within ~1 hour of release

### `sir_r0_all_ecosystems.csv`
SIR model fit results (β, γ, R₀, r²) for all releases across all three ecosystems.

Key numbers to verify:
- npm: 40 clean fits (r² ≥ 0.5); R₀ range [0.8, 38.6]; median 1.43
- PyPI: 25 fits; R₀ range [0.8, 1.2]; median 0.99
- pub.dev: 25 fits; R₀ range [1.7, 18.9]; median 10.30

---

## How to regenerate

```bash
# Full pipeline (npm)
python scripts/05_compute_crs_validation.py   # -> data/crs_scores.csv
python scripts/04_fit_sir_model.py             # -> data/sir_model_results.csv

# Cross-ecosystem merge
python scripts/11_merge_pypi_crs.py           # -> data/combined_crs_scores.csv
```

Outputs will differ slightly if you collect fresh propagation signals
(GitHub issue counts change over time), but R₀ values and Pattern C
rates should remain within ±5% of the values above.
