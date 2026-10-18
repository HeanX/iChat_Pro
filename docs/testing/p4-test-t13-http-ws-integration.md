# P4 Test T13 — HTTP/WebSocket 集成测试用例索引与验收记录（Issue #194）

> 执行日期：2026-10-18（Asia/Shanghai）。
> 基线：`cfe53be`；被验证的提交：`2e09122`（第 3 轮审查对象）与整改后的最新 HEAD。
> 分支 `test/p4-test-t13-http-ws-integration`（worktree `.review/wt-t13`）。
> 环境：Windows / Python 3.13.12（仓库内 `.venv`）/ Django 6.0.5 / Channels 4.3.2 /
> 本地 SQLite + InMemoryChannelLayer。**不是** PostgreSQL/Redis，本机也没有。
> 交付物：`chat/tests/test_http_ws_integration.py`（79 条）。

---

## 1. 这张卡到底缺什么（缺口判断）

`docs/handoff/messaging-protocol.md` §9 与 `docs/phase4/tasks.md` 对 #194 的缺口描述一致：

> 已有接口/WS 自动回归；缺「P0 接口 × 成功/异常/边界/越权」的用例索引与验收记录。

开工前的实测盘点（命令见 §5）确认了这个判断，并定位到具体空白：

| 维度 | 开工前 | 本次补充 |
| --- | --- | --- |
| HTTP 私聊发送 | 有成功/replay/冲突 | 补 6 类畸形载荷、64 KiB 与 64 字符边界、拉黑、停用接收者、自发送、错会话类型、GET 带体、未登录 |
| HTTP 群聊发送 | 有偶发调用（`test_messaging_service`/`test_sync_api`/`test_t32_review` 共 4 处 POST `send-group`，都是为幂等/事件/文件主题顺带发一条），但 `recipients_mismatch` / `group_muted` / `group_too_large` 三个码全仓 **0 次命中** | 补 10 条，含 50/51 人上限边界与群聊重放零写 |
| sync 补取 | 有游标/分页/可见性 | 补 `limit` 非整型与 1/200 clamp 边界、非成员、非活跃会话 |
| 撤回 / 状态 / 删除（私聊分支） | 有部分 | 补 HTTP 撤回的时间窗边界与非成员越权、状态双端可见、删除只影响本人 |
| 撤回 / 状态 / 删除（**群聊分支**） | **0 条**：第 3 轮审查实测把三个视图的 `else:` 群聊分支整体改成不可达，全量 470 条仍全绿 | 补 8 条，每条都以「群聊分支被删即红」为设计目标 |
| `message.recall`（WS 事件，single） | **0 次命中，完全无覆盖** | 补 4 条：成功、重复、超时、越权 |
| `message.recall`（WS 事件，group） | **0 条** | 补 3 条：发送者撤回、非发送者被拒、撤回帧到达其他成员 |
| WebSocket 信封 | 有 ping/pong 与 close **4003**（`test_protocol.py`，但那条只用 `assertRaises(Exception)` 等超时，从未读 close code）、close **4401**（`test_core.py:994`） | 补信封字段集合、非对象 JSON、未知事件；4003 改为读 `websocket.close` 帧断言 |
| 回执（WS） | 有成功路径 | 补越权（非接收者）、非法 status |
| typing | 有广播 | 补缺 `conversation_id`、跨会话越权 |
| 文件 API | 只有上传幂等与 key 校验 | 补会话创建边界、跨用户 chunk 越权、`file_forbidden` 的两道守卫、删除态、两种类型不匹配码 |

**结论**：本卡不是"再写一遍已有测试"，而是把四类场景按接口补齐成**可索引**的用例集，
并逐条标注它对应哪一个 contract（`chat/errors.py` REGISTRY 或 ADR-P4-02）。

---

## 2. 用例索引（79 条）

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
| HTTP-GRP-10 | 成功/幂等 | 群聊重放零写：行数、副本数、未读、`sync_sequence`、事件数都不变 | `test_grp_10_replay_is_a_zero_write_operation` |

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

### 2.4b 群聊分支：撤回 / 状态 / 删除（HTTP）

