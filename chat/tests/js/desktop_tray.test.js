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

// Execute the real entry point and its shipped configuration/branding modules.
// Only OS, network and timers are replaced: missing imports, bad resource paths,
// startup ordering and event wiring cannot be hidden by a copied implementation.
function launch(options = {}) {
  const windows = [];
  const trays = [];
  const intervals = new Map();
  const requests = [];
  const children = [];
  const errors = [];
  const order = [];
  let nextTimer = 1;
  let quitting = false;
  let startup;
  const app = new EventEmitter();
  Object.assign(app, {
    isPackaged: Boolean(archive),
    quitCalls: 0,
    readyCalls: 0,
    getPath: key => `/profiles/ichat-pro-desktop/${key}`,
    setPath() {},
    setName() {},
    getVersion: () => '1.0.0',
    setAboutPanelOptions() {},
    requestSingleInstanceLock() {
      order.push('lock');
      return options.lock !== false;
    },
    whenReady() {
      app.readyCalls += 1;
      order.push('ready');
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
      order.push('window');
      windows.push(this);
      this.config = config;
      this.visible = true;
      this.minimized = false;
      this.destroyed = false;
      this.urls = [];
      this.files = [];
      this.executions = [];
      this.webContents = new EventEmitter();
      this.webContents.setWindowOpenHandler = () => {};
      this.webContents.executeJavaScript = code => {
        this.executions.push(code);
        return Promise.resolve();
      };
      this.webContents.openDevTools = () => {};
    }
    loadURL(url) {
      this.urls.push(url);
      if (options.holdReload && this.urls.length > 1) {
        return new Promise((resolve, reject) => { this.failReload = reject; });
      }
      return Promise.resolve();
    }
    loadFile(file) { this.files.push(file); return Promise.resolve(); }
    isDestroyed() { return this.destroyed; }
    isMinimized() { return this.minimized; }
    hide() { this.visible = false; }
    show() { this.visible = true; }
    focus() { this.focused = true; }
    restore() { this.minimized = false; }
    minimize() {
      let prevented = false;
      this.emit('minimize', { preventDefault() { prevented = true; } });
      if (!prevented) this.minimized = true;
      return prevented;
    }
    close() {
      if (this.destroyed) return;
      this.destroyed = true;
      this.emit('closed');
      if (windows.every(window => window.destroyed)) app.emit('window-all-closed');
    }
    static getAllWindows() { return windows.filter(window => !window.destroyed); }
  }
  class Tray extends EventEmitter {
    constructor(icon) {
      super();
      if (options.trayFailure === 'construct') throw new Error('OS tray unavailable');
      this.icon = icon;
      this.destroyCalls = 0;
      trays.push(this);
    }
    setToolTip(value) { this.tooltip = value; }
    setContextMenu(value) {
      if (options.trayFailure === 'menu') throw new Error('OS menu unavailable');
      this.menu = value;
    }
    destroy() { this.destroyCalls += 1; }
  }
  const network = {
    get(url, arg2, arg3) {
      order.push('probe');
      const callback = typeof arg2 === 'function' ? arg2 : arg3;
      const request = new EventEmitter();
      request.url = String(url);
      request.setTimeout = () => {};
      request.destroy = () => {};
      request.respond = status => callback({ statusCode: status, resume() {} });
      requests.push(request);
      if (!options.holdNetwork) queueMicrotask(() => request.respond(options.status || 200));
      return request;
    },
  };
  const electron = {
    app, BrowserWindow: Window, Tray,
    ipcMain: { handle() {} }, safeStorage: {}, shell: {},
    Menu: { buildFromTemplate: value => value, setApplicationMenu() {} },
  };
  const context = vm.createContext({
    Buffer, URL,
    console: { log() {}, error: (...args) => errors.push(args.join(' ')) },
    process: {
      platform: 'win32', argv: options.argv || [], env: options.env || {},
      resourcesPath: resourceRoot,
    },
    setInterval(callback, delay) {
      const id = nextTimer++;
      intervals.set(id, { callback, delay });
      return id;
    },
    clearInterval: id => intervals.delete(id),
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
      if (dependency === 'fs' && options.iconMissing) return { existsSync: () => false };
      if (dependency === 'child_process') return {
        spawn(command, args) {
          order.push('spawn');
          const child = new EventEmitter();
          Object.assign(child, { command, args, pid: 12345, stdout: new EventEmitter(), stderr: new EventEmitter() });
          children.push(child);
          return child;
        },
      };
      return require(dependency);
    }, module, module.exports, path.join(desktopRoot, file), desktopRoot);
    modules.set(file, module.exports);
    return module.exports;
  }
  load('main.js');
  return { app, windows, trays, intervals, requests, children, errors, order,
    started: () => startup || Promise.resolve(),
    flush: () => new Promise(resolve => setImmediate(resolve)),
  };
}

