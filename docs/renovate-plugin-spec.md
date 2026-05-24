# DepCast Renovate Plugin — Specification (Phase 5, Planned)

The highest-leverage live-signal source for DepCast is a **Renovate plugin** that
emits anonymized upgrade-outcome events to a DepCast aggregator endpoint.  Each
event records whether a bot-opened upgrade PR passed CI, was merged, or was
manually closed — providing real-time D(t) signal at ecosystem scale.

---

## Plugin Contract (Proposed)

```json
POST https://api.depcast.io/v1/signal
{
  "package":        "chalk",
  "from":           "4.1.2",
  "to":             "5.0.0",
  "outcome":        "ci_failed" | "merged" | "closed_manual",
  "checks_total":   12,
  "checks_failed":  3,
  "repo_hash":      "<sha256 of org/repo — never stored raw>",
  "ts":             1714000000
}
```

## Privacy Model

- `repo_hash` is a one-way SHA-256 hash of `org/repo`.  Raw repository
  identities are never stored or surfaced.
- Only aggregate counts per `(package, to_version)` are exposed via the
  query API.
- Opt-in is explicit: repos must add `"depcast": true` to `renovate.json`.

## Aggregator Query API (Proposed)

```
GET https://api.depcast.io/v1/crs?package=chalk&version=5.0.0
→ { "D_t": 0.38, "sample_size": 142, "last_updated": "2026-05-01T12:00:00Z" }
```

`D_t` is the fraction of upgrade PRs that failed CI within 72 h of the
release, normalized to [0, 1].  It feeds directly into the CRS formula:

```
CRS(t) = w1·V(r) + w2·E(r) + w3·D(t) + w4·H(m)
```

## Implementation Roadmap

| Step | Description | Status |
|------|-------------|--------|
| 5.1 | Finalize this spec and move to `docs/` | Done |
| 5.2 | Implement Renovate plugin — POST to aggregator on PR close | Planned |
| 5.3 | Build aggregator endpoint (FastAPI or serverless) | Planned |
| 5.4 | Privacy audit: confirm no raw repo identities stored | Planned |
| 5.5 | Publish plugin to npm + submit PR to Renovate OSS repo | Planned |
| 5.6 | Reach 500 opt-in repos (minimum for statistically significant D(t)) | Planned |

## Minimum Viable D(t)

500 opt-in repositories is the minimum for a statistically meaningful
aggregate failure rate per release.  Below this threshold, a single large
monorepo can skew D(t).  The 500-repo milestone is the key gate before
an ICSE industry-track submission.
