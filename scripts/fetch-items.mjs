#!/usr/bin/env node
/**
 * Fetch Data Dragon item crit chances into data/items.json.
 *
 * Only items with a non-zero FlatCritChanceMod are kept; the tag engine needs
 * the final-build crit total for the "数学奇才" tag.
 *
 * Usage: node scripts/fetch-items.mjs
 */

import { writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const OUT = join(ROOT, 'data', 'items.json')

const versions = await (await fetch('https://ddragon.leagueoflegends.com/api/versions.json')).json()
const version = versions[0]
const payload = await (await fetch(`https://ddragon.leagueoflegends.com/cdn/${version}/data/zh_CN/item.json`)).json()
const items = {}
for (const [id, item] of Object.entries(payload.data ?? {})) {
  const crit = item?.stats?.FlatCritChanceMod
  if (typeof crit === 'number' && crit > 0) {
    items[id] = { name: item.name, crit }
  }
}
await writeFile(OUT, JSON.stringify({ version, source: 'ddragon', items }, null, 1), 'utf8')
console.log(`[items] ${Object.keys(items).length} crit items from DDragon ${version}`)
