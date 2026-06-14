# iChat Pro API 接口文档

> 版本：2026-06-11  
> 来源：`accounts/urls.py`、`chat/urls.py`、`chat/routing.py`、`accounts/views.py`、`chat/views.py`、`chat/consumers.py` 与后端测试用例。  
> 说明：本机 `gh issue list` 可执行，但当前环境未登录 GitHub，返回 `HTTP 401: Requires authentication`，因此本文以代码与现有后端设计文档为准。

## 1. 通用约定

### 1.1 基础信息

| 项目 | 说明 |
| --- | --- |
| Base URL | 本地开发默认 `http://127.0.0.1:8000` |
| 认证方式 | Django Session Cookie |
| CSRF | 页面内 POST/PUT/DELETE 请求需携带 Django CSRF Token |
| 数据格式 | API 默认使用 JSON 请求体与 JSON 响应；少量页面表单接口使用 `application/x-www-form-urlencoded` |
| 未登录 | 多数接口由 `@login_required` 保护，未登录通常重定向到 `/login/` |
| 时间格式 | ISO 8601 字符串，例如 `2026-06-11T20:30:00+08:00` |

### 1.2 错误格式

常见错误响应：

```json
{
  "error": "错误说明或错误码"
}
```

部分发送消息接口会返回更结构化的错误：

```json
{
  "error": "conversation_forbidden",
  "detail": "You have blocked this user."
}
```

常见状态码：

| 状态码 | 含义 |
| --- | --- |
| 200 | 请求成功 |
| 201 | 创建成功 |
| 302 | 未登录时跳转登录页 |
| 400 | 请求体、参数或业务状态非法 |
| 403 | 无权限、非联系人、拉黑关系、群权限不足 |
| 404 | 资源不存在或当前用户不可见 |
| 405 | HTTP 方法不允许 |
| 409 | 资源冲突，如重复成员、重复撤回、群成员版本不一致 |

### 1.3 端到端加密字段约定

后端只存储和转发密文，不生成、不保存私钥、会话密钥或明文。

| 字段 | 说明 |
| --- | --- |
| `ciphertext` | 加密后的消息密文 |
| `nonce` | 加密随机数 |
| `auth_tag` | AEAD 认证标签 |
| `algorithm` | 支持 `AES-256-GCM`、`AES-128-GCM`、`ChaCha20-Poly1305`；公钥接口当前限定 `ECDH-P256` |
| `sender_key_version` | 发送者公钥版本 |
| `receiver_key_version` | 接收者公钥版本 |
| `client_message_id` | 客户端幂等或本地追踪用 ID |
| `reply_to_message_id` | 回复或转发关联的原消息 ID |
| `membership_version` | 群聊成员版本，发送群消息时用于防止密文接收者集合过期 |

禁止在公钥上传接口提交 `private_key`、`session_key`、`file_key` 等私密材料。

## 2. 页面与表单接口

这些接口主要返回 HTML 或执行表单动作，供 Web 页面使用。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET/POST | `/login/` | 登录 |
| GET/POST | `/register/` | 注册 |
| GET | `/logout/` | 退出登录 |
| GET/POST | `/profile/edit/` | 编辑用户名、昵称、头像、简介等资料 |
| GET | `/contacts/` | 联系人页面 |
| GET | `/contacts/search/?q=<keyword>` | 搜索用户，返回 JSON |
| POST | `/contacts/request/send/` | 发送好友申请，表单字段 `username` 或 `user_id` |
| POST | `/contacts/request/<request_id>/accept/` | 接受好友申请 |
| POST | `/contacts/request/<request_id>/reject/` | 拒绝好友申请 |
| POST | `/contacts/request/cancel/<user_id>/` | 按用户 ID 取消外发申请，返回 JSON |
| POST | `/contacts/request/accept-by-user/<user_id>/` | 按用户 ID 接受申请，返回 JSON |
| POST | `/contacts/request/reject-by-user/<user_id>/` | 按用户 ID 拒绝申请，返回 JSON |
| POST | `/contacts/<contact_id>/delete/` | 删除联系人 |
| GET | `/contacts/<contact_id>/chat/` | 打开或创建与联系人的私聊会话 |
| GET | `/groups/` | 群列表页面 |
| GET/POST | `/groups/create/` | 创建群页面/表单 |
| GET | `/groups/<group_id>/` | 群详情页面 |
| POST | `/groups/<group_id>/add-member/` | 表单添加群成员 |
| POST | `/groups/<group_id>/leave/` | 表单退出群 |

