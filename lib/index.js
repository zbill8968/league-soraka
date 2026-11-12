/**
 * league-soraka — host plugin.
 *
 * The Harness loads this plugin in a plain Node process, so it cannot open a
 * transparent window itself. What it does instead:
 *   - supervises the Python overlay process (spawn, restart, stop);
 *   - exposes model-facing tools so the agent can inspect status, refresh the
 *     OP.GG data snapshot and explain the policy;
 *   - ships a skill that teaches the agent when to use those tools.
 *
 * The overlay does the real-time work (mouse, capture, OCR, matching, badges) and
 * reads the same data snapshot from disk, so the hot path never waits on a model.
 */

import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdir, readFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const name = 'league-soraka'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const SKILLS_DIR = join(ROOT, 'skills')

function log(ctx, level, message) {
  try {
    const logger = typeof ctx?.logger === 'function' ? ctx.logger(name) : ctx?.logger
    if (logger && typeof logger[level] === 'function') {
      logger[level](message)
      return
    }
  } catch {
    /* fall through */
  }
  const line = `[${name}] ${message}`
  if (level === 'error') console.error(line)
  else console.log(line)
}

function resolveElectron(root) {
  const candidates = [
    process.env.DSH_LOL_AUGMENT_ELECTRON,
    join(root, 'node_modules', 'electron', 'dist', 'electron.exe'),
    join(root, 'node_modules', 'electron', 'dist', 'electron'),
  ].filter(Boolean)
  return candidates.find((candidate) => existsSync(candidate)) ?? null
}

/**
 * The Python overlay is the default backend: it is the only one that starts
 * reliably on this machine (Electron aborts before creating a window).
 */
function resolvePython() {
  const candidates = [
    process.env.DSH_LOL_AUGMENT_PYTHON,
    join(process.env.USERPROFILE ?? '', '.dsh', 'dsh-runtimes', 'dsh-primary-runtime', 'dependencies', 'python', 'python.exe'),
    join(process.env.LOCALAPPDATA ?? '', 'Programs', 'Python', 'Python312', 'python.exe'),
  ].filter(Boolean)
  return candidates.find((candidate) => existsSync(candidate)) ?? 'python'
}

async function readJson(path, fallback) {
  try {
    return JSON.parse(await readFile(path, 'utf8'))
  } catch {
    return fallback
  }
}

function toolDef(spec) {
  return {
    name: spec.name,
    description: spec.description,
    parameters: spec.parameters ?? {},
    output: spec.output,
    async execute(args, exec) {
      return spec.execute(args ?? {}, exec)
    },
  }
}

function jsonTool(name, description, parameters, execute) {
  return toolDef({
    name,
    description,
    parameters,
    output: {
      schema: { type: 'object' },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value, null, 2) }],
    },
    execute,
  })
}

/**
 * Register the model-facing tools. Kept separate so a host that rejects plain
 * tool objects fails one registration instead of taking the whole plugin down.
 */
function registerTools(toolCtx, settings, state) {
  toolCtx.tools.register(
    jsonTool(
      'lol_augment_status',
      'Report the ARAM: Mayhem augment helper status: whether the in-game overlay is running, the loaded OP.GG data patch, champion coverage and the last exit.',
      {},
      async () => {
        const catalog = await readJson(join(settings.dataDir, 'catalog.json'), null)
        const stats = await readJson(join(settings.dataDir, 'stats.json'), null)
        const meta = await readJson(join(settings.dataDir, 'meta.json'), null)
        return {
          overlay: {
            running: Boolean(state.child),
            pid: state.child?.pid ?? null,
            startedAt: state.startedAt,
            lastExit: state.lastExit,
            hotkey: 'F8 = recommend, F9 = save screenshot (used for layout calibration)',
          },
          data: {
            patch: catalog?.patch ?? null,
            augments: catalog?.augments?.length ?? 0,
            champions: Object.keys(stats?.byChampion ?? {}).length,
            fetchedAt: stats?.fetchedAt ?? meta?.fetchedAt ?? null,
            dataDir: settings.dataDir,
          },
        }
      },
    ),
  )

  toolCtx.tools.register(
    jsonTool(
      'lol_augment_refresh_data',
      'Re-download the ARAM: Mayhem augment statistics from OP.GG into the local snapshot (resumable; keeps the previous snapshot on failure).',
      { limit: { type: 'number', description: 'Only fetch the first N champions (smoke test).' } },
      async (args) => {
        const script = join(ROOT, 'scripts', 'fetch-opgg.mjs')
        const cliArgs = [script, '--data-dir', settings.dataDir]
        if (args.limit) cliArgs.push('--limit', String(args.limit))
        return await new Promise((resolvePromise) => {
          const child = spawn(process.execPath, cliArgs, { cwd: ROOT, env: process.env, windowsHide: true })
          let out = ''
          let err = ''
          child.stdout.on('data', (chunk) => (out += chunk))
          child.stderr.on('data', (chunk) => (err += chunk))
          child.on('exit', (code) => {
            resolvePromise({ code, tail: (out + err).split('\n').filter(Boolean).slice(-6) })
          })
        })
      },
    ),
  )

  toolCtx.tools.register(
    toolDef({
      name: 'lol_augment_explain_policy',
      description:
        'Explain the augment keep/reroll policy used by the overlay: which card to keep, which to reroll, and when to reroll the kept card for a higher-tier result.',
      parameters: {
        scores: {
          type: 'array',
          description: 'Optional three performance scores (0-100) to run the policy on.',
        },
      },
      output: {
        schema: { type: 'object' },
        render: (_args, value) => [{ type: 'text', text: value.text }],
      },
      async execute(args) {
        const { recommend, POLICY_PRESETS } = await import('../lib/policy.js')
        const scores = Array.isArray(args.scores) && args.scores.length === 3 ? args.scores : [90, 82, 74]
        const cards = scores.map((score, slot) => ({
          slot,
          augmentId: slot,
          name: `card ${slot + 1}`,
          rarity: 'gold',
          score,
          percentile: score / 100,
          grade: 'B',
          rerollUsed: false,
          origin: 'initial',
        }))
        const result = recommend({ cards, policy: POLICY_PRESETS.balanced })
        return {
          text: result.reason,
          policy: POLICY_PRESETS.balanced,
          keepSlot: result.keepSlot,
          rerollSlots: result.rerollSlots,
        }
      },
    }),
  )
}

