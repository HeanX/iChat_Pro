# Phase4 技术方案与代码入口

> 整理日期：2026-10-01；代码核对基线：`b3d529f`。  
> “现状”为当前本地代码的静态核对；“建议/拟新增”为交接设计，尚未实现。依赖版本以仓库实际锁定值为准，新工具由工作包验证兼容性后固定，不盲目升级全部依赖。

## 1. 架构与技术边界

现有业务继续由 Django Templates、原生 JavaScript、Django Session、Channels 承担。Windows 延续 Electron；Android 在 P4 T22 先完成可运行的技术验证。没有要求迁移 React/Vue、DRF、JWT 或独立微服务。

```text
Browser / Electron / Android
            │ HTTPS + Session/CSRF，WSS
            ▼
      Nginx / TLS / 静态资源
            ▼
    Django ASGI / Daphne（多个进程）
      │          │           │
 PostgreSQL    Redis     持久化 media
 业务与密文   分发/缓存   头像/密文分块
```

消息可靠性的事实来源是 PostgreSQL，Redis 负责实时分发，不是消息历史数据库。Channel Layer 允许在容量不足时丢弃 group 消息，不能将 `group_send` 成功当成持久送达保证；因此补取必须读取持久数据。[Channels 官方规范](https://channels.readthedocs.io/en/stable/channel_layer_spec.html)

## 2. 现有代码与缺口

| 区域 | 现状与入口 | Phase4 需要补齐 |
| --- | --- | --- |
| 配置 | `ichat_pro/settings.py` 支持 DATABASE_URL、REDIS_URL；默认 SQLite/内存通道；非法 DB URL 静默回 SQLite | 生产配置失败即拒绝启动；规范 URL 解析；独立环境与启动检查 |
| 生产依赖 | `requirements.txt` 有 Django 6.0.5、Channels/Daphne 4.3.2、redis 6.4.0 | 未列 `channels-redis` 和 PostgreSQL 驱动；需选型、固定和真实集成验证 |
| ASGI/认证 | `ichat_pro/asgi.py` 已用 `AllowedHostsOriginValidator`、`AuthMiddlewareStack` | 域名、Cookie、CSRF 与 Android Origin 策略联调；账号禁用/设备撤销应验证现存连接 |
| 模型 | `chat/models.py` 的 Conversation(single/group)、Member、EncryptedMessage、GroupMessage/Recipient、EncryptedFile/Key | 增量同步游标/幂等信息；P1 独立设备与逐设备密文关系 |
| 账号公钥 | `accounts/models.py` 的 UserPublicKey 按 user+key_version 保存，历史版本保留 | 当前不是设备级身份模型；不能靠上传新公钥冒充完整多设备分发 |
| 消息写入 | `ChatConsumer.create_private_message/create_group_message` 有事务、逻辑消息唯一约束 | 校验顺序、冲突重放、并发副作用、持久化后的推送失败处理 |
| HTTP fallback | `chat/views.py` 调用 Consumer 创建方法，视图另有权限处理与推送 | 统一业务服务与按查看者序列化；防止两个入口规则漂移 |
| 历史 | 私聊/群聊历史按页查询；私聊默认按 `-created_at` | 增量补取、稳定次序、并发分页与撤回/删除同步 |
| Web 前端 | `static/js/chat.js` 内置 WS、固定 1500ms 重连、发送状态/HTTP fallback | 一个共享连接状态机、ACK超时、密文待发箱、补取与去重；生命周期幂等 |
| 密钥存储 | `key-manager.js` 将不可导出的 CryptoKey 存 IndexedDB；localStorage 存公开记录 | 新建/导入/迁移的明文 JWK 备份暂写 sessionStorage，平台存储与迁移仍需实现 |
| 草稿 | `chat.js` 有 localStorage 草稿逻辑 | 原生客户端草稿应加密或只驻内存；切号隔离；不能机械复制旧存储行为 |
| 桌面 | `desktop/main.js` 默认 localhost HTTP，自动启动 runserver；preload 仅暴露平台标识 | 云地址、离线错误页、安装构建、IPC/安全存储、通知/文件系统 |
| Android | 当前没有 mobile/android 工程 | T22 技术验证、工程骨架、APK、适配和平台测试 |
| CI | `.github/workflows/django.yml` 仅 Python/Django/迁移/后端测试 | Node 加密测试、覆盖率、静态/安全、真实服务集成、客户端构建、冒烟 |

### 2.1 已发现、需要任务复核的具体风险

1. 私聊创建方法在会话/权限校验前按 `(sender_id, client_message_id)` 找旧消息；应测试同一 ID 携带不同会话/接收者是否错误重放。安全校验与幂等命中不能互相绕过。
2. 群聊在成员版本和 recipients 校验后才查幂等；“已提交、ACK丢失、随后成员变更”可能无法获得原确认。须先验证当前访问身份、再识别合法原请求重放，对真正的新消息才检查当前分发版本。
3. WS 私聊分别按发送者/接收者序列化；HTTP 私聊目前直接推送创建方法返回的通用 message，再给发送者序列化响应。统一后必须覆盖双方不可读取另一份密文的测试。
4. 重放虽可能不重复入库，入口仍可能重复广播；客户端应去重，服务端应区分 created/replayed，不重复计数、生成同步事件和通知。
5. 在线状态尝试调用非标准 `group_channels` 并在异常时继续；真实 Redis、多连接关闭和多进程需要专门验证，不能按内存测试推定在线状态正确。
6. API 使用 `login_required`，失效时可能重定向 HTML 登录页；新客户端应识别 401/4401/登录重定向，API 应逐步统一 JSON 认证错误，不显示解析异常。
7. 生产设置已有 Secure Cookie/HSTS，但缺少完整代理信任、静态收集和私有媒体部署方案；不能仅设置 `DJANGO_DEBUG=False` 就声明生产就绪。

这些是静态观察和测试切入点，并非本次已经修复的缺陷。

## 3. 云端配置与部署（WP01）

### 3.1 建议配置契约

| 项目 | 现有变量/拟新增 | 约束 |
| --- | --- | --- |
| Django 调试/秘密 | `DJANGO_DEBUG`、`DJANGO_SECRET_KEY` | 实际代码读 DJANGO_DEBUG，旧 README 的 DEBUG 名称不能直接照用；生产 false 且秘密必填 |
| 域名 | `DJANGO_ALLOWED_HOSTS` | 精确域名列表，不用 `*` |
| DB | `DATABASE_URL` | 生产必须 PostgreSQL；解析用户/密码百分号编码、端口和查询参数；无效值明确失败 |
| 通道与缓存 | `REDIS_URL` | 生产必填；私网访问；缓存、Channel Layer 与限流命名隔离 |
| 代理/CSRF | 拟新增 `DJANGO_CSRF_TRUSTED_ORIGINS` 与受信代理配置 | 精确 HTTPS Origin；仅受信代理可设置 forwarded header |
| 存储 | 拟新增 `DJANGO_MEDIA_ROOT`、STATIC_ROOT 配置 | 持久卷与收集目录分开；不可将整个 media 当成公开静态文件 |
| 客户端 | 拟新增 `ICHAT_SERVER_URL` | 完整 HTTPS Origin；开发 localhost 使用显式 dev 模式；安装包无需环境变量也有已配置目标 |
| AI | 沿用已有按用户配置及服务端允许配置 | 不写入安装包，不自动读取聊天密文解密结果 |

这些新增名字需要在代码、`.env.example`、部署清单与客户端构建中一起落地。生产模板仅放占位符，不复制现有 `.env`。主 README 也需在实现任务中更新实际变量名。

### 3.2 交付文件建议

WP01 可新增 `deploy/`：Nginx 模板、Compose 或 systemd 方案、部署/备份/恢复脚本、生产环境示例和运行手册。首次只选择一种主要托管路径，避免维护两个未经验证的生产方案。

实施要点：

- 固定 Python 3.13+ 与兼容依赖，安装 PostgreSQL 驱动和 channels-redis；执行 migrate、CSS构建、collectstatic。
- Nginx 转发 Host、受信 X-Forwarded-Proto 及 WS Upgrade/Connection；idle timeout 大于心跳周期；配合上传分块大小限制。
- Daphne 绑定内部地址；PG/Redis 不映射公网端口；仅开放 TLS、必要的 HTTP跳转和受限管理入口。
- 静态资源可直接服务；头像按公开策略处理；聊天密文分块通过鉴权接口或鉴权后的内部重定向，不能让 URL 绕过 EncryptedFileKey 权限。
- 存储卷同时覆盖文件分块、元数据需要的路径；清理任务不能删除合法完成文件。
- `/health/live/`、`/health/ready/` 是建议新增接口：liveness 检查进程，readiness 有超时地检查 DB/Redis，故障返回503。公开输出仅状态，内部日志保留脱敏原因。
- 日志只记录版本、request_id、事件类型、错误码和必要实体 ID；禁止记录整个请求/响应、Cookie、密文载荷或上传内容。

### 3.3 备份、升级与回滚

备份 PostgreSQL 和持久化文件并记录版本、时间、校验值、保留期。首版可采用短维护窗口保证 DB和文件快照一致，之后再优化在线备份；恢复到独立实例后验证用户/关系/会话/消息数量、文件存在与测试客户端解密。

数据迁移不能简单假定 SQLite dump 直接成功；若需要保留旧数据，核对时区、序列、约束和外键。备份文件属于敏感业务数据，不能上传 Git。

升级按向后兼容的“新增字段/兼容读写→迁移→切换→后续清理”推进。应用回滚不等于数据库自动降级；不可逆 migration 必须在发布手册明确恢复方案。不要回滚掉客户端所需的身份、公钥历史版本或已经生成的密文元数据。

## 4. HTTP 与 WebSocket 契约（WP02）

### 4.1 已有路由

| 通道 | 路径/事件 | 用途 |
| --- | --- | --- |
| HTTP | `/api/conversations/`、`/api/conversations/create/` | 会话列表/私聊创建 |
| HTTP | `/api/conversations/{id}/messages/` | 私聊历史分页 |
| HTTP | `/api/groups/{id}/members/`、`/api/groups/{id}/messages/` | 群成员/群历史 |
| HTTP POST | `/api/conversations/{id}/messages/send/` | 私聊 fallback |
| HTTP POST | `/api/conversations/{id}/messages/send-group/` | 群聊 fallback |
| HTTP | `/api/keys/upload/`、`/api/keys/{user_id}/`、`/api/keys/{user_id}/{key_version}/`、`/api/keys/batch/` | 公钥管理 |
| HTTP | `/api/files/uploads/…`、`/api/files/{id}/…` | 上传、密钥元数据、鉴权下载 |
| WS | `/ws/chat/` | Session 认证的单一实时入口 |
| WS 请求 | `connection.ping`、`message.single.send`、`message.group.send`、`message.receipt.update` | 心跳、发送和回执 |
| WS 响应 | `connection.ready/pong`、`message.single/group.accepted`、`message.single/group.new`、`message.receipt.updated`、`error` | 连接、持久确认、推送、回执、错误 |
| WS 状态 | `message.recalled`、`message.deleted`、`group.members.changed` | 消息/成员变更 |

HTTP 当前响应多为业务对象；WS `send_event` 输出 `protocol_version`、`event`、`request_id`、服务端时间 `sent_at` 与 `data`。必须兼容现有前端，不强行一次改成所有 HTTP 都使用新 envelope。

### 4.2 当前发送字段

WS 请求外层当前使用 `event`、`request_id`、`data`；服务端输出 `protocol_version: "1.0"`。当前 receive_json 没有完成请求版本强制协商，T31 需要补齐，并约定旧客户端省略版本时的兼容窗口。

| 私聊 data | 群聊 data |
| --- | --- |
| conversation_id、receiver_id、client_message_id | group_id、membership_version、client_message_id |
| message_type、algorithm | message_type、algorithm、sender_key_version |
| sender_key_version、receiver_key_version | recipients[]：receiver_id、receiver_key_version、ciphertext、nonce、auth_tag、sender_ephemeral_public_key |
| ciphertext、nonce、auth_tag、sender_ephemeral_public_key | sender_copy（发送者可解密副本） |
| sender_copy、可选 reply_to_message_id/file_id | 可选 reply_to_message_id/file_id |

兼容限制：AES-256-GCM；nonce 解码12字节；tag16字节；每份 ciphertext 解码上限65536字节；client_message_id 非空字符串且≤64字符；当前群上限50名有效成员；recipients 必须覆盖有效成员（包括发送者），不能擅自去掉发送者而破坏验证。需要额外加入 WS 整包大小和列表长度预检查，避免只校验单份密文。

`request_id` 关联一次传输请求，重试可改变；`client_message_id` 关联一条逻辑消息，重试必须不变。所有重试保存同一加密 envelope，不重新随机加密后复用原 ID。

### 4.3 建议统一服务层

新增 `chat/services/messaging.py` 或等价模块，承接同步的事务业务；HTTP直接调用，WS通过 database_sync_to_async 调用。返回 `{created/replayed, message identity, viewer payloads}`，由入口包装响应。不要让视图与 Consumer 再各写一套校验。

事务顺序建议：结构校验→身份/当前访问权校验→校验幂等请求一致性→会话锁→新消息的当前权限/成员版本/文件校验→保存消息及收件副本→更新未读/会话时间/同步序号→提交。重放只返回原结果，不再次计数或创建同步事件。

保持现有 `(sender, client_message_id)` 唯一约束；私聊/群聊各自作用域必须明确。相同 ID 改变会话、接收者、类型、密文或附件，返回409 `idempotency_conflict`，不能返回另一会话的记录。建议增加规范化请求摘要，排除 request_id/传输字段；若采用等价比较也必须覆盖并发冲突。

实时推送在事务提交后执行。推送失败不推翻已保存的消息，也不能触发重复保存；返回持久确认，记录脱敏故障并依赖后续补取。ACK丢失时客户端用原 ID核对/重试。由于当前业务使用异步包装，Agent 应明确事务提交后到 channel layer 的调用边界并覆盖异常测试。

### 4.4 错误规范建议

保留已有代码并集中注册：`invalid_payload`、`unsupported_algorithm`、`conversation_not_found`、`conversation_forbidden`、`membership_conflict`、`recipients_mismatch`、`group_muted`。拟新增 `authentication_required`、`unsupported_protocol_version`、`idempotency_conflict`、`storage_unavailable`、`sync_cursor_invalid/expired`；只有实现后才可宣传为已有。

HTTP：输入400、认证401、权限403、资源404、冲突409、限流429、临时服务503。WS使用相同业务错误码；认证失效4401，权限/撤销可采用已文档化关闭码。客户端依据码决定刷新密钥/成员、停止重试或允许手动重试，不按错误字符串推断。

## 5. 状态机、补取与排序（WP02）

### 5.1 状态语义

| 状态 | 触发 | 禁止行为 |
| --- | --- | --- |
| sending | 加密 envelope 与稳定 ID已建立，提交中 | 不因 socket.send 成功就标记 sent |
| sent | 服务端确认事务持久化，并返回正式 ID/服务端时间 | ACK丢失视为结果未知，不断言未保存 |
| delivered | 对方客户端成功接受该密文并按协议回执 | 不把写入 Redis或推送函数返回当送达 |
| read | 对方进入会话并按协议确认阅读 | 不因通知弹出就标已读 |
| failed | 明确失败或等待超时，保留可重试待发项 | 不删除内容；超时后仍允许迟到 ACK 将其恢复为 sent |

回执单调：sending→sent→delivered→read；read不能降成delivered。failed→sending使用原ID；failed收到原请求迟到确认可→sent。撤回/删除是独立生命周期状态，应优先于旧推送与旧回执，避免“撤回后被重放复活”。群聊每个 Recipient 独立回执，聚合规则明确标注。

连接采用 connecting/online/offline/reconnecting/auth_required 状态；指数退避带抖动、上限小于30秒，并在 online/前台恢复时立即尝试。单实例连接、心跳和超时可清理；认证失败停止循环，切号销毁旧socket、timer、待发箱与游标。

### 5.2 建议持久同步模型

仅重拉“最新30条”或用客户端时间作为 since，不能保证遗漏补取。推荐新增会话内单调序号和持久事件：

| 建议模型/字段 | 作用 |
| --- | --- |
| Conversation.sync_sequence | 当前会话已提交序号；在持有该会话行锁的事务里递增 |
| ConversationEvent(conversation, sequence, kind, message_type, message_id, audience_user_id?, created_at) | 唯一约束(conversation,sequence)，持久记录新消息及必要状态变更；只存引用/元数据，不存明文和整套密文 |
| 客户端 cursor | 按服务Origin、user、device和conversation隔离；仅保存已完整应用的同步位置 |

所有生成事件的写入路径，包括文本、文件、转发、回执/撤回/删除和成员操作，按同一锁顺序分配序号。P0 必须覆盖新增消息的全部写入路径；P1 完善状态变更事件。自增消息主键在并发事务中可能先分配后提交，不能只把最大主键当“所有之前消息已提交”的保证。

建议新增 `GET /api/conversations/{id}/sync/?cursor=…&limit=100`，返回 `items`、`next_cursor`、`has_more`、固定的 `high_water`。cursor 使用服务端签名，绑定会话/用户（P1绑定设备）和页快照上界；首次可用空cursor。消息按sequence升序；请求范围不包含high_water之后新事件，下一轮再补。

读取时重做当前权限和逐用户/设备投影：成员状态、joined_at、历史 Recipient、个人删除/清空/自动删除策略、设备撤销均不能由cursor绕过。不可见事件可以推进扫描游标，但不得返回泄漏元数据；`has_more`按扫描边界计算，避免空页无限循环。撤回事件不带旧正文；个人删除事件只投影给本人。

初次加载旧历史可继续使用分页接口，但应增加 `(created_at,id)` 稳定排序，并通过迁移回填历史事件或提供明确的初始快照/游标锚点。数据库中已经有的文件消息与转发消息也需覆盖，不能只回填文本。

连接 ready后：先缓冲/应用实时事件，同时补到high_water，按稳定身份合并；再从下一游标继续追赶实时期间新增。客户端必须按事件序号顺序应用并原子推进cursor，分页失败从上个cursor重试，不能先保存high_water。保留期导致cursor过期时显式返回需重新快照，不能静默跳过。

### 5.3 去重与显示顺序

本地消息身份用 `(conversation_type, conversation_id, message_id)`；未确认消息用 `(sender_id, client_message_id)` 并在ACK后合并。私聊与群聊ID来自不同表，不可全局仅按message_id去重。

展示优先使用服务端序号（有回填时）；兼容历史按 `(created_at,message_id)`。临时消息单独排序，收到ACK后按服务端数据定位；成员历史边界与副本匹配不能根据显示时间猜测。

## 6. 加密兼容与安全存储（WP05）

### 6.1 必须保留的现有算法契约

现有 `private-chat-e2ee.js`、`group-chat-e2ee.js` 实际使用 ECDH P-256、HKDF SHA-256、AES-GCM 256位。公钥为 SPKI DER Base64，文本UTF-8，nonce12字节，tag16字节与ciphertext拆开传输。

消息 HKDF info为 `chat-message-encryption-v1`，salt为上下文UTF-8的SHA-256：

```text
私聊 single:{conversation_id}:{sender_id}:{receiver_id}:{sender_key_version}:{receiver_key_version}
群聊 group:{group_id}:{membership_version}:{sender_id}:{receiver_id}:{sender_key_version}:{receiver_key_version}
```

当前发送路径为每份接收副本生成临时ECDH密钥，并独立生成sender_copy；旧静态路径保留兼容。Android原生若承担派生/加密，必须逐字节对齐上下文、SPKI、tag拼接和编码。不得按旧“全是静态密钥”的文档重新实现，也不得宣称现在具备Double Ratchet或完整前向保密。

文件密钥包装独立使用 `ichat-file-key-wrap-v1`，AAD含file_id/holder_id；分块参数和实际salt上下文以 `file-transfer.js` 及两个E2EE模块为准，先生成测试向量再写平台适配。对象存储不能代替密钥分发和鉴权。

### 6.2 存储接口建议

由WP05先定义公共KeyStore能力契约：生成身份、取得公钥/指纹、按handle和版本派生、导入用户授权的旧备份、删除/锁定、查询平台能力。正常业务不返回私钥；不要提供任意键值读取、任意文件访问或通用eval的原生桥。

| 平台 | 建议 | 限制与验收 |
| --- | --- | --- |
| Web | 保留IndexedDB不可导出CryptoKey及CSP；公开记录可localStorage | 清理明文pending backup；解释用户授权备份与丢钥策略；不能阻止已在同Origin执行的恶意JS使用密钥 |
| Windows | 主进程使用Electron safeStorage保护持久化密钥材料，preload暴露窄接口；需要时短暂导入为不可导出WebCryptoKey | 检查isEncryptionAvailable，失败不得降级明文；普通文件只含密文；safeStorage不是同用户恶意进程隔离 |
| Android | Android Keystore保护包装密钥或原生身份密钥；持久化仅保存受保护材料 | 直接Keystore P-256/ECDH支持须按选定API/设备验证；不满足时使用Keystore包装软件生成身份密钥，不悄悄存明文 |

safeStorage在Windows使用DPAPI；应按其实际威胁边界验收。[Electron safeStorage](https://www.electronjs.org/docs/latest/api/safe-storage) Android方案依据系统Keystore能力，具体兼容性由T22/T29原型验证。[Android Keystore](https://developer.android.com/privacy-and-security/keystore)

现有不可导出IndexedDB私钥不能直接export迁移；先保留旧读路径，用户有备份时显式导入，缺失时提示限制。禁止自动删旧钥、自动换钥或声称旧历史可恢复。初始化/迁移中的JWK只能短暂驻内存，不写sessionStorage；若需要备份下载，应使用用户授权的加密导出并明确恢复要求。

凭据继续使用Django Session，Cookie HttpOnly/Secure；无新增Token体系时不要让客户端制造Token数据库。草稿、待发箱、密钥和非敏感界面配置分别定义存储策略；原生端待发箱只持久化密文envelope，草稿加密或仅内存。

### 6.3 IPC/桥与通知

Electron维持nodeIntegration=false、contextIsolation=true、sandbox=true。所有特权调用检查发送frame与允许Origin、参数和当前账号；导航与新窗只准受信来源/安全外链。官方要求验证IPC发送者，不能只因为启用了contextIsolation就忽略鉴权。[Electron安全指南](https://www.electronjs.org/docs/latest/tutorial/security)

Android桥同样按精确HTTPS Origin和顶层frame限制；外链进入系统浏览器，非受信页面无桥能力。任何远程页面执行权仍能影响端侧内容，CSP和依赖固定是共同前提。通知默认使用通用提示；明文预览只有用户开启且在接收端本地生成，云推送永远不携带聊天明文或私钥。

## 7. 多设备设计（P1，WP05 + WP02）

建议新增UserDevice（用户、UUID、平台、名称、最近活动、撤销时间、设备列表版本）与DevicePublicKey（device、key_version、SPKI、指纹、活动标记）。身份必须绑定登录Session/WS，不能仅相信客户端随意提交的device_id。

私聊保留一条逻辑EncryptedMessage，新增按设备的密文Envelope；群聊保留GroupMessage/用户Recipient语义，再增加设备副本。EncryptedFileKey按有效设备扩展。唯一约束覆盖“逻辑消息+设备”，保证Sender的其他有效设备和Receiver设备都获得各自独立加密副本。

服务端输出有效设备集合与版本；发送端按每台有效设备生成密文，在事务中校验集合/版本，新增/撤销导致冲突时刷新。公钥历史按设备+版本保留，信任按设备指纹表达，不能把当前user级sender_copy自动分给所有设备。

撤销应失效相关Session、关闭现有WS或在每次发送/读/同步校验设备状态；停止向其路由新副本，排除后续密钥分发，并更新设备集合版本。不能撤销设备已经保存的旧明文或旧密钥，这一边界须说明。

迁移保留旧消息与user级公钥的读取分支，所有新字段兼容旧数据；先定义legacy设备映射，再启用新协议。P0阶段不强行拆掉现有user级加密系统，P1必须在双方客户端与服务端协议都就绪后启用，禁止混合一半改造。

## 8. Windows 客户端（WP03）

- 新增完整服务Origin配置，HTTPS使用正确客户端；云模式不spawn Python。原先ICHAT_HOST/PORT和runserver只留显式开发分支。
- 离线时显示本地错误/重试页，不能只启动失败就退出；连接状态和消息重试复用Web共享模块。
- 选定并锁定安装构建工具，生成exe；只包含desktop需要的资源，排除.env、DB、media、.venv、测试证据及源码秘密。构建含版本、产品名、图标和SHA-256。
- IPC用于托盘、通知、安全文件选择/保存及KeyStore；不要暴露shell/任意路径操作。明文文件只在用户主动保存时落盘，临时文件清理与路径冲突有处理。
- 平台模块读取服务端设置/客户端策略统一判断静音和通知，点击跳转先验证当前账号及conversation_id。
- 卸载默认保留还是清除用户密钥必须明确；清除行为提示旧历史解密可能丢失，不可静默执行。

## 9. Android 技术验证与实现（WP04）

### 9.1 T22 必须先回答的问题

现有页面需要服务端Django模板渲染，不是可直接拷进Capacitor webDir的静态SPA。官方将Capacitor `server.url` 定位为开发热重载用途；不能只设置云URL后就宣称正式方案完成。[Capacitor配置说明](https://capacitorjs.com/docs/config)

推荐验证两条候选路径后写ADR：

1. **等价Android WebView外壳**：加载受信HTTPS Django页面，原生提供小范围平台能力，沿用同Origin Cookie/CSRF与WebSocket。优先验证这一低改造路径，所有桥需来源限制与安全存储。
2. **本地Capacitor前端**：提取可打包前端，新增所需JSON初始化/认证契约；明确跨Origin Cookie、CSRF、CORS、WSS和插件通信。只有完成最小登录/聊天/安全存储原型才选此路径，不能临时将所有Origin加入白名单。

这是技术建议，SRS允许等价混合方案；不要求用户先选择。Agent应以通过原型的证据做决策。若试验性使用server.url，须显式标为原型限制，不能当完整产品化验收依据。

### 9.2 原型通过条件

APK可安装→注册/登录/CSRF请求→WSS收发→相同E2EE向量→Keystore存储→重启恢复→返回键/前后台/断网恢复。任一步无法满足先修技术路径，别先堆通知和文件插件。

固定Android工具链、SDK/API范围、Gradle与JDK组合并保存构建步骤；不在本文猜测所有新工具版本。可新建 `mobile/`，生成Android工程是否提交以团队约定为准，签名密钥与local.properties不提交。

适配要点：单栏会话列表/聊天切换、触摸菜单、键盘/安全区、动态视口高度、焦点、返回层级、文件系统选择器及内容URI。网络变化/恢复只触发同一个共享连接控制器；不能前后台各创建一个socket。系统不保证后台长期WS，后台通知需明确运行条件；完全退出推送留P2并说明Android强制停止等限制。

## 10. 迁移与公共文件管理

涉及accounts/chat模型由各包提出字段设计，指定一个迁移负责人生成连续migration；禁止并行Agent占用同一迁移编号。合并后跑makemigrations一致性检查，再在PostgreSQL上从空库与旧基线各迁移一次。

拆分chat.js只能围绕新连接/同步/平台接口进行小范围抽取，保持模板加载顺序和全局入口兼容。建议共享模块归WP02、平台bridge归WP03/WP04、KeyStore契约归WP05；根package/requirements/settings/模板公共脚本区串行整合。

## 11. 测试、CI与证据（WP06/WP07）

测试分层：现有Django与Node基线→PostgreSQL/Redis真实集成→浏览器/Electron/Android跨端→安全/弱网/恢复。覆盖率分别统计语句/分支；高风险目录/函数清单要先定义，不能只用全项目单一fail-under声称全部阈值满足。

CI可按独立job分Python/Node、真实服务集成、Windows构建、Android构建；测试部署和发布使用隔离环境配置。全量UI或性能可放专用触发流程，但P0核心接口/加密/权限/幂等失败必须失败退出，不能continue-on-error。安装包与报告记录commit、版本、校验值，不上传秘密和明文业务数据。

最低关键测试：

| 场景 | 必须断言 |
| --- | --- |
| HTTP/WS同ID并发重试 | 一条逻辑消息、一次未读增长、一次新消息事件、同一正式ID |
| 同ID不同内容/会话 | 明确409冲突，不重放无关内容、不越权 |
| 成功写入后ACK丢失 | 重试得到原结果；迟到ACK与本地失败项合并 |
| 提交后Redis异常/漏推送 | 已确认数据不丢；恢复同步可补取 |
| 大量遗漏/分页并发 | 超过一页仍完整；页中插入/延迟提交无遗漏，无重复显示 |
| 历史/清空/撤回/加入 | 严格投影本人副本及合法历史；旧事件不复活删除内容 |
| 群成员变更后的重试 | 原合法提交可确认，真正新请求不能用旧分发列表 |
| 账号/设备切换与撤销 | 无串号、无旧socket、撤销会话停止后续材料分发 |
| 存储检查 | 使用测试哨兵，DB/日志/sessionStorage/普通文件无私钥和聊天明文 |
| 平台互操作 | UTF-8、SPKI、HKDF、AES tag、sender_copy、文件包装测试向量一致 |

## 12. 基线验证与交付检查

2026-10-01本地执行结果：

| 检查 | 结果 |
| --- | --- |
| `.venv/Scripts/python.exe manage.py check` | 通过，0 issues |
| `.venv/Scripts/python.exe manage.py makemigrations --check --dry-run` | 通过，No changes detected |
| `.venv/Scripts/python.exe manage.py test` | **347 tests，128.479s，OK** |
| `node chat/tests/js/private_chat_e2ee.test.js` | all tests passed |
| `node chat/tests/js/group_chat_e2ee.test.js` | all tests passed |
| `npm run test:e2ee` | 未通过执行环境：npm启动器缺npm-cli.js；直接Node分别执行两个脚本通过 |

上述仅证明现有本地基线，未证明云端部署、Android安装、正式依赖或Phase4新增功能已完成。全量测试未生成覆盖率数字，不引用历史报告的157/其他条数作为当前结果。

开发Agent优先调用项目 `.venv`；本机PATH的python指向LibreOffice运行时。未来CI应使用明确配置的Python与Node版本。最小复核命令：

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test
node chat/tests/js/private_chat_e2ee.test.js
node chat/tests/js/group_chat_e2ee.test.js
```

每个工作包另补其真实平台/依赖测试，保留失败输出与限制；本次整理未修改业务代码或生产配置。
