# 模块 3：Web 前端与端到端加密（`static/js/` + `templates/`）

> 交接对象：负责浏览器端页面、加密、连接状态机、通知门控的 Agent。
> 核对日期 2026-10-18，基线 `cfe53be`。规范见 [conventions.md](conventions.md)。

---

## 1. 这块现在是什么

无构建步骤、无框架，全部是 IIFE + `window` 全局模块，由 Django 模板直接 `<script src>` 引入。

| 文件 | 大小 | 导出 | 职责 |
| --- | ---: | --- | --- |
| `chat.js` | 430 KB / 10,364 行 | 约 41 个 `window.*` + 全局函数 | 主应用：会话列表、消息渲染、发送、设置页内嵌、AI 助手 |
| `chat-connection.js` | 19 KB / 505 行 | `window.ChatConnection`（UMD） | 连接状态机、退避、心跳、待发箱、消息状态机、sync 走页、去重 |
| `key-manager.js` | 15 KB / 396 行 | `window.iChatKeyManager` | 身份密钥生成/存取（IndexedDB，不可导出 CryptoKey） |
| `private-chat-e2ee.js` | 28 KB / 702 行 | `window.iChatPrivateE2EE` | 私聊加密/解密、公钥缓存、信任存储、文件密钥 wrap |
| `group-chat-e2ee.js` | 29 KB / 743 行 | `window.iChatGroupE2EE` | 群聊逐收件人加密/解密、批量取公钥 |
| `file-transfer.js` | 45 KB / 1,094 行 | `window.iChatFileTransfer` | 分块加密上传/下载解密 |
| `chat-notifications.js` | 6 KB / 148 行 | `window.ChatNotifications`（UMD） | 通知门控（是否通知 + 通知什么内容），纯函数无 DOM |
| `chat-message-actions.js` | 38 KB / 1,058 行 | `window.MessageActions` | 消息右键菜单：复制/回复/转发/删除/撤回/重发/多选 |
| `chat-conversation-actions.js` | 13 KB / 403 行 | `window.ConversationActions` | 会话右键菜单：置顶/静音/归档/清空/删除/已读 |
| `context-menu.js` | 7 KB / 218 行 | `window.ContextMenu` | 单例右键菜单组件 |
| `event-dispatcher.js` | 5 KB / 124 行 | — | CSP 事件委派：`data-ichat-onclick` 等 |
| `translations_data.js` | 66 KB / 1,498 行 | `window.translations` / `t` / `applyLanguage` | 4 语言 × 332 键 |
| `theme.js` | 4 KB / 120 行 | `window.ThemeManager` | **无模板引用（死代码）** |
| `base-init.js` / `auth-theme.js` | 小 | — | 首屏主题防闪烁、登录页主题切换 |

### 脚本加载顺序（不能乱）

`templates/pages/chat.html`：

```
key-manager（带 data-current-user-id）→ private-chat-e2ee → group-chat-e2ee
→ file-transfer → context-menu → chat-conversation-actions → chat-message-actions
→ chat-connection → chat-notifications → chat.js → 内联 nonce 脚本
```

`templates/base.html` 末尾统一加载 `event-dispatcher.js` → `translations_data.js` → `base-init.js`。

**硬依赖**：`chat.js` 在**模块求值期**（不是 DOMContentLoaded）就读 `window.ChatNotifications.createNotificationCenter`（:24）和 `ChatConnection.createSeenRegistry(500)`（:266）。这两个模块必须先于 `chat.js` 加载，否则整页脚本报错。

### 模板组织

| 位置 | 说明 |
| --- | --- |
| `templates/base.html` | head：本地 Tailwind CSS（生产）或 Play CDN（开发）+ Lucide 0.462.0（SRI）+ `base.css`；body 末 3 个脚本 |
| `templates/pages/chat.html` | **唯一主工作区**，include sidebar / standalone_contacts / standalone_groups / standalone_group_detail / empty_state / chat_window / ai_assistant_window / right_panel / drawer_menu |
| `templates/pages/login.html` / `register.html` | extends `base_auth.html` |
| `templates/pages/avatar_editor.html` | 独立完整 HTML，**不继承 base** |
| 其余 `pages/*` | 都是被 `components/sidebar.html` 内嵌的侧栏面板，各自带 `<script nonce>` |

