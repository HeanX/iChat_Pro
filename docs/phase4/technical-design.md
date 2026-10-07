# Phase4 现行技术方案与代码入口

> 更新：2026-10-07；核对代码 f2f60ec。本文描述当前实现，并明确剩余范围。首次交接方案基线 b3d529f 及 347 条测试是历史记录，见 baseline-report.md 与 Git 历史。

## 1. 总体架构

```text
Browser / Electron Windows（Android 尚未交付）
          │ HTTPS 8443 / WSS，Session Cookie + CSRF
          ▼
     Nginx TLS / 静态资源
          ▼
     Django ASGI / Daphne loopback 8000
          │                 │                 │
 PostgreSQL 16      Redis 6 系列         持久化 media
 业务/密文/事件      分发/缓存             鉴权密文文件
```

PostgreSQL 保存业务、密文与事件，Redis 负责实时分发/缓存；Redis 推送成功不等于客户端持久收取。当前服务器是 systemd 托管的 Daphne 实例；多 worker 水平扩展是后续容量方案，不是本次已做部署。

## 2. 实现与剩余范围

| 区域 | 已有实现与代码入口 | 剩余范围 |
| --- | --- | --- |
| 配置 | settings.py 正确解析 DATABASE_URL，非法配置启动失败；CSRF/代理/静态媒体根目录 | 独立测试环境与完整发布门禁 #176/#177 |
| 依赖 | psycopg 3.3.6、channels-redis 4.3.0、redis 客户端 8.1.0 | 升级须走锁定与双后端回归 |
| 认证 | ASGI Origin + Session；API 401 JSON、WS 4401；协议不支持 4003 | Android Cookie/Origin 联调、设备撤销 |
| 模型 | Conversation、消息/逐 viewer 副本、EncryptedFile/Key、ConversationEvent | 设备级身份和副本、变更事件 |
| 消息写入 | chat/services/messaging.py，HTTP/WS 文本/文件/转发共用事务 | 新写入口不得绕过服务 |
| 增量读取 | sync 视图 + chat/services/sync.py 签名游标，历史同源可见性 | 历史事件回填和撤回/删除/成员事件 |
| 共享前端 | chat-connection.js：状态机、退避、ACK、内存 outbox、walker/queue/seen/timeline | 崩溃/重启待发箱持久化、Android 接线 |
| 行渲染 | message-accepted.js 与 chat.js：晚 ACK 行身份、排序、邻居分组重建 | 继续在接线/DOM 回归验证新分支 |
| 密钥 | key-manager.js：IndexedDB 不可导出 CryptoKey，公开记录、公钥缓存 | 平台 KeyStore、设备身份/迁移 |
| 草稿/AI | chat.js 安全 KV 缓存、safeStorage 写入/迁移、epoch、按键失败横幅 | Web 回退与旧迁移失败明文边界仍存在 |
| Windows | desktop：默认云模式、显式 dev、安装、品牌、托盘、IPC、通知 | #145 最终 UI 验收、#146 文件集成 |
| Android | 尚无原生工程/APK | #149–#157 技术验证与实施 |
| CI | django.yml 四 job，真实 PG/Redis、Node、Windows Chromium/品牌/NSIS | required checks、覆盖率、扫描、Android、部署冒烟 |

原风险 R-01–R-07 已分别进入 #159/#163/#158/#138 的整改证据链；原始代码行号只适用于初次取证基线。现行处置与历史证据见 [风险登记](risk-register.md)。

## 3. 云端配置与部署

| 配置 | 现行契约 |
| --- | --- |
| DJANGO_DEBUG / DJANGO_SECRET_KEY | 生产关闭 DEBUG 且秘密必填；读取进程环境，不自动读 .env |
| DJANGO_ALLOWED_HOSTS | 精确主机名；当前 chat.20060810.xyz |
| DJANGO_CSRF_TRUSTED_ORIGINS | 完整带 8443 的 HTTPS Origin |
| DATABASE_URL | PostgreSQL 生产连接；百分号编码/端口/参数解析，错误拒绝启动 |
| REDIS_URL | 生产 loopback；Django cache 与 Channel Layer 共用 Redis 服务 |
| DJANGO_STATIC_ROOT / DJANGO_MEDIA_ROOT | 静态收集与业务媒体持久目录分离 |
| SECURE_PROXY_SSL_HEADER | DEBUG=False 时信任受控代理的 X-Forwarded-Proto=https；代理端清理用户伪造头 |
| ICHAT_SERVER_URL | 桌面云 Origin；defaults.json 内建，显式环境变量可覆盖 |

TLS 使用 8443，443 属共存代理。私有 uploads 不以 /media 公开服务。生产运行、证书续期 hook 和端口约束见 [部署架构](deploy-architecture.md) 与 [Runbook](../../deploy/runbook.md)。

发布统一从仓库外 `/opt/ichat/bin/deploy.sh`，避免回滚同时降级部署工具。支持 `--commit <sha>`；readiness 经 Nginx TLS 并检查 DB+cache JSON；成功追加 DEPLOYMENTS.log。数据库不随应用自动降级。备份含 DB/media/校验和/来源 SHA；已有同主机独立库与目录演练，跨实例恢复未验收。

