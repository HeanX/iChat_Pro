// P4 Test T08 (#189): key-fingerprint change reminders.
//
// Exercises the production helpers in static/js/chat.js and the trust record
// handling in static/js/private-chat-e2ee.js directly -- no reimplementation
// of the rule lives in this file.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const root = path.resolve(__dirname, '../../..');
const chatSource = fs.readFileSync(path.join(root, 'static/js/chat.js'), 'utf8').replace(/\r\n/g, '\n');

function functionSource(name) {
  const start = chatSource.indexOf('function ' + name + '(');
  assert.notEqual(start, -1, 'production function exists: ' + name);
  const tail = chatSource.slice(start);
  const next = tail.search(/\n(?:async )?function /);
  return next < 0 ? tail : tail.slice(0, next);
}

function chatSandbox() {
  const context = vm.createContext({ currentLanguage: 'en' });
  const names = ['isEndorsedTrustStatus', 'getActiveKeyStatus', 'contactKeyHasChanged', 'trustRowLabel'];
  vm.runInContext(names.map(functionSource).join('\n'), context);
  return context;
}

function keyStatus({ activeFingerprint, activeVersion, records, keyChanged }) {
  return {
    active_key: { user_id: 2, key_fingerprint: activeFingerprint, key_version: activeVersion },
    keys: records.map(r => ({
      user_id: 2,
      key_fingerprint: r.fingerprint,
      key_version: r.version,
      trust_status: r.trust,
    })),
    ...(keyChanged === undefined ? {} : { key_changed: keyChanged }),
  };
}

test('a brand-new key is untrusted and is reported as a change', () => {
  const ctx = chatSandbox();
  const status = keyStatus({
    activeFingerprint: 'BBBB',
    activeVersion: 2,
    records: [
      { fingerprint: 'BBBB', version: 2, trust: 'untrusted' },
      { fingerprint: 'AAAA', version: 1, trust: 'verified' },
    ],
  });
  assert.equal(ctx.getActiveKeyStatus(status).trust_status, 'untrusted');
  assert.equal(ctx.contactKeyHasChanged(status), true, 'a verified v1 replaced by v2 must warn');
});

test('the reminder clears once the new fingerprint is verified', () => {
  const ctx = chatSandbox();
  const status = keyStatus({
    activeFingerprint: 'BBBB',
    activeVersion: 2,
    records: [
      { fingerprint: 'BBBB', version: 2, trust: 'verified' },
      { fingerprint: 'AAAA', version: 1, trust: 'verified' },
    ],
  });
  assert.equal(
    ctx.contactKeyHasChanged(status),
    false,
    're-verifying the active key resolves the warning instead of warning forever'
  );
});

test('a first-time unverified contact is not a key change', () => {
  const ctx = chatSandbox();
  const status = keyStatus({
    activeFingerprint: 'CCCC',
    activeVersion: 1,
    records: [{ fingerprint: 'CCCC', version: 1, trust: 'untrusted' }],
  });
  assert.equal(ctx.contactKeyHasChanged(status), false);
});

test('rotating away from a key the user never endorsed is not a change', () => {
  const ctx = chatSandbox();
  const status = keyStatus({
    activeFingerprint: 'DDDD',
    activeVersion: 2,
    records: [
      { fingerprint: 'DDDD', version: 2, trust: 'untrusted' },
      { fingerprint: 'CCCC', version: 1, trust: 'untrusted' },
    ],
  });
  assert.equal(ctx.contactKeyHasChanged(status), false);
});

test('a missing or malformed payload never claims a key change', () => {
  const ctx = chatSandbox();
  assert.equal(ctx.contactKeyHasChanged(null), false);
  assert.equal(ctx.contactKeyHasChanged({}), false);
  assert.equal(ctx.contactKeyHasChanged({ active_key: null }), false);
});

test('the server-provided verdict wins over local inference', () => {
  const ctx = chatSandbox();
  // Server says "no change" even though a local heuristic could disagree.
  assert.equal(ctx.contactKeyHasChanged(keyStatus({
    activeFingerprint: 'BBBB',
    activeVersion: 2,
    records: [
      { fingerprint: 'BBBB', version: 2, trust: 'untrusted' },
      { fingerprint: 'AAAA', version: 1, trust: 'verified' },
    ],
    keyChanged: false,
  })), false);
  // Server says "changed" even when the payload was trimmed of history.
  assert.equal(ctx.contactKeyHasChanged(keyStatus({
    activeFingerprint: 'BBBB',
    activeVersion: 2,
    records: [],
    keyChanged: true,
  })), true);
});

