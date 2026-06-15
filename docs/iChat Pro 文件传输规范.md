# iChat Pro 文件传输规范

> 状态：Draft v0.1  
> 适用范围：图片、普通文件、贴纸等多媒体消息传输  
> 设计目标：服务端只存储加密后的文件内容和必要元数据，不保存文件明文、明文文件名或明文 `file_key`。

## 1. 总体原则

1. 文件内容由客户端本地加密，服务端只负责鉴权、存储、转发文件元数据和提供下载。
2. 文件上传使用 HTTP API；消息通知、上传完成通知和接收端实时提醒使用现有 `/ws/chat/` WebSocket。
3. 小文件和大文件统一抽象为 `file_upload_session`，大文件必须分片上传。
4. 完成文件上传与发送文件消息是两个步骤：`complete` 只让文件可下载，消息发送仍遵守私聊/群聊现有密文消息结构。
5. 私聊和群聊共用同一个加密文件对象。群聊中，文件本体只上传一次，`file_key` 分别加密给每位接收成员。
6. 消息表中的 `message_type` 使用现有预留值：`image`、`file`、`sticker`。消息密文只承载文件消息描述，不直接承载二进制文件。
7. 文件名、MIME 类型、尺寸、缩略图等可能暴露隐私的信息，默认放入客户端加密后的 `encrypted_metadata`。
8. 服务端校验权限、大小、分片完整性、哈希一致性、幂等键和会话成员身份，但不解密文件。

## 2. 术语

| 名称 | 说明 |
| --- | --- |
| `file_id` | 服务端生成的加密文件 ID |
| `upload_id` | 文件上传会话 ID，上传完成前用于续传和分片提交 |
| `client_file_id` | 客户端为单个文件预生成的 UUID，用作幂等键和加密 AAD 的稳定文件标识 |
| `file_key` | 客户端为单个文件随机生成的对称密钥，32 字节，不上传明文 |
| `encrypted_file_key` | 使用文件密钥包装密钥加密后的 `file_key` |
| `chunk_index` | 分片序号，从 0 开始 |
| `chunk_size` | 分片明文大小建议值，默认 1 MiB；最后一片可小于该值。AES-256-GCM 下密文正文与明文等长，`auth_tag` 单独存储 |
| `file_sha256` | 原始明文文件 SHA-256，只能放入加密元数据；服务端可选保存密文哈希 |
| `ciphertext_sha256` | 加密后完整文件或分片密文的 SHA-256；完整文件哈希可在上传完成时提交，分片哈希必须逐片提交 |

## 3. 加密约定

### 3.1 文件内容加密

客户端为每个文件生成独立 `file_key`：

```text
file_key = random(32 bytes)
```

文件内容使用 `AES-256-GCM` 加密。分片模式下每个分片使用独立 nonce。AAD 中的 ID 固定使用 `client_file_id`，该值由发送端在创建上传会话前生成，并由服务端持久化后在文件详情接口返回给接收端。转发或追加新持有人时必须继续使用原始上传文件的 `client_file_id`，不得为同一密文文件重新生成新的 AAD 标识。

```text
chunk_nonce = random(12 bytes)
chunk_ciphertext, chunk_auth_tag = AES-GCM-Encrypt(
  key = file_key,
  nonce = chunk_nonce,
  plaintext = chunk_bytes,
  aad = "ichat-file-chunk-v1:" + client_file_id + ":" + chunk_index
)
```

约束：

1. 同一个 `file_key` 下 nonce 不得重复。
2. 每个分片单独保存 `nonce`、`auth_tag`、`ciphertext_sha256` 和 `size_bytes`。
3. 上传阶段服务端不解密分片，只校验密文大小、分片序号和密文哈希。
4. 完成上传时，服务端应按 `chunk_index` 顺序将分片密文正文拼接为一个完整密文文件，并清理临时分片文件；分片 nonce、auth tag 和偏移信息仍保存在数据库中，供客户端下载后分段解密。

文件元数据使用同一个 `file_key` 加密，算法也固定为 `AES-256-GCM`：

```text
metadata_nonce = random(12 bytes)
encrypted_metadata, metadata_auth_tag = AES-GCM-Encrypt(
  key = file_key,
  nonce = metadata_nonce,
  plaintext = utf8(metadata_json),
  aad = "ichat-file-metadata-v1:" + client_file_id
)
```

元数据加密约束：

1. `metadata_nonce` 与任何分片 nonce 都不得重复。
2. `metadata_json` 中可以包含原始文件名、MIME 类型、明文大小、明文哈希、图片尺寸和缩略图引用。
3. 服务端只校验 `encrypted_metadata`、`metadata_nonce`、`metadata_auth_tag` 的格式和长度，不解析元数据明文。

