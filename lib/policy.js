/**
 * Augment pick policy for one offer screen.
 *
 * The user's rule set: keep the single best card, reroll the two weaker ones, and
 * only reroll the kept card when a rerolled card turns out better (a higher grade
 * or a materially higher score). Everything here is a pure function over scored
 * cards so it can be unit-tested without a screen or a game.
 *
 * Card origins matter: `initial` cards are the three originally offered, `reroll`
 * cards are what came back from a reroll. A rerolled card that beats the original
 * keep is exactly the situation where the user wants to reroll the keep too.
 */

import { RARITY_RANK } from './score.js'

/** @typedef {'conservative'|'balanced'|'aggressive'} PolicyStyle */

/**
 * @typedef {object} PolicyConfig
 * @property {PolicyStyle} style
 * @property {number} minGap         score gap below the kept card that justifies a reroll
 * @property {number} upgradeGap     score gap above the kept card that justifies rerolling it
 * @property {boolean} chaseOnRarityUp
 * @property {number} protectPercentile  never suggest rerolling a card this strong (0..1)
 */

/** @type {Record<PolicyStyle, PolicyConfig>} */
export const POLICY_PRESETS = {
  conservative: {
    style: 'conservative',
    minGap: 6,
    upgradeGap: 10,
    chaseOnRarityUp: false,
    protectPercentile: 0.9,
  },
  balanced: {
    style: 'balanced',
    minGap: 4,
    upgradeGap: 6,
    chaseOnRarityUp: true,
    protectPercentile: 0.95,
  },
  aggressive: {
    style: 'aggressive',
    minGap: 2,
    upgradeGap: 3,
    chaseOnRarityUp: true,
    protectPercentile: 1,
  },
}

/**
 * @typedef {object} ScoredCard
 * @property {number} slot            0-based position on screen
 * @property {number|string|null} augmentId
 * @property {string} name
 * @property {string|null} rarity     silver|gold|prismatic|null
 * @property {number|null} score      0..100 OP.GG performance for the champion
 * @property {number|null} percentile 0..1 inside the champion pool
 * @property {string} grade           S|A|B|C|D|?
 * @property {boolean} rerollUsed
 * @property {'initial'|'reroll'} [origin]
 */

/**
 * Order cards best-first. Unknown cards go last, keeping their screen order.
 * @param {ScoredCard[]} cards
 * @returns {ScoredCard[]}
 */
export function rankCards(cards) {
  return [...cards].sort((a, b) => {
    const as = a.score ?? -1
    const bs = b.score ?? -1
    if (bs !== as) return bs - as
    return a.slot - b.slot
  })
}

function isKnown(card) {
  return card.score !== null && card.score !== undefined
}

function rarityRank(card) {
  return card.rarity ? (RARITY_RANK[card.rarity] ?? 0) : 0
}

function isProtected(card, policy) {
  return card.percentile !== null && card.percentile !== undefined && card.percentile >= policy.protectPercentile
}

/**
 * @typedef {object} Recommendation
 * @property {number} keepSlot
 * @property {number[]} rerollSlots
 * @property {boolean} chaseKeep
 * @property {number|null} chaseSlot
 * @property {number|null} finalSlot
 * @property {string} reason
 * @property {ScoredCard[]} ranked
 */

/**
 * Produce the recommendation for one offer.
 * @param {object} input
 * @param {ScoredCard[]} input.cards
 * @param {PolicyConfig} [input.policy]
 * @returns {Recommendation}
 */
export function recommend({ cards, policy = POLICY_PRESETS.balanced }) {
  if (!Array.isArray(cards) || cards.length === 0) {
    return {
      keepSlot: -1,
      rerollSlots: [],
      chaseKeep: false,
      chaseSlot: null,
      finalSlot: null,
      reason: '没有识别到卡片，按 F2 重新截取。',
      ranked: [],
    }
  }

  const ranked = rankCards(cards)
  const known = ranked.filter(isKnown)
  if (known.length === 0) {
    return {
      keepSlot: ranked[0].slot,
      rerollSlots: [],
      chaseKeep: false,
      chaseSlot: null,
      finalSlot: ranked[0].slot,
      reason: '三张卡都没有可用数据（可能是新版本增幅），先按通用强度选，建议刷新数据。',
      ranked,
    }
  }

  const keep = known[0]
  const keepScore = keep.score
  const rerollSlots = ranked
    .filter((card) => card.slot !== keep.slot && !card.rerollUsed)
    .filter((card) => !isKnown(card) || card.score < keepScore - policy.minGap)
    .map((card) => card.slot)

  // The keep card is a reroll result: decide whether the still-unspent "previous
  // keep" is worth rerolling to try to beat it.
  let chaseSlot = null
  if (keep.origin === 'reroll') {
    const previousKeep = ranked
      .filter((card) => card.slot !== keep.slot && card.origin !== 'reroll' && !card.rerollUsed && isKnown(card))
      .sort((a, b) => b.score - a.score)[0]
    if (previousKeep && !isProtected(previousKeep, policy)) {
      const scoreUp = keepScore - previousKeep.score >= policy.upgradeGap
      const rarityUp = policy.chaseOnRarityUp && rarityRank(keep) > rarityRank(previousKeep)
      if (scoreUp || rarityUp) chaseSlot = previousKeep.slot
    }
  }

  return {
    keepSlot: keep.slot,
    rerollSlots,
    chaseKeep: chaseSlot !== null,
    chaseSlot,
    finalSlot: keep.slot,
    reason: buildReason({ keep, rerollSlots, chaseSlot, ranked, policy }),
    ranked,
  }
}

function cardLabel(card) {
  return `${card.slot + 1} 号卡${card.name ? `「${card.name}」` : ''}`
}

function buildReason({ keep, rerollSlots, chaseSlot, ranked, policy }) {
  const scoreText = keep.score !== null ? ` ${keep.score.toFixed(1)}` : ''
  const parts = [`保留 ${cardLabel(keep)}（${keep.grade}${scoreText}）`]

  if (rerollSlots.length === 0) {
    parts.push('其余不建议重置')
  } else {
    parts.push(`重置 ${rerollSlots.map((slot) => `${slot + 1} 号卡`).join('、')}`)
  }

  if (chaseSlot !== null) {
    parts.push(`重置结果更强，回手重置 ${chaseSlot + 1} 号卡去追`)
  } else if (keep.origin !== 'reroll') {
    parts.push(`若重置后出现高出 ${policy.upgradeGap} 分或更高稀有度的卡，再回手重置 ${keep.slot + 1} 号卡`)
  }

  return `${parts.join('；')}。`
}