这三个端点都按 `conversation.type` 分支。第 3 轮审查实测：把三个视图的群聊
`else:` 分支整体改成不可达（`elif False:`），**整改当时的全量 470 条仍然全绿**——
也就是说整改前的 66 条里没有任何一条守卫群聊分支（历史实验陈述；
当前全量为 483 条，含本轮新增的 13 条群聊用例）。下面 8 条每条都以
「群聊分支被删即红」为设计目标（变异验证 GRP-RECALL / GRP-STATUS / GRP-DELETE，
已复测全部 CAUGHT；第 4 轮审查者用同样的变异独立复验，同样全部 CAUGHT）。

| id | 类别 | 断言要点 | 方法 |
| --- | --- | --- | --- |
| HTTP-GOPS-01 | 成功 | 群主撤回群消息，全部副本状态翻成 `recalled`（群聊独有的级联副作用） | `test_gops_01_owner_can_recall_a_group_message` |
| HTTP-GOPS-02 | 异常 | 重复撤回 → 409 | `test_gops_02_second_group_recall_is_a_conflict` |
| HTTP-GOPS-03 | 越权 | 非发送者 → 403，`GroupMessage.status` 不变 | `test_gops_03_group_recall_by_non_sender_is_forbidden` |
| HTTP-GOPS-04 | 边界 | 31 分钟后 → 400 | `test_gops_04_group_recall_after_the_window_fails` |
| HTTP-GOPS-05 | 成功 | status 返回 `conversation_type=group` 且列出每个接收者及其状态 | `test_gops_05_group_status_lists_every_recipient` |
| HTTP-GOPS-06 | 成功 | 普通成员也能查群消息状态 | `test_gops_06_group_status_is_visible_to_a_plain_member` |
| HTTP-GOPS-07 | 成功/越权 | 删除记录为 GROUP 类型，共享行仍在 | `test_gops_07_group_delete_is_recorded_as_a_group_deletion` |
| HTTP-GOPS-08 | 异常 | 消息 id 属于另一个群 → 404 | `test_gops_08_group_message_not_found_in_this_conversation` |

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
| HTTP-FILE-11 | 异常 | 统一服务层路径 → 400 **已注册** `file_type_mismatch` | `test_file_11_registered_file_type_mismatch_is_reachable` |
| HTTP-FILE-12 | 越权 | `file_forbidden` 两道守卫都断言：共享 helper（非持有人）+ metadata 视图自己的 key 检查（无 key 的 owner 也被拒） | `test_file_12_non_holder_cannot_read_metadata_without_a_key` |
| HTTP-FILE-12b | 越权 | 共享 helper 的守卫经**下载**路径再钉一次（删除该守卫即红） | `test_file_12b_shared_helper_rejects_a_keyless_member_for_download` |
| HTTP-FILE-13 | 回归锚点 | 同时钉住注册表 409 与视图 410 | `test_file_unavailable_status_mismatch_is_still_open` |

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
| WS-GOPS-01 | 成功 | 发送者经 WS 撤回群消息，全部副本翻成 `recalled` | `test_gops_ws_01_recall_group_message_over_websocket` |
| WS-GOPS-02 | 越权 | 非发送者撤回群消息 → `message_not_found` | `test_gops_ws_02_group_recall_by_non_sender_reports_not_found` |
| WS-GOPS-03 | 成功 | 撤回帧到达其他成员（`conversation_type=group` 的 group_send 分支） | `test_gops_ws_03_group_recall_reaches_other_members` |

---

## 3. 覆盖统计（按类别）

| 类别 | 条数 |
| --- | ---: |
| 成功（含成功/幂等、成功/边界、成功/越权等复合项） | 18 |
| 异常（注册错误码 / 错误方法 / 畸形输入） | 27 |
| 边界（长度、大小、上限、clamp、时间窗） | 12 |
| 越权（他人 id、非成员、停用、拉黑、跨会话） | 21 |
| 回归锚点（已知偏差） | 1 |
| **合计** | **79** |

> 口径说明（与 §2 索引表逐行一致，可用下方命令复算）：
> ① 复合标注（如「成功/幂等」「成功/越权」共 5 条）按**首类**计入；
> ② `HTTP-FILE-02` 用约 0.91 TiB 触发 `file_too_large`，属「远超上限」而非边界值本身
> （上限是 100 MiB），因此按「边界」标注但**不当作边界覆盖证据**引用。

