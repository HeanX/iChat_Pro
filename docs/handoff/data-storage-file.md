# 模块 6：数据模型、存储与加密文件（`chat/models.py` + media 边界）

> 交接对象：负责数据库模型、迁移、加密文件分片上传/下载、存储边界的 Agent。
> 核对日期 2026-10-18，基线 `cfe53be`。规范见 [conventions.md](conventions.md)。

---

## 1. 这块现在是什么

| 位置 | 内容 |
| --- | --- |
| `chat/models.py` | 844 行，16 个模型 |
| `chat/migrations/` | 21 个迁移（最新 `0021_conversation_sync_sequence_conversationevent`） |
| `accounts/migrations/` | 15 个迁移（最新 `0015_keyverificationrequest`） |
| `chat/urls.py` | 第 148–156 行是 9 条文件路由（对应 9 个视图，定义在 `chat/views.py`） |
| `chat/management/commands/cleanup_expired_uploads.py` | 过期上传与孤儿分片清理 |
| `static/js/file-transfer.js` | 客户端分块加密（1 MiB/块，上限 100 MiB，4 并发） |
| `deploy/nginx/ichat.conf` | `/media/avatars/` 公开，`/media/uploads/` 显式 403 |

---

## 2. 数据模型总览

### 会话与成员

| 模型 | 关键字段 | 约束 |
| --- | --- | --- |
| `Conversation` | `type`(single/group)、`status`、`membership_version`、`sync_sequence`、`last_message_id/at`、`muted_until`、`auto_delete_seconds` | 索引 `type`、`last_message_at` |
| `ConversationEvent` | `conversation`、`sequence`、`kind`（**目前只有 `message`**）、`message_type`、`message_id` | **唯一 `(conversation, sequence)`** |
| `ConversationMember` | `role`(owner/admin/member)、`status`(active/left/removed/muted)、`unread_count`、`last_read_message_id`、`joined_at`、`cleared_at`、`hidden_at`、`is_pinned`、`muted_until`、`auto_delete_seconds` | 唯一 `(conversation, user)` |
| `GroupInvitation` | 状态机 pending_admin → pending_invitee → accepted/rejected/cancelled | — |

### 消息

| 模型 | 是否存密文 | 说明 |
| --- | --- | --- |
| `EncryptedMessage` | 是（单份） | 私聊。`ciphertext` / `nonce` / `auth_tag` / `sender_ephemeral_public_key` + `sender_copy_*` 四列 |
| `GroupMessage` | **否** | 只存元数据 + `sender_copy_*`，密文本体在收件人行 |
| `GroupMessageRecipient` | 是（每人一份） | 唯一 `(group_message, receiver)` |
| `UserMessageDeletion` | — | 个人删除（不删物理行） |
| `ChatReport` | — | 举报 |
| `UserPresence` | — | 在线状态冗余表 |
| `AdminOperationLog` | — | 管理操作审计 |
| `GroupAnnouncement` | — | 群公告 |
| `UserLLMConfig` | — | 每用户的 LLM 端点与加密 API Key（AI 助手用） |

### 文件

| 模型 | 关键字段 | 约束 |
| --- | --- | --- |
| `EncryptedFile` | `upload_id`(UUID, unique)、`client_file_id`、`owner`、`sender`、`conversation`、`message_kind`、`parent_file_id`、`status`(uploading/available/failed/deleted)、`storage_path`、`total_size_bytes`、`chunk_size_bytes`(默认 1 MiB)、`chunk_count`、`algorithm`、`ciphertext_sha256`、`encrypted_metadata`+`metadata_nonce`+`metadata_auth_tag`、`expires_at`、`deleted_at` | `upload_id` 唯一；唯一约束 `unique_client_file_per_owner` = `(owner, client_file_id)` |
| `EncryptedFileChunk` | `chunk_index`、`size_bytes`、`offset_bytes`、`nonce`、`auth_tag`、`ciphertext_sha256`、`storage_path`（合并后置空） | 唯一 `unique_file_chunk_index` = `(file, chunk_index)` |
| `EncryptedFileKey` | `encrypted_file_key`、`nonce`、`auth_tag`、`algorithm`(默认 AES-256-GCM)、`sender`、`sender_key_version`、`receiver_key_version`、`membership_version` | 唯一 `unique_file_key_per_holder` = `(file, holder)` |

> **字段名易错点**：模型里是 `chunk_size_bytes` 和 `total_size_bytes`，**不是** `chunk_size` / `total_size`。写视图或迁移时按符号名核对。

