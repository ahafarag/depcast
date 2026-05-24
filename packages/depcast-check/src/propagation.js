"use strict";
/**
 * D(t) — Observed Failure Rate
 *
 * Queries the GitHub Search API for issues mentioning the package and
 * version, created within a configurable window after the publish date.
 * Returns a normalised score.
 *
 * Note: D(t) is near-zero at publish time (no downstream failures yet).
 * It becomes meaningful within 1–6 hours post-publish.  The publisher gate
 * primarily relies on V(r) and E(r); D(t) is included for completeness.
 *
 * Normalisation ceiling: 50 issues within the window (top of our training set).
 */

const https = require("https");

const ISSUE_CEILING = 50;

function httpsGet(url, token) {
  return new Promise((resolve, reject) => {
    const headers = { "User-Agent": "depcast-check/1.0" };
    if (token) headers["Authorization"] = `token ${token}`;
    https
      .get(url, { headers }, (res) => {
        if (res.statusCode === 301 || res.statusCode === 302) {
          return httpsGet(res.headers.location, token).then(resolve).catch(reject);
        }
        const chunks = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () => {
          try {
            resolve({ status: res.statusCode, body: JSON.parse(Buffer.concat(chunks).toString()) });
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
 * Count GitHub issues mentioning a package@version within windowHours of
 * the given publishedAt ISO string.
 *
 * @param {string} pkg           Package name
 * @param {string} version       New version string
 * @param {string} publishedAt   ISO 8601 publish timestamp (or null for "now")
 * @param {string} githubToken   GitHub personal access token
 * @param {number} windowHours   Look-ahead window in hours (default 24)
 */
async function countIssues(pkg, version, publishedAt, githubToken, windowHours = 24) {
  const from = publishedAt ? new Date(publishedAt) : new Date();
  const to = new Date(from.getTime() + windowHours * 3600 * 1000);

  const pkgName = pkg.replace(/^@[^/]+\//, ""); // strip scope for query
  const query = encodeURIComponent(
    `"${pkgName}" "${version}" is:issue created:${from.toISOString().slice(0, 10)}..${to.toISOString().slice(0, 10)}`
  );
  const url = `https://api.github.com/search/issues?q=${query}&per_page=1`;

  const { status, body } = await httpsGet(url, githubToken);
  if (status !== 200) return 0;
  return body.total_count || 0;
}

/**
 * Compute D(t) for a package release.
 *
 * @param {string} pkg
 * @param {string} version
 * @param {string|null} publishedAt   ISO 8601 or null
 * @param {string} githubToken
 * @returns {{ D_t: number, issues_24h: number }}
 */
async function computePropagation(pkg, version, publishedAt, githubToken) {
  const issues_24h = await countIssues(pkg, version, publishedAt, githubToken, 24);
  const D_t = Math.min(issues_24h / ISSUE_CEILING, 1);
  return { D_t, issues_24h };
}

module.exports = { computePropagation };
