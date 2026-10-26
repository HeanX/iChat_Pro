// P4 Test T08 (#189) remediation -- WEB-03: a key-verification decision is a
// user authorisation, never a side effect of refreshing a fingerprint.
//
// These tests run the production `respondToKeyVerification`,
// `refreshKeyVerificationAfterChange` and `handleKeyVerificationNew` declared in
// static/js/chat.js, together with the real `loadPeerTrust` / `trustPeerKey`
// store from static/js/private-chat-e2ee.js. Only the HTTP client, the
// fingerprint endpoint, DOM rendering and the WebSocket arrival are stubbed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const root = path.resolve(__dirname, '../../..');

function read(relative) {
  return fs.readFileSync(path.join(root, relative), 'utf8').replace(/\r\n/g, '\n');
}

const chatSource = read('static/js/chat.js');
const e2eeSource = read('static/js/private-chat-e2ee.js');

// Brace matching starts at the body brace (found after the matching closing
// parenthesis), so default object parameters such as `options = {}` cannot
// truncate the slice.
function extractFunction(source, name, { required = true } = {}) {
  let start = -1;
  for (const prefix of ['async function ' + name + '(', 'function ' + name + '(']) {
    const found = source.indexOf(prefix);
    if (found !== -1) {
      start = found;
      break;
    }
  }
  if (start === -1) {
    assert.ok(!required, 'production declaration exists: ' + name);
    return null;
  }
  const openParen = source.indexOf('(', start);
  let depth = 0;
  let cursor = openParen;
  for (; cursor < source.length; cursor += 1) {
    if (source[cursor] === '(') depth += 1;
    else if (source[cursor] === ')') {
      depth -= 1;
      if (depth === 0) break;
    }
  }
  const bodyStart = source.indexOf('{', cursor);
  depth = 0;
  for (let i = bodyStart; i < source.length; i += 1) {
    if (source[i] === '{') depth += 1;
    else if (source[i] === '}') {
      depth -= 1;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  return assert.fail('balanced body for ' + name);
}

// Real helpers used by the entry points under test.
const CHAT_SUPPORT = [
  'isEndorsedTrustStatus',
  'getActiveKeyStatus',
  'contactKeyHasChanged',
  'keyVerificationOtherUserId',
];
// The WEB-03 rule helper only exists after the fix; before it the entry points
// still load, so the behavioural assertions (not a missing declaration) are
// what fail.
const CHAT_TRUST_RULE = [
  'acceptedVerificationPeer',
  'keyStatusMatchesAcceptedVerification',
  'verificationConfirmation',
  'verificationIdentifiesSameRequest',
  'rememberLocallyAnswered',
  'forgetLocallyAnswered',
  'isLocallyAnswered',
];
const CHAT_ENTRY_POINTS = [
  'refreshKeyVerificationAfterChange',
  'respondToKeyVerification',
  'handleKeyVerificationNew',
];
const TRUST_STORE_FUNCTIONS = ['loadPeerTrust', 'trustPeerKey'];

const chatSupportSource = CHAT_SUPPORT.map(name => extractFunction(chatSource, name)).join('\n');
const chatRuleSource = CHAT_TRUST_RULE
  .map(name => extractFunction(chatSource, name, { required: false }))
  .filter(Boolean)
  .join('\n');
const chatEntrySource = CHAT_ENTRY_POINTS.map(name => extractFunction(chatSource, name)).join('\n');
const trustStoreSource = TRUST_STORE_FUNCTIONS
  .map(name => extractFunction(e2eeSource, name))
  .join('\n');

const TRUST_KEY = 'ichat_peer_identity:';

function keyStatusPayload({ userId, version, fingerprint, trust = 'untrusted', keyChanged = false, history = [] }) {
  const active = {
    user_id: userId,
    key_version: version,
    key_fingerprint: fingerprint,
    algorithm: 'ECDH-P256',
    identity_public_key: 'public-key-' + fingerprint,
    is_active: true,
  };
  return {
    user_id: userId,
    active_key: active,
    keys: [{ ...active, trust_status: trust }, ...history],
    key_changed: keyChanged,
  };
}

const flush = () => new Promise(resolve => setImmediate(resolve));

function createWorld(options = {}) {
  const store = new Map(
    Object.entries(options.stored || {}).map(([key, value]) => [key, JSON.stringify(value)])
  );
  const cacheCalls = [];
  const toasts = [];
  const renders = { pendingGroups: 0, pendingKeyVerifications: 0 };
  const detailsUpdates = [];
  const statusCalls = [];
  const apiCalls = [];
  const pendingFetches = [];
  const statusByUser = new Map(
    (options.status || []).map(payload => [Number(payload.user_id), payload])
  );

  const context = vm.createContext({
    console,
    currentLanguage: 'en',
    myUserId: 1,
    activeChatId: options.activeChatId || null,
    conversationsById: options.conversationsById || {},
    contactKeyStatusCacheByUserId: {},
    keyTrustListCache: null,
    pendingKeyVerificationRequests: options.pending || [],
    // Production's in-flight guard state, reset per world.
    keyVerificationResponsesInFlight: {},
    // Production's self-echo bookkeeping, reset per world.
    locallyAnsweredVerificationIds: {},
    KEY_VERIFICATION_ECHO_TTL_MS: 60000,
    Date,
    TRUST_STORAGE_PREFIX: TRUST_KEY,
    IDENTITY_ALGORITHM: 'ECDH-P256',
    PrivateChatCryptoError: class PrivateChatCryptoError extends Error {},
    localStorage: {
      getItem: key => (store.has(key) ? store.get(key) : null),
      setItem: (key, value) => store.set(key, value),
      removeItem: key => store.delete(key),
    },
    _cachePublicKey: (userId, keyVersion, key) => cacheCalls.push({ userId, keyVersion, key }),
    document: { getElementById: () => null },
    window: {
      showToast: message => toasts.push(message),
      iChatPrivateE2EE: null,
    },
    renderPendingGroupInvitations: () => {
      renders.pendingGroups += 1;
    },
    renderPendingKeyVerifications: () => {
      renders.pendingKeyVerifications += 1;
    },
    fetchPendingGroupInvitations: async () => {
      pendingFetches.push(true);
    },
    updateDetailsPanel: async conv => {
      detailsUpdates.push(conv);
    },
    fetchContactKeyStatus: async (userId, config = {}) => {
      statusCalls.push({ userId: Number(userId), force: Boolean(config && config.force) });
      const payload = statusByUser.get(Number(userId));
      return payload === undefined ? null : payload;
    },
    apiFetch: async (url, init = {}) => {
      const call = { url, action: init.body ? JSON.parse(init.body).action : null };
      apiCalls.push(call);
      return options.api ? options.api(call) : { status: 'ok' };
    },
  });

  vm.runInContext(trustStoreSource, context);
  context.window.iChatPrivateE2EE = { trustPeerKey: context.trustPeerKey };
  vm.runInContext([chatSupportSource, chatRuleSource, chatEntrySource].join('\n\n'), context);

  return {
    context,
    statusByUser,
    cacheCalls,
    toasts,
    renders,
    detailsUpdates,
    statusCalls,
    apiCalls,
    pendingFetches,
    trust: userId => {
      const raw = store.get(TRUST_KEY + userId);
      return raw ? JSON.parse(raw) : null;
    },
    versions: userId => {
      const record = store.get(TRUST_KEY + userId);
      return record ? JSON.parse(record).versions : null;
    },
    store,
  };
}

// Contact 7 rotated from an endorsed v1 (AAAA) to a fresh v2 (BBBB); the
// server holds no verdict for it.
function rotatedContactStatus(fingerprint = 'BBBB', { trust = 'untrusted', version = 2 } = {}) {
  return keyStatusPayload({
    userId: 7,
    version,
    fingerprint,
    trust,
    keyChanged: version > 1,
    history: [
      {
        user_id: 7,
        key_version: 1,
        key_fingerprint: 'AAAA',
        algorithm: 'ECDH-P256',
        identity_public_key: 'public-key-AAAA',
        trust_status: 'verified',
      },
    ],
  });
}

const incomingRequest = {
  id: 42,
  requester_id: 7,
  responder_id: 1,
  requester_key_version: 2,
  requester_key_fingerprint: 'BBBB',
  responder_key_version: 1,
  responder_key_fingerprint: 'MINE',
  status: 'pending',
  direction: 'incoming',
};

function acceptedVerification(request) {
  return { ...request, status: 'accepted' };
}

test('declining a verification request does not trust the new fingerprint', async () => {
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    pending: [{ ...incomingRequest }],
    status: [rotatedContactStatus()],
    api: async () => ({ status: 'ok', request: { ...incomingRequest, status: 'declined' } }),
  });

  await world.context.respondToKeyVerification(42, 'decline');

  assert.equal(world.apiCalls[0].action, 'decline');
  assert.deepEqual(
    world.versions(7),
    { 1: 'AAAA' },
    'a decline must not add the untrusted v2 fingerprint to the local trust store'
  );
  assert.deepEqual(world.cacheCalls, [], 'a decline must not replace the cached send key');
  assert.equal(
    world.context.pendingKeyVerificationRequests.length,
    0,
    'the declined request is still removed from the tray'
  );
});

