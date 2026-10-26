/**
 * P4 T22 review: mobile shell configuration contract (origin normalisation,
 * HTTPS enforcement, WSS derivation, live-reload gating). Pure Node — no DOM,
 * no emulator, no network.
 */
const assert = require('node:assert');
const test = require('node:test');
const path = require('node:path');

const cfgPath = path.join(__dirname, '..', 'www', 'js', 'mobile-config.js');
const {
  normalizeOrigin,
  isLoopback,
  buildWsUrl,
  resolveMobileConfig,
  buildCapacitorConfig,
} = require(cfgPath);

test('normalizeOrigin lowercases host and drops default ports', () => {
  assert.strictEqual(normalizeOrigin('HTTPS://CHAT.Example.COM:443/'), 'https://chat.example.com');
  assert.strictEqual(normalizeOrigin('http://chat.example.com:80'), 'http://chat.example.com');
  assert.strictEqual(normalizeOrigin('https://chat.example.com:8443'), 'https://chat.example.com:8443');
  assert.strictEqual(normalizeOrigin('  https://chat.example.com:8443  '), 'https://chat.example.com:8443');
});

test('normalizeOrigin rejects non-http schemes and garbage', () => {
  assert.strictEqual(normalizeOrigin('ftp://chat.example.com'), null);
  assert.strictEqual(normalizeOrigin('javascript:alert(1)'), null);
  assert.strictEqual(normalizeOrigin('not a url'), null);
  assert.strictEqual(normalizeOrigin(''), null);
  assert.strictEqual(normalizeOrigin(null), null);
  assert.strictEqual(normalizeOrigin(undefined), null);
});

test('normalizeOrigin agrees with desktop app-config for the same inputs', () => {
  const desktop = require(path.join(__dirname, '..', '..', 'desktop', 'app-config.js'));
  const samples = [
    'HTTPS://SUB.20060810.XYZ:8443 ',
    'https://sub.example.com:443',
    'http://sub.example.com:80',
    'https://localhost:8443',
  ];
  for (const s of samples) {
    assert.strictEqual(normalizeOrigin(s), desktop.normalizeOrigin(s), 'mismatch for ' + s);
  }
});

test('isLoopback only accepts loopback hosts', () => {
  assert.strictEqual(isLoopback('http://localhost:8000'), true);
  assert.strictEqual(isLoopback('http://127.0.0.1:8000'), true);
  assert.strictEqual(isLoopback('https://chat.example.com'), false);
  assert.strictEqual(isLoopback('garbage'), false);
});

test('buildWsUrl derives /ws/chat/ from the origin', () => {
  assert.strictEqual(buildWsUrl('https://chat.20060810.xyz:8443'), 'wss://chat.20060810.xyz:8443/ws/chat/');
  assert.strictEqual(buildWsUrl('http://localhost:8000'), 'ws://localhost:8000/ws/chat/');
  assert.strictEqual(buildWsUrl('https://chat.example.com:443'), 'wss://chat.example.com/ws/chat/');
  assert.strictEqual(buildWsUrl('ftp://x'), null);
});

test('remote plain HTTP is rejected with an HTTPS explanation', () => {
  const out = resolveMobileConfig({ ICHAT_MOBILE_SERVER_URL: 'http://chat.example.com:8000' });
  assert.strictEqual(out.mode, 'unconfigured');
  assert.strictEqual(out.insecure, true);
  assert.strictEqual(out.wsUrl, null);
  assert.ok(out.message.indexOf('HTTPS') !== -1, 'message explains HTTPS requirement: ' + out.message);
});

test('loopback plain HTTP stays available for local development', () => {
  const out = resolveMobileConfig({ ICHAT_MOBILE_SERVER_URL: 'http://localhost:8000' });
  assert.strictEqual(out.mode, 'configured');
  assert.strictEqual(out.insecure, true);
  assert.strictEqual(out.wsUrl, 'ws://localhost:8000/ws/chat/');
});

