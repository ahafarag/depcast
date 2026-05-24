"""
DepCast Consumer Gate — Phase 5, Task 5.2

Runs inside a GitHub Actions workflow triggered on Renovate PRs.
Reads context from environment variables set by the workflow, then:

  1. Parses the Renovate PR title for (package, from_version, to_version)
  2. Runs depcast-check to compute the CRS score
  3. Posts a comment with the full CRS report
  4. Applies a depcast label (depcast:safe / depcast:wait / depcast:avoid)
  5. For AVOID: requests changes (blocks merge)
  6. For WAIT:  posts an advisory comment only (does not block)
  7. On PR close/merge: emits an anonymized signal to the aggregator

Environment variables (set by action.yml):
  GITHUB_TOKEN          required  — token for API calls
  DEPCAST_EVENT         required  — "opened" | "reopened" | "closed"
  DEPCAST_PR_TITLE      required  — PR title string
  DEPCAST_PR_NUMBER     required  — PR number (integer)
  DEPCAST_REPO          required  — "owner/repo"
  DEPCAST_PR_MERGED     optional  — "true" | "false"
  DEPCAST_THRESHOLD     optional  — CRS threshold (default 0.60)
  DEPCAST_FAIL_ON       optional  — "avoid" | "wait" | "never" (default "avoid")
  DEPCAST_AGGREGATOR    optional  — aggregator URL (empty = no signal)
  DEPCAST_CHECKS_TOTAL  optional  — total CI checks on the PR
  DEPCAST_CHECKS_FAILED optional  — failed CI checks on the PR
  DEPCAST_NODE_PATH     optional  — path to depcast-check src/ dir
  GITHUB_OUTPUT         required  — file for setting action outputs
"""

import json
import os
import subprocess
import sys

# Add src/ to path for sibling imports
sys.path.insert(0, os.path.dirname(__file__))
from parse_pr import parse_renovate_title
from github_api import (
    post_comment, add_labels, remove_label,
    ensure_label_exists, request_changes,
)
from emit_signal import emit_signal


# ── Label definitions ─────────────────────────────────────────────────────────
LABELS = {
    "SAFE":  ("depcast:safe",  "0e8a16"),   # green
    "WAIT":  ("depcast:wait",  "e4b429"),   # yellow
    "AVOID": ("depcast:avoid", "b60205"),   # red
}
ALL_DEPCAST_LABELS = [v[0] for v in LABELS.values()]


# ── Comment templates ─────────────────────────────────────────────────────────
def _bar(value, width=20):
    filled = round(value * width)
    return "[" + "#" * filled + "." * (width - filled) + "]"


