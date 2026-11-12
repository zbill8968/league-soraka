#!/usr/bin/env node
/**
 * Long-lived classifier worker.
 *
 * Protocol: one JSON request per stdin line, one JSON response per stdout line.
 *   request : {"champion":"Rengar","crops":["a.png","b.png","c.png"],"names":["甲","乙","丙"],"policy":"balanced"}
 *   response: the classify result object
 *
 * Loading catalog/stats once turns each offer from ~0.5 s into a few milliseconds.
 */

import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createClassifier } from './classify-core.mjs'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const args = process.argv.slice(2)
const dataIndex = args.indexOf('--data')
const dataDir = dataIndex >= 0 ? resolve(args[dataIndex + 1]) : resolve(ROOT, 'data')

const require = createRequire(import.meta.url)
const readline = require('node:readline')

const classify = await createClassifier(dataDir)

const rl = readline.createInterface({ input: process.stdin, terminal: false })
process.stdout.write('ready\n')

rl.on('line', async (line) => {
  const text = line.trim()
  if (!text) return
  try {
    const request = JSON.parse(text)
    const result = await classify(request)
    process.stdout.write(JSON.stringify(result) + '\n')
  } catch (error) {
    process.stdout.write(JSON.stringify({ error: String(error?.message ?? error) }) + '\n')
  }
})
