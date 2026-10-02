# ADR-P4-02：三端协议版本与错误码规范（P4 T31 / Issue #158）

> 状态：已实现（服务端 + Web 客户端，2026-10-01）。本文是协议契约，HTTP/WS 双通道与三端实现逐步对齐；标注「T32/T36 落地」的条目在对应任务验收前不得宣传为已有能力。

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
| `idempotency_conflict` | 409 | 否 | 同 client_message_id 携带不同内容（**T32 落地**，注册先行） |
| `sync_cursor_invalid` / `sync_cursor_expired` | 400/410 | 否 | 补取游标非法/过期（**T34 落地**，届时注册） |

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
| `membership_conflict` / `recipients_mismatch` | 刷新成员列表/密钥后按原 client_message_id 重试（T32 起原 ID 重试可见） |
| `idempotency_conflict` | 停止重试，提示冲突，不允许覆盖原消息（T32 落地） |
| `storage_unavailable`（retryable=true） | 保留待发项，按退避重试 |
| 其余 `retryable=false` | 消息标 failed，允许用户手动重试（T32/T33 落地完整状态机） |

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

- `limit` 1..200；`items` 按 `sequence` 升序。
- **空 cursor**：快照新 high_water，从 sequence 0 起全量补取（首次进入会话用）。
- **游标语义**：`next_cursor` **恒返回**——walk 未完成时指向下一页（快照冻结，范围不含 high_water 之后的新事件）；walk 完成时绑定 high_water 并**释放快照**（`hw=null`），客户端下一次携带该游标即只取增量。客户端必须持久化 `next_cursor`（按服务 Origin + user + conversation 隔离），分页失败从上一游标重试，不得先存 high_water。
- 游标为 HMAC-SHA256 签名（绑定会话+用户，T34 P0；设备绑定 P1），TTL 7 天——过期返回 **410 `sync_cursor_expired`**（客户端重新快照），篡改/跨会话复用返回 **400 `sync_cursor_invalid`**。
- 读取时重做当前权限投影，与历史端点**完全同源**（复用同一批可见性 helper）：成员资格、群聊 pre-join 过滤（新成员拿不到加入前事件——与 E2EE 密钥边界一致）、逐 viewer 密文投影（sender_copy 语义与实时推送一致）、**cleared_at / 自动删除截断 / 个人删除排除 / 私聊拉黑 403**（复验整改：游标不能扩大任何可见性）。撤回/删除的状态变更**事件**仍属 P1。

### 6.3 边界（P0 如实声明）

- **不回填历史**：事件日志自部署起累积；部署前的旧历史仍走分页接口（其排序已加 `(created_at, id)` 稳定 tiebreaker，T35）。
- 客户端仍以实时推送为主通道，sync 用于断线补取与对账；`group_send` 成功不代表持久送达（见 technical-design §1）。


## 7. 敏感数据存储边界（T21 落地，2026-10-02）

| 数据 | 位置 | 保护 |
| --- | --- | --- |
| E2EE 私钥（长期） | 浏览器 IndexedDB（不可导出 CryptoKey） | Web 平台边界；Electron 同 |
| 待导出私钥备份（JWK，创建/导入/迁移后暂存） | sessionStorage，**桌面构建经 Electron safeStorage 加密**（DPAPI，`enc:` 前缀） | 桌面：OS 级加密；Web 回退：tab 作用域 + 明确警告（M4 KeyStore 契约替代） |
| localStorage 密钥记录 | 公开元数据（无私钥字段，private_key 已删除） | 非敏感 |
| 会话凭据 | HttpOnly Session Cookie | 服务端 Session；桌面同 |
| 服务器日志 | 部署 runbook 约定：无 Cookie/密文载荷/完整请求体 | journald + nginx 分文件 |

- 桌面桥：`iChatDesktop.secureStorage.{isAvailable,encrypt,decrypt}`（ipcRenderer.invoke，通道白名单；main 侧 sender frame 校验 + 16KB 载荷上限；Electron safeStorage = Windows DPAPI）。
- 运行中连接丢失**不导航**：桌面端注入覆盖横幅（非破坏性），M2 待发箱与 socket 重试保留；导航仅发生在启动失败（无待发箱可丢）。
- 已知限制（如实）：Web 回退路径的 sessionStorage 备份未加密（tab 关闭即清除）；M4 KeyStore 契约将统一迁移。