### 3.2 文件密钥封装

私聊：

1. 发送端生成 `file_key`。
2. 发送端基于双方 ECDH + HKDF 派生 `file_key_wrap_key`。
3. 使用 `AES-256-GCM` 和 `file_key_wrap_key` 加密 `file_key`，生成发给接收方的 `encrypted_file_key`。
4. 为了发送端自己多端查看，必须为发送者保存一份 `encrypted_file_key`，其持有人是发送者本人。

群聊：

1. 文件本体只加密并上传一次。
2. 对每个活跃群成员分别生成一条 `encrypted_file_key` 记录。
3. `membership_version` 必须与发送消息时的群成员版本一致；若过期，服务端返回 `membership_version_conflict`。

推荐 HKDF `info`：

```text
ichat-file-key-wrap-v1
```

密钥封装算法固定为：

```text
wrap_nonce = random(12 bytes)
encrypted_file_key, wrap_auth_tag = AES-GCM-Encrypt(
  key = file_key_wrap_key,
  nonce = wrap_nonce,
  plaintext = file_key,
  aad = "ichat-file-key-wrap-v1:" + file_id + ":" + holder_user_id
)
```

其中 `holder_user_id` 是该条文件密钥记录的持有人 ID。私聊通常包括发送者和接收者两条记录；群聊包括发送者和每个活跃群成员的记录。

`file_key` 明文、文件明文和明文文件名禁止进入数据库、HTTP 日志、WebSocket 日志和服务端异常日志。

## 4. 数据模型

### 4.1 `EncryptedFile`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint PK | 文件 ID |
| `client_file_id` | varchar | 发送端生成的文件 UUID，对同一上传者幂等 |
| `owner_id` | FK User | 上传发起人 |
| `conversation_id` | FK Conversation | 所属会话 |
| `message_kind` | varchar | `image`、`file`、`sticker` |
| `parent_file_id` | FK EncryptedFile nullable | 缩略图或派生文件指向原文件 |
| `derivative_role` | varchar nullable | `original`、`thumbnail`、`preview` |
| `status` | varchar | `uploading`、`available`、`failed`、`deleted` |
| `storage_path` | varchar | 完整密文文件路径，不含原始文件名 |
| `total_size_bytes` | bigint | 密文总大小 |
| `chunk_size_bytes` | int | 标准分片大小 |
| `chunk_count` | int | 分片数量 |
| `ciphertext_sha256` | varchar | 完整密文哈希，可选 |
| `encrypted_metadata` | text | 加密后的元数据 JSON |
| `metadata_nonce` | varchar | 元数据 nonce |
| `metadata_auth_tag` | varchar | 元数据认证标签 |
| `algorithm` | varchar | `AES-256-GCM` |
| `expires_at` | datetime | 可选，临时文件清理时间 |
| `created_at` | datetime | 创建时间 |
| `updated_at` | datetime | 更新时间 |
| `deleted_at` | datetime | 软删除时间 |

唯一约束：

```text
unique(owner_id, client_file_id)
```

`encrypted_metadata` 明文结构由客户端解密后使用：

```json
{
  "original_name": "photo.jpg",
  "mime_type": "image/jpeg",
  "plain_size_bytes": 2489132,
  "plain_sha256": "hex",
  "width": 1920,
  "height": 1080,
  "duration_ms": null,
  "thumbnail_file_id": 123,
  "caption": ""
}
```

### 4.2 `EncryptedFileChunk`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint PK | 分片 ID |
| `file_id` | FK EncryptedFile | 文件 ID |
| `chunk_index` | int | 从 0 开始 |
| `size_bytes` | int | 当前分片密文大小 |
| `offset_bytes` | bigint | 当前分片在完整密文文件中的起始偏移 |
| `nonce` | varchar | 分片 nonce |
| `auth_tag` | varchar | 分片认证标签 |
| `ciphertext_sha256` | varchar | 分片密文哈希 |
| `storage_path` | varchar nullable | 上传完成前的临时分片密文路径；合并为完整密文文件后置空 |
| `created_at` | datetime | 上传时间 |

唯一约束：

```text
unique(file_id, chunk_index)
```

### 4.3 `EncryptedFileKey`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint PK | 文件密钥记录 ID |
| `file_id` | FK EncryptedFile | 文件 ID |
| `holder_id` | FK User | 持有该文件密钥、可解密该文件的用户 |
| `encrypted_file_key` | text | 加密后的 `file_key` |
| `nonce` | varchar | 密钥封装 nonce |
| `auth_tag` | varchar | 密钥封装认证标签 |
| `algorithm` | varchar | `AES-256-GCM` |
| `sender_key_version` | int | 发送者公钥版本 |
| `receiver_key_version` | int | 接收者公钥版本 |
| `membership_version` | int | 群聊成员版本，私聊可为空 |
| `created_at` | datetime | 创建时间 |

