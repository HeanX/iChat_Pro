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
 * Two invariants, both review findings from the T22 review:
 *
 *   1. Release output MUST NOT contain a `server` block. A live-reload URL is
 *      only emitted when --live-reload is passed explicitly.
 *   2. An invalid configuration MUST NOT touch the committed file. The earlier
 *      version still wrote a fallback before exiting 1, which meant a mistyped
 *      CI variable could rewrite a tracked file.
 *
 * T22 remediation (review AND-01): the step ALSO writes the runtime injection
 * artifact `www/js/mobile-env.js`, which is what `www/js/shell.js` reads as
 * `window.ICHAT_MOBILE_ENV`. Before this, `npx cap sync` shipped a start screen
 * with no way to receive the origin, so the documented steps could never leave
 * the "未配置" state. Both files are written together, only when the config
 * resolves, and nothing is written on the invalid path.
 */
'use strict';

const fs = require('node:fs');
const path = require('node:path');

const {
  resolveMobileConfig,
  buildCapacitorConfig,
  buildMobileEnvScript,
} = require('../www/js/mobile-config.js');

/** Write via a sibling temp file: a torn write must not leave a half file. */
function writeAtomic(target, contents) {
  const temp = target + '.tmp';
  fs.writeFileSync(temp, contents, 'utf8');
  fs.renameSync(temp, target);
}

const liveReload = process.argv.indexOf('--live-reload') !== -1;
const resolved = resolveMobileConfig(process.env, { liveReload: liveReload });
const mobileRoot = path.join(__dirname, '..');
const target = path.join(mobileRoot, 'capacitor.config.json');
const envTarget = path.join(mobileRoot, 'www', 'js', 'mobile-env.js');

if (resolved.mode !== 'configured') {
  // Exit before writing anything: the committed capacitor.config.json stays as
  // reviewed, and no mobile-env.js may exist, because shell.js would otherwise
  // pick up a stale address from an earlier build.
  console.error('[iChat mobile] 配置无效，未写入 ' + target);
  console.error('[iChat mobile] 原因：' + resolved.message);
  console.error('[iChat mobile] 现有 capacitor.config.json 保持不变；未生成 ' + envTarget);
  process.exit(1);
}

const envScript = buildMobileEnvScript(resolved);
if (envScript === null) {
  // resolveMobileConfig said "configured" but the renderer refused: never leave
  // the two artifacts disagreeing about whether this build has an address.
  console.error('[iChat mobile] 无法生成运行时注入脚本，未写入任何文件。');
  process.exit(1);
}

const config = buildCapacitorConfig(resolved);
writeAtomic(target, JSON.stringify(config, null, 2) + '\n');
writeAtomic(envTarget, envScript);

console.log('[iChat mobile] 已写入 ' + target);
console.log('[iChat mobile] origin=' + resolved.origin + ' ws=' + resolved.wsUrl);
console.log('[iChat mobile] server.url=' + (config.server ? config.server.url : '(未设置，发布模式)'));
console.log('[iChat mobile] 已写入 ' + envTarget + '（运行时注入 window.ICHAT_MOBILE_ENV）');
if (!config.server) {
  console.log('[iChat mobile] 发布模式：WebView 加载打包进 APK 的本地资源，服务地址由 mobile-env.js 提供。');
}
console.log('[iChat mobile] 下一步：npx cap sync android（把 www/ 复制进原生工程），然后构建 APK。');
