# P4 Test T13 — HTTP/WebSocket 集成测试用例索引与验收记录（Issue #194）

> 执行日期：2026-10-18（Asia/Shanghai）。
> 基线：`cfe53be`（分支 `test/p4-test-t13-http-ws-integration`，worktree `.review/wt-t13`）。
> 环境：Windows / Python 3.13.12（仓库内 `.venv`）/ Django 6.0.5 / Channels 4.3.2 /
> 本地 SQLite + InMemoryChannelLayer。**不是** PostgreSQL/Redis，本机也没有。
> 交付物：`chat/tests/test_http_ws_integration.py`（64 条）。

---

## 1. 这张卡到底缺什么（缺口判断）

`docs/handoff/messaging-protocol.md` §9 与 `docs/phase4/tasks.md` 对 #194 的缺口描述一致：

> 已有接口/WS 自动回归；缺「P0 接口 × 成功/异常/边界/越权」的用例索引与验收记录。

开工前的实测盘点（命令见 §5）确认了这个判断，并定位到具体空白：

| 维度 | 开工前 | 本次补充 |
| --- | --- | --- |
| HTTP 私聊发送 | 有成功/replay/冲突 | 补 6 类畸形载荷、64 KiB 与 64 字符边界、拉黑、停用接收者、自发送、错会话类型、GET 带体、未登录 |
| HTTP 群聊发送 | **无**（`recipients_mismatch` / `group_muted` / `group_too_large` 三个码全仓 0 次命中） | 补 9 条，含 50/51 人上限边界 |
| sync 补取 | 有游标/分页/可见性 | 补 `limit` 非整型与 1/200 clamp 边界、非成员、非活跃会话 |
| 撤回 / 状态 / 删除 | 有部分 | 补 HTTP 撤回的时间窗边界与非成员越权、状态双端可见、删除只影响本人 |
| `message.recall`（WS 事件） | **0 次命中，完全无覆盖** | 补 4 条：成功、重复、超时、越权 |
| WebSocket 信封 | 有 ping/pong、4043、4401 | 补信封字段集合、非对象 JSON、未知事件 |
| 回执（WS） | 有成功路径 | 补越权（非接收者）、非法 status |
| typing | 有广播 | 补缺 `conversation_id`、跨会话越权 |
| 文件 API | 只有上传幂等与 key 校验 | 补会话创建边界、跨用户 chunk 越权、非持有人元数据、删除态、类型不匹配 |

**结论**：本卡不是"再写一遍已有测试"，而是把四类场景按接口补齐成**可索引**的用例集，
并逐条标注它对应哪一个 contract（`chat/errors.py` REGISTRY 或 ADR-P4-02）。

---

## 2. 用例索引（64 条）

方法名规则：`test_<id 小写>_...`，一个 id 对应恰好一个方法。
`files` 列之外无第二处定义——同一行为不在别的文件重复。

### 2.1 私聊发送 `POST /api/conversations/<id>/messages/send/`

| id | 类别 | 断言要点 | 方法 |
| --- | --- | --- | --- |
| HTTP-PRIV-01 | 成功 | 201、`created=true`、落库、接收者 unread+1 | `test_priv_01_success_persists_and_reports_created` |
| HTTP-PRIV-02 | 成功/幂等 | 重放 200、`created=false`、同 `message_id`、不新增行 | `test_priv_02_replay_returns_200_without_a_second_row` |
| HTTP-PRIV-03 | 异常 | 同 id 不同内容 → 409 `idempotency_conflict`，`retryable=false` | `test_priv_03_same_id_different_content_conflicts` |
| HTTP-PRIV-04 | 异常 | 6 个子用例 → 400 `invalid_payload` | `test_priv_04_malformed_payload_is_invalid_payload` |
| HTTP-PRIV-05 | 异常 | 非 AES-256-GCM → 400 `unsupported_algorithm` | `test_priv_05_unsupported_algorithm_is_rejected` |
| HTTP-PRIV-06 | 边界 | 密文 = 65536 字节整 → 接受 | `test_priv_06_ciphertext_at_the_limit_is_accepted` |
| HTTP-PRIV-07 | 边界 | 密文 65537 字节 → 400，且不落库 | `test_priv_07_ciphertext_over_the_limit_is_rejected` |
| HTTP-PRIV-08 | 边界 | `client_message_id` 64 字符 → 接受 | `test_priv_08_client_message_id_at_the_limit_is_accepted` |
| HTTP-PRIV-09 | 边界 | 65 字符 → 400 | `test_priv_09_client_message_id_over_the_limit_is_rejected` |
| HTTP-PRIV-10 | 越权 | 非成员 → 403 `conversation_forbidden`、零落库 | `test_priv_10_non_member_cannot_send` |
| HTTP-PRIV-11 | 越权 | 发给自己 → 403 | `test_priv_11_self_send_is_forbidden` |
| HTTP-PRIV-12 | 越权 | 接收者停用 → 404 `receiver_not_found` | `test_priv_12_deactivated_receiver_is_not_found` |
| HTTP-PRIV-13 | 越权 | 被对方拉黑 → 403 | `test_priv_13_blocked_pair_is_forbidden` |
| HTTP-PRIV-14 | 异常 | 对群会话用私聊端点 → 404 `conversation_not_found` | `test_priv_14_private_endpoint_rejects_a_group_conversation` |
| HTTP-PRIV-15 | 异常 | GET 带合法体 → 405 且不落库 | `test_priv_15_get_is_method_not_allowed` |
| HTTP-PRIV-16 | 越权 | 未登录 → 401 `authentication_required` 且是 JSON | `test_priv_16_unauthenticated_gets_401_json` |

