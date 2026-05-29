# iChat Pro 数据库设计规范文档

## 1. 设计目标

本数据库设计规范面向 iChat Pro 基于端到端加密的轻量级即时通讯系统。数据库需要支撑用户注册登录、联系人管理、好友申请、私聊、群聊、历史消息、端到端加密元数据、加密文件分块存储、WebSocket 消息状态、管理员后台管理等核心功能。

本系统数据库设计的核心目标如下：

| 目标           | 说明                                                         |
| -------------- | ------------------------------------------------------------ |
| 支撑业务完整性 | 覆盖用户、联系人、会话、消息、群聊、文件、管理日志等核心业务数据 |
| 支撑端到端加密 | 只保存公钥、密文、随机数、认证标签和加密元数据，不保存私钥和明文 |
| 支撑实时通信   | 保存消息状态、会话更新时间、未读计数等数据，便于 WebSocket 推送和会话列表展示 |
| 支撑文件扩展   | 支持图片、文件、表情包等多媒体消息的加密文件元数据和分块存储 |
| 支撑权限控制   | 通过会话成员表、群成员表、文件密钥表等关系表判断用户是否有权访问数据 |
| 支撑课程提交   | 能够生成 ER 图、字段说明表和 SQL 建表文件，满足软件工程大作业的数据模型章节要求 |

------

## 2. 数据库技术选型

系统第一阶段采用 SQLite 作为数据库。SQLite 轻量、无需单独部署数据库服务，适合课程项目开发、演示和提交。Django 默认支持 SQLite，可以通过 Django ORM 快速定义数据模型并生成数据库表。

| 技术         | 用途                                         |
| ------------ | -------------------------------------------- |
| SQLite       | 第一阶段轻量级关系数据库                     |
| Django ORM   | 定义模型、管理迁移、操作数据库               |
| Django Admin | 管理用户、群组、消息元数据、管理员日志       |
| 本地文件系统 | 保存加密后的文件块，数据库只保存路径和元数据 |

后续如果需要部署到生产环境，可以将 SQLite 替换为 PostgreSQL 或 MySQL，但表结构设计原则保持不变。

------

## 3. 数据库总体设计原则

### 3.1 安全原则

数据库必须遵守以下安全原则：

1. 不保存用户私钥。
2. 不保存 `session_key` 明文。
3. 不保存 `file_key` 明文。
4. 不保存聊天消息明文。
5. 不保存原始图片、原始文件或原始文件路径。
6. 所有消息内容均以密文形式保存。
7. 所有 AES-GCM 密文必须保存对应的 `nonce` 和 `auth_tag`。
8. 用户公钥需要保存密钥版本和公钥指纹。
9. 文件名如涉及隐私，应保存加密后的文件名。
10. 管理员只能查看密文和元数据，不能查看端到端加密消息明文。

### 3.2 命名原则

数据库表名采用小写英文和下划线命名。字段名采用语义清晰的小写英文和下划线命名。

| 类型     | 命名示例                                                |
| -------- | ------------------------------------------------------- |
| 表名     | `user_profile`, `contact`, `encrypted_message`          |
| 主键     | `id`                                                    |
| 外键     | `user_id`, `group_id`, `conversation_id`                |
| 时间字段 | `created_at`, `updated_at`, `deleted_at`                |
| 状态字段 | `status`, `role`, `message_type`                        |
| 加密字段 | `ciphertext`, `nonce`, `auth_tag`, `encrypted_file_key` |

### 3.3 时间字段原则

核心业务表建议包含：

| 字段         | 说明             |
| ------------ | ---------------- |
| `created_at` | 创建时间         |
| `updated_at` | 更新时间         |
| `deleted_at` | 软删除时间，可选 |

对于消息、文件、成员关系等业务表，应根据场景增加 `sent_at`、`delivered_at`、`read_at`、`joined_at`、`left_at` 等时间字段。

### 3.4 软删除原则

对于用户、联系人、群聊、消息、文件等关键数据，建议优先采用软删除。

| 字段         | 说明                                           |
| ------------ | ---------------------------------------------- |
| `status`     | 表示 active、deleted、disabled、removed 等状态 |
| `deleted_at` | 记录删除时间                                   |

软删除可以避免误删数据，也便于管理员审计和系统测试。

------

## 4. 数据表总览

系统数据库按业务模块划分为以下几类：

| 模块         | 数据表                                                       | 说明                               |
| ------------ | ------------------------------------------------------------ | ---------------------------------- |
| 用户模块     | `user_profile`                                               | 用户扩展资料                       |
| 密钥模块     | `user_key`                                                   | 用户公钥、公钥指纹、密钥版本       |
| 联系人模块   | `friend_request`, `contact`                                  | 好友申请、联系人关系               |
| 会话模块     | `conversation`, `conversation_member`                        | 私聊/群聊统一会话模型              |
| 单聊消息模块 | `encrypted_message`                                          | 单聊密文消息                       |
| 群聊模块     | `group_chat`, `group_member`, `group_message`, `group_message_recipient` | 群聊、群成员、群消息、群成员密文   |
| 文件模块     | `encrypted_file`, `encrypted_file_chunk`, `encrypted_file_key` | 加密文件、文件块、文件密钥密文     |
| 消息状态模块 | `message_receipt`                                            | 可选，用于多设备或更细粒度消息状态 |
| 管理模块     | `admin_operation_log`                                        | 管理员操作日志                     |