唯一约束：

```text
unique(file_id, holder_id)
```

### 4.4 消息关联

私聊 `EncryptedMessage` 和群聊 `GroupMessage` 必须新增：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `file_id` | FK EncryptedFile nullable | 文件消息关联的文件 |

没有 `file_id` 关联时，消息历史、撤回、软删除、下载权限和文件清理都无法保持一致。消息本身仍保留密文字段，用于加密文件卡片描述、引用信息或兼容现有消息渲染逻辑。

## 5. HTTP API

所有接口使用 Django Session Cookie 鉴权，写操作必须携带 CSRF Token。

### 5.1 创建上传会话

`POST /api/files/uploads/`

请求：

```json
{
  "client_file_id": "uuid",
  "conversation_id": 1001,
  "conversation_type": "single",
  "message_kind": "image",
  "total_size_bytes": 5242880,
  "chunk_size_bytes": 1048576,
  "chunk_count": 5,
  "algorithm": "AES-256-GCM",
  "encrypted_metadata": "base64",
  "metadata_nonce": "base64",
  "metadata_auth_tag": "base64",
  "membership_version": null
}
```

响应 `201`：

```json
{
  "upload_id": "uuid",
  "file_id": 88,
  "client_file_id": "uuid",
  "status": "uploading",
  "chunk_size_bytes": 1048576,
  "expires_at": "2026-06-15T12:00:00Z",
  "uploaded_chunks": []
}
```

服务端校验：

1. 当前用户是会话活跃成员。
2. 私聊必须满足联系人、拉黑和隐私设置约束。
3. 群聊必须校验 `membership_version`。
4. `client_file_id` 对当前用户幂等。
5. 文件大小、分片数量和类型符合系统限额。

`ciphertext_sha256` 不在创建上传会话时提交。大文件应边加密边上传，避免为了计算完整密文哈希而预先完整加密一遍。客户端可在加密每个分片时增量更新 SHA-256，完成所有分片后得到完整 `ciphertext_sha256`，无需缓存整个密文文件。完整密文哈希可在完成上传接口提交；如果客户端不提交，服务端以每个分片的 `ciphertext_sha256` 作为完整性校验依据。省略完整密文哈希不会降低 AES-GCM 的篡改检测安全性，只是少了一层跨分片的冗余一致性校验。

若同一用户使用相同 `client_file_id` 重复创建上传会话：

1. 既有文件仍为 `uploading` 时，服务端返回既有 `upload_id` 和已上传分片状态，不创建新文件。
2. 既有文件已为 `available` 时，服务端返回 `200` 和既有 `file_id`、`client_file_id`、`status = "available"`，不允许重新覆盖密文文件。
3. 既有文件为 `failed` 或 `deleted` 时，服务端返回 `409 client_file_id_conflict`，客户端必须生成新的 `client_file_id` 后重新上传。

### 5.2 上传分片

`PUT /api/files/uploads/<upload_id>/chunks/<chunk_index>/`

请求类型：`multipart/form-data`

| 字段 | 说明 |
| --- | --- |
| `chunk` | 加密后的二进制分片 |
| `nonce` | Base64 nonce |
| `auth_tag` | Base64 auth tag |
| `ciphertext_sha256` | 分片密文 SHA-256 |
| `size_bytes` | 分片密文大小 |

响应 `200`：

```json
{
  "upload_id": "uuid",
  "file_id": 88,
  "chunk_index": 0,
  "status": "stored"
}
```

幂等规则：

1. 同一 `upload_id + chunk_index` 重复上传且哈希一致，返回 `200`。
2. 同一 `upload_id + chunk_index` 重复上传但哈希不同，返回 `409 chunk_conflict`。

### 5.3 查询上传状态

`GET /api/files/uploads/<upload_id>/`

响应：

```json
{
  "upload_id": "uuid",
  "file_id": 88,
  "client_file_id": "uuid",
  "status": "uploading",
  "chunk_count": 5,
  "uploaded_chunks": [0, 1, 2],
  "missing_chunks": [3, 4],
  "expires_at": "2026-06-15T12:00:00Z"
}
```

### 5.4 完成上传

`POST /api/files/uploads/<upload_id>/complete/`

请求：

```json
{
  "ciphertext_sha256": "hex-or-null"
}
```

