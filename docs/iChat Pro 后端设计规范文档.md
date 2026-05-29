# iChat Pro 后端设计规范文档

## 1. 设计目标

本系统后端面向端到端加密即时通信场景设计，主要负责用户认证、公钥管理、会话关系维护、密文消息存储、群聊成员管理、加密文件存储和 WebSocket 实时推送。由于系统采用端到端加密机制，后端不负责生成用户私钥，不负责生成会话密钥，不负责消息加密或解密，也不保存任何聊天明文内容。

后端系统的核心定位为：

> 后端是账号、关系、权限、密文存储和实时转发服务器，而不是消息明文处理服务器。

系统后端设计应满足以下目标：

| 目标     | 说明                                               |
| -------- | -------------------------------------------------- |
| 安全性   | 不保存用户私钥、会话密钥、消息明文和原始文件内容   |
| 可维护性 | 按功能模块拆分应用，降低代码耦合                   |
| 可扩展性 | 支持后续扩展群聊、文件、Sender Key、消息状态等功能 |
| 实时性   | 使用 WebSocket 实现单聊和群聊消息实时推送          |
| 一致性   | 后端接口、数据库模型和前端加密流程保持一致         |
| 可测试性 | 能通过测试证明数据库中只保存密文，不保存明文       |

------

## 2. 技术栈选型

本系统后端建议采用以下技术栈：

| 技术                  | 用途                                           |
| --------------------- | ---------------------------------------------- |
| Python                | 后端主要开发语言                               |
| Django                | Web 后端框架，负责用户、模型、路由和业务逻辑   |
| Django REST Framework | 提供 REST API 接口                             |
| Django Channels       | 实现 WebSocket 实时通信                        |
| SQLite                | 课程项目阶段使用的轻量数据库                   |
| Redis                 | 可选，用于 Channels 消息层，第一版可不强制使用 |
| 本地文件系统          | 保存加密后的图片、文件和分块密文               |
| JWT 或 Django Session | 用户登录认证与接口鉴权                         |

课程项目第一阶段可采用 `Django + SQLite + Django Channels + 本地文件系统` 完成核心功能，后续若部署到生产环境，可将 SQLite 替换为 PostgreSQL 或 MySQL，将本地文件系统替换为对象存储。

------

## 3. 后端总体职责边界

### 3.1 后端负责的内容

| 功能           | 说明                                          |
| -------------- | --------------------------------------------- |
| 用户注册登录   | 管理账号、密码哈希、登录状态                  |
| 用户资料管理   | 管理昵称、头像、简介等信息                    |
| 公钥管理       | 保存用户公钥、公钥指纹、密钥版本              |
| 单聊会话管理   | 创建或获取两个用户之间的会话                  |
| 群聊管理       | 创建群聊、添加成员、移除成员、维护成员状态    |
| 密文消息存储   | 保存密文、nonce、auth_tag、算法标识和消息状态 |
| WebSocket 推送 | 将密文消息实时推送给接收者                    |
| 加密文件存储   | 保存加密文件块、加密文件密钥和文件元数据      |
| 权限校验       | 校验用户是否有权访问会话、群聊和文件          |
| 消息状态维护   | 维护 sent、delivered、read 等状态             |

### 3.2 后端不负责的内容

| 禁止事项             | 原因                                  |
| -------------------- | ------------------------------------- |
| 不保存用户私钥       | 私钥只能保存在客户端                  |
| 不生成 session_key   | 会话密钥由客户端通过 ECDH + HKDF 派生 |
| 不保存 session_key   | 保存会话密钥会破坏端到端加密          |
| 不解密消息           | 后端没有密钥，也不应尝试解密          |
| 不保存消息明文       | 数据库中只允许保存密文                |
| 不保存原始图片和文件 | 文件也应以密文形式存储                |
| 不在日志中记录明文   | 防止日志泄露用户隐私                  |

------

## 4. 后端分层架构

系统后端采用分层结构设计：

