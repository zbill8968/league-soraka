/**
 * Electron overlay process for dsh-lol-augment.
 *
 * Why a separate process: the Harness host runs plugins in a plain Node child
 * process (no Electron APIs), so the transparent overlay, the global hotkey and
 * screen capture all live here. The plugin only spawns and supervises this app.
 *
 * This file is CommonJS on purpose: Electron's ESM facade does not expose the
 * main-process APIs. The shared scoring/matching code is ESM and is therefore
 * loaded with dynamic import() during startup.
 *
 * Modes:
 *   electron overlay/main.cjs                 normal overlay (F2 capture, F4 calibrate)
 *   electron overlay/main.cjs --mock          demo cards, no game required
 *   electron overlay/main.cjs --data-dir X    override the data directory
 */

const { app, BrowserWindow, desktopCapturer, globalShortcut, ipcMain, screen } = require('electron')
const { readFile, writeFile, mkdir } = require('node:fs/promises')
const { dirname, join, resolve } = require('node:path')
const fs = require('node:fs')
const https = require('node:https')

const ROOT = resolve(__dirname, '..')
const LOG_DIR = join(ROOT, '.cache')
const LOG_FILE = join(LOG_DIR, 'overlay-main.log')
try {
  fs.mkdirSync(LOG_DIR, { recursive: true })
  fs.appendFileSync(LOG_FILE, `\n===== boot ${new Date().toISOString()} argv=${process.argv.join(' ')} =====\n`)
} catch {
  /* logging must never block startup */
}

function logLine(...parts) {
  const line = `[${new Date().toISOString()}] ${parts.join(' ')}`
  try {
    fs.appendFileSync(LOG_FILE, line + '\n')
  } catch {
    /* ignore */
  }
  console.log(line)
}

process.on('uncaughtException', (error) => {
  logLine('uncaughtException', error?.stack ?? String(error))
})
process.on('unhandledRejection', (error) => {
  logLine('unhandledRejection', error?.stack ?? String(error))
})

app.setName('dsh-lol-augment')
logLine('electron', process.versions.electron, 'node', process.versions.node, 'chrome', process.versions.chrome)

const argv = process.argv.slice(1)
const flag = (name) => argv.includes(`--${name}`)
const value = (name, fallback) => {
  const index = argv.indexOf(`--${name}`)
  return index >= 0 ? argv[index + 1] : fallback
}

const MOCK = flag('mock')
const DATA_DIR = resolve(value('data-dir', join(ROOT, 'data')))
const LAYOUT_PATH = join(DATA_DIR, 'layout.json')

let lib = null
let win = null
let quitting = false

let catalog = { augments: [], champions: [] }
let stats = { byChampion: {} }
let iconIndex = { size: 0, items: [] }
let layout = null
let currentChampion = null
let championTimer = null

async function loadLibs() {
  const [matcher, score, policy] = await Promise.all([
    import('../lib/matcher.js'),
    import('../lib/score.js'),
    import('../lib/policy.js'),
  ])
  lib = { ...matcher, ...score, ...policy }
}

async function readJson(path, fallback) {
  try {
    return JSON.parse(await readFile(path, 'utf8'))
  } catch {
    return fallback
  }
}

async function loadData() {
  catalog = await readJson(join(DATA_DIR, 'catalog.json'), { augments: [], champions: [] })
  stats = await readJson(join(DATA_DIR, 'stats.json'), { byChampion: {} })
  iconIndex = await readJson(join(DATA_DIR, 'icon-index.json'), { size: 0, items: [] })
  layout = await readJson(LAYOUT_PATH, null)
}

function createWindow() {
  const display = screen.getPrimaryDisplay()
  const { x, y, width, height } = display.bounds
  win = new BrowserWindow({
    x,
    y,
    width,
    height,
    transparent: true,
    frame: false,
    resizable: false,
    movable: false,
    fullscreenable: false,
    skipTaskbar: true,
    focusable: false,
    hasShadow: false,
    show: false,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: join(ROOT, 'overlay', 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false,
    },
  })
  win.setAlwaysOnTop(true, 'screen-saver')
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })
  win.setIgnoreMouseEvents(true, { forward: true })
  win.loadFile(join(ROOT, 'overlay', 'index.html'))
  win.once('ready-to-show', () => {
    logLine('ready-to-show')
    if (win) win.showInactive()
  })
  win.webContents.on('did-finish-load', () => {
    logLine('did-finish-load')
    pushState()
    if (MOCK) {
      setTimeout(() => {
        logLine('mock: auto capture')
        win && win.webContents.send('cmd', { type: 'capture' })
      }, 1500)
    }
  })
  win.webContents.on('did-fail-load', (_event, code, description, url) => {
    logLine('did-fail-load', code, description, url)
  })
  win.webContents.on('preload-error', (_event, path, error) => {
    logLine('preload-error', path, error?.stack ?? String(error))
  })
  win.webContents.on('render-process-gone', (_event, details) => {
    logLine('render-process-gone', JSON.stringify(details))
  })
  win.webContents.on('console-message', (_event, level, message) => {
    logLine('renderer', level, message)
  })
  win.on('closed', () => {
    win = null
  })
}

