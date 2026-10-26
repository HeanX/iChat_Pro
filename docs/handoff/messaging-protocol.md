# 模块 2：消息、协议与同步补取（`chat/services/` + `chat/consumers.py`）

> 交接对象：负责消息写入一致性、WebSocket 协议、幂等、同步补取的 Agent。
> 核对日期 2026-10-18，基线 `cfe53be`。规范见 [conventions.md](conventions.md)。

---

## 1. 这块现在是什么

| 文件 | 行数 | 职责 |
| --- | ---: | --- |
| `chat/services/messaging.py` | 736 | **唯一**的消息写入事务实现（私聊/群聊/幂等/文件密钥/事件） |
| `chat/services/sync.py` | 77 | 同步游标签名与校验（HMAC-SHA256） |
| `chat/consumers.py` | 921 | WebSocket consumer：鉴权、在线状态、事件分发、序列化 |
| `chat/errors.py` | 108 | **错误码单一事实来源**（25 个码 + 协议版本常量） |
| `chat/models.py` | 844 | Conversation / ConversationEvent / EncryptedMessage / GroupMessage* / EncryptedFile* |
| `chat/views.py` | 4,703 | 120 个视图；发送/转发/文件发送视图调用同一服务层 |
| `chat/urls.py` | 161 | 约 70 条路由 |
| `chat/routing.py` | 7 | 唯一 WS 路由 `ws/chat/` |

**核心设计**：HTTP 和 WebSocket 两条发送路径都调用同一个 `messaging.send_private_message` / `send_group_message`。**业务代码里没有旁路写入**：`EncryptedMessage.objects.create` 只出现在 `messaging.py:534`，`GroupMessage.objects.create` 只在 `messaging.py:673`。

**唯一例外是演练脚本**：`deploy/seed_drill_data.py`（78/86/121/137 行）会直接 create，用于备份恢复演练造数。它不在请求路径上，但改模型字段时要一并改它，否则 `restore_drill.sh` 会红。

---

## 2. 最短读代码路径

1. `chat/errors.py` 全文（108 行，先建立错误码世界观）。
2. `chat/services/messaging.py` 的 `send_private_message`(458-590) 与 `send_group_message`(596-736)，对照读 `_private_digest_payload`(273-289) / `_private_stored_payload`(292-316)。
3. `chat/services/sync.py` 全文（77 行）。
4. `chat/consumers.py` 的 `receive_json`(150-323) 与 `connect/disconnect`(39-101)。
5. 需要时再看 `chat/views.py` 的 `conversation_sync_view`(3765-3882)。

跳过：`serialize_*` 系列（850-921）先只看签名；`avatar_url` / `display_name` / `initials` / `avatar_color` 是展示辅助，与协议无关。

---

## 3. 写入路径的确定顺序（改这里必须原样保持）

### 私聊 `send_private_message`

```
1  结构校验          validate_private_message(61-111)
2  预处理            normalize_pending_file_keys + incoming_digest (461-464)
3  事务 + 会话行锁   select_for_update, type=SINGLE, status=ACTIVE (466-475)
4  成员与拉黑        active 成员恰好 2 人；双向 BlockedUser 检查 (477-497)
5  接收者存活        is_active=False → receiver_not_found (499-502)
6  幂等重放          (sender_id, client_message_id) 命中 → 冲突或返回既有 (504-514)
7  新消息专属校验    validate_attached_file (516-526)
8  插入              savepoint + IntegrityError 后重查 (528-565)
9  created 副作用    文件密钥 → sync_sequence+=1 → ConversationEvent
                    → last_message → 接收者 unread+1 (567-588)
```

### 群聊 `send_group_message`

顺序与私聊一致，但**重放在成员版本检查之前**（R-02 修复），并且多两步：

```
成员上限 50 → group_too_large
membership_version 不匹配 → membership_conflict
recipients 集合 ≠ 活跃成员集合 → recipients_mismatch
```

### created vs replayed

`SendResult.created` 是唯一开关。`created=False` 时**零写**：不重写文件密钥、不加未读、不分配序号、不写事件、不改最后消息。调用方用 `result.created` 门控广播（`consumers.py:191-199`、`210-215`；`views.py` 六处）。

---

## 4. 幂等实现细节

