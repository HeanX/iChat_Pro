// Real Chromium layout/focus regression using the Electron already installed
// for the desktop build. Only a disposable profile and a mocked API are used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { createServer } = require('node:http');
const sourceArg = process.argv.indexOf('--source-root');
const root = sourceArg === -1 ? path.resolve(__dirname, '../..') : path.resolve(process.argv[sourceArg + 1]);

if (!process.versions.electron) {
  const env = { ...process.env };
  delete env.ELECTRON_RUN_AS_NODE;
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'ichat-sidebar-layout-'));
  env.ICHAT_LAYOUT_TEST_PROFILE = profile;
  const result = spawnSync(require('electron'), [__filename, '--renderer', ...process.argv.slice(2)], {
    env, encoding: 'utf8', timeout: 60000, maxBuffer: 1024 * 1024,
  });
  process.stdout.write(result.stdout || '');
  process.stderr.write(result.stderr || '');
  if (result.error) console.error(result.error);
  assert.equal(path.dirname(profile), path.resolve(os.tmpdir()));
  assert.ok(path.basename(profile).startsWith('ichat-sidebar-layout-'));
  fs.rmSync(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
  process.exit(result.status === 0 && !result.error ? 0 : 1);
}

const { app, BrowserWindow } = require('electron');
const read = name => fs.readFileSync(path.join(root, name), 'utf8');
const between = (source, start, end) => {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a, `production markup/function anchors: ${start}`);
  return source.slice(a, b);
};
const sidebar = read('templates/components/sidebar.html');
const chatHeader = between(sidebar, '<div id="sidebar-chat-header"', '<!-- Scrollable Conversations List -->');
const notificationView = between(sidebar, '<div id="sidebar-view-notifications"', '<div id="sidebar-view-data-storage"')
  .replace("{% include 'pages/notifications.html' %}", read('templates/pages/notifications.html'));
const navigation = between(read('static/js/chat.js'), 'function navigateSidebar(viewName) {', '// Backward-compatible wrappers');
const apiStub = `
  window.iChatDesktop = { notifications: {} };
  window.layoutWrites = [];
  const values = { display_notifications: true, private_chat_notifications: true,
    group_chat_notifications: true, message_preview_private: true, message_preview_group: true };
  window.fetch = async (url, options) => ({ ok: true, json: async () => {
    if (options && options.body) {
      const changed = JSON.parse(options.body);
      layoutWrites.push(changed);
      Object.assign(values, changed);
    }
    return { ...values };
  }});
`;
let fixture = `<!doctype html><html><head><meta charset="utf-8">
  <script>${apiStub}</script>
  <style>${read('static/css/tailwind.css')}</style>
  <style>${read('static/css/base.css')}</style>
  <style>${read('static/css/chat.css')}</style>
  </head><body><div class="chat-layout">
  <div id="sidebar-container" class="w-full md:w-[420px] flex flex-col h-full relative flex-shrink-0">
  <div id="sidebar-chat-view" class="h-full flex flex-col">${chatHeader}
  <div id="sidebar-chat-list" class="flex-1 overflow-y-auto">Test conversation</div></div>
  ${notificationView}</div><div id="chat-window-container" class="flex-1">Chat</div></div>
  <script>var lastSidebarView = 'chat';${navigation}</script>
  <script>${read('static/js/event-dispatcher.js')}</script></body></html>`;
fixture = fixture.replace(/\{%[\s\S]*?%\}/g, '').replace(/\{\{[\s\S]*?\}\}/g, '')
  .replace(/<i\b[^>]*data-lucide=[^>]*><\/i>/g,
    '<svg width="20" height="20"><path d="M2 5h16M2 10h16M2 15h16" stroke="currentColor"/></svg>');

assert.ok(process.env.ICHAT_LAYOUT_TEST_PROFILE, 'launch this test through Node');
app.setPath('userData', process.env.ICHAT_LAYOUT_TEST_PROFILE);
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const until = async condition => {
  const deadline = Date.now() + 3000;
  while (Date.now() < deadline) {
    if (await condition()) return;
    await pause(20);
  }
  assert.fail('renderer setting/save did not complete within 3 seconds');
};
let window;
let server;
const evaluate = async (fn, ...args) => {
  try { return await window.webContents.executeJavaScript(`(${fn.toString()})(...${JSON.stringify(args)})`); }
  catch (error) { console.error('Renderer evaluation failed:', fn.toString(), args); throw error; }
};
function snapshot() {
  const rect = el => {
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, width: r.width, height: r.height, right: r.right, bottom: r.bottom };
  };
  const sidebar = document.getElementById('sidebar-container');
  const view = document.getElementById('sidebar-view-notifications');
  return {
    outerScroll: sidebar.scrollTop, sidebar: rect(sidebar), view: rect(view),
    settingsHeader: rect(view.querySelector('.settings-view-header')),
    chat: rect(document.getElementById('sidebar-chat-view')),
    chatHeader: rect(document.getElementById('sidebar-chat-header')),
    menu: rect(document.getElementById('drawer-btn')),
    search: rect(document.getElementById('sidebar-search')),
  };
}
function assertStationary(before, after) {
  assert.equal(after.outerScroll, 0, 'focusing a checkbox must not scroll the sidebar shell');
  assert.equal(after.view.y, before.view.y, 'the settings card must stay inside the viewport');
  assert.equal(after.settingsHeader.y, before.settingsHeader.y, 'settings header remains fixed');
}
function assertChatHeader(state) {
  assert.equal(state.outerScroll, 0, 'returning to chat must leave the sidebar shell at zero');
  assert.ok(state.chat.y >= 0 && state.chat.bottom <= state.sidebar.bottom, 'chat card is contained');
  assert.ok(state.menu.width >= 38 && state.menu.height >= 38, 'menu has a complete click target');
  assert.ok(state.search.x - state.menu.right >= 8, 'menu and search must not overlap');
  assert.ok(state.search.right <= state.chatHeader.right, 'search must fit inside the header');
  assert.ok(Math.abs((state.menu.y + state.menu.height / 2) - (state.search.y + state.search.height / 2)) <= 1,
    'menu and search centers must align');
}

