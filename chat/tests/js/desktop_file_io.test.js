'use strict';
/**
 * P4 T19 (issue #146): native file selection / save / open.
 *
 * Three layers are exercised against the shipped code, not copies:
 *  1. desktop/file-io.js    - the access policy, with real bytes in a temp dir
 *  2. desktop/main.js       - the real entry point under a vm harness, so the
 *                             IPC contract (trust check, dialog-before-grant,
 *                             sequential writes, token-addressed open) is the
 *                             one that actually ships
 *  3. desktop/preload.js + static/js/file-transfer.js - the bridge and the
 *                             renderer streaming path (chunking, offsets,
 *                             failure messages)
 *
 * Native acceptance that needs an installed build and a human at the dialog is
 * NOT covered here and is listed as unverified in docs/phase4/windows-files.md.
 */
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const { after, test: nodeTest } = require('node:test');

const test = (name, callback) => nodeTest(name, { timeout: 15000 }, callback);

// Objects created inside the vm realm have a different Object.prototype, so
// deepStrictEqual would fail on the prototype. Compare the data itself.
const sameShape = (actual, expected, message) => {
  assert.deepEqual(JSON.parse(JSON.stringify(actual)), expected, message);
};

const desktopRoot = path.resolve(__dirname, '../../../desktop');
const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'ichat-file-io-test-'));
let tempCounter = 0;
const tempPath = name => path.join(tempRoot, `${String(++tempCounter).padStart(3, '0')}-${name}`);

after(() => {
  assert.equal(path.dirname(tempRoot), path.resolve(os.tmpdir()));
  assert.ok(path.basename(tempRoot).startsWith('ichat-file-io-test-'));
  fs.rmSync(tempRoot, { recursive: true, force: true, maxRetries: 3, retryDelay: 50 });
});

const io = require(path.join(desktopRoot, 'file-io.js'));
const fileTransferSource = fs.readFileSync(
  path.resolve(desktopRoot, '../static/js/file-transfer.js'), 'utf8');

// ── Layer 1: access policy ────────────────────────────────────────────────

test('peer-supplied names cannot escape the chosen directory or shadow devices', () => {
  assert.equal(io.sanitizeFileName('..\\..\\windows\\system32\\evil.txt'), 'evil.txt');
  assert.equal(io.sanitizeFileName('a/b/c/report.pdf'), 'report.pdf');
  assert.equal(io.sanitizeFileName('C:\\Users\\me\\Desktop\\doc.docx'), 'doc.docx');
  assert.equal(io.sanitizeFileName('CON.txt'), '_CON.txt');
  assert.equal(io.sanitizeFileName('lpt1'), '_lpt1');
  assert.equal(io.sanitizeFileName('invoice.exe '), 'invoice.exe');
  assert.equal(io.sanitizeFileName('report.2026.pdf...'), 'report.2026.pdf');
  assert.equal(io.sanitizeFileName('a<b>c|d?e*f.txt'), 'a_b_c_d_e_f.txt');
  assert.equal(io.sanitizeFileName(''), null);
  assert.equal(io.sanitizeFileName('   '), null);
  assert.equal(io.sanitizeFileName('..'), null);
  assert.equal(io.sanitizeFileName('.hidden'), '.hidden');
  const clamped = io.sanitizeFileName('x'.repeat(400) + '.pdf');
  assert.equal(clamped.length, 180);
  assert.ok(clamped.endsWith('.pdf'), 'the extension survives clamping: ' + clamped);
});

test('byte counts are parsed strictly instead of coerced', () => {
  assert.equal(io.toByteCount(0), 0);
  assert.equal(io.toByteCount('10'), 10);
  assert.equal(io.toByteCount(''), null, "Number('') === 0 must not become a free pass");
  assert.equal(io.toByteCount('12abc'), null);
  assert.equal(io.toByteCount(-1), null);
  assert.equal(io.toByteCount(1.5), null);
  assert.equal(io.toByteCount(Number.MAX_SAFE_INTEGER + 2), null);
  assert.equal(io.toByteCount(null), null);
  assert.equal(io.toByteCount({}), null);
});

