# 模块 1：账号、认证与公钥信任（`accounts/`）

> 交接对象：负责账号体系、联系人、公钥/信任、隐私与设置 API 的 Agent。
> 核对日期 2026-10-18，基线 `cfe53be`。相关规范见 [conventions.md](conventions.md)。

---

## 1. 这块现在是什么

```
accounts/
├── models.py      17,196 B /  479 行  14 个模型 + 2 个 post_save 信号
├── views.py       68,336 B /1,837 行  全部视图与 JSON API
├── forms.py        4,425 B /  136 行  RegistrationForm、ProfileForm
├── urls.py         5,459 B /  160 行  43 条路由（无 app_name，name 进全局命名空间）
├── admin.py        2,169 B /   72 行  7 个模型注册
├── tests.py       61,072 B /1,499 行  127 条测试 / 16 个测试类
└── migrations/    15 个（0001–0015，最新 0015_keyverificationrequest）
```

**路由命名空间**：`accounts.urls` 没有 `app_name`，43 个路由名直接进全局；与 `chat.urls` 的 66 个名字无重名。改路由名会影响全站 `{% url %}`。

服务端**不持有任何用户私钥**。`UserPublicKey` 只存公钥与指纹；`KeyTrust` 只存"我信任这个对端公钥的哪个版本"。所有加解密在浏览器/客户端完成。

参考代码入口：

| 想了解 | 读这里 |
| --- | --- |
| 认证怎么走 | `accounts/views.py` 的 `login_view` / `register_view` / `logout_view`；`ichat_pro/middleware.py` 的 `ApiAuthMiddleware` |
| 公钥与信任 | `accounts/models.py` 的 `UserPublicKey` / `KeyTrust` / `KeyVerificationRequest`；`upload_public_key_view` / `public_key_view` / `batch_public_keys_view` |
| 设置 API | `accounts/urls.py` + `chat/views.py`（**存储/隐私/屏蔽 API 已迁到 chat 侧**，accounts 只留注释指路） |
| 会话与多账号 | `session_list_view` / `session_terminate_view` / `multi_account_view` |
| 联系人 | `contacts/search/`、`contacts/request/send/`、`.../accept/`、`.../reject/`、`.../cancel/`、`contacts/<id>/delete/` |
| 群组视图 | `/groups/`、`/groups/create/`、`/groups/<id>/`、`add-member/`、`leave/`（**模型已换成 `chat.Conversation`**，视图还留在 accounts） |

### 认证行为要点

| 行为 | 细节 |
| --- | --- |
| 注册密码 | 用户名 ≥3 且匹配 `^[\w.@+-]+$`；密码 ≥8、至少一字母一数字、不得含用户名；再叠 Django 四个 `AUTH_PASSWORD_VALIDATORS` |
| 登录 | 支持用邮箱登录（先按 `email__iexact` 改写成真实用户名）；`is_valid()` 后再显式 `authenticate()` 一次才 `login()` |
| 登录失败文案 | 统一"用户名或密码错误"，防枚举；**例外**：`is_active=False` 会额外提示"账号已被禁用"（等于泄露账号存在） |
| `next` 跳转 | `_safe_next_url` 用 `url_has_allowed_host_and_scheme`，只放行站内相对路径 |
| 登出 | **GET 也生效**（没有 `require_POST`），模板里就是普通 `<a href>` |
| CSRF | 全仓库 0 处 `@csrf_exempt`；`chat.js` 的 `apiFetch` 统一带 `X-CSRFToken` |
| 401 范围 | 只有 `/api/` 前缀返回 401 JSON；`/contacts/`、`/profile/edit/`、`/groups/` 等页面匿名仍是 302 |

---

## 2. 数据模型