test('cancelling a verification request does not trust the pending fingerprint', async () => {
  const outgoing = {
    id: 43,
    requester_id: 1,
    responder_id: 7,
    requester_key_version: 1,
    requester_key_fingerprint: 'MINE',
    responder_key_version: 2,
    responder_key_fingerprint: 'BBBB',
    status: 'pending',
    direction: 'outgoing',
  };
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    pending: [outgoing],
    status: [rotatedContactStatus()],
    api: async () => ({ status: 'ok', request: { ...outgoing, status: 'cancelled' } }),
  });

  await world.context.respondToKeyVerification(43, 'cancel');

  assert.equal(world.apiCalls[0].action, 'cancel');
  assert.deepEqual(world.versions(7), { 1: 'AAAA' }, 'cancelling is not an endorsement');
  assert.deepEqual(world.cacheCalls, []);
});

test('an expired verification event does not trust the current fingerprint', async () => {
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    pending: [{ ...incomingRequest }],
    status: [rotatedContactStatus()],
  });

  world.context.handleKeyVerificationNew({
    data: { event: 'expired', verification: { ...incomingRequest, status: 'expired' } },
  });
  await flush();

  assert.deepEqual(world.versions(7), { 1: 'AAAA' }, 'an expired request grants nothing');
  assert.deepEqual(world.cacheCalls, []);
});

