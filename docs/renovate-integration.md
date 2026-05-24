# DepCast + Renovate Integration Guide

DepCast adds two companion layers on top of Renovate:

| Layer | Who installs it | What it does |
|-------|----------------|--------------|
| **Consumer gate** | Repo owners using Renovate | Labels and optionally blocks AVOID-rated upgrade PRs opened by Renovate |
| **Renovate preset** | Repo owners | Conservative base policy — holds major bumps 3 days, automerges minor/patch |

---

## Consumer Gate (recommended)

Copies one workflow file to your repo. For every Renovate-opened upgrade PR it:

1. Computes the DepCast CRS score (V(r), E(r), D(t), H(m))
2. Labels the PR `depcast:safe` / `depcast:wait` / `depcast:avoid`
3. Posts a full signal breakdown as a PR comment
4. Requests changes (blocks merge) for AVOID-rated releases (CRS ≥ 0.60)
5. On close/merge: emits an anonymized signal to the DepCast aggregator

### Setup

**Step 1** — copy the workflow:

```bash
# from your repo root
mkdir -p .github/workflows
curl -sSL https://raw.githubusercontent.com/ahafarag/depcast/main/packages/depcast-consumer/consumer-workflow-template.yml \
  -o .github/workflows/depcast-consumer.yml
```

**Step 2** — no `renovate.json` change required for labelling and blocking.
To also emit anonymized outcome signals (opt-in):

```json
{
  "extends": ["config:base"],
  "depcast": true
}
```

### What a PR comment looks like

> ## DepCast Compatibility Risk Check
>
> | | |
> |---|---|
> | **Package** | `chalk@5.0.0` (prior: `4.1.2`) |
> | **CRS** | `0.186` — 🟢 SAFE |
> | **Pattern** | C |
>
> | Signal | Score | Bar | Notes |
> |--------|-------|-----|-------|
> | V(r) API volatility | `0.000` | `[....................]` | pattern C |
> | E(r) Exposure | `0.611` | `[############........]` | 439M weekly downloads |
> | D(t) Observed failures | `0.000` | `[....................]` | 0 issues / 24h |
> | H(m) Maintainer history | `0.030` | `[#...................]` | R₀ = 1.162 |
>
> ✅ **No action required.** This upgrade looks safe.

---

## Renovate Preset

Extends Renovate's default policy: holds npm major bumps 3 days before
they appear in PRs, automerges minor/patch after 1 day.

```json
{
  "extends": [
    "config:base",
    "github>ahafarag/depcast//presets/default"
  ]
}
```

---

## Publisher Gate (for package authors)

If you *publish* npm packages, add the publisher gate to block high-risk
releases before they reach the registry:

```bash
# Copy to .github/workflows/publish.yml
curl -sSL https://raw.githubusercontent.com/ahafarag/depcast/main/packages/depcast-check/publisher-gate-workflow.yml \
  -o .github/workflows/publish.yml
```

Runs `npx depcast-check@latest` on every version tag push. Exits non-zero
(blocking publish) if CRS ≥ 0.60 (AVOID zone).

---

## CRS Signal Reference

| Signal | Available at publish time | Source |
|--------|--------------------------|--------|
| V(r) — API volatility | Immediate | npm tarball diff |
| E(r) — Downstream exposure | Immediate | npm downloads API |
| D(t) — Observed failure rate | Delayed 1–6 h | GitHub Search API |
| H(m) — Maintainer history | Immediate | DepCast SIR model |

**Threshold guide:**

| CRS | Rating | Renovate action |
|-----|--------|-----------------|
| 0.00–0.25 | SAFE | Auto-label `depcast:safe` |
| 0.25–0.60 | WAIT | Label `depcast:wait`; post comment |
| 0.60–1.00 | AVOID | Label `depcast:avoid`; block merge |

---

## Links

- GitHub: <https://github.com/ahafarag/depcast>
- CLI: `npm install -g depcast-check`
- Preprint: <https://doi.org/10.5281/zenodo.20360606>
