/**
 * Minimal Electron smoke test: proves the binary can boot and reach app-ready on
 * this machine, independent of the overlay code. Writes to .cache/smoke.log.
 */

const { app } = require('electron')
const fs = require('node:fs')
const { join, resolve, dirname } = require('node:path')

const ROOT = resolve(dirname(__filename), '..')
const LOG = join(ROOT, '.cache', 'smoke.log')

function say(line) {
  try {
    fs.mkdirSync(dirname(LOG), { recursive: true })
    fs.appendFileSync(LOG, `[${new Date().toISOString()}] ${line}\n`)
  } catch {
    /* ignore */
  }
}

say(`boot electron=${process.versions.electron} node=${process.versions.node}`)

app.whenReady().then(() => {
  say('ready')
  const { BrowserWindow } = require('electron')
  try {
    const win = new BrowserWindow({ width: 200, height: 120, show: false, frame: false, transparent: true })
    win.loadURL('data:text/html,<h1>ok</h1>')
    win.webContents.once('did-finish-load', () => {
      say('window loaded')
      win.destroy()
      say('done')
      app.quit()
    })
    setTimeout(() => {
      say('timeout waiting for load')
      app.quit()
    }, 5000)
  } catch (error) {
    say('window failed: ' + (error && error.stack ? error.stack : String(error)))
    app.quit()
  }
})

process.on('uncaughtException', (error) => say('uncaught ' + (error && error.stack ? error.stack : String(error))))