test('an outgoing accept trusts the responder fingerprint the requester pinned', async () => {
  const outgoing = {
    id: 44,
    requester_id: 1,
    responder_id: 7,
    requester_key_version: 1,
    requester_key_fingerprint: 'MINE',
    responder_key_version: 2,
    responder_key_fingerprint: 'BBBB',
    status: 'pending',
    direction: 'outgoing',
  };
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    pending: [outgoing],
    status: [rotatedContactStatus('BBBB', { trust: 'verified' })],
    api: async () => ({ status: 'ok', request: { ...outgoing, status: 'accepted' } }),
  });

  await world.context.respondToKeyVerification(44, 'accept');

  assert.equal(
    world.versions(7)['2'],
    'BBBB',
    'the requester checks responder_key_fingerprint, not its own fingerprint'
  );
  assert.equal(world.cacheCalls.length, 1);
});

test('an outgoing accept is rejected when the responder key moved on', async () => {
  const outgoing = {
    id: 45,
    requester_id: 1,
    responder_id: 7,
    requester_key_version: 1,
    requester_key_fingerprint: 'MINE',
    responder_key_version: 2,
    responder_key_fingerprint: 'BBBB',
    status: 'pending',
    direction: 'outgoing',
  };
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    pending: [outgoing],
    status: [rotatedContactStatus('DDDD')],
    api: async () => ({ status: 'ok', request: { ...outgoing, status: 'accepted' } }),
  });

  await world.context.respondToKeyVerification(45, 'accept');

  assert.deepEqual(world.versions(7), { 1: 'AAAA' });
  assert.deepEqual(world.cacheCalls, []);
});

test('a verification that does not involve the signed-in user grants nothing', async () => {
  const foreign = {
    id: 46,
    requester_id: 7,
    responder_id: 8,
    requester_key_version: 2,
    requester_key_fingerprint: 'BBBB',
    responder_key_version: 1,
    responder_key_fingerprint: 'EEEE',
    status: 'accepted',
    direction: 'incoming',
  };
  const world = createWorld({
    stored: {
      [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } },
      [TRUST_KEY + 8]: { user_id: 8, versions: { 1: 'EEEE' } },
    },
    status: [rotatedContactStatus('BBBB', { trust: 'verified' })],
  });

  await world.context.refreshKeyVerificationAfterChange(7, { grantTrustFor: foreign });

  assert.deepEqual(world.versions(7), { 1: 'AAAA' }, 'a third party cannot authorise trust');
  assert.deepEqual(world.cacheCalls, []);
});

test('a bare refresh does not trust whatever fingerprint the server reports', async () => {
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    status: [rotatedContactStatus()],
  });

  await world.context.refreshKeyVerificationAfterChange(7);

  assert.deepEqual(world.versions(7), { 1: 'AAAA' }, 'refreshing a fingerprint is not verifying it');
  assert.deepEqual(world.cacheCalls, []);
  assert.deepEqual(
    world.statusCalls.map(call => call.userId),
    [7],
    'the fingerprint is still fetched so the panel can be redrawn'
  );
});

test('the currently open chat is refreshed but never trusted as a side effect', async () => {
  const world = createWorld({
    stored: { [TRUST_KEY + 9]: { user_id: 9, versions: { 1: 'OLD9' } } },
    activeChatId: 101,
    conversationsById: {
      101: { id: 101, type: 'single', peer_id: 9, name: 'Nine', is_secure: true },
    },
    status: [
      keyStatusPayload({
        userId: 9,
        version: 2,
        fingerprint: 'NEW9',
        keyChanged: true,
        history: [
          {
            user_id: 9,
            key_version: 1,
            key_fingerprint: 'OLD9',
            algorithm: 'ECDH-P256',
            identity_public_key: 'public-key-OLD9',
            trust_status: 'verified',
          },
        ],
      }),
    ],
  });

  await world.context.refreshKeyVerificationAfterChange(null);

  assert.deepEqual(
    world.versions(9),
    { 1: 'OLD9' },
    'another contact being open is not a verification of that contact'
  );
  assert.deepEqual(world.cacheCalls, []);
  assert.equal(world.detailsUpdates.length, 1, 'the open conversation is still re-rendered');
});

