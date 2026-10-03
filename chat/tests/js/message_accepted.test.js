// Exercise the actual ACK handler, row patcher and timeline helper against a
// small DOM fixture. No copy of the ACK/reordering implementation lives here.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const root = path.resolve(__dirname, '../../..');
const chatSource = fs.readFileSync(path.join(root, 'static/js/chat.js'), 'utf8').replace(/\r\n/g, '\n');
const connectionSource = fs.readFileSync(path.join(root, 'static/js/chat-connection.js'), 'utf8');

function functionSource(name) {
  const start = chatSource.indexOf('function ' + name + '(');
  assert.notEqual(start, -1, 'production function exists: ' + name);
  const tail = chatSource.slice(start);
  const next = tail.search(/\n(?:async )?function /);
  return next < 0 ? tail : tail.slice(0, next);
}

class Element {
  constructor(className = '') {
    this.className = className;
    this.attributes = {};
    this.children = [];
    this.parentNode = null;
    this.dataset = new Proxy({}, {
      get: (_, key) => this.getAttribute('data-' + key.replace(/[A-Z]/g, c => '-' + c.toLowerCase())),
      set: (_, key, value) => {
        this.setAttribute('data-' + key.replace(/[A-Z]/g, c => '-' + c.toLowerCase()), value);
        return true;
      },
    });
  }
  set id(value) { this.setAttribute('id', value); }
  get id() { return this.getAttribute('id'); }
  set innerHTML(value) {
    assert.equal(value, '', 'fixture only implements clearing markup');
    this.children.forEach(child => { child.parentNode = null; });
    this.children = [];
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  matches(selector) {
    const absent = selector.match(/:not\(\[([^\]]+)\]\)/);
    if (absent && this.getAttribute(absent[1]) !== null) return false;
    selector = selector.replace(/:not\([^)]*\)/g, '');
    for (const [, name] of selector.matchAll(/\.([\w-]+)/g)) {
      if (!this.className.split(' ').includes(name)) return false;
    }
    for (const [, name, value] of selector.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)) {
      const actual = this.getAttribute(name);
      if (actual === null || (value !== undefined && actual !== value)) return false;
    }
    return true;
  }
  querySelectorAll(selector) {
    const direct = selector.startsWith(':scope > ');
    selector = selector.replace(/^:scope > /, '');
    const nodes = direct ? this.children : this.children.flatMap(child => [child, ...child.descendants()]);
    return nodes.filter(node => node.matches(selector));
  }
  descendants() { return this.children.flatMap(child => [child, ...child.descendants()]); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { return this.matches(selector) ? this : this.parentNode?.closest(selector) || null; }
  remove() {
    if (!this.parentNode) return;
    this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1);
    this.parentNode = null;
  }
  appendChild(node) {
    node.remove();
    this.children.push(node);
    node.parentNode = this;
    return node;
  }
  insertBefore(node, next) {
    assert.equal(next.parentNode, this);
    node.remove();
    this.children.splice(this.children.indexOf(next), 0, node);
    node.parentNode = this;
  }
  replaceWith(node) {
    const parent = this.parentNode;
    assert.ok(parent, 'only attached rows can be replaced');
    node.remove();
    parent.children[parent.children.indexOf(this)] = node;
    node.parentNode = parent;
    this.parentNode = null;
  }
}

