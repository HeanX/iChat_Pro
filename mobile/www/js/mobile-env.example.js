/**
 * Shape reference for the generated runtime injection file `mobile-env.js`.
 *
 * DO NOT EDIT mobile/www/js/mobile-env.js BY HAND — it is a build artifact and
 * is gitignored. It is produced by the generator, which resolves the origin
 * through www/js/mobile-config.js and stamps it into this global:
 *
 *   cd mobile
 *   $env:ICHAT_MOBILE_SERVER_URL = "https://chat.example.com:8443"
 *   npm run config:generate        # writes www/js/mobile-env.js + capacitor.config.json
 *   npx cap sync android           # copies www/ into android/app/src/main/assets/public
 *
 * This example file is not loaded by index.html; it only documents the shape so
 * a reviewer can tell an injected build from an unconfigured one at a glance.
 * An unconfigured build ships NO mobile-env.js at all, which is why index.html
 * loads it with a plain <script> tag whose 404 is harmless.
 */
window.ICHAT_MOBILE_ENV = {
  ICHAT_MOBILE_SERVER_URL: 'https://chat.example.com:8443',
  ICHAT_MOBILE_WS_URL: 'wss://chat.example.com:8443/ws/chat/'
};
