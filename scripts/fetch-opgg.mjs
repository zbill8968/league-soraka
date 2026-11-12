#!/usr/bin/env node
/**
 * Build data/catalog.json + data/stats.json from OP.GG's ARAM: Mayhem pages.
 *
 * Usage:
 *   node scripts/fetch-opgg.mjs                  # all champions, resume, polite
 *   node scripts/fetch-opgg.mjs --limit 5        # first 5 champions (smoke test)
 *   node scripts/fetch-opgg.mjs --champions annie,ahri
 *   node scripts/fetch-opgg.mjs --force          # ignore the resume cache
 *   node scripts/fetch-opgg.mjs --concurrency 2 --delay 150
 */

import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { fetchChampions, fetchChampionAugments } from './opgg.mjs'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const DATA = join(ROOT, 'data')

function parseArgs(argv) {
  const out = { limit: 0, champions: [], force: false, check: false, concurrency: 2, delay: 150, dataDir: DATA }
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i]
    if (arg === '--limit') out.limit = Number(argv[++i] ?? 0)
    else if (arg === '--champions') out.champions = String(argv[++i] ?? '').split(',').filter(Boolean)
    else if (arg === '--force') out.force = true
    else if (arg === '--check') out.check = true
    else if (arg === '--concurrency') out.concurrency = Math.max(1, Number(argv[++i] ?? 2))
    else if (arg === '--delay') out.delay = Math.max(0, Number(argv[++i] ?? 150))
    else if (arg === '--data-dir') out.dataDir = resolve(String(argv[++i]))
  }
  return out
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function readJson(path, fallback) {
  try {
    return JSON.parse(await readFile(path, 'utf8'))
  } catch {
    return fallback
  }
}

async function writeJson(path, value) {
  await mkdir(dirname(path), { recursive: true })
  await writeFile(path, JSON.stringify(value, null, 1), 'utf8')
}

function isUsableAugment(entry) {
  return (
    entry &&
    typeof entry === 'object' &&
    typeof entry.id === 'number' &&
    typeof entry.name === 'string' &&
    entry.name.length > 0 &&
    typeof entry.performance === 'number'
  )
}

function normaliseAugment(entry) {
  return {
    id: entry.id,
    key: entry.key ?? null,
    name: entry.name,
    rarity: typeof entry.rarity === 'number' ? entry.rarity : null,
    desc: typeof entry.desc === 'string' ? entry.desc : '',
    largeIcon: entry.largeIcon ?? null,
    smallIcon: entry.smallIcon ?? null,
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2))
  const catalogPath = join(args.dataDir, 'catalog.json')
  const statsPath = join(args.dataDir, 'stats.json')
  const metaPath = join(args.dataDir, 'meta.json')

  console.log('[fetch-opgg] champions…')
  const { patch, champions } = await fetchChampions()
  if (args.check) {
    process.stdout.write(JSON.stringify({ patch, champions: champions.length }))
    return
  }
  console.log(`[fetch-opgg] patch ${patch}, ${champions.length} champions`)

  let targets = champions
  if (args.champions.length) {
    const wanted = new Set(args.champions.map((s) => s.toLowerCase()))
    targets = champions.filter((c) => wanted.has(c.slug.toLowerCase()))
    if (!targets.length) throw new Error(`no champion matched: ${args.champions.join(',')}`)
  }
  if (args.limit > 0) targets = targets.slice(0, args.limit)

  const catalog = await readJson(catalogPath, {})
  const stats = await readJson(statsPath, {})
  const prevMeta = await readJson(metaPath, {})
  const patchChanged = Boolean(stats.patch) && patch && stats.patch !== patch

  /** @type {Map<number, any>} */
  const augments = new Map()
  for (const item of catalog.augments ?? []) augments.set(item.id, item)

  stats.byChampion ??= {}
  const errors = []
  let done = 0
  const total = targets.length

  async function work(champion) {
    if (!args.force && !patchChanged && stats.byChampion[champion.slug]) {
      done++
      return
    }
    try {
      const { augments: table } = await fetchChampionAugments(champion.slug)
      const rows = {}
      for (const entry of table) {
        if (!isUsableAugment(entry)) continue
        rows[entry.id] = {
          performance: entry.performance,
          popular: typeof entry.popular === 'number' ? entry.popular : 0,
          tier: typeof entry.tier === 'number' ? entry.tier : null,
        }
        const current = augments.get(entry.id)
        if (!current || !current.smallIcon) augments.set(entry.id, normaliseAugment(entry))
      }
      stats.byChampion[champion.slug] = rows
    } catch (error) {
      errors.push({ slug: champion.slug, error: String(error?.message ?? error) })
      console.error(`[fetch-opgg] ${champion.slug}: ${error?.message ?? error}`)
    } finally {
      done++
      if (done % 10 === 0 || done === total) {
        console.log(`[fetch-opgg] ${done}/${total} (${errors.length} errors)`)
      }
      await sleep(args.delay)
    }
  }

  let cursor = 0
  const workers = Array.from({ length: Math.min(args.concurrency, total) }, async () => {
    while (cursor < targets.length) {
      const index = cursor++
      await work(targets[index])
    }
  })
  await Promise.all(workers)

  const outCatalog = {
    patch: patch ?? prevMeta.patch ?? null,
    source: 'op.gg',
    generatedAt: new Date().toISOString(),
    champions: champions.map((c) => ({
      slug: c.slug,
      name: c.name,
      championId: c.championId,
      tier: c.tier,
      rank: c.rank,
    })),
    augments: [...augments.values()].sort((a, b) => a.id - b.id),
  }
  const outStats = {
    patch: outCatalog.patch,
    source: 'op.gg',
    fetchedAt: new Date().toISOString(),
    byChampion: stats.byChampion,
  }

  const usedIds = new Set()
  for (const rows of Object.values(outStats.byChampion ?? {})) {
    for (const id of Object.keys(rows)) usedIds.add(Number(id))
  }
  const before = outCatalog.augments.length
  outCatalog.augments = outCatalog.augments.filter((a) => usedIds.has(a.id))
  if (outCatalog.augments.length !== before) {
    console.log(`[fetch-opgg] pruned ${before - outCatalog.augments.length} unused augments`)
  }
  await writeJson(catalogPath, outCatalog)
  await writeJson(statsPath, outStats)
  await writeJson(metaPath, {
    patch: outCatalog.patch,
    champions: Object.keys(outStats.byChampion).length,
    augments: outCatalog.augments.length,
    fetchedAt: outStats.fetchedAt,
    errors,
  })

  console.log(
    `[fetch-opgg] wrote ${catalogPath} (${outCatalog.augments.length} augments) and ${statsPath} (${Object.keys(outStats.byChampion).length} champions)`,
  )
  if (errors.length) {
    console.log(`[fetch-opgg] ${errors.length} champions failed; rerun to retry (resume is on)`)
  }
}

main().catch((error) => {
  console.error('[fetch-opgg] fatal:', error)
  process.exit(1)
})