function pushState() {
  if (!win) return
  win.webContents.send('state', {
    mock: MOCK,
    patch: catalog.patch ?? null,
    augments: catalog.augments?.length ?? 0,
    champions: Object.keys(stats.byChampion ?? {}).length,
    icons: iconIndex.items?.length ?? 0,
    calibrated: Boolean(layout?.icons?.length === 3),
    champion: currentChampion ? { name: currentChampion.name } : null,
  })
}

async function saveLayout(points) {
  const payload = { version: 1, generatedAt: new Date().toISOString(), icons: points }
  await mkdir(DATA_DIR, { recursive: true })
  await writeFile(LAYOUT_PATH, JSON.stringify(payload, null, 1), 'utf8')
  layout = payload
  return payload
}

function augmentById(id) {
  return (catalog.augments ?? []).find((a) => a.id === id) ?? null
}

function championModel() {
  const slug = currentChampion ? lib.normalizeChampionName(currentChampion.name) : null
  if (!slug) return null
  const model = lib.buildChampionModel(stats.byChampion, slug)
  return model.poolSize > 0 ? model : null
}

/**
 * Turn matched icons into a recommendation.
 * @param {{id:number|null, confidence:number}[]} matches
 * @param {{slot:number, rerollUsed?:boolean}[]} meta
 */
function buildRecommendation(matches, meta) {
  const model = championModel()
  const cards = matches.map((match, index) => {
    const augment = match.id ? augmentById(match.id) : null
    const scored =
      model && match.id
        ? lib.scoreAugment(model, match.id)
        : { score: null, percentile: null, grade: '?', popular: 0, known: false }
    return {
      slot: meta?.[index]?.slot ?? index,
      augmentId: match.id,
      name: augment?.name ?? '未识别',
      rarity: augment ? lib.rarityFromCode(augment.rarity) : null,
      score: scored.score,
      percentile: scored.percentile,
      grade: scored.grade,
      rerollUsed: Boolean(meta?.[index]?.rerollUsed),
      confidence: match.confidence,
    }
  })
  const recommendation = lib.recommend({ cards, policy: lib.POLICY_PRESETS.balanced })
  return {
    champion: currentChampion?.name ?? null,
    hasChampionData: Boolean(model),
    cards,
    recommendation,
    patch: catalog.patch ?? null,
  }
}

function mockResult() {
  const demo = [
    { name: 'High Roller', score: 93.1, percentile: 0.98, rarity: 'prismatic', grade: 'S' },
    { name: 'Witchful Thinking', score: 81.4, percentile: 0.42, rarity: 'gold', grade: 'B' },
    { name: 'Homeguard', score: 76.2, percentile: 0.18, rarity: 'silver', grade: 'C' },
  ]
  const cards = demo.map((card, index) => ({
    slot: index,
    augmentId: null,
    name: card.name,
    rarity: card.rarity,
    score: card.score,
    percentile: card.percentile,
    grade: card.grade,
    rerollUsed: false,
    confidence: 1,
  }))
  const recommendation = lib.recommend({ cards, policy: lib.POLICY_PRESETS.balanced })
  return { champion: 'Annie', hasChampionData: true, cards, recommendation, patch: catalog.patch ?? '16.22', mock: true }
}

ipcMain.handle('get-state', () => ({
  mock: MOCK,
  layout,
  patch: catalog.patch ?? null,
  champion: currentChampion ? currentChampion.name : null,
  calibrated: Boolean(layout?.icons?.length === 3),
}))

ipcMain.handle('source-id', async () => {
  const sources = await desktopCapturer.getSources({ types: ['screen'], thumbnailSize: { width: 1, height: 1 } })
  const primary = screen.getPrimaryDisplay()
  const match =
    sources.find((s) => String(s.display_id) === String(primary.id)) ??
    sources.find((s) => /screen/i.test(s.name)) ??
    sources[0]
  return match ? match.id : null
})