| 模型 | 作用 | 关键字段与约束 |
| --- | --- | --- |
| `UserProfile` | 头像、昵称、签名、生日、`user_type` | OneToOne `User` |
| `UserPrivacySettings` | 谁可以找我、是否显示在线、已读回执 | OneToOne `User` |
| `BlockedUser` | 拉黑关系 | `blocker` / `blocked`；**唯一 `(blocker, blocked)`** |
| `UserStorageSettings` | 自动下载、缓存上限 | OneToOne `User` |
| `UserNotificationSettings` | 显示/私聊/群聊/预览四开关 | OneToOne `User`；对应 `display_notifications`、`private_chat_notifications`、`group_chat_notifications`、`message_preview_private`、`message_preview_group` |
| `UserGeneralSettings` | 通用设置 | OneToOne `User` |
| `UserChatFolderSettings` | 聊天文件夹 | OneToOne `User` |
| `MultiAccountContext` | 多账号快速切换上下文 | OneToOne `User` |
| `FriendRequest` | 好友申请 | `sender` / `receiver`；**唯一 `(sender, receiver)` 仅限 status=pending**（历史被拒后可再次申请） |
| `Contact` | 好友关系 | `user` / `contact`；**唯一 `(user, contact)`**，双向两行 |
| `UserPublicKey` | ECDH-P256 身份公钥 + 多版本历史 | `identity_public_key`(Text)、`key_fingerprint`(64 hex，**SHA-256(SPKI) 大写**)、`algorithm`(默认 `ECDH-P256`)、`key_version`、`is_active`；**唯一 `(user, key_version)`** |
| `KeyTrust` | 我对对端某公钥版本的信任 | `user` / `contact` / `key_fingerprint` / `key_version` / `trust_status`；**唯一 `(user, contact, key_fingerprint)`** |
| `KeyVerificationRequest` | 带外验证请求 | `requester` / `responder` + 双方指纹与版本快照 + `status`(pending/accepted/declined/expired/cancelled) + `responded_at`；状态机见 `models.py` 的 `KeyVerificationRequest` 类（约 386 行起） |
| `UserProfileUpdateLog` | 资料变更审计 | — |

> 注意：`UserPublicKey` 的唯一键是 `(user, key_version)`，**不是** `(user, fingerprint)`。指纹单独建索引，用于查询与校验，不承担唯一性。

---

## 3. 关键契约（不能动）

1. **`ApiAuthMiddleware`**：任何 `/api/` 路径未登录必须返回 **401 JSON** `{"error": "authentication_required", ...}`，不能返回 302 到登录页。这是 R-06 的修复，XHR 依赖它。
2. **公钥指纹**：`SHA-256(SPKI)` 的**大写 hex**，服务端会重算并拒绝不匹配的上传（`accounts/views.py` 约 1055 行）。改动计算方式 = 破坏所有已存信任关系。
3. **上传公钥时不得携带私钥字段**：服务端拒绝含 `private_key` / `session_key` / `file_key` 字段的载荷。
4. **`KeyTrust` 不自动继承**：对端换公钥后，新指纹默认**未信任**，必须显式标记。旧信任记录保留用于审计。任何"自动信任新钥"的改动都是安全事故。
5. **拉黑双向生效**：`BlockedUser` 在消息服务里被双向检查（发送方拉黑对方、或对方拉黑发送方，都返回 `conversation_forbidden`）。
6. **密码策略**：走 Django `AUTH_PASSWORD_VALIDATORS` 四项，不要绕过。
7. **头像上传**：写入 `MEDIA_ROOT`，生产环境 nginx 只公开 `/media/avatars/`。测试必须隔离 `MEDIA_ROOT`——`accounts/tests.py` 已有类级临时目录（历史缺陷：曾向仓库真实 `media/` 写入 82 个文件，2026-10-13 修复于 commit `78e7f54`）。

---

## 4. 本地验证

```powershell
.\.venv\Scripts\python.exe manage.py test accounts -v 2
# 期望：138 条测试通过（127 + #189 新增 11 条）

.\.venv\Scripts\python.exe manage.py test accounts.tests.AvatarAndProfilePrivacyTests
# 头像/隐私测试：必须确认没有往仓库 media/ 写文件
git status --short -- media/      # 应无输出
```

完整套件速度：`accounts` 的 138 条占全量的一部分，单跑约 40–60s。全量条数见 [testing-quality-ci.md](testing-quality-ci.md)。

