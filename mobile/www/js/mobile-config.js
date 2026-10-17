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
    // `cleartext` must mirror the scheme. Hardcoding false made live reload
    // silently fail over http://localhost, because the WebView refused to load
    // a cleartext URL. Allow it only when the origin really is loopback http.
    cfg.server = { url: resolved.serverUrl, cleartext: resolved.insecure === true };
  }
  return cfg;
}

const api = {
  normalizeOrigin: normalizeOrigin,
  isLoopback: isLoopback,
  buildWsUrl: buildWsUrl,
  resolveMobileConfig: resolveMobileConfig,
  buildCapacitorConfig: buildCapacitorConfig,
};

if (typeof module !== 'undefined' && module.exports) {
  module.exports = api;
} else {
  window.iChatMobileConfig = api;
}
