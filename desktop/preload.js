const { contextBridge, ipcRenderer } = require('electron');

// T21: narrow bridge over Electron safeStorage (DPAPI on Windows) for
// encrypting key material at rest. Sender validation happens in main.
const INVOKE_CHANNELS = [
  'ichat:secure-storage:is-available',
  'ichat:secure-storage:encrypt',
  'ichat:secure-storage:decrypt',
];

contextBridge.exposeInMainWorld('iChatDesktop', {
  isElectron: true,
  platform: process.platform,
  secureStorage: {
    isAvailable: () => ipcRenderer.invoke('ichat:secure-storage:is-available'),
    encrypt: (plainText) => ipcRenderer.invoke('ichat:secure-storage:encrypt', String(plainText)),
    decrypt: (cipherB64) => ipcRenderer.invoke('ichat:secure-storage:decrypt', String(cipherB64)),
  },
  __channels: INVOKE_CHANNELS,
});
