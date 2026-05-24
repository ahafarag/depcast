"use strict";
/**
 * V(r) — API Volatility
 *
 * Computes the fraction of the prior version's exported symbols that are
 * absent from the new version.  Works for npm (JS/TS tarballs).
 *
 * V(r) = removed_symbols / prior_symbols   (clamped to [0, 1])
 *
 * Implementation: fetches tarballs from the npm registry, extracts via
 * Node.js built-in zlib + manual tar block parsing (no native deps).
 */

"use strict";

const https = require("https");
const zlib = require("zlib");
const path = require("path");

const JS_EXTENSIONS = new Set([".js", ".mjs", ".cjs", ".ts", ".d.ts"]);

// Patterns that identify exported declarations in JS/TS source files.
const EXPORT_PATTERNS = [
  /^export\s+(?:default\s+)?(?:async\s+)?function[\s*]+(\w+)/m,
  /^export\s+(?:const|let|var)\s+(\w+)/m,
  /^export\s+(?:abstract\s+)?class\s+(\w+)/m,
  /^export\s+interface\s+(\w+)/m,
  /^export\s+type\s+(\w+)\s*[=<{]/m,
  /^export\s+enum\s+(\w+)/m,
  /^module\.exports\s*=\s*\{([^}]+)\}/m,
  /^exports\.(\w+)\s*=/m,
];

/**
 * Fetch a URL, following redirects, returning a Buffer.
 */
function fetchBuffer(url) {
  return new Promise((resolve, reject) => {
    https
      .get(url, { headers: { "User-Agent": "depcast-check/1.0" } }, (res) => {
        if (res.statusCode === 301 || res.statusCode === 302) {
          return fetchBuffer(res.headers.location).then(resolve).catch(reject);
        }
        if (res.statusCode !== 200) {
          return reject(new Error(`HTTP ${res.statusCode} for ${url}`));
        }
        const chunks = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () => resolve(Buffer.concat(chunks)));
        res.on("error", reject);
      })
      .on("error", reject);
  });
}

/**
 * Parse a .tar.gz buffer (already gunzipped or raw gzip) and extract
 * file contents for files matching the given extension filter.
 *
 * @param {Buffer} gzipBuf  Raw .tar.gz buffer
 * @param {Set<string>} exts  File extensions to include
 * @returns {Promise<Map<string, string>>}  path -> content
 */
function parseTarGz(gzipBuf, exts) {
  return new Promise((resolve, reject) => {
    const files = new Map();
    let buf = Buffer.alloc(0);

    const gunzip = zlib.createGunzip();
    gunzip.on("error", reject);

    gunzip.on("data", (chunk) => {
      buf = Buffer.concat([buf, chunk]);
    });

    gunzip.on("end", () => {
      let offset = 0;
      while (offset + 512 <= buf.length) {
        // Read tar header (512 bytes)
        const header = buf.slice(offset, offset + 512);

        // Check for end-of-archive (two consecutive zero-filled blocks)
        const isZero = header.every((b) => b === 0);
        if (isZero) break;

        // Parse name (null-terminated, 100 bytes)
        const nameRaw = header.slice(0, 100).toString("utf8").replace(/\0.*$/, "");
        // Parse size (octal, 12 bytes at offset 124)
        const sizeOct = header.slice(124, 136).toString("utf8").replace(/\0.*$/, "").trim();
        const size = parseInt(sizeOct, 8) || 0;
        // Type flag (offset 156): '0' or '\0' = regular file
        const typeFlag = header[156];

        offset += 512; // advance past header

        const isRegularFile = typeFlag === 0x30 || typeFlag === 0; // '0' or NUL
        if (isRegularFile && size > 0) {
          const ext = path.extname(nameRaw).toLowerCase();
          if (exts.has(ext)) {
            const content = buf.slice(offset, offset + size).toString("utf8");
            files.set(nameRaw, content);
          }
        }

        // Advance past file data (padded to 512-byte boundary)
        const paddedSize = Math.ceil(size / 512) * 512;
        offset += paddedSize;
      }
      resolve(files);
    });

    const { Readable } = require("stream");
    const readable = new Readable();
    readable.push(gzipBuf);
    readable.push(null);
    readable.pipe(gunzip);
  });
}

/**
 * Extract all exported symbol names from a single source file's text.
 */
function extractSymbols(source) {
  const symbols = new Set();
  const lines = source.split("\n");

  for (const line of lines) {
    const trimmed = line.trimStart();
    for (const pat of EXPORT_PATTERNS) {
      const m = trimmed.match(pat);
      if (m) {
        if (pat.source.includes("module\\.exports")) {
          m[1].split(",").forEach((n) => {
            const name = n.trim().split(":")[0].trim();
            if (name) symbols.add(name);
          });
        } else if (m[1]) {
          symbols.add(m[1]);
        }
        break;
      }
    }
  }
  return symbols;
}

/**
 * Extract all exported symbols from an npm tarball buffer.
 */
async function symbolsFromTarball(gzipBuf) {
  const files = await parseTarGz(gzipBuf, JS_EXTENSIONS);
  const symbols = new Set();
  for (const content of files.values()) {
    for (const sym of extractSymbols(content)) symbols.add(sym);
  }
  return symbols;
}

/**
 * Compute V(r) for an npm package release.
 *
 * @param {string} pkg       Package name (e.g. "chalk")
 * @param {string} version   New (breaking) version (e.g. "5.0.0")
 * @param {string} prior     Prior stable version (e.g. "4.1.2")
 * @returns {{ V_r: number, n_prior: number, n_removed: number }}
 */
async function computeVolatility(pkg, version, prior) {
  const barePkg = pkg.replace(/^@[^/]+\//, "");
  const tarballUrl = (v) =>
    `https://registry.npmjs.org/${pkg}/-/${barePkg}-${v}.tgz`;

  const [bufNew, bufPrior] = await Promise.all([
    fetchBuffer(tarballUrl(version)),
    fetchBuffer(tarballUrl(prior)),
  ]);

  const [symNew, symPrior] = await Promise.all([
    symbolsFromTarball(bufNew),
    symbolsFromTarball(bufPrior),
  ]);

  const removed = [...symPrior].filter((s) => !symNew.has(s));
  const n_prior = symPrior.size;
  const n_removed = removed.length;
  const V_r = n_prior > 0 ? n_removed / n_prior : 0;

  return { V_r: Math.min(V_r, 1), n_prior, n_removed };
}

module.exports = { computeVolatility };