响应 `200`：

```json
{
  "file_id": 88,
  "client_file_id": "uuid",
  "status": "available",
  "created_at": "2026-06-15T12:01:00Z"
}
```

服务端完成动作：

1. 校验所有分片已上传。
2. 如果请求提供 `ciphertext_sha256`，服务端保存完整密文哈希；如果未提供，服务端保存空值并依赖分片哈希校验。
3. 按 `chunk_index` 顺序将临时分片密文正文合并为 `EncryptedFile.storage_path` 指向的完整密文文件。
4. 为每个 `EncryptedFileChunk` 写入 `offset_bytes` 和 `size_bytes`，用于 Range 下载后的分段解密。
5. 清理临时分片文件，保留分片元数据记录，并将 `EncryptedFileChunk.storage_path` 置空，避免指向已删除临时文件。
6. 将 `EncryptedFile.status` 改为 `available`。
7. 通过 WebSocket 向上传端推送 `file.upload.completed`。

`complete` 只完成文件可用状态，不创建聊天消息。文件消息必须通过下一节的发送接口创建，以便私聊和群聊分别遵循现有消息结构。

### 5.5 发送文件消息

`POST /api/files/<file_id>/messages/`

私聊请求：

```json
{
  "client_message_id": "uuid",
  "conversation_id": 1001,
  "conversation_type": "single",
  "message_type": "image",
  "reply_to_message_id": null,
  "ciphertext": "base64-file-card-ciphertext",
  "nonce": "base64",
  "auth_tag": "base64",
  "sender_key_version": 1,
  "receiver_key_version": 3,
  "file_keys": [
    {
      "holder_id": 1,
      "receiver_key_version": 1,
      "encrypted_file_key": "base64-for-sender",
      "nonce": "base64",
      "auth_tag": "base64"
    },
    {
      "holder_id": 2,
      "receiver_key_version": 3,
      "encrypted_file_key": "base64-for-receiver",
      "nonce": "base64",
      "auth_tag": "base64"
    }
  ]
}
```

群聊请求：

```json
{
  "client_message_id": "uuid",
  "conversation_id": 12,
  "conversation_type": "group",
  "message_type": "file",
  "reply_to_message_id": null,
  "membership_version": 9,
  "file_keys": [
    {
      "holder_id": 1,
      "receiver_key_version": 1,
      "encrypted_file_key": "base64-for-sender",
      "nonce": "base64",
      "auth_tag": "base64"
    },
    {
      "holder_id": 2,
      "receiver_key_version": 3,
      "encrypted_file_key": "base64-for-member-2",
      "nonce": "base64",
      "auth_tag": "base64"
    }
  ],
  "recipients": [
    {
      "receiver_id": 1,
      "receiver_key_version": 1,
      "ciphertext": "base64-file-card-ciphertext-for-sender",
      "nonce": "base64",
      "auth_tag": "base64"
    },
    {
      "receiver_id": 2,
      "receiver_key_version": 3,
      "ciphertext": "base64-file-card-ciphertext-for-member-2",
      "nonce": "base64",
      "auth_tag": "base64"
    }
  ]
}
```

响应 `201`：

```json
{
  "file_id": 88,
  "message_id": 501,
  "status": "sent",
  "created_at": "2026-06-15T12:01:00Z"
}
```

服务端发送动作：

1. 校验 `EncryptedFile.status == "available"`。
2. 强制校验请求中的 `message_type` 与上传会话创建时的 `message_kind` 一致，或符合白名单映射关系，例如 `image -> image`、`sticker -> sticker`、`file -> file`。
3. 校验 `file_keys` 覆盖所有应接收用户，私聊至少覆盖发送者和接收者，群聊覆盖发送者和当前活跃群成员。
4. 私聊创建 `EncryptedMessage`，使用根节点 `ciphertext`、`nonce`、`auth_tag`。
5. 群聊创建 `GroupMessage`，并为 `recipients` 中每个成员创建 `GroupMessageRecipient` 密文副本。
6. 群聊的 `EncryptedFileKey.membership_version` 由服务端从请求中的 `membership_version` 写入。`file_keys` 写入使用 upsert 语义：同一 `file_id + holder_id` 已存在且密钥内容一致时更新 `membership_version`，不存在时插入，已存在但密钥内容不一致时返回 `409 file_key_conflict`。
7. 通过 WebSocket 推送文件消息事件。

如果群聊上传期间成员发生变化，服务端返回 `409 membership_version_conflict`，并在 `detail` 中返回最新 `membership_version`。客户端不需要重新上传分片，只需拉取最新群成员和公钥，重新生成 `file_keys` 与 `recipients` 后再次调用本接口。