### 2.2 群聊发送 `POST /api/conversations/<id>/messages/send-group/`

| id | 类别 | 断言要点 | 方法 |
| --- | --- | --- | --- |
| HTTP-GRP-01 | 成功 | 201、每人一份副本、副本密文互不相同 | `test_grp_01_success_stores_one_copy_per_member` |
| HTTP-GRP-02 | 异常 | 收件人少一个 → 409 `recipients_mismatch`、零落库 | `test_grp_02_recipients_mismatch_is_rejected` |
| HTTP-GRP-03 | 异常 | 重复 receiver → 400 `invalid_payload` | `test_grp_03_duplicate_receiver_is_invalid_payload` |
| HTTP-GRP-04 | 异常 | 成员版本过期 → 409 `membership_conflict` | `test_grp_04_stale_membership_version_conflicts` |
| HTTP-GRP-05 | 异常 | 禁言中普通成员 → 403 `group_muted` | `test_grp_05_muted_group_blocks_plain_members` |
| HTTP-GRP-06 | 成功/边界 | 禁言中群主仍可发（角色豁免） | `test_grp_06_muted_group_still_allows_the_owner` |
| HTTP-GRP-07 | 边界 | 恰好 50 人 → 201 | `test_grp_07_fifty_members_is_allowed` |
| HTTP-GRP-08 | 边界 | 51 人 → 400 `group_too_large`、零落库 | `test_grp_08_fifty_one_members_hits_the_cap` |
| HTTP-GRP-09 | 越权 | 非成员 → 403 `conversation_forbidden` | `test_grp_09_non_member_cannot_send` |

### 2.3 同步补取 `GET /api/conversations/<id>/sync/`

| id | 类别 | 断言要点 | 方法 |
| --- | --- | --- | --- |
| HTTP-SYNC-01 | 异常 | `limit=ten` → 400 `invalid_payload` | `test_sync_01_non_integer_limit_is_invalid_payload` |
| HTTP-SYNC-02 | 边界 | `limit=0` → clamp 到 1 | `test_sync_02_limit_below_range_clamps_to_one` |
| HTTP-SYNC-03 | 边界 | `limit=999` → 不报错，仍按 200 上限 | `test_sync_03_limit_above_range_clamps_to_two_hundred` |
| HTTP-SYNC-04 | 异常 | 篡改签名 → 400 `sync_cursor_invalid` | `test_sync_04_tampered_cursor_is_rejected` |
| HTTP-SYNC-05 | 越权 | 非成员 → 404 `conversation_not_found` | `test_sync_05_non_member_is_not_found` |
| HTTP-SYNC-06 | 异常 | 会话非 active → 404 | `test_sync_06_inactive_conversation_is_not_found` |

### 2.4 撤回 / 状态 / 删除（HTTP）

