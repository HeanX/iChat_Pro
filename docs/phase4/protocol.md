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