test('save requests are validated before any dialog is shown', () => {
  const api = io.createFileIo();
  assert.deepEqual(
    api.validateSaveRequest({ fileName: 'a.txt', totalBytes: 3 }),
    { fileName: 'a.txt', totalBytes: 3 },
  );
  for (const bad of [null, [], 'x', {}, { fileName: '', totalBytes: 1 },
    { fileName: 'a.txt', totalBytes: -1 }, { fileName: 'a.txt' }]) {
    assert.throws(() => api.validateSaveRequest(bad), /invalid_payload/, JSON.stringify(bad));
  }
  assert.throws(
    () => api.validateSaveRequest({ fileName: 'a.bin', totalBytes: io.MAX_FILE_BYTES + 1 }),
    /too_large/,
  );
  // A directory is not a file target even though the dialog returned a path.
  const directory = path.join(tempRoot, 'a-directory');
  fs.mkdirSync(directory, { recursive: true });
  assert.throws(() => api.createSaveGrant({
    fileName: 'x.txt', totalBytes: 1, senderId: 1, filePath: directory,
  }), /invalid_target/);
  assert.throws(() => api.createSaveGrant({
    fileName: 'x.txt', totalBytes: 1, senderId: 1, filePath: 'relative.txt',
  }), /invalid_target/);
  assert.throws(() => api.createSaveGrant({
    fileName: 'x.txt', totalBytes: 1, senderId: 0, filePath: tempPath('x.txt'),
  }), /untrusted sender/);
});

test('chunks are appended in order and the declared total is enforced', () => {
  const api = io.createFileIo();
  const target = tempPath('ordered.bin');
  const grant = api.createSaveGrant({ fileName: 'ordered.bin', totalBytes: 5, senderId: 1, filePath: target });

  assert.deepEqual(api.writeChunk({ token: grant.token, senderId: 1, offset: 0, dataBase64: 'AAAA' }),
    { written: 3, total: 5 });
  // Skipping ahead is refused: the renderer cannot make the file sparse.
  assert.throws(() => api.writeChunk({ token: grant.token, senderId: 1, offset: 5, dataBase64: 'AAA=' }),
    /offset_mismatch/);
  // Replaying an accepted offset would duplicate bytes.
  assert.throws(() => api.writeChunk({ token: grant.token, senderId: 1, offset: 0, dataBase64: 'AAA=' }),
    /offset_mismatch/);
  assert.throws(() => api.writeChunk({ token: grant.token, senderId: 1, offset: 3, dataBase64: 'AAAAAAAAAA==' }),
    /too_large/, 'declared_total');
  assert.deepEqual(api.writeChunk({ token: grant.token, senderId: 1, offset: 3, dataBase64: 'AAA=' }),
    { written: 5, total: 5 });
  assert.throws(() => api.writeChunk({ token: grant.token, senderId: 1, offset: 5, dataBase64: 'AA==' }),
    /too_large/, 'declared_total');
  assert.deepEqual(fs.readFileSync(target), Buffer.from([0, 0, 0, 0, 0]));

  assert.deepEqual(api.sealGrant({ token: grant.token, senderId: 1 }), { fileName: 'ordered.bin', bytes: 5 });
  assert.throws(() => api.writeChunk({ token: grant.token, senderId: 1, offset: 5, dataBase64: 'AA==' }),
    /already_sealed/);
});

test('a grant is bound to one sender and can expire', () => {
  let clock = 1_000;
  const api = io.createFileIo({ now: () => clock, grantTtlMs: 500 });
  const target = tempPath('bound.txt');
  const grant = api.createSaveGrant({ fileName: 'bound.txt', totalBytes: 2, senderId: 7, filePath: target });

  assert.throws(() => api.writeChunk({ token: grant.token, senderId: 8, offset: 0, dataBase64: 'AAA=' }),
    /wrong_sender/);
  assert.throws(() => api.writeChunk({ token: 'not-a-token', senderId: 7, offset: 0, dataBase64: 'AAA=' }),
    /not_found/);
  clock += 500;
  assert.throws(() => api.writeChunk({ token: grant.token, senderId: 7, offset: 0, dataBase64: 'AAA=' }),
    /expired/);
  assert.equal(api.activeGrants(), 0, 'an expired grant is dropped, not revived');
});