/**
 * @param {object} ctx cordis context
 * @param {object} [config] loader row config
 */
export function apply(ctx, config = {}) {
  const settings = {
    enabled: true,
    autoStart: true,
    mock: false,
    // 'python' (default, reliable) or 'electron' (needs a working Electron install)
    overlay: 'python',
    dataDir: join(ROOT, 'data'),
    overlayArgs: [],
    ...(config && typeof config === 'object' ? config : {}),
  }
  if (!settings.enabled) {
    log(ctx, 'warn', 'disabled by config')
    return
  }

  const state = { child: null, restarts: 0, startedAt: null, lastExit: null }
  const useElectron = settings.overlay === 'electron'
  const overlayEntry = useElectron ? join(ROOT, 'overlay', 'main.cjs') : join(ROOT, 'overlay-py', 'overlay.py')
  const electron = useElectron ? resolveElectron(ROOT) : null
  const python = useElectron ? null : resolvePython()

  function startOverlay() {
    if (useElectron && !electron) {
      log(ctx, 'error', 'Electron binary not found; set overlay: "python" or run `pnpm install`')
      return null
    }
    if (state.child) return state.child

    let command
    let args
    if (useElectron) {
      command = electron
      args = [overlayEntry, '--data-dir', settings.dataDir]
    } else {
      command = python
      args = [overlayEntry, '--data', settings.dataDir, '--node', process.execPath]
    }
    if (settings.mock) args.push('--mock')
    args.push(...(Array.isArray(settings.overlayArgs) ? settings.overlayArgs : []))

    const env = { ...process.env }
    // Electron refuses to start as a desktop app when this is inherited.
    delete env.ELECTRON_RUN_AS_NODE

    const child = spawn(command, args, {
      cwd: ROOT,
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    })
    state.child = child
    state.startedAt = new Date().toISOString()

    child.stdout?.on('data', (chunk) => log(ctx, 'log', String(chunk).trim()))
    child.stderr?.on('data', (chunk) => log(ctx, 'error', String(chunk).trim()))
    child.on('error', (error) => {
      log(ctx, 'error', `overlay spawn failed: ${error?.message ?? error}`)
    })
    child.on('exit', (code, signal) => {
      state.child = null
      state.lastExit = { code, signal, at: new Date().toISOString() }
      log(ctx, 'warn', `overlay exited (code=${code} signal=${signal})`)
      if (state.restarts < 3 && settings.autoStart) {
        state.restarts += 1
        setTimeout(() => {
          if (!state.child) startOverlay()
        }, 1500)
      }
    })
    return child
  }

  function stopOverlay() {
    if (!state.child) return
    try {
      state.child.kill()
    } catch {
      /* ignore */
    }
    state.child = null
  }

  ctx.inject(['tools'], (toolCtx) => {
    try {
      registerTools(toolCtx, settings, state)
    } catch (error) {
      log(ctx, 'error', `tool registration failed: ${error?.message ?? error}`)
    }
  })

  ctx.inject(['skills'], (skillCtx) => {
    skillCtx.skills.registerProvider(() => ({
      name: `${name}-skill`,
      rank: 600,
      getSkills: async () => [
        {
          name: 'league-soraka',
          description:
            'ARAM: Mayhem augment helper: status of the in-game overlay, OP.GG data refresh and the keep/reroll policy. Use when the user asks about augment picks, the F8 overlay, or the OP.GG snapshot.',
          body: await readFile(join(SKILLS_DIR, 'league-soraka', 'SKILL.md'), 'utf8').catch(() => ''),
        },
      ],
    }))
  })

  mkdir(settings.dataDir, { recursive: true }).catch(() => {})

  if (settings.autoStart) {
    startOverlay()
    ctx.on?.('dispose', () => stopOverlay())
  }

  log(ctx, 'log', `ready (data ${settings.dataDir}, overlay ${state.child ? 'running' : 'not running'})`)
}