------

## 5. 用户与账号相关数据表

### 5.1 用户基础表

Django 默认提供 `auth_user` 表，用于保存用户账号、密码哈希、权限等基础信息。本系统建议直接使用 Django 自带用户表作为登录认证基础。

`auth_user` 中重点使用字段如下：

| 字段           | 说明           |
| -------------- | -------------- |
| `id`           | 用户 ID        |
| `username`     | 用户名         |
| `password`     | 密码哈希       |
| `email`        | 邮箱，可选     |
| `is_active`    | 账号是否启用   |
| `is_staff`     | 是否可进入后台 |
| `is_superuser` | 是否超级管理员 |
| `date_joined`  | 注册时间       |
| `last_login`   | 最后登录时间   |

说明：密码必须以哈希形式保存，不允许保存明文密码。

### 5.2 用户资料表 `user_profile`

`user_profile` 用于保存聊天系统中的用户扩展资料。

| 字段         | 类型         | 约束                 | 说明             |
| ------------ | ------------ | -------------------- | ---------------- |
| `id`         | integer      | PK                   | 资料 ID          |
| `user_id`    | integer      | FK, unique, not null | 对应 Django 用户 |
| `nickname`   | varchar(64)  | not null             | 用户昵称         |
| `avatar`     | varchar(255) | nullable             | 头像路径或 URL   |
| `bio`        | varchar(255) | nullable             | 个性签名         |
| `status`     | varchar(20)  | default active       | 账号业务状态     |
| `created_at` | datetime     | not null             | 创建时间         |
| `updated_at` | datetime     | not null             | 更新时间         |

`status` 可取值：

| 值         | 说明           |
| ---------- | -------------- |
| `active`   | 正常           |
| `disabled` | 被管理员禁用   |
| `deleted`  | 已注销或软删除 |

建议索引：

| 索引字段   | 作用                     |
| ---------- | ------------------------ |
| `user_id`  | 快速通过用户 ID 查询资料 |
| `nickname` | 支持昵称搜索             |

------

## 6. 密钥相关数据表

### 6.1 用户公钥表 `user_key`

`user_key` 用于保存用户端到端加密所需的公钥、算法、指纹和密钥版本。

| 字段                  | 类型         | 约束         | 说明                              |
| --------------------- | ------------ | ------------ | --------------------------------- |
| `id`                  | integer      | PK           | 密钥记录 ID                       |
| `user_id`             | integer      | FK, not null | 所属用户                          |
| `identity_public_key` | text         | not null     | 用户身份公钥                      |
| `pre_key_public`      | text         | nullable     | 预密钥公钥，Signal 风格增强版使用 |
| `key_fingerprint`     | varchar(128) | not null     | 公钥指纹                          |
| `algorithm`           | varchar(50)  | not null     | 公钥算法，例如 ECDH-X25519        |
| `key_version`         | integer      | not null     | 密钥版本                          |
| `is_active`           | boolean      | default true | 是否当前有效                      |
| `created_at`          | datetime     | not null     | 创建时间                          |
| `updated_at`          | datetime     | not null     | 更新时间                          |

安全要求：

1. 该表只保存公钥，不保存私钥。
2. 同一用户可以有多条历史公钥记录，但只能有一条当前有效公钥。
3. 用户更新公钥时，应生成新的 `key_version`。
4. 客户端应根据 `key_fingerprint` 判断联系人公钥是否变化。

建议唯一约束：

| 字段组合                | 说明                               |
| ----------------------- | ---------------------------------- |
| `user_id + key_version` | 同一用户同一版本只能有一条密钥记录 |

建议索引：

| 索引字段          | 作用             |
| ----------------- | ---------------- |
| `user_id`         | 查询用户当前公钥 |
| `key_fingerprint` | 公钥指纹校验     |
| `is_active`       | 查询当前有效密钥 |

------

## 7. 联系人与好友申请数据表

### 7.1 好友申请表 `friend_request`

`friend_request` 用于保存用户之间的好友申请记录。

| 字段           | 类型         | 约束            | 说明     |
| -------------- | ------------ | --------------- | -------- |
| `id`           | integer      | PK              | 申请 ID  |
| `from_user_id` | integer      | FK, not null    | 申请人   |
| `to_user_id`   | integer      | FK, not null    | 接收人   |
| `remark`       | varchar(255) | nullable        | 申请备注 |
| `status`       | varchar(20)  | default pending | 申请状态 |
| `created_at`   | datetime     | not null        | 申请时间 |
| `handled_at`   | datetime     | nullable        | 处理时间 |

`status` 可取值：

| 值          | 说明   |
| ----------- | ------ |
| `pending`   | 待处理 |
| `accepted`  | 已同意 |
| `rejected`  | 已拒绝 |
| `cancelled` | 已取消 |

建议约束：

| 约束                         | 说明                   |
| ---------------------------- | ---------------------- |
| `from_user_id != to_user_id` | 不能给自己发送好友申请 |
| 同一双方 pending 申请唯一    | 防止重复发送好友申请   |