**设置页是内嵌面板，不是独立页面**：`#sidebar-view-settings-home`（`settings_home.html`）+ `#sidebar-view-settings`（`settings_panel_body.html`），由 `navigateSidebar(view)` 切显隐。`chat/views.py` 的 `settings_view()` 实际渲染的还是 `pages/chat.html`（`open_settings=True`）。

**孤儿模板**（无任何 view 渲染，改它们不会有任何效果）：`pages/settings.html`、`components/chat_layout_base.html`、`components/sidebar_page.html`、`components/message_bubble.html`、`components/chat_list_item.html`、`components/auto_download_row.html`、`components/profile_detail_sidebar.html`。

---

## 2. 最短读代码路径

1. `static/js/chat-connection.js` 的头部注释（19-80 行）+ 文件末尾导出列表 —— 这是全部连接语义。
2. `static/js/key-manager.js` 的 `DB_NAME` / `KEY_STORE` 与 `window.iChatKeyManager` 导出（376 行起）。
3. `static/js/private-chat-e2ee.js` 的常量头（8-12 行）与 `window.iChatPrivateE2EE` 导出（686 行起）。
4. `static/js/chat-notifications.js` 全文（148 行，含完整门控顺序）。
5. `chat.js` **不要通读**。先看 6-24 行与 99-122 行的全局变量区，再用符号搜索（`navigateSidebar`、`renderMessages`、`sendMessage`、`selectChat`）。

---

## 3. 加密契约（不能改）

| 项目 | 值 |
| --- | --- |
| 身份算法 | ECDH P-256 |
| 消息算法 | AES-256-GCM |
| 派生 | HKDF，`info = "chat-message-encryption-v1"` |
| nonce / auth tag | 12 字节 / 16 字节（服务端强校验） |
| 单条密文上限 | 65,536 字节（base64 解码后） |
| 私钥存放 | IndexedDB `ichat-e2ee-keys` / store `identity_private_keys`，**不可导出 CryptoKey** |
| 公钥缓存 localStorage | `ichat_e2ee_public_key_cache`（上限 200 条） |
| 对端信任 localStorage | `ichat_peer_identity:<userId>` |
| 群聊 | 每个收件人一份独立密文（`GroupMessageRecipient`） |
| 文件分块 | 1 MiB/块，单文件上限 100 MiB，最多 4 块并发 |
| 文件密钥 | 每持有者一条 wrapped key（`EncryptedFileKey`） |

**历史兼容**：任何改动都必须保证旧密文仍可解。换算法 = 需要版本升级路径 + 服务端 `algorithm` 字段同步。

---

## 4. 存储边界（三层，别搞混）

| 层 | 存什么 | 加密 |
| --- | --- | --- |
| IndexedDB `ichat-e2ee-keys` v1 | 身份私钥 CryptoKey（键 `${userId}` 或 `${userId}:v{keyVersion}`，旋转后双写） | 由浏览器密钥库保护，不可导出 |
| localStorage（桌面走 safeStorage） | `ichat_drafts`、`ichat_ai_history[:assistantId]` | **Web 端明文**，桌面端 DPAPI |
| localStorage（配置，始终明文） | `ichat_identity_key:{userId}`（只有公钥记录）、两个公钥缓存、`ichat_peer_identity:*`、`ichat_ai_*`、`ichat_lang`、`ichat-theme`、`ichat_sessions`、`ichat_accounts`、`ichat_folders`、`ichat:syncCursor:*` | 无（本就非敏感） |
| sessionStorage | `ichat_identity_key_pending_backup:{userId}`（待导出 JWK；桌面为 `enc:` + DPAPI 密文） | Web 端明文，仅会话内 |