> 注意：`EncryptedFile.Status` 的四个值在模型里是 `uploading/available/failed/deleted`；而 `Conversation.Status` 是 `active/archived/deleted`，`EncryptedMessage.Status` 是 `sent/delivered/read/deleted/failed/recalled`。**同名不同义，别跨模型复用。**

---

## 3. 文件上传/下载流程

```
POST   /api/files/uploads/                      创建上传会话（返回 upload_id）
GET    /api/files/uploads/<id>/                 查询上传状态
PUT    /api/files/uploads/<id>/chunks/<index>/  逐块上传（每块 ≤1 MiB，服务端只存密文）
POST   /api/files/uploads/<id>/complete/        完成（校验块数、算 ciphertext_sha256）
GET    /api/files/<file_id>/                    元数据
GET    /api/files/<file_id>/download/           下载（鉴权）
GET    /api/files/<file_id>/chunks/<index>/     分块下载
POST   /api/files/<file_id>/messages/           把文件挂到消息上发送
DELETE /api/files/uploads/<id>/cancel/          取消
```

**服务端全程只见密文**。文件密钥由客户端用各收件人公钥包裹后写入 `EncryptedFileKey`，服务端不持有任何明文密钥。

客户端的 AAD 约定（客户端侧，服务端不校验但必须与加解密两端一致）：

```
分块      AAD  = ichat-file-chunk-v1:{clientFileId}:{chunkIndex}
元数据    AAD  = ichat-file-metadata-v1:{clientFileId}
密钥包裹  AAD  = ichat-file-key-wrap-v1:{fileId}:{holderId}
```

**注意这里有两个不同的东西，容易混**：

- 上面三条是 **AES-GCM 的 AAD**（`additionalData`）。
- 文件密钥包裹**另外**用一个 **HKDF info**，字面量也叫 `ichat-file-key-wrap-v1`（`private-chat-e2ee.js:422` 的 `FILE_KEY_WRAP_HKDF_INFO`）。它是派生密钥时的 `info` 参数，不是 AAD。

改任何一个都会让已上传文件解不开。每块上传时附带 `SHA-256(ciphertext)` 供服务端校验完整性；发送方再自包一份用于多设备。

---

## 4. 迁移规则（最容易出事）

1. **迁移编号串行合并**：两个分支同时改模型会撞号。合并前先 `.\.venv\Scripts\python.exe manage.py showmigrations`。
2. **不要删除已上线迁移、不要重编号、不要 `--fake`**。
3. 合并后必须 `makemigrations --check --dry-run` 为 `No changes detected`。
4. 生产迁移前必须有可用备份——`deploy.sh` 会跑 `migrate`，但**不会自动备份**，且**回滚应用不降级数据库**。
5. SQLite 上 `select_for_update` 是空操作，并发正确性靠唯一约束兜底；**PG 上才是真行锁**。

---

## 5. 存储边界（安全红线）

| 路径 | 访问策略 | 说明 |
| --- | --- | --- |
| `media/avatars/` | nginx 公开，1 天缓存 | 头像必须公开才能被 `<img>` 直接加载 |
| `media/uploads/` | nginx **显式 403** | 加密分片只能经认证的 `/api/files/` 读取 |
| `/static/` | nginx 公开，7 天缓存 | 由 `collectstatic` 产出 |
| `/var/lib/ichat/media` | systemd `ReadWritePaths` | 改 `DJANGO_MEDIA_ROOT` 必须同步改 service |

**不得**把 `media/` 整个开放、把 `storage_path` 拼进公开 URL、或在日志里打印文件路径与密钥。

---

## 6. 清理任务

```bash
.\.venv\Scripts\python.exe manage.py cleanup_expired_uploads --dry-run
.\.venv\Scripts\python.exe manage.py cleanup_expired_uploads --older-than-hours 24
```

删除过期 `EncryptedFile` 记录及其磁盘文件，并清理超过 24 小时的孤儿分片目录。**生产需 cron 调度**（runbook 里目前没有这条，属缺口）。

---

## 7. 本地验证

```powershell
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py showmigrations chat | Select-Object -Last 5
.\.venv\Scripts\python.exe manage.py test chat.tests.test_integration chat.tests.test_t32_review -v 2
# 密文完整性 + 文件消息幂等，共 34 条

# 清理命令空跑
.\.venv\Scripts\python.exe manage.py cleanup_expired_uploads --dry-run
```

---

## 8. 已知坑