## 3. 公钥与密钥信任 API

### 3.1 上传当前用户公钥

`POST /api/keys/upload/`

请求：

```json
{
  "identity_public_key": "base64-public-key",
  "algorithm": "ECDH-P256",
  "key_fingerprint": "可选，SHA-256 十六进制指纹"
}
```

响应 `201`：

```json
{
  "key": {
    "user_id": 1,
    "identity_public_key": "base64-public-key",
    "key_fingerprint": "ABCDEF...",
    "algorithm": "ECDH-P256",
    "key_version": 1,
    "is_active": true,
    "created_at": "2026-06-11T20:30:00+08:00"
  }
}
```

错误：`invalid_json`、`private_key_material_not_allowed`、`unsupported_algorithm`、`invalid_public_key`、`fingerprint_mismatch`。

### 3.2 公钥查询

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/keys/<user_id>/` | 查询用户当前有效公钥 |
| GET | `/api/keys/<user_id>/<key_version>/` | 查询指定版本公钥 |
| POST | `/api/keys/batch/` | 批量查询当前有效公钥 |
| GET | `/api/keys/fingerprint/<user_id>/` | 查询当前有效公钥指纹 |
| GET | `/api/keys/fingerprints/` | 查询当前用户所有历史公钥 |
| GET | `/api/keys/contacts/<user_id>/fingerprints/` | 查询联系人所有公钥及当前信任状态 |
| POST | `/api/keys/contacts/<user_id>/trust/` | 信任或标记联系人当前公钥 |
| DELETE | `/api/keys/contacts/<user_id>/trust/` | 取消对联系人当前公钥的信任记录 |
| GET | `/api/keys/trust/` | 当前用户的密钥信任列表 |

批量查询请求：

```json
{
  "user_ids": [1, 2, 3]
}
```

信任公钥请求：

```json
{
  "trust_status": "trusted"
}
```

`trust_status` 使用 `KeyTrust.TrustStatus` 枚举，当前常见值包括 `trusted`、`untrusted`。

## 4. 会话 API

### 4.1 会话列表

`GET /api/conversations/?filter=<filter>`

`filter` 可选：

| 值 | 说明 |
| --- | --- |
| 空 | 默认返回未归档、未隐藏的活跃会话 |
| `archived` | 返回已归档会话 |
| `hidden` | 返回已隐藏会话 |

响应：

```json
{
  "conversations": [
    {
      "id": 10,
      "type": "single",
      "name": "Alice",
      "peer_id": 2,
      "peer_username": "alice",
      "unread": 0,
      "last_message_at": "2026-06-11T20:30:00+08:00",
      "last_message_preview": "Encrypted message",
      "last_message_data": {
        "id": 99,
        "ciphertext": "...",
        "nonce": "...",
        "auth_tag": "...",
        "algorithm": "AES-256-GCM",
        "sender_id": 1,
        "receiver_id": 2,
        "message_type": "text",
        "sender_key_version": 1,
        "receiver_key_version": 1
      },
      "is_pinned": false,
      "is_muted": false,
      "muted_until": null,
      "is_archived": false,
      "cleared_at": null,
      "is_secure": true
    }
  ]
}
```

群聊会话额外包含 `member_count`、`membership_version`。

### 4.2 创建或复用私聊会话

`POST /api/conversations/create/`

请求：

```json
{
  "peer_id": 2
}
```

响应：

```json
{
  "conversation_id": 10,
  "created": true
}
```

约束：不能与自己聊天；默认要求双方为联系人，除非接收方隐私设置允许所有人发消息；拉黑关系禁止创建。

### 4.3 会话管理

| 方法 | 路径 | 请求体 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/conversations/<conversation_id>/pin/` | 无 | 置顶 |
| DELETE | `/api/conversations/<conversation_id>/pin/` | 无 | 取消置顶 |
| POST | `/api/conversations/<conversation_id>/mute/` | `{"duration_minutes": 120}` | 静音，范围 1 到 10080 分钟 |
| DELETE | `/api/conversations/<conversation_id>/mute/` | 无 | 取消静音 |
| POST | `/api/conversations/<conversation_id>/archive/` | 无 | 归档 |
| POST | `/api/conversations/<conversation_id>/unarchive/` | 无 | 取消归档 |
| DELETE | `/api/conversations/<conversation_id>/` | 无 | 当前用户隐藏会话 |
| POST | `/api/conversations/<conversation_id>/clear/` | 无 | 当前用户清空会话历史视图 |
| POST | `/api/conversations/<conversation_id>/read/` | 无 | 标记已读 |
| POST | `/api/conversations/<conversation_id>/unread/` | `{"unread_count": 3}` | 标记未读，范围 1 到 99 |

