const { contextBridge, ipcRenderer } = require('electron');

// T21: narrow bridge over Electron safeStorage (DPAPI on Windows) for
// encrypting key material at rest. Sender validation happens in main.
const INVOKE_CHANNELS = [
  'ichat:secure-storage:is-available',
  'ichat:secure-storage:encrypt',
  'ichat:secure-storage:decrypt',
  'ichat:notifications:show',
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
  __channels: INVOKE_CHANNELS,
});