建议索引：

| 索引字段       | 作用             |
| -------------- | ---------------- |
| `from_user_id` | 查询我发出的申请 |
| `to_user_id`   | 查询我收到的申请 |
| `status`       | 查询待处理申请   |

### 7.2 联系人表 `contact`

`contact` 用于保存用户之间的联系人关系。用户与用户之间是多对多关系，通过联系人表进行拆分。

| 字段              | 类型        | 约束           | 说明         |
| ----------------- | ----------- | -------------- | ------------ |
| `id`              | integer     | PK             | 联系关系 ID  |
| `owner_id`        | integer     | FK, not null   | 当前用户     |
| `contact_user_id` | integer     | FK, not null   | 联系人用户   |
| `alias`           | varchar(64) | nullable       | 备注名       |
| `status`          | varchar(20) | default active | 联系关系状态 |
| `created_at`      | datetime    | not null       | 建立时间     |
| `updated_at`      | datetime    | not null       | 更新时间     |

`status` 可取值：

| 值        | 说明       |
| --------- | ---------- |
| `active`  | 正常联系人 |
| `blocked` | 已拉黑     |
| `deleted` | 已删除     |

建议唯一约束：

| 字段组合                     | 说明                           |
| ---------------------------- | ------------------------------ |
| `owner_id + contact_user_id` | 同一用户不能重复添加同一联系人 |

说明：如果 A 与 B 成为好友，可以保存两条记录：A 的联系人中有 B，B 的联系人中有 A。这样便于双方设置不同备注名或删除状态。

------

## 8. 会话相关数据表

### 8.1 会话表 `conversation`

`conversation` 用于统一表示私聊会话和群聊会话。

| 字段              | 类型        | 约束           | 说明         |
| ----------------- | ----------- | -------------- | ------------ |
| `id`              | integer     | PK             | 会话 ID      |
| `type`            | varchar(20) | not null       | 会话类型     |
| `created_by_id`   | integer     | FK, nullable   | 创建者       |
| `last_message_at` | datetime    | nullable       | 最近消息时间 |
| `last_message_id` | integer     | nullable       | 最近消息 ID  |
| `status`          | varchar(20) | default active | 会话状态     |
| `created_at`      | datetime    | not null       | 创建时间     |
| `updated_at`      | datetime    | not null       | 更新时间     |

`type` 可取值：

| 值       | 说明     |
| -------- | -------- |
| `single` | 私聊会话 |
| `group`  | 群聊会话 |

`status` 可取值：

| 值         | 说明   |
| ---------- | ------ |
| `active`   | 正常   |
| `archived` | 已归档 |
| `deleted`  | 已删除 |

建议索引：

| 索引字段          | 作用                   |
| ----------------- | ---------------------- |
| `type`            | 区分私聊/群聊          |
| `last_message_at` | 会话列表按最近消息排序 |

### 8.2 会话成员表 `conversation_member`

`conversation_member` 用于保存会话与用户之间的成员关系。

| 字段                   | 类型        | 约束           | 说明            |
| ---------------------- | ----------- | -------------- | --------------- |
| `id`                   | integer     | PK             | 关系 ID         |
| `conversation_id`      | integer     | FK, not null   | 会话 ID         |
| `user_id`              | integer     | FK, not null   | 用户 ID         |
| `joined_at`            | datetime    | not null       | 加入时间        |
| `left_at`              | datetime    | nullable       | 退出时间        |
| `status`               | varchar(20) | default active | 成员状态        |
| `unread_count`         | integer     | default 0      | 未读数          |
| `last_read_message_id` | integer     | nullable       | 最近已读消息 ID |

`status` 可取值：

| 值        | 说明         |
| --------- | ------------ |
| `active`  | 当前成员     |
| `left`    | 已退出       |
| `removed` | 已移除       |
| `muted`   | 免打扰，可选 |

建议唯一约束：

| 字段组合                    | 说明                               |
| --------------------------- | ---------------------------------- |
| `conversation_id + user_id` | 同一会话中同一用户只有一条成员记录 |

建议索引：

| 索引字段                   | 作用             |
| -------------------------- | ---------------- |
| `conversation_id`          | 查询会话成员     |
| `user_id`                  | 查询用户会话列表 |
| `conversation_id + status` | 查询 active 成员 |

------

## 9. 单聊密文消息表

### 9.1 单聊密文消息表 `encrypted_message`

`encrypted_message` 用于保存一对一私聊消息密文。该表不得保存明文内容。

| 字段                   | 类型        | 约束         | 说明             |
| ---------------------- | ----------- | ------------ | ---------------- |
| `id`                   | integer     | PK           | 消息 ID          |
| `conversation_id`      | integer     | FK, not null | 所属会话         |
| `sender_id`            | integer     | FK, not null | 发送者           |
| `receiver_id`          | integer     | FK, not null | 接收者           |
| `message_type`         | varchar(20) | not null     | 消息类型         |
| `ciphertext`           | text        | nullable     | 文本类消息密文   |
| `nonce`                | varchar(64) | nullable     | AES-GCM 随机数   |
| `auth_tag`             | varchar(64) | nullable     | AES-GCM 认证标签 |
| `algorithm`            | varchar(50) | not null     | 加密算法         |
| `sender_key_version`   | integer     | nullable     | 发送者公钥版本   |
| `receiver_key_version` | integer     | nullable     | 接收者公钥版本   |
| `status`               | varchar(20) | default sent | 消息状态         |
| `created_at`           | datetime    | not null     | 发送时间         |
| `updated_at`           | datetime    | not null     | 更新时间         |
| `deleted_at`           | datetime    | nullable     | 删除时间         |

