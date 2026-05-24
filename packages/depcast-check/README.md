# depcast-check

Pre-publish compatibility risk check using the DepCast CRS model.

Computes the Compatibility Risk Score (CRS) for an npm package release and
exits non-zero if the score exceeds the configured threshold — blocking a
risky publish before it reaches the registry.

## Install

```bash
npm install -g depcast-check
```

## CLI Usage

```bash
# Check chalk@5.0.0 against prior 4.1.2
depcast-check --package chalk --version 5.0.0 --prior 4.1.2

# Auto-detect prior version
depcast-check --package glob --version 9.0.0

# With GitHub token (enables D(t) propagation signal)
depcast-check --package moment --version 2.0.0 --github-token $GITHUB_TOKEN

# JSON output (for CI scripts)
depcast-check --package chalk --version 5.0.0 --json

# Stricter gate: block WAIT and AVOID
depcast-check --package react --version 19.0.0 --fail-on wait
```

## Output

```
DepCast CRS Check
-----------------------------------------------
Package:  chalk@5.0.0  (prior: 4.1.2)
-----------------------------------------------
V(r):  0.000  [....................]  API volatility       pattern C
E(r):  0.611  [############........]  Downstream exposure  (439M weekly downloads)
D(t):  0.000  [....................]  Observed failures    (0 issues/24h)
H(m):  0.030  [#...................]  Maintainer history   (R0=1.162)
-----------------------------------------------
CRS:   0.186   SAFE
-----------------------------------------------
Recommendation: Release looks safe. Proceed with publish.
```

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | SAFE or WAIT (below threshold) |
| `1` | AVOID (CRS >= threshold) |
| `2` | Error (bad args, network error, package not found) |

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--package` / `-p` | — | Package name (required) |
| `--version` / `-v` | — | New version to check (required) |
| `--prior` | auto | Prior stable version; auto-detected if omitted |
| `--threshold` | `0.60` | CRS threshold above which the gate fails |
| `--fail-on` | `avoid` | `avoid` \| `wait` \| `never` |
| `--allow-override` | `false` | Warn but never block (audit mode) |
| `--github-token` | `$GITHUB_TOKEN` | Token for GitHub propagation signal |
| `--json` | `false` | Output raw JSON instead of formatted report |

## GitHub Actions

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

## CRS Signals

| Signal | Description | Available at publish |
|--------|-------------|----------------------|
| V(r) | API volatility — fraction of prior exported symbols removed | Immediate |
| E(r) | Downstream exposure — normalised weekly downloads | Immediate |
| D(t) | Observed failure rate — GitHub issues / 24h post-publish | Delayed (1–6h) |
| H(m) | Maintainer history — R₀ from SIR propagation model | Immediate |

## Threshold Guide

| CRS | Rating | Action |
|-----|--------|--------|
| 0.00–0.25 | SAFE | Publish freely |
| 0.25–0.60 | WAIT | Publish; monitor issues 24–48h |
| 0.60–1.00 | AVOID | Hold; review breaking changes |
