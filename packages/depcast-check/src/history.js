"use strict";
/**
 * H(m) — Maintainer History
 *
 * Uses the R₀ value from the DepCast SIR model as a proxy for how
 * disruptive a given package's breaking changes historically have been.
 * R₀ > 1 indicates super-critical propagation (more disruptive).
 *
 * The bundled table covers the 34 packages in the DepCast training set.
 * For unknown packages the ecosystem median (1.43) is used.
 *
 * Normalisation ceiling: 38.6 (eslint outlier in training set; kept to
 * preserve the relative scale of legitimate super-critical packages).
 */

// R0 values from DepCast SIR model (npm training set, n=34 packages)
const R0_TABLE = {"lodash":1.467,"express":1.196,"react":2.382,"webpack":2.399,"typescript":1.774,"vue":1.459,"moment":1.426,"axios":1.414,"jest":1.444,"eslint":38.626,"mocha":1.477,"chalk":1.162,"node-fetch":1.032,"uuid":1.344,"glob":1.255,"rimraf":1.445,"mkdirp":1.499,"semver":1.366,"commander":1.361,"yargs":9.368,"dotenv":1.154,"mongoose":2.771,"sequelize":1.257,"typeorm":1.194,"graphql":1.265,"apollo-server":1.024,"next":3.529,"nuxt":1.541,"gatsby":1.235,"tailwindcss":1.367,"postcss":1.228,"rollup":1.222,"vite":1.688,"prettier":1.766};

// Normalisation ceiling = max R0 in training set
const R0_CEILING = Math.max(...Object.values(R0_TABLE));

// npm ecosystem median R0 (used for unknown packages)
const R0_MEDIAN = 1.435;

/**
 * Compute H(m) for a package.
 *
 * @param {string} pkg  Package name (scope stripped for lookup)
 * @returns {{ H_m: number, R0: number, source: string }}
 */
function computeHistory(pkg) {
  const bare = pkg.replace(/^@[^/]+\//, "");
  const r0 = R0_TABLE[bare] ?? R0_MEDIAN;
  const source = R0_TABLE[bare] ? "training-set" : "ecosystem-median";
  const H_m = Math.min(r0 / R0_CEILING, 1);
  return { H_m, R0: r0, source };
}

module.exports = { computeHistory };
