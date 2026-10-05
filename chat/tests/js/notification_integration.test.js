// Run production notification wiring and message handlers, including the
// inline settings page that shares the chat page's JavaScript global scope.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test: nodeTest } = require('node:test');
const test = (name, run) => nodeTest(name, { timeout: 5000 }, run);
const root = path.resolve(__dirname, '../../..');
const source = fs.readFileSync(path.join(root, 'static/js/chat.js'), 'utf8').replace(/\r\n/g, '\n');
const settingsSource = fs.readFileSync(path.join(root, 'templates/pages/notifications.html'), 'utf8')
  .match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];
const ChatNotifications = require('../../../static/js/chat-notifications');
const ChatConnection = require('../../../static/js/chat-connection');
const SETTINGS = {
  user_id: 1, display_notifications: true, private_chat_notifications: true,
  group_chat_notifications: true, message_preview_private: true, message_preview_group: true,
};

function functionSource(name) {
  const match = new RegExp('(?:async )?function ' + name + '\\(').exec(source);
  assert.ok(match, 'production function exists: ' + name);
  const tail = source.slice(match.index);
  const next = tail.search(/\n(?:async )?function /);
  return next < 0 ? tail : tail.slice(0, next);
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function fixture(options = {}) {
  const shown = [], selected = [], errors = [], listeners = new Map(), elements = new Map();
  const row = { classList: { add() {}, remove() {} } };
  for (const id of ['notif-display', 'notif-private', 'notif-groups', 'notif-private-preview', 'notif-groups-preview']) {
    elements.set(id, { checked: true, closest: () => row });
  }
  let click;
  const state = {
    console: { error: (...args) => errors.push(args), warn() {}, log() {} },
    Promise, Date, Number, ChatNotifications, ChatConnection,
    currentLanguage: 'en', myUserId: 1, conversations: [],
    conversationsById: options.conversations || { 7: { id: 7, name: 'Peer', type: 'private', unread: 0 } },
    activeChatId: options.activeChatId || 99, messages: [], wsClient: null,
    document: {
      hidden: false, hasFocus: () => false,
      querySelectorAll: () => [], getElementById: id => elements.get(id) || null,
    },
    Notification: { permission: options.permission || 'granted', requestPermission: async () => 'denied' },
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
    setTimeout, clearTimeout,
    apiFetch: options.apiFetch || (async () => SETTINGS),
    fetch: async () => ({ ok: true, json: options.fetchSettings || options.apiFetch || (async () => SETTINGS) }),
    selectChat: options.selectChat || (async id => selected.push(id)),
    fetchConversations: options.refresh || (async () => {}),
    formatClockTime: () => '12:00', canDecryptMessagePayload: () => true,
    decryptFailureLabel: () => 'Encrypted message', getGroupMemberInfo: () => ({}),
    updateSidebarPreview() {}, appendMessageElement() {}, tagLastMessageRow() {},
    patchMessageRowInPlace() {}, scrollToBottom() {},
    backgroundSeen: () => false, backgroundMark() {},
    iChatPrivateE2EE: { decryptPrivateMessage: options.decrypt || (async () => 'private secret') },
    iChatGroupE2EE: { decryptGroupMessage: options.decrypt || (async () => 'group secret') },
    iChatDesktop: { notifications: {
      show: options.show || (payload => { shown.push(payload); return Promise.resolve(true); }),
      onClicked: callback => { click = callback; },
    } },
    addEventListener(type, callback) { listeners.set(type, callback); },
    dispatchEvent(event) { if (listeners.has(event.type)) listeners.get(event.type)(event); },
  };
  state.window = state;
  const context = vm.createContext(state);
  // Same order as chat.html: the settings page is included in the sidebar
  // before chat.js is loaded. Its initial request resolves asynchronously.
  vm.runInContext(settingsSource, context, { filename: 'notifications.html' });
  vm.runInContext(source.match(/^function _t4\([^\n]+/m)[0], context);
  const start = source.indexOf('// T18: incoming-message notification gating');
  const end = source.indexOf('\nlet isSelectingMessages', start);
  vm.runInContext(source.slice(start, end), context, { filename: 'chat.js:notifications' });
  for (const name of ['handlePrivateMessageReceived', 'handleGroupMessageReceived']) {
    vm.runInContext(functionSource(name), context, { filename: 'chat.js:' + name });
  }
  return {
    state, context, shown, selected, errors, elements,
    load: () => vm.runInContext('typeof loadChatNotificationSettings === "function" ? loadChatNotificationSettings() : loadNotificationSettings()', context),
    settings: data => context.applyNotificationSettings(data),
    click: data => click(data), setup: () => context.setupDesktopNotificationBridge(),
    private: (meta) => context.handlePrivateMessageReceived({ conversation_id: 7, message_id: 101, sender_id: 2 }, meta),
    group: (meta) => context.handleGroupMessageReceived({ group_id: 7, message_id: 102, sender_id: 2, sender_name: 'Alice' }, meta),
  };
}

test('pending or failed settings requests cannot enable notifications or previews', async () => {
  const request = deferred();
  const h = fixture({ apiFetch: () => request.promise });
  const loading = h.load();
  await h.private();
  assert.equal(h.shown.length, 0, 'pending settings are not permission to preview');
  request.reject(new Error('offline'));
  await loading;
  await h.private();
  assert.equal(h.shown.length, 0, 'failed settings stay quiet');
});

test('embedded settings controls update mute and preview gates without reloading chat', async () => {
  const h = fixture();
  await h.load();
  await h.private();
  assert.equal(h.shown.at(-1).body, 'private secret');
  h.settings({ ...SETTINGS, display_notifications: false });
  const count = h.shown.length;
  await h.private();
  assert.equal(h.shown.length, count, 'master switch applies in the current chat document');
  h.settings({ ...SETTINGS, message_preview_private: false });
  await h.private();
  assert.equal(h.shown.at(-1).body, 'New message');
  h.settings({ ...SETTINGS, user_id: 9 });
  await h.private();
  assert.equal(h.shown.at(-1).body, 'New message', 'another account cannot turn this account preview on');
});

test('a late initial settings response cannot overwrite a more recent saved switch', async () => {
  const request = deferred();
  const h = fixture({ apiFetch: () => request.promise, fetchSettings: async () => SETTINGS });
  await new Promise(setImmediate);
  const loading = h.load();
  h.settings({ ...SETTINGS, display_notifications: false });
  request.resolve(SETTINGS);
  await loading;
  await h.private();
  assert.equal(h.shown.length, 0);
});

test('desktop settings do not request or depend on browser notification permission', async () => {
  const h = fixture({ permission: 'denied' });
  await h.load();
  assert.equal(h.elements.get('notif-display').checked, true, 'browser denial does not disable the Electron switch');
  assert.equal(await h.context.requestNotificationPermissionIfNeeded(), true);
});

for (const channel of ['private', 'group']) {
  test(channel + ' message application survives synchronous and asynchronous notification failures', async () => {
    for (const show of [() => { throw new Error('bridge stopped'); }, () => Promise.reject(new Error('IPC denied'))]) {
      const h = fixture({ show, activeChatId: 7 });
      await h.load();
      assert.equal(await h[channel](), true);
      await Promise.resolve();
      assert.equal(h.state.messages.length, 1, 'received message is applied exactly once');
      assert.equal(h.state.messages[0].text, channel + ' secret');
    }
  });
}

test('group decryption failure sends a generic realtime notification but never reports successful sync application', async () => {
  const h = fixture({ decrypt: async () => { throw new Error('missing_key_material'); } });
  await h.load();
  assert.equal(await h.group(), false);
  assert.equal(h.shown.length, 1);
  assert.equal(h.shown[0].body, 'New group message');
  assert.equal(await h.group({ source: 'sync' }), false);
  assert.equal(h.shown.length, 1, 'sync remains silent even on decrypt failure');
  h.settings({ ...SETTINGS, group_chat_notifications: false });
  await h.group();
  assert.equal(h.shown.length, 1, 'failure does not bypass group switch');
});

test('missing E2EE modules also use a generic body and cannot advance sync as decrypted messages', async () => {
  for (const channel of ['private', 'group']) {
    const h = fixture();
    await h.load();
    h.state[channel === 'private' ? 'iChatPrivateE2EE' : 'iChatGroupE2EE'] = undefined;
    assert.equal(await h[channel](), false);
    assert.equal(h.shown.at(-1).body, channel === 'private' ? 'New message' : 'New group message');
  }
});

test('unknown conversation is refreshed before deciding whether it is muted', async () => {
  const request = deferred();
  const h = fixture({ conversations: {}, refresh: () => request.promise });
  await h.load();
  const applied = h.private();
  await new Promise(setImmediate);
  assert.equal(h.shown.length, 0, 'missing mute information is not permission to notify');
  h.state.conversationsById[7] = { name: 'Peer', muted_until: new Date(Date.now() + 3600000).toISOString() };
  request.resolve();
  await applied;
  await new Promise(setImmediate);
  assert.equal(h.shown.length, 0, 'newly discovered mute is respected');
});

test('notification conversation refresh does not hold up message application', async () => {
  const request = deferred();
  const h = fixture({ conversations: {}, activeChatId: 7, refresh: () => request.promise });
  await h.load();
  assert.equal(await h.private(), true, 'applying the decrypted message does not wait for optional toast metadata');
  assert.equal(h.state.messages.length, 1);
  request.resolve();
});

test('click on a newly created conversation refreshes the list then selects it', async () => {
  const request = deferred();
  const h = fixture({ conversations: {}, refresh: () => request.promise });
  h.setup();
  const clicked = h.click({ conversationId: 7, conversationType: 'single' });
  await Promise.resolve();
  assert.deepEqual(h.selected, []);
  h.state.conversationsById[7] = { id: 7, type: 'private' };
  request.resolve();
  await clicked;
  assert.deepEqual(h.selected, [7]);
});

test('notification click contains refresh/selection failures instead of rejecting the preload listener', async () => {
  const h = fixture({ selectChat: async () => { throw new Error('history unavailable'); } });
  h.setup();
  await assert.doesNotReject(async () => h.click({ conversationId: 7, conversationType: 'single' }));
});