test('a successful accept still trusts the verified fingerprint and refreshes the send cache', async () => {
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    pending: [{ ...incomingRequest }],
    status: [rotatedContactStatus('BBBB', { trust: 'verified' })],
    api: async () => ({ status: 'ok', request: acceptedVerification(incomingRequest) }),
  });

  await world.context.respondToKeyVerification(42, 'accept');

  assert.equal(
    world.versions(7)['2'],
    'BBBB',
    'the accepted fingerprint must still be persisted locally'
  );
  assert.equal(world.versions(7)['1'], 'AAAA', 'the historical endorsement is preserved');
  assert.equal(world.cacheCalls.length, 1, 'the accepted key must replace the cached send key');
  assert.equal(world.cacheCalls[0].userId, 7);
  assert.equal(world.cacheCalls[0].keyVersion, 2);
  assert.equal(
    world.toasts[world.toasts.length - 1],
    'Security verification completed',
    'a real accept still reports success'
  );
});

test('an accept whose server fingerprint no longer matches the request grants nothing', async () => {
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    pending: [{ ...incomingRequest }],
    // The key rotated again between the request and the accept.
    status: [rotatedContactStatus('CCCC')],
    api: async () => ({ status: 'ok', request: acceptedVerification(incomingRequest) }),
  });

  await world.context.respondToKeyVerification(42, 'accept');

  assert.deepEqual(
    world.versions(7),
    { 1: 'AAAA' },
    'the server key is no longer the fingerprint the user accepted'
  );
  assert.deepEqual(world.cacheCalls, [], 'an unverified fingerprint must not become the send key');
});

test('an accept whose live key reports the same fingerprint at a newer version grants nothing', async () => {
  // Round-3 review MINOR-4 / mutation MU9: the version dimension had no test at
  // all, so deleting the version comparison stayed green. Same fingerprint,
  // different version must be treated as "the key moved".
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    pending: [{ ...incomingRequest }],
    // Same fingerprint as the pinned v2/BBBB, but the server now publishes v3.
    status: [keyStatusPayload({ userId: 7, version: 3, fingerprint: 'BBBB', trust: 'verified' })],
    api: async () => ({ status: 'ok', request: acceptedVerification(incomingRequest) }),
  });

  await world.context.respondToKeyVerification(42, 'accept');

  assert.deepEqual(
    world.versions(7),
    { 1: 'AAAA' },
    'a version bump invalidates the authorisation even when the fingerprint matches'
  );
  assert.deepEqual(world.cacheCalls, [], 'and the newer version must not become the send key');
  assert.equal(
    world.toasts[world.toasts.length - 1],
    'Keys changed. Nothing was trusted. Start verification again.',
    'the refusal is reported as a changed key'
  );
});

test('the same fingerprint at the pinned version still grants', async () => {
  // Control for the case above: only the version differs, so if this passes and
  // the previous one fails, the version comparison is what is doing the work.
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    pending: [{ ...incomingRequest }],
    status: [keyStatusPayload({ userId: 7, version: 2, fingerprint: 'BBBB', trust: 'verified' })],
    api: async () => ({ status: 'ok', request: acceptedVerification(incomingRequest) }),
  });

  await world.context.respondToKeyVerification(42, 'accept');

  assert.equal(world.versions(7)['2'], 'BBBB');
  assert.equal(world.toasts[world.toasts.length - 1], 'Security verification completed');
});

test('a snapshot-consistent accept whose key rotated mid-flight reports the refusal', async () => {
  // Round-3 review MU2: moving the success toast out of the `outcome.granted`
  // check stayed green because every "no success" case made the grant
  // impossible. This one reaches the granted===false branch through the normal
  // path: the snapshot matches and the server confirms, but the live endpoint
  // has already rotated.
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    pending: [{ ...incomingRequest }],
    status: [rotatedContactStatus('DDDD')],
    api: async () => ({ status: 'ok', request: acceptedVerification(incomingRequest) }),
  });

  const outcome = await world.context.refreshKeyVerificationAfterChange(7, {
    grantTrustFor: acceptedVerification(incomingRequest),
  });
  assert.equal(outcome.granted, false, 'the refresh reports no grant');

  await world.context.respondToKeyVerification(42, 'accept');
  assert.equal(
    world.toasts.some(text => text === 'Security verification completed'),
    false,
    'no success may be claimed when granted is false'
  );
  assert.match(world.toasts[world.toasts.length - 1], /Nothing was trusted/);
});