## 5. 私聊消息 API

### 5.1 获取私聊消息

`GET /api/conversations/<conversation_id>/messages/?page=1&per_page=30`

响应：

```json
{
  "conversation_id": 10,
  "page": 1,
  "total_pages": 1,
  "total_messages": 3,
  "has_next": false,
  "has_previous": false,
  "messages": [
    {
      "id": 99,
      "sender_id": 1,
      "receiver_id": 2,
      "message_type": "text",
      "ciphertext": "...",
      "nonce": "...",
      "auth_tag": "...",
      "algorithm": "AES-256-GCM",
      "sender_key_version": 1,
      "receiver_key_version": 1,
      "reply_to_message_id": null,
      "status": "sent",
      "recalled_at": null,
      "created_at": "2026-06-11T20:30:00+08:00"
    }
  ]
}
```

约束：仅会话活跃成员可访问；私聊额外要求仍是联系人；已删除和清空时间点之前的消息不会返回。

### 5.2 HTTP 发送私聊消息

`POST /api/conversations/<conversation_id>/messages/send/`

请求：

```json
{
  "receiver_id": 2,
  "ciphertext": "base64-ciphertext",
  "nonce": "base64-nonce",
  "auth_tag": "base64-auth-tag",
  "algorithm": "AES-256-GCM",
  "sender_key_version": 1,
  "receiver_key_version": 1,
  "client_message_id": "client-uuid",
  "message_type": "text",
  "reply_to_message_id": null
}
```

响应 `201` 返回已创建消息对象，并通过 WebSocket 向接收者推送 `message.single.new`。

