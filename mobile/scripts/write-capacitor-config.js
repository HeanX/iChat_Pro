#!/usr/bin/env node
/**
 * P4 T22 (Issue #149): generate capacitor.config.json from the reviewed
 * config contract in www/js/mobile-config.js.
 *
 * The generated file is committed so that a build machine does not need to run
 * this step, but it is always reproducible:
 *
 *   node ./scripts/write-capacitor-config.js                     # release
 *   ICHAT_MOBILE_SERVER_URL=https://... node ... --live-reload   # dev only
 *
 * Three invariants, all review findings:
 *
 *   1. Release output MUST NOT contain a `server` block. A live-reload URL is
 *      only emitted when --live-reload is passed explicitly.
 *   2. An invalid configuration MUST NOT touch the committed file (T22 review,
 *      round 1: the first version wrote a fallback before exiting 1).
 *   3. An invalid configuration MUST NOT leave a usable `mobile-env.js` behind
 *      (T22 review, AND-01 round 2, F-02). The unconfigured branch deletes a
 *      stale artifact: otherwise `npx cap sync` packages the previous build's
 *      origin and the start screen says "就绪" while pointing at the wrong
 *      server.
 *
 * T22 remediation (review AND-01): the step ALSO writes the runtime injection
 * artifact `www/js/mobile-env.js`, which is what `www/js/shell.js` reads as
 * `window.ICHAT_MOBILE_ENV`. Before this, `npx cap sync` shipped a start screen
 * with no way to receive the origin, so the documented steps could never leave
 * the "未配置" state.
 */
'use strict';

const fs = require('node:fs');
const path = require('node:path');

const {
  resolveMobileConfig,
  buildCapacitorConfig,
  buildMobileEnvScript,
} = require('../www/js/mobile-config.js');

const mobileRoot = path.join(__dirname, '..');
const target = path.join(mobileRoot, 'capacitor.config.json');
const envTarget = path.join(mobileRoot, 'www', 'js', 'mobile-env.js');

/** Best-effort unlink that never masks the error that caused the cleanup. */
function unlinkQuietly(file) {
  try {
    fs.unlinkSync(file);
  } catch (err) {
    if (err.code !== 'ENOENT') {
      console.error('[iChat mobile] 无法清理 ' + file + '：' + err.message);
    }
  }
}

/**
 * Write via a sibling temp file so a torn write cannot leave a half file, and
 * always remove the temp file: a failed build must not litter the tree with an
 * untracked `*.tmp` that a later `git add -A` would pick up.
 */
function writeAtomic(targetPath, contents) {
  const temp = targetPath + '.tmp';
  try {
    fs.writeFileSync(temp, contents, 'utf8');
    fs.renameSync(temp, targetPath);
  } catch (err) {
    unlinkQuietly(temp);
    throw err;
  }
}

const liveReload = process.argv.indexOf('--live-reload') !== -1;
const resolved = resolveMobileConfig(process.env, { liveReload: liveReload });