test('a socket accept whose key rotated after the snapshot grants nothing and claims no success', async () => {
  // Round-3 review MU2, socket form: the snapshot matches and the event is
  // genuine, but the live endpoint has moved on, so granted is false. Moving the
  // success toast out of the `outcome.granted` gate must fail here.
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    pending: [{ ...incomingRequest }],
    status: [rotatedContactStatus('DDDD')],
  });

  world.context.handleKeyVerificationNew({
    data: { event: 'accepted', verification: acceptedVerification(incomingRequest) },
  });
  await flush();

  assert.deepEqual(world.versions(7), { 1: 'AAAA' }, 'the rotation invalidates the authorisation');
  assert.equal(
    world.toasts.some(text => text === 'Security verification completed'),
    false,
    'no success may be claimed when the socket path granted nothing'
  );
  assert.match(world.toasts[world.toasts.length - 1], /Nothing was trusted/);
});

test('a failed respond call leaves no verified-looking local state', async () => {
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    pending: [{ ...incomingRequest }],
    status: [rotatedContactStatus('BBBB', { trust: 'verified' })],
    api: async () => {
      throw new Error('Network unreachable');
    },
  });

  await world.context.respondToKeyVerification(42, 'accept');

  assert.deepEqual(world.versions(7), { 1: 'AAAA' }, 'a failed authorisation must not grant trust');
  assert.deepEqual(world.cacheCalls, []);
  assert.deepEqual(world.statusCalls, [], 'no refresh may run after the request failed');
  assert.equal(world.toasts[world.toasts.length - 1], 'Network unreachable');
  assert.equal(world.pendingFetches.length, 1, 'the tray is re-synced after a failure');
});

test('an accepted socket event trusts only a fingerprint matching the accepted request', async () => {
  // The event must be identifiable as the request the tray showed, so the
  // snapshot has to be present (round-2 M-1: a crafted event naming another
  // contact must not be able to pick who gets trusted).
  const matching = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    pending: [{ ...incomingRequest }],
    status: [rotatedContactStatus('BBBB', { trust: 'verified' })],
  });
  matching.context.handleKeyVerificationNew({
    data: { event: 'accepted', verification: acceptedVerification(incomingRequest) },
  });
  await flush();
  assert.equal(matching.versions(7)['2'], 'BBBB', 'the peer of an accepted request may be trusted');

  const mismatched = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    pending: [{ ...incomingRequest }],
    status: [rotatedContactStatus('CCCC')],
  });
  mismatched.context.handleKeyVerificationNew({
    data: { event: 'accepted', verification: acceptedVerification(incomingRequest) },
  });
  await flush();
  assert.deepEqual(
    mismatched.versions(7),
    { 1: 'AAAA' },
    'an accepted event for a different fingerprint must not trust the current key'
  );
});

test('a response body naming another version never becomes the authorisation', async () => {
  // Round-2 review M-1: the authorisation must come from the snapshot the user
  // clicked, never from the response body. Here the tray held v1/AAAA while the
  // 2xx body claims it accepted v2/BBBB.
  const stalePending = {
    ...incomingRequest,
    requester_key_version: 1,
    requester_key_fingerprint: 'AAAA',
  };
  const confirmed = {
    ...incomingRequest,
    status: 'accepted',
    requester_key_version: 2,
    requester_key_fingerprint: 'BBBB',
  };
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    pending: [stalePending],
    status: [rotatedContactStatus('BBBB', { trust: 'verified' })],
    api: async () => ({ status: 'ok', request: confirmed }),
  });

  await world.context.respondToKeyVerification(42, 'accept');

  assert.deepEqual(
    world.versions(7),
    { 1: 'AAAA' },
    'the body must not upgrade the authorisation to a fingerprint the user never approved'
  );
  assert.deepEqual(world.cacheCalls, [], 'and it must not reach the send cache either');
});

test('a response body naming another contact cannot grant trust to anyone', async () => {
  // Round-2 review M-1: a hostile or buggy relay swaps the peer in the 2xx body.
  const world = createWorld({
    stored: {
      [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } },
      [TRUST_KEY + 8]: { user_id: 8, versions: { 1: 'EEEE' } },
    },
    pending: [{ ...incomingRequest }],
    status: [
      rotatedContactStatus('BBBB', { trust: 'verified' }),
      keyStatusPayload({ userId: 8, version: 5, fingerprint: 'EVIL', trust: 'verified' }),
    ],
    api: async () => ({
      status: 'ok',
      request: {
        ...incomingRequest,
        requester_id: 8,
        requester_key_version: 5,
        requester_key_fingerprint: 'EVIL',
        status: 'accepted',
      },
    }),
  });

  await world.context.respondToKeyVerification(42, 'accept');

  assert.deepEqual(world.versions(8), { 1: 'EEEE' }, 'the substituted contact is never trusted');
  assert.deepEqual(world.versions(7), { 1: 'AAAA' }, 'and neither is a peer the user did not confirm');
  assert.deepEqual(world.cacheCalls, [], 'no send key may be replaced by the substituted fingerprint');
  assert.equal(
    world.toasts[world.toasts.length - 1].includes('Security verification completed'),
    false,
    'a refused substitution must not report success'
  );
});

