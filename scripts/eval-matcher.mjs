#!/usr/bin/env node
/**
 * Offline accuracy check for the icon matcher.
 *
 * Renders every icon in data/icons at a card-sized crop (default 120px), applies a
 * little resampling noise and a brightness shift, then asks the matcher to identify
 * it. This is an upper bound on real accuracy: it has no card frame, no HUD scaling
 * and no client colour treatment, but it catches hash-code regressions cheaply.
 *
 * Usage: node scripts/eval-matcher.mjs [--size 120] [--noise 6] [--shift 8]
 */

import { readFile, readdir } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { PNG } from 'pngjs'
import { describeRGBA } from '../lib/hash.js'
import { best } from '../lib/matcher.js'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const DATA = join(ROOT, 'data')
const ICONS = join(DATA, 'icons')

const argv = process.argv.slice(2)
const num = (name, fallback) => {
  const i = argv.indexOf(`--${name}`)
  return i >= 0 ? Number(argv[i + 1]) : fallback
}
const SIZE = num('size', 120)
const NOISE = num('noise', 6)
const SHIFT = num('shift', 8)

function resizeNearest(png, size) {
  const out = new Uint8ClampedArray(size * size * 4)
  const { width, height, data } = png
  for (let y = 0; y < size; y++) {
    const sy = Math.min(height - 1, Math.floor((y * height) / size))
    for (let x = 0; x < size; x++) {
      const sx = Math.min(width - 1, Math.floor((x * width) / size))
      const src = (sy * width + sx) * 4
      const dst = (y * size + x) * 4
      const alpha = data[src + 3] / 255
      for (let c = 0; c < 3; c++) {
        out[dst + c] = Math.round(data[src + c] * alpha + 40 * (1 - alpha))
      }
      out[dst + 3] = 255
    }
  }
  return out
}

function addNoise(rgba, amount, shift) {
  let seed = 12345
  const rnd = () => {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff
    return seed / 0x7fffffff
  }
  for (let i = 0; i < rgba.length; i += 4) {
    const delta = (rnd() - 0.5) * amount + shift
    for (let c = 0; c < 3; c++) {
      rgba[i + c] = Math.max(0, Math.min(255, rgba[i + c] + delta))
    }
  }
  return rgba
}

async function main() {
  const index = JSON.parse(await readFile(join(DATA, 'icon-index.json'), 'utf8'))
  const files = (await readdir(ICONS)).filter((f) => f.endsWith('.png'))
  let correct = 0
  let wrong = 0
  let rejected = 0
  let ambiguous = 0
  const mistakes = []

  for (const file of files) {
    const truth = Number(file.replace('.png', ''))
    const png = PNG.sync.read(await readFile(join(ICONS, file)))
    const cropped = addNoise(resizeNearest(png, SIZE), NOISE, SHIFT)
    const desc = describeRGBA(cropped, SIZE, SIZE)
    const match = best(desc, index)
    if (match.ambiguous) ambiguous++
    if (match.id === truth) correct++
    else if (match.id === null) rejected++
    else {
      wrong++
      mistakes.push({ truth, got: match.id, distance: Number(match.distance.toFixed(3)) })
    }
  }

  const total = files.length
  console.log(
    `[eval] size=${SIZE} icons=${total} top1=${correct} (${((correct / total) * 100).toFixed(1)}%) ` +
      `wrong=${wrong} rejected=${rejected} ambiguous=${ambiguous}`,
  )
  if (mistakes.length) console.log('[eval] sample mistakes:', JSON.stringify(mistakes.slice(0, 8)))
}

main().catch((error) => {
  console.error('[eval] fatal:', error)
  process.exit(1)
})
