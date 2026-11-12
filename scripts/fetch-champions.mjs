#!/usr/bin/env node
/**
 * Build data/champions.json: OP.GG ARAM: Mayhem champion strength (tier + rank)
 * with both English and Chinese names, so the champ-select helper can match OCR
 * text from the Chinese client.
 *
 * Usage: node scripts/fetch-champions.mjs
 */

import { readFile, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { fetchHtml, decodeFlightPayload, extractArrays } from './opgg.mjs'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const OUT = join(ROOT, 'data', 'champions.json')

/**
 * `--tags-only` merges Data Dragon's class tags into the existing snapshot
 * without touching OP.GG tiers/ranks. Useful when another tool owns the rank
 * data and we only need Tank/Mage/Marksman/... for the teammate tags.
 */
async function tagsOnly() {
  const raw = JSON.parse(await readFile(OUT, 'utf8'))
  const versions = await (await fetch('https://ddragon.leagueoflegends.com/api/versions.json')).json()
  const version = versions[0]
  const data = await (await fetch(`https://ddragon.leagueoflegends.com/cdn/${version}/data/zh_CN/champion.json`)).json()
  const byId = new Map()
  for (const champ of Object.values(data.data ?? {})) {
    byId.set(Number(champ.key), { tags: champ.tags ?? [], partype: champ.partype ?? null })
  }
  let missing = 0
  for (const row of raw.champions ?? []) {
    const dd = byId.get(Number(row.id))
    row.tags = dd?.tags ?? []
    row.partype = dd?.partype ?? null
    if (!dd) missing += 1
  }
  await writeFile(OUT, JSON.stringify(raw, null, 1), 'utf8')
  console.log(`[champions] tags merged from DDragon ${version}: ${raw.champions?.length ?? 0} rows, ${missing} missing`)
}

if (process.argv.includes('--tags-only')) {
  await tagsOnly()
  process.exit(0)
}

async function championList(locale) {
  const prefix = locale ? `/${locale}` : ''
  const html = await fetchHtml(`https://op.gg${prefix}/lol/modes/aram-mayhem`)
  const payload = decodeFlightPayload(html)
  const arrays = extractArrays(payload, 'champions')
  const list = arrays.sort((a, b) => b.length - a.length)[0] ?? []
  const out = new Map()
  for (const entry of list) {
    const id = Number(entry?.champion_id ?? entry?.id ?? 0)
    const key = String(entry?.key ?? '').trim()
    if (!id || !key) continue
    out.set(id, {
      id,
      key,
      name: String(entry.name ?? key),
      tier: typeof entry.tier === 'number' ? entry.tier : null,
      rank: typeof entry.rank === 'number' ? entry.rank : null,
    })
  }
  return out
}

async function ddragon(locale) {
  const versions = await (await fetch('https://ddragon.leagueoflegends.com/api/versions.json')).json()
  const version = versions[0]
  const data = await (await fetch(`https://ddragon.leagueoflegends.com/cdn/${version}/data/${locale}/champion.json`)).json()
  const out = new Map()
  for (const champ of Object.values(data.data ?? {})) {
    out.set(Number(champ.key), {
      name: champ.name,
      title: champ.title,
      tags: champ.tags ?? [],
      partype: champ.partype ?? null,
    })
  }
  return { version, out }
}

const [en, zh, ddZh, ddEn] = await Promise.all([championList(''), championList('zh-cn'), ddragon('zh_CN'), ddragon('en_US')])
const rows = []
for (const [id, entry] of en) {
  rows.push({
    id,
    key: entry.key,
    nameEn: ddEn.out.get(id)?.name ?? entry.name,
    titleEn: ddEn.out.get(id)?.title ?? null,
    nameZh: ddZh.out.get(id)?.name ?? zh.get(id)?.name ?? null,
    titleZh: ddZh.out.get(id)?.title ?? null,
    tags: ddEn.out.get(id)?.tags ?? ddZh.out.get(id)?.tags ?? [],
    partype: ddEn.out.get(id)?.partype ?? ddZh.out.get(id)?.partype ?? null,
    tier: entry.tier,
    rank: entry.rank,
  })
}
rows.sort((a, b) => (a.rank ?? 999) - (b.rank ?? 999))

const total = rows.length
const payload = {
  patch: ddZh.version,
  source: 'op.gg/aram-mayhem champion tier list',
  generatedAt: new Date().toISOString(),
  total,
  champions: rows,
}
await writeFile(OUT, JSON.stringify(payload, null, 1), 'utf8')
console.log(`[champions] ${rows.length} champions (zh names: ${rows.filter((r) => r.nameZh).length})`)
console.log(rows.slice(0, 5).map((r) => `${r.rank}. ${r.nameZh || r.nameEn} / ${r.nameEn}`).join('\n'))