| id | 类别 | 断言要点 | 方法 |
| --- | --- | --- | --- |
| HTTP-RCL-01 | 成功 | 发送者 30 分钟内撤回 → `recalled` + `recalled_at` | `test_rcl_01_sender_recalls_within_the_window` |
| HTTP-RCL-02 | 异常 | 重复撤回 → 409 | `test_rcl_02_already_recalled_is_a_conflict` |
| HTTP-RCL-03 | 边界 | 31 分钟后 → 400（**已知偏差**，见 §4） | `test_rcl_03_recall_after_the_window_fails` |
| HTTP-RCL-04 | 越权 | 非发送者 → 403，消息状态不变 | `test_rcl_04_non_sender_cannot_recall` |
| HTTP-RCL-05 | 越权 | 非成员 → 404 | `test_rcl_05_non_member_cannot_recall` |
| HTTP-MSG-01 | 成功 | 双方都能查状态 | `test_msg_01_status_is_visible_to_both_participants` |
| HTTP-DEL-01 | 成功/越权 | 个人删除只影响本人视图，底行仍在 | `test_del_01_delete_is_per_user` |

### 2.5 文件 API

| id | 类别 | 断言要点 | 方法 |
| --- | --- | --- | --- |
| HTTP-FILE-01 | 异常 | 4 个必填字段缺失/非法 → 400 `invalid_file_metadata` | `test_file_01_missing_metadata_is_rejected` |
| HTTP-FILE-02 | 边界 | 超每类大小上限 → 400 `file_too_large` | `test_file_02_over_the_size_limit_is_rejected` |
| HTTP-FILE-03 | 越权 | 非成员开会话 → 403、无记录 | `test_file_03_non_member_cannot_open_an_upload_session` |
| HTTP-FILE-04 | 越权 | 往别人的会话上传 → 403 | `test_file_04_upload_into_a_foreign_conversation_is_forbidden` |
| HTTP-FILE-05 | 异常 | 不存在的 `upload_id` → 403 `upload_forbidden` | `test_file_05_chunk_on_an_unknown_upload_is_forbidden` |
| HTTP-FILE-06 | 越权 | 别人的会话上传分片 → 403 | `test_file_06_foreign_chunk_upload_is_forbidden` |
| HTTP-FILE-07 | 越权 | 无密钥的第三方读元数据 → 403 `file_forbidden` | `test_file_07_non_holder_cannot_read_metadata` |
| HTTP-FILE-08 | 异常 | 已删除文件 → 410 `file_unavailable`（**已知偏差**） | `test_file_08_deleted_file_is_unavailable` |
| HTTP-FILE-09 | 异常 | `message_kind` 与 `message_type` 不符 → 400 `message_type_mismatch` | `test_file_09_send_message_type_mismatch` |
| HTTP-FILE-10 | 异常 | 不存在的 file_id → 404 `file_not_found` | `test_file_10_send_file_message_rejects_unknown_file` |
| HTTP-FILE-11 | 回归锚点 | 同时钉住注册表 409 与视图 410 | `test_file_unavailable_status_mismatch_is_still_open` |

### 2.6 WebSocket

通过 `ichat_pro.asgi.application` + `WebsocketCommunicator` 走真实 consumer（含
`AllowedHostsOriginValidator` 与 `AuthMiddlewareStack`），session 头在同步的
`setUp` 里预先生成（`force_login` 不能在 async 体里跑）。

