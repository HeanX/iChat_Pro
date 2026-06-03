# iChat Pro 实时通信与端到端加密消息协议设计文档

> 状态：Draft v0.2
> 适用范围：T10、T11、T12、T14、T15
> 评审对象：成员 A（公钥管理）、成员 B（消息模型）、成员 D（前端接入）
> 对齐说明：T30 协议收敛任务已完成（2026-06-03）。代码实现、测试和前端 WebSocket 客户端已与本草案对齐。私聊幂等、事件名称、群成员变化通知和载荷限制均已收敛至本协议版本。

## 1. 文档目的

本文档统一 iChat Pro 一期 WebSocket 消息格式，作为 Django Channels、消息模型和浏览器端加密模块的联调依据。

一期协议遵循以下边界：

1. 客户端负责生成密钥、派生会话密钥、加密和解密消息。
2. 服务端负责登录校验、权限校验、密文保存、消息状态维护和实时转发。
3. WebSocket 数据包、服务端日志和数据库中不得出现消息明文、用户私钥、`session_key` 或明文 `file_key`。
4. 群聊使用逐成员加密：每位当前活跃成员只收到属于自己的密文副本。
5. 图片、文件、表情包协议暂不纳入一期 WebSocket 核心实现；后续通过加密文件接口扩展。

## 2. 一期统一结论

### 2.1 连接地址

一期使用单一 WebSocket 入口：

```text
/ws/chat/
```

原因：

1. 一个登录用户只需要维护一个连接。
2. 私聊和群聊密文都可以通过用户个人通道精准推送。
3. 群聊每位成员收到的密文不同，不适合直接向群通道广播同一载荷。
4. 浏览器端重连、状态维护和 Electron 封装更简单。

### 2.2 身份认证

一期使用 Django Session Cookie 鉴权。用户登录后，浏览器访问同源 `/ws/chat/` 时自动携带 Session Cookie。

```text
HTTP 登录成功
→ 浏览器持有 Session Cookie
→ 建立 /ws/chat/
→ Channels AuthMiddlewareStack 读取登录用户
→ 匿名用户拒绝连接
```

一期不在 URL 查询参数中传递 Token，例如不使用：

```text
/ws/chat/?token=xxx
```

URL 中的 Token 容易出现在日志、历史记录或调试信息中。后续如改为独立前端或多端认证，再设计短期 WebSocket Ticket。

### 2.3 Channels 通道组

| 通道组 | 一期用途 |
| --- | --- |
| `user_{user_id}` | 必须。向指定用户推送私聊密文、群聊个人密文副本、状态变化和系统通知 |
| `group_{group_id}` | 可选。只用于不含敏感载荷的群成员变化通知 |
| `conversation_{conversation_id}` | 暂不使用。避免误广播接收者专属密文 |

客户端不得通过伪造 `sender_id`、`receiver_id` 或通道组名称绕过权限校验。服务端始终以当前 Session 用户和数据库关系为准。

## 3. 通用消息信封

所有 JSON 数据包使用统一外层结构：

