const assert = require('node:assert/strict');
const { test } = require('node:test');

const ChatNotifications = require('../../../static/js/chat-notifications.js');

const FUTURE = new Date(Date.now() + 3600_000).toISOString();
const PAST = new Date(Date.now() - 3600_000).toISOString();

function makeCenter(overrides = {}) {
  const shown = [];
  const center = ChatNotifications.createNotificationCenter({
    getSettings: overrides.settings === undefined ? null : () => overrides.settings,
    getConversation: id => (overrides.conversations || {})[id] || null,
    isAppUnfocused: () => overrides.unfocused !== undefined ? overrides.unfocused : true,
    show: payload => shown.push(payload),
    translate: (en) => en,
  });
  return { shown, center };
}

const baseMeta = {
  convId: 7,
  convType: 'single',
  isSelf: false,
  decryptError: false,
  isFileMsg: false,
  text: 'hello there',
  senderName: 'Alice',
};

test('realtime private message notifies with conversation title and preview body', () => {
  const { center, shown } = makeCenter({
    conversations: { 7: { name: 'Alice', muted_until: null } },
  });
  assert.equal(center.notifyIncoming(baseMeta), true);
  assert.equal(shown.length, 1);
  assert.deepEqual(shown[0], {
    conversationId: 7,
    conversationType: 'single',
    title: 'Alice',
    body: 'hello there',
  });
});

test('master switch off silences everything', () => {
  const { center, shown } = makeCenter({ settings: { display_notifications: false } });
  assert.equal(center.notifyIncoming(baseMeta), false);
  assert.equal(shown.length, 0);
});

test('private and group toggles gate their own conversation types', () => {
  const groupMeta = { ...baseMeta, convType: 'group', senderName: 'Bob' };
  const { center: c1, shown: s1 } = makeCenter({ settings: { private_chat_notifications: false } });
  assert.equal(c1.notifyIncoming(baseMeta), false);
  assert.equal(c1.notifyIncoming(groupMeta), true);
  assert.equal(s1.length, 1);

  const { center: c2 } = makeCenter({ settings: { group_chat_notifications: false } });
  assert.equal(c2.notifyIncoming(groupMeta), false);
});

test('muted conversation is skipped, expired mute is not', () => {
  const { center: c1 } = makeCenter({ conversations: { 7: { name: 'Alice', muted_until: FUTURE } } });
  assert.equal(c1.notifyIncoming(baseMeta), false);

  const { center: c2, shown } = makeCenter({ conversations: { 7: { name: 'Alice', muted_until: PAST } } });
  assert.equal(c2.notifyIncoming(baseMeta), true);
  assert.equal(shown.length, 1);
});

test('focused app window does not notify', () => {
  const { center, shown } = makeCenter({ unfocused: false });
  assert.equal(center.notifyIncoming(baseMeta), false);
  assert.equal(shown.length, 0);
});

test('own messages and sync catch-up never notify', () => {
  const { center, shown } = makeCenter();
  assert.equal(center.notifyIncoming({ ...baseMeta, isSelf: true }), false);
  assert.equal(center.notifyIncoming({ ...baseMeta, source: 'sync' }), false);
  assert.equal(shown.length, 0);
});

test('preview off, decrypt failures and file messages fall back to a generic body', () => {
  const { center, shown } = makeCenter();
  center.notifyIncoming({ ...baseMeta, text: 'secret', decryptError: true });
  assert.equal(shown[shown.length - 1].body, 'New message');
  center.notifyIncoming({ ...baseMeta, text: '', isFileMsg: true });
  assert.equal(shown[shown.length - 1].body, 'New message');

  const noPreview = makeCenter({ settings: { message_preview_private: false } });
  noPreview.center.notifyIncoming(baseMeta);
  assert.equal(noPreview.shown[0].body, 'New message');

  const noGroupPreview = makeCenter({ settings: { message_preview_group: false } });
  noGroupPreview.center.notifyIncoming({ ...baseMeta, convType: 'group', text: 'group secret' });
  assert.equal(noGroupPreview.shown[0].body, 'New group message');
});

test('group title joins group name and sender', () => {
  const { center, shown } = makeCenter({
    conversations: { 7: { name: 'Project X', muted_until: null } },
  });
  center.notifyIncoming({ ...baseMeta, convType: 'group', senderName: 'Bob' });
  assert.equal(shown[0].title, 'Project X · Bob');
});

test('titles and bodies are clamped with an ellipsis', () => {
  const { center, shown } = makeCenter();
  center.notifyIncoming({
    ...baseMeta,
    senderName: 'A'.repeat(200),
    text: 'B'.repeat(500),
  });
  assert.equal(shown[0].title.length, ChatNotifications.TITLE_MAX);
  assert.ok(shown[0].title.endsWith('…'));
  assert.equal(shown[0].body.length, ChatNotifications.BODY_MAX);
  assert.ok(shown[0].body.endsWith('…'));
});

test('normalizeSettings keeps defaults for missing or non-boolean fields', () => {
  assert.deepEqual(ChatNotifications.normalizeSettings(null), ChatNotifications.DEFAULT_SETTINGS);
  assert.deepEqual(
    ChatNotifications.normalizeSettings({ display_notifications: false, private_chat_notifications: 'yes' }),
    { ...ChatNotifications.DEFAULT_SETTINGS, display_notifications: false },
  );
});

test('unknown conversation still notifies using the sender name', () => {
  const { center, shown } = makeCenter();
  assert.equal(center.notifyIncoming({ ...baseMeta, senderName: undefined }), true);
  assert.equal(shown[0].title, 'iChat Pro');
});

test('isConversationMuted is false for absent or malformed values', () => {
  assert.equal(ChatNotifications.isConversationMuted(null), false);
  assert.equal(ChatNotifications.isConversationMuted({}), false);
  assert.equal(ChatNotifications.isConversationMuted({ muted_until: 'not-a-date' }), false);
  assert.equal(ChatNotifications.isConversationMuted({ muted_until: FUTURE }), true);
});
