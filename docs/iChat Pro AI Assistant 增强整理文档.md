# iChat Pro AI Assistant 增强整理文档

## 1. 背景

本轮工作围绕 iChat Pro 内置 AI Assistant 页面展开，目标是把原本偏 Mock/演示性质的 AI 对话入口，整理成更接近真实可用的多模型 AI 会话能力，并让它在消息列表、顶部状态栏、右侧详情面板、转发、搜索、Markdown 渲染等交互上尽量和普通私聊保持一致。

改动重点包括：

- 支持真实模型配置：请求地址、API Key、模型名称。
- 支持 Qwen、Claude、GPT 系列模型显示与识别。
- 支持流式输出。
- AI 对话进入左侧消息列表，可打开多个 Assistant 会话。
- AI 消息支持普通消息的右键菜单、转发、搜索、删除等交互。
- AI 输出默认按 Markdown 渲染，不再要求 `/md` 前缀。
- 统一 AI 头像、名称、模型状态、右侧模型信息展示。

## 2. 后端能力

### 2.1 AI 状态接口

新增/完善 `api/ai/status/` 状态接口，用于前端判断 AI 能力是否可用。

当前状态接口返回：

- `available`：AI 模块是否可用。
- `configured`：是否已配置可用模型参数。
- `mock_mode`：是否处于本地 Mock 模式。
- `supported_modes`：支持的 AI 工作模式。

其中 `supported_modes` 用于前端后续动态渲染模式按钮，避免前端长期硬编码。

### 2.2 AI Chat 模式提示词

AI 对话接口支持 `mode` 参数，并根据模式使用不同 system prompt：

- `chat`：普通问答与对话。
- `summarize`：总结文本。
- `draft_reply`：润色或起草回复。

这样前端的“总结文本 / 润色消息 / 常见提问”等快捷入口可以使用同一接口，但获得不同的行为约束。

### 2.3 真实模型配置

AI 请求支持从前端传入真实模型配置：

- 请求地址：`endpoint`
- API Key：`api_key`
- 模型名称：`model`

后端根据配置选择 OpenAI-compatible 或 Anthropic-compatible 的调用方式。

### 2.4 流式输出

AI 对话接口支持 `stream: true`。

实现方式：

- 后端使用 `StreamingHttpResponse` 返回 `text/event-stream`。
- provider 逐段解析模型返回的 SSE chunk。
- 前端收到增量内容后持续更新同一个 AI 气泡，实现逐段输出效果。
- 流式错误会通过 SSE error 事件送回前端，避免只在后端日志里报错。

曾修复的问题：

- ASGI 下 `StreamingHttpResponse` 不能直接消费同步 `map`/普通 iterator。
- 改为 async generator，并用 `sync_to_async` 拉取同步 provider chunk。
- 模型 API 返回 403/503 等错误时，前端现在可以显示错误气泡。

## 3. 前端模型设置

### 3.1 右侧模型信息面板

右侧“模型信息”面板加入真实模型设置区域：

- 请求地址。
- API Key。
- 模型名称下拉框。
- 保存设置。
- 清空设置。
- 配置状态提示。

配置保存在浏览器 `localStorage` 中，不写入普通聊天消息。

### 3.2 请求地址下拉填写框

请求地址从普通 input 升级为自定义 combobox：

- 保留手动输入能力。
- 右侧提供下拉按钮。
- 下拉中显示常用预设地址。
- 保存设置时，将当前请求地址写入本地历史。
- 下次打开面板时可直接选择最近使用过的地址。

本地存储 key：

- `ichat_ai_endpoint_history`

内置预设包括：

- DashScope Qwen
- OpenAI
- Anthropic Claude
- 4Router

### 3.3 模型名称选择

模型名称选择框支持多个模型系列：

- Qwen 系列。
- Claude 系列。
- GPT 系列。

如果用户保存的模型名称不在内置选项中，前端会动态插入该选项，避免保存后无法回显。

## 4. 多 Assistant 会话

AI Assistant 不再只是一个固定窗口，而是进入左侧消息列表：

- 默认会话：`ai-assistant`
- 新建会话：`ai-assistant-{timestamp}`
- 左侧列表可显示多个 Assistant。
- 每个 Assistant 有独立本地历史。
- 每个 Assistant 可持有独立模型配置。

相关本地存储 key：

- `ichat_ai_assistants`
- `ichat_ai_history`
- `ichat_ai_history:{assistantId}`
- `ichat_ai_model_settings`
- `ichat_ai_model_settings:{assistantId}`

## 5. AI 头像、名称与状态统一

前端增加模型 provider 识别逻辑，根据模型名称推断 provider：

- 包含 `claude`：Claude Assistant。
- 包含 `gpt`、`openai` 或 `o1/o3/o4`：GPT Assistant。
- 包含 `qwen`、`tongyi`：Qwen Assistant。
- 未识别：AI Assistant。

统一作用范围：

- 左侧消息列表头像与名称。
- 顶部状态栏头像、标题和模型状态。
- AI 气泡头像。
- 右侧模型信息面板头像、名称、badge、模型简介。
- 搜索结果头像。
- 转发弹窗头像。

模型简介也根据 provider 动态调整：