test('grants are capped and malformed payloads never write', () => {
  // Two separate instances: the cap test fills the table on purpose, while the
  // payload test needs a live grant of its own.
  const capped = io.createFileIo({ maxGrants: 2 });
  for (const name of ['cap1', 'cap2']) {
    capped.createSaveGrant({ fileName: 'c.txt', totalBytes: 1, senderId: 1, filePath: tempPath(name) });
  }
  assert.throws(() => capped.createSaveGrant({
    fileName: 'g.txt', totalBytes: 1, senderId: 1, filePath: tempPath('cap3'),
  }), /too_many_grants/);

  const api = io.createFileIo();
  const grant = api.createSaveGrant({ fileName: 'd.txt', totalBytes: 4, senderId: 1, filePath: tempPath('d.txt') });
  for (const dataBase64 of ['', 'not base64!!', 'AAA', 42, null]) {
    assert.throws(
      () => api.writeChunk({ token: grant.token, senderId: 1, offset: 0, dataBase64 }),
      /invalid_payload/, JSON.stringify(dataBase64),
    );
  }
  const oversized = Buffer.alloc(io.MAX_CHUNK_BYTES + 1).toString('base64');
  assert.throws(
    () => api.writeChunk({ token: grant.token, senderId: 1, offset: 0, dataBase64: oversized }),
    /too_large/, 'chunk',
  );
});

test('a failed write does not advance the offset, so a retry lands on the same byte', () => {
  const target = tempPath('retry.bin');
  let failuresLeft = 1;
  const flaky = Object.create(fs);
  flaky.writeFileSync = (...args) => {
    if (failuresLeft > 0) {
      failuresLeft -= 1;
      const error = new Error('no space left on device');
      error.code = 'ENOSPC';
      throw error;
    }
    return fs.writeFileSync(...args);
  };
  const api = io.createFileIo({ fs: flaky });
  const grant = api.createSaveGrant({ fileName: 'retry.bin', totalBytes: 3, senderId: 1, filePath: target });

  assert.throws(() => api.writeChunk({ token: grant.token, senderId: 1, offset: 0, dataBase64: 'AAAA' }),
    (error) => error.code === 'write_failed' && error.detail === 'ENOSPC');
  assert.deepEqual(api.writeChunk({ token: grant.token, senderId: 1, offset: 0, dataBase64: 'AAAA' }),
    { written: 3, total: 3 }, 'the retry is accepted at the same offset');
  assert.deepEqual(fs.readFileSync(target), Buffer.from([0, 0, 0]));
});

test('sealing refuses a short file and opening requires a finished download', () => {
  const api = io.createFileIo();
  const grant = api.createSaveGrant({ fileName: 'short.txt', totalBytes: 4, senderId: 1, filePath: tempPath('short.txt') });
  api.writeChunk({ token: grant.token, senderId: 1, offset: 0, dataBase64: 'AAA=' });
  assert.throws(() => api.sealGrant({ token: grant.token, senderId: 1 }), /incomplete/);
  assert.throws(() => api.openTarget({ token: grant.token, senderId: 1 }), /not_sealed/);

  // An empty file is legitimate, and seal still creates it.
  const emptyPath = tempPath('empty.txt');
  const empty = api.createSaveGrant({ fileName: 'empty.txt', totalBytes: 0, senderId: 1, filePath: emptyPath });
  assert.throws(() => api.writeChunk({ token: empty.token, senderId: 1, offset: 0, dataBase64: 'AAA=' }),
    /too_large/, 'declared_total');
  assert.deepEqual(api.sealGrant({ token: empty.token, senderId: 1 }), { fileName: 'empty.txt', bytes: 0 });
  assert.equal(fs.statSync(emptyPath).size, 0);
});

