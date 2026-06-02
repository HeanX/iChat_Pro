const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');

function bytesToBase64(bytes) {
  return Buffer.from(bytes).toString('base64');
}

async function identityRecord(userId, keyVersion = 1) {
  const pair = await webcrypto.subtle.generateKey(
    { name: 'ECDH', namedCurve: 'P-256' },
    true,
    ['deriveBits']
  );
  const publicSpki = await webcrypto.subtle.exportKey('spki', pair.publicKey);
  const fingerprint = Buffer.from(await webcrypto.subtle.digest('SHA-256', publicSpki)).toString('hex').toUpperCase();
  return {
    local: {
      user_id: String(userId),
      key_version: keyVersion,
      private_key: await webcrypto.subtle.exportKey('jwk', pair.privateKey)
    },
    server: {
      user_id: userId,
      identity_public_key: bytesToBase64(publicSpki),
      key_fingerprint: fingerprint,
      algorithm: 'ECDH-P256',
      key_version: keyVersion
    }
  };
}

function loadModule(localRecord, serverKeys) {
  const storage = new Map();
  const context = {
    TextDecoder,
    TextEncoder,
    URL,
    atob: value => Buffer.from(value, 'base64').toString('binary'),
    btoa: value => Buffer.from(value, 'binary').toString('base64'),
    encodeURIComponent,
    fetch: async url => {
      const [, userIdText, keyVersionText] = url.match(/\/api\/keys\/(\d+)\/(?:(\d+)\/)?/);
      const userId = Number(userIdText);
      const requestedVersion = keyVersionText ? Number(keyVersionText) : null;
      const keys = serverKeys.get(userId) || [];
      const key = requestedVersion === null
        ? keys[keys.length - 1]
        : keys.find(k => k.key_version === requestedVersion);
      return key
        ? { ok: true, status: 200, json: async () => ({ key }) }
        : { ok: false, status: 404, json: async () => ({ error: 'public_key_not_found' }) };
    },
    localStorage: {
      getItem: key => storage.get(key) || null,
      setItem: (key, value) => storage.set(key, value)
    },
    crypto: webcrypto
  };
  context.window = context;
  context.iChatKeyManager = { loadCurrentRecord: () => localRecord.value };
  const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'private-chat-e2ee.js'), 'utf8');
  vm.runInNewContext(source, context);
  return context;
}

async function run() {
  const alice = await identityRecord(1);
  const bob = await identityRecord(2);
  const serverKeys = new Map([[1, [alice.server]], [2, [bob.server]]]);
  const aliceRecord = { value: alice.local };
  const bobRecord = { value: bob.local };
  const aliceBrowser = loadModule(aliceRecord, serverKeys);
  const bobBrowser = loadModule(bobRecord, serverKeys);

  // Alice sends a message to Bob
  const encrypted = await aliceBrowser.iChatPrivateE2EE.encryptPrivateMessage({
    plaintext: 'secret hello',
    conversationId: 42,
    receiverId: 2
  });
  assert.equal(encrypted.algorithm, 'AES-256-GCM');
  assert.equal(encrypted.sender_id, 1);
  assert.equal(encrypted.receiver_id, 2);
  assert.equal(JSON.stringify(encrypted).includes('secret hello'), false);
  assert.equal(await bobBrowser.iChatPrivateE2EE.decryptPrivateMessage(encrypted), 'secret hello');

  // Bob sends a message to Alice (v1)
  const encryptedFromBob = await bobBrowser.iChatPrivateE2EE.encryptPrivateMessage({
    plaintext: 'hello from bob v1',
    conversationId: 42,
    receiverId: 1
  });
  assert.equal(await aliceBrowser.iChatPrivateE2EE.decryptPrivateMessage(encryptedFromBob), 'hello from bob v1');

  const tampered = { ...encrypted, ciphertext: bytesToBase64(Buffer.from('tampered')) };
  await assert.rejects(
    () => bobBrowser.iChatPrivateE2EE.decryptPrivateMessage(tampered),
    error => error.code === 'damaged_ciphertext'
  );

  const rotatedBob = await identityRecord(2, 2);
  serverKeys.get(2).push(rotatedBob.server);
  await assert.rejects(
    () => aliceBrowser.iChatPrivateE2EE.encryptPrivateMessage({
      plaintext: 'blocked until verified',
      conversationId: 42,
      receiverId: 2
    }),
    error => error.code === 'peer_key_changed'
  );

  // Alice explicitly trusts Bob's new rotated key version 2
  aliceBrowser.iChatPrivateE2EE.trustPeerKey(rotatedBob.server);

  // Alice can now encrypt message under version 2
  const encrypted2 = await aliceBrowser.iChatPrivateE2EE.encryptPrivateMessage({
    plaintext: 'new message under v2 key',
    conversationId: 42,
    receiverId: 2
  });
  assert.equal(encrypted2.receiver_key_version, 2);

  // Bob can decrypt the new message using his v2 key
  const oldBobRecordValue = bobRecord.value;
  bobRecord.value = rotatedBob.local;
  assert.equal(await bobBrowser.iChatPrivateE2EE.decryptPrivateMessage(encrypted2), 'new message under v2 key');

  // Alice can still decrypt the old message from Bob (version 1)
  assert.equal(await aliceBrowser.iChatPrivateE2EE.decryptPrivateMessage(encryptedFromBob), 'hello from bob v1');

  // Restore Bob's local key state
  bobRecord.value = oldBobRecordValue;

  aliceBrowser.localStorage.setItem('ichat_peer_identity:2', '{');
  await assert.rejects(
    () => aliceBrowser.iChatPrivateE2EE.encryptPrivateMessage({
      plaintext: 'damaged trust record',
      conversationId: 42,
      receiverId: 2
    }),
    error => error.code === 'peer_trust_invalid'
  );

  aliceRecord.value = null;
  await assert.rejects(
    () => aliceBrowser.iChatPrivateE2EE.encryptPrivateMessage({
      plaintext: 'missing key',
      conversationId: 42,
      receiverId: 2
    }),
    error => error.code === 'local_key_missing'
  );

  console.log('private-chat-e2ee: all tests passed');
}

run().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
