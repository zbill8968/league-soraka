#!/usr/bin/env node
/**
 * CLI wrapper around classify-core.mjs.
 *
 * node scripts/classify.mjs --data ./data --champion Annie \
 *      --crop a.png --crop b.png --crop c.png \
 *      --name 甲 --name 乙 --name 丙 [--policy balanced]
 */

import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createClassifier } from './classify-core.mjs'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')

function parseArgs(argv) {
  const out = { data: resolve(ROOT, 'data'), champion: null, crops: [], names: [], policy: 'balanced' }
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i]
    if (arg === '--data') out.data = resolve(argv[++i])
    else if (arg === '--champion') out.champion = argv[++i]
    else if (arg === '--crop') out.crops.push(resolve(argv[++i]))
    else if (arg === '--name') out.names.push(String(argv[++i] ?? ''))
    else if (arg === '--policy') out.policy = argv[++i]
  }
  return out
}

const args = parseArgs(process.argv.slice(2))
const classify = await createClassifier(args.data)
const result = await classify(args)
process.stdout.write(JSON.stringify(result))
