# Phase4 风险与缺陷登记

> 更新日期：2026-10-07；核对代码基线：`f2f60ec`。历史记录或原阶段设计；下文保留当时口径，不能作为现行完成声明。
> 当前状态见 [项目现状](../current-status.md)；文档用途与归档规则见 [文档维护索引](../documentation-status.md)。

## 本次更新

原 7 项风险均已进入已闭环任务的修复与回归证据链。这里的“处置完成”只针对原缺陷，不替代 Android、多设备、崩溃恢复等新范围的验收。

| 编号 | 当前处置 | 证据入口 |
| --- | --- | --- |
| R-01 | 权限/一致性先于幂等，外会话 ID 返回 409 | [#159](https://github.com/HeanX/iChat_Pro/issues/159)、messaging.py |
| R-02 | 合法群重放先于当前成员版本/密钥覆盖处理 | [#159](https://github.com/HeanX/iChat_Pro/issues/159)、PR #262/#264 |
| R-03 | HTTP/WS 文本/文件/转发共用服务和 viewer 投影 | [#163](https://github.com/HeanX/iChat_Pro/issues/163)、PR #260/#262 |
| R-04 | created/replayed 区分，零重复密钥/未读/广播/事件 | [#159](https://github.com/HeanX/iChat_Pro/issues/159)、PR #264/#266 |
| R-05 | Redis cache 用户连接计数替代 group_channels | [#158](https://github.com/HeanX/iChat_Pro/issues/158)、test_protocol.py、真实依赖 #195；worker 崩溃遗留计数边界需另验 |
| R-06 | API 401 JSON，WS 4401，客户端认证终态 | [#158](https://github.com/HeanX/iChat_Pro/issues/158)、middleware.py |
| R-07 | DB URL 非法启动失败、生产代理/CSRF/静态/媒体配置 | [#138](https://github.com/HeanX/iChat_Pro/issues/138)、settings.py、部署证据 |

原始静态取证如下；当时的“待修复”与行号保留，不能据此判断当前代码仍含这些缺陷。

> 复核日期：2026-10-01；代码基线：main `9d0a660`（PR #206 合并后）。
> 本文对 [technical-design.md §2.1](technical-design.md) 列出的 7 个风险点逐条做静态代码取证。
> 以下初次取证结论与代码行号适用于 2026-10-01 的 9d0a660，不是当前 main。2026-10-07 的处置结果见文首表；保留旧证据用于追溯。

## 复核结论总览

| 编号 | 摘要 | 结论 | 处置任务 |
| --- | --- | --- | --- |
| R-01 | 私聊幂等命中先于会话/权限校验 | 确认 | P4 T32 |
| R-02 | 群聊幂等查询在成员版本/recipients 校验之后 | 确认 | P4 T32 |
| R-03 | HTTP 与 WS 私聊序列化路径漂移 | 确认 | P4 T36 |
| R-04 | 幂等重放仍重复广播、无 created/replayed 区分 | 确认 | P4 T32/T36 |
| R-05 | 在线状态依赖非标准 `group_channels`，异常被吞 | 确认 | P4 T09/Test T13 |
| R-06 | API 认证失效返回 HTML 重定向而非 401 JSON | 确认 | P4 T31 |
| R-07 | 生产配置缺口（代理信任/CSRF/静态收集/URL 静默回退） | 确认 | P4 T11（WP01） |

7 项全部确认，无排除项。以下为逐条证据。

---

## R-01 私聊幂等命中先于会话/权限校验

- **原始描述**（technical-design §2.1.1）：私聊创建方法在会话/权限校验前按 `(sender_id, client_message_id)` 找旧消息；同一 ID 携带不同会话/接收者可能错误重放。
- **代码证据**：`chat/consumers.py:415-433`（`create_private_message`）。
  - L418-425：进入事务后第一步即按 `(sender_id, client_message_id)` 查询旧消息，命中直接 `return cls.serialize_private_message(existing)`；
  - L427-433：会话加载（`select_for_update`）发生在其后；
  - L435-455：成员数、双方成员资格、拉黑校验均在其后。
- **影响**：重放请求即使携带不同的 `conversation_id`/`receiver_id`（甚至是发送者无权访问的会话），也直接拿到原消息的序列化结果，不产生 `409 idempotency_conflict` 或权限错误；安全校验可被幂等命中绕过。返回结果未做 viewer 投影（无 `viewer_id`，L425），返回的是接收者副本密文。
- **处置建议**：幂等命中前先完成身份与请求一致性校验（规范化请求摘要比对），冲突返回 409；对应 P4 T32 统一服务层改造，需补"同 ID 不同会话/内容"负向测试。

## R-02 群聊幂等查询在成员版本/recipients 校验之后

- **原始描述**（§2.1.2）："已提交、ACK 丢失、随后成员变更"可能无法获得原确认。
- **代码证据**：`chat/consumers.py:836-910`（`create_group_message`）。
  - L866-867：`membership_version` 不一致即抛 `membership_conflict`；
  - L869-871：recipients 与当前有效成员不符即抛 `recipients_mismatch`；
  - L882-888：幂等查询（`(sender_id, client_message_id)`）在上述校验之后才执行。
- **影响**：客户端 ACK 丢失后用**原请求**重试时，若期间群成员发生变化（`membership_version` 递增或成员集合变化），重试命中 L866/L871 的错误路径，永远无法取回原消息确认；客户端只能把该消息永久标为失败，与 R-04 叠加还可能诱导客户端用新 ID 重发造成重复消息。
- **处置建议**：校验顺序改为"结构校验 → 当前访问身份 → 幂等一致性识别 → 才是新消息的分发版本校验"（technical-design §4.3 已给出事务顺序）；对应 P4 T32。

## R-03 HTTP 与 WS 私聊序列化路径漂移

- **原始描述**（§2.1.3）：WS 私聊分别按发送者/接收者序列化；HTTP 私聊直接推送创建方法返回的通用 message，再给发送者序列化响应。
- **代码证据**：
  - WS：`chat/consumers.py:120-135`——发送者取 `serialize_private_message_for_viewer(message_id, sender)`（L122-125，含 sender_copy 投影），接收者推送单独用 `receiver_id` 投影（L126-134）。
  - HTTP：`chat/views.py:2457-2477`（`send_private_message_view`）——L2458 调 `create_private_message` 得到**通用序列化**（内部 `serialize_private_message(existing)` 无 `viewer_id`），L2467-2471 把它原样 `group_send` 给接收者；发送者的 HTTP 响应才重新按 viewer 投影（L2473-2477）。
  - 群聊 HTTP（L2504-2511）已按 recipient 投影，与 WS 一致。
- **影响**：当前通用序列化恰好等于接收者副本（`use_sender_copy=False`），行为暂未出错；但私聊存在两条规则不同的代码路径，`sender_copy`、文件 key 投影（`_build_file_payload` 的 holder 选择）等语义一旦调整极易单边修改造成泄漏（如接收者拿到 sender_copy）。
- **处置建议**：P4 T36 统一服务层返回 `{viewer payloads}`，入口只做包装；补"双方不可读取另一份密文"的测试。

## R-04 幂等重放仍重复广播、无 created/replayed 区分

- **原始描述**（§2.1.4）：重放虽可能不重复入库，入口仍可能重复广播；服务端应区分 created/replayed，不重复计数、生成同步事件和通知。
- **代码证据**：
  - WS 私聊：重放返回后（`consumers.py:424-425`），`receive_json` 继续执行 L130-134——发送 `message.single.accepted` 并向接收者 `group_send message.single.new`。
  - WS 群聊：重放返回 `_build_group_accepted`（L887-888）后，L141-146 向**全部 recipients** 再次广播 `message.group.new`。
  - HTTP 私聊：重放同样走 L2467-2471 推送，且返回 201（无 created/replayed 区分）；HTTP 群聊同理（L2504-2513）。
- **影响**：同一逻辑消息的每次重试都会触发对端的重复实时推送；未来引入同步事件/通知（P4 T34 的 `ConversationEvent`）时，若不在服务层区分 created/replayed，会为每条重放重复生成事件与未读副作用。当前依赖客户端按 `message_id` 去重兜底。
- **处置建议**：服务层返回 `created|replayed` 标记；重放只补发确认，不产生新事件/广播/未读；对应 P4 T32/T36。

## R-05 在线状态依赖非标准 `group_channels`，异常被静默吞掉

- **原始描述**（§2.1.5）：在线状态尝试调用非标准 `group_channels` 并在异常时继续；真实 Redis、多连接关闭和多进程需要专门验证。
- **代码证据**：`chat/consumers.py:79-84`（`disconnect`）——`remaining = await self.channel_layer.group_channels(...)`，`except (AttributeError, Exception): pass`。
- **影响**：
  - 内存后端（本地开发/当前测试）没有可靠的 `group_channels` 语义，异常被吞后 `has_other_sessions` 恒为 False → 同一用户最后一个标签页关闭即判定离线，多标签页场景下用户会被错误标记 offline；
  - 多进程部署（Daphne 多 worker）下连接分散在不同进程，group 成员查询结果依赖后端实现；
  - 当前 347 条测试在内存通道下通过，不能推定生产 Redis 行为正确。
- **处置建议**：P4 T09 接入真实 Redis 后用 Test T13/T14 专项验证；必要时改为基于 `UserPresence`/连接计数的显式方案，删除对非标准 API 的依赖与宽泛 except。

## R-06 API 认证失效返回 HTML 重定向而非 401 JSON

- **原始描述**（§2.1.6）：API 使用 `login_required`，失效时可能重定向 HTML 登录页；新客户端应识别 401/4401/登录重定向，API 应逐步统一 JSON 认证错误。
- **代码证据**：
  - `chat/views.py:14` 引入 `login_required`；全部 API 视图使用 `@login_required(login_url='login')`（如 `send_private_message_view` 前 L2427、`send_group_message_view` L2480，另有数十处）；
  - `chat/urls.py` 全部 `/api/` 路由均指向这些视图；
  - WS 侧已有明确处理：未认证关闭码 4401（`consumers.py:33`、L39-41）。
- **影响**：Session 过期后，XHR/fetch 收到 302 → 跟随得到登录页 HTML，`response.json()` 解析异常或被静默当作成功；对 Android/Electron 新客户端，无法可靠区分"未登录"与"服务器错误"。
- **处置建议**：P4 T31 错误码规范中定义 `authentication_required`（HTTP 401 / WS 4401）；过渡期客户端需识别重定向响应。注意修改认证装饰器属跨模块变更，与 WP02 的服务层改造合并实施。

## R-07 生产配置缺口

- **原始描述**（§2.1.7）：生产设置已有 Secure Cookie/HSTS，但缺少完整代理信任、静态收集和私有媒体部署方案；非法 DB URL 静默回 SQLite。
- **代码证据**：`ichat_pro/settings.py`。
  - L100-131：`DATABASE_URL` 存在但解析失败时静默回退 SQLite（else 分支），生产误配置不会启动失败；
  - 全文无 `SECURE_PROXY_SSL_HEADER`（Nginx 终止 TLS 后 Django 需显式信任代理头，否则 `SECURE_SSL_REDIRECT`/CSRF 判定失真）；
  - 无 `CSRF_TRUSTED_ORIGINS` 配置项；
  - 无 `STATIC_ROOT`（`STATICFILES_DIRS` 仅开发目录），`collectstatic` 不可用；
  - `MEDIA_ROOT` 固定为仓库内 `BASE_DIR / 'media'`（L216），不支持持久卷挂载；
  - 已有部分：DEBUG=False 时强制 HTTPS/HSTS/Secure Cookie（L239-258）。
- **影响**：仅设置 `DJANGO_DEBUG=False` 不足以生产就绪；反代场景 CSRF 校验将失败或 SSL 重定向循环；静态与密文文件无法按部署拓扑分离。
- **处置建议**：P4 T11/WP01 按技术文档 §3.1 的配置契约落地：URL 解析规范化 + 非法值 fail-fast、`DJANGO_CSRF_TRUSTED_ORIGINS`、`SECURE_PROXY_SSL_HEADER`、`STATIC_ROOT`/`DJANGO_MEDIA_ROOT` 环境化。

---

## 附带记录（低风险）

1. **注释乱码**：`chat/consumers.py:447`（`P1 fix 鈥?matches`）、`chat/consumers.py:860` 存在历史编码乱码注释。仅影响可读性，随相关任务顺手修复。
2. **`_recall_private_message` 不校验会话成员**：`consumers.py:636-664` 仅按 `sender_id` 取消息，撤回权限=发送者本人，未再验证当前用户会话身份——当前语义可接受（发送者必然曾是成员），但接收者撤回后的推送路由依赖 `message.receiver_id`，多设备场景（P1）需复核。登记备查，暂不列为缺陷。

## 复核方法

- 静态核对，未运行时验证并发行为；行号以 main `9d0a660` 为准。
- 与 [technical-design.md §2.1](technical-design.md) 的 7 条一一对应；本文登记后，该 7 条在 tasks.md 对应任务（T31/T32/T36/T09/T11）实施时必须逐条闭环并回填复测结论。
