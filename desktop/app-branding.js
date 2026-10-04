const path = require('path');

const PRODUCT_NAME = 'iChat Pro';

function getBrandIconPath(app, extension = 'ico') {
  const resourceRoot = app.isPackaged
    ? path.join(process.resourcesPath, 'branding')
    : path.join(__dirname, 'build');
  return path.join(resourceRoot, `icon.${extension}`);
}

function configureBranding(app, Menu) {
  // Native About window captions use app.name. Preserve the existing profile
  // and Chromium session paths before changing that display name.
  const userData = app.getPath('userData');
  const sessionData = app.getPath('sessionData');
  app.setName(PRODUCT_NAME);
  app.setPath('userData', userData);
  app.setPath('sessionData', sessionData);
  app.setAboutPanelOptions({
    applicationName: PRODUCT_NAME,
    applicationVersion: app.getVersion(),
    copyright: 'Copyright © 2026 iChat Pro Team',
    iconPath: getBrandIconPath(app, 'png'),
  });
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    ...(process.platform === 'darwin' ? [{ role: 'appMenu' }] : []),
    { role: 'fileMenu' },
    { role: 'editMenu' },
    { role: 'viewMenu' },
    { role: 'windowMenu' },
    {
      label: '帮助',
      submenu: [{
        label: `关于 ${PRODUCT_NAME}`,
        accelerator: 'CmdOrCtrl+Alt+A',
        click: () => app.showAboutPanel(),
      }],
    },
  ]));
}

module.exports = { PRODUCT_NAME, configureBranding, getBrandIconPath };
