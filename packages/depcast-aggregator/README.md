# depcast-aggregator

Privacy-preserving aggregator for Renovate upgrade outcome signals.
Provides the live D(t) score that feeds into the DepCast CRS formula.

## Run locally

```bash
pip install -r requirements.txt
python -m uvicorn main:app --reload --port 8765
```

Interactive docs: http://localhost:8765/docs

## Endpoints

### `POST /v1/signal`
Receive an anonymized Renovate PR outcome event.

```bash
curl -X POST http://localhost:8765/v1/signal \
  -H "Content-Type: application/json" \
  -d '{
    "package":        "chalk",
    "from":           "4.1.2",
    "to":             "5.0.0",
    "outcome":        "ci_failed",
    "checks_total":   8,
    "checks_failed":  3,
    "repo_hash":      "<sha256_hex_of_org_slash_repo>",
    "ts":             1714000000
  }'
```

`outcome` must be one of: `ci_failed` | `merged` | `closed_manual`

### `GET /v1/crs`
Return the live D(t) score for a package@version.

```bash
curl "http://localhost:8765/v1/crs?package=chalk&version=5.0.0"
# → {"package":"chalk","version":"5.0.0","D_t":0.3333,"n_total":3,"n_failed":1,...}
```

### `GET /v1/hash`
Compute `SHA-256(org/repo)` — convenience endpoint for clients without a crypto library.

```bash
curl "http://localhost:8765/v1/hash?repo=myorg/myrepo"
# → {"repo":"myorg/myrepo","repo_hash":"<64-char hex>"}
```

### `GET /v1/health`
Liveness probe.

## Privacy model

- `repo_hash` is `SHA-256(org/repo)` — only the hash is stored, never the raw identity
- Only aggregate counts per `(package, version)` are exposed via `/v1/crs`
- Individual signal rows are stored for audit but never surfaced via the API
- Opt-in is explicit: repos must add `"depcast": true` to `renovate.json`

## Storage

SQLite file at `data/aggregator.db` (created automatically on first run).
For production, replace with PostgreSQL by changing the `sqlite3` calls to
`asyncpg` with the same schema.

## Deploy

Any ASGI host works: Fly.io, Railway, Vercel (via `vercel.json`), or a VPS.

```bash
# Fly.io
fly launch
fly deploy

# Railway
railway up
```
