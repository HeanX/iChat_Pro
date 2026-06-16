# iChat Pro UML 与架构图交付文档

> 版本：v1.0
> 日期：2026-06-16
> 作者：ketter1024
> 关联 Issue：#120 (P3 T08)
> 渲染引擎：Mermaid （推荐使用 VS Code Mermaid Preview 或 GitHub 原生渲染）

本文档为 iChat Pro 交付 UML 与架构图，覆盖系统用例、核心数据模型、组件架构、端到端加密时序、AI Assistant 交互边界与业务流程。

> **Phase 3 LLM 边界声明**：所有图表中 LLM/AI Assistant 组件**不**读取聊天数据库中的 E2EE 明文，**不**持有或接触任何用户私钥、ECDH 会话密钥或 Key Encryption Key，仅处理用户主动输入的 Prompt 文本。

---

## 1. 系统用例图

```mermaid
graph TB
    subgraph Actors
        U[👤 普通用户]
        A[👤 管理员]
        Q[🤖 Qwen API / LLM Provider]
    end

    subgraph "iChat Pro System"
        subgraph "账号模块"
            UC1[注册]
            UC2[登录]
            UC3[退出]
        end
        subgraph "社交模块"
            UC4[搜索联系人]
            UC5[发送/接受好友申请]
            UC6[管理联系人]
        end
        subgraph "聊天模块"
            UC7[私聊 E2EE 收发]
            UC8[群聊逐成员加密收发]
            UC9[消息操作 - 撤回/删除/转发/回复]
        end
        subgraph "群管理模块"
            UC10[创建群组]
            UC11[邀请/移除成员]
            UC12[群主转让/管理员设置]
        end
        subgraph "AI Assistant 模块"
            UC13[通用问答]
            UC14[文本摘要]
            UC15[草稿生成]
        end
        subgraph "管理模块"
            UC16[管理用户/群组]
            UC17[查看操作日志]
        end
    end

    U --> UC1
    U --> UC2
    U --> UC3
    U --> UC4
    U --> UC5
    U --> UC6
    U --> UC7
    U --> UC8
    U --> UC9
    U --> UC10
    U --> UC11
    U --> UC12
    U --> UC13
    U --> UC14
    U --> UC15
    A --> UC16
    A --> UC17
    Q --> UC13
    Q --> UC14
    Q --> UC15
```

**说明：** 普通用户覆盖注册到聊天的完整闭环；管理员负责用户/群组管理和审计；Qwen API 是 AI Assistant 的外部 LLM 提供商，仅处理用户主动输入的 Prompt，不接触 E2EE 密钥体系。

---

## 2. 核心类图

```mermaid
classDiagram
    class User {
        +int id
        +str username
        +str password
        +str email
        +bool is_active
    }
    class UserProfile {
        +int id
        +str nickname
        +ImageField avatar
        +str bio
        +str phone_number
        +str location
        +date birthday
        +str user_type
    }
    class Contact {
        +int id
        +User user
        +User contact
        +datetime created_at
    }
    class FriendRequest {
        +int id
        +User sender
        +User receiver
        +str status
        +datetime created_at
    }
    class Conversation {
        +int id
        +str type
        +str name
        +str status
        +int membership_version
        +int auto_delete_seconds
        +datetime muted_until
    }
    class ConversationMember {
        +int id
        +Conv conversation
        +User user
        +str role
        +str status
        +int unread_count
        +bool is_pinned
        +datetime muted_until
        +datetime archived_at
    }
    class EncryptedMessage {
        +int id
        +Conv conversation
        +User sender
        +User receiver
        +str ciphertext
        +str nonce
        +str auth_tag
        +int sender_key_version
        +int receiver_key_version
        +str client_message_id
        +str status
        +datetime recalled_at
    }
    class GroupMessage {
        +int id
        +Conv conversation
        +User sender
        +str client_message_id
        +int reply_to_message_id
        +str status
    }
    class GroupMessageRecipient {
        +int id
        +GroupMessage group_message
        +User receiver
        +str ciphertext
        +str nonce
        +str auth_tag
        +int membership_version
        +str status
    }
    class UserPublicKey {
        +int id
        +User user
        +str identity_public_key
        +str key_fingerprint
        +int key_version
        +bool is_active
    }
    class KeyTrust {
        +int id
        +User user
        +User contact
        +str key_fingerprint
        +int key_version
        +str trust_status
    }
    class BlockedUser {
        +int id
        +User blocker
        +User blocked
    }
    class UserPrivacySettings {
        +int id
        +User user
        +str last_seen_visibility
        +str profile_photo_visibility
        +str who_can_send_messages
        +int auto_delete_messages_days
    }
    class UserNotificationSettings {
        +int id
        +User user
        +bool private_chat_notifications
        +bool group_chat_notifications
        +int volume
    }
    class UserStorageSettings {
        +int id
        +User user
        +JSONField settings_json
    }

    User "1" --> "1" UserProfile : has
    User "1" --> "*" Contact : initiates
    User "1" --> "*" FriendRequest : sends
    User "1" --> "*" UserPublicKey : owns
    User "1" --> "*" KeyTrust : verifies
    User "1" --> "1" UserPrivacySettings : configures
    User "1" --> "1" UserNotificationSettings : configures
    User "1" --> "1" UserStorageSettings : configures
    User "1" --> "*" BlockedUser : blocks
    Conversation "1" --> "*" ConversationMember : contains
    Conversation "1" --> "*" EncryptedMessage : holds
    Conversation "1" --> "*" GroupMessage : holds
    GroupMessage "1" --> "*" GroupMessageRecipient : per_recipient
    EncryptedMessage "*" --> "1" User : sender
    EncryptedMessage "*" --> "1" User : receiver
    GroupMessageRecipient "*" --> "1" User : receiver
```

