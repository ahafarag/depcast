"use strict";
/**
 * CRS — Compatibility Risk Score
 *
 * CRS = w1*V(r) + w2*E(r) + w3*D(t) + w4*H(m)
 *
 * Learned weights from logistic regression on 346-release npm dataset:
 *   w1 (V_r) = 0.35  — API volatility is the strongest pre-emptive signal
 *   w2 (E_r) = 0.30  — downstream exposure amplifies blast radius
 *   w3 (D_t) = 0.25  — observed failures (near-zero at publish time)
 *   w4 (H_m) = 0.10  — maintainer history provides a weak prior
 *
 * Thresholds (from spec):
 *   [0.00, 0.25) → SAFE
 *   [0.25, 0.60) → WAIT
 *   [0.60, 1.00] → AVOID
 */

const WEIGHTS = { V_r: 0.35, E_r: 0.30, D_t: 0.25, H_m: 0.10 };

const THRESHOLDS = { SAFE: 0.25, AVOID: 0.60 };

/**
 * Compute CRS from the four component scores.
 * @param {{ V_r, E_r, D_t, H_m }} scores
 * @returns {{ CRS: number, rating: string }}
 */
function computeCRS({ V_r, E_r, D_t, H_m }) {
  const CRS =
    WEIGHTS.V_r * V_r +
    WEIGHTS.E_r * E_r +
    WEIGHTS.D_t * D_t +
    WEIGHTS.H_m * H_m;

  const crs = Math.min(Math.max(CRS, 0), 1);
  const rating =
    crs < THRESHOLDS.SAFE
      ? "SAFE"
      : crs < THRESHOLDS.AVOID
      ? "WAIT"
      : "AVOID";

  return { CRS: parseFloat(crs.toFixed(4)), rating };
}

/**
 * Pattern classification (A/B/C) based on V(r).
 *   A = V(r) >= 0.9   (major API surface removal)
 *   B = 0 < V(r) < 0.9
 *   C = V(r) == 0     (behavioural breaking change — no symbol removal)
 */
function classifyPattern(V_r) {
  if (V_r >= 0.9) return "A";
  if (V_r > 0) return "B";
  return "C";
}

module.exports = { computeCRS, classifyPattern, WEIGHTS };