```text
客户端
  ↓
API / WebSocket 接入层
  ↓
业务服务层 Service
  ↓
数据访问层 Model / Repository
  ↓
数据库 + 加密文件存储
```

各层职责如下：

| 层次             | 职责                                         |
| ---------------- | -------------------------------------------- |
| API 接入层       | 处理 HTTP 请求、参数校验、认证鉴权、返回响应 |
| WebSocket 接入层 | 建立实时连接、校验 Token、推送实时消息       |
| 业务服务层       | 实现用户、会话、群聊、消息、文件等核心业务   |
| 数据访问层       | 通过 Django ORM 操作数据库                   |
| 存储层           | 保存用户数据、密文消息、加密文件块和元数据   |

------

## 5. 后端模块划分

建议后端项目按应用模块拆分：

```text
backend/
├── manage.py
├── config/
│   ├── settings.py
│   ├── urls.py
│   ├── asgi.py
│   └── routing.py
├── apps/
│   ├── accounts/
│   ├── keys/
│   ├── conversations/
│   ├── messages/
│   ├── groups/
│   ├── files/
│   ├── realtime/
│   └── common/
└── media/
    └── encrypted_files/
```

各模块职责如下：

| 模块            | 职责                                       |
| --------------- | ------------------------------------------ |
| `accounts`      | 用户注册、登录、退出、个人资料管理         |
| `keys`          | 用户公钥上传、查询、批量查询、公钥指纹管理 |
| `conversations` | 单聊会话创建、会话成员管理、会话列表       |
| `messages`      | 单聊密文消息保存、拉取、状态更新           |
| `groups`        | 群聊创建、群成员管理、群聊密文消息         |
| `files`         | 加密文件初始化、分块上传、分块下载         |
| `realtime`      | WebSocket 连接、在线状态、实时推送         |
| `common`        | 统一响应、异常处理、权限工具、分页工具     |

------

## 6. 数据库设计规范

### 6.1 总体原则

数据库设计必须遵守以下原则：

1. 消息表不允许出现明文字段。
2. 文件表不允许保存原始文件路径。
3. 用户密钥表只保存公钥，不保存私钥。
4. 所有密文必须保存对应的 `nonce` 和 `auth_tag`。
5. 群聊基础版中，每个接收者应有独立的密文记录。
6. 文件消息中，文件内容密文和文件密钥密文应分开保存。

### 6.2 用户表 `user`

| 字段          | 类型     | 说明     |
| ------------- | -------- | -------- |
| id            | int      | 用户 ID  |
| username      | varchar  | 用户名   |
| password_hash | varchar  | 密码哈希 |
| nickname      | varchar  | 昵称     |
| avatar        | varchar  | 头像     |
| created_at    | datetime | 创建时间 |
| updated_at    | datetime | 更新时间 |

### 6.3 用户密钥表 `user_key`

| 字段                | 类型     | 说明                       |
| ------------------- | -------- | -------------------------- |
| id                  | int      | 主键                       |
| user_id             | int      | 用户 ID                    |
| identity_public_key | text     | 用户身份公钥               |
| key_fingerprint     | varchar  | 公钥指纹                   |
| algorithm           | varchar  | 公钥算法，例如 ECDH-X25519 |
| key_version         | int      | 密钥版本                   |
| is_active           | boolean  | 是否有效                   |
| created_at          | datetime | 创建时间                   |

说明：该表只保存公钥，不保存 `private_key`。

### 6.4 会话表 `conversation`

| 字段       | 类型     | 说明         |
| ---------- | -------- | ------------ |
| id         | int      | 会话 ID      |
| type       | varchar  | single/group |
| created_at | datetime | 创建时间     |
| updated_at | datetime | 更新时间     |

### 6.5 会话成员表 `conversation_member`

| 字段            | 类型     | 说明                |
| --------------- | -------- | ------------------- |
| id              | int      | 主键                |
| conversation_id | int      | 会话 ID             |
| user_id         | int      | 用户 ID             |
| joined_at       | datetime | 加入时间            |
| status          | varchar  | active/left/removed |