### 5.3 消息操作

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/conversations/<conversation_id>/messages/forward/` | 转发消息，客户端重新加密后提交 |
| DELETE | `/api/conversations/<conversation_id>/messages/<message_id>/` | 当前用户软删除消息 |
| POST | `/api/conversations/<conversation_id>/messages/<message_id>/recall/` | 发送者撤回消息，限制 30 分钟内 |
| GET | `/api/conversations/<conversation_id>/messages/<message_id>/status/` | 查询消息发送/送达/已读/撤回状态 |

私聊转发请求体与发送私聊类似，额外字段：

```json
{
  "original_message_id": 99,
  "original_conversation_id": 10,
  "peer_id": 2,
  "ciphertext": "...",
  "nonce": "...",
  "auth_tag": "...",
  "algorithm": "AES-256-GCM"
}
```

撤回响应：

```json
{
  "status": "recalled",
  "message_id": 99,
  "recalled_at": "2026-06-11T20:35:00+08:00"
}
```

## 6. 群聊 API

### 6.1 创建与更新群

| 方法 | 路径 | 请求体 | 权限 |
| --- | --- | --- | --- |
| POST | `/api/groups/` | `{"name": "Project", "avatar": "", "initial_member_ids": [2, 3]}` | 登录用户 |
| PUT | `/api/groups/<conversation_id>/` | `{"name": "New Name", "avatar": ""}` | 群主 |

创建响应：

```json
{
  "id": 20,
  "name": "Project",
  "type": "group",
  "created_at": "2026-06-11T20:30:00+08:00",
  "member_count": 3
}
```

### 6.2 成员管理

| 方法 | 路径 | 请求体 | 权限 | 说明 |
| --- | --- | --- | --- | --- |
| POST | `/api/groups/<conversation_id>/invite/` | `{"user_id": 2}` | 群主/管理员 | 邀请成员 |
| POST | `/api/groups/<conversation_id>/remove/` | `{"user_id": 2}` | 群主/管理员 | 移除成员；管理员不能移除管理员，任何人不能移除群主 |
| POST | `/api/groups/<conversation_id>/leave/` | 无 | 活跃成员 | 退出群；群主有其他成员时需先转让 |
| POST | `/api/groups/<conversation_id>/disband/` | 无 | 群主 | 解散群 |
| GET | `/api/groups/<conversation_id>/members/` | 无 | 活跃成员 | 获取成员与 `membership_version` |
| GET | `/api/groups/<conversation_id>/members-advanced/` | 无 | 活跃成员 | 获取管理面板成员列表与角色 |
| POST | `/api/groups/<conversation_id>/promote/<user_id>/` | 无 | 群主 | 升为管理员 |
| POST | `/api/groups/<conversation_id>/demote/<user_id>/` | 无 | 群主 | 管理员降为成员 |
| POST | `/api/groups/<conversation_id>/transfer/` | `{"user_id": 2}` | 群主 | 转让群主 |

成员列表响应：

```json
{
  "group_id": 20,
  "membership_version": 4,
  "members": [
    {
      "user_id": 1,
      "username": "alice",
      "display_name": "Alice",
      "initials": "AL",
      "avatar_color": "#5c6bc0",
      "role": "owner",
      "is_secure": true
    }
  ]
}
```

### 6.3 群消息

`GET /api/groups/<conversation_id>/messages/?page=1&per_page=30`

响应只返回当前用户自己的那份群消息密文，避免泄露其他成员的独立密文。

```json
{
  "conversation_id": 20,
  "page": 1,
  "total_pages": 1,
  "total_messages": 1,
  "has_next": false,
  "has_previous": false,
  "messages": [
    {
      "id": 100,
      "sender_id": 1,
      "sender_username": "alice",
      "sender_name": "Alice",
      "message_type": "text",
      "ciphertext": "...",
      "nonce": "...",
      "auth_tag": "...",
      "algorithm": "AES-256-GCM",
      "sender_key_version": 1,
      "receiver_key_version": 1,
      "reply_to_message_id": null,
      "membership_version": 4,
      "status": "sent",
      "recalled_at": null,
      "created_at": "2026-06-11T20:30:00+08:00"
    }
  ]
}
```

群消息转发使用：

`POST /api/conversations/<conversation_id>/messages/forward/`

群聊请求体：

```json
{
  "original_message_id": 99,
  "original_conversation_id": 10,
  "message_type": "text",
  "client_message_id": "client-uuid",
  "membership_version": 4,
  "recipients": [
    {
      "receiver_id": 1,
      "ciphertext": "...",
      "nonce": "...",
      "auth_tag": "...",
      "algorithm": "AES-256-GCM",
      "sender_key_version": 1,
      "receiver_key_version": 1
    }
  ]
}
```

约束：

- `recipients` 必须覆盖当前所有活跃成员。
- 如果传入 `membership_version`，必须与服务端当前版本一致，否则返回 `409`。
- 群被静音期间，非群主/管理员不能发送。

### 6.4 群公告与禁言

| 方法 | 路径 | 请求体 | 权限 | 说明 |
| --- | --- | --- | --- | --- |
| GET | `/api/groups/<conversation_id>/announcement/` | 无 | 活跃成员 | 获取当前公告 |
| POST | `/api/groups/<conversation_id>/announcement/` | `{"content": "公告内容"}` | 群主/管理员 | 创建或替换公告 |
| DELETE | `/api/groups/<conversation_id>/announcement/` | 无 | 群主/管理员 | 删除当前公告 |
| POST | `/api/groups/<conversation_id>/mute-group/` | `{"duration_minutes": 60}` | 群主/管理员 | 群禁言 |
| DELETE | `/api/groups/<conversation_id>/mute-group/` | 无 | 群主/管理员 | 取消群禁言 |

## 7. 在线状态 API

| 方法 | 路径 | 请求体 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/users/<user_id>/presence/` | 无 | 查询用户在线状态，遵守可见性设置 |
| PUT | `/api/users/presence/` | `{"status": "away", "presence_visibility": "contacts"}` | 更新当前用户在线状态 |