test('executables and scripts are savable but never auto-opened', () => {
  // Every save keeps its grant (the "open" affordance needs one), so this test
  // needs headroom above the per-instance cap.
  const api = io.createFileIo({ maxGrants: 32 });
  const save = (name) => {
    const filePath = tempPath(name);
    const grant = api.createSaveGrant({ fileName: name, totalBytes: 2, senderId: 1, filePath });
    api.writeChunk({ token: grant.token, senderId: 1, offset: 0, dataBase64: 'AAA=' });
    api.sealGrant({ token: grant.token, senderId: 1 });
    return { token: grant.token, filePath };
  };
  const opened = [];
  for (const name of ['setup.exe', 'run.BAT', 'payload.msi', 'x.ps1', 'link.lnk']) {
    const saved = save(name);
    assert.ok(fs.existsSync(saved.filePath), name + ' is still written to disk');
    assert.throws(() => api.openTarget({ token: saved.token, senderId: 1 }), /blocked_type/, name);
    opened.push(name);
  }
  const text = save('notes.txt');
  assert.equal(api.openTarget({ token: text.token, senderId: 1 }).filePath, text.filePath);
  const vanished = save('gone.txt');
  fs.rmSync(vanished.filePath);
  assert.throws(() => api.openTarget({ token: vanished.token, senderId: 1 }), /not_found/);
  assert.equal(opened.length, 5);
});

// ── Layer 2: the real main.js IPC surface ─────────────────────────────────

const DOWNLOADS = path.join(tempRoot, 'downloads');
fs.mkdirSync(DOWNLOADS, { recursive: true });

/**
 * Same boundary as the tray/notification suites: only the OS surface, network
 * and timers are replaced. Everything below `registerFileIpc` is the shipped
 * code path, with real fs writes into the temp directory.
 */
