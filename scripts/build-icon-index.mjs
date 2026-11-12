#!/usr/bin/env node
/**
 * Build data/icon-index.json from data/icons/*.png.
 * Run after scripts/fetch-icons.mjs (and whenever the icon set or hash code changes).
 */

import { createHash } from 'node:crypto'
import { readFile, readdir, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { PNG } from 'pngjs'
import { describeRGBA } from '../lib/hash.js'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const DATA = join(ROOT, 'data')
const ICONS = join(DATA, 'icons')

async function main() {
  const files = (await readdir(ICONS)).filter((f) => f.toLowerCase().endsWith('.png'))
  const items = []
  const duplicates = new Map()

  for (const file of files) {
    const id = Number(file.replace(/\.png$/i, ''))
    if (!Number.isFinite(id)) continue
    const png = PNG.sync.read(await readFile(join(ICONS, file)))
    // describeRGBA composites transparency onto the same dark panel the game uses.
    const desc = describeRGBA(png.data, png.width, png.height)
    items.push({ id, cov: Array.from(desc.cov), rgb: Array.from(desc.rgb) })
    const key = Array.from(desc.cov).join(',')
    if (!duplicates.has(key)) duplicates.set(key, [])
    duplicates.get(key).push(id)
  }

  items.sort((a, b) => a.id - b.id)
  const out = {
    generatedAt: new Date().toISOString(),
    source: 'data/icons/*.png',
    size: 0,
    items,
  }
  await writeFile(join(DATA, 'icon-index.json'), JSON.stringify(out), 'utf8')

  const fingerprint = createHash('sha256').update(JSON.stringify(items)).digest('hex').slice(0, 16)
  const shared = [...duplicates.values()].filter((ids) => ids.length > 1)
  console.log(`[icon-index] ${items.length} icons, fingerprint ${fingerprint}, ${shared.length} shared-art groups`)
  for (const ids of shared.slice(0, 5)) console.log(`  shared: ${ids.join(',')}`)
}

main().catch((error) => {
  console.error('[icon-index] fatal:', error)
  process.exit(1)
})