错误码覆盖：`chat/errors.py` 的 `REGISTRY` 共 **25** 个码，本文件直接断言了其中 **22** 个
（第 2 轮审查后新增 `file_type_mismatch`）：
`invalid_payload`、`unsupported_algorithm`、`invalid_file_metadata`、
`unsupported_protocol_version`、`not_implemented`、`authentication_required`、
`conversation_not_found`、`receiver_not_found`、`conversation_forbidden`、
`membership_conflict`、`recipients_mismatch`、`group_muted`、`group_too_large`、
`message_not_found`、`already_recalled`、`recall_timeout`、`idempotency_conflict`、
`sync_cursor_invalid`、`file_not_found`、`file_forbidden`、`file_unavailable`、
`file_type_mismatch`。

未覆盖的 3 个及其原因：

| 未覆盖码 | 原因 |
| --- | --- |
| `storage_unavailable` / `database_busy` | 需要注入 `OperationalError` 才能触发，属故障注入（#200 范围）；既有 `test_protocol.py` 只验注册表断言，未验视图路径 |
| `sync_cursor_expired` | 既有 `test_sync_api.py` 用 mock 时间覆盖，本卡不重复 |

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
| 1 | `file_unavailable` 注册表是 409，文件视图返回 410 | `chat/views.py` 6 处（823 / 3315 / 3906 / 4199 / 4257 / 4286） | `HTTP-FILE-08` + `HTTP-FILE-13` 同时钉住两个值，任一侧改动都会红 |
| 2 | HTTP 撤回超时返回裸中文文案，不是注册码 `recall_timeout` | `recall_message_view` | `HTTP-RCL-03` 断言文案；WS 侧 `WS-RCL-03` 断言正确码，两者对照可见不一致 |
| 3 | `message_type_mismatch`、`invalid_json`、`rate_limited` 未进注册表 | `chat/errors.py` | `HTTP-FILE-09` 断言它当前的值，未声称其合规 |
| 4 | `file_too_large`、`upload_forbidden` 同样未进注册表 | `chat/views.py:3379`、`3507` | `HTTP-FILE-02` / `HTTP-FILE-05` / `HTTP-FILE-06` 断言它们当前的值，未声称其合规 |
| 5 | `chat/tests/test_core.py` 1096-1307 行是重复粘贴区 | — | 本卡未改动该文件，仍收集 80 条 |

> 修复上述 1–4 需要改 `chat/views.py` 与 `chat/errors.py`，属于消息模块作者范围，
> 本卡不并行重构（见 `conventions.md` §5「共享文件最终合并必须串行」）。

本文件直接断言、但**不在** `REGISTRY` 中的码共 3 个：
`message_type_mismatch`、`file_too_large`、`upload_forbidden`。
它们出现在测试里是**记录现状**（客户端目前只能按字面值分支），
不构成对 `protocol.md` §2「错误码只能来自注册表」这一契约的认可。
复算命令：

```powershell
.\.venv\Scripts\python.exe -c "import re,pathlib; from chat.errors import REGISTRY; s=pathlib.Path('chat/tests/test_http_ws_integration.py').read_text(encoding='utf-8'); print(sorted({c for c in ['message_type_mismatch','file_too_large','upload_forbidden'] if c in s and c not in REGISTRY}))"
```

---

## 5. 实际执行的命令与原始结果