function fixture(initial, options = {}) {
  const body = new Element();
  const background = body.appendChild(new Element());
  const container = body.appendChild(new Element());
  container.id = 'message-history-container';
  const document = {
    querySelector: selector => body.querySelector(selector),
    querySelectorAll: selector => body.querySelectorAll(selector),
    getElementById: id => body.descendants().find(node => node.id === id) || null,
  };
  const accepted = [];
  const state = {
    document, module: { exports: {} }, console,
    messages: initial.map(msg => ({ isSelf: false, sender: 'peer', ...msg })),
    activeChatId: 6, conversationsById: { 6: { type: 'private' } },
    selectedMessageIds: options.selected ? ['client-offline'] : [],
    pendingOutgoingMessages: { 'client-offline': { conversationId: 6 } },
    chatOutbox: { accepted: id => accepted.push(id) },
    formatClockTime: date => date.toISOString(),
    applyImagePreviewToBubble() {}, isChatSearchOpen: () => false,
    patchMessageStatusInPlace(msg) {
      const row = container.querySelector('[data-row-message-id="' + msg.id + '"]');
      if (row) row.status = msg.status;
    },
  };
  state.window = state;
  state.createMessageBubbleElementNew = (msg, group) => {
    const row = new Element('message-row');
    row.dataset.rowMessageId = msg.id;
    row.text = msg.text;
    row.time = msg.time;
    row.group = group;
    row.select = () => msg.id; // renderer closures must still address this message
    if (!msg.isSystem) {
      const bubble = row.appendChild(new Element('message-bubble-custom'));
      bubble.setAttribute('data-message-id', msg.id);
      const checkbox = row.appendChild(new Element('message-select-checkbox'));
      checkbox.id = 'msg-select-check-' + msg.id;
      checkbox.selected = state.selectedMessageIds.includes(msg.id);
    }
    return row;
  };
  const context = vm.createContext(state);
  vm.runInContext(connectionSource, context, { filename: 'chat-connection.js' });
  state.ChatConnection = context.module.exports;
  for (const name of ['handleMessageAccepted', 'patchMessageRowInPlace', 'getMessageGroupMetaNew', 'renderMessages']) {
    vm.runInContext(functionSource(name), context, { filename: 'chat.js:' + name });
  }
  let fullRenders = 0;
  const render = context.renderMessages;
  context.renderMessages = () => { fullRenders += 1; render(); };
  state.messages.forEach((msg, idx) => container.appendChild(
    state.createMessageBubbleElementNew(msg, context.getMessageGroupMetaNew(state.messages, idx, state.conversationsById[6]))
  ));
  return {
    context, container, background, accepted,
    fullRenders: () => fullRenders,
    ack: payload => context.handleMessageAccepted({ data: { client_message_id: 'client-offline', status: 'sent', ...payload } }),
  };
}

const message = (id, second, extras = {}) => ({
  id, created_at: '2026-10-03T12:00:0' + second + 'Z', text: 'message ' + id, ...extras,
});

function assertTimeline(h, ids) {
  assert.deepEqual(Array.from(h.context.messages, msg => msg.id), ids);
  assert.deepEqual(h.container.children.map(row => row.dataset.rowMessageId), ids.map(String));
  assert.equal(new Set(h.container.children.map(row => row.dataset.rowMessageId)).size, ids.length);
  h.container.children.forEach((row, idx) => {
    const msg = h.context.messages[idx];
    assert.equal(row.text, msg.text, 'no received content is overwritten');
    assert.equal(row.select(), msg.id, 'row action still addresses its own message');
    assert.equal(JSON.stringify(row.group), JSON.stringify(h.context.getMessageGroupMetaNew(h.context.messages, idx)), 'grouping at old and new positions');
    const bubble = row.querySelector('[data-message-id]');
    if (bubble) assert.equal(bubble.getAttribute('data-message-id'), String(msg.id));
    const checkbox = row.querySelector('.message-select-checkbox');
    if (checkbox) assert.equal(checkbox.id, 'msg-select-check-' + msg.id);
  });
}

test('late ACK moves the original row to the tail without missing/duplicate content', () => {
  const h = fixture([message('client-offline', 1, { isSelf: true }), message(43, 2), message(44, 3)], { selected: true });
  h.ack({ message_id: 45, created_at: '2026-10-03T12:00:04Z' });
  assertTimeline(h, [43, 44, 45]);
  assert.deepEqual(Array.from(h.context.selectedMessageIds), [45]);
  assert.equal(h.container.children[2].querySelector('.message-select-checkbox').selected, true);
  assert.equal(h.context.messages[2].time, '2026-10-03T12:00:04.000Z');
  assert.equal(h.context.pendingOutgoingMessages['client-offline'], undefined);
  assert.deepEqual(h.accepted, ['client-offline']);
  assert.equal(h.fullRenders(), 0, 'normal ACK preserves unrelated rows');
  h.ack({ message_id: 45, created_at: '2026-10-03T12:00:04Z' });
  assertTimeline(h, [43, 44, 45]);
});