- GPT：强调通用问答、复杂推理、代码辅助、结构化内容生成。
- Claude：强调长文本理解、稳健推理、写作润色、摘要协作。
- Qwen：强调中文场景、知识问答、总结、草稿润色和实用任务。

## 6. 图标资源

新增/整理了模型图标资源：

- `static/images/ai-model-claude.svg`
- `static/images/ai-model-gpt.svg`
- `static/images/ai-model-qwen.svg`

曾尝试添加 dark mode 专用图标，但实际视觉效果在圆形头像中存在偏移，后续已回滚：

- 取消 `darkIconUrl`。
- 取消 `dark_avatar_url`。
- 取消 light/dark 双图层头像渲染。
- 删除 `ai-model-*-dark.svg`。
- 暗色模式继续使用原亮色图标方案。

当前状态：

- 所有主题统一使用单套模型 SVG。
- AI 头像容器保持原本样式。

## 7. AI 消息渲染

### 7.1 Markdown 默认渲染

普通私聊/群聊中，Markdown 仍遵循 `/md` 前缀规则。

AI Assistant 中，assistant 回复默认视为 Markdown：

- 不要求 `/md` 前缀。
- 搜索结果中也按 AI Markdown 片段渲染。
- 转发 AI 消息到普通用户/群组时，会自动补 `/md`，避免 Markdown 丢失。

### 7.2 等待气泡

等待 AI 输出时的气泡改成普通消息气泡风格，不再使用额外的“Generating...”样式。

流式输出开始后，同一个气泡持续更新内容。

## 8. AI 消息交互

AI 对话消息已接入普通消息右键菜单体系。

支持：

- 复制。
- 回复。
- 转发。
- 删除本地 AI 消息。
- 多选。
- 批量转发。

AI 消息会被转换为普通消息 action 结构，以复用现有 `chat-message-actions.js`。

## 9. 转发能力

### 9.1 AI 消息转发到普通会话

AI assistant 回复转发到普通私聊/群聊时：

- 如果不是 `/md` 开头，会自动补：

```text
/md
原 AI 内容
```

这样普通消息渲染端能继续识别 Markdown。

### 9.2 普通消息转发到 AI

转发弹窗支持选择 AI Assistant 作为目标。

转发到 AI 时：

- 自动打开目标 AI 会话。
- 将被转发内容整理后填入 AI 输入框。
- 使用 `chat` 模式发送给 AI。

### 9.3 转发弹窗头像修复

转发目标头像逻辑已调整：

- AI Assistant 使用模型图标。
- 普通用户优先使用真实头像。
- 如果 target 中没有头像，会从 `conversationsById` 或 `conversations` 按会话 id / peer id / username 兜底查找。
- 最后才回退到首字母头像。

## 10. 搜索能力

AI Assistant 顶部搜索行为向普通私聊靠齐：

- 搜索时隐藏顶部身份信息。
- 搜索栏宽度与普通聊天头部更一致。
- 搜索结果显示头像、名称、日期、摘要。
- Assistant 回复搜索结果默认按 Markdown 渲染。
- 搜索摘要支持换行和多行截断，避免长文本不换行撑破布局。

## 11. UI 细节调整

已处理的前端细节包括：

- 左侧列表中 Assistant 选中状态下 badge 文本可读性。
- 右上角更多菜单透明度过低的问题。
- AI 顶部状态栏宽度与普通私聊对齐。
- AI 搜索状态下隐藏身份信息。
- 右侧模型设置区与现有卡片风格统一。
- 请求地址下拉菜单支持暗色模式。
- AI header、列表、气泡、详情面板的头像尺寸规则统一。

## 12. 安全与隐私边界

AI Assistant 的隐私提示保持明确：

- AI 不会自动读取、抓取或分析 E2EE 私聊/群聊内容。
- 只有用户在 AI 窗口主动输入并提交的文本会发送给模型。
- AI 对话历史和普通 E2EE 聊天记录隔离存储。
- API Key 保存在浏览器本地配置中。

右侧面板和输入区均保留“安全与隐私边界”说明。

## 13. 主要涉及文件

后端：

- `chat/llm.py`
- `chat/views.py`
- `chat/urls.py`

前端 JS：

- `static/js/chat.js`
- `static/js/chat-message-actions.js`

前端 CSS：

- `static/css/chat.css`

模板：

- `templates/components/ai_assistant_window.html`
- `templates/components/right_panel.html`
- `templates/pages/chat.html`

资源：

- `static/images/ai-model-claude.svg`
- `static/images/ai-model-gpt.svg`
- `static/images/ai-model-qwen.svg`

## 14. 当前注意事项

1. 目前模型配置主要保存在浏览器 localStorage，适合本地使用和快速验证。后续如果要多设备同步，需要设计服务端模型配置表。
2. API Key 目前属于前端本地保存，后续如果正式化，应考虑加密保存、用户确认、敏感字段遮蔽和清除机制。
3. 不同 provider 的接口协议并不完全一致。虽然已经支持 OpenAI-compatible 和 Anthropic-compatible，但聚合平台返回格式仍可能有差异，需要继续通过错误气泡暴露细节。
4. AI Assistant 的 Markdown 默认渲染是特例，普通私聊/群聊仍保持 `/md` 规则。
5. Dark mode 专用模型图标已回滚，当前使用统一亮色模型图标。