**已知限制**：浏览器端草稿与 AI 历史可被同源脚本读取；桌面端加密只在 Electron 下生效；桥不可用时新写入只留内存并失效旧 pending，迁移失败会保留旧明文。不要对外宣称"全端未发送内容均已加密"。

---

## 5. 连接状态机（`ChatConnection`）

状态机常量在 `chat-connection.js`：连接态为 `disconnected → connecting → online`，断开走 `reconnecting`；`auth_required`（close 4401）与 `unsupported`（close 4003）是**终态，不再重连**。

| 参数 | 值 |
| --- | --- |
| 心跳间隔 | 25,000 ms（服务端下发 30s，客户端更激进） |
| pong 超时 | 10,000 ms，超时强制关闭 socket 交给退避循环 |
| 退避 | base 500 ms × 2^(attempts-1)，jitter ±25%，上限 25,000 ms（< 30s 验收要求） |
| ACK 超时 / 最大重试 | 10,000 ms / 5 次，重试用**同一个** `client_message_id` |

消息状态机：`sending → sent → delivered → read`；`failed` 只能由 `sending` 进入，且可回 `sending`（重试）或直接 `sent`（迟到 ACK）。`canTransitionMessage` 拒绝任何降级。

去重：`createSeenRegistry(500)` 按会话分桶、每桶上限 500（FIFO），实时推送与 sync 补取共享同一注册表。

游标：`createSyncWalker` 把整个 walk 占为**一个** apply-queue 任务（页内再 enqueue 会死锁）；每页全部 item 应用成功后才写 `nextCursor`；应用失败保留**上一**游标（at-least-once）；`sync_cursor_expired` 清游标并重快照一次；空 items 仍按 `has_more` 续页；`maxPages 100` 后返回 `paused`。游标键 `ichat:syncCursor:{userId}:{convId}`。

待发箱在**内存**：断网与托盘隐藏/恢复可存活；页面重载、进程重启会丢。

### 消息渲染约定

| 项目 | 规则 |
| --- | --- |
| 状态图标 | `renderStatusIconHtml`（chat.js:5846）单点产出；`patchMessageStatusInPlace` 只替换图标，不整表重绘 |
| 排序 | `timelineInsertIndex` 按 `(created_at, id)` 二分定位 |
| 行身份 | `data-row-message-id` 稳定属性；`repositionMessageRow` 移动**整行**而非气泡子节点 |
| 迟到 ACK | 用服务端 `created_at` 重排，并重建相邻分组 |
| 去重 | 活动会话按 `messages` 内 id；后台会话按 seen registry；sync item 按**其自身会话**判重 |
| 历史分页 | `fetchMessages` `per_page=30`，API 返回新→旧，客户端 `reverse()`；滚动到顶 100px 触发下一页并保持位置 |
| 草稿 | localStorage `ichat_drafts`（convId→text），切会话保存、进会话恢复、发送成功清除 |

---

## 6. 通知门控顺序（`ChatNotifications`）

```
0  无 show 回调                  → 不通知
1  meta.isSelf                   → 不通知（自发消息）
2  meta.source !== 'realtime'    → 不通知（sync 补取只更新徽标）
3  isAppUnfocused()              → 聚焦时自己就能看到，不弹
4  normalizeSettings             → 缺省全 false，非 boolean 字段不采用
5  display_notifications         → 总开关
6  private_chat_notifications / group_chat_notifications
7  会话元数据未知                → 不通知（宁可漏也不误弹）
8  muted_until > now             → 跳过
9  标题：群聊「群名 · 发送者」，否则发送者/会话名/"iChat Pro"
10 预览：previewAllowed && 未解密失败 && 非文件消息 && 有正文，才用正文；否则四语通用文案
11 截断：title 80 / body 160，超长补 "…"
12 桌面桥 iChatDesktop.notifications.show()
```

`isAppUnfocused` 由 `chat.js` 传入 `document.hidden || !document.hasFocus()`。