test('online entry point creates one branded tray from a real icon', async () => {
  const h = launch();
  await h.started();
  assert.equal(h.trays.length, 1, h.errors.join('\n'));
  assert.ok(fs.existsSync(h.trays[0].icon));
  assert.equal(h.trays[0].icon, archive
    ? path.join(resourceRoot, 'branding/icon.ico') : path.join(resourceRoot, 'icon.ico'));
  assert.equal(h.trays[0].tooltip, 'iChat Pro');
  assert.equal(h.windows[0].config.webPreferences.contextIsolation, true);
  assert.equal(h.windows[0].config.webPreferences.sandbox, true);
  assert.equal(h.order[0], 'lock');
  assert.equal(h.children.length, 0);
  h.app.quit();
});

test('click, double click and menu restore the same page through repeated minimize cycles', async () => {
  const h = launch();
  await h.started();
  const [window] = h.windows;
  const [tray] = h.trays;
  assert.ok(tray, h.errors.join('\n'));
  const outbox = window.webContents.outbox = { pending: 'offline-message' };
  for (const restore of [() => tray.emit('click'), () => tray.emit('double-click'), () => tray.menu[0].click()]) {
    assert.equal(window.minimize(), true);
    assert.equal(window.visible, false);
    restore();
    assert.equal(window.visible, true);
    assert.equal(window.webContents.outbox, outbox);
    assert.equal(h.windows.length, 1);
    assert.equal(h.trays.length, 1);
    assert.equal(window.urls.length, 1, 'Restoring must not reload the renderer');
  }
  h.app.quit();
});

test('X closes the window, destroys the tray and clears all periodic watches', async () => {
  const h = launch();
  await h.started();
  assert.equal(h.intervals.size, 1);
  h.windows[0].close();
  assert.equal(h.app.quitCalls, 1);
  assert.equal(h.trays[0].destroyCalls, 1);
  assert.equal(h.intervals.size, 0);
  h.app.emit('second-instance');
  assert.equal(h.windows.length, 1, 'Quitting must not resurrect a window');
});

test('tray exit while hidden destroys the same window and leaves no timers', async () => {
  const h = launch();
  await h.started();
  assert.ok(h.trays[0], h.errors.join('\n'));
  h.windows[0].minimize();
  h.trays[0].menu.find(item => item.label === '退出').click();
  assert.equal(h.windows[0].destroyed, true);
  assert.equal(h.trays[0].destroyCalls, 1);
  assert.equal(h.intervals.size, 0);
});

test('cloud offline launch still owns one tray and restores its offline window', async () => {
  const h = launch({ status: 503 });
  await h.started();
  assert.equal(h.trays.length, 1, h.errors.join('\n'));
  assert.equal(h.windows[0].files.length, 1);
  assert.equal(h.intervals.size, 2);
  h.windows[0].minimize();
  h.app.emit('second-instance');
  assert.equal(h.windows[0].visible, true);
  assert.equal(h.windows.length, 1);
  h.app.quit();
  assert.equal(h.intervals.size, 0);
});

test('unconfigured launch still gets a tray without starting probes or Django', async () => {
  const h = launch({ env: { ICHAT_SERVER_URL: 'http://invalid.example' } });
  await h.started();
  assert.equal(h.trays.length, 1, h.errors.join('\n'));
  assert.equal(h.windows[0].files.length, 1);
  assert.equal(h.requests.length, 0);
  assert.equal(h.children.length, 0);
  assert.equal(h.intervals.size, 0);
  h.app.quit();
});

