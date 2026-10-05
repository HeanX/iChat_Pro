const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const { test: nodeTest } = require('node:test');
const test = (name, callback) => nodeTest(name, { timeout: 5000 }, callback);

const desktopRoot = path.resolve(__dirname, '../../../desktop');
const archive = process.env.ICHAT_DESKTOP_TEST_ARCHIVE;
const asar = archive && require(path.join(desktopRoot, 'node_modules/@electron/asar'));
const readSource = file => archive
  ? asar.extractFile(archive, file).toString()
  : fs.readFileSync(path.join(desktopRoot, file), 'utf8');
const resourceRoot = archive ? path.dirname(archive) : path.join(desktopRoot, 'build');

// Executes the REAL entry point (same boundaries as the tray suite: only OS,
// network and timers are replaced) and captures the IPC handlers main.js
// registers, so the T18 notification contract is tested against the shipped
// code instead of a copied implementation.
function launch(options = {}) {
  const windows = [];
  const notifications = [];
  const handlers = new Map();
  const requests = [];
  const errors = [];
  let nextTimer = 1;
  let quitting = false;
  let startup;
  const app = new EventEmitter();
  Object.assign(app, {
    isPackaged: Boolean(archive),
    quitCalls: 0,
    getPath: key => `/profiles/ichat-pro-desktop/${key}`,
    setPath() {},
    setName() {},
    getVersion: () => '1.0.0',
    setAboutPanelOptions() {},
    requestSingleInstanceLock: () => true,
    whenReady() {
      return { then: callback => { startup = Promise.resolve().then(callback); return startup; } };
    },
    quit() {
      if (quitting) return;
      quitting = true;
      app.quitCalls += 1;
      app.emit('before-quit');
      for (const window of windows) window.close();
    },
  });
  class Window extends EventEmitter {
    constructor(config) {
      super();
      windows.push(this);
      this.config = config;
      this.visible = true;
      this.destroyed = false;
      this.urls = [];
      this.sent = [];
      this.webContents = new EventEmitter();
      this.webContents.send = (channel, data) => this.sent.push([channel, data]);
      this.webContents.setWindowOpenHandler = () => {};
      this.webContents.executeJavaScript = () => Promise.resolve();
      this.webContents.openDevTools = () => {};
    }
    loadURL(url) { this.urls.push(url); return Promise.resolve(); }
    loadFile() { return Promise.resolve(); }
    isDestroyed() { return this.destroyed; }
    isMinimized() { return false; }
    hide() { this.visible = false; }
    show() { this.visible = true; }
    focus() {}
    restore() {}
    close() {
      if (this.destroyed) return;
      this.destroyed = true;
      this.emit('closed');
      if (windows.every(window => window.destroyed)) app.emit('window-all-closed');
    }
    static getAllWindows() { return windows.filter(window => !window.destroyed); }
  }
  class Notification extends EventEmitter {
    constructor(config) {
      super();
      this.config = config;
      this.showCalls = 0;
      notifications.push(this);
    }
    show() { this.showCalls += 1; }
  }
  const network = {
    get(url, arg2, arg3) {
      const callback = typeof arg2 === 'function' ? arg2 : arg3;
      const request = new EventEmitter();
      request.setTimeout = () => {};
      request.destroy = () => {};
      request.respond = status => callback({ statusCode: status, resume() {} });
      requests.push(request);
      if (!options.holdNetwork) queueMicrotask(() => request.respond(200));
      return request;
    },
  };
  const electron = {
    app, BrowserWindow: Window, Notification,
    ipcMain: { handle: (channel, fn) => handlers.set(channel, fn) },
    safeStorage: {}, shell: {},
    Menu: { buildFromTemplate: value => value, setApplicationMenu() {} },
  };
  const context = vm.createContext({
    Buffer, URL,
    console: { log() {}, error: (...args) => errors.push(args.join(' ')) },
    process: { platform: 'win32', argv: [], env: options.env || {}, resourcesPath: resourceRoot },
    setInterval() { return nextTimer++; },
    clearInterval() {},
    setTimeout, clearTimeout,
  });
  const modules = new Map();
  function load(file) {
    if (modules.has(file)) return modules.get(file);
    if (file.endsWith('.json')) return JSON.parse(readSource(file));
    const module = { exports: {} };
    const run = vm.runInContext(`(function(require, module, exports, __filename, __dirname) {\n${readSource(file)}\n})`, context, { filename: file });
    run(dependency => {
      if (dependency === 'electron') return electron;
      if (dependency.startsWith('./')) {
        const relative = dependency.slice(2);
        return load(relative.includes('.') ? relative : `${relative}.js`);
      }
      if (dependency === 'http' || dependency === 'https') return network;
      return require(dependency);
    }, module, module.exports, path.join(desktopRoot, file), desktopRoot);
    modules.set(file, module.exports);
    return module.exports;
  }
  load('main.js');
  return {
    app, windows, notifications, handlers, requests, errors,
    started: () => startup || Promise.resolve(),
    appOrigin: () => windows[0] ? new URL(windows[0].urls[0]).origin : '',
  };
}

const VALID = { conversationId: 7, conversationType: 'single', title: 'Alice', body: 'hi' };
const trusted = origin => ({ senderFrame: { url: origin + '/login/' } });