**说明：** 核心类图覆盖 15 个关键模型。`Conversation` 统一承载私聊与群聊，`EncryptedMessage` 只存密文。`GroupMessage` + `GroupMessageRecipient` 实现逐成员独立密文。`KeyTrust` 追踪用户对联系人密钥的验证状态。Phase 3 LLM 相关类（LLMProvider/QwenProvider/MockProvider）为规划中新增，不在此图主体。

---

## 3. 系统组件图

```mermaid
graph TB
    subgraph "Browser / Electron 客户端"
        FE[HTML Templates + Tailwind CSS]
        JS[Vanilla JavaScript<br/>Web Crypto API<br/>ECDH KeyGen / AES-GCM]
        LS[(LocalStorage<br/>私钥/会话密钥)]
    end

    subgraph "Django 后端 (Python 3.12+)"
        subgraph "HTTP Layer"
            V[Django Views / REST API]
            MW[CSP Middleware<br/>安全头注入]
        end
        subgraph "WebSocket Layer"
            WS[Django Channels<br/>ChatConsumer<br/>实时消息转发]
        end
        subgraph "Data Layer"
            M[Django ORM Models]
            DB[(SQLite / PostgreSQL)]
        end
        ADMIN[Django Admin<br/>用户/群组管理<br/>操作日志]
    end

    subgraph "AI Assistant (Phase 3)"
        LLM[LLM Provider Interface]
        QWEN[Qwen API]
        MOCK[Mock Provider]
    end

    FE -->|HTTPS POST| V
    FE -->|WSS| WS
    V --> M
    WS --> M
    M --> DB
    MW --> FE
    ADMIN --> M

    FE -.->|用户主动输入 Prompt| LLM
    LLM --> QWEN
    LLM --> MOCK

    JS -->|Web Crypto ECDH| LS
    JS -->|公钥上传| V
    JS -->|密文 + 签名| WS
```

**说明：** 前端为 Django Templates + Vanilla JavaScript，使用浏览器 Web Crypto API 在客户端完成密钥生成和 AES-GCM 加密。后端 Django 通过 HTTP API 和 WebSocket 提供服务。CSP 中间件注入安全头。AI Assistant 模块独立于 E2EE 密钥体系，仅接收用户主动输入的 Prompt。

> ⚠️ **Phase 3 LLM 边界**：LLM 组件不连接到 `EncryptedMessage`/`GroupMessage` 表，不接触 `UserPublicKey`，不读取 LocalStorage 私钥。箭头 `.->` 表示受控调用路径。

---

## 4. 私聊 E2EE 时序图

