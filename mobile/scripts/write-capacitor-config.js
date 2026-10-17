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
 */
'use strict';

const fs = require('node:fs');
const path = require('node:path');

const { resolveMobileConfig, buildCapacitorConfig } = require('../www/js/mobile-config.js');

const liveReload = process.argv.indexOf('--live-reload') !== -1;
const resolved = resolveMobileConfig(process.env, { liveReload: liveReload });
const target = path.join(__dirname, '..', 'capacitor.config.json');

if (resolved.mode !== 'configured') {
  // Exit before writing anything: the committed capacitor.config.json stays as
  // reviewed. Nothing here can silently replace it with a half-valid fallback.
  console.error('[iChat mobile] 配置无效，未写入 ' + target);
  console.error('[iChat mobile] 原因：' + resolved.message);
  console.error('[iChat mobile] 现有 capacitor.config.json 保持不变。');
  process.exit(1);
}

const config = buildCapacitorConfig(resolved);
fs.writeFileSync(target, JSON.stringify(config, null, 2) + '\n', 'utf8');

console.log('[iChat mobile] 已写入 ' + target);
console.log('[iChat mobile] origin=' + resolved.origin + ' ws=' + resolved.wsUrl);
console.log('[iChat mobile] server.url=' + (config.server ? config.server.url : '(未设置，发布模式)'));
if (!config.server) {
  console.log('[iChat mobile] 发布模式：WebView 加载打包进 APK 的本地资源，运行时由 www/index.html 决定服务地址。');
}
