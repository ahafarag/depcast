"use strict";
/**
 * E(r) — Downstream Exposure
 *
 * Uses npm registry weekly download counts as a proxy for the number of
 * downstream consumers.  Raw downloads are normalised against a reference
 * ceiling (the highest download figure in the DepCast training set).
 *
 * Reference ceiling: semver@7 at ~718M weekly downloads.
 */

const https = require("https");

// Normalisation ceiling (718M = semver@7, the highest in our dataset).
const DOWNLOAD_CEILING = 718_000_000;

function httpsGet(url) {
  return new Promise((resolve, reject) => {
    https
      .get(url, { headers: { "User-Agent": "depcast-check/1.0" } }, (res) => {
        if (res.statusCode === 301 || res.statusCode === 302) {
          return httpsGet(res.headers.location).then(resolve).catch(reject);
        }
        const chunks = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () => {
          try {
            resolve(JSON.parse(Buffer.concat(chunks).toString()));
          } catch (e) {
            reject(e);
          }
        });
        res.on("error", reject);
      })
      .on("error", reject);
  });
}

/**
 * Fetch weekly download count for an npm package.
 * Returns the last-week download figure.
 */
async function fetchWeeklyDownloads(pkg) {
  const encoded = pkg.startsWith("@")
    ? `@${encodeURIComponent(pkg.slice(1))}`
    : encodeURIComponent(pkg);
  const url = `https://api.npmjs.org/downloads/point/last-week/${encoded}`;
  const data = await httpsGet(url);
  return data.downloads || 0;
}

/**
 * Fetch registry metadata for a package (used to resolve prior version).
 */
async function fetchRegistryMeta(pkg) {
  const encoded = pkg.startsWith("@")
    ? `@${encodeURIComponent(pkg.slice(1))}`
    : encodeURIComponent(pkg);
  const url = `https://registry.npmjs.org/${encoded}`;
  return httpsGet(url);
}

/**
 * Compute E(r) for an npm package.
 *
 * @param {string} pkg  Package name
 * @returns {{ E_r: number, weekly_downloads: number }}
 */
async function computeExposure(pkg) {
  const downloads = await fetchWeeklyDownloads(pkg);
  const E_r = Math.min(downloads / DOWNLOAD_CEILING, 1);
  return { E_r, weekly_downloads: downloads };
}

module.exports = { computeExposure, fetchRegistryMeta };