function launch(options = {}) {
  const windows = [];
  const notifications = [];
  const handlers = new Map();
  const requests = [];
  const errors = [];
  const dialogs = [];
  const opened = [];
  let quit = false;
  let startup;
  const app = new EventEmitter();
  Object.assign(app, {
    isPackaged: false,
    quitCalls: 0,
    getPath: (key) => (key === 'downloads' ? DOWNLOADS : `/profiles/ichat-pro-desktop/${key}`),
    setPath() {},
    setName() {},
    getVersion: () => '1.0.0',
    setAboutPanelOptions() {},
    requestSingleInstanceLock: () => true,
    setAppUserModelId(id) { app.aumid = id; },
    whenReady() {
      return { then: (callback) => { startup = Promise.resolve().then(callback); return startup; } };
    },
    quit() {
      if (quit) return;
      quit = true;
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
      if (windows.every((window) => window.destroyed)) app.emit('window-all-closed');
    }
    static getAllWindows() { return windows.filter((window) => !window.destroyed); }
  }
  class Notification extends EventEmitter {
    constructor(config) { super(); this.config = config; notifications.push(this); }
    show() {}
  }
  const network = {
    get(url, arg2, arg3) {
      const callback = typeof arg2 === 'function' ? arg2 : arg3;
      const request = new EventEmitter();
      request.setTimeout = () => {};
      request.destroy = () => {};
      request.respond = (status) => callback({ statusCode: status, resume() {} });
      requests.push(request);
      queueMicrotask(() => request.respond(200));
      return request;
    },
  };
  const electron = {
    app,
    BrowserWindow: Window,
    Notification,
    ipcMain: { handle: (channel, fn) => handlers.set(channel, fn) },
    safeStorage: {},
    shell: {
      openExternal() {},
      openPath: async (target) => {
        opened.push(target);
        if (options.openPathError) return options.openPathError;
        return '';
      },
    },
    dialog: {
      showSaveDialog: async (first, second) => {
        const dialogOptions = second || first;
        dialogs.push(dialogOptions);
        if (options.holdDialog) {
          await new Promise((resolve) => { options.releaseDialog = resolve; });
        }
        if (options.dialogCancel) return { canceled: true };
        const filePath = typeof options.dialogPath === 'function'
          ? options.dialogPath(dialogOptions)
          : options.dialogPath;
        return { canceled: false, filePath };
      },
    },
    Menu: { buildFromTemplate: (value) => value, setApplicationMenu() {} },
  };
  const context = vm.createContext({
    Buffer, URL,
    console: { log() {}, error: (...args) => errors.push(args.join(' ')) },
    process: { platform: 'win32', argv: [], env: options.env || {}, resourcesPath: path.join(desktopRoot, 'build') },
    setInterval() { return 1; },
    clearInterval() {},
    setTimeout,
    clearTimeout,
  });
  const modules = new Map();
  function load(file) {
    if (modules.has(file)) return modules.get(file);
    if (file.endsWith('.json')) return JSON.parse(fs.readFileSync(path.join(desktopRoot, file), 'utf8'));
    const module = { exports: {} };
    const run = vm.runInContext(
      `(function(require, module, exports, __filename, __dirname) {\n${fs.readFileSync(path.join(desktopRoot, file), 'utf8')}\n})`,
      context, { filename: file },
    );
    run((dependency) => {
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
    app, windows, notifications, handlers, requests, errors, dialogs, opened,
    started: () => startup || Promise.resolve(),
    origin: () => (windows[0] ? new URL(windows[0].urls[0]).origin : ''),
  };
}

const frameAt = (origin, senderId = 1) => ({
  senderFrame: { url: origin + '/login/' },
  sender: { id: senderId },
});

/** Real ipcMain.handle turns synchronous throws into rejections. */
const invoke = (handler, event, payload) => Promise.resolve().then(() => handler(event, payload));

test('the file channels are registered with the narrow preload surface', async () => {
  const h = launch();
  await h.started();
  const channels = ['ichat:files:pick-save-target', 'ichat:files:write-chunk', 'ichat:files:seal', 'ichat:files:open-downloaded'];
  for (const channel of channels) assert.ok(h.handlers.get(channel), 'missing handler: ' + channel);
  // The bridge must expose exactly these and nothing path-shaped.
  const preloadSource = fs.readFileSync(path.join(desktopRoot, 'preload.js'), 'utf8');
  for (const channel of channels) assert.ok(preloadSource.includes(channel), 'preload omits ' + channel);
  for (const forbidden of ['readFile', 'readdir', 'unlink', 'rmSync', 'fs.writeFile', 'readPath']) {
    assert.ok(!preloadSource.includes(forbidden), 'preload must not expose ' + forbidden);
  }
  h.app.quit();
});

test('a trusted save request reaches the dialog with the sanitised default name', async () => {
  const target = tempPath('saved-from-dialog.txt');
  const h = launch({ dialogPath: target });
  await h.started();
  const handler = h.handlers.get('ichat:files:pick-save-target');
  const result = await invoke(handler, frameAt(h.origin()), {
    fileName: '..\\..\\invoice.pdf', totalBytes: 5,
  });
  assert.equal(result.canceled, false);
  assert.equal(result.fileName, 'invoice.pdf');
  assert.equal(typeof result.token, 'string');
  assert.equal(h.dialogs.length, 1);
  assert.equal(path.dirname(h.dialogs[0].defaultPath).toLowerCase(), DOWNLOADS.toLowerCase(),
    'the dialog defaults into the user downloads folder');
  assert.equal(path.basename(h.dialogs[0].defaultPath), 'invoice.pdf');
  assert.ok(!h.dialogs[0].filters, 'no filter that would silently append an extension');
  h.app.quit();
});

test('untrusted frames, bad payloads and oversized files never open a dialog', async () => {
  const h = launch({ dialogPath: tempPath('never.txt') });
  await h.started();
  const handler = h.handlers.get('ichat:files:pick-save-target');
  await assert.rejects(
    () => invoke(handler, { senderFrame: { url: 'https://evil.example/x' }, sender: { id: 1 } },
      { fileName: 'a.txt', totalBytes: 1 }),
    /untrusted sender/,
  );
  await assert.rejects(
    () => invoke(handler, { senderFrame: { url: 'https://evil.example/x' }, sender: { id: 1 } },
      { fileName: 'a.txt', totalBytes: 1 }),
    /untrusted sender/,
  );
  for (const payload of [null, { fileName: '', totalBytes: 1 }, { fileName: 'a.txt', totalBytes: -1 }]) {
    await assert.rejects(() => invoke(handler, frameAt(h.origin()), payload), /invalid_payload/);
  }
  await assert.rejects(
    () => invoke(handler, frameAt(h.origin()), { fileName: 'huge.bin', totalBytes: io.MAX_FILE_BYTES + 1 }),
    /too_large/,
  );
  // A frame without a sender id cannot own a grant.
  await assert.rejects(
    () => invoke(handler, { senderFrame: { url: h.origin() + '/login/' } }, { fileName: 'a.txt', totalBytes: 1 }),
    /untrusted sender/,
  );
  assert.equal(h.dialogs.length, 0, 'validation must happen before the modal dialog');
  h.app.quit();
});

test('cancelling the dialog creates no grant and writes nothing', async () => {
  const h = launch({ dialogCancel: true });
  await h.started();
  const handler = h.handlers.get('ichat:files:pick-save-target');
  sameShape(await invoke(handler, frameAt(h.origin()), { fileName: 'a.txt', totalBytes: 4 }),
    { canceled: true });
  assert.equal(h.dialogs.length, 1);
  h.app.quit();
});

test('a second dialog for the same frame is refused while the first is open', async () => {
  const options = { dialogPath: tempPath('concurrent.txt'), holdDialog: true };
  const h = launch(options);
  await h.started();
  const handler = h.handlers.get('ichat:files:pick-save-target');
  const first = invoke(handler, frameAt(h.origin()), { fileName: 'a.txt', totalBytes: 1 });
  // The first request is parked inside showSaveDialog at this point.
  while (!options.releaseDialog) await new Promise((resolve) => setImmediate(resolve));
  await assert.rejects(
    () => invoke(handler, frameAt(h.origin()), { fileName: 'b.txt', totalBytes: 1 }),
    /dialog_in_progress/,
  );
  options.releaseDialog();
  assert.equal((await first).canceled, false);
  assert.equal(h.dialogs.length, 1);

  // After that dialog closed, another save is allowed again.
  const second = invoke(handler, frameAt(h.origin()), { fileName: 'c.txt', totalBytes: 1 });
  while (!options.releaseDialog || h.dialogs.length < 2) await new Promise((resolve) => setImmediate(resolve));
  options.dialogPath = tempPath('concurrent-two.txt');
  options.releaseDialog();
  assert.equal((await second).canceled, false);
  assert.equal(h.dialogs.length, 2);
  h.app.quit();
});

test('end to end: dialog, two chunks, seal, then open the exact saved file', async () => {
  const target = tempPath('e2e-report.pdf');
  const h = launch({ dialogPath: target });
  await h.started();
  const pick = await invoke(h.handlers.get('ichat:files:pick-save-target'), frameAt(h.origin()),
    { fileName: 'e2e-report.pdf', totalBytes: 6 });
  const write = (offset, dataBase64) => invoke(h.handlers.get('ichat:files:write-chunk'),
    frameAt(h.origin()), { token: pick.token, offset, dataBase64 });

  sameShape(await write(0, 'QUJD'), { written: 3, total: 6 });
  sameShape(await write(3, 'REVG'), { written: 6, total: 6 });
  sameShape(await invoke(h.handlers.get('ichat:files:seal'), frameAt(h.origin()),
    { token: pick.token }), { fileName: 'e2e-report.pdf', bytes: 6 });

  assert.equal(fs.readFileSync(target, 'utf8'), 'ABCDEF');
  sameShape(await invoke(h.handlers.get('ichat:files:open-downloaded'), frameAt(h.origin()),
    { token: pick.token }), { opened: true, fileName: 'e2e-report.pdf' });
  assert.deepEqual(h.opened, [target], 'openPath receives the dialog path, never a renderer path');
  h.app.quit();
});

test('writes and opens are refused for other senders and for executables', async () => {
  const textTarget = tempPath('shared.txt');
  const h = launch({ dialogPath: textTarget });
  await h.started();
  const pick = await invoke(h.handlers.get('ichat:files:pick-save-target'), frameAt(h.origin(), 1),
    { fileName: 'shared.txt', totalBytes: 3 });
  // Another webContents in the same app instance cannot use the token.
  await assert.rejects(
    () => invoke(h.handlers.get('ichat:files:write-chunk'), frameAt(h.origin(), 2),
      { token: pick.token, offset: 0, dataBase64: 'QUJD' }),
    /wrong_sender/,
  );
  await invoke(h.handlers.get('ichat:files:write-chunk'), frameAt(h.origin(), 1),
    { token: pick.token, offset: 0, dataBase64: 'QUJD' });
  await invoke(h.handlers.get('ichat:files:seal'), frameAt(h.origin(), 1), { token: pick.token });

  // Same app, second save: an .exe is written but must not be executed.
  const exeTarget = tempPath('received-setup.exe');
  const h2 = launch({ dialogPath: exeTarget });
  await h2.started();
  const exe = await invoke(h2.handlers.get('ichat:files:pick-save-target'), frameAt(h2.origin()),
    { fileName: 'received-setup.exe', totalBytes: 2 });
  await invoke(h2.handlers.get('ichat:files:write-chunk'), frameAt(h2.origin()),
    { token: exe.token, offset: 0, dataBase64: 'TVo=' });
  await invoke(h2.handlers.get('ichat:files:seal'), frameAt(h2.origin()), { token: exe.token });
  assert.equal(fs.readFileSync(exeTarget, 'utf8'), 'MZ');
  await assert.rejects(
    () => invoke(h2.handlers.get('ichat:files:open-downloaded'), frameAt(h2.origin()), { token: exe.token }),
    /blocked_type/,
  );
  assert.deepEqual(h2.opened, [], 'openPath is never called for an executable');
  h.app.quit();
  h2.app.quit();
});

test('an OS-level open failure is reported instead of swallowed', async () => {
  const target = tempPath('no-association.dat');
  const h = launch({ dialogPath: target, openPathError: 'No application is associated with this file' });
  await h.started();
  const pick = await invoke(h.handlers.get('ichat:files:pick-save-target'), frameAt(h.origin()),
    { fileName: 'no-association.dat', totalBytes: 2 });
  await invoke(h.handlers.get('ichat:files:write-chunk'), frameAt(h.origin()),
    { token: pick.token, offset: 0, dataBase64: 'QUI=' });
  await invoke(h.handlers.get('ichat:files:seal'), frameAt(h.origin()), { token: pick.token });
  await assert.rejects(
    () => invoke(h.handlers.get('ichat:files:open-downloaded'), frameAt(h.origin()), { token: pick.token }),
    /open_failed/,
  );
  h.app.quit();
});

// ── Layer 3: bridge and renderer streaming ────────────────────────────────

function loadFileTransfer(bridge) {
  const exposed = {};
  const calls = [];
  const context = vm.createContext({
    Blob, Uint8Array, Promise, Object, String, Number, Math, JSON, Error, console,
    URL,
    atob: (value) => Buffer.from(value, 'base64').toString('binary'),
    btoa: (value) => Buffer.from(value, 'binary').toString('base64'),
    setTimeout,
    clearTimeout,
    window: {
      iChatDesktop: bridge,
      apiFetch: async () => ({}),
      myUserId: 1,
    },
  });
  context.window.window = context.window;
  // The module only reads window.* at call time; loading it defines the API.
  vm.runInContext(fileTransferSource, context, { filename: 'file-transfer.js' });
  const ft = context.window.iChatFileTransfer;
  return { ft, exposed, calls, context };
}

function fakeBridge(overrides = {}) {
  const calls = { pick: [], write: [], seal: [], open: [] };
  const files = {
    pickSaveTarget: async (payload) => { calls.pick.push(payload); return { canceled: false, token: 'tok-1', fileName: payload.fileName }; },
    writeChunk: async (payload) => { calls.write.push(payload); return { written: payload.offset + Buffer.from(payload.dataBase64, 'base64').length, total: 0 }; },
    seal: async (payload) => { calls.seal.push(payload); return { fileName: 'x', bytes: 0 }; },
    openDownloaded: async (payload) => { calls.open.push(payload); return { opened: true, fileName: 'x' }; },
    ...overrides,
  };
  return { bridge: { isElectron: true, files }, calls };
}

test('the browser build keeps the old path: no bridge means no native save', async () => {
  const { ft } = loadFileTransfer(undefined);
  assert.equal(ft.nativeSaveAvailable(), false);
  sameShape(await ft.saveBlobToDisk(new Blob(['abc']), { original_name: 'a.txt' }),
    { status: 'canceled', canceled: true });
  sameShape(await ft.openSavedFile('tok'), { status: 'unsupported' });
});

test('a 5 MiB blob is streamed as 2 MiB chunks with exact offsets and bytes', async () => {
  const { bridge, calls } = fakeBridge();
  const { ft } = loadFileTransfer(bridge);
  const bytes = crypto.randomBytes(5 * 1024 * 1024);
  const blob = new Blob([bytes]);
  const result = await ft.saveBlobToDisk(blob, { original_name: 'big.bin' });

  sameShape(result, { status: 'saved', token: 'tok-1', fileName: 'big.bin' });
  assert.deepEqual(JSON.parse(JSON.stringify(calls.pick)), [{ fileName: 'big.bin', totalBytes: bytes.length }]);
  assert.equal(calls.write.length, 3);
  assert.deepEqual(Array.from(calls.write, (call) => call.offset), [0, 2 * 1024 * 1024, 4 * 1024 * 1024]);
  const reassembled = Buffer.concat(calls.write.map((call) => Buffer.from(call.dataBase64, 'base64')));
  assert.equal(reassembled.length, bytes.length, 'the renderer must not drop or duplicate a byte');
  assert.ok(reassembled.equals(bytes), 'chunk bytes must round-trip unchanged');
  assert.ok(calls.write.every((call) => Buffer.from(call.dataBase64, 'base64').length <= 2 * 1024 * 1024));
  sameShape(calls.seal, [{ token: 'tok-1' }]);
});

test('cancelling the native dialog writes nothing and reports canceled', async () => {
  const { bridge, calls } = fakeBridge({ pickSaveTarget: async (payload) => { calls.pick.push(payload); return { canceled: true }; } });
  const { ft } = loadFileTransfer(bridge);
  sameShape(await ft.saveBlobToDisk(new Blob(['abc']), { original_name: 'a.txt' }),
    { status: 'canceled', canceled: true });
  assert.equal(calls.write.length, 0);
  assert.equal(calls.seal.length, 0);
});

test('main-process failures surface as messages a user can act on', async () => {
  const failure = (code) => fakeBridge({
    writeChunk: async () => { const error = new Error(code); error.code = code; throw error; },
  });
  const cases = [
    ['write_failed', /磁盘已满|被占用/],
    ['offset_mismatch', /写入位置不一致/],
    ['expired', /超时/],
    ['not_found', /凭证已失效/],
  ];
  for (const [code, expected] of cases) {
    const { ft } = loadFileTransfer(failure(code).bridge);
    await assert.rejects(() => ft.saveBlobToDisk(new Blob(['abc']), { original_name: 'a.txt' }),
      (error) => expected.test(error.message) && error.nativeCode === code, code);
  }

  const blocked = fakeBridge({ openDownloaded: async () => { const error = new Error('blocked'); error.code = 'blocked_type'; throw error; } });
  const { ft } = loadFileTransfer(blocked.bridge);
  const refused = await ft.openSavedFile('tok-1');
  assert.equal(refused.status, 'failed');
  assert.match(refused.reason, /安装包和脚本/);

  const missing = fakeBridge({ openDownloaded: async () => { const error = new Error('x'); error.code = 'not_found'; throw error; } });
  const { ft: ft2 } = loadFileTransfer(missing.bridge);
  assert.match((await ft2.openSavedFile('tok-1')).reason, /凭证已失效/);
});

test('a truncated write is rejected instead of reported as saved', async () => {
  const { bridge } = fakeBridge({
    writeChunk: async () => ({ written: 0, total: 4 }), // main never advanced
  });
  const { ft } = loadFileTransfer(bridge);
  await assert.rejects(() => ft.saveBlobToDisk(new Blob(['abcd']), { original_name: 'a.bin' }),
    /写入位置不一致/);
});
