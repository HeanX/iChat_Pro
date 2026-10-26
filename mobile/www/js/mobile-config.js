/**
 * P4 T22 (Issue #149): mobile shell configuration contract.
 *
 * This is the Android-side counterpart of desktop/app-config.js. It resolves
 * the server origin the WebView loads and the WSS endpoint the shell talks to,
 * so that the origin rules are testable in plain Node without an emulator.
 *
 * Contract (see docs/handoff/android-mobile.md and ADR-P4-03):
 *   - The service origin MUST be HTTPS for any non-loopback host. The WebView
 *     shares the cookie jar with the WebSocket, and a mixed http/wss pair
 *     breaks the Django Session + CSRF flow.
 *   - `server.url` is the Capacitor live-reload escape hatch and MUST NOT be
 *     used for a release build. `buildServerUrl` therefore refuses to emit it
 *     unless liveReload is explicitly requested; release resolution never
 *     sets it.
 *   - Origin normalisation must agree with desktop/app-config.js
 *     (lowercase host, default port dropped) or the two clients cannot be
 *     compared in the cross-end tests.
 *
 * No DOM, no window.crypto, no network: Node testable.
 */

/*
 * Everything lives inside this IIFE on purpose. The file used to declare six
 * functions at top level, so loading it in the WebView also published
 * normalizeOrigin / isLoopback / buildWsUrl / resolveMobileConfig /
 * buildCapacitorConfig / buildMobileEnvScript as globals. They are read-only
 * there and cannot escalate, but a third-party script that happened to define
 * `resolveMobileConfig` later would silently replace the HTTPS/loopback policy.
 * One named global (`window.iChatMobileConfig`) is the whole public surface.
 *
 * The body is deliberately NOT re-indented, so a reviewer can see that this
 * commit only adds the wrapper: `git diff -w` should show no logic changes.
 */
