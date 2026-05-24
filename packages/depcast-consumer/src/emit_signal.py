"""
POST an anonymized signal to the DepCast aggregator endpoint.

repo_hash = SHA-256(org/repo) — raw repo identity never leaves this process.
"""

import hashlib
import json
import time
import urllib.request
import urllib.error


def emit_signal(
    aggregator_url: str,
    package: str,
    from_version: str,
    to_version: str,
    outcome: str,          # "ci_failed" | "merged" | "closed_manual"
    checks_total: int,
    checks_failed: int,
    repo: str,             # "org/repo" — hashed before sending
) -> dict | None:
    """
    Send outcome signal to aggregator.  Returns the aggregator response dict,
    or None if aggregator_url is empty (opt-out / not configured).
    """
    if not aggregator_url:
        return None

    repo_hash = hashlib.sha256(repo.encode()).hexdigest()
    payload = {
        "package": package,
        "from": from_version,
        "to": to_version,
        "outcome": outcome,
        "checks_total": checks_total,
        "checks_failed": checks_failed,
        "repo_hash": repo_hash,
        "ts": int(time.time()),
    }

    url = aggregator_url.rstrip("/") + "/v1/signal"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "depcast-consumer/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        # Aggregator errors must never block the CI run
        print(f"[depcast] warning: could not reach aggregator: {e}")
        return None
