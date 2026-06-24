# iChat Pro UML 与架构图交付文档

> 版本：v2.0
> 日期：2026-06-24
> 作者：ketter1024
> 关联 Issue：#120 (P3 T08) · 修复 Issue：#124
> 渲染引擎：Mermaid（推荐 VS Code Mermaid Preview 或 GitHub 原生渲染）

本文档为 iChat Pro 交付 UML 与架构图，覆盖系统用例、数据库 ER 模型、核心类结构、组件架构、端到端加密时序、AI Assistant 交互边界与业务流程。所有图表基于 **2026-06-24 main 分支代码** 绘制。

> **Phase 3 LLM 边界声明**：所有图表中 LLM/AI Assistant 组件**不**读取聊天数据库中的 E2EE 明文，**不**持有或接触任何用户私钥、ECDH 会话密钥或 Key Encryption Key，仅处理用户主动输入的 Prompt 文本。

---

## 1. 系统用例图

```mermaid
graph LR
    U((普通用户)) --> 账号模块
    U --> 社交模块
    U --> 聊天模块
    U --> 群管理模块
    U --> AI模块

    Admin((管理员)) --> 管理模块

    Qwen((LLM Provider)) --> AI模块

    subgraph 账号模块
        UC1[注册]
        UC2[登录]
        UC3[退出]
    end

    subgraph 社交模块
        UC4[搜索联系人]
        UC5[发送/接受好友申请]
        UC6[管理联系人]
    end

    subgraph 聊天模块
        UC7[私聊 E2EE 收发]
        UC8[群聊逐成员加密]
        UC9[消息撤回/删除/转发/回复]
    end

    subgraph 群管理模块
        UC10[创建群组]
        UC11[邀请/移除成员]
        UC12[群主转让/管理员设置]
    end

    subgraph AI模块[AI Assistant 模块]
        UC13[通用问答]
        UC14[文本摘要]
        UC15[草稿生成]
    end

    subgraph 管理模块
        UC16[管理用户/群组]
        UC17[查看操作日志]
    end
```

**说明：** 普通用户覆盖注册到聊天的完整闭环；管理员负责用户/群组管理和审计；LLM Provider（Qwen / Anthropic / OpenAI 兼容）是 AI Assistant 的外部 LLM 提供商，仅处理用户主动输入的 Prompt，不接触 E2EE 密钥体系。采用 `graph LR` 水平布局，Actor 在左、用例模块在右。

---

## 2. 数据库 ER 图

```mermaid
%%{init: {'flowchart': {'useMaxWidth': false, 'nodeSpacing': 16, 'rankSpacing': 24}}}%%
flowchart TD
    User[User 用户]

    User -->|"1:1"| UP[UserProfile 用户资料]
    User -->|"1:1"| UNS[UserNotificationSettings 通知设置]
    User -->|"1:1"| USS[UserStorageSettings 存储设置]
    User -->|"1:1"| UPr[UserPresence 在线状态]

    User -->|"1:N"| CT[Contact 联系人]
    User -->|"1:N"| FR[FriendRequest 好友申请]
    User -->|"1:N"| BU[BlockedUser 拉黑]
    User -->|"1:N"| LLC[UserLLMConfig LLM配置]

    User -->|"1:N"| CM[ConversationMember 会话成员]

    Conv[Conversation 会话] -->|"1:N"| CM
    Conv -->|"1:N"| EM[EncryptedMessage 私聊密文]
    Conv -->|"1:N"| GM[GroupMessage 群聊逻辑消息]
    Conv -->|"1:N"| GA[GroupAnnouncement 群公告]
    Conv -->|"1:N"| EF[EncryptedFile 加密文件]

    GM -->|"1:N"| GMR[GroupMessageRecipient 逐成员密文副本]
    EF -->|"1:N"| EFC[EncryptedFileChunk 文件分块]
    EF -->|"1:N"| EFK[EncryptedFileKey 文件密钥包裹]

    EM -->|"FK"| User
    GMR -->|"FK"| User
    CM -->|"FK"| User
    FR -->|"FK"| User
    CT -->|"FK"| User
    EFK -->|"FK"| User
```

**核心实体字段说明：**