`message_type` 可取值：

| 值        | 说明     |
| --------- | -------- |
| `text`    | 文本     |
| `image`   | 图片     |
| `file`    | 文件     |
| `sticker` | 表情包   |
| `system`  | 系统消息 |

`status` 可取值：

| 值          | 说明                       |
| ----------- | -------------------------- |
| `sent`      | 服务器已保存               |
| `delivered` | 接收方已收到               |
| `read`      | 接收方已读                 |
| `deleted`   | 已删除                     |
| `failed`    | 发送失败，通常前端本地状态 |

字段说明：

1. 文本消息直接使用 `ciphertext`、`nonce`、`auth_tag` 保存密文。
2. 图片、文件、表情包消息可以通过 `message_type` 标识，并关联 `encrypted_file` 表。
3. 该表不得出现 `plaintext`、`content_plain`、`message_text` 等明文字段。

建议索引：

| 索引字段                       | 作用                 |
| ------------------------------ | -------------------- |
| `conversation_id + created_at` | 分页加载历史消息     |
| `sender_id`                    | 查询用户发送记录     |
| `receiver_id`                  | 查询用户接收记录     |
| `status`                       | 查询消息状态         |
| `conversation_id + id`         | 基于 message_id 分页 |

------

## 10. 群聊相关数据表

### 10.1 群聊表 `group_chat`

`group_chat` 用于保存群聊资料。

| 字段              | 类型         | 约束                 | 说明        |
| ----------------- | ------------ | -------------------- | ----------- |
| `id`              | integer      | PK                   | 群聊 ID     |
| `conversation_id` | integer      | FK, unique, not null | 对应会话 ID |
| `name`            | varchar(100) | not null             | 群名称      |
| `avatar`          | varchar(255) | nullable             | 群头像      |
| `owner_id`        | integer      | FK, not null         | 群主        |
| `status`          | varchar(20)  | default active       | 群状态      |
| `created_at`      | datetime     | not null             | 创建时间    |
| `updated_at`      | datetime     | not null             | 更新时间    |

`status` 可取值：

| 值          | 说明         |
| ----------- | ------------ |
| `active`    | 正常         |
| `dismissed` | 已解散       |
| `deleted`   | 被管理员删除 |

建议索引：

| 索引字段          | 作用             |
| ----------------- | ---------------- |
| `owner_id`        | 查询用户创建的群 |
| `conversation_id` | 从会话定位群资料 |
| `status`          | 管理员筛选群状态 |

### 10.2 群成员表 `group_member`

`group_member` 用于保存群聊与用户之间的成员关系。用户与群组之间是多对多关系，需要通过该表拆分。

| 字段            | 类型        | 约束           | 说明      |
| --------------- | ----------- | -------------- | --------- |
| `id`            | integer     | PK             | 群成员 ID |
| `group_id`      | integer     | FK, not null   | 群聊 ID   |
| `user_id`       | integer     | FK, not null   | 用户 ID   |
| `role`          | varchar(20) | default member | 群内角色  |
| `status`        | varchar(20) | default active | 成员状态  |
| `joined_at`     | datetime    | not null       | 加入时间  |
| `left_at`       | datetime    | nullable       | 退出时间  |
| `invited_by_id` | integer     | FK, nullable   | 邀请人    |

`role` 可取值：

| 值       | 说明     |
| -------- | -------- |
| `owner`  | 群主     |
| `admin`  | 管理员   |
| `member` | 普通成员 |

`status` 可取值：

| 值        | 说明     |
| --------- | -------- |
| `active`  | 当前成员 |
| `left`    | 主动退出 |
| `removed` | 被移除   |

建议唯一约束：

| 字段组合             | 说明                                 |
| -------------------- | ------------------------------------ |
| `group_id + user_id` | 一个用户在同一个群中只有一条成员记录 |

建议索引：

| 索引字段            | 作用             |
| ------------------- | ---------------- |
| `group_id + status` | 查询当前群成员   |
| `user_id + status`  | 查询用户加入的群 |
| `role`              | 查询群主/管理员  |

### 10.3 群消息表 `group_message`

`group_message` 用于保存群消息的逻辑记录，不保存具体明文内容。基础版群聊端到端加密采用逐成员加密，因此每个接收者的具体密文保存在 `group_message_recipient` 中。

| 字段           | 类型        | 约束         | 说明      |
| -------------- | ----------- | ------------ | --------- |
| `id`           | integer     | PK           | 群消息 ID |
| `group_id`     | integer     | FK, not null | 群聊 ID   |
| `sender_id`    | integer     | FK, not null | 发送者    |
| `message_type` | varchar(20) | not null     | 消息类型  |
| `algorithm`    | varchar(50) | not null     | 加密算法  |
| `status`       | varchar(20) | default sent | 消息状态  |
| `created_at`   | datetime    | not null     | 发送时间  |
| `updated_at`   | datetime    | not null     | 更新时间  |
| `deleted_at`   | datetime    | nullable     | 删除时间  |

