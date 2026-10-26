/**
 * P4 T22 remediation, review AND-01 (MINOR).
 *
 * The start screen used to tell the user to set ICHAT_MOBILE_SERVER_URL and
 * "regenerate + rebuild", but nothing in the build ever produced the
 * `window.ICHAT_MOBILE_ENV` that www/js/shell.js reads, so the documented steps
 * could not leave the "未配置" state. These tests cover the missing link:
 *
 *   process env -> write-capacitor-config.js -> www/js/mobile-env.js
 *     -> window.ICHAT_MOBILE_ENV -> resolveMobileConfig() -> configured
 *
 * No DOM and no jsdom: shell.js needs a document, so we test the contract it
 * consumes instead of the renderer itself. The generator is executed for real
 * in a throwaway copy of the tree, so nothing in mobile/ is mutated.
 */
const assert = require('node:assert');
const test = require('node:test');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { spawnSync } = require('node:child_process');

const mobileRoot = path.join(__dirname, '..');
const configModule = require(path.join(mobileRoot, 'www', 'js', 'mobile-config.js'));
const { resolveMobileConfig, buildMobileEnvScript } = configModule;

const SCRIPT_REL = path.join('scripts', 'write-capacitor-config.js');
const ENV_REL = path.join('www', 'js', 'mobile-env.js');

/**
 * Copy the sources the generator needs into a scratch directory shaped like
 * mobile/, so `npm run config:generate` can be executed without touching the
 * working tree (mobile-env.js would otherwise be left behind).
 */
function makeScratch() {
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'ichat-mobile-env-'));
  fs.mkdirSync(path.join(scratch, 'scripts'));
  fs.mkdirSync(path.join(scratch, 'www', 'js'), { recursive: true });
  fs.copyFileSync(
    path.join(mobileRoot, SCRIPT_REL), path.join(scratch, SCRIPT_REL));
  fs.copyFileSync(
    path.join(mobileRoot, 'www', 'js', 'mobile-config.js'),
    path.join(scratch, 'www', 'js', 'mobile-config.js'));
  fs.copyFileSync(
    path.join(mobileRoot, 'capacitor.config.json'),
    path.join(scratch, 'capacitor.config.json'));
  return scratch;
}

function removeScratch(scratch) {
  const resolved = path.resolve(scratch);
  assert.strictEqual(path.dirname(resolved), path.resolve(os.tmpdir()));
  assert.ok(path.basename(resolved).startsWith('ichat-mobile-env-'));
  fs.rmSync(resolved, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
}

/** Run the generator with a scrubbed environment (never inherits the host's). */
function runGenerator(scratch, extraEnv) {
  const env = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (key !== 'ICHAT_MOBILE_SERVER_URL') env[key] = value;
  }
  Object.assign(env, extraEnv || {});
  return spawnSync(process.execPath, [SCRIPT_REL], {
    cwd: scratch, env, encoding: 'utf8', windowsHide: true,
  });
}

/** Execute the generated mobile-env.js the way a <script> tag would. */
function loadInjectedGlobal(scratch) {
  const file = path.join(scratch, ENV_REL);
  const sandbox = { window: {} };
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), sandbox, { filename: file });
  return sandbox.window.ICHAT_MOBILE_ENV;
}

test('the documented steps inject the origin and derive the WebSocket URL', () => {
  const scratch = makeScratch();
  try {
    const run = runGenerator(scratch, {
      ICHAT_MOBILE_SERVER_URL: 'https://chat.example.com:8443',
    });
    assert.strictEqual(run.status, 0, 'generator exits 0: ' + run.stderr);

    assert.ok(fs.existsSync(path.join(scratch, ENV_REL)), 'mobile-env.js is written');
    const injected = loadInjectedGlobal(scratch);
    assert.strictEqual(typeof injected, 'object', 'window.ICHAT_MOBILE_ENV is assigned');
    assert.strictEqual(injected.ICHAT_MOBILE_SERVER_URL, 'https://chat.example.com:8443');

    // The real chain shell.js performs: window global -> resolver.
    const resolved = resolveMobileConfig(injected);
    assert.strictEqual(resolved.mode, 'configured', 'injected env resolves as configured');
    assert.strictEqual(resolved.origin, 'https://chat.example.com:8443');
    assert.strictEqual(resolved.wsUrl, 'wss://chat.example.com:8443/ws/chat/');
    assert.strictEqual(resolved.serverUrl, null, 'release build still pins no dev URL');

    assert.strictEqual(injected.ICHAT_MOBILE_WS_URL, resolved.wsUrl,
      'recorded ws URL matches the resolver output');
  } finally {
    removeScratch(scratch);
  }
});