### 5.6 追加文件密钥

`POST /api/files/<file_id>/keys/`

该接口用于转发文件或在新会话中复用已有密文文件。拥有文件访问权限的用户可以为新的持有人提交重新封装后的 `encrypted_file_key`，无需重新上传文件本体。

请求：

```json
{
  "target_conversation_id": 1002,
  "file_keys": [
    {
      "holder_id": 5,
      "receiver_key_version": 4,
      "encrypted_file_key": "base64-for-new-holder",
      "nonce": "base64",
      "auth_tag": "base64"
    }
  ]
}
```

服务端校验：

1. 当前用户必须拥有原文件的 `EncryptedFileKey`。
2. `target_conversation_id` 中的目标用户或群成员必须允许接收该文件。这里的“允许接收”只基于联系人关系、拉黑状态、隐私设置和群成员关系判断，不涉及文件明文或文件内容。
3. 新增的 `holder_id` 必须属于目标会话的可接收用户集合。
4. 重复提交同一 `file_id + holder_id` 且密钥哈希一致时保持幂等；内容不一致时返回 `409 file_key_conflict`。

完成追加密钥后，客户端仍需通过 `POST /api/files/<file_id>/messages/` 在目标会话中创建新的文件消息。转发复用已有密文文件时，客户端解密仍使用原文件详情接口返回的 `client_file_id` 作为 AAD 的文件标识。

### 5.7 下载文件元数据

`GET /api/files/<file_id>/`

响应：

```json
{
  "file_id": 88,
  "client_file_id": "uuid",
  "conversation_id": 1001,
  "message_kind": "image",
  "status": "available",
  "total_size_bytes": 5243300,
  "chunk_size_bytes": 1048576,
  "chunk_count": 5,
  "algorithm": "AES-256-GCM",
  "encrypted_metadata": "base64",
  "metadata_nonce": "base64",
  "metadata_auth_tag": "base64",
  "encrypted_file_key": {
    "encrypted_file_key": "base64",
    "nonce": "base64",
    "auth_tag": "base64",
    "algorithm": "AES-256-GCM",
    "sender_key_version": 1,
    "receiver_key_version": 3
  },
  "chunks": [
    {
      "chunk_index": 0,
      "size_bytes": 1048600,
      "offset_bytes": 0,
      "nonce": "base64",
      "auth_tag": "base64",
      "ciphertext_sha256": "hex"
    }
  ],
  "download_url": "/api/files/88/download/"
}
```

权限规则：

1. 当前用户必须拥有对应 `EncryptedFileKey` 记录，即 `EncryptedFileKey.holder_id == request.user.id`。
2. 当前用户必须仍可访问该会话，或该文件消息未被对当前用户软删除。
3. 若文件已撤回或过期，返回 `410 file_unavailable`。

### 5.8 下载完整密文文件

`GET /api/files/<file_id>/download/`

响应：`application/octet-stream`

响应头：

```text
Content-Type: application/octet-stream
Content-Length: <encrypted file size>
Accept-Ranges: bytes
X-iChat-File-SHA256: <hex-or-empty>
```

服务端从 `EncryptedFile.storage_path` 流式返回完整密文文件。客户端结合 `/api/files/<file_id>/` 返回的分片 `offset_bytes`、`size_bytes`、`nonce` 和 `auth_tag`，按偏移切分密文并逐片解密。该接口必须支持 HTTP Range，便于暂停、续传和大文件流式下载。

### 5.9 下载单个分片（兼容接口）

`GET /api/files/<file_id>/chunks/<chunk_index>/`

响应：`application/octet-stream`

响应头：

```text
Content-Type: application/octet-stream
Content-Length: <encrypted chunk size>
X-iChat-Chunk-SHA256: <hex>
```

该接口仅作为调试或兼容路径。默认客户端下载应优先使用 `/api/files/<file_id>/download/`，避免大量小请求和重复鉴权开销。

### 5.10 取消上传

`DELETE /api/files/uploads/<upload_id>/`

响应：

```json
{
  "upload_id": "uuid",
  "status": "cancelled"
}
```

服务端应清理未完成分片，并将文件状态标记为 `deleted` 或删除未完成记录。

## 6. WebSocket 事件

### 6.1 上传完成通知

发送给上传端：

```json
{
  "protocol_version": "1.0",
  "event": "file.upload.completed",
  "request_id": null,
  "sent_at": "2026-06-15T12:01:00Z",
  "data": {
    "file_id": 88,
    "conversation_id": 1001,
    "message_kind": "image",
    "status": "available"
  }
}
```