```powershell
# 解释器核对
.\.venv\Scripts\python.exe --version            # Python 3.13.12
.\.venv\Scripts\python.exe -c "import django; print(django.get_version())"   # 6.0.5
.\.venv\Scripts\python.exe -c "import channels; print(channels.__version__)" # 4.3.2

# 本卡交付物（跑多次，耗时在 37–41s 之间波动）
.\.venv\Scripts\python.exe manage.py test chat.tests.test_http_ws_integration
# → Found 79 test(s). / Ran 79 tests / OK

# 全量（含本卡 79 条）
.\.venv\Scripts\python.exe manage.py test --parallel 1
# → Found 483 test(s). / Ran 483 tests / OK (skipped=2)

# 模块回归（messaging 相关，含本卡）
.\.venv\Scripts\python.exe manage.py test chat.tests.test_http_ws_integration chat.tests.test_protocol chat.tests.test_private_realtime chat.tests.test_group_realtime chat.tests.test_messaging_service chat.tests.test_t32_review chat.tests.test_sync_api
# → Found 144 test(s). / Ran 144 tests / OK (skipped=2)

# 前端链（未改动前端，确认没有连带破坏）
npm run test:e2ee
# → tests 62 / pass 62 / fail 0

# 变异测试（证明用例真的会红）——脚本已入库，见 chat/tests/t13_mutation_probe.py
.\.venv\Scripts\python.exe chat/tests/t13_mutation_probe.py
# → All 11 mutations caught; every file restored byte-identically.
```

`skipped=2` 是 PostgreSQL-only 的并发用例（本地 SQLite 无 PG），**不计入通过**，
也未在任何地方宣称并发已验证。

变异测试结果（每个变异体注入后跑目标用例，再按字节还原并校验 SHA-256）。
**下表是第二轮（审查整改后）的最终结果**；第一轮的粗略结论已被它取代：

| 变异体 | 注入的缺陷 | 结果 |
| --- | --- | --- |
| M1 | 群成员上限 50→60 | **CAUGHT**（`test_grp_08`） |
| M3 | 关闭 recipients 一致性检查 | **CAUGHT**（`test_grp_02`） |
| M4 | sync `limit` **整行**去掉（含下界） | **CAUGHT**（`test_sync_02` + `test_sync_03`） |
| M4b | sync **只去上界** `max(1, limit)` | **CAUGHT**（`test_sync_03`，整改后） |
| M4c | sync 上界放宽 200→500 | **CAUGHT**（`test_sync_03`，整改后） |
| M5 | 去掉二次撤回防护 | **CAUGHT**（`test_rcl_02`） |
| M6 | 匿名连接拦截删除 | **CAUGHT**（`test_env_05`） |
| M7 | 去掉 chunk 上传归属校验 | **CAUGHT**（`test_file_06`） |
| M8 | 去掉拉黑检查 | **CAUGHT**（`test_priv_13`） |
| B1 | 4003 改成 4400 | **CAUGHT**（`test_env_06`，整改后） |
| B1b | 删除 4003 的 close 调用 | **CAUGHT**（`test_env_06`，整改后） |

实测输出：`All 11 mutations caught; every file restored byte-identically.`

> **M4 的口径更正**：第一轮只跑了"整行去掉"，因此把上界 clamp 误记为已覆盖。
> 独立审查复跑后发现**只去上界时用例仍绿**，说明 `test_sync_03` 原来只断言
> `len(items)==3`，而 fixture 只有 3 条事件，任何 ≥3 的上限都满足。
> 整改后该用例自建 205 条事件的会话，断言首页恰为 200 条且 `has_more=true`，
> M4b/M4c 均能被抓到。同理，`test_env_06` 原来用
> `assertRaises(Exception)` 等超时，**从未读取 close code**，4003 改 4400 也能过；
> 整改后改为读 `websocket.close` 帧并断言 `code == 4003`。

M0（跳过"乐观重放"分支）单独判定为**等价变异体**：跳过该分支后，重放请求落到
`IntegrityError` + savepoint 恢复路径，`PrivateSendHttpTests` 与全套仍为 `OK`。
这是服务层的双保险（`messaging.py` 注释明确写了「concurrent same-ID inserts are
covered via IntegrityError」），**不是覆盖漏洞**，因此不为此写假测试，也不列入上表。

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

期望：`Ran 79 tests` + `OK`，耗时约 32–40s（WebSocket 与 205 事件用例占大头）。

---

## 8. 独立审查与整改记录

按 `conventions.md` §1（仓库根 `docs/handoff/` 在本分支不存在，流程以其工作区副本为准）
的强制要求，作者写完后另开**全新上下文**的子 Agent 审查，只给它仓库路径、分支、
`gh issue view 194` 原文、`.review/pending.diff` 与必跑命令，不给任何实现说明。
每次整改后**再开一个新的**（不复用）。