```mermaid
sequenceDiagram
    actor A as Alice (发送方)
    participant AC as Alice 浏览器<br/>Web Crypto
    participant WS as WebSocket Server<br/>Django Channels
    participant DB as 后端数据库
    participant BC as Bob 浏览器<br/>Web Crypto
    actor B as Bob (接收方)

    Note over A,B: 前提：双方已完成 ECDH P-256 密钥对生成并上传公钥

    A->>AC: 输入消息 plaintext
    AC->>WS: GET /api/keys/{bob_id}/ 获取 Bob 活跃公钥
    WS->>DB: 查询 UserPublicKey(is_active=True)
    DB-->>WS: Bob 公钥 + key_version
    WS-->>AC: identity_public_key, key_version, fingerprint

    AC->>AC: 生成 ECDH shared secret<br/>AES-256-GCM 加密 plaintext<br/>→ ciphertext + nonce + auth_tag
    Note over AC: 服务端从未接触 plaintext

    AC->>WS: WebSocket: {"type": "message.single.send",<br/>"conversation_id": N,<br/>"ciphertext": "...", "nonce": "...",<br/>"auth_tag": "...", "sender_key_version": V,<br/>"receiver_key_version": W,<br/>"client_message_id": "..."}
    WS->>DB: INSERT EncryptedMessage(ciphertext, nonce, auth_tag, ...)
    DB-->>WS: OK
    WS-->>AC: {"type": "message.single.accepted",<br/>"message_id": M}

    WS->>BC: WebSocket Push: {"type": "message.single.received",<br/>"message_id": M, "ciphertext": "...", ...}
    BC->>BC: ECDH shared secret 解密<br/>ciphertext → plaintext
    BC-->>B: 显示 "Hello Bob!"

    BC->>WS: WebSocket: {"type": "message.receipt.update",<br/>"message_id": M, "status": "read"}
    WS->>DB: UPDATE EncryptedMessage.status = "read"
    WS-->>AC: {"type": "message.receipt.updated",<br/>"message_id": M, "status": "read"}
    AC-->>A: 显示 ✓✓ 已读
```

**说明：** 私聊 E2EE 全流程：获取对方公钥 → 客户端 ECDH → 本地 AES-GCM 加密 → WebSocket 密文传输 → 服务端存转发 → 接收方本地解密。**服务端在任何环节都不接触消息明文**。送达回执（delivered）和已读回执（read）仅标记状态，不传递明文。

---

## 5. 群聊逐成员加密时序图

```mermaid
sequenceDiagram
    actor S as Sender (Alice)
    participant SC as Sender 浏览器
    participant WS as WebSocket Server
    participant DB as Database
    participant RC1 as Bob 浏览器
    participant RC2 as Carol 浏览器
    actor R1 as Bob
    actor R2 as Carol

    Note over S,R2: 群聊包含 Alice(群主), Bob(成员), Carol(成员)

    S->>SC: 输入群消息 "Team meeting at 3pm"
    SC->>WS: GET /api/keys/batch/?user_ids=bob,carol 获取所有成员公钥
    WS->>DB: 批量查询 UserPublicKey(is_active=True)
    DB-->>WS: [{Bob: pubKey_B, vB}, {Carol: pubKey_C, vC}]
    WS-->>SC: 批量公钥 + key_versions

    SC->>SC: 对 Bob: ECDH(Bob) → AES-GCM → ciphertext_B<br/>对 Carol: ECDH(Carol) → AES-GCM → ciphertext_C
    Note over SC: ciphertext_B ≠ ciphertext_C<br/>每成员独立密文

    SC->>WS: WebSocket: {"type": "message.group.send",<br/>"conversation_id": G,<br/>"recipients": [<br/>  {"user_id": bob, "ciphertext": "ct_B", ...},<br/>  {"user_id": carol, "ciphertext": "ct_C", ...}<br/>], "membership_version": 1}

    WS->>DB: BEGIN TRANSACTION
    WS->>DB: INSERT GroupMessage (logical)
    WS->>DB: INSERT GroupMessageRecipient (Bob, ct_B)
    WS->>DB: INSERT GroupMessageRecipient (Carol, ct_C)
    WS->>DB: COMMIT
    WS-->>SC: {"type": "message.group.accepted", "message_id": M}

    WS->>RC1: Push: {"type": "message.group.received", "ciphertext": "ct_B", ...}
    WS->>RC2: Push: {"type": "message.group.received", "ciphertext": "ct_C", ...}

    RC1->>RC1: 本地 ECDH + AES-GCM 解密 → plaintext
    RC2->>RC2: 本地 ECDH + AES-GCM 解密 → plaintext
    RC1-->>R1: "Team meeting at 3pm"
    RC2-->>R2: "Team meeting at 3pm"
```