### 6.2 私聊文件消息

沿用 `message.single.new`，`message_type` 设置为 `image` 或 `file`，并增加 `file` 对象：

```json
{
  "protocol_version": "1.0",
  "event": "message.single.new",
  "request_id": null,
  "sent_at": "2026-06-15T12:01:00Z",
  "data": {
    "message_id": 501,
    "conversation_id": 1001,
    "sender_id": 1,
    "receiver_id": 2,
    "message_type": "image",
    "ciphertext": "base64-file-card-ciphertext",
    "nonce": "base64",
    "auth_tag": "base64",
    "algorithm": "AES-256-GCM",
    "sender_key_version": 1,
    "receiver_key_version": 3,
    "file": {
      "file_id": 88,
      "message_kind": "image",
      "chunk_count": 5
    },
    "status": "sent",
    "created_at": "2026-06-15T12:01:00Z"
  }
}
```

### 6.3 群聊文件消息

沿用 `message.group.new`。当前项目的群聊消息模型是逐成员密文副本：文件卡片 `ciphertext` 与普通群聊消息一样，存储在 `GroupMessageRecipient` 上，并按接收者分别推送。文件本体仍只上传一次，`encrypted_file_key` 也按持有人分别保存。若未来升级为 Sender Key 或共享群组会话密钥，文件卡片密文层级必须随群消息协议一起升级。

```json
{
  "protocol_version": "1.0",
  "event": "message.group.new",
  "request_id": null,
  "sent_at": "2026-06-15T12:01:00Z",
  "data": {
    "message_id": 601,
    "group_id": 12,
    "conversation_id": 12,
    "sender_id": 1,
    "message_type": "file",
    "membership_version": 9,
    "ciphertext": "base64-file-card-ciphertext-for-this-recipient",
    "nonce": "base64",
    "auth_tag": "base64",
    "algorithm": "AES-256-GCM",
    "sender_key_version": 1,
    "receiver_key_version": 3,
    "file": {
      "file_id": 88,
      "message_kind": "file",
      "chunk_count": 10
    },
    "created_at": "2026-06-15T12:01:00Z"
  }
}
```

## 7. 客户端流程

### 7.1 发送文件

1. 用户选择文件。
2. 客户端读取文件基础信息，生成缩略图和文件卡片描述。
3. 客户端生成 `file_key`，加密元数据和文件分片。
4. 客户端调用创建上传会话接口。
5. 客户端并发上传分片，建议并发数 3 到 5。
6. 上传中断时查询上传状态，只补传缺失分片。
7. 调用完成上传接口，使服务端合并完整密文文件并将文件状态改为 `available`。
8. 客户端为每个接收者生成 `encrypted_file_key` 和文件卡片密文。
9. 调用发送文件消息接口创建私聊或群聊文件消息。
10. 本地消息状态从 `uploading` 变为 `sent`。

### 7.2 接收文件

1. WebSocket 收到 `message.single.new` 或 `message.group.new`。
2. 客户端先用消息会话密钥解密文件卡片密文，显示最小可用状态，例如“收到一个图片/文件”。
3. 调用 `/api/files/<file_id>/` 获取 `client_file_id`、元数据、分片列表和自己的 `encrypted_file_key`。
4. 客户端解密 `file_key`，再解密 `encrypted_metadata`，获得文件名、MIME 类型、明文大小和缩略图引用。
5. 根据用户自动下载设置判断是否下载缩略图或原文件。
6. 优先通过 `/api/files/<file_id>/download/` 流式下载完整密文文件，必要时使用 HTTP Range 续传。
7. 根据分片元数据的 `offset_bytes` 和 `size_bytes` 切分密文，逐片校验 `ciphertext_sha256`。
8. 用 `file_key` 和对应 AAD 解密分片并拼接文件，AAD 格式为 `"ichat-file-chunk-v1:" + client_file_id + ":" + chunk_index`。
9. 校验 `plain_sha256`，成功后写入本地缓存。

这是一个有意的隐私取舍：WebSocket 推送不直接携带文件名、明文大小和缩略图详情，避免未鉴权的实时事件承载过多敏感元数据。为了减少一次往返，后续可以在 WebSocket 的 `file` 对象中附带当前用户的 `encrypted_file_key` 和 `encrypted_metadata`，但必须保持逐用户推送，不能向群通道广播统一密钥。图片场景还可以在文件卡片密文中直接携带缩略图 `file_id`、缩略图 `encrypted_file_key` 或内嵌小缩略图密文，用于先展示预览再按需下载原图。

### 7.3 缩略图处理

图片缩略图也是加密文件，最低要求如下：

