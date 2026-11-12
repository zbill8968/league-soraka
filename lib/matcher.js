/**
 * Match a card icon crop against the offline icon index.
 *
 * The index is produced by scripts/build-icon-index.mjs from the OP.GG icon set
 * (data/icons/*.png) and ships as data/icon-index.json.
 */

import { describeRGBA, descriptorDistance } from './hash.js'

/**
 * @typedef {object} IconIndex
 * @property {number} size   descriptor size marker (0 = normalized already)
 * @property {{id: number, v: number[]}[]} items
 */

/**
 * Build a descriptor for an RGBA crop.
 * @param {Uint8ClampedArray|Uint8Array} rgba
 * @param {number} width
 * @param {number} height
 */
export function describeCrop(rgba, width, height) {
  return describeRGBA(rgba, width, height)
}

/**
 * Rank the index by distance to a descriptor.
 * @param {ReturnType<typeof describeCrop>} desc
 * @param {IconIndex} index
 * @returns {{id: number, distance: number}[]}
 */
export function rank(desc, index) {
  return index.items
    .map((item) => ({ id: item.id, distance: descriptorDistance(desc, item) }))
    .sort((a, b) => a.distance - b.distance)
}

/** Distance above which a crop is treated as "not in the index". */
export const MAX_DISTANCE = 0.03
/** Two candidates closer than this are indistinguishable (shared artwork). */
export const AMBIGUITY_MARGIN = 0.002

/**
 * Pick the best match and report how trustworthy it is.
 * @param {ReturnType<typeof describeCrop>} desc
 * @param {IconIndex} index
 * @param {{maxDistance?: number}} [options]
 * @returns {{id: number|null, distance: number, confidence: number, ambiguous: boolean, runnerUp: number|null}}
 */
export function best(desc, index, options = {}) {
  const maxDistance = options.maxDistance ?? MAX_DISTANCE
  if (!index?.items?.length) {
    return { id: null, distance: 1, confidence: 0, ambiguous: false, runnerUp: null }
  }
  const sorted = rank(desc, index)
  const first = sorted[0]
  const second = sorted[1]
  const margin = second ? second.distance - first.distance : 1
  const confidence = Math.max(0, Math.min(1, (1 - first.distance / maxDistance) * 0.75 + Math.min(1, margin / 0.01) * 0.25))
  const ambiguous = Boolean(second) && margin < AMBIGUITY_MARGIN && first.distance < maxDistance
  return {
    id: first.distance <= maxDistance && !ambiguous ? first.id : null,
    distance: first.distance,
    confidence,
    ambiguous,
    runnerUp: second ? second.id : null,
  }
}
