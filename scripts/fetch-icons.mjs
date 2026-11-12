#!/usr/bin/env node
/**
 * Download the augment icon set referenced by data/catalog.json.
 * Icons are required for the offline matcher: the overlay compares in-game card
 * crops against these exact images.
 *
 * Usage: node scripts/fetch-icons.mjs [--force] [--concurrency 4]
 */

import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const DATA = join(ROOT, 'data')
const ICONS = join(DATA, 'icons')

const argv = process.argv.slice(2)
const force = argv.includes('--force')
const concIndex = argv.indexOf('--concurrency')
const concurrency = concIndex >= 0 ? Math.max(1, Number(argv[concIndex + 1])) : 4

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function download(url, dest) {
  const res = await fetch(url, { headers: { 'user-agent': 'Mozilla/5.0' } })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const buffer = Buffer.from(await res.arrayBuffer())
  if (buffer.length < 500) throw new Error(`suspiciously small icon (${buffer.length} bytes)`)
  await writeFile(dest, buffer)
  return buffer.length
}

async function main() {
  const catalog = JSON.parse(await readFile(join(DATA, 'catalog.json'), 'utf8'))
  await mkdir(ICONS, { recursive: true })
  const targets = catalog.augments.filter((a) => (a.largeIcon || a.smallIcon) && a.name)
  console.log(`[icons] ${targets.length} augments`)

  let done = 0
  let downloaded = 0
  const errors = []
  let cursor = 0

  async function worker() {
    while (cursor < targets.length) {
      const augment = targets[cursor++]
      const file = join(ICONS, `${augment.id}.png`)
      const url = augment.largeIcon ?? augment.smallIcon
      try {
        if (!force && existsSync(file)) {
          done++
          continue
        }
        await download(url, file)
        downloaded++
      } catch (error) {
        errors.push({ id: augment.id, error: String(error?.message ?? error) })
      } finally {
        done++
        if (done % 50 === 0 || done === targets.length) {
          console.log(`[icons] ${done}/${targets.length} (downloaded ${downloaded}, errors ${errors.length})`)
        }
        await sleep(40)
      }
    }
  }

  await Promise.all(Array.from({ length: concurrency }, worker))
  console.log(`[icons] done: ${downloaded} downloaded, ${errors.length} errors`)
  if (errors.length) console.log(JSON.stringify(errors.slice(0, 10), null, 1))
}

main().catch((error) => {
  console.error('[icons] fatal:', error)
  process.exit(1)
})