**Web 端不弹系统通知**：`chat.js` 里没有 `new Notification(...)`，浏览器里只有设置页的权限 UI。系统通知只在 Electron 桌面上通过桥发出。

设置未知/损坏时**默认全关**（`DEFAULT_SETTINGS` 全 `false`）。

---

## 7. CSP 约束（最容易踩）

`ichat_pro/csp_middleware.py` 默认策略：

- `script-src`：`'self'` + `nonce-<随机>` + `https://unpkg.com`（生产不含 Tailwind CDN）；**默认不含 `'unsafe-inline'`**。
- `style-src`：含 `'unsafe-inline'`（`CSP_ALLOW_UNSAFE_INLINE_STYLE` 默认 true）。
- `connect-src`：`'self' ws://<host> wss://<host>`。
- `img-src`：`'self' data: blob:`；`object-src 'none'`；`frame-ancestors 'none'`。
- `DEBUG=True` 时该响应头会被**改名**为 `Content-Security-Policy-Report-Only`——开发期只报告不拦截。

因此：**新写内联 `onclick=` 会被静默拦截**。事件必须用 `data-ichat-on*` 形式，由 `event-dispatcher.js` 委派。模板里目前共 **219 处 `data-ichat-on*`**，其中 `data-ichat-onclick` **193 处**、`oninput` 16、`onchange` 6、`onsubmit` 4；HTML 属性形式的静态 `onclick=` 已清零（`templates/` 下没有），但页面里仍有 JS 直接赋值的 `el.onclick = function()`（如 `contacts_sidebar.html:178`），那不受 CSP 限制。内联 `<script>` 需带 `nonce="{{ request.csp_nonce }}"`。

**已知未修的遗留**：`chat.js` 里有 **21 处 `onclick=`**，其中约 **18 处**是动态 `innerHTML` 拼接出来的（群管理、隐私选择器、解除拉黑、群侧栏等；行号 933/941/1035/4139/4140/4237/4582/4583/4645/4647/4650/4654/4744/4865/4866/4899/4900/4901/8797/8897），另 1 处 6326 是 `querySelector` 选择器、属误计入。这些按钮在开发期（Report-Only）能点，在**生产 CSP 下不会响应**。修它们是把字符串改成 `data-ichat-on*` 属性 + 补 `event-dispatcher.js` 的解释器能力，属于真实缺陷而非文档问题。

---

## 8. 本地验证

```powershell
npm run test:e2ee
# 11 个文件全过；其中 node --test 部分 62 项。约 40s。

node .\chat\tests\js\chat_connection.test.js     # 20 个 test_* 函数，输出 9 项 ✓，5s
node .\chat\tests\js\private_chat_e2ee.test.js   # 输出 "private-chat-e2ee: all tests passed"
```

**不需要 Electron 或浏览器**：全部测试用 `node:vm` + `node:crypto.webcrypto` + stub 的 fetch/localStorage/DOM。桌面两套（`desktop_tray` / `desktop_notifications`）可通过 `ICHAT_DESKTOP_TEST_ARCHIVE` 改读打包后的 asar。

布局相关（真 Chromium，仅 Windows）：

```powershell
cd desktop
npm run test:layout      # 36 项：3 尺寸（1260×720 / 900×480 / 400×720）× 2 主题 × 6 检查
```

`test:layout` 是**生产源码 + 隔离 mock API**，不是登录态生产页面实测——这一点在 #145 里已被明确记录。

---

## 9. 已知坑

