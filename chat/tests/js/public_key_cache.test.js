const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

for (const moduleName of ['private-chat-e2ee.js', 'group-chat-e2ee.js']) {
  test(moduleName + ': exact versions never fall back, versionless offline sends do', () => {
    const source = fs.readFileSync(path.resolve(__dirname, '../../../static/js', moduleName), 'utf8').replace(/\r\n/g, '\n');
    const helper = source.match(/  function _getCachedPublicKey\([\s\S]*?\n  \}/);
    const trust = source.match(/  function trustPeerKey\([\s\S]*?\n  \}/);
    assert.ok(helper && trust, 'exercise production cache and trust functions');
    const v1 = { user_id: 1, key_version: 1 };
    const v3 = { user_id: 1, key_version: 3 };
    const cache = new Map([['1:v1', v1], ['1:v3', v3], ['10:v9', { user_id: 10, key_version: 9 }]]);
    const context = vm.createContext({
      _publicKeyCache: cache, TRUST_STORAGE_PREFIX: 'trust:', IDENTITY_ALGORITHM: 'ECDH-P256',
      loadPeerTrust: () => null, localStorage: { setItem() {} },
      _cachePublicKey: (id, version, key) => cache.set(id + ':v' + version, key),
    });
    vm.runInContext(helper[0] + '\n' + trust[0], context);
    assert.equal(context._getCachedPublicKey(1, 1), v1);
    assert.equal(context._getCachedPublicKey(1, 3), v3);
    assert.equal(context._getCachedPublicKey(1, 2), null, 'missing historical version is never substituted');
    assert.equal(context._getCachedPublicKey(1, 4), null, 'new version is never replaced with an old key');
    assert.equal(context._getCachedPublicKey(1, null), v3, 'offline versionless send uses newest cached key');
    assert.equal(context._getCachedPublicKey(1, undefined), v3);
    assert.equal(context._getCachedPublicKey(2, null), null, 'different user prefix is never used');
    const v4 = { user_id: 1, key_version: 4, algorithm: 'ECDH-P256', identity_public_key: 'public-key', key_fingerprint: 'fingerprint' };
    context.trustPeerKey(v4);
    assert.equal(context._getCachedPublicKey(1, null), v4, 'explicitly trusted rotation becomes the cached send key');
    assert.equal(context._getCachedPublicKey(1, 1), v1, 'historical key remains available');
  });
}
