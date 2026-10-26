/**
 * P4 T22 (Issue #149) shell bootstrap.
 *
 * Deliberately tiny: the skeleton proves the packaging + config path works on a
 * device. Chat, WS and E2EE belong to later cards (T23–T30).
 *
 * The service origin is NOT baked into this file. `mobile-config.js` is the
 * single source of truth; here we only read the value the native build stamped
 * into `window.ICHAT_MOBILE_ENV`, falling back to a placeholder so the page is
 * still openable in a desktop browser for layout checks.
 */
(function () {
  'use strict';

  var PROTOCOL_VERSION = '1.0';

  function $(id) {
    return document.getElementById(id);
  }

  function setState(text, state) {
    var badge = $('shell-state');
    if (!badge) return;
    badge.textContent = text;
    badge.setAttribute('data-state', state);
  }

  function render() {
    var env = window.ICHAT_MOBILE_ENV || {};
    var api = window.iChatMobileConfig;
    if (!api) {
      setState('配置模块缺失', 'error');
      return;
    }

    var resolved = api.resolveMobileConfig(env);

    if (resolved.mode !== 'configured') {
      setState('未配置', 'unconfigured');
      $('shell-unconfigured').hidden = false;
      $('shell-message').textContent = resolved.message || '';
      if (resolved.insecure) {
        document.body.setAttribute('data-insecure', 'true');
      }
      return;
    }

    setState('就绪', 'ready');
    $('shell-ready').hidden = false;
    $('fact-origin').textContent = resolved.origin;
    $('fact-ws').textContent = resolved.wsUrl;
    $('fact-protocol').textContent = PROTOCOL_VERSION;
  }

  document.addEventListener('DOMContentLoaded', render);
})();