## 4. 发送事务与幂等

HTTP 与 WebSocket 共用服务，文件 single/group、转发 single/group 均接入。发送事务持会话行锁，先验证当前访问身份/会话权限，再查询原 client_message_id 与规范化持久内容；冲突 409，不泄漏其他会话的原消息。

合法群聊重放先于当前 membership_version/recipients 校验；新消息才检查当前成员集合。sender_copy 摘要只取持久字段，不因回显额外键冲突。唯一约束竞速使用内层 atomic savepoint，IntegrityError 回滚后外层健康事务恢复查询。

文件密钥的规范化、材料校验、成员覆盖及写入在服务 created 分支内。拒绝与重放零密钥副作用。转发允许 owner 或合法 key-holder，不强制附件与目标会话相同；成员密钥覆盖始终检查。created 才递增未读/last_message、广播、生成事件。

HTTP 认证错误为 401 JSON，WS close 4401；业务错误从 chat/errors.py 注册表映射。完整载荷及错误表见 [protocol.md](protocol.md)。

## 5. 持久事件与补取

迁移 `0021_conversation_sync_sequence_conversationevent` 新增 Conversation.sync_sequence 和 ConversationEvent；唯一 `(conversation, sequence)`，同事务分配，只存引用/元数据。P0 仅新增消息，文本/文件/转发全部覆盖，合法重放不生成新事件。

`GET /api/conversations/{id}/sync/` 支持 limit 1–200（默认 100）；HMAC-SHA256 游标绑定用户与会话、TTL 7 天。快照分页按 sequence 推进，整 walk 完成游标释放 high_water，下次只取增量。空 items 不能提前结束，has_more 按原事件位置计算。当前权限投影同历史：个人删除、清空、自动删除截断、私聊拉黑、群 pre-join 与 viewer 密文。

前端整 walk 经共享队列逐条 await 应用；整页成功再存 cursor；失败保留旧 cursor；410 清除旧 token 并重新快照一次。安全页数暂停之后自动续跑，实时消息可以在暂停期间应用，时间线排序修正到 `(created_at, id)`。不是撤回/删除变更日志，也不回填上线前历史。

## 6. 连接与消息状态

连接 connecting/online/reconnecting；指数退避 500ms×2、抖动后上限 25s；networkUp 立即尝试，心跳 25s、pong 等待 10s。auth_required/unsupported 停止同条件无限重连。

消息 sending→sent→delivered→read 单调；只有未确认 sending 可失败，迟到 ACK 可将 failed 恢复 sent。envelope 在传输之前 track，WS 成功后 armAck，HTTP accepted 确认；重发使用相同 client_message_id，最多 5 次尝试。离线加密需要已缓存公钥，缓存信任变更必须按加密模块规则处理。

待发箱只在内存：托盘 hide/show、运行中离线横幅不销毁；页面重载或进程重启不保证未确认项恢复。晚 ACK 应用服务端时间，临时 ID 更新为正式 ID，按稳定 data-row-message-id 定位完整行、排序后重建邻居分组，避免按数组位置覆盖别的消息。

## 7. 平台与隐私

桌面云模式不启动本地 Django；startup 离线页和运行中非破坏横幅区分。最小化有托盘则 hide，恢复同一窗口；托盘失败正常最小化；X/退出清理进程、托盘、定时器。单实例锁在 whenReady/探测/spawn 之前。

safeStorage IPC 保护待导出 JWK、草稿、AI 历史；上限与迁移边界见 protocol §7。不可导出 CryptoKey 限制直接提取，不阻止恶意同源脚本使用；Web 回退和旧明文迁移失败须如实声明。默认卸载保留用户数据，用户显式选择删除才清理。

通知设置未知/损坏时关闭，未知会话先读取元数据检查静音；预览关闭用通用文案，自发/sync 补取/已聚焦不通知。点击恢复同一窗口定位会话，Windows AUMID 与 build.appId 一致。布局修复已回归，但 #145 最后安装版 UI 记录仍待补。

## 8. Android 与多设备待办

Android 先做 T22 技术验证：模板、HTTPS、Session/CSRF、WSS、平台安全存储和生命周期必须同时可用。其余 APK、返回键、网络恢复、通知、文件和权限保持未完成状态，不能用窄屏浏览器 fixture 代替原生验收。

当前 UserPublicKey 按 user/key_version，不是设备级身份。设备列表、独立密钥、撤销和逐设备副本 #167–#169 需单独设计迁移；共享账户云端数据不等于新设备有历史解密能力。KeyStore 和历史迁移在 #166/#170 完整验收前不得宣传完成。

## 9. 验证与交付

基线 CI run 37630595919：SQLite 与 PostgreSQL/Redis 各 404 条 Django 测试；Node 加密/连接/ACK/缓存/桌面/通知套件；Windows Chromium 36/36、品牌资源与 NSIS 24/24，全绿。迁移一致性已检查。

尚无覆盖率阈值、静态/安全扫描及 Android 构建全链；main required_status_checks 未配置，CI 失败并非强制禁止合并。readiness 通过仅证明健康依赖；登录/核心接口失败阻断与跨实例恢复仍待验收。详细完成判定见 [任务清单](tasks.md) 与 [项目现状](../current-status.md)。