test('the local action, not the response body, decides whether trust is granted', async () => {
  // Round-2 review H1 / mutation M2: a decline whose 2xx body claims "accepted"
  // must still grant nothing. Without this case, widening the first gate to
  // `action === "accept" || action === "decline"` stays invisible to the suite.
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    pending: [{ ...incomingRequest }],
    status: [rotatedContactStatus('BBBB', { trust: 'verified' })],
    api: async () => ({ status: 'ok', request: acceptedVerification(incomingRequest) }),
  });

  await world.context.respondToKeyVerification(42, 'decline');

  assert.deepEqual(
    world.versions(7),
    { 1: 'AAAA' },
    'the user clicked decline, so the server-side "accepted" body is not an authorisation'
  );
  assert.deepEqual(world.cacheCalls, [], 'no send key may be replaced by a declined request');
});

test('a double click sends one respond request and reports one outcome', async () => {
  // Round-2 review m-1: two POSTs raced two contradictory toasts.
  let posts = 0;
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    pending: [{ ...incomingRequest }],
    status: [rotatedContactStatus('BBBB', { trust: 'verified' })],
    api: async () => {
      posts += 1;
      return { status: 'ok', request: acceptedVerification(incomingRequest) };
    },
  });

  await Promise.all([
    world.context.respondToKeyVerification(42, 'accept'),
    world.context.respondToKeyVerification(42, 'accept'),
  ]);

  assert.equal(posts, 1, 'the in-flight guard keeps the authorization single-shot');
  assert.equal(world.toasts.length, 1, 'only one outcome is reported');
  assert.equal(world.versions(7)['2'], 'BBBB', 'and the single request still grants correctly');
});

test('the local snapshot, not the response body, is the authorisation object', async () => {
  // Round-2 review M-1: even when the body is honest about the request id, a
  // substituted fingerprint must not be trusted.
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    pending: [{ ...incomingRequest }],
    status: [keyStatusPayload({ userId: 7, version: 9, fingerprint: 'XXXX', trust: 'verified' })],
    api: async () => ({
      status: 'ok',
      request: {
        ...incomingRequest,
        requester_key_version: 9,
        requester_key_fingerprint: 'XXXX',
        status: 'accepted',
      },
    }),
  });

  await world.context.respondToKeyVerification(42, 'accept');

  assert.deepEqual(
    world.versions(7),
    { 1: 'AAAA' },
    'the body cannot pick a different version of the approved contact'
  );
  assert.equal(
    world.toasts[world.toasts.length - 1],
    'Keys changed. Nothing was trusted. Start verification again.',
    'the user is told nothing was trusted instead of seeing a success toast'
  );
});

test('an honest accept of the held snapshot still grants and reports success', async () => {
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    pending: [{ ...incomingRequest }],
    status: [rotatedContactStatus('BBBB', { trust: 'verified' })],
    api: async () => ({ status: 'ok', request: acceptedVerification(incomingRequest) }),
  });

  await world.context.respondToKeyVerification(42, 'accept');

  assert.equal(world.versions(7)['2'], 'BBBB', 'the approved fingerprint is still trusted');
  assert.equal(world.cacheCalls.length, 1, 'and still replaces the cached send key');
  assert.equal(world.toasts[world.toasts.length - 1], 'Security verification completed');
});

test('the echo of our own accept after the HTTP success is silent', async () => {
  // Round-3 review MAJOR-1 / E5: the server pushes `accepted` to both parties, so
  // this page receives an echo of the accept it just performed. The tray entry is
  // already gone, so without the self-echo record the user is told "nothing was
  // trusted" immediately after a real success.
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    pending: [{ ...incomingRequest }],
    status: [rotatedContactStatus('BBBB', { trust: 'verified' })],
    api: async () => ({ status: 'ok', request: acceptedVerification(incomingRequest) }),
  });

  await world.context.respondToKeyVerification(42, 'accept');
  const afterHttp = world.toasts.slice();
  assert.deepEqual(afterHttp, ['Security verification completed'], 'the HTTP path reports one success');

  world.context.handleKeyVerificationNew({
    data: { event: 'accepted', verification: acceptedVerification(incomingRequest) },
  });
  await flush();

  assert.deepEqual(
    world.toasts,
    ['Security verification completed'],
    'the echo adds no duplicate success and no contradictory refusal'
  );
  assert.deepEqual(world.versions(7), { 1: 'AAAA', 2: 'BBBB' }, 'trust was granted exactly once');
  assert.equal(world.cacheCalls.length, 1, 'and the send cache was written once');
});