**说明：** 群聊采用"一个逻辑消息 + 逐成员独立密文副本"模型。发送方为每个在线成员单独加密，`ciphertext_B ≠ ciphertext_C`。服务端存储逻辑消息和按收件人分组的密文副本，新成员无法解密加入前的历史消息。

---

## 6. AI Assistant 时序图

```mermaid
sequenceDiagram
    actor U as User
    participant FE as Frontend
    participant BE as Django Backend
    participant LLM as LLM Provider Interface
    participant QWEN as Qwen API
    participant MOCK as Mock Provider

    Note over U,MOCK: Phase 3 LLM 边界：LLM 不可读取聊天 E2EE 明文或密钥

    U->>FE: 打开 AI Assistant 面板
    FE->>BE: GET /api/llm/status/
    BE->>BE: 检查 QWEN_API_KEY 配置
    alt Qwen API Key 已配置
        BE-->>FE: {"provider": "qwen", "status": "available"}
    else 未配置
        BE-->>FE: {"provider": "mock", "status": "fallback"}
    end

    U->>FE: 输入 Prompt "帮我总结这段文本..."
    FE->>BE: POST /api/llm/chat/ {"prompt": "...", "session_id": "S"}
    Note over BE: 仅接收用户主动输入的 Prompt<br/>不读取聊天数据库

    BE->>BE: Validate prompt (长度限制、内容过滤)
    BE->>LLM: route(prompt, session_id)

    alt Qwen 可用
        LLM->>QWEN: POST /api/v1/services/aigc/text-generation/generation<br/>{"model": "qwen-plus", "input": {"messages": [...]}}
        QWEN-->>LLM: {"output": {"text": "总结结果..."}}
    else Mock Fallback
        LLM->>MOCK: generate(prompt)
        MOCK-->>LLM: "[Mock] 这是模拟回复。请配置 Qwen API Key 以获取实际 AI 回复。"
    end

    LLM-->>BE: response_text
    BE-->>FE: {"response": "总结结果...", "provider": "qwen"}
    FE-->>U: 显示 AI 回复

    opt 用户将回复复制为草稿
        U->>FE: 点击 "复制为草稿"
        FE->>FE: 填入聊天输入框（本地操作）
        Note over FE: AI 回复通过用户手动操作<br/>进入 E2EE 加密流程
    end
```

**说明：** AI Assistant 的 LLM 组件仅处理用户主动输入的 Prompt，**不**自动读取聊天数据库中的密文或明文。用户如需将 AI 回复用于聊天，需手动复制粘贴——此时内容进入正常的 E2EE 加密流程。Mock Provider 确保在无 API Key 时仍可演示。

> ⚠️ **Phase 3 LLM 边界**：LLM Provider 不持有 `User` 私钥，不访问 `EncryptedMessage`/`GroupMessage` 表，不解密 Session Key，不参与 ECDH 密钥协商。其职责严格限定为：接收用户 Prompt → 调用外部 API → 返回文本。

---

## 7. 活动图：用户从登录到聊天

```mermaid
stateDiagram-v2
    [*] --> 登录页
    登录页 --> 输入凭证
    输入凭证 --> 验证身份
    验证身份 --> 登录失败: 凭证无效
    登录失败 --> 输入凭证: 重试
    验证身份 --> 聊天主页: 登录成功

    聊天主页 --> 检查密钥
    检查密钥 --> 生成密钥对: 无本地密钥
    生成密钥对 --> 上传公钥
    检查密钥 --> 上传公钥: 密钥已存在但未上传
    上传公钥 --> 选择操作

    选择操作 --> 搜索联系人: 添加联系人
    搜索联系人 --> 发送好友申请
    发送好友申请 --> 对方同意: 等待响应
    对方同意 --> 打开私聊

    选择操作 --> 打开私聊: 已有联系人
    打开私聊 --> 获取对方公钥
    获取对方公钥 --> 输入消息
    输入消息 --> 本地E2EE加密
    本地E2EE加密 --> WebSocket发送密文
    WebSocket发送密文 --> 收到送达回执
    收到送达回执 --> 收到已读回执
    收到已读回执 --> 选择操作

    选择操作 --> 创建群组
    创建群组 --> 选择初始成员
    选择初始成员 --> 群聊加密发送

    选择操作 --> [*]: 退出登录
```