1. 客户端本地生成缩略图，建议最长边不超过 480px。
2. 缩略图使用独立 `thumbnail_file_key` 加密，创建独立 `EncryptedFile`，`derivative_role = "thumbnail"`，`parent_file_id` 指向原图文件。
3. 原图 `encrypted_metadata.thumbnail_file_id` 指向缩略图文件 ID。
4. 缩略图也必须为发送者和接收者写入 `EncryptedFileKey`。
5. 自动下载策略可以默认优先下载缩略图，原图按网络和大小限制下载。

### 7.4 自动下载策略

与现有 Data and Storage 设置对齐：

| 网络 | 图片 | 视频 | 文件 |
| --- | --- | --- | --- |
| Wi-Fi | 默认自动下载 | 默认按大小限制 | 默认按大小限制 |
| Mobile | 默认仅缩略图 | 默认不自动下载 | 默认不自动下载 |
| Roaming | 默认不自动下载 | 默认不自动下载 | 默认不自动下载 |

服务端只保存设置，不主动推送或解密内容；自动下载完全由客户端执行。

## 8. 限额

建议默认值：

| 项目 | 默认限制 |
| --- | --- |
| 单文件最大大小 | 100 MiB |
| 图片最大大小 | 20 MiB |
| 贴纸最大大小 | 2 MiB |
| 分片大小 | 1 MiB |
| 单次上传会话有效期 | 24 小时 |
| 单用户并发上传会话 | 5 |
| 单文件分片并发上传 | 5 |
| 支持 MIME 类型 | 白名单控制 |

后续如果接入对象存储，可将 `storage_path` 替换为对象 key，并由服务端签发短期上传/下载 URL。

用户配额、会话配额或服务端磁盘空间不足时，返回 `507 insufficient_storage`。

限额错误统一返回 `429 upload_rate_limited`，`detail` 必须说明触发的具体限制：

```json
{
  "error": "upload_rate_limited",
  "detail": "Too many active upload sessions. Limit: 5."
}
```

## 9. 错误码

| HTTP | 错误码 | 说明 |
| --- | --- | --- |
| 400 | `invalid_json` | 请求体不是合法 JSON |
| 400 | `invalid_file_metadata` | 文件元数据字段缺失或格式错误 |
| 400 | `invalid_chunk_index` | 分片序号越界 |
| 400 | `invalid_chunk_hash` | 分片哈希不匹配 |
| 400 | `message_type_mismatch` | 文件消息类型与上传会话的 `message_kind` 不一致 |
| 400 | `unsupported_file_type` | 不支持的文件类型 |
| 400 | `file_too_large` | 文件超过限制 |
| 403 | `conversation_forbidden` | 当前用户不能访问会话 |
| 403 | `file_forbidden` | 当前用户没有该文件密钥或访问权限 |
| 403 | `upload_forbidden` | `upload_id` 不存在或不属于当前用户；对未授权用户不区分两种情况 |
| 404 | `file_not_found` | 文件不存在 |
| 409 | `chunk_conflict` | 重复分片内容不一致 |
| 409 | `client_file_id_conflict` | 同一用户重复使用已失败或已删除文件的 `client_file_id` |
| 409 | `file_key_conflict` | 追加文件密钥时同一持有人已有不同密钥记录 |
| 409 | `upload_incomplete` | 完成上传时仍有分片缺失 |
| 409 | `membership_version_conflict` | 群成员版本过期 |
| 410 | `upload_expired` | 上传会话已过期 |
| 410 | `file_unavailable` | 文件已删除、撤回或过期 |
| 429 | `upload_rate_limited` | 上传频率或并发超过限制 |
| 507 | `insufficient_storage` | 用户配额、会话配额或服务端磁盘空间不足 |

错误响应统一格式：

```json
{
  "error": "upload_incomplete",
  "detail": "Missing chunks: 3,4"
}
```

## 10. 安全与审计

1. 生产环境所有文件 API 和 WebSocket 连接必须运行在 HTTPS/WSS 下；开发环境可使用 localhost HTTP。
2. 禁止在服务端日志打印 `encrypted_file_key`、`nonce`、`auth_tag` 的完整值，可打印前 8 位用于排障。
3. 文件存储路径必须由服务端生成随机 ID，不使用用户上传文件名。
4. 下载接口必须校验当前用户拥有对应的 `EncryptedFileKey` 记录，即 `holder_id` 匹配当前用户。
5. 所有包含 `upload_id` 的接口都必须校验上传会话属于当前用户，包括查询状态、上传分片、完成上传和取消上传；不存在或不属于当前用户时统一返回 `403 upload_forbidden`，不得透露上传会话是否存在。
6. 管理后台只能查看文件状态、大小、分片数、密文哈希和所属会话，不提供明文预览。
7. 文件撤回后，消息状态改为 `recalled`，下载接口返回 `410 file_unavailable`。
8. 用户本地删除消息不应删除共享文件本体，只隐藏该用户的消息视图。
9. 所有未完成上传会话应由定时任务清理。
10. 服务端应对上传接口增加速率限制和磁盘配额检查。

