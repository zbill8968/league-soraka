/**
 * Perceptual descriptor for augment icons.
 *
 * The in-game card and the OP.GG PNG show the same artwork, but the game draws it
 * on a dark panel at a different scale while OP.GG ships transparent padding. So the
 * descriptor is built on the artwork's own bounding box and compares two things:
 *
 *   - coverage: how much of each 16x16 cell is artwork (silhouette)
 *   - colour:   the average colour of the artwork inside that cell
 *
 * That combination survives background, padding and scale differences far better
 * than a plain luma correlation.
 */

export const GRID = 16
const ART_ALPHA = 0.15
const ART_LUMA = 60

/**
 * @typedef {object} Descriptor
 * @property {Float32Array} cov   16*16 coverage, 0..1
 * @property {Float32Array} rgb   16*16*3 average artwork colour
 */

/** Bounding box of the artwork (alpha or brightness above the card background). */
function artBox(rgba, width, height) {
  let minX = width
  let minY = height
  let maxX = -1
  let maxY = -1
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const p = (y * width + x) * 4
      const a = rgba[p + 3] / 255
      const luma = 0.299 * rgba[p] + 0.587 * rgba[p + 1] + 0.114 * rgba[p + 2]
      if (a > ART_ALPHA || luma > ART_LUMA) {
        if (x < minX) minX = x
        if (x > maxX) maxX = x
        if (y < minY) minY = y
        if (y > maxY) maxY = y
      }
    }
  }
  if (maxX < minX || maxY < minY) return { x: 0, y: 0, w: width, h: height }
  const pad = Math.round(Math.max(maxX - minX, maxY - minY) * 0.05)
  const x0 = Math.max(0, minX - pad)
  const y0 = Math.max(0, minY - pad)
  const x1 = Math.min(width, maxX + 1 + pad)
  const y1 = Math.min(height, maxY + 1 + pad)
  return { x: x0, y: y0, w: Math.max(4, x1 - x0), h: Math.max(4, y1 - y0) }
}

/**
 * @param {Uint8ClampedArray|Uint8Array} rgba
 * @param {number} width
 * @param {number} height
 * @returns {Descriptor}
 */
export function describeRGBA(rgba, width, height) {
  if (!width || !height || rgba.length < width * height * 4) {
    throw new Error(`describeRGBA: bad input ${width}x${height} (${rgba.length} bytes)`)
  }
  const box = artBox(rgba, width, height)
  const n = GRID
  const cov = new Float32Array(n * n)
  const rgb = new Float32Array(n * n * 3)
  const total = new Uint32Array(n * n)
  const art = new Uint32Array(n * n)

  for (let y = box.y; y < box.y + box.h; y++) {
    const gy = Math.min(n - 1, Math.floor(((y - box.y) * n) / box.h))
    for (let x = box.x; x < box.x + box.w; x++) {
      const gx = Math.min(n - 1, Math.floor(((x - box.x) * n) / box.w))
      const cell = gy * n + gx
      total[cell]++
      const p = (y * width + x) * 4
      const a = rgba[p + 3] / 255
      const luma = 0.299 * rgba[p] + 0.587 * rgba[p + 1] + 0.114 * rgba[p + 2]
      if (a <= ART_ALPHA && luma <= ART_LUMA) continue
      art[cell]++
      rgb[cell * 3] += rgba[p]
      rgb[cell * 3 + 1] += rgba[p + 1]
      rgb[cell * 3 + 2] += rgba[p + 2]
    }
  }

  for (let i = 0; i < n * n; i++) {
    cov[i] = total[i] ? art[i] / total[i] : 0
    const count = art[i] || 1
    rgb[i * 3] /= count
    rgb[i * 3 + 1] /= count
    rgb[i * 3 + 2] /= count
  }
  return { cov, rgb }
}

/**
 * Distance between two descriptors, 0 (identical) .. 1 (unrelated).
 * @param {Descriptor} a
 * @param {Descriptor} b
 * @returns {number}
 */
export function descriptorDistance(a, b) {
  if (!a?.cov || !b?.cov || a.cov.length !== b.cov.length) return 1
  let covDiff = 0
  let colourDiff = 0
  let colourWeight = 0
  for (let i = 0; i < a.cov.length; i++) {
    covDiff += Math.abs(a.cov[i] - b.cov[i])
    const both = Math.min(a.cov[i], b.cov[i])
    if (both > 0.05) {
      colourWeight += both
      for (let c = 0; c < 3; c++) {
        const k = i * 3 + c
        colourDiff += Math.abs(a.rgb[k] - b.rgb[k]) * both
      }
    }
  }
  const covDistance = covDiff / a.cov.length
  const colourDistance = colourWeight > 0 ? colourDiff / (colourWeight * 3 * 255) : 0
  return 0.55 * covDistance + 0.45 * colourDistance
}