### 6.6 单聊密文消息表 `encrypted_message`

| 字段                 | 类型     | 说明                    |
| -------------------- | -------- | ----------------------- |
| id                   | int      | 消息 ID                 |
| conversation_id      | int      | 会话 ID                 |
| sender_id            | int      | 发送者 ID               |
| receiver_id          | int      | 接收者 ID               |
| message_type         | varchar  | text/image/file/sticker |
| ciphertext           | text     | 消息密文                |
| nonce                | varchar  | AES-GCM 随机数          |
| auth_tag             | varchar  | AES-GCM 认证标签        |
| algorithm            | varchar  | 加密算法                |
| sender_key_version   | int      | 发送者密钥版本          |
| receiver_key_version | int      | 接收者密钥版本          |
| created_at           | datetime | 发送时间                |
| status               | varchar  | sent/delivered/read     |

说明：该表不得包含 `plaintext`、`content_plain` 等明文字段。

### 6.7 群聊表 `group_chat`

| 字段       | 类型     | 说明     |
| ---------- | -------- | -------- |
| id         | int      | 群聊 ID  |
| name       | varchar  | 群名称   |
| avatar     | varchar  | 群头像   |
| owner_id   | int      | 群主 ID  |
| created_at | datetime | 创建时间 |
| updated_at | datetime | 更新时间 |

### 6.8 群成员表 `group_member`

| 字段      | 类型     | 说明                |
| --------- | -------- | ------------------- |
| id        | int      | 主键                |
| group_id  | int      | 群聊 ID             |
| user_id   | int      | 成员 ID             |
| role      | varchar  | owner/admin/member  |
| joined_at | datetime | 加入时间            |
| left_at   | datetime | 退出时间            |
| status    | varchar  | active/left/removed |

### 6.9 群消息表 `group_message`

| 字段         | 类型     | 说明                    |
| ------------ | -------- | ----------------------- |
| id           | int      | 群消息 ID               |
| group_id     | int      | 群聊 ID                 |
| sender_id    | int      | 发送者 ID               |
| message_type | varchar  | text/image/file/sticker |
| algorithm    | varchar  | 加密算法                |
| created_at   | datetime | 发送时间                |
| status       | varchar  | sent/delivered/read     |

说明：该表只保存群消息逻辑记录，不保存明文内容。

### 6.10 群消息接收表 `group_message_recipient`

| 字段         | 类型     | 说明               |
| ------------ | -------- | ------------------ |
| id           | int      | 主键               |
| message_id   | int      | 群消息 ID          |
| receiver_id  | int      | 接收者 ID          |
| ciphertext   | text     | 该接收者对应的密文 |
| nonce        | varchar  | 随机数             |
| auth_tag     | varchar  | 认证标签           |
| key_version  | int      | 密钥版本           |
| delivered_at | datetime | 送达时间           |
| read_at      | datetime | 已读时间           |

### 6.11 加密文件表 `encrypted_file`

| 字段                 | 类型     | 说明               |
| -------------------- | -------- | ------------------ |
| id                   | int      | 文件 ID            |
| message_id           | int      | 对应消息 ID        |
| sender_id            | int      | 发送者 ID          |
| file_ciphertext_path | varchar  | 加密文件存储路径   |
| file_size            | int      | 原文件大小         |
| chunk_size           | int      | 分块大小           |
| chunk_count          | int      | 分块数量           |
| mime_type            | varchar  | 文件类型           |
| encrypted_filename   | text     | 加密后的原始文件名 |
| created_at           | datetime | 上传时间           |

### 6.12 加密文件块表 `encrypted_file_chunk`

