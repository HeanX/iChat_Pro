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
 * Release output MUST NOT contain a `server` block. `npm run config:generate`
 * exits non-zero if ICHAT_MOBILE_SERVER_URL is unset, so a release build can
 * never silently bake in a developer machine.
 */
'use strict';

const fs = require('node:fs');
const path = require('node:path');

const { resolveMobileConfig, buildCapacitorConfig } = require('../www/js/mobile-config.js');

const liveReload = process.argv.indexOf('--live-reload') !== -1;
const resolved = resolveMobileConfig(process.env, { liveReload: liveReload });
const config = buildCapacitorConfig(resolved);

const target = path.join(__dirname, '..', 'capacitor.config.json');
fs.writeFileSync(target, JSON.stringify(config, null, 2) + '\n', 'utf8');

if (resolved.mode !== 'configured') {
  console.error('[iChat mobile] 配置无效：' + resolved.message);
  console.error('[iChat mobile] 已写入不含 server 块的默认配置：' + target);
  process.exit(1);
}

console.log('[iChat mobile] 已写入 ' + target);
console.log('[iChat mobile] origin=' + resolved.origin + ' ws=' + resolved.wsUrl);
console.log('[iChat mobile] server.url=' + (config.server ? config.server.url : '(未设置，发布模式)'));
if (!config.server && !liveReload) {
  console.log('[iChat mobile] 发布模式：WebView 加载打包进 APK 的本地资源，运行时由 www/index.html 决定服务地址。');
}