ipcMain.handle('crops', async (_event, payload) => {
  if (MOCK) return mockResult()
  const { crops } = payload ?? {}
  if (!Array.isArray(crops) || crops.length === 0) return { error: 'no-crops' }
  const matches = crops.map((crop) => {
    const desc = lib.describeCrop(new Uint8ClampedArray(crop.data), crop.width, crop.height)
    return lib.best(desc, iconIndex)
  })
  const meta = crops.map((crop) => ({ slot: crop.slot, rerollUsed: false }))
  return buildRecommendation(matches, meta)
})

ipcMain.handle('save-layout', async (_event, points) => {
  const saved = await saveLayout(points)
  pushState()
  return saved
})

ipcMain.handle('set-click-through', (_event, enabled) => {
  const clickThrough = Boolean(enabled)
  if (win) {
    win.setIgnoreMouseEvents(clickThrough, { forward: true })
    win.setFocusable(!clickThrough)
    if (!clickThrough) win.focus()
  }
  return true
})

ipcMain.handle('screenshot', async (_event, dataUrl) => {
  const base64 = String(dataUrl ?? '').replace(/^data:image\/png;base64,/, '')
  const dir = join(DATA_DIR, 'screenshots')
  await mkdir(dir, { recursive: true })
  const file = join(dir, `capture-${Date.now()}.png`)
  await writeFile(file, Buffer.from(base64, 'base64'))
  return file
})

function startChampionPolling() {
  const poll = async () => {
    try {
      const data = await readLiveClientData()
      const name = data?.activePlayer?.summonerName
      const player = (data?.allPlayers ?? []).find((p) => p.summonerName === name)
      if (player?.championName) {
        if (!currentChampion || currentChampion.name !== player.championName) {
          currentChampion = { name: player.championName }
          pushState()
        }
      }
    } catch {
      /* not in a game */
    }
  }
  poll()
  championTimer = setInterval(poll, 5000)
}

function readLiveClientData() {
  return new Promise((resolvePromise, reject) => {
    const req = https.get(
      'https://127.0.0.1:2999/liveclientdata/allgamedata',
      { rejectUnauthorized: false, timeout: 1500 },
      (res) => {
        let body = ''
        res.setEncoding('utf8')
        res.on('data', (chunk) => (body += chunk))
        res.on('end', () => {
          try {
            resolvePromise(JSON.parse(body))
          } catch (error) {
            reject(error)
          }
        })
      },
    )
    req.on('error', reject)
    req.on('timeout', () => req.destroy(new Error('timeout')))
  })
}

function registerShortcuts() {
  const bind = (accelerator, type) => {
    try {
      globalShortcut.register(accelerator, () => win && win.webContents.send('cmd', { type }))
      return globalShortcut.isRegistered(accelerator)
    } catch (error) {
      logLine('hotkey error', accelerator, error?.message ?? String(error))
      return false
    }
  }
  const okF2 = bind('F2', 'capture')
  const okF4 = bind('F4', 'calibrate')
  const okF3 = bind('F3', 'screenshot')
  logLine(`hotkeys capture=${okF2} calibrate=${okF4} screenshot=${okF3}`)
}

const singleInstance = app.requestSingleInstanceLock()
logLine('single-instance-lock', String(singleInstance))
if (!singleInstance) {
  logLine('another instance holds the lock; quitting')
  app.quit()
} else {
  app.on('second-instance', () => win && win.showInactive())
  app.whenReady().then(async () => {
    try {
      logLine('app ready')
      await loadLibs()
      logLine('libs loaded')
      await loadData()
      logLine(`data loaded patch=${catalog.patch || '?'} augments=${(catalog.augments || []).length}`)
      createWindow()
      logLine('window created')
      registerShortcuts()
      startChampionPolling()
      logLine(`data dir ${DATA_DIR}`)
      if (MOCK) logLine('mock mode: F2 shows a demo recommendation')
    } catch (error) {
      logLine('startup failed', error?.stack ?? String(error))
      app.quit()
    }
  })
}

app.on('will-quit', () => {
  quitting = true
  if (championTimer) clearInterval(championTimer)
  globalShortcut.unregisterAll()
})

app.on('window-all-closed', () => {
  if (!quitting) app.quit()
})