| 实体 | 关键字段 |
|------|----------|
| User | id(PK), username, password, email |
| UserProfile | user_id(FK), nickname, avatar, bio, user_type |
| Conversation | id(PK), type(single\|group), status, membership_version |
| EncryptedMessage | conversation_id(FK), sender_id(FK), receiver_id(FK), ciphertext, nonce, auth_tag, client_message_id, status |
| GroupMessage | conversation_id(FK), sender_id(FK), client_message_id, status |
| GroupMessageRecipient | group_message_id(FK), receiver_id(FK), ciphertext, nonce, auth_tag, membership_version |
| UserLLMConfig | user_id(FK), api_url, api_key(Fernet加密), model |

**说明：** ER 图采用 `flowchart TD` 纵向布局，从上到下依次为 User → 设置表 → 关系表 → Conversation → 消息密文表。User 为中枢，Conversation 统一承载私聊与群聊。`%%{init}%%` 指令解除 Mermaid 最大宽度限制。密钥相关实体（UserPublicKey、KeyTrust 等）和详细字段参见类图。

---

## 3. 核心类图

```mermaid
classDiagram
    class User {
        +int id
        +str username
        +str email
        +bool is_active
    }
    class UserProfile {
        +str nickname
        +ImageField avatar
        +str bio
        +str user_type
    }
    class Contact {
        +User user
        +User contact
    }
    class FriendRequest {
        +User sender
        +User receiver
        +str status
    }
    class UserPublicKey {
        +str identity_public_key
        +str key_fingerprint
        +int key_version
        +bool is_active
    }
    class KeyTrust {
        +User user
        +User contact
        +str trust_status
    }
    class BlockedUser {
        +User blocker
        +User blocked
    }
    class UserLLMConfig {
        +str api_url
        +str api_key
        +str model
    }
    class Conversation {
        +str type
        +str name
        +str status
        +int membership_version
    }
    class ConversationMember {
        +str role
        +str status
        +int unread_count
        +bool is_pinned
    }
    class EncryptedMessage {
        +str ciphertext
        +str nonce
        +str auth_tag
        +str sender_ephemeral_public_key
        +str sender_copy_ciphertext
        +str status
        +str client_message_id
    }
    class GroupMessage {
        +str client_message_id
        +str sender_copy_ciphertext
        +str status
    }
    class GroupMessageRecipient {
        +str ciphertext
        +str sender_ephemeral_public_key
        +int membership_version
        +str status
    }
    class LlmProvider {
        <<abstract>>
        +complete(messages, system) str
        +stream(messages, system) Iterator
    }
    class MockProvider {
        +complete() str
    }
    class OpenAICompatibleProvider {
        +str endpoint
        +str model
    }
    class AnthropicMessagesProvider {
        +str url
        +str model
    }

    User "1" --> "1" UserProfile
    User "1" --> "*" Contact
    User "1" --> "*" FriendRequest
    User "1" --> "*" UserPublicKey
    User "1" --> "*" KeyTrust
    User "1" --> "*" BlockedUser
    User "1" --> "*" UserLLMConfig
    Conversation "1" --> "*" ConversationMember
    Conversation "1" --> "*" EncryptedMessage
    Conversation "1" --> "*" GroupMessage
    ConversationMember "*" --> "1" User
    GroupMessage "1" --> "*" GroupMessageRecipient
    GroupMessageRecipient "*" --> "1" User
    LlmProvider <|-- MockProvider
    LlmProvider <|-- OpenAICompatibleProvider
    LlmProvider <|-- AnthropicMessagesProvider
```

**说明：** 核心类图聚焦 17 个关键类。`Conversation` 统一承载私聊与群聊。消息密文仅存 `EncryptedMessage`（私聊）和 `GroupMessage` + `GroupMessageRecipient`（群聊逐成员副本），含 `sender_ephemeral_public_key` 和 `sender_copy_ciphertext` 前向保密字段。`UserLLMConfig` 以 Fernet 加密存储 API Key。`LlmProvider` 抽象类派生出 Mock、OpenAI 兼容、Anthropic 三种实现。其余 settings 类（Privacy、Notification、Storage 等）和文件传输类属辅助模型，未在图中展开。

---

## 4. 系统组件图

