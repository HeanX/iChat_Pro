const { contextBridge, ipcRenderer } = require('electron');

// T21: narrow bridge over Electron safeStorage (DPAPI on Windows) for
// encrypting key material at rest. Sender validation happens in main.
const INVOKE_CHANNELS = [
  'ichat:secure-storage:is-available',
  'ichat:secure-storage:encrypt',
  'ichat:secure-storage:decrypt',
  'ichat:notifications:show',
  // T19: native file save/open. Note what is absent on purpose: the renderer
  // cannot name a path, cannot read a file back, and cannot delete one.
  'ichat:files:pick-save-target',
  'ichat:files:write-chunk',
  'ichat:files:seal',
  'ichat:files:release',
  'ichat:files:open-downloaded',
];

contextBridge.exposeInMainWorld('iChatDesktop', {
  isElectron: true,
  platform: process.platform,
  secureStorage: {
    isAvailable: () => ipcRenderer.invoke('ichat:secure-storage:is-available'),
    encrypt: (plainText) => ipcRenderer.invoke('ichat:secure-storage:encrypt', String(plainText)),
    decrypt: (cipherB64) => ipcRenderer.invoke('ichat:secure-storage:decrypt', String(cipherB64)),
  },
  // T18: incoming-message notifications. The renderer only asks the main
  // process to show one; clicks come back with the conversation target.
  notifications: {
    show: (payload) => ipcRenderer.invoke('ichat:notifications:show', payload),
    onClicked: (callback) => {
      if (typeof callback !== 'function') throw new TypeError('callback required');
      ipcRenderer.on('ichat:notifications:clicked', (_event, data) => {
        try {
          callback(data);
        } catch (err) {
          console.error('[iChatDesktop] notification click handler failed:', err);
        }
      });
    },
  },
  // T19: decrypted bytes go to disk only through a save dialog the user
  // confirmed. Base64 is the wire form because a Blob cannot cross
  // contextBridge; main decodes, enforces the cap and appends sequentially.
  files: {
    pickSaveTarget: (payload) => ipcRenderer.invoke('ichat:files:pick-save-target', payload),
    writeChunk: (payload) => ipcRenderer.invoke('ichat:files:write-chunk', payload),
    seal: (payload) => ipcRenderer.invoke('ichat:files:seal', payload),
    release: (payload) => ipcRenderer.invoke('ichat:files:release', payload),
    openDownloaded: (payload) => ipcRenderer.invoke('ichat:files:open-downloaded', payload),
  },
  __channels: INVOKE_CHANNELS,
});