| 轮次 | 结论 | 主要发现 | 处理 |
| --- | --- | --- | --- |
| 1 | PASS WITH FINDINGS | BLOCKER 1（close code 未真断言）、MAJOR 2（sync 200 上界未覆盖、变异表口径不实）、MINOR 4 | 全部整改 |
| 2 | PASS WITH FINDINGS | MAJOR 1（`file_type_mismatch` 未覆盖的理由写错）、MINOR 9（含 2 处文档事实错误、1 处自相矛盾） | 全部整改 |
| 3 | **FAIL** | BLOCKER 3（**群聊 recall/status/delete 三个分支与 WS 群聊撤回可整体删除仍全绿**；第三轮记录预设结论）、MAJOR 6、MINOR 3 | 全部整改，见「第 3 轮」 |
| 4 | **PASS WITH FINDINGS** | BLOCKER 0、MAJOR 0、MINOR 3（环境/信息性） | 无需改代码；记录见「第 4 轮」 |

### 第 1 轮

**BLOCKER（已修）** `test_env_06` 用 `assertRaises(Exception)` 等超时，**从未读取 close code**。
把 `consumers.py` 的 4003 改成 4400、或整行删掉 `await self.close(...)`，用例都**仍然绿**。
整改：改为 `receive_output()` 读取真实 `websocket.close` 帧并断言
`code == UNSUPPORTED_PROTOCOL_CLOSE_CODE == 4003`。整改后 B1/B1b 两个变异体均 **CAUGHT**。

> 公平记录：这个弱写法在既有 `chat/tests/test_protocol.py:132` 里也有。
> 本卡只修自己新增的那一条，**未改动** `test_protocol.py`（避免扩大本卡边界），
> 但已在下面「遗留」中记录，建议消息模块作者单独修。

**MAJOR（已修）**

1. `test_sync_03` 自称验证 200 上限，实际只断言 `len(items)==3`，而 fixture 只有 3 条事件。
   整改：该用例自建 205 条事件的会话，断言首页恰 200 条、`has_more=true`、`high_water=205`，
   第二页取回 201–205 且 `has_more=false`。M4b/M4c 均 **CAUGHT**。
2. §5 变异表曾把 M4 记成「去掉 sync clamp → CAUGHT」，掩盖了上界未覆盖的事实。
   整改：拆成 M4 / M4b / M4c 三行并补写「口径更正」。

**MINOR（已处理）**：补齐漏记的未注册码（`file_too_large`、`upload_forbidden`）；
变异脚本入库为 `chat/tests/t13_mutation_probe.py`；`test_priv_03` 的恒真断言改为断言注册表契约。

### 第 2 轮

**MAJOR（已修）** 本文曾写 `file_type_mismatch`「需要特殊数据 fixture，覆盖不了」，这个理由**不成立**。
实测表明存在**两条不同的检查、返回两个不同的码**：

| 端点 | 触发 | 返回 |
| --- | --- | --- |
| `POST /api/files/<id>/messages/`（`send_file_message_view`） | `message_type` 与上传的 `message_kind` 不符 | 400 **`message_type_mismatch`**（未注册） |
| `POST /api/conversations/<id>/messages/send/` 带 `file_id`（统一服务 `validate_attached_file`） | 同上 | 400 **`file_type_mismatch`**（已注册） |

整改：新增 `HTTP-FILE-11` 专门走统一服务层拿到**已注册**的码
（变异验证 R2-1：删掉该校验后用例红），既有的 `HTTP-FILE-09` 继续记录未注册的那个。

**MINOR（已处理）**

1. 本文「WebSocket 信封：有 ping/pong、4043、4401」中的 **4043 是笔误**，基线全仓无此码
   （只有 close 4003 与 4401），已改正。
2. 「HTTP 群聊发送：**无**」不成立——基线有 4 处 POST `send-group`
   （`test_messaging_service:296`、`test_sync_api:228/429`、`test_t32_review:547`），
   只是都为别的主题顺带发送。已改为如实描述。
3. `test_priv_02` 只断言行数，不查 unread / sync 序号 / 事件数。已补齐三项
   （变异验证 R2-3：让重放再计未读后用例红）。
