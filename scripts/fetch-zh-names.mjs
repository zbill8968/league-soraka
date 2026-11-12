#!/usr/bin/env node
/**
 * Add Chinese augment names to data/catalog.json.
 *
 * OP.GG serves the same augment ids under /zh-cn/..., so joining the Chinese page
 * with the English one gives a bilingual dictionary without any extra source.
 *
 * Usage: node scripts/fetch-zh-names.mjs [--champions 8]
 */

import { readFile, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { fetchHtml, decodeFlightPayload, extractArrays } from './opgg.mjs'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const DATA = join(ROOT, 'data')
const BASE = 'https://op.gg/zh-cn/lol/modes/aram-mayhem'

const args = process.argv.slice(2)
const countIndex = args.indexOf('--champions')
const championCount = countIndex >= 0 ? Number(args[countIndex + 1]) : 8

async function fetchZh(slug) {
  const html = await fetchHtml(`${BASE}/${encodeURIComponent(slug)}/augments`)
  const payload = decodeFlightPayload(html)
  const tables = extractArrays(payload, 'data')
  const table = tables
    .filter((arr) => Array.isArray(arr) && arr.some((x) => x && typeof x === 'object' && 'performance' in x))
    .sort((a, b) => b.length - a.length)[0]
  if (!table) throw new Error(`no zh table for ${slug}`)
  return table
}

async function main() {
  const catalog = JSON.parse(await readFile(join(DATA, 'catalog.json'), 'utf8'))
  const champions = (catalog.champions ?? []).slice(0, championCount)
  const zh = new Map()
  const zhDesc = new Map()
  const errors = []
  for (const champion of champions) {
    try {
      const table = await fetchZh(champion.slug)
      for (const entry of table) {
        if (entry && typeof entry.id === 'number') {
          if (typeof entry.name === 'string' && entry.name) zh.set(entry.id, entry.name)
          if (typeof entry.desc === 'string' && entry.desc) zhDesc.set(entry.id, entry.desc)
        }
      }
      console.log(`[zh] ${champion.slug}: ${zh.size} names so far`)
    } catch (error) {
      errors.push(`${champion.slug}: ${error?.message ?? error}`)
    }
    await new Promise((r) => setTimeout(r, 150))
  }
  let updated = 0
  let updatedDesc = 0
  for (const augment of catalog.augments ?? []) {
    const name = zh.get(augment.id)
    if (name) {
      augment.nameZh = name
      updated++
    }
    const desc = zhDesc.get(augment.id)
    if (desc) {
      augment.descZh = desc
      updatedDesc++
    }
  }
  catalog.zhUpdatedAt = new Date().toISOString()
  await writeFile(join(DATA, 'catalog.json'), JSON.stringify(catalog, null, 1), 'utf8')
  console.log(`[zh] wrote nameZh for ${updated}/${catalog.augments.length} augments, descZh for ${updatedDesc} (${errors.length} errors)`)
  if (errors.length) console.log(errors.slice(0, 5))
}

main().catch((error) => {
  console.error('[zh] fatal:', error)
  process.exit(1)
})