| 字段           | 类型    | 说明           |
| -------------- | ------- | -------------- |
| id             | int     | 文件块 ID      |
| file_id        | int     | 文件 ID        |
| chunk_index    | int     | 文件块序号     |
| chunk_path     | varchar | 加密文件块路径 |
| chunk_nonce    | varchar | 文件块 nonce   |
| chunk_auth_tag | varchar | 文件块认证标签 |
| chunk_size     | int     | 当前块大小     |

### 6.13 加密文件密钥表 `encrypted_file_key`

| 字段               | 类型     | 说明                               |
| ------------------ | -------- | ---------------------------------- |
| id                 | int      | 主键                               |
| file_id            | int      | 文件 ID                            |
| receiver_id        | int      | 接收者 ID                          |
| encrypted_file_key | text     | 使用 session_key 加密后的 file_key |
| key_nonce          | varchar  | 加密 file_key 的 nonce             |
| key_auth_tag       | varchar  | 加密 file_key 的认证标签           |
| key_version        | int      | 会话密钥版本                       |
| created_at         | datetime | 创建时间                           |

------

## 7. REST API 设计规范

### 7.1 通用接口规范

所有接口采用 JSON 数据格式，路径以 `/api/` 开头。接口返回统一响应结构：

```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

错误响应结构：

```json
{
  "code": 40301,
  "message": "无权访问该资源",
  "data": null
}
```

### 7.2 错误码规范

| 错误码 | 含义           |
| ------ | -------------- |
| 40001  | 请求参数错误   |
| 40101  | 用户未登录     |
| 40301  | 无权访问       |
| 40401  | 资源不存在     |
| 40901  | 状态冲突       |
| 41301  | 文件过大       |
| 42901  | 请求过于频繁   |
| 50001  | 服务器内部错误 |

------

## 8. 用户与认证接口

| 接口                  | 方法  | 功能             |
| --------------------- | ----- | ---------------- |
| `/api/auth/register/` | POST  | 用户注册         |
| `/api/auth/login/`    | POST  | 用户登录         |
| `/api/auth/logout/`   | POST  | 用户退出         |
| `/api/users/me/`      | GET   | 获取当前用户信息 |
| `/api/users/me/`      | PATCH | 修改个人资料     |

### 8.1 注册接口规则

注册时后端只处理账号信息，不生成端到端加密私钥。客户端完成注册后，应在本地生成密钥对，并调用公钥上传接口上传公钥。

后端必须使用密码哈希存储用户密码，不允许保存明文密码。

------

## 9. 公钥管理接口

| 接口                               | 方法 | 功能                 |
| ---------------------------------- | ---- | -------------------- |
| `/api/keys/upload/`                | POST | 上传当前用户公钥     |
| `/api/keys/{user_id}/`             | GET  | 获取指定用户公钥     |
| `/api/keys/batch/`                 | POST | 批量获取多个用户公钥 |
| `/api/keys/fingerprint/{user_id}/` | GET  | 获取用户公钥指纹     |

### 9.1 公钥上传请求

```json
{
  "identity_public_key": "base64公钥",
  "key_fingerprint": "公钥指纹",
  "algorithm": "ECDH-X25519",
  "key_version": 1
}
```

### 9.2 公钥管理规则

1. 后端只允许保存公钥，不允许保存私钥。
2. 如果用户更新公钥，应生成新的 `key_version`。
3. 客户端应在本地保存联系人公钥指纹。
4. 如果联系人公钥变化，客户端应提示用户重新验证身份。

------

## 10. 单聊接口设计

| 接口                               | 方法 | 功能               |
| ---------------------------------- | ---- | ------------------ |
| `/api/conversations/`              | GET  | 获取会话列表       |
| `/api/conversations/single/`       | POST | 创建或获取单聊会话 |
| `/api/messages/send/`              | POST | 发送单聊密文消息   |
| `/api/messages/{conversation_id}/` | GET  | 拉取历史密文消息   |
| `/api/messages/{message_id}/read/` | POST | 标记消息已读       |

### 10.1 单聊密文消息请求

```json
{
  "conversation_id": 1001,
  "receiver_id": 2,
  "message_type": "text",
  "ciphertext": "base64密文",
  "nonce": "base64随机数",
  "auth_tag": "base64认证标签",
  "algorithm": "AES-GCM",
  "sender_key_version": 1,
  "receiver_key_version": 1
}
```

### 10.2 单聊后端校验规则

| 校验项                 | 说明                                       |
| ---------------------- | ------------------------------------------ |
| 当前用户是否登录       | 防止匿名发送                               |
| 当前用户是否属于该会话 | 防止越权发送                               |
| 接收者是否属于该会话   | 防止向错误对象发送                         |
| 密文字段是否完整       | `ciphertext`、`nonce`、`auth_tag` 必须存在 |
| 消息类型是否合法       | 仅允许 text/image/file/sticker 等预设类型  |
| 消息大小是否合法       | 防止超大请求                               |
| 密钥版本是否存在       | 便于客户端判断密钥是否变更                 |

后端不校验明文内容，因为后端无法解密消息。

------

## 11. 群聊接口设计

| 接口                                     | 方法 | 功能             |
| ---------------------------------------- | ---- | ---------------- |
| `/api/groups/`                           | POST | 创建群聊         |
| `/api/groups/{group_id}/`                | GET  | 获取群资料       |
| `/api/groups/{group_id}/members/`        | GET  | 获取群成员       |
| `/api/groups/{group_id}/members/add/`    | POST | 添加成员         |
| `/api/groups/{group_id}/members/remove/` | POST | 移除成员         |
| `/api/groups/{group_id}/messages/send/`  | POST | 发送群聊密文     |
| `/api/groups/{group_id}/messages/`       | GET  | 拉取群聊历史密文 |

### 11.1 群聊密文消息请求

```json
{
  "group_id": 12,
  "message_type": "text",
  "algorithm": "AES-GCM",
  "recipients": [
    {
      "receiver_id": 1,
      "ciphertext": "base64密文A",
      "nonce": "base64随机数A",
      "auth_tag": "base64认证标签A",
      "key_version": 1
    },
    {
      "receiver_id": 2,
      "ciphertext": "base64密文B",
      "nonce": "base64随机数B",
      "auth_tag": "base64认证标签B",
      "key_version": 1
    }
  ]
}
```

### 11.2 群聊后端校验规则

| 校验项                              | 说明                                                 |
| ----------------------------------- | ---------------------------------------------------- |
| 发送者是否是 active 群成员          | 非成员不能发群消息                                   |
| recipients 是否都属于该群           | 防止把群消息发给非群成员                             |
| recipients 是否覆盖当前 active 成员 | 防止漏发群成员密文                                   |
| 被移除成员是否被排除                | removed/left 成员不能收到新消息                      |
| 每个接收者是否有独立密文            | 符合基础版逐成员加密设计                             |
| 消息类型是否合法                    | 只允许预设消息类型                                   |
| 密文字段是否完整                    | 每个接收者都必须有 `ciphertext`、`nonce`、`auth_tag` |

------

## 12. WebSocket 设计规范

### 12.1 连接地址

WebSocket 连接地址：

```text
/ws/chat/
```

连接时携带用户认证 Token：

```text
/ws/chat/?token=xxx
```

后端在连接建立时完成以下操作：

1. 校验 Token 是否有效。
2. 获取当前用户 ID。
3. 将当前连接加入用户个人通道组。
4. 根据需要加入相关群聊通道组。
5. 维护用户在线状态。

### 12.2 通道组设计

| 通道组                           | 说明                         |
| -------------------------------- | ---------------------------- |
| `user_{user_id}`                 | 用户个人通道组，用于单聊推送 |
| `group_{group_id}`               | 群聊通道组，用于群消息推送   |
| `conversation_{conversation_id}` | 可选，用于会话级推送         |

### 12.3 单聊推送格式

```json
{
  "event": "message.new",
  "conversation_type": "single",
  "message_id": 501,
  "conversation_id": 1001,
  "sender_id": 1,
  "receiver_id": 2,
  "message_type": "text",
  "ciphertext": "base64密文",
  "nonce": "base64随机数",
  "auth_tag": "base64认证标签",
  "algorithm": "AES-GCM",
  "created_at": "2026-05-28T20:30:00"
}
```

### 12.4 群聊推送格式

```json
{
  "event": "message.new",
  "conversation_type": "group",
  "group_id": 12,
  "message_id": 888,
  "sender_id": 1,
  "receiver_id": 2,
  "message_type": "text",
  "ciphertext": "base64发给当前用户的密文",
  "nonce": "base64随机数",
  "auth_tag": "base64认证标签",
  "algorithm": "AES-GCM",
  "created_at": "2026-05-28T20:30:00"
}
```

说明：群聊推送时，每个用户收到的是属于自己的密文，不是同一份明文或同一份可公开密文。

------

## 13. 加密文件后端设计

### 13.1 文件接口

| 接口                                  | 方法 | 功能               |
| ------------------------------------- | ---- | ------------------ |
| `/api/files/init/`                    | POST | 初始化文件上传任务 |
| `/api/files/{file_id}/chunk/`         | POST | 上传加密文件块     |
| `/api/files/{file_id}/complete/`      | POST | 标记上传完成       |
| `/api/files/{file_id}/`               | GET  | 获取文件元数据     |
| `/api/files/{file_id}/chunk/{index}/` | GET  | 下载指定密文块     |

### 13.2 文件初始化请求

```json
{
  "message_id": 501,
  "file_size": 104857600,
  "chunk_size": 4194304,
  "chunk_count": 25,
  "mime_type": "image/png",
  "encrypted_filename": "base64加密文件名"
}
```

### 13.3 文件块上传请求

```json
{
  "file_id": "file_123",
  "chunk_index": 0,
  "encrypted_chunk": "base64密文块",
  "chunk_nonce": "base64随机数",
  "chunk_auth_tag": "base64认证标签"
}
```

### 13.4 文件密钥保存请求

```json
{
  "file_id": "file_123",
  "keys": [
    {
      "receiver_id": 2,
      "encrypted_file_key": "base64加密后的file_key",
      "key_nonce": "base64随机数",
      "key_auth_tag": "base64认证标签",
      "key_version": 1
    }
  ]
}
```

### 13.5 文件处理规则

1. 后端只接收加密文件块，不接收原始文件。
2. 文件存储路径使用 UUID，不使用用户上传的原始文件名。
3. 文件名如需保存，应由客户端加密后上传。
4. 上传大文件时，后端支持分块上传。
5. 单块大小建议为 1MB 或 4MB。
6. 课程项目阶段最大文件大小可限制为 100MB 或 200MB。
7. 用户下载文件块时，后端必须校验其是否有权访问该文件。
8. 后端不解密文件，不生成缩略图原图；缩略图应由客户端生成并加密上传。

------

## 14. 权限控制规范

后端必须对所有敏感资源进行权限校验。

| 场景             | 权限规则                                           |
| ---------------- | -------------------------------------------------- |
| 获取当前用户信息 | 必须登录                                           |
| 获取他人公钥     | 必须登录                                           |
| 拉取单聊消息     | 当前用户必须属于该会话                             |
| 发送单聊消息     | 当前用户必须属于该会话                             |
| 创建群聊         | 登录用户可创建，创建者为群主                       |
| 添加群成员       | 只有群主或管理员可添加                             |
| 移除群成员       | 只有群主或管理员可移除                             |
| 拉取群消息       | 当前用户必须是 active 群成员                       |
| 发送群消息       | 当前用户必须是 active 群成员                       |
| 下载文件块       | 当前用户必须是文件对应消息的接收者或群 active 成员 |
| 修改群信息       | 只有群主或管理员可修改                             |

特别注意：文件下载不能只依赖 `file_id`，必须检查当前用户是否有权访问该文件对应的消息。

------

## 15. 消息状态规范

消息状态建议包括：

| 状态      | 说明             |
| --------- | ---------------- |
| sending   | 客户端本地发送中 |
| sent      | 服务器已保存     |
| delivered | 接收方设备已收到 |
| read      | 接收方已读       |
| failed    | 发送失败         |

后端主要负责 `sent`、`delivered` 和 `read` 状态维护。`sending` 和 `failed` 可主要由前端本地维护。

------

## 16. 安全规范

### 16.1 密钥安全

| 规范                    | 说明                           |
| ----------------------- | ------------------------------ |
| 不接收 private_key 字段 | 后端接口不应包含私钥参数       |
| 不接收 session_key 字段 | 会话密钥只存在客户端           |
| 不接收 file_key 明文    | 文件密钥必须由客户端加密后上传 |
| 公钥记录版本            | 便于客户端识别密钥变化         |
| 公钥保存指纹            | 用于用户验证联系人身份         |

### 16.2 消息安全

| 规范                       | 说明                   |
| -------------------------- | ---------------------- |
| 不保存明文                 | 消息表只保存密文       |
| 不做明文内容审查           | 后端无法读取明文       |
| 不在日志中输出密文完整内容 | 避免日志过大和泄露风险 |
| 必须保存 nonce 和 auth_tag | 接收方解密时需要       |
| 校验消息大小               | 防止恶意超大请求       |

### 16.3 文件安全

| 规范                 | 说明                      |
| -------------------- | ------------------------- |
| 文件使用 UUID 命名   | 避免暴露原始文件名        |
| 原始文件名可加密保存 | 降低元数据泄露            |
| 文件块必须鉴权下载   | 防止通过 file_id 越权访问 |
| 限制上传大小         | 防止存储攻击              |
| 支持文件块完整性校验 | 通过 auth_tag 检测篡改    |

### 16.4 日志安全

日志中允许记录：

```text
user_id
message_id
conversation_id
group_id
file_id
request_id
error_code
created_at
```

日志中禁止记录：

```text
plaintext
private_key
session_key
file_key
解密后的图片内容
解密后的文件内容
```

------

## 17. 参数校验规范

后端应对所有接口进行参数校验。

| 参数           | 校验规则                         |
| -------------- | -------------------------------- |
| `ciphertext`   | 必填，字符串，长度受限           |
| `nonce`        | 必填，字符串，长度合法           |
| `auth_tag`     | 必填，字符串，长度合法           |
| `message_type` | 必须属于允许范围                 |
| `receiver_id`  | 必须存在且合法                   |
| `group_id`     | 必须存在且当前用户有权限         |
| `chunk_index`  | 必须为非负整数                   |
| `chunk_count`  | 必须大于 0                       |
| `file_size`    | 不得超过系统限制                 |
| `mime_type`    | 必须属于允许范围或通过白名单校验 |

------

## 18. 分页与查询规范

消息列表和会话列表需要支持分页，避免一次性返回过多数据。

### 18.1 消息拉取接口示例

```text
GET /api/messages/{conversation_id}/?before=501&limit=30
```

参数说明：

| 参数     | 说明                           |
| -------- | ------------------------------ |
| `before` | 拉取指定消息 ID 之前的历史消息 |
| `limit`  | 每次拉取数量，建议默认 30      |

### 18.2 群消息拉取接口示例

```text
GET /api/groups/{group_id}/messages/?before=888&limit=30
```

后端返回的仍然是当前用户有权访问的密文消息。

------

## 19. 测试规范

后端测试不仅要验证接口是否可用，还要验证端到端加密的约束是否被破坏。

| 测试编号 | 测试目标       | 操作                     | 预期结果                   |
| -------- | -------------- | ------------------------ | -------------------------- |
| TC-BE-01 | 用户注册登录   | 注册并登录用户           | 返回成功，密码被哈希存储   |
| TC-BE-02 | 公钥上传       | 上传用户公钥             | 数据库保存公钥，不保存私钥 |
| TC-BE-03 | 单聊密文发送   | A 向 B 发送密文          | 数据库只保存 ciphertext    |
| TC-BE-04 | 单聊越权访问   | C 拉取 A 和 B 的会话     | 返回 403                   |
| TC-BE-05 | 群聊创建       | A 创建群聊并添加 B、C    | 群成员表写入成功           |
| TC-BE-06 | 群聊密文发送   | A 在群中发送消息         | 每个 active 成员有独立密文 |
| TC-BE-07 | 非群成员访问   | D 拉取群消息             | 返回 403                   |
| TC-BE-08 | 成员退出       | C 退出后 A 再发群消息    | C 不再收到新密文           |
| TC-BE-09 | 文件初始化     | A 上传 100MB 图片        | 创建文件元数据成功         |
| TC-BE-10 | 文件分块上传   | 上传多个 encrypted_chunk | 文件块按序保存             |
| TC-BE-11 | 文件越权下载   | 非接收者下载文件块       | 返回 403                   |
| TC-BE-12 | 明文检索       | 在数据库搜索原始消息     | 搜不到明文                 |
| TC-BE-13 | WebSocket 鉴权 | 未登录连接 WebSocket     | 连接被拒绝                 |
| TC-BE-14 | WebSocket 推送 | A 发送消息给 B           | B 收到密文推送             |

------

## 20. 第一阶段实现范围

第一阶段建议优先实现以下功能：

| 功能               | 是否必须 | 说明            |
| ------------------ | -------- | --------------- |
| 用户注册登录       | 必须     | 系统基础功能    |
| 用户公钥上传与查询 | 必须     | E2EE 必备       |
| 单聊会话创建       | 必须     | 单聊基础        |
| 单聊密文消息发送   | 必须     | 核心功能        |
| WebSocket 单聊推送 | 必须     | 实时通信        |
| 群聊创建与成员管理 | 必须     | 群聊基础        |
| 群聊逐成员密文发送 | 必须     | 基础版群聊 E2EE |
| 数据库无明文验证   | 必须     | 答辩和测试重点  |
| 文件分块上传       | 可选     | 有时间实现      |
| Sender Key 群聊    | 可选     | 后续扩展        |
| Double Ratchet     | 可选     | 后续扩展        |
| 多设备同步         | 可选     | 后续扩展        |

第一阶段目标是完成一个可运行、可演示、数据库中无明文的端到端加密聊天系统。

------

## 21. 后续扩展方向

后续版本可继续扩展以下能力：

| 扩展方向              | 说明                             |
| --------------------- | -------------------------------- |
| Signal 风格预密钥机制 | 支持离线建立加密会话             |
| Double Ratchet        | 每条消息派生新的消息密钥         |
| Sender Key 群聊       | 提升群聊加密效率                 |
| 多设备同步            | 同一用户多个设备独立密钥管理     |
| 文件断点续传          | 提升大文件上传体验               |
| 文件秒传去重          | 需谨慎设计，避免破坏隐私         |
| 更细粒度权限          | 支持管理员、禁言、邀请确认等     |
| 消息撤回              | 删除服务器密文记录，并通知客户端 |
| 定时销毁消息          | 到期删除密文和文件块             |

------

## 22. 总结

本系统后端设计围绕端到端加密通信展开。后端不保存私钥、不生成会话密钥、不解密消息、不保存明文，只负责用户认证、公钥管理、会话关系、群成员关系、密文消息存储、加密文件存储和 WebSocket 转发。

通过该设计，系统能够在保证聊天功能完整性的同时，降低服务器侧泄露用户聊天内容的风险。第一阶段重点完成用户注册登录、公钥管理、单聊密文消息、群聊逐成员密文消息和 WebSocket 实时推送；后续可进一步扩展大文件分块加密、Sender Key 群聊和 Double Ratchet 等高级安全机制。