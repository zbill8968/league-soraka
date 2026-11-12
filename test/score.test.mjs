import test from 'node:test'
import assert from 'node:assert/strict'

import {
  buildChampionModel,
  gradeFromPercentile,
  normalizeChampionName,
  rarityFromCode,
  scoreAugment,
} from '../lib/score.js'

const stats = {
  annie: {
    1: { performance: 90, popular: 5 },
    2: { performance: 80, popular: 3 },
    3: { performance: 70, popular: 1 },
    4: { performance: 0, popular: 0 },
    5: { performance: 120, popular: 9 },
  },
}

test('buildChampionModel drops unusable rows and clamps outliers', () => {
  const model = buildChampionModel(stats, 'annie')
  assert.equal(model.poolSize, 4)
  assert.equal(model.byAugment.get('4'), undefined)
  assert.equal(model.byAugment.get('5').performance, 100)
})

test('percentiles run from 0 (worst) to 1 (best)', () => {
  const model = buildChampionModel(stats, 'annie')
  assert.equal(model.byAugment.get('3').percentile, 0)
  assert.equal(model.byAugment.get('5').percentile, 1)
  assert.ok(model.byAugment.get('2').percentile > 0 && model.byAugment.get('2').percentile < 1)
})

test('scoreAugment reports unknown augments instead of guessing', () => {
  const model = buildChampionModel(stats, 'annie')
  const known = scoreAugment(model, 1)
  assert.equal(known.known, true)
  assert.equal(known.score, 90)

  const missing = scoreAugment(model, 999)
  assert.equal(missing.known, false)
  assert.equal(missing.score, null)
  assert.equal(missing.grade, '?')
})

test('gradeFromPercentile buckets', () => {
  assert.equal(gradeFromPercentile(1), 'S')
  assert.equal(gradeFromPercentile(0.95), 'S')
  assert.equal(gradeFromPercentile(0.94), 'A')
  assert.equal(gradeFromPercentile(0.8), 'A')
  assert.equal(gradeFromPercentile(0.79), 'B')
  assert.equal(gradeFromPercentile(0.5), 'B')
  assert.equal(gradeFromPercentile(0.49), 'C')
  assert.equal(gradeFromPercentile(0.25), 'C')
  assert.equal(gradeFromPercentile(0.24), 'D')
})

test('champion names normalise across client and OP.GG spellings', () => {
  assert.equal(normalizeChampionName('MonkeyKing'), 'wukong')
  assert.equal(normalizeChampionName('Dr. Mundo'), 'drmundo')
  assert.equal(normalizeChampionName('Lee Sin'), 'leesin')
  assert.equal(normalizeChampionName('KSante'), 'ksante')
})

test('rarity codes map to card rarities', () => {
  assert.equal(rarityFromCode(1), 'silver')
  assert.equal(rarityFromCode(4), 'gold')
  assert.equal(rarityFromCode(8), 'prismatic')
  assert.equal(rarityFromCode(null), null)
})
