const { contextBridge } = require('electron');

// Expose a minimal, safe API to the renderer process.
// Currently iChat Pro runs entirely in the browser context and does not
// require Electron-specific APIs.  This bridge can be extended later
// for native notifications, file-system access, or auto-update.
contextBridge.exposeInMainWorld('iChatDesktop', {
  platform: process.platform,
  isElectron: true,
});
