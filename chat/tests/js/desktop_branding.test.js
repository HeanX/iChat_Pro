const assert = require('node:assert/strict');
const path = require('node:path');
const { test } = require('node:test');
const { configureBranding } = require('../../../desktop/app-branding');

test('display branding preserves existing user-data and session paths', () => {
  const overrides = new Map();
  let name = 'ichat-pro-desktop';
  let aboutOptions;
  let template;
  let aboutCalls = 0;
  const app = {
    isPackaged: false,
    getPath: key => overrides.get(key) || path.join('/profiles', key, name),
    setPath: (key, value) => overrides.set(key, value),
    setName: value => { name = value; },
    getVersion: () => '2.3.4',
    setAboutPanelOptions: value => { aboutOptions = value; },
    showAboutPanel: () => { aboutCalls += 1; },
  };
  const Menu = {
    buildFromTemplate: value => { template = value; return value; },
    setApplicationMenu: value => assert.equal(value, template),
  };
  const originalUserData = app.getPath('userData');
  const originalSessionData = app.getPath('sessionData');
  configureBranding(app, Menu);
  assert.equal(name, 'iChat Pro');
  assert.equal(app.getPath('userData'), originalUserData);
  assert.equal(app.getPath('sessionData'), originalSessionData);
  assert.equal(aboutOptions.applicationName, 'iChat Pro');
  assert.equal(aboutOptions.applicationVersion, '2.3.4', 'Use the runtime version after upgrades');
  const about = template.find(item => item.label === '帮助').submenu[0];
  assert.equal(about.label, '关于 iChat Pro');
  about.click();
  assert.equal(aboutCalls, 1, 'The visible menu entry must open the native About panel');
});
