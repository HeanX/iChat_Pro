# iChat Pro Phase 3 演示脚本与验收文档

> 版本：v1.0
> 日期：2026-06-16
> 作者：ketter1024
> 关联 Issue：#119 (P3 T07)
> 适用：Phase 3 团队演示与课程验收

本文档为 Phase 3 现场演示提供完整操作步骤、演示账号、API 配置指引和已知限制说明。

---

## 1. 演示准备

### 1.1 环境要求

- Python 3.12+（虚拟环境 `.venv` 已安装 `requirements.txt` 依赖）
- Node.js 18+（Electron 桌面端可选）
- 两个浏览器或浏览器 + 隐身窗口（模拟双用户）
- （可选）Qwen API Key（阿里云百炼平台）

### 1.2 一键启动

```powershell
# 1. 激活虚拟环境
.\.venv\Scripts\Activate.ps1

# 2. 应用数据库迁移
python manage.py migrate

# 3. 创建演示账号
python demo_setup.py

# 4. 启动开发服务器
python manage.py runserver 127.0.0.1:8000
```

打开浏览器访问 `http://127.0.0.1:8000/`。

### 1.3 演示账号

| 用户名 | 密码 | 用途 |
|--------|------|------|
| `alice` | `demo1234` | 主演示账号 |
| `bob` | `demo1234` | 联系人/私聊对方 |
| `carol` | `demo1234` | 群聊第三成员 |

三个账号已通过 `demo_setup.py` 自动建立双向联系人关系。

---

## 2. 演示流程（8 步）

### 步骤 1：登录演示账号

| 操作 | 预期结果 |
|------|----------|
| 在浏览器 A 打开 `http://127.0.0.1:8000/` | 自动跳转登录页 |
| 输入 `alice` / `demo1234`，点击登录 | 进入聊天主页，左侧显示会话列表 |
| 在浏览器 B（隐身窗口）登录 `bob` / `demo1234` | 进入聊天主页 |

> **演示点**：注册/登录流程完整可用，表单校验正常，错误提示清晰。

### 步骤 2：展示 Phase 2 核心能力

| 操作 | 预期结果 |
|------|----------|
| 点击左侧栏 **Settings** | 设置首页显示，包含个人资料、通知、隐私安全等入口 |
| 点击 **Notifications** | 通知设置页，开关和滑块可操作 |
| 点击 **Privacy and Security** | 隐私设置页，可见性和权限选项可切换 |
| 点击 **Data and Storage** | 数据存储页，缓存统计和清理入口 |
| 点击 **Contacts** | 联系人侧栏，显示 bob 和 carol |
| 搜索 `bob` | 搜索结果正确显示 |

> **演示点**：设置中心 4 个二级页面完整可用，联系人搜索正常，左侧栏不遮挡聊天区。

### 步骤 3：私聊 E2EE 演示

| 操作 | 预期结果 |
|------|----------|
| alice 点击会话列表中的 bob 会话 | 打开私聊窗口 |
| alice 发送消息 `"Hello Bob! This message is end-to-end encrypted."` | 消息气泡出现，状态为已发送 |
| 切换到 bob 浏览器 | bob 实时收到消息，可正常解密显示 |
| bob 回复 `"Hi Alice! I received it securely."` | alice 实时收到 |
| 打开浏览器 DevTools → Network → WS | WebSocket 帧中只有 ciphertext/nonce，无明文 |
| 点击聊天头部 🔒 图标 | 显示加密协议信息和密钥指纹 |

> **演示点**：私聊 E2EE 实时收发、WebSocket 密文传输验证、密钥指纹展示。

### 步骤 4：群聊逐成员加密演示

| 操作 | 预期结果 |
|------|----------|
| alice 点击右下角 **+** → **New Group** | 创建群组界面 |
| 输入群名 `"Demo Team"`，选择 bob 和 carol | 创建成功，自动跳转群聊 |
| alice 发送 `"Team standup at 3pm today"` | 消息发送成功 |
| 切换到 bob | bob 实时收到群消息 |
| 切换到 carol | carol 同样实时收到 |
| 在 Django Admin 查看 GroupMessageRecipient 表 | 每位收件人的 ciphertext 值不同 |

> **演示点**：群聊逐成员独立加密、实时推送、密文互异。