说明：

1. 该表不保存群聊明文。
2. 该表也不直接保存统一的 `ciphertext`，因为基础版群聊中每个成员对应一份不同密文。
3. `message_type` 为 image/file/sticker 时，关联 `encrypted_file` 表保存加密文件元数据。

建议索引：

| 索引字段                | 作用              |
| ----------------------- | ----------------- |
| `group_id + created_at` | 群聊历史消息分页  |
| `sender_id`             | 查询发送者消息    |
| `status`                | 查询删除/正常消息 |

### 10.4 群消息接收者密文表 `group_message_recipient`

`group_message_recipient` 用于保存每个群成员对应的群消息密文。

| 字段           | 类型        | 约束           | 说明             |
| -------------- | ----------- | -------------- | ---------------- |
| `id`           | integer     | PK             | 主键             |
| `message_id`   | integer     | FK, not null   | 群消息 ID        |
| `receiver_id`  | integer     | FK, not null   | 接收者           |
| `ciphertext`   | text        | nullable       | 发给该用户的密文 |
| `nonce`        | varchar(64) | nullable       | 随机数           |
| `auth_tag`     | varchar(64) | nullable       | 认证标签         |
| `key_version`  | integer     | nullable       | 接收者密钥版本   |
| `delivered_at` | datetime    | nullable       | 送达时间         |
| `read_at`      | datetime    | nullable       | 已读时间         |
| `status`       | varchar(20) | default unread | 接收状态         |

`status` 可取值：

| 值          | 说明   |
| ----------- | ------ |
| `unread`    | 未读   |
| `delivered` | 已送达 |
| `read`      | 已读   |
| `deleted`   | 已删除 |

建议唯一约束：

| 字段组合                   | 说明                                       |
| -------------------------- | ------------------------------------------ |
| `message_id + receiver_id` | 同一条群消息对同一接收者只能有一条密文记录 |

建议索引：

| 索引字段                | 作用                         |
| ----------------------- | ---------------------------- |
| `receiver_id + status`  | 查询用户未读群消息           |
| `message_id`            | 查询一条群消息所有接收者密文 |
| `receiver_id + read_at` | 查询已读状态                 |

------

## 11. 多媒体与加密文件数据表

### 11.1 加密文件表 `encrypted_file`

`encrypted_file` 保存图片、文件、表情包等多媒体消息的加密文件元数据。

| 字段                   | 类型         | 约束              | 说明                |
| ---------------------- | ------------ | ----------------- | ------------------- |
| `id`                   | integer      | PK                | 文件 ID             |
| `message_id`           | integer      | nullable          | 对应单聊消息 ID     |
| `group_message_id`     | integer      | nullable          | 对应群消息 ID       |
| `sender_id`            | integer      | FK, not null      | 发送者              |
| `file_ciphertext_path` | varchar(255) | nullable          | 加密文件存储路径    |
| `file_size`            | integer      | not null          | 原文件大小          |
| `chunk_size`           | integer      | not null          | 分块大小            |
| `chunk_count`          | integer      | not null          | 分块数量            |
| `mime_type`            | varchar(100) | not null          | 文件类型            |
| `encrypted_filename`   | text         | nullable          | 加密后的原始文件名  |
| `thumbnail_file_id`    | integer      | nullable          | 缩略图文件 ID，可选 |
| `upload_status`        | varchar(20)  | default uploading | 上传状态            |
| `created_at`           | datetime     | not null          | 创建时间            |
| `updated_at`           | datetime     | not null          | 更新时间            |

`upload_status` 可取值：

| 值          | 说明     |
| ----------- | -------- |
| `uploading` | 上传中   |
| `completed` | 上传完成 |
| `failed`    | 上传失败 |
| `deleted`   | 已删除   |

约束说明：

1. `message_id` 和 `group_message_id` 至少有一个不为空。
2. 服务器保存的是加密文件路径，不保存原始文件路径。
3. 大文件应通过 `chunk_count` 和 `chunk_size` 支持分块上传。

建议索引：

| 索引字段           | 作用               |
| ------------------ | ------------------ |
| `message_id`       | 查询单聊文件       |
| `group_message_id` | 查询群聊文件       |
| `sender_id`        | 查询用户发送的文件 |
| `upload_status`    | 查询未完成上传     |

### 11.2 加密文件块表 `encrypted_file_chunk`

`encrypted_file_chunk` 保存大文件分块密文的存储路径和认证信息。

| 字段             | 类型         | 约束         | 说明                  |
| ---------------- | ------------ | ------------ | --------------------- |
| `id`             | integer      | PK           | 文件块 ID             |
| `file_id`        | integer      | FK, not null | 所属文件              |
| `chunk_index`    | integer      | not null     | 文件块序号，从 0 开始 |
| `chunk_path`     | varchar(255) | not null     | 加密文件块路径        |
| `chunk_nonce`    | varchar(64)  | not null     | 文件块 nonce          |
| `chunk_auth_tag` | varchar(64)  | not null     | 文件块认证标签        |
| `chunk_size`     | integer      | not null     | 当前块大小            |
| `created_at`     | datetime     | not null     | 上传时间              |