4. `test_file_02` 用 10^12 字节（约 0.91 TiB），属「远超」而非边界。已在 §3 的分类口径中注明。
5. `test_rcl_04` 在 HTTP 与 WS 两个类里同名，按裸方法名无法区分。索引表已按类名限定。
6. `file_forbidden` 有两道守卫，原先只覆盖一道。已补 `HTTP-FILE-12`
   （变异验证 R2-2：删掉 metadata 视图自己的 key 守卫后用例红）。
7. 本节原文自相矛盾（「尚未重新开新审查者」却又称「已另开」）。已改为上表的轮次记录，
   未发生的事不再写成已发生。
8. 新文档引用 `docs/handoff/*`，而该目录在本分支不存在（只存在于主工作区磁盘）。
   §1/§8 的引用已改为不依赖该路径（本节开头注明了例外及原因）。
9. `t13_mutation_probe.py` 的 `subprocess.run` 无 `timeout`，卡死时 `finally` 仍会执行还原，
   但可能长时间挂起。已加 `timeout=900`。

### 第 3 轮（FAIL，整改量最大的一轮）

**BLOCKER（已修）**

1. **群聊 P0 分支零覆盖**：撤回 / 状态 / 删除三个视图都按 `conversation.type` 分支，
   但整改前只有私聊分支有用例。审查者把三个 `else:` 群聊分支改成 `elif False:`、
   把 WS 群聊撤回的 `conversation_type == 'group'` 改成 `'group-off'`，
   **全量 470 条仍然全绿**——群聊分支被整段删除都不会被发现。
   整改：新增 `HTTP-GOPS-01…08`（8 条）与 `WS-GOPS-01…03`（3 条），
   每条都以「群聊分支被删即红」为设计目标。复测 5 个群聊分支变异体：
   GRP-RECALL / GRP-STATUS / GRP-DELETE / WS-GRP-RECALL / WS-GRP-RECALL-CODE **全部 CAUGHT**。
2. **第三轮记录预设结论**：本节曾写「本轮整改后已再开第三个审查者复验」，
   而当时结论尚未返回，属于把未发生的事写成已发生。整改：轮次表只记已返回的结论，
   第 3 轮的「FAIL」与发现项在收到结论后才填入。
3. **§3 统计与 §2 索引口径矛盾**（异常 26/越权 17 vs 实测 25/18，`HTTP-FILE-02` 归类冲突）。
   整改：统计改由索引表逐行解析得出（成功 18 / 异常 27 / 边界 12 / 越权 21 / 锚点 1 = 79），
   并写明复合标注按首类计入的口径。

**MAJOR（已修）**

1. 模块 docstring 自称 case index 却写「64 test methods / File API — 11」，
   与实际 79 条 / 14 条不符（`FILE-12`、`FILE-12b`、`FILE-13` 及群聊分支未列）。
   整改：docstring 索引重写为与文件逐条对应。
2. `file_unavailable` 的 410 在 `chat/views.py` 有 **6 处**（823/3315/3906/4199/4257/4286），非 4 处。已改。
3. 4401 的断言在 `test_core.py:994`，不在 `test_protocol.py`。§1 已改。
4. `docs/handoff/*` 引用（本分支不存在该目录）。已消除，仅 §8 开头保留一处并注明例外。
5. `traceability-matrix.md` 的 NEW-TST-010/011 用字面条数声称覆盖度，未声明群聊分支缺口。已改写。
6. **变异脚本判据不可靠**：`t13_mutation_probe.py` 用 `sys.executable` 且以
   `code != 0` 判「CAUGHT」，用裸 `python`（Django 2.2.4）跑时 import 崩溃也会被判成 CAUGHT。
   整改：脚本固定使用仓库 `.venv` 的解释器（存在性校验），并把「测试失败」与
   「环境失败」区分开（失败输出里必须出现 `FAILED` 才算 CAUGHT，否则记为 ERROR）。
7. **群聊侧幂等无用例**。整改：新增 `HTTP-GRP-10`，断言重放后行数、副本数、
   未读、`sync_sequence`、事件数全部不变。

**MINOR（已处理）**

