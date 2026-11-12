const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('overlay', {
  getState: () => ipcRenderer.invoke('get-state'),
  getSourceId: () => ipcRenderer.invoke('source-id'),
  sendCrops: (crops) => ipcRenderer.invoke('crops', { crops }),
  saveLayout: (points) => ipcRenderer.invoke('save-layout', points),
  setClickThrough: (enabled) => ipcRenderer.invoke('set-click-through', enabled),
  saveScreenshot: (dataUrl) => ipcRenderer.invoke('screenshot', dataUrl),
  onCommand: (handler) => {
    const listener = (_event, payload) => handler(payload)
    ipcRenderer.on('cmd', listener)
    return () => ipcRenderer.removeListener('cmd', listener)
  },
  onState: (handler) => {
    const listener = (_event, payload) => handler(payload)
    ipcRenderer.on('state', listener)
    return () => ipcRenderer.removeListener('state', listener)
  },
})
