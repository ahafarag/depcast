# depcast-consumer

DepCast Consumer Gate — GitHub Actions composite action for Renovate upgrade PRs.

Runs the DepCast CRS check on every Renovate-opened upgrade PR, labels it,
blocks AVOID-rated merges, and emits anonymized outcome signals to the
DepCast aggregator to build up live D(t) data.

## Setup

### 1. Opt in via `renovate.json`

```json
{
  "extends": ["config:base"],
  "depcast": true,
  "depcastThreshold": 0.60
}
```

### 2. Add the workflow

Copy [`consumer-workflow-template.yml`](consumer-workflow-template.yml)
to `.github/workflows/depcast-consumer.yml` in your repo.

```yaml
name: DepCast Consumer Gate

on:
  pull_request:
    types: [opened, reopened, synchronize, closed]

jobs:
  depcast:
    if: github.actor == 'renovate[bot]' || github.actor == 'renovate'
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
      contents: read

    steps:
      - name: DepCast compatibility risk check
        uses: ahafarag/depcast/packages/depcast-consumer@main
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          threshold: "0.60"
          fail-on: "avoid"
          # aggregator-url: "https://api.depcast.io"
```

That's it. For every Renovate PR the action will:
1. Parse the PR title to extract package name and version
2. Run `depcast-check` (V_r, E_r, D_t, H_m → CRS)
3. Apply a label: `depcast:safe` / `depcast:wait` / `depcast:avoid`
4. Post a comment with the full signal breakdown
5. Request changes (block merge) for AVOID-rated upgrades
6. On PR close/merge: emit an anonymized signal to the aggregator

## What a check comment looks like

> ## DepCast Compatibility Risk Check
>
> | | |
> |---|---|
> | **Package** | `chalk@5.0.0` (prior: `4.1.2`) |
> | **CRS** | `0.186` — 🟢 SAFE |
> | **Pattern** | C |
>
> ### Signal Breakdown
>
> | Signal | Score | Bar | Notes |
> |--------|-------|-----|-------|
> | V(r) API volatility     | `0.000` | `[....................]` | pattern C — 0/0 symbols removed |
> | E(r) Exposure           | `0.611` | `[############........]` | 439M weekly downloads |
> | D(t) Observed failures  | `0.000` | `[....................]` | 0 GitHub issues / 24h |
> | H(m) Maintainer history | `0.030` | `[#...................]` | R₀ = 1.162 |
>
> ✅ **No action required.** This upgrade looks safe. Proceed with merge.

## Inputs

| Input | Default | Description |
|-------|---------|-------------|
| `github-token` | — | `secrets.GITHUB_TOKEN` (required) |
| `threshold` | `0.60` | CRS threshold; block if CRS ≥ threshold |
| `fail-on` | `avoid` | `avoid` \| `wait` \| `never` |
| `aggregator-url` | `""` | DepCast aggregator URL; empty = no signal |
| `depcast-check-path` | `""` | Path to depcast-check `src/` (auto-detected) |

## Outputs

| Output | Description |
|--------|-------------|
| `crs` | Computed CRS score |
| `rating` | `SAFE` \| `WAIT` \| `AVOID` |
| `blocked` | `true` if merge was blocked |
| `skipped` | `true` if PR was not a Renovate upgrade PR |

## Privacy

When `aggregator-url` is set, the action emits one signal per PR close:

```json
{
  "package": "chalk",
  "from": "4.1.2",
  "to": "5.0.0",
  "outcome": "merged",
  "checks_total": 8,
  "checks_failed": 0,
  "repo_hash": "<SHA-256 of org/repo — never the raw name>",
  "ts": 1714000000
}
```

Raw repository identities are **never** stored or transmitted.