(function () {
'use strict';

const LOOPBACK_HOSTS = ['localhost', '127.0.0.1', '::1', '[::1]'];

/**
 * Normalise an http(s) origin: lowercase host, drop default port.
 * Returns null for anything that is not a valid http(s) URL.
 */
function normalizeOrigin(raw) {
  if (raw === null || raw === undefined) return null;
  let u;
  try {
    u = new URL(String(raw).trim());
  } catch (e) {
    return null;
  }
  if (u.protocol !== 'https:' && u.protocol !== 'http:') return null;
  if (!u.hostname) return null;
  let port = u.port;
  if ((u.protocol === 'https:' && port === '443') || (u.protocol === 'http:' && port === '80')) {
    port = '';
  }
  return u.protocol + '//' + u.hostname.toLowerCase() + (port ? ':' + port : '');
}

function isLoopback(origin) {
  try {
    return LOOPBACK_HOSTS.indexOf(new URL(origin).hostname.toLowerCase()) !== -1;
  } catch (e) {
    return false;
  }
}

/**
 * Resolve the app configuration for the mobile shell.
 *
 * @param {object} env        process environment (ICHAT_MOBILE_SERVER_URL, ...)
 * @param {object} [options]  { liveReload: boolean }
 * @returns {{mode:string, origin:(string|null), wsUrl:(string|null),
 *            serverUrl:(string|null), insecure:boolean, message:(string|null)}}
 */
function resolveMobileConfig(env, options) {
  const opts = options || {};
  const raw = String((env && env.ICHAT_MOBILE_SERVER_URL) || '').trim();
  const origin = normalizeOrigin(raw);

  if (!origin) {
    return {
      mode: 'unconfigured',
      origin: null,
      wsUrl: null,
      serverUrl: null,
      insecure: false,
      message: raw
        ? 'ICHAT_MOBILE_SERVER_URL 不是合法的 HTTP(S) 地址：' + raw
        : '未配置服务地址：请设置 ICHAT_MOBILE_SERVER_URL（例如 https://chat.example.com:8443）。',
    };
  }

  const loopback = isLoopback(origin);
  const insecure = origin.indexOf('http://') === 0;
  if (insecure && !loopback) {
    return {
      mode: 'unconfigured',
      origin: null,
      wsUrl: null,
      serverUrl: null,
      insecure: true,
      message: '远程服务地址必须使用 HTTPS（当前为 HTTP）：' + origin,
    };
  }

  const wsUrl = buildWsUrl(origin);
  if (!wsUrl) {
    return {
      mode: 'unconfigured',
      origin: null,
      wsUrl: null,
      serverUrl: null,
      insecure: insecure,
      message: '无法从服务地址推导 WebSocket 端点：' + origin,
    };
  }

  const liveReload = opts.liveReload === true;
  return {
    mode: 'configured',
    origin: origin,
    wsUrl: wsUrl,
    // Live reload is a development-only affordance; a release build resolves
    // serverUrl to null so that `npx cap sync` never bakes it into the APK.
    serverUrl: liveReload ? origin : null,
    insecure: insecure,
    message: null,
  };
}

/**
 * Map an HTTP(S) origin to the chat WebSocket endpoint.
 * https://host:8443 -> wss://host:8443/ws/chat/
 * http://localhost:8000 -> ws://localhost:8000/ws/chat/
 */
function buildWsUrl(origin) {
  const normalised = normalizeOrigin(origin);
  if (!normalised) return null;
  const wsScheme = normalised.indexOf('https://') === 0 ? 'wss://' : 'ws://';
  return wsScheme + normalised.slice(normalised.indexOf('://') + 3) + '/ws/chat/';
}

/**
 * Render the runtime injection file `www/js/mobile-env.js`.
 *
 * `npx cap sync` only copies files: the build environment never reaches the
 * WebView. `shell.js` reads `window.ICHAT_MOBILE_ENV`, so the generator stamps
 * that variable into a build artifact at config-generation time. Keeping the
 * renderer here means the injected values come from the same resolution the
 * tests already cover, instead of a second ad-hoc copy of the origin rules.
 *
 * Returns null when the build is not configured: an unconfigured build must
 * ship no env file at all, otherwise a stale deployment address would keep the
 * start screen looking configured.
 *
 * @param {object} resolved  output of resolveMobileConfig()
 * @returns {string|null}    JavaScript source for a classic <script> tag
 */
function buildMobileEnvScript(resolved) {
  if (!resolved || resolved.mode !== 'configured' || !resolved.origin || !resolved.wsUrl) {
    return null;
  }
  // JSON.stringify also escapes a hostile origin; normalizeOrigin already
  // restricts the charset, so this is defence in depth for the assignment.
  const payload = {
    ICHAT_MOBILE_SERVER_URL: resolved.origin,
    // Informational: mobile-config.js derives the WSS endpoint from the origin.
    // Recorded so a build can be inspected without re-running the resolver.
    ICHAT_MOBILE_WS_URL: resolved.wsUrl,
  };
  return [
    '/* Generated by `npm run config:generate` (mobile/scripts/write-capacitor-config.js).',
    ' * Do not edit, do not commit: mobile/www/js/mobile-env.js is a build artifact',
    ' * and is gitignored. Shape mirrors mobile/www/js/mobile-env.example.js. */',
    'window.ICHAT_MOBILE_ENV = ' + JSON.stringify(payload) + ';',
    '',
  ].join('\n');
}

/**
 * Capacitor config object for capacitor.config.json.
 * Kept as a pure function so the generated file has a reviewable contract.
 */
function buildCapacitorConfig(resolved) {
  const cfg = {
    appId: 'com.ichat.pro',
    appName: 'iChat Pro',
    webDir: 'www',
    android: {
      allowMixedContent: false,
      captureInput: true,
      webContentsDebuggingEnabled: false,
    },
  };
  if (resolved && resolved.mode === 'configured' && resolved.serverUrl) {
    // Defence in depth: this function is exported and could be handed a
    // hand-built object. Re-derive the scheme from the URL instead of trusting
    // the `insecure` flag, so a forged {insecure:true, serverUrl:'https://...'}
    // still cannot produce cleartext:true for a remote host.
    const serverOrigin = normalizeOrigin(resolved.serverUrl);
    const cleartext = serverOrigin !== null && isLoopback(serverOrigin) &&
      serverOrigin.indexOf('http://') === 0;
    if (serverOrigin !== null) {
      // `cleartext` must mirror the scheme. Hardcoding false made live reload
      // silently fail over http://localhost, because the WebView refused to
      // load a cleartext URL. Only loopback http may set it.
      cfg.server = { url: serverOrigin, cleartext: cleartext };
    }
  }
  return cfg;
}

const api = {
  normalizeOrigin: normalizeOrigin,
  isLoopback: isLoopback,
  buildWsUrl: buildWsUrl,
  resolveMobileConfig: resolveMobileConfig,
  buildCapacitorConfig: buildCapacitorConfig,
  buildMobileEnvScript: buildMobileEnvScript,
};

if (typeof module !== 'undefined' && module.exports) {
  // Node: the test suite and scripts/write-capacitor-config.js require() this.
  module.exports = api;
} else {
  // WebView: exactly one global, never the internal functions.
  window.iChatMobileConfig = api;
}

})();