建议唯一约束：

| 字段组合                | 说明                             |
| ----------------------- | -------------------------------- |
| `file_id + chunk_index` | 同一个文件同一个分块只能保存一次 |

建议索引：

| 索引字段                | 作用             |
| ----------------------- | ---------------- |
| `file_id + chunk_index` | 按顺序下载文件块 |

### 11.3 加密文件密钥表 `encrypted_file_key`

`encrypted_file_key` 用于保存每个接收者对应的加密文件密钥。文件内容只用 `file_key` 加密一次，`file_key` 再分别加密给每个接收者。

| 字段                 | 类型        | 约束         | 说明                               |
| -------------------- | ----------- | ------------ | ---------------------------------- |
| `id`                 | integer     | PK           | 主键                               |
| `file_id`            | integer     | FK, not null | 文件 ID                            |
| `receiver_id`        | integer     | FK, not null | 接收者                             |
| `encrypted_file_key` | text        | not null     | 使用 session_key 加密后的 file_key |
| `key_nonce`          | varchar(64) | not null     | 加密 file_key 使用的 nonce         |
| `key_auth_tag`       | varchar(64) | not null     | 加密 file_key 的认证标签           |
| `key_version`        | integer     | nullable     | 接收者密钥版本                     |
| `created_at`         | datetime    | not null     | 创建时间                           |

安全要求：

1. 不保存明文 `file_key`。
2. 单聊中通常为接收者和发送者各保存一份 `encrypted_file_key`。
3. 群聊中为每个 active 群成员保存一份 `encrypted_file_key`。
4. 非接收者即使下载了加密文件块，也无法解密 `file_key`。

建议唯一约束：

| 字段组合                | 说明                                       |
| ----------------------- | ------------------------------------------ |
| `file_id + receiver_id` | 一个文件对一个接收者只保存一条文件密钥密文 |

------

## 12. 管理员与审计日志数据表

### 12.1 管理员操作日志表 `admin_operation_log`

`admin_operation_log` 用于记录管理员关键操作。

| 字段          | 类型        | 约束         | 说明         |
| ------------- | ----------- | ------------ | ------------ |
| `id`          | integer     | PK           | 日志 ID      |
| `admin_id`    | integer     | FK, not null | 操作管理员   |
| `target_type` | varchar(50) | not null     | 操作对象类型 |
| `target_id`   | integer     | nullable     | 操作对象 ID  |
| `action`      | varchar(50) | not null     | 操作类型     |
| `description` | text        | nullable     | 操作说明     |
| `ip_address`  | varchar(64) | nullable     | 操作 IP      |
| `created_at`  | datetime    | not null     | 操作时间     |

`target_type` 可取值：

| 值        | 说明     |
| --------- | -------- |
| `user`    | 用户     |
| `group`   | 群聊     |
| `message` | 消息     |
| `file`    | 文件     |
| `system`  | 系统配置 |

`action` 可取值：

| 值               | 说明       |
| ---------------- | ---------- |
| `disable_user`   | 禁用用户   |
| `enable_user`    | 恢复用户   |
| `delete_group`   | 删除群聊   |
| `delete_message` | 删除消息   |
| `delete_file`    | 删除文件   |
| `login_admin`    | 管理员登录 |

日志安全要求：

1. 日志不得记录消息明文。
2. 日志不得记录用户私钥、session_key、file_key。
3. 日志可记录对象 ID、操作类型、错误码和时间。

------

## 13. 消息状态与已读扩展表

### 13.1 消息回执表 `message_receipt`

第一阶段可以直接在 `encrypted_message` 和 `group_message_recipient` 中保存消息状态。如果后续需要多设备、多接收端、更细粒度的已读回执，可以增加 `message_receipt` 表。

| 字段           | 类型         | 约束         | 说明              |
| -------------- | ------------ | ------------ | ----------------- |
| `id`           | integer      | PK           | 回执 ID           |
| `message_type` | varchar(20)  | not null     | single/group      |
| `message_id`   | integer      | not null     | 消息 ID           |
| `user_id`      | integer      | FK, not null | 用户 ID           |
| `delivered_at` | datetime     | nullable     | 送达时间          |
| `read_at`      | datetime     | nullable     | 已读时间          |
| `device_id`    | varchar(128) | nullable     | 设备 ID，后续扩展 |
| `created_at`   | datetime     | not null     | 创建时间          |

说明：该表为后续扩展表，第一阶段可暂不实现。

------

## 14. 字段枚举规范

### 14.1 消息类型 `message_type`

| 值        | 说明     |
| --------- | -------- |
| `text`    | 文本消息 |
| `image`   | 图片消息 |
| `file`    | 文件消息 |
| `sticker` | 表情包   |
| `system`  | 系统消息 |

### 14.2 会话类型 `conversation.type`

| 值       | 说明 |
| -------- | ---- |
| `single` | 私聊 |
| `group`  | 群聊 |

### 14.3 消息状态

