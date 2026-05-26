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
  return "█".repeat(filled) + "░".repeat(width - filled);
}

function ratingColor(rating) {
  if (rating === "SAFE") return "\x1b[32m";
  if (rating === "WAIT") return "\x1b[33m";
  return "\x1b[31m";
}
const RESET  = "\x1b[0m";
const BOLD   = "\x1b[1m";
const DIM    = "\x1b[2m";
const YELLOW = "\x1b[33m";
const SEP    = "  " + "─".repeat(49);

function fmtDownloads(n) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(0)}M wkly downloads`;
  if (n >= 1_000)     return `${(n / 1_000).toFixed(0)}K wkly downloads`;
  return `${n} wkly downloads`;
}

function printReport(r) {
  const col = ratingColor(r.rating);
  const isPatternC = r.pattern === "C";

  console.log("");
  console.log(`  ${BOLD}depcast-check${RESET}  ${BOLD}${r.pkg}@${r.version}${RESET}  ${DIM}(prior: ${r.prior})${RESET}`);
  console.log(SEP);
  console.log(`  V(r)  API volatility       ${r.V_r.toFixed(3)}  ${bar(r.V_r)}  ${isPatternC ? YELLOW + "Pattern C" + RESET : "Pattern " + r.pattern}`);
  console.log(`  E(r)  Downstream exposure  ${r.E_r.toFixed(3)}  ${bar(r.E_r)}  ${fmtDownloads(r.weekly_downloads)}`);
  console.log(`  D(t)  Observed failures    ${r.D_t.toFixed(3)}  ${bar(r.D_t)}  ${r.issues_24h} issues / 24h`);
  console.log(`  H(m)  Maintainer history   ${r.H_m.toFixed(3)}  ${bar(r.H_m)}  R0 = ${r.R0}`);
  console.log(SEP);

  if (isPatternC) {
    console.log(`  ${YELLOW}! Pattern C${RESET} — no symbols removed; behaviour change possible`);
    console.log(SEP);
  }

  const action = recommendation(r.rating);
  console.log(`  CRS ${BOLD}${r.CRS.toFixed(3)}${RESET}  \xb7  ${col}${BOLD}${r.rating}${RESET}  \xb7  ${action}`);
  console.log("");
}

function recommendation(rating) {
  if (rating === "SAFE") return "proceed with publish";
  if (rating === "WAIT") return "monitor issues 24-48h before adopting";
  return "hold — review breaking changes";
}