`status` 常见值：`online`、`away`、`busy`、`offline`。  
`presence_visibility` 常见值：`everyone`、`contacts`、`nobody`。

响应：

```json
{
  "user_id": 1,
  "is_online": true,
  "last_seen": "2026-06-11T20:30:00+08:00",
  "status": "online",
  "presence_visibility": "contacts"
}
```

## 8. 存储设置 API

| 方法 | 路径 | 请求体 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/storage/stats/` | 无 | 获取当前用户存储估算统计 |
| POST | `/api/storage/clear/` | `{"categories": ["images", "videos", "stickers", "other", "video_stream_chunks"]}` | 清理指定类别缓存；当前服务端清理为占位实现 |
| GET | `/api/storage/settings/` | 无 | 获取存储设置 |
| POST | `/api/storage/settings/` | 任意设置子集 | 深度合并并保存存储设置 |

存储统计响应：

```json
{
  "categories": {
    "images": {"size_bytes": 0, "count": 0, "label": "Images"},
    "videos": {"size_bytes": 0, "count": 0, "label": "Video files"},
    "stickers": {"size_bytes": 0, "count": 0, "label": "Stickers and emojis"},
    "other": {"size_bytes": 2048, "count": 1, "label": "Other"},
    "video_stream_chunks": {"size_bytes": 0, "count": 0, "label": "Cached video stream chunks"}
  },
  "total_bytes": 2048,
  "quota_bytes": 52428800,
  "usage_percent": 0.0
}
```

## 9. 隐私与安全 API

### 9.1 隐私设置

| 方法 | 路径 | 请求体 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/privacy/settings/` | 无 | 获取隐私设置 |
| POST | `/api/privacy/settings/` | 设置子集 | 更新隐私设置 |

可更新字段：

| 字段 | 允许值 |
| --- | --- |
| `last_seen_visibility` | `everyone`、`contacts`、`nobody` |
| `profile_photo_visibility` | `everyone`、`contacts`、`nobody` |
| `phone_number_visibility` | `everyone`、`contacts`、`nobody` |
| `bio_visibility` | `everyone`、`contacts`、`nobody` |
| `forward_link_visibility` | `everyone`、`contacts`、`nobody` |
| `who_can_send_messages` | `everyone`、`contacts` |
| `who_can_voice_video_call` | `everyone`、`contacts` |
| `auto_delete_messages_days` | 0 到 365 |
| `sensitive_content_filter` | boolean |
| `passcode_lock_enabled` | boolean |
| `two_step_verification_enabled` | boolean |
| `login_email` | 邮箱或空字符串 |

### 9.2 拉黑与账号安全

