# ADR-P4-02：三端协议版本与错误码规范（P4 T31 / Issue #158）

> 状态：服务端/Web/Windows 已实现，更新于 2026-10-07，代码基线 f2f60ec；T31–T36 已闭环。Android 协议接入及设备级增强仍待实施。

## 1. 协议版本协商

| 方向 | 载荷 | 约定 |
| --- | --- | --- |
| 服务端 → 客户端 | 每个事件外层 `protocol_version` | 常量 `"1.0"`（`chat.errors.PROTOCOL_VERSION`），早已存在，保持不变 |
| 客户端 → 服务端 | 请求外层 `protocol_version`（可选） | Web 端自 T31 起在 `wsClient.sendPayload` 统一注入；缺省按 `"1.0"` 处理（**1.0 兼容窗口**，正式版本 ≥2.0 时改为必填） |

- 服务端支持集合：`SUPPORTED_PROTOCOL_VERSIONS = {"1.0"}`。
- 不支持的版本：回 `error` 事件（code=`unsupported_protocol_version`）后以 **close 4003** 关闭；客户端收到 4003 不得用同一版本无限重连（Web 端已停止重连循环并置连接徽标 `unsupported`）。
- HTTP 通道暂不做版本协商（无 envelope）；T36 统一服务层时如需引入，沿用同一注册表。

## 2. 错误码注册表（单一事实来源：`chat/errors.py`）

每个码绑定：HTTP 状态、`retryable`、默认文案。**客户端按 code 分支，禁止按 message 字符串推断**。未知 code 防御性回退为 400/不可重试。