**说明：** 从登录开始，经历密钥初始化（如需要）、联系人建立、私聊 E2EE 加密发送的完整活动流程。每条消息在发送前必须完成：获取接收方公钥 → 本地生成 ECDH Shared Secret → AES-256-GCM 加密 → 密文通过 WebSocket 发送。

---

## 8. 活动图：AI Assistant 使用流程

```mermaid
stateDiagram-v2
    [*] --> 打开AI_Assistant面板
    打开AI_Assistant面板 --> 检查LLM状态

    检查LLM状态 --> Qwen可用: API Key 已配置
    检查LLM状态 --> Mock模式: API Key 未配置
    Mock模式 --> 显示Mock提示

    Qwen可用 --> 显示可用状态
    显示可用状态 --> 用户输入Prompt
    显示Mock提示 --> 用户输入Prompt

    用户输入Prompt --> 内容校验
    内容校验 --> 校验失败: 过长/违规
    校验失败 --> 显示错误提示
    显示错误提示 --> 用户输入Prompt

    内容校验 --> 校验通过
    校验通过 --> 路由Provider

    路由Provider --> 调用Qwen_API: Qwen 可用
    路由Provider --> 调用Mock: Mock 模式

    调用Qwen_API --> 等待API响应
    等待API响应 --> API错误: 超时/限流
    API错误 --> 调用Mock: 自动降级
    等待API响应 --> 返回结果

    调用Mock --> 返回结果

    返回结果 --> 用户查看回复
    用户查看回复 --> 复制为草稿: 需要用到聊天
    复制为草稿 --> 填入聊天输入框
    填入聊天输入框 --> E2EE加密流程: 进入正常聊天加密

    用户查看回复 --> 继续提问: 继续对话
    继续提问 --> 用户输入Prompt

    用户查看回复 --> 关闭面板
    关闭面板 --> [*]
```

**说明：** AI Assistant 使用流程的核心安全约束：用户输入 Prompt 后**不读取聊天数据库**，LLM 回复**不自动进入 E2EE 聊天**——用户需手动复制。Qwen API 调用失败时自动降级到 Mock 模式，确保演示路径不中断。

---

## 9. 交付总结

| # | 图名 | 类型 | 状态 |
|---|------|------|------|
| 1 | 系统用例图 | Use Case | ✅ |
| 2 | 核心类图 | Class Diagram | ✅ |
| 3 | 系统组件图 | Component Diagram | ✅ |
| 4 | 私聊 E2EE 时序图 | Sequence Diagram | ✅ |
| 5 | 群聊逐成员加密时序图 | Sequence Diagram | ✅ |
| 6 | AI Assistant 时序图 | Sequence Diagram | ✅ |
| 7 | 用户从登录到聊天活动图 | Activity Diagram | ✅ |
| 8 | AI Assistant 使用流程活动图 | Activity Diagram | ✅ |

**共交付 8 张 Mermaid 图**（最低要求 5 张），覆盖全部核心模块、E2EE 加密链路和 Phase 3 LLM 接入边界。

所有图表可在 GitHub 上直接渲染（Mermaid 原生支持），也可在 VS Code 中安装 Mermaid Preview 插件查看。

### 当前代码一致性校验

- **类图一致性**：类图覆盖的 15 个模型与 `accounts/models.py` 和 `chat/models.py` 定义一致。LLMProvider/QwenProvider/MockProvider 为 Phase 3 规划新增，尚未在代码中创建。
- **时序图一致性**：私聊 E2EE 流程与 `static/js/private-chat-e2ee.js` 和 `chat/consumers.py` 实现一致。
- **组件图一致性**：架构分层与实际部署拓扑一致（Django + Channels + SQLite + Browser Web Crypto + Electron Shell）。
- **AI Assistant 边界**：所有图表遵从 Phase 3 LLM 不放开 E2EE 明文访问的安全约束。
