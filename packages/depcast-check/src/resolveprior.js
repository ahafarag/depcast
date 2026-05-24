"use strict";
/**
 * Resolve the prior stable version for a package.
 *
 * Given a breaking version X.Y.Z, finds the highest version in the
 * registry that is strictly less than X.Y.Z and is not a pre-release.
 */

const { fetchRegistryMeta } = require("./exposure");

/**
 * Parse a semver string into [major, minor, patch] integers.
 * Returns null for pre-releases or unparseable strings.
 */
function parseSemver(v) {
  if (v.includes("-")) return null; // pre-release
  const m = v.match(/^(\d+)\.(\d+)\.(\d+)$/);
  if (!m) return null;
  return [parseInt(m[1], 10), parseInt(m[2], 10), parseInt(m[3], 10)];
}

/** Compare two semver tuples. Returns -1, 0, or 1. */
function cmpTuple(a, b) {
  for (let i = 0; i < 3; i++) {
    if (a[i] < b[i]) return -1;
    if (a[i] > b[i]) return 1;
  }
  return 0;
}

/**
 * @param {string} pkg       Package name
 * @param {string} version   Breaking version (e.g. "5.0.0")
 * @returns {Promise<string>} Prior stable version string
 */
async function resolvePriorVersion(pkg, version) {
  const targetParsed = parseSemver(version);
  if (!targetParsed) {
    throw new Error(`Cannot parse breaking version: ${version}`);
  }

  const meta = await fetchRegistryMeta(pkg);
  const versions = Object.keys(meta.versions || {});

  const candidates = versions.filter((v) => {
    const parsed = parseSemver(v);
    return parsed !== null && cmpTuple(parsed, targetParsed) < 0;
  });

  if (candidates.length === 0) {
    throw new Error(`No prior stable version found for ${pkg}@${version}`);
  }

  candidates.sort((a, b) => {
    const pa = parseSemver(a);
    const pb = parseSemver(b);
    return cmpTuple(pb, pa); // descending
  });
  return candidates[0];
}

module.exports = { resolvePriorVersion };
