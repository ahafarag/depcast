#!/usr/bin/env node
"use strict";
/**
 * depcast-check CLI
 *
 * Usage:
 *   depcast-check --package <name> --version <new> [--prior <old>]
 *                 [--threshold <0.60>] [--fail-on <avoid|wait|never>]
 *
 * Exit codes:
 *   0 = SAFE or WAIT (below threshold)
 *   1 = AVOID (CRS >= threshold, or rating >= fail-on level)
 *   2 = error (missing args, network error, package not found)
 */

const { check } = require("./index");

// ── Argument parsing ──────────────────────────────────────────────────────────
const args = process.argv.slice(2);

function arg(flag) {
  const i = args.indexOf(flag);
  return i !== -1 && i + 1 < args.length ? args[i + 1] : null;
}
function flag(flag) {
  return args.includes(flag);
}

const pkg = arg("--package") || arg("-p");
const version = arg("--version") || arg("-v");
const prior = arg("--prior");
const threshold = parseFloat(arg("--threshold") || "0.60");
const failOn = (arg("--fail-on") || "avoid").toLowerCase();
const allowOverride = flag("--allow-override");
const githubToken = arg("--github-token") || process.env.GITHUB_TOKEN || "";
const jsonOutput = flag("--json");

if (!pkg || !version) {
  console.error("Usage: depcast-check --package <name> --version <new> [--prior <old>]");
  console.error("       [--threshold 0.60] [--fail-on avoid|wait|never] [--allow-override]");
  console.error("       [--github-token <token>] [--json]");
  process.exit(2);
}

// ── Run check ─────────────────────────────────────────────────────────────────
(async () => {
  try {
    const result = await check({ pkg, version, prior: prior || undefined, githubToken });

    if (jsonOutput) {
      console.log(JSON.stringify(result, null, 2));
    } else {
      printReport(result);
    }

    // Determine exit code
    const shouldFail = shouldBlock(result.rating, failOn, threshold, result.CRS);
    if (shouldFail && !allowOverride) {
      process.exit(1);
    }
    process.exit(0);
  } catch (err) {
    console.error(`depcast-check error: ${err.message}`);
    process.exit(2);
  }
})();

// ── Helpers ───────────────────────────────────────────────────────────────────

function shouldBlock(rating, failOn, threshold, crs) {
  if (failOn === "never") return false;
  if (crs >= threshold) return true;
  if (failOn === "wait" && (rating === "WAIT" || rating === "AVOID")) return true;
  return false;
}

function bar(value, width = 20) {
  const filled = Math.round(value * width);
  return "[" + "#".repeat(filled) + ".".repeat(width - filled) + "]";
}

function ratingColor(rating) {
  if (rating === "SAFE") return "\x1b[32m";   // green
  if (rating === "WAIT") return "\x1b[33m";   // yellow
  return "\x1b[31m";                           // red
}
const RESET = "\x1b[0m";
const BOLD  = "\x1b[1m";

function printReport(r) {
  const line = "-".repeat(47);
  const dls = r.weekly_downloads >= 1_000_000
    ? `${(r.weekly_downloads / 1_000_000).toFixed(0)}M weekly downloads`
    : `${(r.weekly_downloads / 1_000).toFixed(0)}K weekly downloads`;

  console.log(`\n${BOLD}DepCast CRS Check${RESET}`);
  console.log(line);
  console.log(`Package:  ${BOLD}${r.pkg}@${r.version}${RESET}  (prior: ${r.prior})`);
  console.log(line);
  console.log(`V(r):  ${r.V_r.toFixed(3)}  ${bar(r.V_r)}  API volatility       pattern ${r.pattern}`);
  console.log(`E(r):  ${r.E_r.toFixed(3)}  ${bar(r.E_r)}  Downstream exposure  (${dls})`);
  console.log(`D(t):  ${r.D_t.toFixed(3)}  ${bar(r.D_t)}  Observed failures    (${r.issues_24h} issues/24h)`);
  console.log(`H(m):  ${r.H_m.toFixed(3)}  ${bar(r.H_m)}  Maintainer history   (R0=${r.R0})`);
  console.log(line);

  const col = ratingColor(r.rating);
  console.log(`CRS:   ${BOLD}${r.CRS.toFixed(3)}${RESET}   ${col}${BOLD}${r.rating}${RESET}`);
  console.log(line);
  console.log(recommendation(r.rating));
  console.log("");
}

function recommendation(rating) {
  if (rating === "SAFE") return "Recommendation: Release looks safe. Proceed with publish.";
  if (rating === "WAIT") return "Recommendation: Monitor issues for 24-48h before broad adoption.";
  return "Recommendation: HIGH RISK. Review breaking changes; consider a major version bump.";
}
