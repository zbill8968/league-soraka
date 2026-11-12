/**
 * Shared augmentation classifier.
 *
 * Loads catalog / icon index / stats once, then answers per-offer requests. The
 * CLI (classify.mjs) and the long-lived daemon (classify-daemon.mjs) both use it so
 * the panel does not pay Node startup + a 2.5 MB JSON parse on every hotkey.
 */

import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { PNG } from 'pngjs'

import { describeRGBA } from '../lib/hash.js'
import { best } from '../lib/matcher.js'
import { buildChampionModel, normalizeChampionName, rarityFromCode, scoreAugment } from '../lib/score.js'
import { POLICY_PRESETS, recommend } from '../lib/policy.js'

function normText(text) {
  return String(text ?? '')
    .toLowerCase()
    .replace(/[\s\u3000·・:：,，.。!！?？'"“”‘’()（）\[\]【】\-_/\\]/g, '')
}

function levenshtein(a, b) {
  const m = a.length
  const n = b.length
  if (!m) return n
  if (!n) return m
  let prev = Array.from({ length: n + 1 }, (_, i) => i)
  for (let i = 1; i <= m; i++) {
    const cur = [i]
    for (let j = 1; j <= n; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
    }
    prev = cur
  }
  return prev[n]
}

function matchByName(text, augments) {
  const needle = normText(text)
  if (!needle) return null
  let bestMatch = null
  for (const augment of augments) {
    for (const name of [augment.nameZh, augment.name]) {
      const hay = normText(name)
      if (!hay) continue
      if (hay === needle) return { id: augment.id, ratio: 1 }
      const ratio = 1 - levenshtein(needle, hay) / Math.max(needle.length, hay.length)
      if (ratio >= 0.62 && (!bestMatch || ratio > bestMatch.ratio)) bestMatch = { id: augment.id, ratio }
    }
  }
  return bestMatch
}

async function readJson(path, fallback) {
  try {
    return JSON.parse(await readFile(path, 'utf8'))
  } catch {
    return fallback
  }
}

/** Build a classifier bound to one data directory. */
export async function createClassifier(dataDir) {
  const catalog = await readJson(join(dataDir, 'catalog.json'), { augments: [], champions: [] })
  const index = await readJson(join(dataDir, 'icon-index.json'), { items: [] })
  const stats = await readJson(join(dataDir, 'stats.json'), { byChampion: {} })
  const byId = new Map((catalog.augments ?? []).map((a) => [a.id, a]))
  const augments = catalog.augments ?? []

  // Cross-champion averages, computed once.
  const sums = new Map()
  const counts = new Map()
  for (const table of Object.values(stats.byChampion ?? {})) {
    for (const [id, row] of Object.entries(table)) {
      const value = row?.performance
      if (typeof value !== 'number' || value <= 0) continue
      sums.set(id, (sums.get(id) ?? 0) + value)
      counts.set(id, (counts.get(id) ?? 0) + 1)
    }
  }
  const globalTable = {}
  for (const [id, sum] of sums) globalTable[id] = { performance: sum / (counts.get(id) || 1) }
  const globalModel = buildChampionModel({ __global__: globalTable }, '__global__')

  /**
   * @param {{champion?: string|null, crops?: string[], names?: string[], policy?: string}} request
   */
  return async function classify(request) {
    const warnings = []
    const champion = request.champion ?? null
    const crops = request.crops ?? []
    const names = request.names ?? []
    const slug = champion ? normalizeChampionName(champion) : null
    let model = slug ? buildChampionModel(stats.byChampion, slug) : null
    let mode = 'champion'
    if (!model || model.poolSize === 0) {
      if (slug) warnings.push(`no OP.GG data for champion "${champion}" (${slug}); using global averages`)
      model = globalModel
      mode = 'global'
      warnings.push('no champion detected; grades use cross-champion averages')
    }

    const cards = []
    for (let slot = 0; slot < 3; slot++) {
      const cropPath = crops[slot]
      const ocrName = names[slot] ?? ''
      const byName = ocrName ? matchByName(ocrName, augments) : null
      const score = (id, extra) => {
        const augment = byId.get(id)
        const scored = model && id ? scoreAugment(model, id) : { score: null, percentile: null, grade: '?' }
        return {
          slot,
          augmentId: id,
          name: augment?.nameZh || augment?.name || '未识别',
          nameEn: augment?.name ?? null,
          rarity: augment ? rarityFromCode(augment.rarity) : null,
          score: scored.score,
          percentile: scored.percentile,
          grade: scored.grade,
          ...extra,
        }
      }

      if (byName && byName.ratio >= 0.62) {
        cards.push(score(byName.id, { confidence: Number(byName.ratio.toFixed(3)), matchedBy: 'name', ocr: ocrName }))
        continue
      }
      if (!cropPath) {
        cards.push({ slot, augmentId: null, name: '未识别', rarity: null, score: null, percentile: null, grade: '?', confidence: 0, ocr: ocrName })
        continue
      }
      try {
        const png = PNG.sync.read(await readFile(cropPath))
        const match = best(describeRGBA(png.data, png.width, png.height), index)
        if (match.ambiguous) warnings.push(`slot ${slot + 1}: shared artwork (${match.id ?? '?'} vs ${match.runnerUp ?? '?'})`)
        cards.push(score(match.id, { confidence: Number(match.confidence.toFixed(3)), distance: Number(match.distance.toFixed(4)) }))
      } catch (error) {
        warnings.push(`slot ${slot + 1}: ${error?.message ?? error}`)
        cards.push({ slot, augmentId: null, name: '未识别', rarity: null, score: null, percentile: null, grade: '?', confidence: 0 })
      }
    }

    const policy = POLICY_PRESETS[request.policy] ?? POLICY_PRESETS.balanced
    return {
      champion,
      mode,
      patch: catalog.patch ?? null,
      dataAge: stats.fetchedAt ?? null,
      cards,
      recommendation: recommend({ cards, policy }),
      warnings,
    }
  }
}
