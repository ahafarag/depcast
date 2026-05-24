"""
Thin GitHub REST API client — stdlib only (urllib.request).
Covers the operations needed by the DepCast consumer gate.
"""

import json
import urllib.request
import urllib.error

_API = "https://api.github.com"


def _req(method: str, path: str, token: str, body=None) -> dict | list | None:
    url = f"{_API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "depcast-consumer/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read()
        raise RuntimeError(f"GitHub API {method} {path} → HTTP {e.code}: {raw.decode()[:300]}")


def post_comment(owner: str, repo: str, pr_number: int, body: str, token: str) -> dict:
    return _req("POST", f"/repos/{owner}/{repo}/issues/{pr_number}/comments", token, {"body": body})


def add_labels(owner: str, repo: str, pr_number: int, labels: list[str], token: str) -> list:
    return _req("POST", f"/repos/{owner}/{repo}/issues/{pr_number}/labels", token, {"labels": labels})


def remove_label(owner: str, repo: str, pr_number: int, label: str, token: str) -> None:
    import urllib.parse
    encoded = urllib.parse.quote(label, safe="")
    try:
        _req("DELETE", f"/repos/{owner}/{repo}/issues/{pr_number}/labels/{encoded}", token)
    except RuntimeError as e:
        if "HTTP 404" in str(e):
            pass  # label wasn't present
        else:
            raise


def ensure_label_exists(owner: str, repo: str, label: str, color: str, token: str) -> None:
    """Create the label if it doesn't already exist."""
    try:
        _req("POST", f"/repos/{owner}/{repo}/labels", token,
             {"name": label, "color": color, "description": f"DepCast: {label}"})
    except RuntimeError as e:
        if "HTTP 422" in str(e):
            pass  # already exists
        else:
            raise


def request_changes(owner: str, repo: str, pr_number: int, body: str, token: str) -> dict:
    return _req("POST", f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews", token,
                {"body": body, "event": "REQUEST_CHANGES"})


def approve_pr(owner: str, repo: str, pr_number: int, body: str, token: str) -> dict:
    return _req("POST", f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews", token,
                {"body": body, "event": "APPROVE"})


def get_pr(owner: str, repo: str, pr_number: int, token: str) -> dict:
    return _req("GET", f"/repos/{owner}/{repo}/pulls/{pr_number}", token)


def enable_auto_merge(owner: str, repo: str, pr_number: int, token: str) -> None:
    """Re-enable auto-merge after a deferred AVOID is downgraded (best-effort)."""
    pr = get_pr(owner, repo, pr_number, token)
    if pr.get("auto_merge"):
        return  # already enabled
    # GraphQL enablePullRequestAutoMerge is needed; skip for now (best-effort)
