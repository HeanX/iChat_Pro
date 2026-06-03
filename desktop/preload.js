const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('iChatDesktop', {
  isElectron: true,
  platform: process.platform,
});