```mermaid
graph TB
    subgraph 客户端[Browser / Electron 客户端]
        FE[HTML Templates + Tailwind CSS]
        JS[Vanilla JavaScript<br/>Web Crypto API<br/>ECDH / AES-GCM]
        LS[(LocalStorage<br/>私钥/会话密钥)]
    end

    subgraph 后端[Django 后端]
        subgraph HTTP层[HTTP Layer]
            V[Django Views / REST API]
            MW[CSP Middleware<br/>安全头注入]
        end
        subgraph WebSocket层[WebSocket Layer]
            WS[Django Channels<br/>ChatConsumer<br/>实时消息转发]
        end
        subgraph 数据层[Data Layer]
            M[Django ORM Models]
            DB[(SQLite / PostgreSQL)]
        end
        subgraph LLM层[LLM Provider Layer]
            LLP[chat/llm.py<br/>LlmProvider 抽象]
            ANTHRO[AnthropicMessagesProvider]
            OPENAI[OpenAICompatibleProvider]
            MOCK[MockProvider]
        end
        ADMIN[Django Admin<br/>用户/群组管理<br/>操作日志]
    end

    subgraph 外部服务[外部 LLM API]
        QWEN_API[Qwen / DashScope]
        ANTHRO_API[Anthropic API]
        OPENAI_API[OpenAI / 兼容 API]
    end

    FE -->|HTTPS POST| V
    FE -->|WSS| WS
    V --> M
    WS --> M
    M --> DB
    MW --> FE
    ADMIN --> M

    FE -.->|用户主动输入 Prompt| V
    V --> LLP
    LLP --> ANTHRO
    LLP --> OPENAI
    LLP --> MOCK
    ANTHRO --> ANTHRO_API
    OPENAI --> OPENAI_API
    OPENAI --> QWEN_API

    JS -->|Web Crypto ECDH| LS
    JS -->|公钥上传| V
    JS -->|密文 + 签名| WS
```

**说明：** 前端使用浏览器 Web Crypto API 在客户端完成密钥生成和 AES-256-GCM 加密，私钥仅存 LocalStorage。后端 Django + Channels 通过 HTTP REST API 和 WebSocket 提供服务。CSP 中间件注入安全头。LLM Provider 层支持 Anthropic Messages API、OpenAI 兼容 API（含 Qwen/DashScope）和 Mock 降级，IP/域名白名单校验防止 SSRF。AI 模块不连接到消息密文表或密钥表。

> ⚠️ **Phase 3 LLM 边界**：LLM 组件不读取 `EncryptedMessage`/`GroupMessage` 表，不接触 `UserPublicKey`，不读取 LocalStorage 私钥。虚线 `.->` 表示受控调用路径。

---

## 5. 私聊 E2EE 时序图（含前向保密）

```mermaid
sequenceDiagram
    actor A as Alice（发送方）
    participant AC as Alice 浏览器<br/>Web Crypto
    participant WS as WebSocket Server<br/>Django Channels
    participant DB as 后端数据库
    participant BC as Bob 浏览器<br/>Web Crypto
    actor B as Bob（接收方）

    Note over A,B: 前提：双方已完成 ECDH P-256 密钥对生成并上传公钥

    A->>AC: 输入消息明文
    AC->>WS: GET /api/keys/{bob_id}/ 获取 Bob 活跃公钥
    WS->>DB: 查询 UserPublicKey(is_active=True)
    DB-->>WS: Bob 公钥 + key_version + fingerprint
    WS-->>AC: identity_public_key, key_version

    AC->>AC: 生成临时 ECDH 密钥对 (ephemeral)<br/>ECDH(Alice私钥, Bob公钥) → shared_secret<br/>AES-256-GCM 加密 → ciphertext + nonce + auth_tag
    AC->>AC: 为发送方副本生成独立临时密钥<br/>ECDH(临时私钥, Alice公钥) → sender_copy<br/>加密明文 → sender_copy_ciphertext
    Note over AC: 服务端从未接触明文

    AC->>WS: WebSocket: message.single.send<br/>ciphertext, nonce, auth_tag,<br/>sender_ephemeral_public_key,<br/>sender_copy_ciphertext,<br/>client_message_id
    WS->>DB: INSERT EncryptedMessage(全部密文字段)
    DB-->>WS: OK
    WS-->>AC: message.single.accepted

    WS->>BC: Push: message.single.received
    BC->>BC: ECDH(Bob私钥, Alice公钥) 解密 ciphertext<br/>→ 明文 "Hello Bob!"
    BC-->>B: 显示消息

    BC->>WS: WebSocket: message.receipt.update<br/>status: read
    WS->>DB: UPDATE EncryptedMessage.status = "read"
    WS-->>AC: message.receipt.updated (已读)
    AC-->>A: 显示双勾已读
```

