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
  // Exit before writing anything: the committed capacitor.config.json stays as
  // reviewed. A stale mobile-env.js is removed rather than merely reported,
  // because `cap sync` would otherwise bake the previous origin into the APK.
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

  console.error('[iChat mobile] 配置无效，未写入 ' + target);
  console.error('[iChat mobile] 原因：' + resolved.message);
  console.error('[iChat mobile] 现有 capacitor.config.json 保持不变。');
  if (hadStaleEnv) {
    console.error('[iChat mobile] 已删除上一次构建遗留的 ' + envTarget + '（否则旧地址会随 cap sync 进包）。');
  } else if (!staleEnvStuck) {
    console.error('[iChat mobile] 未生成 ' + envTarget + '（本次未写入该文件）。');
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