| 方法 | 路径 | 请求体 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/privacy/blocked/` | 无 | 当前用户拉黑列表 |
| POST | `/api/privacy/block/` | `{"user_id": 2}` | 拉黑用户，并删除双方联系人关系 |
| POST | `/api/privacy/unblock/` | `{"user_id": 2}` | 取消拉黑 |
| POST | `/api/privacy/delete-contacts/` | 无 | 删除当前用户全部联系人 |
| POST | `/api/privacy/delete-account/` | 无 | 匿名化并停用当前账号，同时登出 |

## 10. 自动删除 API

| 方法 | 路径 | 请求体 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/settings/auto-delete/` | 无 | 获取当前用户创建的私聊全局自动删除默认值 |
| PUT | `/api/settings/auto-delete/` | `{"seconds": 86400}` 或 `{"disabled": true}` | 更新全局默认值 |
| GET | `/api/conversations/<conversation_id>/auto-delete/` | 无 | 获取当前用户在该会话的自动删除设置 |
| PUT | `/api/conversations/<conversation_id>/auto-delete/` | `{"seconds": 86400}` 或 `{"disabled": true}` | 更新当前用户会话级覆盖值 |

## 11. 搜索 API

`GET /api/search/?q=<keyword>&scope=<scope>`

`scope` 可选：`all`、`contacts`、`private_chats`、`group_chats`。

响应：

```json
{
  "results": {
    "conversations": [
      {
        "conversation_id": 10,
        "peer_id": 2,
        "peer_username": "alice",
        "peer_display_name": "Alice"
      }
    ],
    "contacts": [
      {
        "id": 2,
        "username": "alice",
        "nickname": "Alice",
        "is_contact": true
      }
    ],
    "groups": [
      {
        "id": 20,
        "name": "Project",
        "is_member": true,
        "member_count": 3
      }
    ],
    "channels": []
  },
  "scope": "all",
  "query": "ali"
}
```

## 12. 通知、多账号、会话设备与资料同步 API

### 12.1 通知设置

| 方法 | 路径 | 请求体 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/settings/notifications/` | 无 | 获取通知设置 |
| PUT | `/api/settings/notifications/update/` | 通知字段子集 | 更新通知设置 |

字段：

`offline_notifications`、`all_accounts_notifications`、`notification_sound`、`volume`、`message_sent_sound`、`private_chat_notifications`、`group_chat_notifications`、`channel_notifications`、`message_preview_private`、`message_preview_group`、`message_preview_channel`、`contact_join_notifications`。

### 12.2 二维码名片

`GET /api/qr-card/`

响应：

```json
{
  "user_id": 1,
  "username": "alice",
  "nickname": "Alice",
  "avatar": "http://127.0.0.1:8000/media/avatars/a.png",
  "bio": "Hello",
  "phone_number": ""
}
```

### 12.3 多账号上下文

| 方法 | 路径 | 请求体 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/account/context/` | 无 | 获取当前用户多账号上下文 |
| PUT | `/api/account/context/update/` | `{"context_json": {...}}` | 更新上下文 JSON |

### 12.4 登录会话管理

| 方法 | 路径 | 请求体 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/sessions/` | 无 | 列出当前用户活跃 Session，返回不透明 `session_id` |
| POST | `/api/sessions/terminate/` | `{"session_id": "sid_2"}` | 终止指定非当前 Session |

### 12.5 资料同步事件

`GET /api/profile/updates/?since=<iso-time>`

响应：

```json
{
  "updates": [
    {
      "id": 1,
      "user_id": 2,
      "username": "alice",
      "created_at": "2026-06-11T20:30:00+08:00"
    }
  ]
}
```

## 13. WebSocket API

### 13.1 连接

| 项目 | 说明 |
| --- | --- |
| URL | `ws://127.0.0.1:8000/ws/chat/` |
| 认证 | Django Session Cookie |
| Origin | 受 `AllowedHostsOriginValidator` 校验 |
| 连接成功事件 | `connection.ready` |

