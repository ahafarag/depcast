"use strict";
/**
 * depcast-check — programmatic API
 *
 * const { check } = require('depcast-check');
 * const result = await check({ pkg: 'chalk', version: '5.0.0', githubToken: '...' });
 */

const { computeVolatility } = require("./volatility");
const { computeExposure } = require("./exposure");
const { computePropagation } = require("./propagation");
const { computeHistory } = require("./history");
const { computeCRS, classifyPattern } = require("./crs");
const { resolvePriorVersion } = require("./resolveprior");

/**
 * Run the full DepCast CRS check for an npm package release.
 *
 * @param {object} opts
 * @param {string}  opts.pkg           Package name (e.g. "chalk")
 * @param {string}  opts.version       New (breaking) version (e.g. "5.0.0")
 * @param {string} [opts.prior]        Prior stable version; auto-resolved if omitted
 * @param {string} [opts.githubToken]  GitHub token for D(t) query
 * @param {string} [opts.publishedAt]  ISO 8601 publish timestamp for D(t) window
 *
 * @returns {Promise<{
 *   pkg: string, version: string, prior: string,
 *   V_r: number, E_r: number, D_t: number, H_m: number,
 *   CRS: number, rating: string, pattern: string,
 *   weekly_downloads: number, R0: number, issues_24h: number
 * }>}
 */
async function check(opts) {
  const { pkg, version, githubToken, publishedAt } = opts;

  const prior = opts.prior || (await resolvePriorVersion(pkg, version));

  const [volatility, exposure, propagation] = await Promise.all([
    computeVolatility(pkg, version, prior),
    computeExposure(pkg),
    githubToken
      ? computePropagation(pkg, version, publishedAt || null, githubToken)
      : Promise.resolve({ D_t: 0, issues_24h: 0 }),
  ]);

  const { H_m, R0 } = computeHistory(pkg);

  const { CRS, rating } = computeCRS({
    V_r: volatility.V_r,
    E_r: exposure.E_r,
    D_t: propagation.D_t,
    H_m,
  });

  const pattern = classifyPattern(volatility.V_r);

  return {
    pkg,
    version,
    prior,
    V_r: parseFloat(volatility.V_r.toFixed(3)),
    E_r: parseFloat(exposure.E_r.toFixed(3)),
    D_t: parseFloat(propagation.D_t.toFixed(3)),
    H_m: parseFloat(H_m.toFixed(3)),
    CRS,
    rating,
    pattern,
    weekly_downloads: exposure.weekly_downloads,
    R0,
    issues_24h: propagation.issues_24h,
    n_prior_symbols: volatility.n_prior,
    n_removed_symbols: volatility.n_removed,
  };
}

module.exports = { check };
