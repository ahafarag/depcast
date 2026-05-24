# DepCast Publisher Gate — Specification

A pre-publish CI check that computes the Compatibility Risk Score (CRS)
for a package release and blocks high-risk publishes before they reach
the registry.

---

## GitHub Actions Integration

```yaml
# .github/workflows/publish.yml
name: Publish

on:
  push:
    tags: ['v*']

jobs:
  depcast-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: DepCast compatibility risk check
        uses: depcast/check@v1
        with:
          package-manager: npm          # npm | pypi | pubdev
          threshold: 0.60              # block if CRS >= threshold (AVOID zone)
          fail-on: avoid               # avoid | wait | never
          github-token: ${{ secrets.GITHUB_TOKEN }}

      - name: Publish to npm
        run: npm publish
        env:
          NODE_AUTH_TOKEN: ${{ secrets.NPM_TOKEN }}
```

The `depcast/check@v1` step runs before publish and exits non-zero if
CRS exceeds the configured threshold, blocking the publish step.

---

## Action Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `package-manager` | No | `npm` | Target registry: `npm`, `pypi`, or `pubdev` |
| `threshold` | No | `0.60` | CRS threshold above which the gate fails |
| `fail-on` | No | `avoid` | Rating level that triggers failure: `avoid`, `wait`, or `never` |
| `github-token` | Yes | — | Token for GitHub Search API (propagation signal) |
| `prior-version` | No | auto | Prior stable version to diff against; auto-detected if omitted |
| `allow-override` | No | `false` | If `true`, gate warns but never blocks (audit mode) |

## Action Outputs

| Output | Description |
|--------|-------------|
| `crs` | Computed CRS score [0, 1] |
| `rating` | SAFE / WAIT / AVOID |
| `v_r` | API volatility score |
| `e_r` | Downstream exposure score |
| `d_t` | Observed failure rate |
| `h_m` | Maintainer history score |
| `pattern` | A / B / C (pattern classification) |

---

## Local CLI Usage

```bash
# Install
npm install -g depcast-check

# Run before publish
depcast-check --package chalk --version 5.0.0 --prior 4.1.2

# Output:
# DepCast CRS Check
# ─────────────────────────────────────
# Package:  chalk@5.0.0  (prior: 4.1.2)
# V(r):     0.412   API volatility
# E(r):     0.931   Downstream exposure  (189M weekly downloads)
# D(t):     0.000   Observed failure rate (no signal yet)
# H(m):     0.041   Maintainer history
# ─────────────────────────────────────
# CRS:      0.346   WAIT
# ─────────────────────────────────────
# Recommendation: Monitor issues for 24h before broad adoption.

# Exit codes:
#   0 = SAFE or WAIT (below threshold)
#   1 = AVOID (CRS >= threshold)
#   2 = error (missing token, package not found, etc.)
```

---

## Threshold Guidance

| CRS Range | Rating | Recommended Action |
|-----------|--------|--------------------|
| 0.00–0.25 | SAFE   | Publish freely |
| 0.25–0.60 | WAIT   | Publish; monitor issues for 24–48h |
| 0.60–1.00 | AVOID  | Hold; review breaking changes; consider major bump |

The default threshold (`0.60`) blocks only AVOID-rated releases.
Set `fail-on: wait` for stricter gating (blocks WAIT and AVOID).

---

## CRS Computation at Publish Time

At the moment of running `depcast-check`, not all four signals are
available with equal confidence:

| Signal | Availability at publish time | Source |
|--------|------------------------------|--------|
| V(r)   | Immediate — computed from tarball diff | local |
| E(r)   | Immediate — from registry download stats | registry API |
| D(t)   | Delayed — requires downstream CI runs | GitHub Search API |
| H(m)   | Immediate — from prior release R₀ history | DepCast aggregator |

Because D(t) is near-zero at publish time (no downstream failures yet),
the gate relies primarily on V(r) and E(r) as pre-emptive signals.
D(t) becomes meaningful within 1–6 hours post-publish and is used by
the **consumer gate** (Renovate plugin) rather than the publisher gate.

---

## Integration with Renovate (Consumer Gate)

```json
// renovate.json
{
  "extends": ["config:base"],
  "depcast": true,
  "depcastThreshold": 0.60
}
```

With the Renovate plugin enabled, upgrade PRs for AVOID-rated releases
are automatically deferred for 48h and labelled `depcast:wait`.