test('missing configuration reports unconfigured without throwing', () => {
  for (const env of [{}, { ICHAT_MOBILE_SERVER_URL: '' }, { ICHAT_MOBILE_SERVER_URL: '   ' }]) {
    const out = resolveMobileConfig(env);
    assert.strictEqual(out.mode, 'unconfigured');
    assert.strictEqual(out.origin, null);
    assert.ok(out.message.length > 0, 'message present');
  }
});

test('release resolution never sets server.url (no live-reload dependency)', () => {
  const out = resolveMobileConfig({ ICHAT_MOBILE_SERVER_URL: 'https://chat.20060810.xyz:8443' });
  assert.strictEqual(out.mode, 'configured');
  assert.strictEqual(out.origin, 'https://chat.20060810.xyz:8443');
  assert.strictEqual(out.wsUrl, 'wss://chat.20060810.xyz:8443/ws/chat/');
  assert.strictEqual(out.serverUrl, null, 'release build must not pin the dev machine');

  const capCfg = buildCapacitorConfig(out);
  assert.strictEqual(capCfg.server, undefined, 'capacitor config has no server block on release');
  assert.strictEqual(capCfg.appId, 'com.ichat.pro');
  assert.strictEqual(capCfg.webDir, 'www');
  assert.strictEqual(capCfg.android.allowMixedContent, false);
});

test('live reload is opt-in and only then emits server.url', () => {
  const out = resolveMobileConfig(
    { ICHAT_MOBILE_SERVER_URL: 'https://chat.20060810.xyz:8443' },
    { liveReload: true },
  );
  assert.strictEqual(out.serverUrl, 'https://chat.20060810.xyz:8443');
  const capCfg = buildCapacitorConfig(out);
  assert.strictEqual(capCfg.server.url, 'https://chat.20060810.xyz:8443');
  assert.strictEqual(capCfg.server.cleartext, false, 'https live reload is not cleartext');
});

test('live reload over loopback http sets cleartext so the WebView can load it', () => {
  // Hardcoding cleartext:false made http://localhost live reload silently fail:
  // the WebView refused the cleartext URL. It must mirror the scheme.
  const out = resolveMobileConfig(
    { ICHAT_MOBILE_SERVER_URL: 'http://localhost:8000' },
    { liveReload: true },
  );
  assert.strictEqual(out.mode, 'configured');
  const capCfg = buildCapacitorConfig(out);
  assert.strictEqual(capCfg.server.url, 'http://localhost:8000');
  assert.strictEqual(capCfg.server.cleartext, true, 'loopback http live reload needs cleartext');
});

test('a forged resolved object cannot enable cleartext for a remote host', () => {
  // buildCapacitorConfig is exported; it must not trust the insecure flag.
  const forged = {
    mode: 'configured',
    origin: 'https://chat.example.com',
    wsUrl: 'wss://chat.example.com/ws/chat/',
    serverUrl: 'https://chat.example.com',
    insecure: true,
    message: null,
  };
  const capCfg = buildCapacitorConfig(forged);
  assert.strictEqual(capCfg.server.cleartext, false, 'remote https must stay non-cleartext');

  const forgedRemoteHttp = {
    mode: 'configured',
    origin: 'http://chat.example.com',
    wsUrl: 'ws://chat.example.com/ws/chat/',
    serverUrl: 'http://chat.example.com',
    insecure: true,
    message: null,
  };
  const capCfg2 = buildCapacitorConfig(forgedRemoteHttp);
  assert.strictEqual(capCfg2.server.cleartext, false, 'remote http never gets cleartext');
});

test('a garbage serverUrl is dropped rather than written verbatim', () => {
  const capCfg = buildCapacitorConfig({
    mode: 'configured',
    serverUrl: 'javascript:alert(1)',
    insecure: true,
  });
  assert.strictEqual(capCfg.server, undefined, 'invalid serverUrl is not emitted');
});