def build_comment(pkg, version, prior, result, threshold, event):
    rating = result["rating"]
    crs = result["CRS"]

    # Rating badge
    badge = {"SAFE": "🟢 SAFE", "WAIT": "🟡 WAIT", "AVOID": "🔴 AVOID"}[rating]

    dl = result.get("weekly_downloads", 0)
    dl_str = f"{dl/1_000_000:.0f}M" if dl >= 1_000_000 else f"{dl/1_000:.0f}K"

    lines = [
        f"## DepCast Compatibility Risk Check",
        f"",
        f"| | |",
        f"|---|---|",
        f"| **Package** | `{pkg}@{version}` (prior: `{prior}`) |",
        f"| **CRS** | `{crs:.3f}` — {badge} |",
        f"| **Pattern** | {result.get('pattern','?')} |",
        f"",
        f"### Signal Breakdown",
        f"",
        f"| Signal | Score | Bar | Notes |",
        f"|--------|-------|-----|-------|",
        f"| V(r) API volatility    | `{result['V_r']:.3f}` | `{_bar(result['V_r'])}` | pattern {result.get('pattern','?')} — {result.get('n_removed_symbols',0)}/{result.get('n_prior_symbols',0)} symbols removed |",
        f"| E(r) Exposure          | `{result['E_r']:.3f}` | `{_bar(result['E_r'])}` | {dl_str} weekly downloads |",
        f"| D(t) Observed failures | `{result['D_t']:.3f}` | `{_bar(result['D_t'])}` | {result.get('issues_24h',0)} GitHub issues / 24h |",
        f"| H(m) Maintainer history| `{result['H_m']:.3f}` | `{_bar(result['H_m'])}` | R₀ = {result.get('R0',0):.3f} |",
        f"",
    ]

    if rating == "SAFE":
        lines += [
            f"✅ **No action required.** This upgrade looks safe. Proceed with merge.",
        ]
    elif rating == "WAIT":
        lines += [
            f"⚠️ **Advisory.** CRS is in the WAIT zone (`{crs:.3f}` < `{threshold:.2f}`).  ",
            f"This PR will not be blocked, but consider monitoring the upstream issue tracker ",
            f"for 24–48 h before merging into production.",
        ]
    else:  # AVOID
        lines += [
            f"🚫 **Merge blocked.** CRS `{crs:.3f}` ≥ threshold `{threshold:.2f}`.  ",
            f"Review the breaking changes for `{pkg}@{version}` before merging.  ",
            f"Re-run this check after the score drops below the threshold.",
        ]

    lines += [
        f"",
        f"<sub>Powered by [DepCast](https://github.com/ahafarag/depcast) · "
        f"[Threshold: {threshold:.2f}]</sub>",
    ]

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    # Read environment
    token = os.environ["GITHUB_TOKEN"]
    event = os.environ.get("DEPCAST_EVENT", "opened")
    pr_title = os.environ.get("DEPCAST_PR_TITLE", "")
    pr_number = int(os.environ["DEPCAST_PR_NUMBER"])
    repo = os.environ["DEPCAST_REPO"]              # "owner/repo"
    pr_merged = os.environ.get("DEPCAST_PR_MERGED", "false").lower() == "true"
    threshold = float(os.environ.get("DEPCAST_THRESHOLD", "0.60"))
    fail_on = os.environ.get("DEPCAST_FAIL_ON", "avoid").lower()
    aggregator_url = os.environ.get("DEPCAST_AGGREGATOR", "")
    checks_total = int(os.environ.get("DEPCAST_CHECKS_TOTAL", "0"))
    checks_failed = int(os.environ.get("DEPCAST_CHECKS_FAILED", "0"))
    node_path = os.environ.get("DEPCAST_NODE_PATH", "")
    output_file = os.environ.get("GITHUB_OUTPUT", "")

    owner, repo_name = repo.split("/", 1)

    # ── Parse PR title ────────────────────────────────────────────────────────
    parsed = parse_renovate_title(pr_title)
    if not parsed:
        print(f"[depcast] PR title does not match Renovate pattern: {pr_title!r}")
        print("[depcast] Skipping check.")
        _set_output(output_file, "skipped", "true")
        return 0

    pkg = parsed["package"]
    to_version = parsed["to_version"]
    from_version = parsed.get("from_version")

    print(f"[depcast] Package: {pkg}  to: {to_version}  from: {from_version or 'auto'}")

    # ── On PR close/merge: emit signal then exit ──────────────────────────────
    if event == "closed":
        if not from_version:
            print("[depcast] No from_version available for signal; skipping.")
            return 0
        outcome = "merged" if pr_merged else "closed_manual"
        result = emit_signal(
            aggregator_url, pkg, from_version, to_version,
            outcome, checks_total, checks_failed, repo,
        )
        print(f"[depcast] Signal emitted: {outcome}  aggregator response: {result}")
        return 0

    # ── Run depcast-check ─────────────────────────────────────────────────────
    cli_path = os.path.join(node_path, "cli.js") if node_path else "depcast-check"
    cmd = ["node", cli_path, "--package", pkg, "--version", to_version, "--json"]
    if from_version:
        cmd += ["--prior", from_version]
    if token:
        cmd += ["--github-token", token]

    print(f"[depcast] Running: {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        output_str = proc.stdout.strip()
        if not output_str:
            raise RuntimeError(f"depcast-check produced no output. stderr: {proc.stderr[:300]}")
        crs_result = json.loads(output_str)
    except subprocess.TimeoutExpired:
        print("[depcast] depcast-check timed out after 120s. Skipping.")
        return 0
    except Exception as e:
        print(f"[depcast] Error running depcast-check: {e}")
        return 1

    prior = crs_result.get("prior", from_version or "?")
    rating = crs_result["rating"]
    crs = crs_result["CRS"]

    print(f"[depcast] CRS={crs:.3f}  rating={rating}  pattern={crs_result.get('pattern','?')}")

    # ── Ensure labels exist in the repo ──────────────────────────────────────
    for label, color in LABELS.values():
        ensure_label_exists(owner, repo_name, label, color, token)

    # ── Remove old depcast labels, apply current one ───────────────────────
    for old_label in ALL_DEPCAST_LABELS:
        remove_label(owner, repo_name, pr_number, old_label, token)

    current_label, _ = LABELS[rating]
    add_labels(owner, repo_name, pr_number, [current_label], token)
    print(f"[depcast] Applied label: {current_label}")

    # ── Post comment ─────────────────────────────────────────────────────────
    comment_body = build_comment(pkg, to_version, prior, crs_result, threshold, event)
    post_comment(owner, repo_name, pr_number, comment_body, token)
    print("[depcast] Comment posted.")

    # ── Block / advise ────────────────────────────────────────────────────────
    should_block = (
        fail_on != "never"
        and (
            crs >= threshold
            or (fail_on == "wait" and rating in ("WAIT", "AVOID"))
        )
    )

    if should_block:
        review_body = (
            f"DepCast CRS `{crs:.3f}` ≥ threshold `{threshold:.2f}` — "
            f"merge blocked until the risk score drops or the threshold is overridden."
        )
        request_changes(owner, repo_name, pr_number, review_body, token)
        print(f"[depcast] Requested changes (blocking merge).")

    # ── Write outputs ─────────────────────────────────────────────────────────
    _set_output(output_file, "crs", f"{crs:.4f}")
    _set_output(output_file, "rating", rating)
    _set_output(output_file, "blocked", str(should_block).lower())
    _set_output(output_file, "skipped", "false")

    # Exit non-zero when blocking
    if should_block:
        print(f"[depcast] Exiting with code 1 (merge blocked).")
        return 1

    return 0


def _set_output(output_file: str, key: str, value: str) -> None:
    if output_file:
        with open(output_file, "a") as f:
            f.write(f"{key}={value}\n")
    else:
        print(f"[depcast] OUTPUT: {key}={value}")


if __name__ == "__main__":
    sys.exit(main())