### 步骤 5：打开 AI Assistant

| 操作 | 预期结果 |
|------|----------|
| alice 点击左侧栏 **AI Assistant** 入口 | 打开 AI Assistant 对话面板 |
| 顶部显示当前 Provider 状态 | `Qwen (available)` 或 `Mock (fallback)` |

> **演示点**：AI Assistant 入口清晰，状态可见。

### 步骤 6：AI 通用问答

| 操作 | 预期结果 |
|------|----------|
| 在 AI 输入框输入 `"What is end-to-end encryption?"` | |
| 点击发送 | AI 返回关于 E2EE 的解释 |
| 追问 `"How does ECDH key exchange work?"` | AI 返回 ECDH 原理说明 |

> **演示点**：AI 多轮对话能力，回答质量展示。

### 步骤 7：文本摘要与草稿生成

| 操作 | 预期结果 |
|------|----------|
| 粘贴一段英文文本（约 200 词）到输入框 | |
| 输入 `"Please summarize the above text in 3 bullet points."` | AI 返回 3 点摘要 |
| 输入 `"Generate a polite reply to this message: 'Can we reschedule to 4pm?'"` | AI 生成草稿回复 |
| 点击 **Copy to Draft** （复制为草稿） | 内容复制到剪贴板 |
| 切换到 bob 私聊，粘贴并发送 | 消息正常加密发送 |

> **演示点**：AI 文本摘要和草稿生成，用户手动将 AI 回复带入 E2EE 加密流程。

### 步骤 8：Mock Fallback 演示

| 操作 | 预期结果 |
|------|----------|
| （如 Qwen API Key 未配置则自动进入 Mock 模式） | AI 面板显示 `Mock Mode` 提示 |
| 输入任意问题 | 返回 `[Mock] 这是模拟回复。请配置 Qwen API Key 以获取实际 AI 回复。` |
| 说明：无 API Key 时仍可走通完整演示路径 | Mock 模式确保演示不中断 |

> **演示点**：Mock 降级机制确保无外部依赖时仍可演示完整 UI 流程。

---

## 3. Qwen API 配置指引

### 3.1 获取 API Key