- 字段名：`client_message_id`（`max_length=64`），**作用域是发送者全局**，不是会话内。同一 ID 用于另一会话 → 409 `idempotency_conflict`。
- 摘要：`json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False)` → UTF-8 → SHA-256 hex。
- `sender_copy` 归一化只保留 4 个字段，客户端多传的字段被丢弃（这是刻意的，避免客户端版本差异造成假冲突）。
- DB 兜底：条件唯一约束 `unique_client_private_message` / `unique_client_group_message`（`client_message_id IS NOT NULL`）。
- 并发同 ID：savepoint + `IntegrityError` 后重查。**该路径的测试全部 `skipUnless(connection.vendor == "postgresql")`，本地 SQLite 跑不到。**

---

## 5. 协议契约（不能改）

### 信封

| 方向 | 格式 |
| --- | --- |
| C→S | `{protocol_version?, event, request_id?, data}`；`protocol_version` 缺省按 `"1.0"` |
| S→C | `{protocol_version, event, request_id, sent_at, data}` |
| 错误 | `{protocol_version, event:"error", request_id, data:{code, message, retryable}}` |

版本不匹配 → `unsupported_protocol_version` + **close code 4003**。未登录 → **close code 4401**。

### 客户端 event 全列表

| event | 应答 | 说明 |
| --- | --- | --- |
| `connection.ping` | `connection.pong` | 心跳 |
| `message.single.send` | `message.single.accepted`（含 `created`）+ 给接收者 `message.single.new` | |
| `message.group.send` | `message.group.accepted` + 给每个收件人 `message.group.new` | |
| `message.receipt.update` | `message.receipt.updated` | 仅 delivered/read，单向 |
| `message.recall` | `message.recalled` | 仅发送者，30 分钟内 |
| `typing.start` / `typing.stop` | `typing.start.ack` / `typing.stop.ack` + 对端 `typing` | |
| 其它 | `not_implemented`（501） | |

### 服务端下行事件（全部 `group_send` 到 `user_{id}`）

`message.single.new`、`message.group.new`、`message.receipt.updated`、`message.recalled`、`message.deleted`、`typing`、`presence.updated`、`group.members.changed`、`group.invitation.new`、`key.verification.new`、`profile.updated`、`file.upload.completed`。

### 错误码

25 个，全部在 `chat/errors.py` 的 `REGISTRY`。**只有 `storage_unavailable` 与 `database_busy` 是 `retryable=true`**（都 503）。新增错误码必须同时进注册表，否则客户端无法按码分支。

---

## 6. 同步补取

- 序号：`Conversation.sync_sequence` 在发送事务内、持行锁时 `+=1`，随后写 `ConversationEvent(conversation, sequence, kind="message", ...)`。唯一约束 `(conversation, sequence)` 保证无洞无重复。
- 接口：`GET /api/conversations/<id>/sync/?cursor=&limit=`，`limit` 默认 100、clamp 1–200。
- 返回：`{items:[{sequence, kind, created_at, message}], next_cursor, has_more, high_water}`。
- 游标：`base64url(json) + "." + hmac_sha256_hex`，字段 `v/c/u/s/hw/t`，绑定会话 + 用户，TTL **7 天**。
- 分页：`next_cursor` **总是返回**；走完时必须把 `hw` 置 null 释放快照，下一次调用重新快照取增量。
- 可见性：复用历史读的投影（`cleared_at`、自动删除 cutoff、`UserMessageDeletion`、群 pre-join）；不可见事件被跳过但仍推进游标。

**限制（P0 如实声明）**：`ConversationEvent.Kind` 只有 `message`。撤回/删除/成员变更**不进事件流**，断线期间的这类变化 sync 补不回来。

---

## 7. 本地验证

```powershell
# 只跑消息相关
.\.venv\Scripts\python.exe manage.py test chat.tests.test_messaging_service chat.tests.test_t32_review chat.tests.test_sync_api chat.tests.test_protocol chat.tests.test_private_realtime chat.tests.test_group_realtime -v 2
# 期望：11 + 18 + 14 + 11 + 6 + 5 = 65 条
# 其中 PostgreSQL-only 的并发用例在 SQLite 上会被 skip

# 全量
.\.venv\Scripts\python.exe manage.py test --parallel 1     # 全量，条数见 testing-quality-ci.md

# 真并发验证必须上 PostgreSQL
$env:DATABASE_URL='postgres://ichat:ichat-ci@127.0.0.1:5432/ichat'
.\.venv\Scripts\python.exe manage.py test chat.tests.test_messaging_service
```

---

## 8. 已知坑（真实存在，不是猜测）