**说明：** 私聊 E2EE 全流程，包含 Phase 3 新增的前向保密（Forward Secrecy）：发送方生成临时 ECDH 密钥对，接收方密文和发送方副本分别使用独立临时密钥加密。`sender_copy_ciphertext` 允许发送方在其他设备解密自己发送的消息。服务端在任何环节不接触明文，已读回执仅标记状态。

---

## 6. 群聊逐成员加密时序图（含邀请流程）

```mermaid
sequenceDiagram
    actor S as Alice（群主/发送方）
    participant SC as Alice 浏览器
    participant WS as WebSocket Server
    participant DB as Database
    participant RC1 as Bob 浏览器
    participant RC2 as Carol 浏览器

    Note over S,RC2: 群聊成员: Alice(群主), Bob, Carol

    S->>SC: 输入群消息 "Team meeting at 3pm"
    SC->>WS: GET /api/keys/batch/?user_ids=bob,carol
    WS->>DB: 批量查询 UserPublicKey(is_active=True)
    DB-->>WS: Bob公钥+Carol公钥 + key_versions
    WS-->>SC: 批量公钥

    SC->>SC: 为 Bob: 生成临时 ECDH → AES-GCM → ciphertext_B<br/>为 Carol: 生成临时 ECDH → AES-GCM → ciphertext_C
    Note over SC: ciphertext_B ≠ ciphertext_C<br/>每成员独立临时密钥 + 独立密文

    SC->>WS: WebSocket: message.group.send<br/>recipients: [{bob, ct_B}, {carol, ct_C}],<br/>membership_version: 1

    WS->>DB: BEGIN TRANSACTION
    WS->>DB: INSERT GroupMessage (logical)
    WS->>DB: INSERT GroupMessageRecipient(Bob, ct_B, ephemeral_key_B)
    WS->>DB: INSERT GroupMessageRecipient(Carol, ct_C, ephemeral_key_C)
    WS->>DB: COMMIT
    WS-->>SC: message.group.accepted

    WS->>RC1: Push: ct_B + ephemeral_key_B
    WS->>RC2: Push: ct_C + ephemeral_key_C
    RC1->>RC1: 本地 ECDH + AES-GCM 解密 → 明文
    RC2->>RC2: 本地 ECDH + AES-GCM 解密 → 明文
```

**说明：** 群聊采用"一个逻辑消息 + 逐成员独立临时密钥 + 独立密文副本"模型。发送方为每个成员单独生成临时 ECDH 密钥对并加密，`ciphertext_B ≠ ciphertext_C`。`membership_version` 校验确保成员变更后消息只面向当前有效成员。Phase 3 新增 `GroupInvitation` 两步邀请流程（管理员审批 → 受邀者确认）。

---

## 7. AI Assistant 时序图