for (const [name, stamp, expected] of [
  ['front', '2026-10-03T12:00:00Z', [3, 1, 2]],
  ['middle', '2026-10-03T12:00:02Z', [1, 3, 2]],
  ['same timestamp ID tiebreak', '2026-10-03T12:00:03Z', [1, 2, 3]],
]) {
  test('ACK repositions to ' + name + ' and repairs both grouping boundaries', () => {
    const h = fixture([message(1, 1), message(2, 3), message('client-offline', 5, { isSelf: true })]);
    h.ack({ message_id: 3, created_at: stamp });
    assertTimeline(h, expected);
    assert.equal(h.fullRenders(), 0);
  });
}

test('a row without a bubble is located by its stable row ID', () => {
  const h = fixture([message('client-offline', 1, { isSystem: true }), message(1, 2)]);
  h.ack({ message_id: 2, created_at: '2026-10-03T12:00:03Z' });
  assertTimeline(h, [1, 2]);
});

test('a legacy row gets its ID before movement', () => {
  const h = fixture([message('client-offline', 1), message(1, 2)]);
  h.container.children[0].removeAttribute('data-row-message-id');
  h.ack({ message_id: 2, created_at: '2026-10-03T12:00:03Z' });
  assertTimeline(h, [1, 2]);
});

test('ACK without a timestamp still updates row, checkbox and selected identity', () => {
  const h = fixture([message('client-offline', 1, { isSelf: true })], { selected: true });
  h.ack({ message_id: 1 });
  assertTimeline(h, [1]);
  assert.deepEqual(Array.from(h.context.selectedMessageIds), [1]);
});

test('missing original row safely restores the timeline instead of patching an unrelated row', () => {
  const h = fixture([message('client-offline', 1), message(1, 2)]);
  h.container.children[0].remove();
  h.ack({ message_id: 2, created_at: '2026-10-03T12:00:03Z' });
  assertTimeline(h, [1, 2]);
  assert.equal(h.fullRenders(), 1);
});

test('movement stays inside the active history, even when an AI row has the same ID', () => {
  const h = fixture([message('client-offline', 1), message(1, 2), message(2, 3)]);
  const ai = h.background.appendChild(new Element('message-row'));
  ai.dataset.rowMessageId = 3;
  h.ack({ message_id: 3, created_at: '2026-10-03T12:00:04Z' });
  assertTimeline(h, [1, 2, 3]);
  assert.equal(ai.parentNode, h.background);
  assert.equal(h.background.children.length, 1);
});

test('a missing specified successor is not silently treated as the tail', () => {
  const h = fixture([message(1, 1), message(2, 2)]);
  assert.equal(h.context.ChatConnection.repositionMessageRow(1, 99), false);
  assertTimeline(h, [1, 2]);
});

test('moving a self message away joins the peer group at its former position', () => {
  const h = fixture([message(1, 0), message('client-offline', 1, { isSelf: true }), message(2, 2), message(3, 3)]);
  h.ack({ message_id: 4, created_at: '2026-10-03T12:00:04Z' });
  assertTimeline(h, [1, 2, 3, 4]);
  assert.equal(h.fullRenders(), 0);
});

test('a missing successor row restores DOM order without overwriting another message', () => {
  const h = fixture([message(1, 1), message(2, 3), message('client-offline', 5, { isSelf: true })]);
  h.container.children[1].remove();
  h.ack({ message_id: 3, created_at: '2026-10-03T12:00:02Z' });
  assertTimeline(h, [1, 3, 2]);
  assert.equal(h.fullRenders(), 1);
});