test('the echo of our own accept that overtakes the HTTP response is silent', async () => {
  // Round-3 review MAJOR-1 / E1-E3: the same race in the other order. The echo
  // arrives while the POST is still in flight, so the tray entry is already
  // recorded as locally answered before the response lands.
  let releaseResponse;
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    pending: [{ ...incomingRequest }],
    status: [rotatedContactStatus('BBBB', { trust: 'verified' })],
    api: () =>
      new Promise(resolve => {
        releaseResponse = () =>
          resolve({ status: 'ok', request: acceptedVerification(incomingRequest) });
      }),
  });

  const pendingPost = world.context.respondToKeyVerification(42, 'accept');
  // the socket frame wins the race
  world.context.handleKeyVerificationNew({
    data: { event: 'accepted', verification: acceptedVerification(incomingRequest) },
  });
  await flush();
  releaseResponse();
  await pendingPost;
  await flush();

  assert.deepEqual(
    world.toasts,
    ['Security verification completed'],
    'exactly one success, never two, and no refusal'
  );
  assert.deepEqual(world.versions(7), { 1: 'AAAA', 2: 'BBBB' }, 'granted exactly once');
});

test('an accept that is not ours still fails closed with a readable message', async () => {
  // The counterpart of the two echo cases: an id this page never answered keeps
  // the round-3 fail-closed behaviour, so the self-echo guard cannot be used to
  // silence a genuine event.
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    status: [rotatedContactStatus('BBBB', { trust: 'verified' })],
  });

  world.context.handleKeyVerificationNew({
    data: { event: 'accepted', verification: acceptedVerification(incomingRequest) },
  });
  await flush();

  assert.deepEqual(world.versions(7), { 1: 'AAAA' }, 'nothing is granted');
  assert.equal(world.toasts.length, 1, 'one message');
  assert.match(world.toasts[0], /no longer in this page's pending list/);
});

test('a socket accept naming another contact grants nothing', async () => {
  // Round-2 M-1, socket form: the tray held contact 7, the event names contact 8.
  const world = createWorld({
    stored: {
      [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } },
      [TRUST_KEY + 8]: { user_id: 8, versions: { 1: 'EEEE' } },
    },
    pending: [{ ...incomingRequest }],
    status: [
      rotatedContactStatus('BBBB', { trust: 'verified' }),
      keyStatusPayload({ userId: 8, version: 5, fingerprint: 'EVIL', trust: 'verified' }),
    ],
  });

  world.context.handleKeyVerificationNew({
    data: {
      event: 'accepted',
      verification: {
        ...incomingRequest,
        requester_id: 8,
        requester_key_version: 5,
        requester_key_fingerprint: 'EVIL',
        status: 'accepted',
      },
    },
  });
  await flush();

  assert.deepEqual(world.versions(8), { 1: 'EEEE' }, 'the substituted contact is never trusted');
  assert.deepEqual(world.versions(7), { 1: 'AAAA' }, 'nor is a peer the user did not confirm');
  assert.deepEqual(world.cacheCalls, []);
});

test('a socket accept with no tray snapshot grants nothing and says so', async () => {
  // Round-2 review G2 / M-1 (socket path): the user never saw this request in
  // this tab, so "the server accepted it" is not "the user approved it". Even a
  // self-consistent event with a matching live fingerprint must grant nothing.
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    status: [rotatedContactStatus('BBBB', { trust: 'verified' })],
  });

  world.context.handleKeyVerificationNew({
    data: { event: 'accepted', verification: acceptedVerification(incomingRequest) },
  });
  await flush();

  assert.deepEqual(world.versions(7), { 1: 'AAAA' }, 'no snapshot means no user authorisation');
  assert.deepEqual(world.cacheCalls, [], 'and nothing may reach the send cache');
  assert.deepEqual(world.statusCalls, [], 'the fingerprint endpoint is not even consulted');
  assert.equal(
    world.toasts.some(text => text === 'Security verification completed'),
    false,
    'a refused event must not claim success'
  );
  assert.equal(world.toasts.length, 1, 'exactly one readable message is shown');
  assert.match(world.toasts[0], /no longer in this page's pending list/);
});

test('a socket accept with no snapshot cannot trust a rotated or substituted key', async () => {
  const rotated = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    status: [rotatedContactStatus('CCCC')],
  });
  rotated.context.handleKeyVerificationNew({
    data: { event: 'accepted', verification: acceptedVerification(incomingRequest) },
  });
  await flush();
  assert.deepEqual(rotated.versions(7), { 1: 'AAAA' }, 'a rotated key is never trusted');

  const other = createWorld({
    stored: {
      [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } },
      [TRUST_KEY + 9]: { user_id: 9, versions: { 1: 'OLD9' } },
    },
    status: [
      rotatedContactStatus('BBBB', { trust: 'verified' }),
      keyStatusPayload({ userId: 9, version: 2, fingerprint: 'EVIL', trust: 'verified' }),
    ],
  });
  other.context.handleKeyVerificationNew({
    data: {
      event: 'accepted',
      verification: {
        ...incomingRequest,
        requester_id: 9,
        requester_key_version: 2,
        requester_key_fingerprint: 'EVIL',
        status: 'accepted',
      },
    },
  });
  await flush();
  assert.deepEqual(other.versions(9), { 1: 'OLD9' }, 'an unrelated contact is never trusted');
  assert.deepEqual(other.cacheCalls, []);
});