## 11. 落地顺序

### Phase A：基础文件消息

1. 新增 `EncryptedFile`、`EncryptedFileChunk`、`EncryptedFileKey` 模型和迁移。
2. 新增上传会话、分片上传、完成上传、发送文件消息、文件元数据和完整密文下载 API。
3. 私聊文件发送打通，支持图片、缩略图和普通文件。
4. 缩略图按独立加密文件处理，原图元数据记录 `thumbnail_file_id`。
5. 完成上传时合并临时分片为完整密文文件，并支持 `/api/files/<file_id>/download/` 流式下载。
6. 消息历史接口返回 `file` 对象。
7. 前端支持选择文件、上传进度、下载、解密和本地缓存。

### Phase B：群聊、转发与缩略图优化

1. 群聊文件密钥按成员封装。
2. 支持追加文件密钥，用于文件转发和跨会话复用密文文件。
3. 缩略图优化：支持将极小缩略图作为 Base64 字段放入 `encrypted_metadata`，建议上限 32 KiB，以减少一次独立文件下载。
4. 接入自动下载设置。
5. 支持断点续传和失败重试。

### Phase C：产品化增强

1. 接入对象存储或 CDN 的短期签名 URL。
2. 支持 Range 下载、暂停/继续下载。
3. 增加用户级、会话级存储配额。
4. 增加文件安全扫描的密文侧策略说明；若需要明文扫描，必须明确破坏 E2EE 边界并由用户授权。

## 12. 验收用例

| 编号 | 场景 | 预期 |
| --- | --- | --- |
| FT-01 | 私聊发送 1MB 图片 | 接收方收到文件消息并成功解密 |
| FT-02 | 私聊发送 100MB 文件 | 分片上传完成，服务端合并为完整密文文件，客户端可通过单个下载接口流式下载 |
| FT-03 | 群聊发送图片 | 文件本体只存一份，每个成员有独立 `encrypted_file_key` |
| FT-04 | 非会话成员下载文件 | 返回 `403 file_forbidden` |
| FT-05 | 修改某个分片密文 | 客户端解密失败并提示文件损坏 |
| FT-06 | 重复上传同一分片 | 哈希一致返回成功，哈希不同返回 `409 chunk_conflict` |
| FT-07 | 上传会话过期后继续上传 | 返回 `410 upload_expired` |
| FT-08 | 撤回文件消息后下载 | 返回 `410 file_unavailable` |
| FT-09 | 检查数据库和日志 | 不出现文件明文、明文文件名、明文 `file_key` |
| FT-10 | 群成员变更后用旧版本发送 | 返回 `409 membership_version_conflict`，客户端重新生成密钥和 `recipients` 后可复用已上传文件发送 |
| FT-11 | 发送者在另一台设备下载自己发送的文件 | 通过发送者自己的 `EncryptedFileKey` 成功解密 |
| FT-12 | 群成员被移除后尝试下载历史文件 | 按产品策略处理；若移除后禁止访问，返回 `403 file_forbidden` |
| FT-13 | 用户密钥轮换后下载旧文件 | 使用旧 `receiver_key_version` 对应密钥材料或本地历史密钥成功解密；缺失时提示无法解密 |
| FT-14 | 同一 `client_file_id` 重复创建上传会话 | 返回同一个未完成 `upload_id` 和已上传分片状态 |
| FT-15 | 使用不属于当前用户的 `upload_id` 上传分片 | 返回 `403 upload_forbidden` |
| FT-16 | 图片文件包含缩略图 | 缩略图作为独立加密文件下载并解密，原图按需下载 |
| FT-17 | 新成员加入群聊后尝试解密加入前的历史文件 | 没有对应 `EncryptedFileKey`，返回 `403 file_forbidden` 或客户端提示无权解密 |
| FT-18 | 转发已有文件到另一个会话 | 只追加新持有人的 `EncryptedFileKey` 并创建新文件消息，不重新上传密文文件 |
| FT-19 | 上传时声明 `message_kind=file`，发送时使用 `message_type=sticker` | 返回 `400 message_type_mismatch` |
| FT-20 | 服务端或用户配额不足 | 返回 `507 insufficient_storage` |