| 值          | 说明         |
| ----------- | ------------ |
| `sent`      | 服务器已保存 |
| `delivered` | 接收方已收到 |
| `read`      | 接收方已读   |
| `deleted`   | 已删除       |
| `failed`    | 发送失败     |

### 14.4 群成员角色

| 值       | 说明     |
| -------- | -------- |
| `owner`  | 群主     |
| `admin`  | 管理员   |
| `member` | 普通成员 |

### 14.5 群成员状态

| 值        | 说明     |
| --------- | -------- |
| `active`  | 当前成员 |
| `left`    | 主动退出 |
| `removed` | 被移除   |

------

## 15. 表关系设计说明

### 15.1 用户与联系人关系

用户与用户之间存在多对多关系，通过 `contact` 表拆分。

```text
auth_user 1 —— N contact N —— 1 auth_user
```

说明：

1. `owner_id` 表示当前用户。
2. `contact_user_id` 表示联系人。
3. A 添加 B 后，可建立两条联系人记录，分别支持双方独立备注和状态管理。

### 15.2 用户与群聊关系

用户与群聊之间是多对多关系，通过 `group_member` 表拆分。

```text
auth_user 1 —— N group_member N —— 1 group_chat
```

说明：

1. 一个用户可以加入多个群。
2. 一个群可以包含多个用户。
3. `group_member.role` 区分群主、管理员、普通成员。
4. `group_member.status` 区分当前成员、已退出、已移除成员。

### 15.3 会话与成员关系

会话与用户之间是多对多关系，通过 `conversation_member` 表拆分。

```text
conversation 1 —— N conversation_member N —— 1 auth_user
```

说明：

1. 私聊会话中通常有两个 active 成员。
2. 群聊会话中可以有多个 active 成员。
3. 会话列表通过 `conversation_member.user_id` 查询当前用户参与的会话。

### 15.4 群消息与接收者密文关系

基础版群聊端到端加密采用逐成员加密，一条群消息对应多条接收者密文。

```text
group_message 1 —— N group_message_recipient N —— 1 auth_user
```

说明：

1. `group_message` 保存群消息逻辑记录。
2. `group_message_recipient` 保存每个接收者对应的密文。
3. 接收方只拉取属于自己的密文记录。

### 15.5 文件与文件密钥关系

一个加密文件可以对应多个接收者的文件密钥密文。

```text
encrypted_file 1 —— N encrypted_file_key N —— 1 auth_user
```

说明：

1. 文件本体只加密一次。
2. `encrypted_file_key` 为每个接收者保存一份加密后的 `file_key`。
3. 群聊发送文件时，不为每个成员重复上传完整文件，只分别保存文件密钥密文。

------

## 16. 索引设计规范

为了提高历史消息、会话列表、群成员、文件下载等查询效率，建议建立以下索引。

| 表名                      | 索引字段                      | 用途                   |
| ------------------------- | ----------------------------- | ---------------------- |
| `user_profile`            | `nickname`                    | 用户搜索               |
| `user_key`                | `user_id, is_active`          | 查询当前用户公钥       |
| `friend_request`          | `to_user_id, status`          | 查询收到的待处理申请   |
| `friend_request`          | `from_user_id, status`        | 查询发出的申请         |
| `contact`                 | `owner_id, status`            | 查询联系人列表         |
| `conversation_member`     | `user_id, status`             | 查询用户会话列表       |
| `conversation`            | `last_message_at`             | 会话列表排序           |
| `encrypted_message`       | `conversation_id, created_at` | 私聊历史消息分页       |
| `encrypted_message`       | `receiver_id, status`         | 查询接收方未读消息     |
| `group_member`            | `group_id, status`            | 查询 active 群成员     |
| `group_member`            | `user_id, status`             | 查询用户加入的群       |
| `group_message`           | `group_id, created_at`        | 群聊历史消息分页       |
| `group_message_recipient` | `receiver_id, status`         | 查询用户群消息状态     |
| `encrypted_file_chunk`    | `file_id, chunk_index`        | 按顺序下载文件块       |
| `encrypted_file_key`      | `file_id, receiver_id`        | 查询接收者文件密钥密文 |
| `admin_operation_log`     | `admin_id, created_at`        | 管理员操作审计         |

------

## 17. 数据安全与隐私规范

### 17.1 禁止字段

以下字段不得出现在数据库中：

```text
private_key
session_key
file_key
plaintext
plain_content
message_plaintext
original_file_path
decrypted_content
```

### 17.2 允许保存的加密相关字段

数据库允许保存以下字段：

```text
identity_public_key
key_fingerprint
ciphertext
nonce
auth_tag
algorithm
encrypted_file_key
encrypted_filename
chunk_nonce
chunk_auth_tag
key_version
```

### 17.3 管理员可见范围

管理员可以看到：

| 数据                         | 是否可见 |
| ---------------------------- | -------- |
| 用户账号                     | 可见     |
| 用户资料                     | 可见     |
| 群聊资料                     | 可见     |
| 群成员关系                   | 可见     |
| 消息发送者、接收者、发送时间 | 可见     |
| 消息密文                     | 可见     |
| 文件密文元数据               | 可见     |

管理员不能看到：