test('the release capacitor.config.json still has no server block after generation', () => {
  const scratch = makeScratch();
  try {
    const run = runGenerator(scratch, {
      ICHAT_MOBILE_SERVER_URL: 'https://chat.example.com:8443',
    });
    assert.strictEqual(run.status, 0, run.stderr);
    const capCfg = JSON.parse(fs.readFileSync(path.join(scratch, 'capacitor.config.json'), 'utf8'));
    assert.strictEqual(capCfg.server, undefined, 'no server block on release');
    assert.strictEqual(capCfg.appId, 'com.ichat.pro');
    assert.strictEqual(capCfg.webDir, 'www');
    assert.strictEqual(capCfg.android.allowMixedContent, false);
  } finally {
    removeScratch(scratch);
  }
});

test('an unconfigured build writes nothing and leaves the committed config intact', () => {
  const scratch = makeScratch();
  try {
    const before = fs.readFileSync(path.join(scratch, 'capacitor.config.json'), 'utf8');
    const run = runGenerator(scratch, {});
    assert.strictEqual(run.status, 1, 'generator exits 1 without an origin');
    assert.ok(run.stderr.indexOf('ICHAT_MOBILE_SERVER_URL') !== -1,
      'stderr names the variable: ' + run.stderr);
    assert.strictEqual(fs.existsSync(path.join(scratch, ENV_REL)), false,
      'no mobile-env.js is written when unconfigured');
    assert.strictEqual(
      fs.readFileSync(path.join(scratch, 'capacitor.config.json'), 'utf8'), before,
      'capacitor.config.json is not rewritten');
    // No window.ICHAT_MOBILE_ENV at all is exactly what an unconfigured build
    // looks like; shell.js falls back to {} and renders "未配置".
    assert.strictEqual(resolveMobileConfig({}).mode, 'unconfigured');
  } finally {
    removeScratch(scratch);
  }
});

test('a mobile/.env file alone is not read (the wording must not promise it)', () => {
  // The old start screen told users to put the variable in mobile/.env. The
  // generator only reads process.env, so this must keep failing; the on-screen
  // steps now say so explicitly.
  const scratch = makeScratch();
  try {
    fs.writeFileSync(path.join(scratch, '.env'), 'ICHAT_MOBILE_SERVER_URL=https://chat.example.com:8443\n');
    const run = runGenerator(scratch, {});
    assert.strictEqual(run.status, 1, '.env is not a supported input');
    assert.strictEqual(fs.existsSync(path.join(scratch, ENV_REL)), false);
  } finally {
    removeScratch(scratch);
  }
});

test('a remote plain-HTTP origin is rejected before any file is written', () => {
  const scratch = makeScratch();
  try {
    const run = runGenerator(scratch, {
      ICHAT_MOBILE_SERVER_URL: 'http://chat.example.com:8000',
    });
    assert.strictEqual(run.status, 1);
    assert.ok(run.stderr.indexOf('HTTPS') !== -1, 'stderr explains HTTPS: ' + run.stderr);
    assert.strictEqual(fs.existsSync(path.join(scratch, ENV_REL)), false);
  } finally {
    removeScratch(scratch);
  }
});