if (resolved.mode !== 'configured') {
  // Exit before writing anything. Two artifacts are cleaned up here, for the
  // same reason: `cap sync` copies whatever is on disk into the APK, so a
  // leftover from an earlier build silently ships a stale configuration.
  //
  //   (a) mobile-env.js: would carry the previous origin, and the start screen
  //       would claim "就绪" while pointing at the wrong server.
  //   (b) capacitor.config.json: would carry a `server` block left by an
  //       earlier `--live-reload` run, which is not a release shape at all.
  //       It is rewritten to the release shape (no `server`) rather than left
  //       alone, because "unchanged" is only safe if the previous state was a
  //       release build.
  let hadStaleEnv = false;
  let staleEnvStuck = false;
  try {
    fs.unlinkSync(envTarget);
    hadStaleEnv = true;
  } catch (err) {
    if (err.code !== 'ENOENT') {
      // Permissions or a directory in the way: say so loudly, because silently
      // leaving it means the next `cap sync` ships the old address.
      console.error('[iChat mobile] 警告：无法删除陈旧的 ' + envTarget + '：' + err.message);
      console.error('[iChat mobile] 警告：该文件仍含上一次构建的服务地址，npx cap sync 会把它打进包。');
      staleEnvStuck = true;
    }
  }
  unlinkQuietly(envTarget + '.tmp');

  const releaseConfig = JSON.stringify(buildCapacitorConfig(null), null, 2) + '\n';
  let configReset = false;
  let staleServerBlock = false;
  let previousConfig = null;
  // Three states, not two (2026-10-27 round-3 review F-10). Collapsing
  // "does not exist" and "exists but cannot be read" into `null` meant the whole
  // reset block was skipped with ZERO output: the file may well have carried a
  // live-reload `server` block, and nobody would be told. An unreadable config is
  // not the same thing as an absent one, so it gets its own warning.
  let configUnreadable = false;
  try {
    previousConfig = fs.readFileSync(target, 'utf8');
  } catch (err) {
    if (err.code !== 'ENOENT') {
      configUnreadable = true;
      console.error('[iChat mobile] 警告：无法读取 ' + target + '：' + err.message);
      console.error('[iChat mobile] 警告：无法判断它是否含 server 块；'
        + '若上一次用过 --live-reload，打包可能产出热重载形态的 APK。');
    }
  }
  if (previousConfig !== null) {
    // Compare SEMANTICALLY, not byte-wise: a Windows working tree checked out
    // with core.autocrlf=true has CRLF here, and rewriting an already-correct
    // file would just churn line endings on every failed run.
    try {
      const parsed = JSON.parse(previousConfig);
      staleServerBlock = parsed !== null && typeof parsed === 'object' &&
        Object.prototype.hasOwnProperty.call(parsed, 'server');
    } catch (err) {
      // Unparseable: treat it as stale so it gets replaced by a known-good file.
      staleServerBlock = true;
    }
    if (staleServerBlock) {
      try {
        writeAtomic(target, releaseConfig);
        configReset = true;
      } catch (err) {
        console.error('[iChat mobile] 警告：无法把 ' + target + ' 重置为发布形态：' + err.message);
        console.error('[iChat mobile] 警告：该文件仍含 server 块，打包会产出热重载形态的 APK。');
      }
    }
  }

  console.error('[iChat mobile] 配置无效，未写入 ' + target);
  console.error('[iChat mobile] 原因：' + resolved.message);
  if (hadStaleEnv) {
    console.error('[iChat mobile] 已删除上一次构建遗留的 ' + envTarget + '（否则旧地址会随 cap sync 进包）。');
  } else if (!staleEnvStuck) {
    console.error('[iChat mobile] 未生成 ' + envTarget + '（本次未写入该文件）。');
  }
  if (configReset) {
    console.error('[iChat mobile] ' + target +
      ' 上次是 --live-reload 形态（含 server 块），已重置为发布形态（无 server 块）。');
  } else if (configUnreadable) {
    console.error('[iChat mobile] ' + target + ' 无法读取，未做任何处置（见上方警告）。');
  } else if (previousConfig !== null) {
    console.error('[iChat mobile] ' + target + ' 已是发布形态，保持不变。');
  }
  if (staleEnvStuck) {
    console.error('[iChat mobile] 警告：产物不干净，打包前请先修复上面两个问题。');
  }
  process.exit(1);
}

const envScript = buildMobileEnvScript(resolved);
if (envScript === null) {
  // resolveMobileConfig said "configured" but the renderer refused: never leave
  // the two artifacts disagreeing about whether this build has an address.
  console.error('[iChat mobile] 无法生成运行时注入脚本，未写入任何文件。');
  process.exit(1);
}

// mobile-env.js is written first: it is the file that actually decides what the
// start screen shows. capacitor.config.json is origin-independent on the
// release path, so a failure between the two leaves "old config + new env"
// rather than the dangerous "new config + old env". A rollback deletes the new
// env file: failing closed (unconfigured-looking) beats shipping a mismatched
// pair.
writeAtomic(envTarget, envScript);

const config = buildCapacitorConfig(resolved);
try {
  writeAtomic(target, JSON.stringify(config, null, 2) + '\n');
} catch (err) {
  console.error('[iChat mobile] 写入 ' + target + ' 失败：' + err.message);
  console.error('[iChat mobile] 已回滚（删除）' + envTarget + '，避免留下半套产物。');
  unlinkQuietly(envTarget);
  process.exit(1);
}

console.log('[iChat mobile] 已写入 ' + envTarget + '（运行时注入 window.ICHAT_MOBILE_ENV）');
console.log('[iChat mobile] 已写入 ' + target);
console.log('[iChat mobile] origin=' + resolved.origin + ' ws=' + resolved.wsUrl);
console.log('[iChat mobile] server.url=' + (config.server ? config.server.url : '(未设置，发布模式)'));
if (!config.server) {
  console.log('[iChat mobile] 发布模式：WebView 加载打包进 APK 的本地资源，服务地址由 mobile-env.js 提供。');
}
console.log('[iChat mobile] 下一步：npx cap sync android（把 www/ 复制进原生工程），然后构建 APK。');
