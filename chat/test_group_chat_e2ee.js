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

function groupPayload(encrypted, recipient) {
  return {
    algorithm: encrypted.algorithm,
    group_id: 77,
    membership_version: encrypted.membership_version,
    sender_id: 1,
    receiver_id: recipient.receiver_id,
    sender_key_version: encrypted.sender_key_version,
    receiver_key_version: recipient.receiver_key_version,
    ciphertext: recipient.ciphertext,
    nonce: recipient.nonce,
    auth_tag: recipient.auth_tag,
    sender_ephemeral_public_key: recipient.sender_ephemeral_public_key
  };
}

function loadGroupModule(localRecord, serverKeys, privateKeys) {
  const storage = new Map();
  async function importPrivateKey(privateKeyJwk) {
    return webcrypto.subtle.importKey(
      'jwk',
      privateKeyJwk,
      { name: 'ECDH', namedCurve: 'P-256' },
      false,
      ['deriveBits']
    );
  }
  const context = {
    TextDecoder,
    TextEncoder,
    URL,
    CustomEvent: function CustomEvent(type, init) {
      return { type, detail: init && init.detail };
    },
    atob: value => Buffer.from(value, 'base64').toString('binary'),
    btoa: value => Buffer.from(value, 'binary').toString('base64'),
    encodeURIComponent,
    fetch: async (url, options = {}) => {
      const batch = url.match(/\/api\/keys\/batch\//);
      if (batch) {
        const body = JSON.parse(options.body || '{}');
        const ids = body.user_ids || [];
        const keys = ids
          .map(userId => {
            const userKeys = serverKeys.get(Number(userId)) || [];
            return userKeys[userKeys.length - 1] || null;
          })
          .filter(Boolean);
        return { ok: true, status: 200, json: async () => ({ keys }) };
      }
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
      setItem: (key, value) => storage.set(key, value),
      removeItem: key => storage.delete(key)
    },
    document: { cookie: '' },
    crypto: webcrypto,
    dispatchEvent: () => true
  };
  context.window = context;
  context.iChatKeyManager = {
    loadCurrentRecord: () => localRecord.value,
    getCurrentPrivateKey: async keyVersion => {
      if (keyVersion && privateKeys.has(Number(keyVersion))) {
        return importPrivateKey(privateKeys.get(Number(keyVersion)));
      }
      if (localRecord.value && privateKeys.has(Number(localRecord.value.key_version))) {
        return importPrivateKey(privateKeys.get(Number(localRecord.value.key_version)));
      }
      return null;
    }
  };
  const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'group-chat-e2ee.js'), 'utf8');
  vm.runInNewContext(source, context);
  return context;
}

async function run() {
  const alice = await identityRecord(1);
  const bob = await identityRecord(2);
  const serverKeys = new Map([[1, [alice.server]], [2, [bob.server]]]);
  const aliceRecord = { value: alice.local };
  const bobRecord = { value: bob.local };
  const alicePrivateKeys = new Map([[1, alice.local.private_key]]);
  const bobPrivateKeys = new Map([[1, bob.local.private_key]]);
  const aliceBrowser = loadGroupModule(aliceRecord, serverKeys, alicePrivateKeys);
  const bobBrowser = loadGroupModule(bobRecord, serverKeys, bobPrivateKeys);

  const encrypted = await aliceBrowser.iChatGroupE2EE.encryptGroupMessage({
    plaintext: 'group hello',
    groupId: 77,
    membershipVersion: 1,
    memberIds: [1, 2]
  });
  const bobCopy = encrypted.recipients.find(r => r.receiver_id === 2);
  const aliceCopy = encrypted.recipients.find(r => r.receiver_id === 1);
  assert.equal(await bobBrowser.iChatGroupE2EE.decryptGroupMessage(groupPayload(encrypted, bobCopy)), 'group hello');
  assert.equal(await aliceBrowser.iChatGroupE2EE.decryptGroupMessage(groupPayload(encrypted, aliceCopy)), 'group hello');

  await assert.rejects(
    () => bobBrowser.iChatGroupE2EE.decryptGroupMessage({
      ...groupPayload(encrypted, bobCopy),
      sender_ephemeral_public_key: null
    }),
    error => error.code === 'damaged_ciphertext'
  );

  const rotatedBob = await identityRecord(2, 2);
  bobPrivateKeys.set(2, rotatedBob.local.private_key);
  serverKeys.get(2).push(rotatedBob.server);
  bobRecord.value = rotatedBob.local;
  aliceBrowser.iChatGroupE2EE.trustPeerKey(rotatedBob.server);

  const encryptedAfterBobReset = await aliceBrowser.iChatGroupE2EE.encryptGroupMessage({
    plaintext: 'group hello after bob reset',
    groupId: 77,
    membershipVersion: 1,
    memberIds: [1, 2]
  });
  const rotatedBobCopy = encryptedAfterBobReset.recipients.find(r => r.receiver_id === 2);
  assert.equal(rotatedBobCopy.receiver_key_version, 2);
  assert.equal(
    await bobBrowser.iChatGroupE2EE.decryptGroupMessage(groupPayload(encryptedAfterBobReset, rotatedBobCopy)),
    'group hello after bob reset'
  );

  console.log('group-chat-e2ee: all tests passed');
}

run().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
