/* global window, document, navigator */

const api = window.overlay

const video = document.getElementById('video')
const badgesEl = document.getElementById('badges')
const planEl = document.getElementById('plan')
const hintEl = document.getElementById('hint')
const calibText = document.getElementById('calibText')
const btnReset = document.getElementById('btnReset')
const btnSave = document.getElementById('btnSave')
const btnCancel = document.getElementById('btnCancel')

const state = {
  mock: false,
  calibrated: false,
  layout: null,
  patch: null,
  champion: null,
}

let stream = null
let sourceId = null
let captureCanvas = document.createElement('canvas')
let captureCtx = captureCanvas.getContext('2d', { willReadFrequently: true })
let busy = false
let hintTimer = null

const calib = { active: false, points: [] }

function showHint(text, ms = 4000) {
  hintEl.textContent = text
  hintEl.style.display = 'block'
  if (hintTimer) clearTimeout(hintTimer)
  if (ms > 0) {
    hintTimer = setTimeout(() => {
      hintEl.style.display = 'none'
    }, ms)
  }
}

async function startStream() {
  if (stream) return stream
  if (!sourceId) sourceId = await api.getSourceId()
  if (!sourceId) throw new Error('no screen source')
  stream = await navigator.mediaDevices.getUserMedia({
    audio: false,
    video: {
      mandatory: {
        chromeMediaSource: 'desktop',
        chromeMediaSourceId: sourceId,
        maxWidth: 3840,
        maxHeight: 2160,
        maxFrameRate: 8,
      },
    },
  })
  video.srcObject = stream
  await video.play().catch(() => {})
  await new Promise((resolve) => {
    if (video.videoWidth) return resolve()
    video.addEventListener('loadedmetadata', () => resolve(), { once: true })
  })
  return stream
}

function grabFrame() {
  const width = video.videoWidth
  const height = video.videoHeight
  if (!width || !height) throw new Error('capture not ready')
  captureCanvas.width = width
  captureCanvas.height = height
  captureCtx.drawImage(video, 0, 0, width, height)
  return captureCanvas
}

function cropRGBA(canvas, x, y, w, h) {
  const tmp = document.createElement('canvas')
  tmp.width = Math.max(8, Math.round(w))
  tmp.height = Math.max(8, Math.round(h))
  const ctx = tmp.getContext('2d', { willReadFrequently: true })
  ctx.drawImage(canvas, Math.round(x), Math.round(y), tmp.width, tmp.height, 0, 0, tmp.width, tmp.height)
  return { width: tmp.width, height: tmp.height, data: ctx.getImageData(0, 0, tmp.width, tmp.height).data }
}

function currentIcons() {
  const icons = state.layout?.icons
  if (Array.isArray(icons) && icons.length === 3) return icons
  return null
}

/**
 * Where to draw a card badge. Uses the calibrated layout when present, otherwise
 * falls back to a demo row so mock mode (and first-run screenshots) still show
 * something on screen.
 */
function displayPoints(count) {
  const icons = currentIcons()
  if (icons) return icons.slice(0, count)
  const offsets = [0.5 - 0.17, 0.5, 0.5 + 0.17]
  return Array.from({ length: count }, (_, index) => ({ x: offsets[index] ?? 0.5, y: 0.62, size: 0.08 }))
}

async function doCapture() {
  if (busy) return
  busy = true
  try {
    if (state.mock) {
      const result = await api.sendCrops([])
      render(result)
      return
    }
    const icons = currentIcons()
    if (!icons) {
      showHint('还没标定三张卡的位置：按 F4 标定一次')
      return
    }
    const canvas = grabFrame()
    const crops = icons.map((point, slot) => {
      const cx = point.x * canvas.width
      const cy = point.y * canvas.height
      const size = (point.size ?? 0.07) * canvas.width
      const inset = size * 0.1
      const crop = cropRGBA(canvas, cx - size / 2 + inset, cy - size / 2 + inset, size - inset * 2, size - inset * 2)
      return { slot, width: crop.width, height: crop.height, data: crop.data }
    })
    const result = await api.sendCrops(crops)
    render(result)
  } catch (error) {
    showHint(`识别失败：${error?.message ?? error}`)
  } finally {
    busy = false
  }
}