test('a socket accept with a matching snapshot grants and reports success', async () => {
  // The counterpart of the two cases above: a real accept of the request the
  // user actually saw still works end to end.
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    pending: [{ ...incomingRequest }],
    status: [rotatedContactStatus('BBBB', { trust: 'verified' })],
  });

  world.context.handleKeyVerificationNew({
    data: { event: 'accepted', verification: acceptedVerification(incomingRequest) },
  });
  await flush();

  assert.equal(world.versions(7)['2'], 'BBBB', 'the pinned fingerprint is trusted');
  assert.equal(world.versions(7)['1'], 'AAAA', 'history is preserved');
  assert.equal(world.cacheCalls.length, 1, 'and the send cache follows');
  assert.equal(
    world.toasts[world.toasts.length - 1],
    'Security verification completed',
    'success is reported only after a real grant'
  );
  assert.equal(
    world.toasts.some(text => /no longer in this page's pending list/.test(text)),
    false
  );
});

test('a response body with a different request id is treated as a mismatch', async () => {
  // Round-2 review F-1: only the id comparison rejects this one, so without a
  // dedicated case deleting that check would go unnoticed.
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    pending: [{ ...incomingRequest }],
    status: [rotatedContactStatus('BBBB', { trust: 'verified' })],
    api: async () => ({
      status: 'ok',
      request: { ...incomingRequest, id: 99, status: 'accepted' },
    }),
  });

  await world.context.respondToKeyVerification(42, 'accept');

  assert.deepEqual(world.versions(7), { 1: 'AAAA' }, 'a different request id is not our accept');
  assert.deepEqual(world.cacheCalls, []);
  assert.equal(
    world.toasts[world.toasts.length - 1].includes('Security verification completed'),
    false
  );
  assert.match(world.toasts[world.toasts.length - 1], /different verification request/);
});

test('an unreadable 204-style response reports a readable message, not a JSON error', async () => {
  // Round-2 review F-3: a 204 (or proxy-stripped body) makes apiFetch throw the
  // raw parser error. Still safe, but the toast must be actionable.
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    pending: [{ ...incomingRequest }],
    status: [rotatedContactStatus('BBBB', { trust: 'verified' })],
    api: async () => {
      throw new SyntaxError('Unexpected end of JSON input');
    },
  });

  await world.context.respondToKeyVerification(42, 'accept');

  assert.deepEqual(world.versions(7), { 1: 'AAAA' }, 'nothing is granted');
  const last = world.toasts[world.toasts.length - 1];
  assert.equal(last.includes('Unexpected end of JSON input'), false, 'no raw parser text');
  assert.match(last, /returned no confirmation/);
  assert.match(last, /Start verification again/);
});

test('granting trust is scoped to the contact the verification named', async () => {
  // Contact 8 must publish the very fingerprint contact 7's request pinned,
  // otherwise the fingerprint comparison alone would reject and this case would
  // pass even with the `peer.userId === userId` guard removed (review M7).
  const world = createWorld({
    stored: {
      [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } },
      [TRUST_KEY + 8]: { user_id: 8, versions: { 1: 'EEEE' } },
    },
    status: [
      rotatedContactStatus('BBBB', { trust: 'verified' }),
      keyStatusPayload({ userId: 8, version: 2, fingerprint: 'BBBB', trust: 'verified' }),
    ],
  });

  const outcome = await world.context.refreshKeyVerificationAfterChange(8, {
    grantTrustFor: acceptedVerification(incomingRequest),
  });

  assert.deepEqual(
    world.versions(8),
    { 1: 'EEEE' },
    'the accepted request named contact 7, so contact 8 stays untouched'
  );
  assert.deepEqual(world.cacheCalls, [], 'and contact 8 never becomes the send key');
  assert.equal(outcome.granted, false, 'no grant is reported for the wrong contact');
});

test('an accept without a response body still trusts only the held request fingerprint', async () => {
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    pending: [{ ...incomingRequest }],
    status: [rotatedContactStatus('BBBB', { trust: 'verified' })],
    api: async () => ({ status: 'ok' }),
  });

  await world.context.respondToKeyVerification(42, 'accept');

  assert.equal(world.versions(7)['2'], 'BBBB');
  assert.equal(world.cacheCalls.length, 1, 'the send cache still follows a genuine accept');
});

test('a 2xx accept body that is not an accept grants nothing', async () => {
  const world = createWorld({
    stored: { [TRUST_KEY + 7]: { user_id: 7, versions: { 1: 'AAAA' } } },
    pending: [{ ...incomingRequest }],
    status: [rotatedContactStatus('BBBB', { trust: 'verified' })],
    api: async () => ({ status: 'ok', request: { ...incomingRequest, status: 'expired' } }),
  });

  await world.context.respondToKeyVerification(42, 'accept');

  assert.deepEqual(
    world.versions(7),
    { 1: 'AAAA' },
    'the server says the request was not accepted, so no trust is granted'
  );
  assert.deepEqual(world.cacheCalls, []);
});