客户端发送消息的通用格式：

```json
{
  "protocol_version": "1.0",
  "event": "message.single.send",
  "request_id": "req-1",
  "sent_at": "2026-06-11T20:30:00Z",
  "data": {}
}
```

服务端响应事件格式：

```json
{
  "event": "message.single.accepted",
  "request_id": "req-1",
  "data": {}
}
```

错误事件：

```json
{
  "event": "error",
  "request_id": "req-1",
  "data": {
    "code": "invalid_payload",
    "message": "错误说明"
  }
}
```

### 13.2 客户端可发送事件

| 事件 | data | 说明 |
| --- | --- | --- |
| `connection.ping` | `{}` | 心跳，返回 `connection.pong` |
| `message.single.send` | 私聊密文字段 | 创建私聊密文消息 |
| `message.group.send` | 群聊密文字段与 `recipients` | 创建群聊密文消息 |
| `message.receipt.update` | `{"conversation_type": "single", "message_id": 99, "status": "read"}` | 更新送达/已读状态 |
| `message.recall` | `{"conversation_id": 10, "message_id": 99, "conversation_type": "single"}` | 撤回消息 |
| `typing.start` | `{"conversation_id": 10}` | 正在输入 |
| `typing.stop` | `{"conversation_id": 10}` | 停止输入 |

私聊发送 `data` 示例：

```json
{
  "conversation_id": 10,
  "receiver_id": 2,
  "ciphertext": "...",
  "nonce": "...",
  "auth_tag": "...",
  "algorithm": "AES-256-GCM",
  "sender_key_version": 1,
  "receiver_key_version": 1,
  "client_message_id": "client-uuid",
  "message_type": "text",
  "reply_to_message_id": null
}
```

群聊发送 `data` 示例：

```json
{
  "conversation_id": 20,
  "message_type": "text",
  "client_message_id": "client-uuid",
  "membership_version": 4,
  "recipients": [
    {
      "receiver_id": 1,
      "ciphertext": "...",
      "nonce": "...",
      "auth_tag": "...",
      "algorithm": "AES-256-GCM",
      "sender_key_version": 1,
      "receiver_key_version": 1
    }
  ]
}
```

### 13.3 服务端推送事件

| 事件 | 说明 |
| --- | --- |
| `connection.ready` | WebSocket 已连接 |
| `connection.pong` | 心跳响应 |
| `message.single.accepted` | 私聊消息已被服务端接收 |
| `message.single.new` | 收到新的私聊消息 |
| `message.group.accepted` | 群聊消息已被服务端接收 |
| `message.group.new` | 收到新的群聊消息密文 |
| `message.receipt.updated` | 消息状态已更新 |
| `message.recalled` | 消息已撤回 |
| `message.deleted` | 当前用户某条消息已软删除 |
| `typing` | 对方输入状态，`data.action` 为 `typing` 或 `stop` |
| `presence.updated` | 在线状态更新 |
| `group.members.changed` | 群成员变动，包含最新 `membership_version` |
| `error` | 请求错误或未实现事件 |

## 14. 对接注意事项

1. 前端必须自行完成明文加密、解密、群聊按接收者分别加密；后端只保存密文字段。
2. 私聊历史和发送会校验联系人关系、拉黑关系和会话成员关系。
3. 群聊发送时 `recipients` 必须覆盖所有活跃成员，包括发送者自己；成员变化后需重新拉取公钥和 `membership_version`。
4. 删除消息是当前用户视角的软删除；撤回消息是全局状态变更，且仅发送者可在 30 分钟内撤回。
5. `storage_clear` 目前是服务端占位实现，真实文件/Blob 清理由后续任务补齐。
6. `/api/sessions/terminate/` 使用 `/api/sessions/` 返回的不透明 `session_id`，不能直接传 Django 原始 session key。
