import test from 'node:test'
import assert from 'node:assert/strict'

import { POLICY_PRESETS, recommend, rankCards } from '../lib/policy.js'

const card = (slot, score, extra = {}) => ({
  slot,
  augmentId: slot + 1000,
  name: `A${slot}`,
  rarity: 'gold',
  score,
  percentile: score === null ? null : score / 100,
  grade: score === null ? '?' : 'B',
  rerollUsed: false,
  origin: 'initial',
  ...extra,
})

test('keeps the best card and rerolls the two weaker ones', () => {
  const result = recommend({
    cards: [card(0, 91.2), card(1, 80.1), card(2, 72.4)],
    policy: POLICY_PRESETS.balanced,
  })
  assert.equal(result.keepSlot, 0)
  assert.deepEqual(result.rerollSlots, [1, 2])
  assert.equal(result.chaseKeep, false)
  assert.equal(result.finalSlot, 0)
})

test('does not reroll cards within the min gap', () => {
  const result = recommend({
    cards: [card(0, 85), card(1, 84), card(2, 83)],
    policy: POLICY_PRESETS.balanced,
  })
  assert.deepEqual(result.rerollSlots, [])
})

test('conservative policy needs a bigger gap than aggressive', () => {
  const cards = [card(0, 85), card(1, 80), card(2, 70)]
  assert.deepEqual(recommend({ cards, policy: POLICY_PRESETS.conservative }).rerollSlots, [2])
  assert.deepEqual(recommend({ cards, policy: POLICY_PRESETS.aggressive }).rerollSlots, [1, 2])
})

test('chases the previous keep when a rerolled card comes back stronger', () => {
  const result = recommend({
    cards: [
      card(0, 84, { percentile: 0.6 }),
      card(1, 92, { origin: 'reroll', rerollUsed: true, percentile: 0.97 }),
      card(2, 70, { rerollUsed: true }),
    ],
    policy: POLICY_PRESETS.balanced,
  })
  assert.equal(result.keepSlot, 1)
  assert.equal(result.chaseKeep, true)
  assert.equal(result.chaseSlot, 0)
  assert.match(result.reason, /回手重置 1 号卡/)
})

test('does not chase when the previous keep is already top grade', () => {
  const result = recommend({
    cards: [
      card(0, 84, { percentile: 0.99 }),
      card(1, 92, { origin: 'reroll', rerollUsed: true, percentile: 0.97 }),
    ],
    policy: POLICY_PRESETS.balanced,
  })
  assert.equal(result.keepSlot, 1)
  assert.equal(result.chaseKeep, false)
})

test('never suggests rerolling a card that was already rerolled', () => {
  const result = recommend({
    cards: [card(0, 90), card(1, 70, { rerollUsed: true }), card(2, 60)],
    policy: POLICY_PRESETS.balanced,
  })
  assert.deepEqual(result.rerollSlots, [2])
})

test('unknown cards are rerolled and never win the keep slot', () => {
  const result = recommend({
    cards: [
      card(0, null, { grade: '?', percentile: null }),
      card(1, 88),
      card(2, null, { grade: '?', percentile: null }),
    ],
    policy: POLICY_PRESETS.balanced,
  })
  assert.equal(result.keepSlot, 1)
  assert.deepEqual(result.rerollSlots, [0, 2])
})

test('handles empty and fully unknown offers', () => {
  assert.equal(recommend({ cards: [] }).keepSlot, -1)

  const allUnknown = recommend({
    cards: [card(0, null, { grade: '?', percentile: null }), card(1, null, { grade: '?', percentile: null })],
    policy: POLICY_PRESETS.balanced,
  })
  assert.deepEqual(allUnknown.rerollSlots, [])
  assert.match(allUnknown.reason, /数据/)
})

test('rankCards keeps slot order on ties', () => {
  const ranked = rankCards([card(0, 80), card(1, 90), card(2, 80)])
  assert.deepEqual(
    ranked.map((c) => c.slot),
    [1, 0, 2],
  )
})
