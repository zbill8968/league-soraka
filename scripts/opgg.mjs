/**
 * OP.GG ARAM: Mayhem data source.
 *
 * OP.GG has no public API. Its mode pages are React Server Components: the page
 * HTML carries the rendered data inside `self.__next_f.push([1,"..."])` chunks.
 * Decoding those chunks and reading the embedded JSON is enough to get the
 * champion list and the per-champion augment table without a browser.
 */

const BASE = 'https://op.gg'
const UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'

/**
 * @param {string} url
 * @returns {Promise<string>}
 */
export async function fetchHtml(url) {
  const res = await fetch(url, {
    headers: {
      'user-agent': UA,
      'accept-language': 'en-US,en;q=0.9',
      accept: 'text/html,application/xhtml+xml',
    },
    redirect: 'follow',
  })
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`)
  return res.text()
}

/**
 * Concatenate the decoded RSC flight payload of a page.
 * @param {string} html
 * @returns {string}
 */
export function decodeFlightPayload(html) {
  const re = /self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)/g
  let out = ''
  for (const match of html.matchAll(re)) {
    try {
      out += JSON.parse(match[1])
    } catch {
      /* skip malformed chunk */
    }
  }
  return out
}

/**
 * Extract every JSON array that follows `"<key>":[` in the payload.
 * @param {string} payload
 * @param {string} key
 * @returns {any[][]}
 */
export function extractArrays(payload, key) {
  const needle = `"${key}":[`
  const arrays = []
  let from = 0
  for (;;) {
    const at = payload.indexOf(needle, from)
    if (at < 0) break
    const start = at + needle.length - 1
    let depth = 0
    let inString = false
    let escaped = false
    let i = start
    for (; i < payload.length; i++) {
      const ch = payload[i]
      if (inString) {
        if (escaped) escaped = false
        else if (ch === '\\') escaped = true
        else if (ch === '"') inString = false
        continue
      }
      if (ch === '"') inString = true
      else if (ch === '[') depth++
      else if (ch === ']') {
        depth--
        if (depth === 0) {
          try {
            arrays.push(JSON.parse(payload.slice(start, i + 1)))
          } catch {
            /* skip malformed array */
          }
          break
        }
      }
    }
    from = i + 1
  }
  return arrays
}

/**
 * Detect the live patch from the page title.
 * @param {string} html
 * @returns {string|null}
 */
export function detectPatch(html) {
  const title = html.match(/<title>([^<]*)<\/title>/i)?.[1] ?? ''
  return title.match(/(\d+\.\d+)/)?.[1] ?? null
}

/**
 * @typedef {object} ChampionEntry
 * @property {string} slug
 * @property {string} name
 * @property {number} championId
 * @property {number|null} tier
 * @property {number|null} rank
 */

/**
 * Fetch the champion list used by the Mayhem mode page.
 * @returns {Promise<{patch: string|null, champions: ChampionEntry[]}>}
 */
export async function fetchChampions() {
  const html = await fetchHtml(`${BASE}/lol/modes/aram-mayhem`)
  const payload = decodeFlightPayload(html)
  const arrays = extractArrays(payload, 'champions')
  const list = arrays.sort((a, b) => b.length - a.length)[0] ?? []
  const seen = new Map()
  for (const entry of list) {
    const slug = String(entry?.key ?? '').trim()
    if (!slug || seen.has(slug)) continue
    seen.set(slug, {
      slug,
      name: String(entry.name ?? slug),
      championId: Number(entry.champion_id ?? entry.id ?? 0),
      tier: typeof entry.tier === 'number' ? entry.tier : null,
      rank: typeof entry.rank === 'number' ? entry.rank : null,
    })
  }
  return { patch: detectPatch(html), champions: [...seen.values()] }
}

/**
 * Fetch the augment table for one champion.
 * @param {string} slug
 * @returns {Promise<{patch: string|null, augments: any[]}>}
 */
export async function fetchChampionAugments(slug) {
  const html = await fetchHtml(`${BASE}/lol/modes/aram-mayhem/${encodeURIComponent(slug)}/augments`)
  const payload = decodeFlightPayload(html)
  const arrays = extractArrays(payload, 'data')
  const best = arrays
    .filter((arr) => Array.isArray(arr) && arr.some((x) => x && typeof x === 'object' && 'performance' in x))
    .sort((a, b) => b.length - a.length)[0]
  if (!best) throw new Error(`no augment table for ${slug}`)
  return { patch: detectPatch(html), augments: best }
}