| # | 坑 |
| --- | --- |
| 1 | `GroupMessage` 不存密文，只有 `GroupMessageRecipient` 存。查群消息内容必须 join 收件人表 |
| 2 | `ConversationEvent` 目前只有 `message` 一类，撤回/删除/成员变更**不进事件流** |
| 3 | `EncryptedFile` 的 `client_file_id` 是幂等键，与 `client_message_id` 是两套 |
| 4 | 分片上传的 `chunk_size_bytes`（默认 1 MiB）与前端 `CHUNK_SIZE` 必须一致；改前端不改后端会静默错位 |
| 5 | `parent_file_id` 支持缩略图/预览派生物（`DerivativeRole`），但前端目前只用到 `original` |
| 6 | `test_core.py` 在 1096-1346 行重复定义了 3 个类（AST 与前面完全相同），后定义覆盖前定义。精确数字见 §9 |
| 7 | 软删不硬删：会话/群消息置 `status`，私聊置 `deleted_at`。物理清理只能靠清理命令 |
| 8 | `EncryptedFileChunk.storage_path` 在合并成完整文件后置空，只剩 `EncryptedFile.storage_path`；排查磁盘占用时要看两处 |
| 9 | 文件密钥的 `membership_version` 与群消息的成员版本是同一套语义，成员变更后旧文件的 wrapped key 仍按旧版本解释 |

---

## 9. 这个模块的开放任务

| Issue | 内容 | 优先级 | 缺口 |
| --- | --- | --- | --- |
| [#155](https://github.com/HeanX/iChat_Pro/issues/155) | P4 T28 Android 文件选择与保存 | P1 | 阻塞于 Android 端 |
| [#193](https://github.com/HeanX/iChat_Pro/issues/193) | P4 Test T12 覆盖率统计 | P0 | **仓库无任何覆盖率配置**（无 `.coveragerc`、无 coverage 依赖） |
| [#198](https://github.com/HeanX/iChat_Pro/issues/198) | P4 Test T17 安全测试 | P0 | 无 SAST/DAST/依赖扫描 |
| [#188](https://github.com/HeanX/iChat_Pro/issues/188) | P4 Test T07 服务端密文边界审计 | P0 | 有测试断言"模型无 plaintext 列"，缺完整审计报告 |

### 建议的第一个动作：修 `test_core.py` 的重复类

这是**真实的代码卫生缺陷**，但不是"丢了 20 条覆盖"：`chat/tests/test_core.py` 在 1096-1346 行重复粘贴了 import 与 3 个类（`ConversationModelTests`/`ConversationMemberModelTests`/`EncryptedMessageModelTests`），**两份定义的 AST 逐字节相同**，后定义覆盖前者。

精确数字（已用 `ast` 与 Django 测试加载器双向核对）：

| 状态 | 文件里的 `def test_` 个数 | 实际收集 | 全量 |
| --- | ---: | ---: | ---: |
| 现在（含重复块） | 100 | 80 | 404 |
| 只删 1096-1307（重复的 3 个类） | 80 | 80 | **仍是 404** |
| 误删到 1346（连带删掉 `AdminOperationLogTests`） | 77 | 77 | 381 |

**正确做法是删 1096-1307，不要删到 1346**：1308 行的 `AdminOperationLogTests` 是正常测试，不是重复块的一部分。删除重复块不会改变任何有意义的测试数字，只是让文件里的 `def test_` 个数不再虚高（100 → 80）。

单独提醒：如果有人告诉你"修完会变成 381"，那是把 `AdminOperationLogTests` 一起删掉了的结果，属于误删，不是预期。"384" 这个数在任何口径下都不成立。

---

## 10. 给下一个 Agent 的提示词

```text
你在 iChat_Pro 仓库负责【数据模型与存储模块】。

必读：docs/handoff/conventions.md、docs/handoff/data-storage-file.md、
docs/phase4/technical-design.md 的存储边界段。
再读代码：chat/models.py 的 ConversationEvent / EncryptedMessage / GroupMessageRecipient /
EncryptedFile 四块 → chat/management/commands/cleanup_expired_uploads.py →
deploy/nginx/ichat.conf 的 media 段。

任务：【填写 Issue 号与标题】。

不可破坏：
- 必须有唯一约束兜底（conversation+sequence、file+holder、owner+client_file_id）。
- 不得新增明文列；不得把 media/uploads/ 公开。
- 迁移编号串行，不删不改已上线迁移。
- 服务端不持有任何文件明文密钥。

必须执行：
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test chat.tests.test_integration chat.tests.test_t32_review
.\.venv\Scripts\python.exe manage.py test --parallel 1

涉及并发/锁的改动必须用真实 PostgreSQL 复跑。

完成前必须另开独立审查子 Agent 复核 diff（见 conventions.md §1）。
```