```mermaid
sequenceDiagram
    actor U as 用户
    participant FE as 前端 AI 面板
    participant BE as Django Backend
    participant DB as Database
    participant LLP as LlmProvider
    participant EXT as 外部 LLM API

    Note over U,EXT: LLM 不可读取聊天 E2EE 明文或密钥

    U->>FE: 打开 AI Assistant 面板
    FE->>BE: GET /api/llm/config/
    BE->>DB: 查询 UserLLMConfig
    DB-->>BE: api_url, model, encrypted_api_key
    BE->>BE: Fernet 解密 API Key
    alt 已配置 API Key
        BE-->>FE: {"provider": "qwen/anthropic/openai", "status": "available"}
    else 未配置
        BE-->>FE: {"provider": "mock", "status": "fallback"}
    end

    U->>FE: 输入 Prompt "帮我总结这段文本..."
    FE->>BE: POST /api/llm/chat/ {"prompt": "..."}
    Note over BE: 仅接收用户主动输入的 Prompt<br/>不读取聊天数据库

    BE->>BE: validate_llm_endpoint(endpoint)<br/>校验 HTTPS + 域名白名单 + 非内网 IP
    BE->>LLP: get_llm_provider(config)

    alt Anthropic / OpenAI 兼容 / Qwen
        LLP->>EXT: POST /v1/messages 或 /v1/chat/completions
        EXT-->>LLP: {"content": "..."}
    else Mock 降级
        LLP->>LLP: MockProvider.complete()
        Note over LLP: [Mock] 模拟回复
    end

    LLP-->>BE: response_text
    BE-->>FE: {"response": "...", "provider": "qwen"}
    FE-->>U: 显示 AI 回复

    opt 用户将回复复制为草稿
        U->>FE: 点击"复制为草稿"
        FE->>FE: 填入聊天输入框（本地操作）
        Note over FE: 进入正常 E2EE 加密流程
    end
```

**说明：** AI Assistant 的 LLM 组件仅处理用户主动输入的 Prompt，**不**自动读取聊天密文或明文。API Key 通过 Fernet 加密存储在 `UserLLMConfig`。`validate_llm_endpoint()` 对用户配置的 LLM 端点进行 HTTPS 校验、域名白名单过滤和 SSRF 防护（禁止私有/环回/保留 IP）。MockProvider 确保无 API Key 时仍可演示。

> ⚠️ **安全边界**：LLM Provider 不持有用户私钥，不访问 `EncryptedMessage`/`GroupMessage` 表，不解密 Session Key，不参与 ECDH 密钥协商。

---

## 8. 活动图：用户从登录到聊天

```mermaid
stateDiagram-v2
    [*] --> 登录页
    登录页 --> 输入凭证
    输入凭证 --> 验证身份
    验证身份 --> 登录失败: 凭证无效
    登录失败 --> 输入凭证: 重试
    验证身份 --> 聊天主页: 登录成功

    聊天主页 --> 检查密钥
    检查密钥 --> 生成ECDH密钥对: 无本地密钥
    生成ECDH密钥对 --> 上传公钥
    检查密钥 --> 上传公钥: 密钥未上传
    上传公钥 --> 选择操作

    选择操作 --> 搜索联系人: 添加联系人
    搜索联系人 --> 发送好友申请
    发送好友申请 --> 等待对方同意
    等待对方同意 --> 打开私聊

    选择操作 --> 打开私聊: 已有联系人
    打开私聊 --> 获取对方公钥
    获取对方公钥 --> 输入消息
    输入消息 --> 生成临时密钥对
    生成临时密钥对 --> AES256GCM加密
    AES256GCM加密 --> WebSocket发送密文
    WebSocket发送密文 --> 收到已读回执
    收到已读回执 --> 选择操作

    选择操作 --> 创建群组
    创建群组 --> 选择初始成员
    选择初始成员 --> 群聊逐成员加密发送

    选择操作 --> 打开AI面板
    打开AI面板 --> 输入Prompt
    输入Prompt --> LLM返回结果
    LLM返回结果 --> 选择操作

    选择操作 --> [*]: 退出登录
```

**说明：** 从登录开始，经历密钥初始化（如需要）、联系人建立、私聊 E2EE 加密发送（含临时密钥生成）、群聊逐成员加密、AI Assistant 使用的完整活动流程。每条消息发送前生成临时 ECDH 密钥对实现前向保密。

---

## 9. 活动图：AI Assistant 使用流程