| code | HTTP | retryable | 语义 |
| --- | --- | --- | --- |
| `invalid_payload` | 400 | 否 | 载荷结构/类型/长度不合法 |
| `unsupported_algorithm` | 400 | 否 | 加密算法不符 |
| `unsupported_protocol_version` | 400 | 否 | 协议版本不受支持（WS 关闭码 4003） |
| `authentication_required` | 401 | 否 | 会话失效（WS 关闭码 4401；HTTP /api/* 由中间件直接回 401 JSON，修 R-06） |
| `receiver_not_found` | 404 | 否 | 接收者不存在或已停用，HTTP/WS 同源拒绝 |
| `invalid_file_metadata` | 400 | 否 | 密钥材料、算法、nonce/tag/版本等不合法；拒绝零写入 |
| `conversation_not_found` | 404 | 否 | 会话不存在/不可用 |
| `conversation_forbidden` | 403 | 否 | 无权在该会话发送 |
| `membership_conflict` | 409 | 否 | 成员版本变化，需刷新成员后重试 |
| `recipients_mismatch` | 409 | 否 | 接收者列表与成员不一致 |
| `group_muted` | 403 | 否 | 群禁言 |
| `group_too_large` | 400 | 否 | 超出成员上限 |
| `message_not_found` | 404 | 否 | 消息不存在/无权 |
| `already_recalled` / `recall_timeout` | 409 | 否 | 撤回状态冲突 |
| `file_not_found` / `file_forbidden` / `file_unavailable` / `file_type_mismatch` | 404/403/409/400 | 否 | 附件相关 |
| `not_implemented` | 501 | 否 | 未实现事件 |
| `storage_unavailable` | 503 | **是** | 存储层暂时不可用（原内部码 `database_busy` 更名；旧码保留注册至 1.0 兼容窗口结束） |
| `idempotency_conflict` | 409 | 否 | 同 client_message_id 携带不同内容（已实现，文本/文件/转发统一） |
| `sync_cursor_invalid` / `sync_cursor_expired` | 400/410 | 否 | 补取游标非法/过期（已注册并实现） |

WS `error` 事件 data 形状：`{code, message, retryable}`；`retryable` 自注册表输出（此前恒为 false）。HTTP 错误体形状：`{"error": code, "detail": ...}` + 上表状态码。

## 3. R-05 修复：在线状态判定

- 旧实现调用 channel layer 的非标准 `group_channels`，内存后端抛 AttributeError 被吞 → 多标签页下最后一个连接关闭即误判离线，且无法跨进程。
- 新实现：**每用户连接计数**存于 Django cache（生产 = Redis，跨 Daphne worker 共享；单进程开发/测试 = LocMem）。`connect` 原子 `add+incr`，`disconnect` 原子 `decr`；计数归零才置离线并广播 `presence.updated`（offline），首个连接才置在线并广播 online。
- 计数器不可用（cache 故障）时回退旧行为（connect 置在线 / disconnect 置离线），只降级精度不阻断连接。
- 键：`ws:connections:{user_id}`，无过期时间；连接/断开严格配对增减。
- 多连接/多进程语义由 Test T13/T14 在真实 Redis 上验证（服务器已具备）。

## 4. 客户端处置矩阵（按 code）

| 客户端收到 | 动作 |
| --- | --- |
| `unsupported_protocol_version` | 停止重连（4003），提示升级 |
| `authentication_required`（HTTP 401 或 WS 4401） | 跳转登录页（带 next），销毁本地连接状态 |
| `membership_conflict` / `recipients_mismatch` | 刷新成员列表/密钥后按原 client_message_id 重试（保留原 ID，处理冲突后重试） |
| `idempotency_conflict` | 停止重试，提示冲突，不允许覆盖原消息（已实现） |
| `storage_unavailable`（retryable=true） | 保留待发项，按退避重试 |
| 其余 `retryable=false` | 消息标 failed，允许用户手动重试（仅未确认消息可失败，已持久化状态不回退） |

## 5. 兼容窗口与验收注记

- 服务端接受**缺省版本**的请求直至协议 2.0 引入；Web 客户端自 T31 起总是发送 `protocol_version: "1.0"`。
- `database_busy` 更名为 `storage_unavailable`：旧码保留注册，服务端代码已全部使用新码。
- T31 交付物：本 ADR、`chat/errors.py`、WS 版本门禁、API 401 中间件、R-05 计数器、Web 客户端处置逻辑、测试（`chat/tests/test_protocol.py`）。


## 6. 遗漏消息补取（T34/T35 落地，2026-10-01）

### 6.1 持久事件模型

- `Conversation.sync_sequence`：会话内单调致密序号，在发送事务内（持会话行锁时）分配——并发不会产生重复或空洞。
- `ConversationEvent(conversation, sequence, kind, message_type, message_id, created_at)`：唯一约束 `(conversation, sequence)`，只存引用与元数据。**P0 范围**：仅 message 创建事件（文本/文件/转发，全部经统一服务层埋点）；撤回/删除/成员变更事件属 P1，实现前不得宣传。

### 6.2 补取接口

```
GET /api/conversations/{id}/sync/?cursor=&limit=100
→ 200 {items: [{sequence, kind, created_at, message: <viewer投影>}],
       next_cursor, has_more, high_water}
```

- `limit` 默认 100，整型值截断到 1..200，非整型返回 400 `invalid_payload`；`items` 按 `sequence` 升序。
- **空 cursor**：快照新 high_water，从 sequence 0 起全量补取（首次进入会话用）。
- **游标语义**：`next_cursor` **恒返回**——walk 未完成时指向下一页（快照冻结，范围不含 high_water 之后的新事件）；walk 完成时绑定 high_water 并**释放快照**（`hw=null`），客户端下一次携带该游标即只取增量。客户端必须持久化 `next_cursor`（按服务 Origin + user + conversation 隔离），分页失败从上一游标重试，不得先存 high_water。
- 游标为 HMAC-SHA256 签名（绑定会话+用户，T34 P0；设备绑定 P1），TTL 7 天——过期返回 **410 `sync_cursor_expired`**（客户端重新快照），篡改/跨会话复用返回 **400 `sync_cursor_invalid`**。
- 读取时重做当前权限投影，与历史端点**完全同源**（复用同一批可见性 helper）：成员资格、群聊 pre-join 过滤（新成员拿不到加入前事件——与 E2EE 密钥边界一致）、逐 viewer 密文投影（sender_copy 语义与实时推送一致）、**cleared_at / 自动删除截断 / 个人删除排除 / 私聊拉黑 403**（复验整改：游标不能扩大任何可见性）。撤回/删除的状态变更**事件**仍属 P1。

### 6.3 边界（P0 如实声明）

- **不回填历史**：事件日志自部署起累积；部署前的旧历史仍走分页接口（其排序已加 `(created_at, id)` 稳定 tiebreaker，T35）。
- 客户端仍以实时推送为主通道，sync 用于断线补取与对账；`group_send` 成功不代表持久送达（见 technical-design §1）。



## 7. 敏感数据存储边界

> T21 已验收 #148；本节更新 2026-10-07，以当前 main.js/key-manager.js/chat.js 为准。

| 数据 | 位置及保护 | 已知边界 |
| --- | --- | --- |
| 长期 E2EE 私钥 | IndexedDB 不可导出 CryptoKey | 同源代码仍可使用密钥；不等同防御 XSS |
| 创建/导入/迁移后的待导出 JWK | 账户键控 sessionStorage；桌面经 safeStorage/DPAPI 加密 | 桌面桥不可用不写新明文并失效旧 pending；Web 回退明文、tab 作用域警告 |
| 公开密钥记录与对端公钥缓存 | localStorage，仅公开材料/版本 | 不得把缓存命中当身份验证或绕过密钥变化信任规则 |
| 聊天草稿、全部 Assistant 历史 | 桌面：内存缓存 + safeStorage 密文 localStorage | Web 回退明文；桌面无桥新写入仅内存；迁移失败保留旧记录 |
| 登录会话 | HttpOnly Session Cookie，生产 Secure/SameSite | Cookie/会话不是 E2EE 私钥；不得输出到日志 |
| 服务器日志 | journald/Nginx，按 Runbook 脱敏约束 | 不记录 Cookie、请求正文、密文载荷和完整凭据；专项审计仍需证据 |

桥 API 为 `iChatDesktop.secureStorage.{isAvailable,encrypt,decrypt}`。main 侧检查 sender frame：应用 Origin 或 file URL；preload 仅暴露窄接口。encrypt 限制 **2,097,152 UTF-8 字节**；decrypt 输入 `enc:`+base64 最多 **2,900,000 字符**，解码后密文最多 **2,162,688 字节**（2 MiB + 64 KiB DPAPI 余量）。这是不同单位的独立预算，不是旧 16KB 限制。

KV 启动扫描草稿和全部 AI 历史键，解密进内存缓存。脏键与 per-key epoch 防止在途水合/写入覆盖新值；清空使旧任务失效。迁移在同一键用确认得到的密文覆盖明文，不再 removeItem 删除刚写的密文。未解析或未解密成功的记录保留，迁移不可用时也保留旧明文；不能承诺旧记录全部加密。新持久化失败按 key+epoch 维护警告，其他键成功不清除；清空对应键清掉该失败状态。

运行中网络中断用覆盖横幅，保留 socket 与内存待发箱；启动失败才去本地离线页。内存 outbox 不承诺页面重载/重启恢复。Web pending 备份、草稿/AI 回退及原生 KeyStore 的完整迁移是明确限制，不能用 Windows DPAPI 结论代替 Android/多设备验收。

## 8. 前端补取与时间线应用约定

补取与实时新增共用应用队列。整 walk 占一个队列任务，各页直接顺序 await 应用，不能在同一队列里嵌套 enqueue 并等待。整页成功才写 next_cursor；占位解密失败不能冒充去重成功。本人来自另一设备的消息也应用；判重按条目自身会话，后台 seen registry 容量 500。

分页按 has_more（包括空 items）继续，100 页安全暂停后 2 秒续跑；过期 410 清旧游标并重新快照一次。暂停允许实时流动，因此呈现按 `(created_at, id)` 有序插入。ACK 采用服务端 created_at；临时/正式 ID 转换通过稳定行身份，先正确定位完整行再排序和重算相邻分组。回归入口为 npm run test:e2ee，包含实际接线的 message_accepted.test.js。