test('preload bridge exposes the narrow notification API and forwards clicks', () => {
  const exposed = {};
  const registered = [];
  let invokePayload;
  const ipcRenderer = {
    invoke: async (channel, payload) => { invokePayload = [channel, payload]; return true; },
    on: (channel, callback) => registered.push([channel, callback]),
  };
  const context = vm.createContext({
    console,
    process: { platform: 'win32' },
    require: dependency => dependency === 'electron' ? ({
      contextBridge: { exposeInMainWorld: (name, api) => { exposed[name] = api; } },
      ipcRenderer,
    }) : require(dependency),
  });
  vm.runInContext(readSource('preload.js'), context, { filename: 'preload.js' });

  const bridge = exposed.iChatDesktop;
  assert.ok(bridge.notifications, 'notifications namespace missing');
  // Functions come from the vm realm: check typeof, not instanceof Function.
  assert.equal(typeof bridge.notifications.show, 'function');
  assert.equal(typeof bridge.notifications.onClicked, 'function');
  assert.ok(bridge.__channels.includes('ichat:notifications:show'));

  bridge.notifications.show(VALID);
  assert.deepEqual(invokePayload, ['ichat:notifications:show', VALID]);

  const clicks = [];
  bridge.notifications.onClicked(payload => clicks.push(payload));
  assert.equal(registered.length, 1);
  assert.equal(registered[0][0], 'ichat:notifications:clicked');
  registered[0][1]('ichat:notifications:clicked', { conversationId: 7 });
  assert.deepEqual(clicks, [{ conversationId: 7 }]);

  assert.throws(() => bridge.notifications.onClicked('nope'), /callback required/);
});

test('valid payload from the app frame shows a branded notification', async () => {
  const h = launch();
  await h.started();
  const handler = h.handlers.get('ichat:notifications:show');
  assert.ok(handler, 'notification IPC handler not registered');
  const result = await handler(trusted(h.appOrigin()), VALID);
  assert.equal(result, true);
  assert.equal(h.notifications.length, 1);
  assert.equal(h.notifications[0].config.title, 'Alice');
  assert.equal(h.notifications[0].config.body, 'hi');
  assert.equal(h.notifications[0].config.icon,
    archive ? path.join(resourceRoot, 'branding/icon.ico') : path.join(resourceRoot, 'icon.ico'));
  assert.equal(h.notifications[0].showCalls, 1);
  h.app.quit();
});

test('untrusted senders and malformed payloads are rejected', async () => {
  const h = launch();
  await h.started();
  const handler = h.handlers.get('ichat:notifications:show');
  // Real ipcMain.handle turns synchronous throws into rejections.
  const invoke = (event, payload) => Promise.resolve().then(() => handler(event, payload));
  await assert.rejects(
    () => invoke({ senderFrame: { url: 'https://evil.example/login/' } }, VALID),
    /untrusted sender/,
  );
  for (const bad of [
    null,
    { ...VALID, conversationId: 'seven' },
    { ...VALID, conversationId: 0 },
    { ...VALID, conversationType: 'channel' },
    { ...VALID, title: '' },
    { ...VALID, body: 42 },
  ]) {
    await assert.rejects(() => invoke(trusted(h.appOrigin()), bad), /invalid payload/, JSON.stringify(bad));
  }
  assert.equal(h.notifications.length, 0);
  h.app.quit();
});

test('long titles and bodies are clamped, not rejected', async () => {
  const h = launch();
  await h.started();
  const handler = h.handlers.get('ichat:notifications:show');
  await handler(trusted(h.appOrigin()), {
    ...VALID,
    title: 'T'.repeat(500),
    body: 'B'.repeat(1000),
  });
  assert.equal(h.notifications[0].config.title.length, 80);
  assert.equal(h.notifications[0].config.body.length, 200);
  h.app.quit();
});

test('clicking the toast restores the same window and routes the conversation', async () => {
  const h = launch();
  await h.started();
  const handler = h.handlers.get('ichat:notifications:show');
  await handler(trusted(h.appOrigin()), { ...VALID, conversationId: 12, conversationType: 'group' });
  const [toast] = h.notifications;
  const [window] = h.windows;
  window.hide();
  assert.equal(window.visible, false);
  toast.emit('click');
  assert.equal(window.visible, true, 'click must restore the window');
  // The payload is created inside the vm realm: compare fields, not prototypes.
  assert.equal(window.sent.length, 1);
  assert.equal(window.sent[0][0], 'ichat:notifications:clicked');
  assert.equal(window.sent[0][1].conversationId, 12);
  assert.equal(window.sent[0][1].conversationType, 'group');
  assert.equal(window.urls.length, 1, 'restoring must not reload the renderer');
  h.app.quit();
});

test('while quitting or without a window nothing is shown', async () => {
  const h = launch({ holdNetwork: true });
  // Handlers register before the origin probe; the probe is held pending, so
  // flush instead of awaiting startup.
  await new Promise(resolve => setImmediate(resolve));
  const handler = h.handlers.get('ichat:notifications:show');
  assert.ok(handler, 'notification IPC handler not registered');
  // file:// frames are trusted regardless of the configured cloud origin.
  assert.equal(await handler({ senderFrame: { url: 'file:///C:/app/resources/index.html' } }, VALID), false);
  assert.equal(h.notifications.length, 0);
  h.requests.forEach(request => request.respond(200));
});

test('secure-storage channels stay registered alongside the notification handler', async () => {
  const h = launch();
  await h.started();
  for (const channel of [
    'ichat:secure-storage:is-available',
    'ichat:secure-storage:encrypt',
    'ichat:secure-storage:decrypt',
    'ichat:notifications:show',
  ]) {
    assert.ok(h.handlers.get(channel), `missing handler: ${channel}`);
  }
  h.app.quit();
});