| id | 类别 | 断言要点 | 方法 |
| --- | --- | --- | --- |
| WS-ENV-01 | 成功 | 信封字段集合恰为 5 个、`protocol_version=1.0` | `test_env_01_envelope_shape` |
| WS-ENV-03 | 异常 | 未知事件 → `not_implemented`、`retryable=false` | `test_env_03_unknown_event_is_not_implemented` |
| WS-ENV-04 | 异常 | 非对象 JSON → `invalid_payload`、`request_id=null` | `test_env_04_non_object_json_is_invalid_payload` |
| WS-ENV-05 | 越权 | 匿名连接 → close **4401** | `test_env_05_anonymous_connect_closes_with_4401` |
| WS-ENV-06 | 异常 | 版本 9.9 → error 后 close **4003** | `test_env_06_unsupported_version_reports_then_closes_4003` |
| WS-RCP-01 | 越权 | 非接收者发回执 → `message_not_found` | `test_rcp_01_receipt_for_a_foreign_message_is_not_found` |
| WS-RCP-02 | 异常 | `status=exploded` → `invalid_payload` | `test_rcp_02_invalid_status_is_rejected` |
| WS-RCP-03 | 成功 | read 回执清零 unread 且通知发送者 | `test_rcp_03_read_receipt_clears_unread_and_never_rebumps` |
| WS-RCL-01 | 成功 | 发送者撤回 → `message.recalled` + 落库 `recalled` | `test_rcl_01_sender_recalls_over_websocket` |
| WS-RCL-02 | 异常 | 二次撤回 → `already_recalled` | `test_rcl_02_second_recall_reports_already_recalled` |
| WS-RCL-03 | 边界 | 31 分钟后 → `recall_timeout` | `test_rcl_03_recall_after_the_window_reports_recall_timeout` |
| WS-RCL-04 | 越权 | 非发送者撤回 → `message_not_found`，状态不变 | `test_rcl_04_non_sender_cannot_recall` |
| WS-TYP-01 | 成功 | start/stop 各有 ack，对端收到 `typing` | `test_typ_01_typing_acks_and_reaches_the_peer` |
| WS-TYP-02 | 异常 | 缺 `conversation_id` → `invalid_payload` | `test_typ_02_typing_without_conversation_id_is_invalid_payload` |
| WS-TYP-03 | 越权 | 跨会话 typing → `conversation_forbidden` | `test_typ_03_typing_in_a_foreign_conversation_is_forbidden` |

---

## 3. 覆盖统计（按类别）

| 类别 | 条数 |
| --- | ---: |
| 成功（happy path，含成功/幂等、成功/边界、成功/越权三个复合项） | 11 |
| 异常（注册错误码 / 错误方法 / 畸形输入） | 24 |
| 边界（长度、大小、上限、clamp、时间窗） | 11 |
| 越权（他人 id、非成员、停用、拉黑、跨会话） | 17 |
| 回归锚点（已知偏差） | 1 |
| **合计** | **64** |

错误码覆盖：`chat/errors.py` 的 `REGISTRY` 共 **25** 个码，本文件直接断言了其中 **21** 个：
`invalid_payload`、`unsupported_algorithm`、`invalid_file_metadata`、
`unsupported_protocol_version`、`not_implemented`、`authentication_required`、
`conversation_not_found`、`receiver_not_found`、`conversation_forbidden`、
`membership_conflict`、`recipients_mismatch`、`group_muted`、`group_too_large`、
`message_not_found`、`already_recalled`、`recall_timeout`、`idempotency_conflict`、
`sync_cursor_invalid`、`file_not_found`、`file_forbidden`、`file_unavailable`。

未覆盖的 4 个及其原因：

| 未覆盖码 | 原因 |
| --- | --- |
| `storage_unavailable` / `database_busy` | 需要注入 `OperationalError` 才能触发，属故障注入（#200 范围）；既有 `test_protocol.py` 只验注册表断言，未验视图路径 |
| `sync_cursor_expired` | 既有 `test_sync_api.py` 用 mock 时间覆盖，本卡不重复 |
| `file_type_mismatch` | 需要先有 kind 与 type 同时合法但内容不符的文件，属数据模块边界 |

复算命令（本节的数字都能由它重算）：

```powershell
.\.venv\Scripts\python.exe -c "import re,pathlib; s=pathlib.Path('chat/tests/test_http_ws_integration.py').read_text(encoding='utf-8'); print(len(re.findall(r'^    def (test_\w+)', s, re.M)))"
```

---

## 4. 已知偏差（如实记录，不在本卡修复）

这些是 `docs/handoff/messaging-protocol.md` §8「已知坑」里已经写下的既有缺陷。
本卡**只做测试与记录**，不改业务代码，因此测试把现状钉住并在注释里标 `KNOWN DEVIATION`：

| # | 现象 | 位置 | 本卡处理 |
| --- | --- | --- | --- |
| 1 | `file_unavailable` 注册表是 409，文件视图返回 410 | `chat/views.py` 4 处 | `HTTP-FILE-08` + `HTTP-FILE-11` 同时钉住两个值，任一侧改动都会红 |
| 2 | HTTP 撤回超时返回裸中文文案，不是注册码 `recall_timeout` | `recall_message_view` | `HTTP-RCL-03` 断言文案；WS 侧 `WS-RCL-03` 断言正确码，两者对照可见不一致 |
| 3 | `message_type_mismatch`、`invalid_json`、`rate_limited` 未进注册表 | `chat/errors.py` | `HTTP-FILE-09` 断言它当前的值，未声称其合规 |
| 4 | `chat/tests/test_core.py` 1096-1307 行是重复粘贴区 | — | 本卡未改动该文件，仍收集 80 条 |