1. 访问 [阿里云百炼平台](https://bailian.console.aliyun.com/)
2. 注册/登录阿里云账号
3. 进入「模型广场」→ 选择 qwen-plus 或 qwen-turbo
4. 在「API-KEY 管理」创建 API Key
5. 将 API Key 设置为环境变量：

```powershell
# Windows PowerShell
$env:QWEN_API_KEY = "sk-your-api-key-here"

# 或写入 .env 文件（不提交到 Git）
echo "QWEN_API_KEY=sk-your-api-key-here" >> .env
```

### 3.2 切换模型

默认使用 `qwen-plus`。可通过环境变量切换：

```powershell
$env:QWEN_MODEL = "qwen-turbo"  # 更快、更便宜
# 或
$env:QWEN_MODEL = "qwen-max"    # 更强能力
```

### 3.3 Mock 模式（无需 API Key）

当 `QWEN_API_KEY` 环境变量**未设置**时，系统自动进入 Mock 模式：
- AI Assistant 仍可打开和使用
- 所有请求返回 `[Mock]` 前缀的模拟回复
- 完整 UI 演示路径不受影响

---

## 4. 演示前检查清单

在执行演示前运行以下命令确认环境正常：

```powershell
# Django 系统检查
python manage.py check

# 迁移一致性检查
python manage.py makemigrations --check --dry-run

# 自动化测试
python manage.py test

# E2EE JavaScript 测试
node chat/test_private_chat_e2ee.js
```

预期全部通过：

| 检查项 | 命令 | 预期结果 |
|--------|------|----------|
| 系统检查 | `python manage.py check` | `System check identified no issues` |
| 迁移检查 | `python manage.py makemigrations --check --dry-run` | `No changes detected` |
| 自动化测试 | `python manage.py test` | `OK` (313 tests) |
| E2EE JS 测试 | `node chat/test_private_chat_e2ee.js` | `all tests passed` |

### 当前检查结果（2026-06-16）

```
✅ python manage.py check          → System check identified no issues (0 silenced).
✅ python manage.py makemigrations --check --dry-run → No changes detected
✅ python manage.py test           → Ran 313 tests in 238.771s — OK
✅ node chat/test_private_chat_e2ee.js → private-chat-e2ee: all tests passed
```

---

## 5. 已知限制与注意事项

### 5.1 AI Assistant 限制

| 限制 | 说明 | 计划 |
|------|------|------|
| 不读取 E2EE 聊天明文 | AI 无法自动理解聊天上下文 | Phase 4 考虑「用户主动授权上下文」 |
| 不接触密钥 | AI 不持有私钥或 Session Key | 安全设计，不会改变 |
| Mock 回复质量有限 | Mock 模式仅返回固定模板 | 正式演示建议配置 Qwen API Key |
| 不支持流式输出 | 当前为一次性返回完整响应 | Phase 4 考虑 SSE 流式 |

### 5.2 通用功能限制

| 限制 | 说明 | 计划 |
|------|------|------|
| Channel / 频道 | 菜单入口已禁用，显示占位状态 | Phase 4 |
| 语音/视频通话 | 未实现 | Phase 4+ |
| 多端同步 | 未实现完整跨设备消息同步 | Phase 4+ |
| 移动端 App | 未实现 | Phase 4+ |
| Signal Protocol | 当前使用 ECDH P-256 + AES-256-GCM 静态密钥方案 | Phase 4+ Double Ratchet |
| 图片/文件 E2EE | 模型和 API 已准备，前端文件传输 UI 部分实现 | Phase 3 后续 |
| Bot / Agent | 菜单占位但未实现真实 Bot 框架 | Phase 4 |

### 5.3 演示环境注意事项

- 数据库为 SQLite，演示前确保已运行 `python demo_setup.py`
- 双用户演示需要两个独立浏览器会话（不同 Profile 或隐身窗口）
- WebSocket 依赖 Django Channels InMemory 层，重启服务器后所有连接断开
- 首次使用时浏览器需生成 ECDH 密钥对，可能需要 1-2 秒
- 确保 127.0.0.1:8000 端口未被占用

---

## 6. 测试命令与结果

```powershell
# 全量后端测试
python manage.py test

# 仅运行 P2 后端测试
python manage.py test chat.test_p2_backend
python manage.py test chat.test_p2_issues

# 仅运行 accounts 测试
python manage.py test accounts

# E2EE JavaScript 模块测试
node chat/test_private_chat_e2ee.js
```

### 最新测试结果

```
Found 313 test(s).
System check identified no issues (0 silenced).
...................................................................................................................
----------------------------------------------------------------------
Ran 313 tests in 238.771s
OK
```

---

## 7. 截图清单

演示过程中建议截取以下关键界面（至少 2-3 张）：

| # | 截图内容 | 对应步骤 |
|---|----------|----------|
| 1 | 登录后的聊天主页（含会话列表） | 步骤 1 |
| 2 | 私聊 E2EE 收发 + WebSocket DevTools 密文展示 | 步骤 3 |
| 3 | 群聊创建与多用户收发 | 步骤 4 |
| 4 | AI Assistant 问答界面 | 步骤 6 |
| 5 | AI Assistant Mock 模式降级 | 步骤 8 |
| 6 | 设置中心隐私页面 | 步骤 2 |

---

## 8. 演示话术要点

1. **开场 (30s)**：iChat Pro 是一个端到端加密即时通讯桌面应用，Phase 2 完成了完整聊天闭环，Phase 3 新增 AI Assistant 能力。

2. **E2EE 安全亮点**：所有消息在浏览器端使用 Web Crypto API 加密，服务端只存储和转发密文。数据库、WebSocket、日志中不出现明文。

3. **AI Assistant 安全边界**：AI 不自动读取聊天记录，仅在用户主动输入 Prompt 时调用。用户的聊天 E2EE 密钥体系与 AI 模块完全隔离。

4. **Mock 降级**：无 API Key 时系统自动降级到 Mock 模式，确保演示不中断。这体现了系统的容错设计。

5. **已知限制**：Channel、Bot、移动端等能力规划在后续 Phase 中，当前占位入口均明确标注。

---

> **文档状态**：✅ 完成
> **关联文档**：`docs/iChat Pro 演示指南.md` (Phase 1) · `docs/iChat Pro Phase 2 验收手册.md` · `docs/iChat Pro UML 与架构图交付文档.md`
