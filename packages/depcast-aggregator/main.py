"""
DepCast Aggregator — Phase 5 (Task 5.3)

Two endpoints:
  POST /v1/signal  — receive anonymized Renovate PR outcome events
  GET  /v1/crs     — return live D(t) for a package@version

Privacy guarantee: repo_hash is SHA-256(org/repo); raw repo identities
are never stored.  Only aggregate counts per (package, version) are kept.

Run:
  uvicorn main:app --reload --port 8765

Deploy (any ASGI host):
  fly deploy / railway up / vercel --prod
"""

import hashlib
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH = Path(__file__).parent / "data" / "aggregator.db"

CREATE_SIGNALS = """
CREATE TABLE IF NOT EXISTS signals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    package      TEXT    NOT NULL,
    from_version TEXT    NOT NULL,
    to_version   TEXT    NOT NULL,
    outcome      TEXT    NOT NULL,
    checks_total  INTEGER,
    checks_failed INTEGER,
    repo_hash    TEXT    NOT NULL,
    ts           INTEGER NOT NULL
);
"""

CREATE_AGGREGATES = """
CREATE TABLE IF NOT EXISTS aggregates (
    package    TEXT    NOT NULL,
    version    TEXT    NOT NULL,
    n_total    INTEGER NOT NULL DEFAULT 0,
    n_failed   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (package, version)
);
"""

INDEX_PKG_VERSION = """
CREATE INDEX IF NOT EXISTS idx_signals_pkg_ver
ON signals (package, to_version);
"""


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_SIGNALS)
    conn.execute(CREATE_AGGREGATES)
    conn.execute(INDEX_PKG_VERSION)
    conn.commit()


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = get_db()
    init_db(app.state.db)
    yield
    app.state.db.close()


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="DepCast Aggregator",
    version="1.0.0",
    description="Privacy-preserving aggregator for Renovate upgrade outcome signals.",
    lifespan=lifespan,
)

# ── Models ────────────────────────────────────────────────────────────────────
Outcome = Literal["ci_failed", "merged", "closed_manual"]


class SignalRequest(BaseModel):
    package: str = Field(..., min_length=1, max_length=200)
    from_version: str = Field(..., alias="from", min_length=1, max_length=50)
    to_version: str = Field(..., alias="to", min_length=1, max_length=50)
    outcome: Outcome
    checks_total: Optional[int] = Field(None, ge=0)
    checks_failed: Optional[int] = Field(None, ge=0)
    repo_hash: str = Field(..., min_length=64, max_length=64)
    ts: Optional[int] = None

    model_config = {"populate_by_name": True}

    @field_validator("repo_hash")
    @classmethod
    def must_be_hex(cls, v: str) -> str:
        try:
            int(v, 16)
        except ValueError:
            raise ValueError("repo_hash must be a 64-char hex string (SHA-256)")
        return v.lower()


class SignalResponse(BaseModel):
    status: str = "accepted"
    package: str
    version: str
    n_total: int
    n_failed: int


class CRSResponse(BaseModel):
    package: str
    version: str
    D_t: float = Field(..., description="Observed failure rate [0, 1]")
    n_total: int = Field(..., description="Total upgrade PR outcomes received")
    n_failed: int = Field(..., description="CI-failed upgrade PRs")
    last_updated: Optional[str] = Field(None, description="ISO 8601 timestamp of last signal")


# ── Helpers ───────────────────────────────────────────────────────────────────
D_T_CEILING = 50  # normalisation ceiling (from DepCast training set)


def compute_d_t(n_failed: int, n_total: int) -> float:
    if n_total == 0:
        return 0.0
    raw = n_failed / n_total  # fraction of PRs that failed CI
    return round(min(raw, 1.0), 4)


def hash_repo(org_repo: str) -> str:
    """SHA-256 hex digest of 'org/repo' — exposed for client convenience."""
    return hashlib.sha256(org_repo.encode()).hexdigest()


# ── Routes ────────────────────────────────────────────────────────────────────
@app.post("/v1/signal", response_model=SignalResponse, status_code=202)
def receive_signal(body: SignalRequest):
    """
    Accept an anonymized Renovate PR outcome event.

    The client must pre-hash the repo identity:
      repo_hash = SHA-256(hex) of "org/repo"

    Only the hash is stored — raw repo names are never persisted.
    """
    db: sqlite3.Connection = app.state.db
    ts = body.ts or int(time.time())

    db.execute(
        """INSERT INTO signals
           (package, from_version, to_version, outcome,
            checks_total, checks_failed, repo_hash, ts)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            body.package,
            body.from_version,
            body.to_version,
            body.outcome,
            body.checks_total,
            body.checks_failed,
            body.repo_hash,
            ts,
        ),
    )

    is_failed = 1 if body.outcome == "ci_failed" else 0
    db.execute(
        """INSERT INTO aggregates (package, version, n_total, n_failed)
           VALUES (?, ?, 1, ?)
           ON CONFLICT(package, version)
           DO UPDATE SET
             n_total  = n_total  + 1,
             n_failed = n_failed + ?""",
        (body.package, body.to_version, is_failed, is_failed),
    )
    db.commit()

    row = db.execute(
        "SELECT n_total, n_failed FROM aggregates WHERE package=? AND version=?",
        (body.package, body.to_version),
    ).fetchone()

    return SignalResponse(
        package=body.package,
        version=body.to_version,
        n_total=row["n_total"],
        n_failed=row["n_failed"],
    )


@app.get("/v1/crs", response_model=CRSResponse)
def get_crs(
    package: str = Query(..., min_length=1, max_length=200),
    version: str = Query(..., min_length=1, max_length=50),
):
    """
    Return the live D(t) score for a package@version.

    D(t) = n_failed / n_total (fraction of upgrade PRs that failed CI).
    Returns 0.0 when no signals have been received yet (early post-publish).
    """
    db: sqlite3.Connection = app.state.db

    agg = db.execute(
        "SELECT n_total, n_failed FROM aggregates WHERE package=? AND version=?",
        (package, version),
    ).fetchone()

    if agg is None:
        return CRSResponse(
            package=package,
            version=version,
            D_t=0.0,
            n_total=0,
            n_failed=0,
            last_updated=None,
        )

    last_row = db.execute(
        """SELECT ts FROM signals
           WHERE package=? AND to_version=?
           ORDER BY ts DESC LIMIT 1""",
        (package, version),
    ).fetchone()

    last_updated = None
    if last_row:
        import datetime
        last_updated = datetime.datetime.utcfromtimestamp(last_row["ts"]).isoformat() + "Z"

    return CRSResponse(
        package=package,
        version=version,
        D_t=compute_d_t(agg["n_failed"], agg["n_total"]),
        n_total=agg["n_total"],
        n_failed=agg["n_failed"],
        last_updated=last_updated,
    )


@app.get("/v1/health")
def health():
    return {"status": "ok", "version": "1.0.0"}


# ── Utility: hash helper (for client use) ─────────────────────────────────────
@app.get("/v1/hash")
def get_hash(repo: str = Query(..., description="org/repo string to hash")):
    """
    Convenience endpoint: returns SHA-256(org/repo) so clients can compute
    the correct repo_hash without bundling a crypto library.

    This endpoint is read-only and does not store anything.
    """
    return {"repo": repo, "repo_hash": hash_repo(repo)}