| # | 坑 | 细节 |
| --- | --- | --- |
| 1 | `chat.js` 430 KB 单文件 | 不要整文件重构；改动前用 grep 定位函数，改完只提交必要行 |
| 2 | 待发箱不持久化 | 页面重载丢未确认消息；这是已知 P0 限制，不是 bug |
| 3 | 首次离线不能新建加密关系 | 需要已缓存且符合信任规则的对端公钥 |
| 4 | 信任不自动继承 | 对端换钥后必须显式确认；新版本解密只派发 `ichat:peer-key-observed`（**全仓无监听者**），发送前要人工 confirm |
| 5 | Web 端明文草稿 | 只在桌面端走 safeStorage |
| 6 | 通知设置改动即时生效但依赖 epoch | `chat.js` 用 `notificationSettingsEpoch` 防迟到响应，改这块注意竞态 |
| 7 | 内联事件被 CSP 拦 | 约 20 处动态 `innerHTML` 里的 `onclick=` 在生产 CSP 下失效，见上节 |
| 8 | 图标依赖 unpkg CDN | CSP 白名单里有 `unpkg.com`，离线环境会缺图标 |
| 9 | 私钥导出只有一次机会 | 长期私钥不可导出，备份 JWK 只能在创建/导入/迁移后从 sessionStorage 的 pending backup 导出 |
| 10 | `window.localKeyVersion` 全仓无赋值 | `file-transfer` / `chat-message-actions` 的兜底元数据会退化为 `1`（只影响省略 metadata 的调用路径） |
| 11 | `iChatFileTransfer._onUploadCompleted` 被调用但未导出 | `chat.js:1806` 调用它，但模块没有导出该函数，`file.upload.completed` 事件实际无效果 |
| 12 | 群聊"永久静音"实为 7 天 | `muted_until` 用的是 10080 分钟，不是无限 |
| 13 | 多设备靠 `sender_copy` | 发送方无法用接收方密文自解（已用 Node 实测），未投影 `sender_copy` 的路径只能解一侧 |

---

## 10. 这个模块的开放任务

| Issue | 内容 | 优先级 | 缺口 |
| --- | --- | --- | --- |
| [#145](https://github.com/HeanX/iChat_Pro/issues/145) | P4 T18 Windows 系统通知 | P1 | **实现与回归已完成**，只差正式登录客户端的预览切换/返回聊天/顶部位置最终复验与截图 |
| [#165](https://github.com/HeanX/iChat_Pro/issues/165) | P4 T38 E2EE 威胁模型与数据流 | P0 | 浏览器端已有 `docs/iChat Pro 浏览器端安全威胁模型.md`，缺跨平台完整版 |
| [#189](https://github.com/HeanX/iChat_Pro/issues/189) | P4 Test T08 密钥指纹与变化提醒 | P0 | 需 UI + 接口 + 测试三件套 |
| [#196](https://github.com/HeanX/iChat_Pro/issues/196) | P4 Test T15 浏览器/Electron E2E | P1 | 无 Playwright；现有 Chromium 测试是布局 fixture |

**#145 的最短路径**：用正式安装版（`desktop/dist/iChat-Pro-Setup-1.0.0.exe`）登录 → 打开设置 → 切换通知预览开关 → 返回聊天 → 检查顶部布局未压缩 → 截图 → 贴到 Issue。这是"只差证据"的项，投入最小。

---

## 11. 给下一个 Agent 的提示词

```text
你在 iChat_Pro 仓库负责【Web 前端与 E2EE 模块】。

必读：docs/handoff/conventions.md、docs/handoff/web-frontend-e2ee.md、
docs/phase4/protocol.md 第 7 节（敏感数据存储边界）。
再读代码：static/js/chat-connection.js 头部注释与导出、chat-notifications.js 全文、
private-chat-e2ee.js 的常量与导出；chat.js 只按符号搜索定位。

任务：【填写 Issue 号与标题】。

不可破坏：
- 算法常数（ECDH-P256 / AES-256-GCM / HKDF info）与 12/16 字节 nonce/tag。
- IndexedDB 库名 ichat-e2ee-keys、store 名 identity_private_keys。
- 协议 event 名与消息状态机方向（只向前）。
- CSP：不得新增内联事件处理器，用 data-ichat-on* 委派。

必须执行：
npm run test:e2ee
（涉及布局）cd desktop && npm run test:layout

完成前必须另开独立审查子 Agent 复核 diff（见 conventions.md §1）。
```