| 数据         | 是否可见 |
| ------------ | -------- |
| 用户私钥     | 不可见   |
| 会话密钥     | 不可见   |
| 文件密钥明文 | 不可见   |
| 消息明文     | 不可见   |
| 原始图片内容 | 不可见   |
| 原始文件内容 | 不可见   |

------

## 18. 数据一致性规范

### 18.1 发送单聊消息

发送单聊消息时，应保证：

1. 当前用户是会话成员。
2. 接收者是会话成员。
3. `ciphertext`、`nonce`、`auth_tag` 完整。
4. `encrypted_message` 写入成功后更新 `conversation.last_message_at`。
5. 接收方 `conversation_member.unread_count` 增加。

### 18.2 发送群聊消息

发送群聊消息时，应保证：

1. 发送者是 active 群成员。
2. 当前群 active 成员列表查询成功。
3. `group_message` 写入成功。
4. 每个 active 成员在 `group_message_recipient` 中都有一条密文记录。
5. 已退出或被移除成员不能收到新密文。
6. 更新会话最近消息时间。

### 18.3 发送文件消息

发送文件消息时，应保证：

1. 创建消息记录。
2. 创建 `encrypted_file` 文件元数据。
3. 保存所有 `encrypted_file_chunk` 文件块。
4. 为每个接收者创建 `encrypted_file_key`。
5. 文件所有分块上传完成后，更新 `encrypted_file.upload_status = completed`。
6. 如果上传失败，允许重传指定 `chunk_index`。

------

## 19. 第一阶段与后续扩展边界

### 19.1 第一阶段建议实现

| 数据表                    | 是否实现 | 说明                              |
| ------------------------- | -------- | --------------------------------- |
| `user_profile`            | 必须     | 用户资料                          |
| `user_key`                | 必须     | 端到端加密公钥                    |
| `friend_request`          | 必须     | 好友申请                          |
| `contact`                 | 必须     | 联系人关系                        |
| `conversation`            | 必须     | 会话                              |
| `conversation_member`     | 必须     | 会话成员                          |
| `encrypted_message`       | 必须     | 单聊密文消息                      |
| `group_chat`              | 必须     | 群聊资料                          |
| `group_member`            | 必须     | 群成员关系                        |
| `group_message`           | 必须     | 群消息逻辑记录                    |
| `group_message_recipient` | 建议     | 如果第一阶段实现群聊 E2EE，则必须 |
| `admin_operation_log`     | 建议     | 支撑后台管理截图                  |
| `encrypted_file`          | 可选     | 如果做图片/文件发送则实现         |
| `encrypted_file_chunk`    | 可选     | 如果做大文件分块则实现            |
| `encrypted_file_key`      | 可选     | 如果做文件 E2EE 则实现            |
| `message_receipt`         | 暂不实现 | 后续多设备或精细回执扩展          |

### 19.2 后续扩展表

| 扩展方向        | 可新增或增强的数据表                           |
| --------------- | ---------------------------------------------- |
| Double Ratchet  | `ratchet_session`, `message_chain_state`       |
| Sender Key 群聊 | `group_sender_key`, `sender_key_distribution`  |
| 多设备同步      | `user_device`, `device_key`                    |
| 消息撤回        | 在消息表中增加 `recalled_at`, `recalled_by_id` |
| 定时销毁        | 在消息表中增加 `expires_at`, `destroyed_at`    |
| 文件断点续传    | 增强 `encrypted_file_chunk` 上传状态字段       |

------

## 20. 数据库验收标准

数据库设计完成后，应满足以下验收标准：

| 验收项         | 标准                                                         |
| -------------- | ------------------------------------------------------------ |
| 表结构完整性   | 覆盖用户、联系人、会话、消息、群聊、文件、日志等核心数据     |
| 明文隔离       | 消息表和文件表中不存在明文字段                               |
| 密钥安全       | 数据库不保存 private_key、session_key、file_key 明文         |
| 加密元数据完整 | 密文消息保存 ciphertext、nonce、auth_tag、algorithm          |
| 群聊关系正确   | 群成员通过 group_member 拆分多对多关系                       |
| 会话关系正确   | 会话成员通过 conversation_member 拆分多对多关系              |
| 文件扩展可行   | 支持 encrypted_file、encrypted_file_chunk、encrypted_file_key |
| 查询效率       | 历史消息、会话列表、群成员查询字段建立索引                   |
| 权限可判断     | 能通过成员表、接收者表、文件密钥表判断用户访问权限           |
| 文档一致性     | 表结构应与需求文档、后端接口、前端界面和系统测试保持一致     |

------

## 21. 总结

iChat Pro 数据库设计围绕轻量级即时通讯和端到端加密两条主线展开。用户、联系人、会话、群聊、消息、文件和管理日志共同构成系统的数据基础；`user_key`、`encrypted_message`、`group_message_recipient`、`encrypted_file_key` 等表则支撑端到端加密机制。

数据库只保存业务元数据、公钥、密文、随机数、认证标签和加密后的文件密钥，不保存用户私钥、会话密钥、文件密钥明文和消息明文。通过联系人表、会话成员表、群成员表、群消息接收者表和文件密钥表，系统能够同时满足业务关系管理、权限控制、历史消息查询、群聊逐成员加密和大文件加密传输等需求。