function render(result) {
  badgesEl.innerHTML = ''
  if (!result || result.error) {
    showHint(result?.error === 'no-crops' ? '没有截图数据' : '识别失败')
    return
  }
  const icons = currentIcons()
  const recommendation = result.recommendation ?? {}
  const keepSlot = recommendation.keepSlot
  const reroll = new Set(recommendation.rerollSlots ?? [])

  const cards = result.cards ?? []
  const points = displayPoints(cards.length)
  cards.forEach((card, index) => {
    const point = points[index]
    if (!point) return
    const el = document.createElement('div')
    el.className = `badge ${card.grade && card.grade !== '?' ? card.grade : 'q'}`
    if (card.slot === keepSlot) el.classList.add('keep')
    if (reroll.has(card.slot)) el.classList.add('reroll')
    const score = card.score === null || card.score === undefined ? '—' : card.score.toFixed(1)
    el.innerHTML = `
      <div class="grade">${card.grade ?? '?'}</div>
      <div class="name">${escapeHtml(card.name ?? '未识别')}</div>
      <div class="meta">${score}${card.confidence !== undefined ? ` · ${Math.round(card.confidence * 100)}%` : ''}</div>
    `
    const top = (point.y + (point.size ?? 0.07) * 0.95) * window.innerHeight
    el.style.left = `${point.x * window.innerWidth}px`
    el.style.top = `${Math.min(window.innerHeight - 90, top)}px`
    badgesEl.appendChild(el)
  })

  planEl.style.display = 'block'
  const champ = result.champion ? `${result.champion} · ` : ''
  const patch = result.patch ? `patch ${result.patch}` : ''
  planEl.innerHTML = `<b>${champ}${patch}</b>　${escapeHtml(recommendation.reason ?? '')}`
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch])
}

async function saveScreenshot() {
  try {
    const canvas = grabFrame()
    const dataUrl = canvas.toDataURL('image/png')
    const file = await api.saveScreenshot(dataUrl)
    showHint(`截图已保存：${file}`)
  } catch (error) {
    showHint(`截图失败：${error?.message ?? error}`)
  }
}

function enterCalibration() {
  calib.active = true
  calib.points = []
  document.body.classList.add('calibrating')
  api.setClickThrough(false)
  badgesEl.innerHTML = ''
  planEl.style.display = 'none'
  renderCalibDots()
  calibText.textContent = '点击三张卡的图标中心（依次 1/2/3）'
  btnSave.disabled = true
  showHint('标定模式：点击三张卡的图标中心，然后保存', 0)
}

function exitCalibration() {
  calib.active = false
  calib.points = []
  document.body.classList.remove('calibrating')
  api.setClickThrough(true)
  hintEl.style.display = 'none'
  document.querySelectorAll('.dot').forEach((el) => el.remove())
}

function renderCalibDots() {
  document.querySelectorAll('.dot').forEach((el) => el.remove())
  calib.points.forEach((point, index) => {
    const dot = document.createElement('div')
    dot.className = 'dot'
    dot.textContent = String(index + 1)
    dot.style.left = `${point.x * window.innerWidth}px`
    dot.style.top = `${point.y * window.innerHeight}px`
    document.body.appendChild(dot)
  })
}

video.addEventListener('click', (event) => {
  if (!calib.active || calib.points.length >= 3) return
  const rect = video.getBoundingClientRect()
  const nx = (event.clientX - rect.left) / rect.width
  const ny = (event.clientY - rect.top) / rect.height
  // Icons scale with screen width; 0.072 of the width is a good default at 1080p.
  calib.points.push({ x: clamp01(nx), y: clamp01(ny), size: 0.072 })
  renderCalibDots()
  if (calib.points.length === 3) {
    btnSave.disabled = false
    calibText.textContent = '三张卡已标记，点“保存布局”'
  } else {
    calibText.textContent = `已标记 ${calib.points.length}/3，继续点击`
  }
})

btnReset.addEventListener('click', () => {
  calib.points = []
  btnSave.disabled = true
  calibText.textContent = '点击三张卡的图标中心（依次 1/2/3）'
  renderCalibDots()
})

btnSave.addEventListener('click', async () => {
  if (calib.points.length !== 3) return
  await api.saveLayout(calib.points.map((p) => ({ x: p.x, y: p.y, size: p.size })))
  state.layout = { icons: calib.points }
  state.calibrated = true
  exitCalibration()
  showHint('布局已保存。按 F2 识别。')
})

btnCancel.addEventListener('click', () => {
  exitCalibration()
})

function clamp01(value) {
  return Math.max(0, Math.min(1, value))
}

api.onCommand(async (command) => {
  if (command.type === 'capture') await doCapture()
  else if (command.type === 'calibrate') enterCalibration()
  else if (command.type === 'screenshot') await saveScreenshot()
})

api.onState((next) => {
  Object.assign(state, next)
})

async function init() {
  const initial = await api.getState()
  Object.assign(state, initial)

  if (state.mock) {
    // Mock mode does not need the capture stream at all: the main process pushes
    // a demo recommendation right after load.
    showHint('Mock 模式：1.5 秒后自动显示演示卡；F2 可重放', 0)
    return
  }

  if (!state.calibrated) {
    showHint('首次使用：按 F2 识别（会提示标定），或按 F4 先标定三张卡的位置', 0)
  } else {
    showHint('就绪：增幅卡出现后按 F2', 3000)
  }

  try {
    await startStream()
  } catch (error) {
    showHint(`屏幕捕获不可用：${error?.message ?? error}`, 0)
  }
}

init()