test('only trusted/verified count as an endorsement', () => {
  const ctx = chatSandbox();
  assert.equal(ctx.isEndorsedTrustStatus('verified'), true);
  assert.equal(ctx.isEndorsedTrustStatus('trusted'), true);
  assert.equal(ctx.isEndorsedTrustStatus('untrusted'), false);
  assert.equal(ctx.isEndorsedTrustStatus(undefined), false);

  const status = keyStatus({
    activeFingerprint: 'BBBB',
    activeVersion: 2,
    records: [
      { fingerprint: 'BBBB', version: 2, trust: 'untrusted' },
      { fingerprint: 'AAAA', version: 1, trust: 'trusted' },
    ],
  });
  assert.equal(ctx.contactKeyHasChanged(status), true, 'trusted counts the same as verified');
});

// ── private-chat-e2ee.js: stored trust never covers an unseen version ──

test('stored trust records keep versions apart and never inherit', () => {
  const source = fs.readFileSync(
    path.join(root, 'static/js/private-chat-e2ee.js'),
    'utf8'
  ).replace(/\r\n/g, '\n');
  const helper = source.match(/  function trustPeerKey\([\s\S]*?\n  \}/);
  const load = source.match(/  function loadPeerTrust\([\s\S]*?\n  \}/);
  assert.ok(helper && load, 'exercise the production trust functions');

  const store = new Map();
  const context = vm.createContext({
    TRUST_STORAGE_PREFIX: 'ichat_peer_identity:',
    IDENTITY_ALGORITHM: 'ECDH-P256',
    localStorage: {
      getItem: k => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, v),
      removeItem: k => store.delete(k),
    },
    _cachePublicKey: () => {},
  });
  vm.runInContext(load[0] + '\n' + helper[0], context);

  const v1 = { user_id: 7, key_version: 1, key_fingerprint: 'AAAA', algorithm: 'ECDH-P256', identity_public_key: 'k1' };
  const v2 = { user_id: 7, key_version: 2, key_fingerprint: 'BBBB', algorithm: 'ECDH-P256', identity_public_key: 'k2' };
  context.trustPeerKey(v1);

  let stored = context.loadPeerTrust(7);
  assert.equal(stored.versions['1'], 'AAAA');
  assert.equal(stored.versions['2'], undefined, 'v1 endorsement must not cover version 2');

  context.trustPeerKey(v2);
  stored = context.loadPeerTrust(7);
  assert.equal(stored.versions['1'], 'AAAA', 'the historical endorsement is preserved');
  assert.equal(stored.versions['2'], 'BBBB');
});

// ── renderTrustList label: a changed row must still state the verdict ──

test('a changed row still reports the current verdict', () => {
  const ctx = chatSandbox();
  assert.equal(
    ctx.trustRowLabel({ key_changed: true, trust_status: 'untrusted' }),
    'Changed · Unverified',
    'showing only "Changed" hid that the new fingerprint is untrusted'
  );
  assert.equal(ctx.trustRowLabel({ key_changed: true, trust_status: 'verified' }), 'Changed · Verified');
});

test('an unchanged row reports only its verdict', () => {
  const ctx = chatSandbox();
  assert.equal(ctx.trustRowLabel({ key_changed: false, trust_status: 'verified' }), 'Verified');
  assert.equal(ctx.trustRowLabel({ key_changed: false, trust_status: 'untrusted' }), 'Unverified');
  assert.equal(ctx.trustRowLabel({ key_changed: false }), 'Unverified', 'missing status is unverified, not trusted');
});

test('the changed-row label is localised', () => {
  const ctx = chatSandbox();
  ctx.currentLanguage = 'zh';
  assert.equal(ctx.trustRowLabel({ key_changed: true, trust_status: 'untrusted' }), '已变更 · 未验证');
  assert.equal(ctx.trustRowLabel({ key_changed: false, trust_status: 'verified' }), '已验证');
});