| # | 坑 | 位置 |
| --- | --- | --- |
| 1 | **存在读/序列化旁路**：HTTP 侧手写 payload 与 consumer 的 `serialize_*` 重复实现，字段可能漂移 | `views.py:4101-4184`、`group_messages_view:1972-2019`、`consumers.py:699-883` |
| 2 | **撤回逻辑两份**：HTTP `recall_message_view`(1063-1170) 与 `ChatConsumer._recall_*`(622-685) 各写一遍 | 改撤回要改两处 |
| 3 | **错误码旁路**：`recall_message_view` 用裸中文文案而非注册码；`file_unavailable` 在 views 返回 **410** 而注册表是 **409**；`message_type_mismatch`、`invalid_json`、`rate_limited` **未注册** | `views.py:1082/1129/3906/3912/3894` |
| 4 | **推送不一致**：HTTP 私聊发送只推接收者，不推发送者其它标签页；文件/转发路径却会推发送者 | `views.py:2249-2258` vs `4015-4018` |
| 5 | **服务端不主动踢线**：`heartbeat_interval_seconds=30` 只下发，服务端不计时不断开；断线检测全在客户端 | `consumers.py:36` |
| 6 | **presence 计数无 TTL**：`ws:connections:{user_id}` 永久键，进程崩溃会残留；cache 故障时 presence 不更新（注释声称会回落旧行为，实际不会） | `consumers.py:103-142` |
| 7 | **群成员上限 50 直接拒绝**，无分片方案 | `messaging.py:58, 619-622` |
| 8 | **私聊不校验 `membership_version`**，只有群聊校验 | |
| 9 | 校验后 `if client_message_id:` 恒真，对应 `if not client_message_id: raise` 分支不可达 | `messaging.py:505/631` vs `556/686` |
| 10 | 推送在服务提交后由调用方执行，推送失败不回滚已存消息（靠 replay/sync 恢复，属设计而非缺陷） | `messaging.py:18-19` |
| 11 | `window.localKeyVersion` 全仓无赋值，文件/转发消息的兜底 key_version 会退化为 `1` | `static/js/file-transfer.js`、`chat-message-actions.js` |
| 12 | `iChatFileTransfer._onUploadCompleted` 被 `chat.js` 调用但模块未导出，`file.upload.completed` 事件实际无效果 | `chat.js:1806` |

---

## 9. 这个模块的开放任务

| Issue | 内容 | 优先级 | 缺口 |
| --- | --- | --- | --- |
| [#164](https://github.com/HeanX/iChat_Pro/issues/164) | P4 T37 Windows—Android 跨端聊天 | P0 | 阻塞于 Android 端整体（见 [android-mobile.md](android-mobile.md)） |
| [#194](https://github.com/HeanX/iChat_Pro/issues/194) | P4 Test T13 HTTP/WS 集成测试 | P0 | 已有自动回归，缺"P0 接口 × 成功/异常/边界/越权"的用例索引与验收记录 |
| [#200](https://github.com/HeanX/iChat_Pro/issues/200) | P4 Test T19 弱网与故障测试 | P0 | 无延迟/丢包/断网/重启的实测证据 |

**建议的第一个真实改进**（不属于任何已开卡，但风险最高）：把 `views.py` 的读/序列化旁路收敛到 `consumers.serialize_*`，消除字段漂移。做这个之前先补一层"HTTP 与 WS 返回同一消息的 payload 必须逐字段相等"的对比测试。

---

## 10. 给下一个 Agent 的提示词

```text
你在 iChat_Pro 仓库负责【消息与协议核心模块】。

必读：docs/handoff/conventions.md、docs/handoff/messaging-protocol.md、
docs/phase4/protocol.md（ADR-P4-02）。
再读代码：chat/errors.py 全文 → chat/services/messaging.py 的两个 send_* →
chat/services/sync.py → chat/consumers.py 的 receive_json。

任务：【填写 Issue 号与标题】。

不可破坏的契约：
- 写入顺序：校验 → 行锁 → 权限 → 幂等重放 → 新消息校验 → savepoint 插入 → created 副作用。
- 群聊重放必须先于 membership_version 检查。
- created=False 必须零写、零广播。
- 错误码只能来自 chat/errors.py 的 REGISTRY，新增必须同时注册。
- 协议 event 名、close code 4003/4401 不得改动。

必须执行：
.\.venv\Scripts\python.exe manage.py test chat.tests.test_messaging_service chat.tests.test_t32_review chat.tests.test_sync_api chat.tests.test_protocol
.\.venv\Scripts\python.exe manage.py test --parallel 1
npm run test:e2ee
并发相关改动必须用真实 PostgreSQL 复跑（SQLite 会 skip 掉并发用例）。

完成前必须另开独立审查子 Agent 复核 diff（见 conventions.md §1）。
```