**测试未覆盖的部分**（改这些地方要自己补测试）：`friend_request_cancel`、`accept_by_user`、`reject_by_user`、`/api/sessions/terminate/`、`KeyVerificationRequest`（后者的 9 个用例在 `chat/tests/test_phase2_issues.py::KeyTrustTests`）、头像编辑器页面。
> `/api/keys/trust/` 家族已于 2026-10-18 由 #189 覆盖：`accounts/test_key_change_reminder.py`（11 条服务端用例）+
> `chat/tests/js/key_change_reminder.test.js`（13 条客户端用例，已并入 `npm run test:e2ee`）。

---

## 5. 已知坑

| 坑 | 细节 |
| --- | --- |
| `accounts/tests.py` 体量大 | 61 KB / 1499 行，16 个测试类。改动前先 `Select-String '^class '` 定位，别通读 |
| 头像测试污染 | 已修；但**新写的上传测试**必须同样隔离 `MEDIA_ROOT`，否则会再次写脏仓库 |
| 设置分散两处 | 通知/隐私/存储/自动删除的视图在 `chat/views.py`，模型在 `accounts/models.py`。改一个设置要同时看两边 |
| 多账号上下文 | `MultiAccountContext` 只是 UI 快捷入口，不是真正的多租户隔离；不要据此认为"一个浏览器登录多个账号" |
| 会话列表 | `session_list_view` 基于 Django session，不是设备列表。设备级身份/撤销仍是开放任务（#167–#170） |
| 公钥批量接口 | `batch_public_keys_view` 用于群聊初始化；前端一次最多传 **100 个 user_id**，N 个成员的群会打这个接口，注意查询次数 |
| `KeyTrust` 与前端不同步 | 服务端有 `/api/keys/trust/`，但浏览器端信任主要存在 localStorage `ichat_peer_identity:<userId>`。两边是两套状态，改信任逻辑要同时看 `accounts/views.py` 与 `static/js/private-chat-e2ee.js` |
| 信任状态必须按指纹解释 | 2026-10-18（#189）修过一次：`/api/keys/trust/` 原先返回**最新那一行** `KeyTrust`，对端轮换后行里还是旧指纹，于是 `trust_status` 显示继承来的旧结论、`key_changed` 拿行跟自己比恒为 False；`/api/keys/contacts/<id>/fingerprints/` 的 `key_changed` 又在重新验证后仍为 True（永久误报）。现在统一走 `_summarize_key_trust()`：当前有效密钥未背书、且存在已背书的旧指纹时才报 `key_changed`。**再改信任逻辑不要退回"取最新一行"的写法。** |
| `previous_*` 与 `last_endorsed_*` 不是一回事 | 连续轮换（v1 已验证 → v2 → v3）后，`previous_key_version=2` 来自 `UserPublicKey` 历史（真正上一版，可能从未被信任、也就没有 `KeyTrust` 行），`last_endorsed_key_version=1` 才是"告警针对的那个指纹"。**别用 `KeyTrust` 记录去算"上一版"**——未被背书的版本根本没有记录。 |
| DELETE 只删当前指纹那一行 | `DELETE /api/keys/contacts/<id>/trust/` **保持 T38 原语义**：只删当前活动指纹的判定，其它历史行保留审计（契约 4）。2026-10-18（#189）一度改成按 `(user, contact)` 全删，被评审打回——那会同时毁掉审计记录，且 API 文档与 toast 文案对不上。轮换后的告警**应该靠验证新密钥清除**，不是靠删记录。副作用要知情：轮换后新指纹还没有行，此时 DELETE 是 no-op（`deleted:false`），告警仍在，这是有意为之。 |
| `KeyTrust` 没有状态转移约束 | POST 可以把状态直接设成 `trusted`/`untrusted`/`verified` 任意值，`verified_at` 无条件写 now()。想加约束要先确认现有客户端不会绕过 |
| 公钥不做曲线点校验 | 只验 base64、非空、解码后 ≤512 字节，**不验是不是合法 P-256 点** |
| 公钥轮换无行锁 | `latest.key_version + 1` 是读后写，同用户并发轮换会撞 `unique_user_public_key_version` |
| `Contact` 非对称唯一 | 唯一键是 `(user, contact)`，A→B 与 B→A 可并存。删除只删一行，反方向残留会让 `_are_contacts` 仍为真 |
| 好友"取消"复用 `rejected` | 模型没有 `cancelled`，前端无法区分"我取消"与"被拒" |
| 坏 JSON 曾会 500 | 2026-10-18（#189）已修 `key_trust_view`：非 dict / 非法 JSON body 现在返回 400 `invalid_json`。`key_verification_requests_view` 仍是直接 `.get()`，坏 body 会 `AttributeError` → 500，尚未修。 |
| `passcode` 是明文字段 | `UserPrivacySettings.passcode` 是 `CharField(128)` 原样存、默认空、**没有哈希也没有任何写入路径**。二次验证/通行密钥只有字段与序列化，`login_view` 完全不读 |
| 用户名规则不一致 | 注册允许 `@.+ -` 且 ≥3；`ProfileForm` 要求 ≥5 且只允许 `[a-zA-Z0-9_]`，且编辑时 `.lower()` |
| 会话终止依赖内存映射 | `/api/sessions/terminate/` 用当前 session 里的 `_session_index_map` 做不透明 ID→真 key 映射，换浏览器后必须先重新 GET `/api/sessions/` |
| 账号删除不全 | 只把 username 改成 `deleted_<ts>` 并置 `is_active=False`，**不删公钥、资料、消息** |
| 速率限制 | **账号侧没有任何速率限制或登录锁定**。`chat/views.py:238` 的 `_rate_limit()` 只用在三个 AI 接口（`ai_config` 30/60s、`ai_status` 60/60s、`ai_chat` 20/60s），返回的错误码 `rate_limited` **未在 `chat/errors.py` 注册** |