1. `test_file_07` 与 `test_file_12` 前半段同构，共享 helper 的 `file_forbidden` 守卫
   删掉后 13 条全绿。整改：`HTTP-FILE-12b` 经**下载**路径再钉一次该守卫
   （实测：把 helper 的 `return None, JsonResponse(file_forbidden)` 改成
   `return ef, None` 后该用例红）。
2. `.gitignore` 新增 `.review/` 的提交不在审查者拿到的 diff 里。整改：diff 与
   HEAD 已重新对齐（见 §5 的被验证提交说明）。
3. `test_file_02` 的 10^12 字节写成「1 TB」不精确（实约 0.91 TiB）。已改为写「约 0.91 TiB」。

### 本轮遗留（未修，如实记录）

| 项 | 说明 |
| --- | --- |
| `test_protocol.py:132` | 同样是 `assertRaises(Exception)` 而非读 close code，属既有缺陷，本卡未越界修改 |
| 评审独立性 | 审查者由作者派出子 Agent，**不继承作者推理上下文，但不等于人类评审**；`tasks.md` 仍把「非作者评审」列为未完成 |
| `message.group.send` 的 WS 侧新增用例 | 本卡 0 条：WS 群聊发送在基线 `test_group_realtime.py` 已有 7 处断言（含成功、非成员、版本冲突、重放），本卡只补了它没有的群聊**撤回**；重复补发送路径不产生新证据 |

### 第 4 轮（最终轮，无 BLOCKER / 无 MAJOR）

第四轮审查者独立复算了全部声称的数字（79 条、分类 18/27/12/21/1、HTTP 61 / WS 18、
REGISTRY 25 断言 22、未覆盖 3 码及理由），并自选 12 个变异点验证——其中 10 个 CAUGHT、
2 个 SURVIVED（均为可解释的等价变异体，见下）。要点：

| 审查者变异 | 注入 | 结果 |
| --- | --- | --- |
| V1/V2/V3 | 三个视图的群聊 `else:` 分支 → `elif False:` | **CAUGHT**（HTTP-GOPS） |
| V4 | WS 群聊撤回 `'group'` → `'group-off'` | **CAUGHT**（WS-GOPS 3 条全红） |
| V4b | 同 V4，但只跑私聊 `WsRecallTests` | SURVIVED → **隔离性证明**：群聊与私聊用例互不搭便车 |
| V5 | 删群聊重放快路径（`messaging.py:637-640`） | SURVIVED → 等价变异体：`IntegrityError` 兜底第二层接住（服务层注释已声明的双保险） |
| V6 | 删共享 helper 的 `file_forbidden` 守卫 A | **CAUGHT**，且恰好只有 `test_file_12b` 红——精确复现本文 R2 整改的声称 |
| V7 | 删群聊撤回的级联 `update` | **CAUGHT**（gops_01） |
| V8/V12 | `_replay_or_conflict` 的 `created=False` → `True` | **CAUGHT**（grp_10 与 priv_02 双红）——零写契约是真断言 |
| V9 | 群聊逐收件人密文 → 共享同一密文 | **CAUGHT**（grp_01）——E2EE 每人一份副本是真断言 |
| V10 | 删 `message_kind != message_type` 检查 | **CAUGHT**（file_11） |

**MINOR（本轮 3 条，均不需改代码）**

1. `.review/pending.diff` 曾是主工作区另一分支的陈旧产物（环境问题，非交付物缺陷）。
   已在 worktree 内重新生成：7 文件、+2262/−3、SHA-256 前 16 位 `28DA1BD8DEF0534F`。
2. §2.4b/§8 里的「整改前全量 470 条仍全绿」是**历史实验陈述**，在当前 HEAD 上不可原样复现
   （470 = 当前 483 − 13 条群聊用例；提交历史 2e09122=66 条 → 04a6840=79 条支持该叙事）。
   保留原文并在此注明口径，避免被当作可复现断言。
3. 上述 V4b / V5 两条等价变异体作为隔离性与双保险的正面证据记录在案。

**结论**：第 4 轮以 `PASS WITH FINDINGS` 收敛（无 BLOCKER、无 MAJOR）。
按 conventions §1，合并前最后一轮的 PASS 须来自**非作者**——本轮仍是同一会话派出的
子 Agent，因此 `tasks.md` 的「非作者人类评审」仍保持未勾选，交由用户/小组完成。
