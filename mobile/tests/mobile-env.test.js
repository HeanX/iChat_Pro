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
function runGenerator(scratch, extraEnv, extraArgs) {
  const env = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (key !== 'ICHAT_MOBILE_SERVER_URL') env[key] = value;
  }
  Object.assign(env, extraEnv || {});
  return spawnSync(process.execPath, [SCRIPT_REL].concat(extraArgs || []), {
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

test('an unreadable capacitor.config.json is reported, not silently skipped', () => {
  // Review F-10: the catch branch used to set previousConfig = null for every
  // error, so "exists but cannot be read" was indistinguishable from "absent"
  // and the whole reset block was skipped with ZERO output. If that file carried
  // a live-reload `server` block, the pack could ship a hot-reload APK and nobody
  // would be told.
  //
  // A real EACCES is awkward to arrange portably, so replace the file with a
  // DIRECTORY of the same name: readFileSync then fails with EISDIR, which is the
  // same "exists but unreadable" shape the code has to handle.
  const scratch = makeScratch();
  try {
    const configPath = path.join(scratch, 'capacitor.config.json');
    fs.rmSync(configPath, { force: true });
    fs.mkdirSync(configPath);

    const run = runGenerator(scratch, {});
    assert.strictEqual(run.status, 1, 'an invalid run still exits 1');
    assert.ok(run.stderr.indexOf('无法读取') !== -1,
      'stderr says the config could not be read: ' + run.stderr);
    assert.ok(run.stderr.indexOf('无法判断它是否含 server 块') !== -1,
      'stderr names the actual risk: ' + run.stderr);
    assert.ok(run.stderr.indexOf('未做任何处置') !== -1,
      'stderr states that nothing was done about the config: ' + run.stderr);
    // The read failure must not be misreported as "already release-shaped".
    assert.strictEqual(run.stderr.indexOf('已是发布形态'), -1,
      'an unreadable config must not be described as already correct: ' + run.stderr);
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

test('an unconfigured run deletes a stale mobile-env.js from an earlier build', () => {
  // F-02 (review AND-01 round 2): the dangerous sequence is "configure once, then
  // run again without the variable". Leaving the old file behind means the next
  // `npx cap sync` packages the previous origin and the start screen says "就绪"
  // pointing at the wrong server.
  const scratch = makeScratch();
  try {
    const first = runGenerator(scratch, {
      ICHAT_MOBILE_SERVER_URL: 'https://old.example.com:8443',
    });
    assert.strictEqual(first.status, 0, first.stderr);
    const stale = path.join(scratch, ENV_REL);
    assert.ok(fs.existsSync(stale), 'first run writes the artifact');
    assert.ok(fs.readFileSync(stale, 'utf8').indexOf('old.example.com') !== -1);

    const second = runGenerator(scratch, {});
    assert.strictEqual(second.status, 1, 'unconfigured run still fails');
    assert.strictEqual(fs.existsSync(stale), false,
      'stale artifact must be deleted, not left to be packaged');
    assert.ok(second.stderr.indexOf('已删除') !== -1,
      'stderr says the stale artifact was removed: ' + second.stderr);
    // And the downgrade is real: replaying the tree now renders unconfigured.
    assert.strictEqual(fs.existsSync(stale), false);
  } finally {
    removeScratch(scratch);
  }
});

test('a failed write leaves no .tmp litter behind', () => {
  // F-02(b/c): an interrupted or failing write must not leave *.tmp files that a
  // later `git add -A` would pick up (capacitor.config.json.tmp is a tracked-name
  // neighbourhood, mobile-env.js.tmp is gitignored).
  const scratch = makeScratch();
  try {
    // Make the final rename impossible by putting a directory where the target
    // file must go: fs.renameSync then fails, and the temp file must be removed.
    const envPath = path.join(scratch, ENV_REL);
    fs.mkdirSync(envPath, { recursive: true });
    const run = runGenerator(scratch, {
      ICHAT_MOBILE_SERVER_URL: 'https://chat.example.com:8443',
    });
    assert.strictEqual(run.status, 1, 'a failed write exits non-zero: ' + run.stdout);
    assert.strictEqual(fs.existsSync(envPath + '.tmp'), false, 'env .tmp must be cleaned up');
    const leftovers = fs.readdirSync(path.join(scratch, 'www', 'js'))
      .filter((f) => f.endsWith('.tmp'));
    assert.deepStrictEqual(leftovers, [], 'no *.tmp under www/js: ' + leftovers);
  } finally {
    removeScratch(scratch);
  }
});

test('a config write failure rolls back the freshly written mobile-env.js', () => {
  // Review F-04: the rollback on "config write failed" had zero coverage — a
  // mutation that deleted it left the suite green. Without the rollback the tree
  // is left with a NEW mobile-env.js and an OLD capacitor.config.json, i.e. the
  // exact "new env + old config" pair the write ordering exists to avoid.
  const scratch = makeScratch();
  try {
    // Put a directory where capacitor.config.json must go so its rename fails,
    // while mobile-env.js (written first) succeeds.
    const cfgPath = path.join(scratch, 'capacitor.config.json');
    fs.rmSync(cfgPath, { force: true });
    fs.mkdirSync(cfgPath, { recursive: true });

    const run = runGenerator(scratch, {
      ICHAT_MOBILE_SERVER_URL: 'https://chat.example.com:8443',
    });
    assert.strictEqual(run.status, 1, 'a failed config write exits non-zero');
    assert.ok(run.stderr.indexOf('已回滚') !== -1,
      'stderr reports the rollback: ' + run.stderr);
    assert.strictEqual(fs.existsSync(path.join(scratch, ENV_REL)), false,
      'mobile-env.js must be deleted again, not left paired with a stale config');
    assert.strictEqual(fs.existsSync(path.join(scratch, ENV_REL) + '.tmp'), false,
      'no env .tmp left behind');
  } finally {
    removeScratch(scratch);
  }
});

test('an invalid run resets a --live-reload capacitor.config.json to the release shape', () => {
  // Review F-03: a successful `--live-reload` run leaves a `server` block in
  // capacitor.config.json. A later run with an invalid configuration used to
  // leave it there, so the next `cap copy` would stamp a hot-reload config into
  // the APK. The invalid path must restore the release shape.
  const scratch = makeScratch();
  try {
    const cfgPath = path.join(scratch, 'capacitor.config.json');

    // 1) live reload succeeds and writes a server block.
    const live = runGenerator(scratch, {
      ICHAT_MOBILE_SERVER_URL: 'http://localhost:8000',
    }, ['--live-reload']);
    assert.strictEqual(live.status, 0, 'live reload generation succeeds: ' + live.stderr);
    const liveCfg = JSON.parse(fs.readFileSync(cfgPath, 'utf8'));
    assert.strictEqual(liveCfg.server.url, 'http://localhost:8000', 'precondition: server block present');
    assert.strictEqual(fs.existsSync(path.join(scratch, ENV_REL)), true);

    // 2) the same tree, now unconfigured: everything must be cleaned up.
    const invalid = runGenerator(scratch, {});
    assert.strictEqual(invalid.status, 1);
    assert.strictEqual(fs.existsSync(path.join(scratch, ENV_REL)), false,
      'stale mobile-env.js is removed');
    const afterCfg = JSON.parse(fs.readFileSync(cfgPath, 'utf8'));
    assert.strictEqual(afterCfg.server, undefined,
      'server block must be gone, got: ' + JSON.stringify(afterCfg.server));
    assert.deepStrictEqual(Object.keys(afterCfg).sort(),
      ['android', 'appId', 'appName', 'webDir'], 'config is back to the release shape');
    assert.strictEqual(afterCfg.android.allowMixedContent, false);

    // 3) a release-shaped config is left byte-identical (no needless rewrite).
    const beforeHash = fs.readFileSync(cfgPath, 'utf8');
    const again = runGenerator(scratch, {});
    assert.strictEqual(again.status, 1);
    assert.strictEqual(fs.readFileSync(cfgPath, 'utf8'), beforeHash,
      'an already-release config is not rewritten');
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

/**
 * Collapse CRLF to LF.
 *
 * A Windows checkout with `core.autocrlf=true` (Git's common Windows default)
 * has CRLF in the working tree while the committed blob is LF. Every string this
 * suite builds or compares must therefore be normalised: the first version of
 * the guard self-check hardcoded `'\n'` in `html.replace(...)`, so on a CRLF
 * checkout the replace matched nothing, the "removed" mutation silently stayed
 * intact, and the test went red with `a removed tag is detected: 3 !== 2` — for
 * a reason that had nothing to do with the code under test.
 *
 * Only CRLF is rewritten: a lone CR is not produced by git and rewriting it
 * would be guessing.
 */
function normaliseEol(text) {
  return String(text).replace(/\r\n/g, '\n');
}

/** Read a text file and normalise its line endings to LF. */
function readText(file) {
  return normaliseEol(fs.readFileSync(file, 'utf8'));
}

/**
 * Parse the classic `<script src>` loads of an HTML document in source order.
 *
 * The previous version of the ordering test used `html.indexOf('js/mobile-env.js')`,
 * which matched the PROSE in the unconfigured panel (`<code>www/js/mobile-env.js</code>`)
 * at offset 936 instead of the tag at offset 3182. Every ordering assertion was
 * therefore vacuously true: moving the tag after shell.js still passed. A real
 * parser is the only way to assert load order.
 *
 * Regex, not a full HTML parser: this document is authored by us and has no
 * commented-out or template-generated script tags, so tag extraction is exact.
 * Both `src="..."` and `src='...'` and a bare `src=path` are accepted so a
 * formatting change cannot make the test silently match nothing.
 *
 * That assumption is now *enforced* rather than trusted: `stripInert` below
 * removes HTML comments and `<template>` blocks, and the ordering test asserts
 * that parsing the stripped document yields exactly the same list. Without that
 * cross-check, wrapping the env tag in `<!-- -->` or `<template>` keeps this
 * parser happy while the browser never executes the script — which revives the
 * original AND-01 defect (page permanently "未配置") with a green suite
 * (2026-10-27 round-3 review F-09).
 *
 * Input is normalised to LF internally, so callers may pass CRLF text; the
 * result (src, attrs, order) is identical either way. `index` is deliberately
 * NOT returned: it is an offset into whatever string was passed, so it would
 * differ between LF and CRLF input and any assertion on it would reintroduce
 * the line-ending dependency this normalisation exists to remove.
 *
 * @param {string} html  document source, LF or CRLF
 * @returns {Array<{src:string, attrs:string}>}
 */
function parseScriptSrcs(html) {
  const normalised = normaliseEol(html);
  const loads = [];
  const tagRe = /<script\b([^>]*)>/gi;
  let tag;
  while ((tag = tagRe.exec(normalised)) !== null) {
    const attrs = tag[1];
    const srcMatch = /\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/i.exec(attrs);
    if (!srcMatch) continue;
    const src = srcMatch[1] !== undefined ? srcMatch[1]
      : srcMatch[2] !== undefined ? srcMatch[2] : srcMatch[3];
    loads.push({ src: src, attrs: attrs });
  }
  return loads;
}

/**
 * Remove markup a browser parses but never executes.
 *
 * Two forms matter here and both revive the original defect while leaving a
 * naive `<script` scan green:
 *   - `<!-- <script src="js/mobile-env.js"></script> -->` — a comment;
 *   - `<template><script src="js/mobile-env.js"></script></template>` — parsed
 *     into a document fragment, so the script never runs.
 *
 * @param {string} html  document source, LF or CRLF
 * @returns {string} source with comments and template blocks removed
 */
function stripInert(html) {
  let out = normaliseEol(html);
  // Comments first: a commented-out <template> is still inert, and removing it
  // before the template pass keeps the two passes independent.
  out = out.replace(/<!--[\s\S]*?-->/g, '');
  // `<template>` cannot nest in HTML, so a non-greedy match is correct here.
  out = out.replace(/<template\b[^>]*>[\s\S]*?<\/template\s*>/gi, '');
  return out;
}

/** Expected load order of the shell's classic scripts. */
const EXPECTED_ORDER = ['js/mobile-env.js', 'js/mobile-config.js', 'js/shell.js'];

test('index.html loads mobile-env.js before mobile-config.js and shell.js', () => {
  // Ordering is load-time correctness, not style: mobile-env.js must define the
  // global before shell.js reads it, and mobile-config.js must be parsed first.
  const html = readText(path.join(mobileRoot, 'www', 'index.html'));
  const loads = parseScriptSrcs(html);
  const order = loads.map((l) => l.src);

  assert.deepStrictEqual(order, EXPECTED_ORDER,
    'unexpected <script src> order: ' + JSON.stringify(order));

  // Review F-09: every assertion below is about *executed* scripts. A tag that
  // is commented out, or sitting in a <template>, is found by the scanner but
  // never runs -- which is the original AND-01 defect wearing a green suite.
  // Requiring the inert-stripped document to parse identically is what makes
  // the rest of this test mean what it says.
  assert.deepStrictEqual(
    parseScriptSrcs(stripInert(html)).map((l) => l.src), order,
    'a <script> tag is inside an HTML comment or a <template>, so it is parsed '
    + 'but never executed');

  // Order is the whole point: re-state it as explicit pairwise comparisons so a
  // failure says which relationship broke.
  const envAt = order.indexOf('js/mobile-env.js');
  const cfgAt = order.indexOf('js/mobile-config.js');
  const shellAt = order.indexOf('js/shell.js');
  assert.ok(envAt !== -1, 'index.html loads js/mobile-env.js');
  assert.ok(envAt < cfgAt, 'env before config (' + envAt + ' < ' + cfgAt + ')');
  assert.ok(cfgAt < shellAt, 'config before shell (' + cfgAt + ' < ' + shellAt + ')');

  // The env script must be a plain classic script: with defer/async/module the
  // 404-tolerance and the ordering guarantee both change.
  for (const load of loads) {
    assert.ok(!/\b(?:defer|async)\b/i.test(load.attrs),
      load.src + ' must not be deferred/async: ' + load.attrs);
    assert.ok(!/\btype\s*=\s*["']?module/i.test(load.attrs),
      load.src + ' must not be a module: ' + load.attrs);
  }

  // Review F-05: comparing relative order alone still passes if all three tags
  // are moved into <head> or after </body>. Pin their location too: the tags must
  // sit inside <body> and before </body>, which is where the markup keeps them.
  const bodyOpen = html.indexOf('<body');
  const bodyClose = html.lastIndexOf('</body>');
  assert.ok(bodyOpen !== -1 && bodyClose !== -1, 'the page has a body');
  const tagPositions = EXPECTED_ORDER.map((src) => {
    const re = new RegExp('<script\\b[^>]*src\\s*=\\s*["\']' + src.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '["\']');
    return { src: src, at: html.search(re) };
  });
  for (const pos of tagPositions) {
    assert.ok(pos.at !== -1, pos.src + ' tag found');
    assert.ok(pos.at > bodyOpen, pos.src + ' must be inside <body> (after <body> at ' + bodyOpen + ')');
    assert.ok(pos.at < bodyClose, pos.src + ' must be before </body> at ' + bodyClose);
  }
});

test('parseScriptSrcs detects a moved or removed env script (guard self-check)', () => {
  // Mutation proof, in-tree: the ordering assertion must FAIL for the exact
  // regression it claims to guard. If somebody re-breaks the assertion into an
  // indexOf, this test keeps failing until it is a real parser again.
  //
  // Every mutation below is built with line-ending-tolerant patterns and applied
  // to the normalised source, so this test behaves identically in an LF and a
  // CRLF working tree (see normaliseEol above).
  const html = readText(path.join(mobileRoot, 'www', 'index.html'));

  const envTag = /[ \t]*<script\s+src="js\/mobile-env\.js"><\/script>[ \t]*\r?\n?/;
  const shellTag = /([ \t]*<script\s+src="js\/shell\.js"><\/script>[ \t]*\r?\n?)/;
  assert.ok(envTag.test(html), 'the env tag the mutations rely on is present');
  assert.ok(shellTag.test(html), 'the shell tag the mutations rely on is present');

  // 1) env moves after shell.js
  const movedAfterShell = html
    .replace(envTag, '')
    .replace(shellTag, '$1  <script src="js/mobile-env.js"></script>\n');
  assert.deepStrictEqual(parseScriptSrcs(movedAfterShell).map((l) => l.src),
    ['js/mobile-config.js', 'js/shell.js', 'js/mobile-env.js'],
    'moving mobile-env.js after shell.js must be detected');

  // 2) env deleted
  const removed = html.replace(envTag, '');
  assert.deepStrictEqual(parseScriptSrcs(removed).map((l) => l.src),
    ['js/mobile-config.js', 'js/shell.js'], 'a removed tag is detected');

  // 3) env deferred: order looks right, but defer breaks the guarantee
  const deferred = html.replace('<script src="js/mobile-env.js">',
    '<script defer src="js/mobile-env.js">');
  assert.ok(/\bdefer\b/i.test(parseScriptSrcs(deferred)[0].attrs), 'defer is detected');

  // 4) env duplicated (the copy lands just before shell.js)
  const duplicated = html.replace(shellTag,
    '  <script src="js/mobile-env.js"></script>\n$1');
  assert.deepStrictEqual(parseScriptSrcs(duplicated).map((l) => l.src),
    ['js/mobile-env.js', 'js/mobile-config.js', 'js/mobile-env.js', 'js/shell.js'],
    'a duplicated tag is detected');
  assert.notDeepStrictEqual(parseScriptSrcs(duplicated).map((l) => l.src), EXPECTED_ORDER,
    'a duplicated tag must not look like the expected order');

  // 5) env commented out (review F-09). The tag is still there and the scanner
  // still finds it, so only the stripInert cross-check can catch this.
  const commented = html.replace(envTag,
    '  <!-- <script src="js/mobile-env.js"></script> -->\n');
  assert.deepStrictEqual(parseScriptSrcs(commented).map((l) => l.src), EXPECTED_ORDER,
    'the naive scanner still sees a commented-out tag (this is the hazard)');
  assert.notDeepStrictEqual(parseScriptSrcs(stripInert(commented)).map((l) => l.src),
    EXPECTED_ORDER, 'a commented-out env tag must be detected');

  // 6) env inside <template> (review F-09). Parsed into a fragment, never run.
  const templated = html.replace(envTag,
    '  <template><script src="js/mobile-env.js"></script></template>\n');
  assert.deepStrictEqual(parseScriptSrcs(templated).map((l) => l.src), EXPECTED_ORDER,
    'the naive scanner still sees a templated tag (this is the hazard)');
  assert.notDeepStrictEqual(parseScriptSrcs(stripInert(templated)).map((l) => l.src),
    EXPECTED_ORDER, 'a templated env tag must be detected');

  // 7) a commented-out <template> is inert too, and the two passes must compose.
  const nested = html.replace(envTag,
    '  <!-- <template><script src="js/mobile-env.js"></script></template> -->\n');
  assert.notDeepStrictEqual(parseScriptSrcs(stripInert(nested)).map((l) => l.src),
    EXPECTED_ORDER, 'a commented-out template must be detected');

  // 8) the real page must survive the strip: no false positive.
  assert.deepStrictEqual(parseScriptSrcs(stripInert(html)).map((l) => l.src),
    EXPECTED_ORDER, 'stripInert must not remove the real script tags');
});

test('parseScriptSrcs gives identical results for LF and CRLF input', () => {
  // Regression guard for the line-ending fragility itself: converting the page
  // to CRLF (what a core.autocrlf=true checkout produces) must not change the
  // parsed loads at all — same order, same src, same attrs.
  const lf = readText(path.join(mobileRoot, 'www', 'index.html'));
  const crlf = lf.replace(/\n/g, '\r\n');
  assert.notStrictEqual(crlf, lf, 'the CRLF variant is really different text');
  assert.ok(crlf.indexOf('\r\n') !== -1, 'CRLF variant contains CRLF');

  const fromLf = parseScriptSrcs(lf);
  const fromCrlf = parseScriptSrcs(crlf);

  assert.deepStrictEqual(fromCrlf, fromLf,
    'CRLF input must parse to the same loads as LF input');
  assert.deepStrictEqual(fromCrlf.map((l) => l.src), EXPECTED_ORDER);
  // Attrs are compared too: a trailing \r leaking into an attribute would let a
  // `defer` check silently miss.
  assert.ok(fromCrlf.every((l) => l.attrs.indexOf('\r') === -1),
    'no carriage return leaks into parsed attributes');

  // A single-line tag cannot carry a CRLF inside it, so the assertions above do
  // not actually prove the parser normalises. A tag split across lines can: in a
  // CRLF document the attribute chunk then contains "\r\n", which would leak into
  // `attrs` (and could hide a `defer` behind a stray \r) unless the parser
  // normalises first. This is what makes that normalisation load-bearing.
  const multilineLf = [
    '<script',
    '  src="js/mobile-env.js">',
    '</script>',
    '<script defer',
    '  src="js/shell.js"></script>',
  ].join('\n');
  const multilineCrlf = multilineLf.replace(/\n/g, '\r\n');
  const mlLf = parseScriptSrcs(multilineLf);
  const mlCrlf = parseScriptSrcs(multilineCrlf);
  assert.deepStrictEqual(mlCrlf, mlLf,
    'a tag split across lines must parse identically under CRLF');
  assert.deepStrictEqual(mlCrlf.map((l) => l.src), ['js/mobile-env.js', 'js/shell.js']);
  assert.ok(mlCrlf.every((l) => l.attrs.indexOf('\r') === -1),
    'CRLF inside a multi-line tag must not reach attrs: ' + JSON.stringify(mlCrlf));
  assert.ok(mlCrlf[1].attrs.indexOf('defer') !== -1, 'multi-line attrs are still parsed');
});

test('the README-style command checks survive CRLF', () => {
  // Same class of bug, different assertion: the page checks in
  // 'the start screen documents the commands that actually exist' and the
  // .gitignore checks in the regression script are plain substring searches.
  // Substring search is line-ending agnostic as long as the needle does not span
  // a newline, so this pins that property directly: a CRLF copy of the page and
  // of .gitignore must satisfy exactly the same checks as the LF copy.
  //
  // NOTE: this deliberately reads the raw bytes instead of using readText().
  // normalising first would make the CRLF variant below still contain LF only,
  // so the test would compare two identical LF strings and prove nothing.
  const lf = fs.readFileSync(path.join(mobileRoot, 'www', 'index.html'), 'utf8')
    .replace(/\r\n/g, '\n');
  const crlf = lf.replace(/\n/g, '\r\n');
  assert.notStrictEqual(crlf, lf, 'the CRLF variant is really different text');
  for (const needle of [
    'npm run config:generate',
    'npx cap sync android',
    'assembleDebug',
    'ICHAT_MOBILE_SERVER_URL',
    '不读 <code>mobile/.env</code>',
    '尚未实现',
  ]) {
    assert.strictEqual(crlf.indexOf(needle) !== -1, lf.indexOf(needle) !== -1,
      'check "' + needle + '" must not depend on line endings');
    assert.ok(lf.indexOf(needle) !== -1, 'page still contains "' + needle + '"');
  }
  assert.strictEqual(crlf.indexOf('或构建环境中设置'), -1,
    'the negative check must hold for CRLF too');

  // .gitignore: every pattern is a whole line; matching is done against a CRLF
  // split, which is what a CRLF checkout produces.
  const ignoreLf = fs.readFileSync(path.join(mobileRoot, '.gitignore'), 'utf8')
    .replace(/\r\n/g, '\n');
  const ignoreCrlf = ignoreLf.replace(/\n/g, '\r\n');
  for (const pattern of ['www/js/mobile-env.js', 'www/js/mobile-env.js.tmp', 'capacitor.config.json.tmp']) {
    const line = ignoreCrlf.split('\r\n').find((l) => l === pattern);
    assert.strictEqual(line, pattern, '.gitignore pattern "' + pattern + '" is a whole line');
  }
});

test('the start screen documents the commands that actually exist', () => {
  // AND-01 was a documentation/implementation mismatch; this pins the two
  // together so the wording cannot drift again.
  const pkg = JSON.parse(fs.readFileSync(path.join(mobileRoot, 'package.json'), 'utf8'));
  assert.ok(pkg.scripts['config:generate'], 'package.json has config:generate');
  const html = readText(path.join(mobileRoot, 'www', 'index.html'));

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