> 修复上述 1–3 需要改 `chat/views.py` 与 `chat/errors.py`，属于消息模块作者范围，
> 本卡不并行重构（见 `conventions.md` §5「共享文件最终合并必须串行」）。

---

## 5. 实际执行的命令与原始结果

```powershell
# 解释器核对
.\.venv\Scripts\python.exe --version            # Python 3.13.12
.\.venv\Scripts\python.exe -c "import django; print(django.get_version())"   # 6.0.5
.\.venv\Scripts\python.exe -c "import channels; print(channels.__version__)" # 4.3.2

# 本卡交付物
.\.venv\Scripts\python.exe manage.py test chat.tests.test_http_ws_integration
# → Found 64 test(s). / Ran 64 tests in 39.209s / OK

# 模块回归（不受本卡影响，确认没有连带破坏）
.\.venv\Scripts\python.exe manage.py test chat.tests.test_protocol chat.tests.test_private_realtime chat.tests.test_group_realtime
# → OK

# 变异测试（证明用例真的会红）
.\.venv\Scripts\python.exe .review/mutation_probe.py
```

变异测试结果（每个变异体注入后跑全套，再按字节还原并校验 SHA-256）：

| 变异体 | 注入的缺陷 | 结果 |
| --- | --- | --- |
| M2 | 群成员上限放宽 10 | **CAUGHT** (`FAILED failures=1`) |
| M3 | 关闭 recipients 一致性检查 | **CAUGHT** |
| M4 | 去掉 sync `limit` clamp | **CAUGHT** |
| M5 | 去掉二次撤回防护 | **CAUGHT** |
| M6 | 去掉匿名连接拦截 | **CAUGHT** (`errors=1`) |
| M7 | 去掉 chunk 上传归属校验 | **CAUGHT** |
| M8 | 去掉拉黑检查 | **CAUGHT** |
| M1 | 跳过"乐观重放"分支 | **SURVIVED → 等价变异体** |

M1 已单独用 `.review/probe_equivalent.py` 判定：跳过乐观重放分支后，重放请求落到
`IntegrityError` + savepoint 恢复路径，`PrivateSendHttpTests` 与全套仍为 `OK`。
**这是服务层的双保险（`messaging.py` 注释明确写了「concurrent same-ID inserts are
covered via IntegrityError」），不是覆盖漏洞**，因此不为此写假测试。

---

## 6. 未覆盖 / 未验证的范围（明确声明）

- **PostgreSQL 并发用例未执行**：本机没有 PG，`@skipUnless(connection.vendor == "postgresql")`
  的并发用例在 SQLite 上静默 skip。本卡**没有新增**并发用例，也没有在任何地方声称并发通过。
- **Redis 多进程语义未验证**：本地是 InMemoryChannelLayer。`presence` 计数跨 Daphne
  worker 的行为本卡未验（属 T14/T13 的服务器侧范围）。
- **真实网络弱网/断线**：不在本卡范围（属 #200 P4 Test T19）。
- **`storage_unavailable` / `database_busy` 的 HTTP 与 WS 路径**：未注入 `OperationalError`，
  仍由既有 `test_protocol.py` 间接覆盖，本卡未新增证据。
- **`file_type_mismatch`**：未构造（需先有 kind/type 一致但内容不符的文件，属数据模块）。
- **跨端（Windows/Android）**：完全没有。本卡全是本机自动化，不能当作安装版或跨端验收。
- **CI**：本卡未提交 PR，未产生 CI run。上面的数字全部来自本地单次执行。

---

## 7. 复跑方式

```powershell
# 在仓库根目录（或本卡 worktree）
.\.venv\Scripts\python.exe manage.py test chat.tests.test_http_ws_integration -v 2

# 只看某一类
.\.venv\Scripts\python.exe manage.py test chat.tests.test_http_ws_integration.WsRecallTests
.\.venv\Scripts\python.exe manage.py test chat.tests.test_http_ws_integration.PrivateSendHttpTests
```

期望：`Ran 64 tests` + `OK`，耗时约 35–45s（WebSocket 用例占大头）。