```json
{
  "protocol_version": "1.0",
  "event": "message.single.send",
  "request_id": "0ca8cc6c-9d91-4b34-86bc-2b89d0122f03",
  "sent_at": "2026-06-01T08:30:00Z",
  "data": {}
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `protocol_version` | string | 是 | 一期固定为 `"1.0"` |
| `event` | string | 是 | 事件名称，使用点号分层 |
| `request_id` | UUID string | 客户端请求必填 | 关联请求、响应和错误，便于排查问题 |
| `sent_at` | ISO 8601 string | 服务端推送必填 | 服务端生成的 UTC 时间；客户端上传时间仅作调试参考 |
| `data` | object | 是 | 具体业务载荷 |

服务端生成的 `created_at`、`sender_id` 和消息状态是权威值。客户端不得自行决定这些字段。

## 4. 基础连接事件

### 4.1 连接成功

服务端接受连接后推送：

```json
{
  "protocol_version": "1.0",
  "event": "connection.ready",
  "request_id": null,
  "sent_at": "2026-06-01T08:30:00Z",
  "data": {
    "user_id": 7,
    "heartbeat_interval_seconds": 30
  }
}
```

### 4.2 心跳

客户端发送：

```json
{
  "protocol_version": "1.0",
  "event": "connection.ping",
  "request_id": "b918c203-9945-4314-a51e-3a00acf665ce",
  "data": {}
}
```

服务端返回：

```json
{
  "protocol_version": "1.0",
  "event": "connection.pong",
  "request_id": "b918c203-9945-4314-a51e-3a00acf665ce",
  "sent_at": "2026-06-01T08:30:30Z",
  "data": {}
}
```

## 5. 私聊密文消息

### 5.1 客户端发送私聊消息

事件：`message.single.send`

```json
{
  "protocol_version": "1.0",
  "event": "message.single.send",
  "request_id": "6314999d-e411-46c2-8c26-1ddd086d9472",
  "data": {
    "client_message_id": "8578733a-60c4-4899-a750-5454d6920652",
    "conversation_id": 1001,
    "receiver_id": 2,
    "message_type": "text",
    "ciphertext": "base64密文",
    "nonce": "base64随机数",
    "auth_tag": "base64认证标签",
    "algorithm": "AES-256-GCM",
    "sender_key_version": 1,
    "receiver_key_version": 3
  }
}
```

| 字段 | 说明 |
| --- | --- |
| `client_message_id` | 客户端生成的 UUID。用于防止断线重试造成重复入库 |
| `conversation_id` | 私聊会话 ID |
| `receiver_id` | 接收方用户 ID |
| `message_type` | 一期 WebSocket 核心实现仅支持 `text` |
| `ciphertext` | Base64 编码的 AES-GCM 密文，不包含认证标签 |
| `nonce` | Base64 编码的 12 字节随机数；同一密钥下不得重复 |
| `auth_tag` | Base64 编码的 16 字节 AES-GCM 认证标签 |
| `algorithm` | 一期固定为 `AES-256-GCM` |
| `sender_key_version` | 发送方用于本条消息的公钥版本 |
| `receiver_key_version` | 接收方用于本条消息的公钥版本 |

服务端必须校验：

1. 当前用户已经登录，并从 Session 获取发送方 ID。
2. 当前用户和 `receiver_id` 均为该私聊会话成员。
3. 双方联系人关系允许发送私聊消息。
4. `client_message_id` 对当前用户保持幂等。
5. 密文字段完整，Base64 可解析，nonce 和 auth_tag 解码后长度正确。
6. `algorithm`、`message_type` 和密文长度符合一期白名单。
7. 公钥版本存在；服务端不解密消息。

### 5.2 服务端确认已保存

事件：`message.single.accepted`

```json
{
  "protocol_version": "1.0",
  "event": "message.single.accepted",
  "request_id": "6314999d-e411-46c2-8c26-1ddd086d9472",
  "sent_at": "2026-06-01T08:31:00Z",
  "data": {
    "client_message_id": "8578733a-60c4-4899-a750-5454d6920652",
    "message_id": 501,
    "conversation_id": 1001,
    "status": "sent",
    "created_at": "2026-06-01T08:31:00Z"
  }
}
```

发送端可以先展示本地 `sending` 状态；收到该事件后更新为 `sent`。

### 5.3 接收方收到私聊密文

事件：`message.single.new`

```json
{
  "protocol_version": "1.0",
  "event": "message.single.new",
  "request_id": null,
  "sent_at": "2026-06-01T08:31:00Z",
  "data": {
    "message_id": 501,
    "conversation_id": 1001,
    "sender_id": 1,
    "receiver_id": 2,
    "message_type": "text",
    "ciphertext": "base64密文",
    "nonce": "base64随机数",
    "auth_tag": "base64认证标签",
    "algorithm": "AES-256-GCM",
    "sender_key_version": 1,
    "receiver_key_version": 3,
    "status": "sent",
    "created_at": "2026-06-01T08:31:00Z"
  }
}
```

接收端根据 `sender_id` 和 `sender_key_version` 获取发送者公钥，在本地完成 `ECDH + HKDF + AES-GCM` 解密。

## 6. 群聊逐成员密文消息

### 6.1 客户端发送群聊消息

事件：`message.group.send`

```json
{
  "protocol_version": "1.0",
  "event": "message.group.send",
  "request_id": "6757daa3-e7bd-4903-be47-65a93f25419f",
  "data": {
    "client_message_id": "67296c03-7dd7-4943-a9f6-4d3e81b960b8",
    "group_id": 12,
    "membership_version": 9,
    "message_type": "text",
    "algorithm": "AES-256-GCM",
    "sender_key_version": 1,
    "recipients": [
      {
        "receiver_id": 1,
        "receiver_key_version": 1,
        "ciphertext": "base64发给用户1的密文",
        "nonce": "base64随机数A",
        "auth_tag": "base64认证标签A"
      },
      {
        "receiver_id": 2,
        "receiver_key_version": 3,
        "ciphertext": "base64发给用户2的密文",
        "nonce": "base64随机数B",
        "auth_tag": "base64认证标签B"
      }
    ]
  }
}
```

服务端必须校验：

1. 当前用户是该群的 `active` 成员。
2. `membership_version` 与数据库中的当前群成员版本一致；不一致时返回 `conflict`，客户端重新拉取成员和公钥后再加密。
3. `recipients` 与数据库中的当前 `active` 成员集合完全一致，包括发送者本人。
4. 已退出或被移除成员不在 `recipients` 中。
5. 每个接收者只出现一次，并拥有独立密文、nonce 和 auth_tag。
6. 每个 `receiver_key_version` 存在且属于对应接收者。
7. 群消息逻辑记录和所有接收者密文副本在同一数据库事务中保存。

### 6.2 服务端确认群消息已保存

事件：`message.group.accepted`

```json
{
  "protocol_version": "1.0",
  "event": "message.group.accepted",
  "request_id": "6757daa3-e7bd-4903-be47-65a93f25419f",
  "sent_at": "2026-06-01T08:32:00Z",
  "data": {
    "client_message_id": "67296c03-7dd7-4943-a9f6-4d3e81b960b8",
    "message_id": 888,
    "group_id": 12,
    "membership_version": 9,
    "status": "sent",
    "created_at": "2026-06-01T08:32:00Z"
  }
}
```

### 6.3 群成员收到自己的密文副本

事件：`message.group.new`

```json
{
  "protocol_version": "1.0",
  "event": "message.group.new",
  "request_id": null,
  "sent_at": "2026-06-01T08:32:00Z",
  "data": {
    "message_id": 888,
    "group_id": 12,
    "membership_version": 9,
    "sender_id": 1,
    "receiver_id": 2,
    "message_type": "text",
    "ciphertext": "base64发给当前用户的密文",
    "nonce": "base64随机数",
    "auth_tag": "base64认证标签",
    "algorithm": "AES-256-GCM",
    "sender_key_version": 1,
    "receiver_key_version": 3,
    "status": "sent",
    "created_at": "2026-06-01T08:32:00Z"
  }
}
```

服务端只能将当前接收者对应的一份密文放入推送载荷，禁止把完整 `recipients` 数组广播给其他成员。

## 7. 消息回执

一期统一使用一个回执事件，私聊和群聊共用。

### 7.1 客户端更新回执

事件：`message.receipt.update`

```json
{
  "protocol_version": "1.0",
  "event": "message.receipt.update",
  "request_id": "b4d41bf5-440c-4ed8-9916-49ee99c262b0",
  "data": {
    "conversation_type": "single",
    "message_id": 501,
    "status": "delivered"
  }
}
```

| 字段 | 可选值 |
| --- | --- |
| `conversation_type` | `single`、`group` |
| `status` | `delivered`、`read` |

服务端校验当前用户确实是消息接收者，且状态只能向前推进：

```text
sent → delivered → read
```

### 7.2 服务端处理回执变化

事件：`message.receipt.updated`

```json
{
  "protocol_version": "1.0",
  "event": "message.receipt.updated",
  "request_id": "b4d41bf5-440c-4ed8-9916-49ee99c262b0",
  "sent_at": "2026-06-01T08:33:00Z",
  "data": {
    "conversation_type": "single",
    "message_id": 501,
    "user_id": 2,
    "status": "delivered",
    "updated_at": "2026-06-01T08:33:00Z"
  }
}
```

一期回执推送边界：

| 场景 | 一期处理方式 |
| --- | --- |
| 私聊 | 保存回执，并将 `message.receipt.updated` 推送给消息发送者 |
| 群聊 | 保存每位接收者的送达和已读时间，但不实时向全群广播逐成员回执 |

群聊如需展示“已读人数”，后续再增加聚合查询或聚合推送事件，避免每次成员回执都产生全群广播。

## 8. 群成员变化通知

事件：`group.members.changed`

```json
{
  "protocol_version": "1.0",
  "event": "group.members.changed",
  "request_id": null,
  "sent_at": "2026-06-01T08:34:00Z",
  "data": {
    "group_id": 12,
    "change": "member_removed",
    "actor_id": 1,
    "affected_user_id": 3,
    "membership_version": 9
  }
}
```

| `change` 可选值 |
| --- |
| `member_added` |
| `member_left` |
| `member_removed` |
| `group_dissolved` |

客户端收到成员变化通知后，应重新拉取当前群成员及其公钥。后续群消息必须按照最新 active 成员列表生成密文。

## 9. 错误响应

事件：`error`

```json
{
  "protocol_version": "1.0",
  "event": "error",
  "request_id": "6314999d-e411-46c2-8c26-1ddd086d9472",
  "sent_at": "2026-06-01T08:35:00Z",
  "data": {
    "code": "forbidden",
    "message": "无权向该会话发送消息",
    "retryable": false
  }
}
```

| 错误码 | 说明 | 是否建议重试 |
| --- | --- | --- |
| `invalid_payload` | JSON 或字段格式错误 | 否 |
| `unauthenticated` | 用户未登录或 Session 已失效 | 登录后重连 |
| `forbidden` | 无权访问会话或群聊 | 否 |
| `not_found` | 会话、群聊、用户或密钥版本不存在 | 否 |
| `not_implemented` | 事件已预留但当前任务阶段尚未实现 | 否 |
| `conflict` | 群成员列表或密钥版本已变化 | 重新拉取后重试 |
| `rate_limited` | 发送过于频繁 | 延迟重试 |
| `internal_error` | 服务端异常 | 可有限重试 |

错误响应不得回显完整密文、密钥或隐私数据。

重复提交不是普通错误。如果“当前发送者 + `client_message_id`”已经对应一条已保存消息，服务端不重复入库，直接再次返回原消息的 `message.single.accepted` 或 `message.group.accepted`。响应中的 `request_id` 使用本次重试请求的值，`message_id` 和 `created_at` 使用首次保存时的权威值。

## 10. 断线重连与幂等

1. 客户端使用指数退避重连，`static/js/chat.js` 中的 `connectWebSocket()` 实现基础重连机制。
2. 重连成功后等待 `connection.ready`，再恢复待发送消息。
3. 每条消息必须携带 `client_message_id`。
4. 服务端应对“当前发送者 + `client_message_id`”建立唯一约束或等价幂等校验。
5. 客户端只重试尚未收到 `accepted` 的消息。
6. 历史消息通过 HTTP 分页接口补齐，WebSocket 只负责实时增量。
7. 客户端收到实时消息或历史消息后，按服务端 `created_at` 排序；时间相同时再按 `message_id` 排序，避免并发推送或重连补齐造成展示乱序。

一期使用 SQLite 和数据库唯一约束实现幂等，便于演示和验证。后续部署到更高流量环境时，可以使用 Redis TTL 缓存或独立幂等记录表降低长期索引压力。

## 11. 加密参数约定

一期浏览器端使用 `static/js/private-chat-e2ee.js`（私聊）、`static/js/group-chat-e2ee.js`（群聊）和 `static/js/key-manager.js`（身份密钥管理）实现端到端加密，使用以下算法组合：

| 参数 | 一期约定 |
| --- | --- |
| 身份密钥协商 | `ECDH P-256` |
| 密钥派生 | `HKDF-SHA-256` |
| 私聊 HKDF context | `single:{conversation_id}:{sender_id}:{receiver_id}:{sender_key_version}:{receiver_key_version}` |
| 群聊 HKDF context | `group:{group_id}:{membership_version}:{sender_id}:{receiver_id}:{sender_key_version}:{receiver_key_version}` |
| HKDF salt | `SHA-256(UTF-8(HKDF context))` |
| HKDF info | `chat-message-encryption-v1` |
| 消息加密 | `AES-256-GCM` |
| nonce | 每条消息随机生成 12 字节 |
| auth_tag | 16 字节，和 ciphertext 分列传输及保存 |
| 二进制编码 | Base64 |

注意：Web Crypto API 返回的 AES-GCM 加密结果默认将认证标签追加在密文末尾。发送前需要按现有前端模板拆分为 `ciphertext` 和 `auth_tag`，接收后再拼接解密。

一期为降低联调复杂度，使用可复现的上下文哈希作为 HKDF salt，而不是为每条消息传输新的随机 salt。不同方向、不同密钥版本和不同群成员版本会派生不同密钥；同一派生密钥下仍必须依靠每条消息唯一的随机 nonce 保证 AES-GCM 安全。后续如升级为消息级密钥轮换或 Double Ratchet，应重新设计每条消息的派生参数。

## 12. 安全边界说明

1. 一期使用静态身份密钥执行 ECDH，不实现完整前向保密。
2. 消息明文、用户私钥、ECDH 共享秘密、会话密钥和明文文件密钥不得进入服务端、数据库或日志。
3. 服务端能够看到通信双方、通信时间、消息类型、密文长度等元数据。
4. 如果服务器下发的 Web 前端代码被恶意篡改，纯 Web 客户端的端到端加密仍可能被破坏。
5. 文件消息不纳入一期 WebSocket 核心协议；后续使用独立 `file_key` 和加密文件接口扩展。
6. 一期方案适用于课程项目演示，不宣称达到 Signal Protocol 或 Double Ratchet 的安全级别。

## 13. 群聊规模与载荷限制

逐成员加密会使 `message.group.send` 的载荷随群成员数量线性增长。现有需求文档要求校验群人数上限，但尚未给出具体数值。

一期建议采用以下默认限制，并由全员评审后写入配置：

| 参数 | 一期建议默认值 | 说明 |
| --- | --- | --- |
| 群聊 active 成员上限 | `50` 人 | 控制逐成员加密计算量和 JSON 载荷大小 |
| 文本明文上限 | `4 KiB` UTF-8 字节 | 加密前在客户端校验，服务端同时限制密文长度 |
| 单个 WebSocket JSON 包上限 | `512 KiB` | 超限返回 `invalid_payload`，不继续解析或入库 |

一期不提供 HTTP POST 作为超大群聊消息的降级发送通道。HTTP POST 只能改变传输方式，不能解决逐成员密文造成的 O(N) 载荷增长。若后续需要支持更大群聊，应评估 Sender Key 或分批提交等方案。

## 14. T10 实现边界

T10 只实现 WebSocket 基础设施，不提前依赖成员 B 尚未完成的消息模型：

1. 安装并配置 Django Channels。
2. 配置 `/ws/chat/` 路由和 `AuthMiddlewareStack`。
3. 匿名用户拒绝连接。
4. 已登录用户加入 `user_{user_id}`。
5. 支持 `connection.ready`、`connection.ping`、`connection.pong`。
6. 对尚未实现的业务事件返回明确错误。
7. 编写连接、鉴权和心跳测试。

T11、T12 再接入私聊和群聊消息保存、转发和回执。

## 15. 待团队确认项

以下事项需要在实现 T11、T12 前由相关成员确认：

| 编号 | 事项 | 建议方案 | 需要确认的人 |
| --- | --- | --- | --- |
| C-01 | 公钥算法命名 | 一期统一使用 `ECDH-P256`，后续再评估 X25519 | A、C |
| C-02 | 公钥版本字段 | 统一使用 `sender_key_version`、`receiver_key_version` | A、B、C |
| C-03 | 消息幂等字段 | 消息表增加 `client_message_id`，并对发送者建立唯一约束 | B、C |
| C-04 | 群成员版本 | 群成员变化时维护 `membership_version`，避免使用过期成员列表发消息 | B、C |
| C-05 | 群消息发送者副本 | `recipients` 必须包含发送者本人，便于历史消息恢复 | B、C |
| C-06 | Session 鉴权 | 一期采用 Django Session Cookie，不使用 URL Token | C、D |
| C-07 | 文本消息范围 | 一期 WebSocket 核心链路先完成 `text`，文件消息后续扩展 | 全员 |
| C-08 | `algorithm` 值 | 协议统一写 `AES-256-GCM`，数据库兼容旧文档中的 `AES-GCM` | A、B、C |
| C-09 | 群聊规模限制 | 默认 active 成员上限 `50` 人、文本 `4 KiB`、单包 `512 KiB` | 全员 |

## 16. 与现有文档的差异说明

现有设计文档存在若干不同版本的建议。本草案为一期落地方案：

| 现有差异 | 一期统一方案 |
| --- | --- |
| `/ws/chat/`、`/ws/group-chat`、按房间拆分路径并存 | 统一为 `/ws/chat/` 单入口 |
| URL Token 与 Django Session 并存 | 当前 Django 模板项目使用 Session Cookie |
| `ECDH-X25519` 与 `P-256` 并存 | 现有浏览器模板已经实现 `ECDH P-256`，一期沿用 |
| `sender_public_key_version` 与 `sender_key_version` 并存 | 统一为较短的 `sender_key_version` |
| 群聊通道广播与逐成员密文并存 | 每份密文通过 `user_{user_id}` 精准推送 |
| WebSocket 与 HTTP 均可发送消息 | 一期实时发送走 WebSocket；历史分页、公钥查询和成员列表走 HTTP |
| HKDF salt 仅使用 `conversation_id` | 使用包含会话、方向、密钥版本和群成员版本的上下文哈希 |

## 17. 主文档引用建议

本文档适合作为接口设计材料或报告附录，不建议全文放入软工大作业主文档。主文档可在系统架构、接口设计、时序图和系统测试章节摘取以下核心结论：

1. 实时通信采用 Django Channels 和单一 WebSocket 入口 `/ws/chat/`。
2. 浏览器端完成 `ECDH P-256 + HKDF-SHA-256 + AES-256-GCM` 加密与解密。
3. 服务端仅保存和转发密文，不保存消息明文、用户私钥或会话密钥。
4. 群聊采用逐成员独立密文副本，并通过个人通道精准推送。

## 18. 评审检查表

- [ ] A 确认公钥上传、查询和版本字段。
- [ ] B 确认私聊消息表和群聊接收者密文表字段。
- [ ] B 确认 `client_message_id` 和 `membership_version` 是否进入模型。
- [ ] D 确认前端统一连接 `/ws/chat/` 并使用 Session Cookie。
- [ ] C 按本草案完成 T10 Channels 基础设施。
- [ ] 全员确认一期先支持文本密文实时通信，文件协议后续扩展。
- [ ] 全员确认群聊规模、文本长度和单包大小限制。