```mermaid
stateDiagram-v2
    [*] --> 打开AI_Assistant面板
    打开AI_Assistant面板 --> 检查LLM配置

    检查LLM配置 --> 加载UserLLMConfig: 已配置
    检查LLM配置 --> 环境变量检测: 未配置数据库
    环境变量检测 --> Mock降级: 无任何Key
    环境变量检测 --> 使用环境变量Key: 有Key
    加载UserLLMConfig --> Fernet解密API_Key

    Fernet解密API_Key --> 验证LLM端点
    验证LLM端点 --> 端点不合法: 非HTTPS/内网IP
    端点不合法 --> 显示安全错误
    显示安全错误 --> [*]
    验证LLM端点 --> 端点合法

    使用环境变量Key --> 端点合法
    端点合法 --> 用户输入Prompt
    Mock降级 --> 用户输入Prompt

    用户输入Prompt --> 内容校验
    内容校验 --> 校验失败: 过长/违规
    校验失败 --> 显示错误提示
    显示错误提示 --> 用户输入Prompt: 重试

    内容校验 --> 校验通过
    校验通过 --> 路由LLM_Provider

    路由LLM_Provider --> Anthropic_API: Anthropic端点
    路由LLM_Provider --> OpenAI兼容API: OpenAI/Qwen端点
    路由LLM_Provider --> Mock响应: Mock模式

    Anthropic_API --> 流式输出结果
    OpenAI兼容API --> 流式输出结果
    Mock响应 --> 返回结果

    流式输出结果 --> 用户查看回复
    返回结果 --> 用户查看回复

    用户查看回复 --> 复制为草稿: 用于聊天
    复制为草稿 --> 填入聊天输入框
    填入聊天输入框 --> E2EE加密流程: 手动操作进入加密

    用户查看回复 --> 继续提问: 多轮对话
    继续提问 --> 用户输入Prompt

    用户查看回复 --> 关闭面板
    关闭面板 --> [*]
```

**说明：** AI Assistant 使用流程的核心安全约束：配置优先从 `UserLLMConfig`（Fernet 加密）读取，其次检测环境变量。`validate_llm_endpoint()` 校验 HTTPS、域名白名单和 IP 合法性后方可调用。LLM 回复**不自动**进入 E2EE 聊天——用户需手动复制粘贴触发本地加密流程。调用失败时自动降级到 MockProvider。

---

## 10. 交付总结

| # | 图名 | 类型 | 状态 |
|---|------|------|------|
| 1 | 系统用例图 | Use Case Diagram | ✅ |
| 2 | 数据库 ER 图 | ER Diagram | ✅ (新增) |
| 3 | 核心类图 | Class Diagram | ✅ |
| 4 | 系统组件图 | Component Diagram | ✅ |
| 5 | 私聊 E2EE 时序图（含前向保密） | Sequence Diagram | ✅ |
| 6 | 群聊逐成员加密时序图（含邀请流程） | Sequence Diagram | ✅ |
| 7 | AI Assistant 时序图 | Sequence Diagram | ✅ |
| 8 | 用户从登录到聊天活动图 | Activity Diagram | ✅ |
| 9 | AI Assistant 使用流程活动图 | Activity Diagram | ✅ |

**共交付 9 张 Mermaid 图**（最低要求 5 张），新增数据库 ER 图，覆盖全部核心模块、E2EE 加密链路、前向保密机制和 Phase 3 LLM 接入边界。

所有图表可在 GitHub 上直接渲染（Mermaid 原生支持）。

### 当前代码一致性校验

- **ER 图一致性**：图中所列关系与 `accounts/models.py`（16 个模型）和 `chat/models.py`（17 个模型）完全对应，字段详见 ER 图下方表格。
- **类图一致性**：聚焦 17 个核心类，新增 `UserLLMConfig`、`LlmProvider` 及 3 个实现类，`EncryptedMessage`/`GroupMessage`/`GroupMessageRecipient` 均已加入前向保密字段。辅助 settings 类和文件传输类在说明文字中标注。
- **时序图一致性**：私聊/群聊 E2EE 流程与 `static/js/private-chat-e2ee.js`、`static/js/group-chat-e2ee.js` 和 `chat/consumers.py` 实现一致；AI Assistant 流程与 `chat/llm.py`（`get_llm_provider`、`validate_llm_endpoint`）一致。
- **组件图一致性**：架构分层与 `ichat_pro/settings.py`（Django + Channels + CSP Middleware）、前端模板结构和 `package.json`（Electron + Tailwind）对应。
- **LLM 安全边界**：所有图表遵从 `chat/llm.py` 中 `validate_llm_endpoint()` 的 SSRF 防护和 `UserLLMConfig` 的 Fernet 加密方案。