(async () => {
  await app.whenReady();
  server = createServer((request, response) => {
    if (request.method !== 'GET' || request.url !== '/') {
      response.writeHead(404).end();
      return;
    }
    response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    response.end(fixture);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const fixtureUrl = `http://127.0.0.1:${server.address().port}/`;
  window = new BrowserWindow({ show: false, webPreferences: { sandbox: true, contextIsolation: true, backgroundThrottling: false, offscreen: true } });
  window.webContents.on('console-message', event => {
    if (event.level >= 2) console.error('Renderer:', event.message);
  });
  // A source-controlled UI fixture must never send requests to the cloud or
  // touch a user's Electron profile, settings, contacts or cryptographic keys.
  window.webContents.session.webRequest.onBeforeRequest((details, done) => {
    done({ cancel: details.url !== fixtureUrl });
  });
  let checks = 0;
  for (const [width, height] of [[1260, 720], [900, 480], [400, 720]]) {
    for (const theme of ['light', 'dark']) {
      window.setContentSize(width, height);
      await window.loadURL(fixtureUrl);
      await until(() => evaluate(() => notifSettingsLoaded));
      await evaluate(theme => {document.documentElement.dataset.theme = theme; navigateSidebar('notifications');}, theme);
      for (const id of ['notif-private-preview', 'notif-groups-preview']) {
        await evaluate(id => document.getElementById(id).closest('label').scrollIntoView({ block: 'center' }), id);
        await evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        const before = await evaluate(snapshot);
        for (const checked of [false, true]) {
          const writesBefore = await evaluate(() => layoutWrites.length);
          const target = await evaluate(id => {
            const r = document.getElementById(id).nextElementSibling.getBoundingClientRect();
            return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
          }, id);
          window.webContents.focus();
          window.webContents.sendInputEvent({ type: 'mouseMove', ...target });
          window.webContents.sendInputEvent({ type: 'mouseDown', button: 'left', clickCount: 1, ...target });
          window.webContents.sendInputEvent({ type: 'mouseUp', button: 'left', clickCount: 1, ...target });
          try { await until(() => evaluate(count => layoutWrites.length > count, writesBefore)); }
          catch (error) { console.error({ width, height, theme, id, checked, target, state: await evaluate(snapshot) }); throw error; }
          assert.equal(await evaluate(id => document.getElementById(id).checked, id), checked, 'real click toggles preview');
          assertStationary(before, await evaluate(snapshot));
          const field = id === 'notif-private-preview' ? 'message_preview_private' : 'message_preview_group';
          assert.equal(await evaluate(field => layoutWrites.at(-1)[field], field), checked, 'setting is saved without reload');
          checks++;
        }
      }
      await evaluate(() => document.getElementById('notif-private-preview').focus());
      const before = await evaluate(snapshot);
      const writesBefore = await evaluate(() => layoutWrites.length);
      assert.equal(before.outerScroll, 0, 'keyboard focus must scroll only the inner settings list');
      window.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Space' });
      window.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Space' });
      await until(() => evaluate(count => layoutWrites.length > count, writesBefore));
      assert.equal(await evaluate(() => document.getElementById('notif-private-preview').checked), false, 'keyboard switch remains usable');
      assertStationary(before, await evaluate(snapshot));
      checks++;
      await evaluate(() => navigateSidebar('chat'));
      assertChatHeader(await evaluate(snapshot));
      checks++;
      console.log(`PASS ${width}x${height} ${theme}: preview off/on, keyboard focus, chat header`);
    }
  }
  console.log(`Sidebar layout: ${checks}/${checks} browser checks passed`);
  window.destroy();
  server.close();
  app.exit(0);
})().catch(error => {
  console.error(error);
  if (window && !window.isDestroyed()) window.destroy();
  if (server) server.close();
  app.exit(1);
});