---

## 6. 这个模块的开放任务

| Issue | 内容 | 优先级 | 现状 | 建议起点 |
| --- | --- | --- | --- | --- |
| [#167](https://github.com/HeanX/iChat_Pro/issues/167) | P4 T40 设备身份模型 | P1 | 无设备表，只有 `UserPublicKey`（每用户多公钥但无设备归属） | 先设计 `Device` 模型与迁移 ADR，**不要**先写代码 |
| [#168](https://github.com/HeanX/iChat_Pro/issues/168) | P4 T41 设备列表与撤销 | P1 | 无 | 依赖 #167 |
| [#169](https://github.com/HeanX/iChat_Pro/issues/169) | P4 T42 多设备密钥分发 | P1 | 无 | 依赖 #167/#168；需与消息模块协商 `recipients` 语义 |
| [#170](https://github.com/HeanX/iChat_Pro/issues/170) | P4 T43 历史密钥迁移 | P2 | 无 | 只做设计与原型，不落地 |

设备模型的最小可行设计（供讨论，不是结论）：`Device(user, device_id, name, platform, public_key_fingerprint, created_at, revoked_at)`，`UserPublicKey` 增加可选 `device` 外键。**这会改变群聊 `recipients` 的语义**（从"每个成员"变成"每个成员×设备"），必须与 [messaging-protocol.md](messaging-protocol.md) 的负责人串行整合。

---

## 7. 给下一个 Agent 的提示词

```text
你在 iChat_Pro 仓库负责【账号与公钥信任模块】。

必读：docs/handoff/conventions.md（流程与证据规则）、docs/handoff/accounts-auth.md（本模块现状）。
再读代码：accounts/models.py、accounts/views.py 的认证与公钥部分、ichat_pro/middleware.py。

任务：【填写 Issue 号与标题】。

约束：
- 不得改变公钥指纹算法，不得让 KeyTrust 自动继承，不得让 /api/ 未登录返回 302。
- 新模型/迁移需先提交设计与编号计划，与消息模块负责人串行合并。
- 测试必须隔离 MEDIA_ROOT，不得向仓库 media/ 写文件。

必须执行：
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test accounts
npm run test:e2ee

完成前必须另开一个独立审查子 Agent 复核你的 diff（见 conventions.md §1），
把审查结论与修复轮次写进 PR 描述。未真实执行的验收项标注为未验证。
```