test('all secondary launch modes quit before ready, network, window or Django', async () => {
  for (const options of [{}, { status: 503 }, { env: { ICHAT_SERVER_URL: 'invalid' } }, { argv: ['--dev'] }]) {
    const h = launch({ ...options, lock: false });
    await h.started();
    assert.equal(h.app.quitCalls, 1);
    assert.equal(h.app.readyCalls, 0);
    assert.equal(h.windows.length, 0);
    assert.equal(h.requests.length, 0);
    assert.equal(h.children.length, 0);
    assert.equal(h.trays.length, 0);
  }
});

test('second launch during an unfinished probe cannot create an extra window', async () => {
  const h = launch({ holdNetwork: true });
  await h.flush();
  h.app.emit('second-instance');
  assert.equal(h.windows.length, 0);
  h.requests[0].respond(200);
  await h.started();
  h.windows[0].minimize();
  h.app.emit('second-instance');
  assert.equal(h.windows[0].visible, true);
  assert.equal(h.windows.length, 1);
  assert.equal(h.trays.length, 1);
  h.app.quit();
});

test('missing icon and OS tray failure keep normal taskbar minimize available', async () => {
  for (const options of [{ iconMissing: true }, { trayFailure: 'construct' }]) {
    const h = launch(options);
    await h.started();
    assert.equal(h.trays.length, 0);
    assert.equal(h.windows[0].visible, true);
    assert.equal(h.windows[0].minimize(), false);
    assert.equal(h.windows[0].minimized, true);
    h.windows[0].restore();
    h.windows[0].close();
    assert.equal(h.app.quitCalls, 1);
  }
});

test('partially constructed tray is destroyed if menu setup fails', async () => {
  const h = launch({ trayFailure: 'menu' });
  await h.started();
  assert.equal(h.trays.length, 1);
  assert.equal(h.trays[0].destroyCalls, 1);
  assert.equal(h.windows[0].minimize(), false);
  h.app.quit();
  assert.equal(h.trays[0].destroyCalls, 1);
});

test('late startup probe after quit cannot create a window or tray', async () => {
  const h = launch({ holdNetwork: true });
  await h.flush();
  h.app.quit();
  h.requests[0].respond(200);
  await h.started();
  assert.equal(h.windows.length, 0);
  assert.equal(h.trays.length, 0);
  assert.equal(h.intervals.size, 0);
});

test('late watch response after quit cannot touch the renderer', async () => {
  const h = launch({ holdNetwork: true });
  await h.flush();
  h.requests[0].respond(200);
  await h.started();
  const watching = [...h.intervals.values()][0].callback();
  h.app.quit();
  h.requests[1].respond(503);
  await watching;
  assert.equal(h.windows[0].executions.length, 0);
  assert.equal(h.intervals.size, 0);
});

test('offline recovery retains its existing connectivity watch until exit', async () => {
  const h = launch({ status: 503 });
  await h.started();
  const watch = [...h.intervals.entries()].find(([, timer]) => timer.delay === 30000)[0];
  const poll = [...h.intervals.values()].find(timer => timer.delay === 5000);
  const polling = poll.callback();
  h.requests.at(-1).respond(200);
  await polling;
  assert.equal(h.intervals.size, 1);
  assert.ok(h.intervals.has(watch), 'Do not lose the live watch handle on recovery');
  h.app.quit();
  assert.equal(h.intervals.size, 0);
});

test('explicit dev exits with Django process-tree cleanup', async () => {
  const h = launch({ argv: ['--dev'] });
  await h.started();
  assert.equal(h.order[0], 'lock');
  assert.equal(h.children.length, 1);
  assert.equal(h.trays.length, 1, h.errors.join('\n'));
  h.app.quit();
  assert.equal(h.children[1].command, 'taskkill');
  assert.deepEqual(Array.from(h.children[1].args), ['/pid', '12345', '/f', '/t']);
});

test('a failed recovery navigation after quit cannot restart offline polling', async () => {
  const h = launch({ status: 503, holdReload: true });
  await h.started();
  const poll = [...h.intervals.values()].find(timer => timer.delay === 5000);
  const polling = poll.callback();
  h.requests.at(-1).respond(200);
  await polling;
  assert.equal(h.windows[0].urls.length, 2);
  h.app.quit();
  h.windows[0].failReload(new Error('Window was closed'));
  await h.flush();
  assert.equal(h.intervals.size, 0, 'A late loadURL rejection must not restart timers');
});
