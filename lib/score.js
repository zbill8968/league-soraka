/**
 * Scoring model for ARAM: Mayhem augments.
 *
 * Input is the OP.GG per-champion augment table (see scripts/fetch-opgg.mjs):
 * every augment carries a 0..100 `performance` score and a pick rate for the
 * champion whose page it came from. We keep OP.GG's number as the base signal and
 * derive a percentile inside that champion's pool so the UI can show S..D grades
 * that mean "top 5% for this champion" rather than a raw number.
 */

/** OP.GG rarity codes -> canonical rarity keys, best last. */
export const RARITY_BY_CODE = new Map([
  [1, 'silver'],
  [2, 'silver'],
  [4, 'gold'],
  [8, 'prismatic'],
])

/** Higher is better. Used to decide whether a rerolled card "upgraded" the screen. */
export const RARITY_RANK = { silver: 1, gold: 2, prismatic: 3 }

/** Champion names that differ between the Live Client Data API and OP.GG slugs. */
const CHAMPION_ALIASES = new Map([
  ['monkeyking', 'wukong'],
  ['nunu', 'nunuwillump'],
  ['renata', 'renataglasc'],
  ['velkoz', 'velkoz'],
  ['reksai', 'reksai'],
  ['chogath', 'chogath'],
  ['ksante', 'ksante'],
  ['belveth', 'belveth'],
  ['drmundo', 'drmundo'],
  ['aurelionsol', 'aurelionsol'],
  ['tahmkench', 'tahmkench'],
  ['twistedfate', 'twistedfate'],
  ['jarvaniv', 'jarvaniv'],
  ['leesin', 'leesin'],
  ['masteryi', 'masteryi'],
  ['missfortune', 'missfortune'],
  ['xinzhao', 'xinzhao'],
  ['kogmaw', 'kogmaw'],
  ['kaisa', 'kaisa'],
  ['khazix', 'khazix'],
])

/**
 * Normalise a champion name to a comparable slug.
 * @param {string} name
 * @returns {string}
 */
export function normalizeChampionName(name) {
  const base = String(name ?? '').toLowerCase().replace(/[^a-z0-9]/g, '')
  return CHAMPION_ALIASES.get(base) ?? base
}

/**
 * Map an OP.GG rarity code to its canonical key.
 * @param {number|null|undefined} code
 * @returns {'silver'|'gold'|'prismatic'|null}
 */
export function rarityFromCode(code) {
  return RARITY_BY_CODE.get(code) ?? null
}

/**
 * Grade a 0..1 percentile into a coarse letter grade.
 * @param {number} percentile 0..1, 1 = best
 * @returns {'S'|'A'|'B'|'C'|'D'}
 */
export function gradeFromPercentile(percentile) {
  if (percentile >= 0.95) return 'S'
  if (percentile >= 0.8) return 'A'
  if (percentile >= 0.5) return 'B'
  if (percentile >= 0.25) return 'C'
  return 'D'
}

/** OP.GG sometimes emits scores above 100 (their own clamp). */
function cleanPerformance(value) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return null
  return Math.min(100, value)
}

/**
 * @typedef {object} ChampionModel
 * @property {string} slug
 * @property {Map<string, {performance: number|null, popular: number, percentile: number}>} byAugment
 * @property {number} poolSize   number of augments with a usable performance value
 * @property {number} median
 */

/**
 * Build the lookup model for one champion.
 * @param {object} statsByChampion  stats.byChampion from data/stats.json
 * @param {string} championSlug
 * @returns {ChampionModel}
 */
export function buildChampionModel(statsByChampion, championSlug) {
  const table = statsByChampion?.[championSlug] ?? {}
  const entries = Object.entries(table)
    .map(([id, row]) => [id, { ...row, performance: cleanPerformance(row?.performance) }])
    .filter(([, row]) => row.performance !== null)
    .sort((a, b) => a[1].performance - b[1].performance)

  const byAugment = new Map()
  const total = entries.length
  entries.forEach(([id, row], index) => {
    byAugment.set(String(id), {
      performance: row.performance,
      popular: typeof row.popular === 'number' ? row.popular : 0,
      percentile: total <= 1 ? 0.5 : index / (total - 1),
    })
  })
  const median = total ? entries[Math.floor(total / 2)][1].performance : 50
  return { slug: championSlug, byAugment, poolSize: total, median }
}

/**
 * Score one augment for the current champion.
 * @param {ChampionModel} model
 * @param {number|string} augmentId
 * @returns {{score: number|null, percentile: number|null, grade: string, popular: number, known: boolean}}
 */
export function scoreAugment(model, augmentId) {
  const row = model?.byAugment?.get(String(augmentId))
  if (!row) {
    return { score: null, percentile: null, grade: '?', popular: 0, known: false }
  }
  return {
    score: row.performance,
    percentile: row.percentile,
    grade: gradeFromPercentile(row.percentile),
    popular: row.popular,
    known: true,
  }
}