test('an unconfigured resolution renders the null script, never an empty global', () => {
  // Defence in depth for the "write an empty object" alternative: an unconfigured
  // build must ship no file, because `window.ICHAT_MOBILE_ENV = {}` in a stale
  // artifact is indistinguishable from a build that was never configured.
  assert.strictEqual(buildMobileEnvScript(resolveMobileConfig({})), null);
  assert.strictEqual(
    buildMobileEnvScript(resolveMobileConfig({ ICHAT_MOBILE_SERVER_URL: 'http://remote.example.com' })),
    null);
  assert.strictEqual(buildMobileEnvScript(null), null);
  assert.strictEqual(buildMobileEnvScript({ mode: 'configured', origin: 'https://x.example' }), null,
    'missing wsUrl is refused');
});

test('the generated script is valid JavaScript and survives an odd-but-legal origin', () => {
  const resolved = resolveMobileConfig({ ICHAT_MOBILE_SERVER_URL: 'https://CHAT.Example.COM:443/' });
  const source = buildMobileEnvScript(resolved);
  assert.ok(source, 'script is rendered');
  assert.ok(source.indexOf('window.ICHAT_MOBILE_ENV = ') !== -1);
  const sandbox = { window: {} };
  vm.runInNewContext(source, sandbox);
  assert.strictEqual(sandbox.window.ICHAT_MOBILE_ENV.ICHAT_MOBILE_SERVER_URL,
    'https://chat.example.com', 'origin is normalised, default port dropped');
});

test('index.html loads mobile-env.js before mobile-config.js and shell.js', () => {
  // Ordering is load-time correctness, not style: mobile-env.js must define the
  // global before shell.js reads it, and mobile-config.js must be parsed first.
  const html = fs.readFileSync(path.join(mobileRoot, 'www', 'index.html'), 'utf8');
  const envAt = html.indexOf('js/mobile-env.js');
  const cfgAt = html.indexOf('js/mobile-config.js');
  const shellAt = html.indexOf('js/shell.js');
  assert.ok(envAt !== -1, 'index.html references js/mobile-env.js: ' + envAt);
  assert.ok(envAt < cfgAt, 'env before config (' + envAt + ' < ' + cfgAt + ')');
  assert.ok(cfgAt < shellAt, 'config before shell (' + cfgAt + ' < ' + shellAt + ')');
  // Exactly one <script> load; the path may also appear in comments/prose.
  const scriptTags = html.split('<script src="js/mobile-env.js">').length - 1;
  assert.strictEqual(scriptTags, 1, 'mobile-env.js is loaded exactly once, got ' + scriptTags);
});

test('the start screen documents the commands that actually exist', () => {
  // AND-01 was a documentation/implementation mismatch; this pins the two
  // together so the wording cannot drift again.
  const pkg = JSON.parse(fs.readFileSync(path.join(mobileRoot, 'package.json'), 'utf8'));
  assert.ok(pkg.scripts['config:generate'], 'package.json has config:generate');
  const html = fs.readFileSync(path.join(mobileRoot, 'www', 'index.html'), 'utf8');

  assert.ok(html.indexOf('npm run config:generate') !== -1, 'page names the real npm script');
  assert.ok(html.indexOf('npx cap sync android') !== -1, 'page names the real cap sync command');
  assert.ok(html.indexOf('assembleDebug') !== -1, 'page names the real gradle task');
  assert.ok(html.indexOf('ICHAT_MOBILE_SERVER_URL') !== -1, 'page names the real variable');

  // The misleading instruction: the old page told users to set the variable in
  // mobile/.env, which the generator never reads. Naming .env to say it is NOT
  // read is fine; telling the user to put the value there is not.
  assert.ok(html.indexOf('或构建环境中设置') === -1,
    'page must not repeat the old "mobile/.env or build environment" instruction');
  assert.ok(html.indexOf('不读 <code>mobile/.env</code>') !== -1,
    'page warns that mobile/.env is not read');
  // And the page must not promise runtime configuration that does not exist.
  assert.ok(html.indexOf('尚未实现') !== -1, 'page states runtime injection is not implemented');
});
