# iChat Pro 技术栈

### 1. 总体定位

你们这个项目可以定位成：

**基于端到端加密的轻量级即时通讯桌面客户端**

或者更稳一点：

**基于 Web 技术封装的轻量级加密即时通讯系统**

核心架构是：

```text
Electron 桌面客户端
    ↓ 加载 Web 页面
Django Web 系统
    ├── Django Templates：页面渲染
    ├── HTMX：局部刷新
    ├── Tailwind CSS：界面样式
    ├── Channels：WebSocket 实时消息
    ├── Web Crypto API：前端私聊加密/解密
    └── SQLite：本地/服务端轻量数据库
```

这套方案的优势是：**你们主要还是在做 Web 开发，不需要真的去写原生桌面软件。Electron 只是把 Web 应用套进桌面窗口里。** Electron 官方定位就是用 JavaScript、HTML、CSS 构建桌面应用，并通过 Chromium 和 Node.js 运行在 Windows、macOS、Linux 等桌面系统上。([Electron](https://electronjs.org/?utm_source=chatgpt.com))

------

### 2. Django：系统主体后端

Django 是整个项目的“主框架”。它负责用户注册登录、好友管理、群聊管理、消息存储、后台管理、数据库 ORM 等核心功能。Django 官方定位是一个高级 Python Web 框架，强调快速开发和清晰实用的设计，比较适合课程项目从概念快速做到可运行系统。([Django Project](https://www.djangoproject.com/?utm_source=chatgpt.com))

在你们项目里，Django 可以负责这些模块：

| 模块       | Django 负责什么                |
| ---------- | ------------------------------ |
| 用户模块   | 注册、登录、退出、用户资料     |
| 好友模块   | 查找用户、好友申请、联系人列表 |
| 会话模块   | 私聊会话、群聊会话             |
| 消息模块   | 保存密文消息、查询历史消息     |
| 群组模块   | 创建群聊、邀请成员、移除成员   |
| 管理员模块 | 用户管理、群组管理、消息管理   |
| 密钥模块   | 保存用户公钥、加密算法信息     |

Django 的一个大优势是自带 Admin 后台。你们可以不用单独写复杂后台页面，直接用 Django Admin 管理用户、群组、消息记录。这样“管理员管理功能”很容易做出截图，也方便写进系统实现章节。

推荐 Django App 拆分：

```text
server/
  manage.py
  config/
    settings.py
    urls.py
    asgi.py
  accounts/      用户、登录、注册、资料
  contacts/      好友、好友申请
  chats/         会话、消息、WebSocket
  groups/        群聊、群成员
  crypto_keys/   用户公钥、加密元数据
  admin_panel/   管理员扩展功能
  templates/
  static/
```

你们报告里的“系统架构图”“类图”“数据模型”都可以围绕这些模块展开。

------

### 3. HTMX：减少复杂前端框架

HTMX 的作用是：**让普通 HTML 也能做局部刷新**。它可以通过 HTML 属性直接使用 AJAX、CSS Transitions、WebSockets、SSE 等能力，从而构建现代交互界面。([htmx](https://htmx.org/docs/?utm_source=chatgpt.com))

它适合你们的原因是：不用完整引入 Vue/React，也能做出类似 SPA 的体验。

例如：

| 功能         | HTMX 可以怎么做                |
| ------------ | ------------------------------ |
| 点击会话列表 | 局部刷新中间聊天窗口           |
| 搜索联系人   | 局部刷新搜索结果               |
| 发送好友申请 | 不刷新整个页面，只更新按钮状态 |
| 加载历史消息 | 上滑或点击后追加旧消息         |
| 修改资料     | 提交表单后局部更新头像/昵称    |

也就是说，页面整体还是 Django 模板渲染，但用户体验不再是“点一下刷新整页”的老式网站，而是更接近 Telegram 这种局部切换。

例如结构可以是：

```html
<div id="chat-window"
     hx-get="/chats/room/12/"
     hx-trigger="click"
     hx-target="#main-panel">
</div>
```

意思是点击某个会话时，只把中间聊天区域换掉，不刷新整个页面。对大作业来说，这种技术点很容易解释：**系统采用 HTMX 实现局部刷新，降低前后端分离复杂度，同时提升用户交互体验。**

------

### 4. Tailwind CSS：快速做 Telegram 风格界面

Tailwind CSS 是负责界面样式的。它是 utility-first CSS 框架，可以用大量原子类直接组合出页面样式，比如 `flex`、`pt-4`、`text-center` 等。([Tailwind CSS](https://tailwindcss.com/?utm_source=chatgpt.com))

对你们的聊天系统来说，Tailwind 很适合做这种界面：

```text
左侧：会话列表
中间：聊天窗口
右侧：联系人/群信息面板
底部：消息输入框
```

界面可以参考 Telegram Desktop：

```text
┌────────────────┬────────────────────────────┬────────────────┐
│ 会话列表        │ 当前聊天窗口                 │ 用户/群信息      │
│ 搜索框          │ 消息气泡                     │ 成员列表         │
│ 好友/群聊       │ 输入框                       │ 操作按钮         │
└────────────────┴────────────────────────────┴────────────────┘
```

Tailwind 的好处是，你们不用花太多时间写一堆 CSS 文件。聊天气泡、三栏布局、按钮、输入框、卡片、阴影、圆角都可以快速做出来。对于“系统实现”章节，界面截图会明显比默认 Bootstrap 或裸 HTML 好看。

------

### 5. Django Channels：实时聊天核心

普通 Django 主要处理 HTTP 请求，例如登录、查询消息、加载页面。但聊天系统需要实时通信，比如 A 发消息后，B 的聊天窗口马上收到。这就需要 WebSocket。

Django Channels 的作用就是把 Django 扩展到 WebSocket 等异步通信场景。Channels 官方说明中也直接提供了聊天室教程，并建议 WebSocket 路径使用 `/ws/` 这类前缀，方便和普通 HTTP 请求区分。([Channels](https://channels.readthedocs.io/en/latest/tutorial/part_2.html?utm_source=chatgpt.com))

在你们项目里，Channels 主要负责：

| 功能         | Channels 负责什么       |
| ------------ | ----------------------- |
| 私聊实时消息 | A 发消息，B 实时收到    |
| 群聊实时消息 | 群成员实时收到群消息    |
| 在线状态     | 用户上线/下线提示，选做 |
| 消息推送     | 不用用户手动刷新        |
| 输入状态     | “对方正在输入”，选做    |

WebSocket 路径可以这样设计：

```text
/ws/chat/private/<conversation_id>/
/ws/chat/group/<group_id>/
```

前端发送消息流程：

```text
用户输入明文
→ 前端加密成密文
→ WebSocket 发送密文
→ Django Channels 接收密文
→ 保存到 SQLite
→ 推送给接收方
→ 接收方前端解密显示
```

这个流程特别适合画“需求分析时序图”，也是你们项目最容易加分的地方。

------

### 6. SQLite：轻量级数据库

SQLite 负责数据存储。它是小型、快速、自包含、高可靠、功能完整的 SQL 数据库引擎。官方也说明 SQLite 是 serverless、zero-configuration 的事务型 SQL 数据库引擎。([SQLite](https://sqlite.org/?utm_source=chatgpt.com))

对你们大作业来说，SQLite 非常合适，因为：

1. 不需要安装 MySQL 或 PostgreSQL。
2. 数据库就是一个 `.sqlite3` 文件。
3. Django 默认就支持 SQLite。
4. 方便导出 SQL 文件。
5. 方便演示，减少“数据库服务没启动”的事故。

你们的大作业要求数据库定义和内容要转为 SQL 文件，代码也要单独打包提交。 SQLite 正好方便满足这个要求。

建议核心表：

| 表名                 | 作用                 |
| -------------------- | -------------------- |
| `auth_user` / `User` | 用户                 |
| `UserProfile`        | 用户资料             |
| `FriendRequest`      | 好友申请             |
| `Contact`            | 好友关系             |
| `Conversation`       | 会话                 |
| `Message`            | 消息，保存密文       |
| `Group`              | 群聊                 |
| `GroupMember`        | 群成员，多对多中间表 |
| `UserPublicKey`      | 用户公钥             |
| `AdminLog`           | 管理员操作日志       |

其中 `GroupMember` 特别适合写进报告，因为用户和群组是典型的多对多关系，需要用中间表拆分。你们文档要求也提到图表说明、数据模型说明、索引设计这些内容，所以可以在报告里明确写：为 `message.conversation_id`、`message.created_at`、`group_member.user_id`、`group_member.group_id` 建立索引，提高历史消息查询和群成员查询效率。

------

### 7. Electron：桌面 App 外壳

Electron 不负责核心业务，它只负责把你们的 Web 系统包装成桌面客户端。Electron 官方文档说明，它可以通过嵌入 Chromium 和 Node.js，让开发者维护一套 JavaScript 代码并创建 Windows、macOS、Linux 跨平台桌面应用。([Electron](https://electronjs.org/docs/latest?utm_source=chatgpt.com))

你们有两种用法：

#### 方案 A：最稳方案，Electron 只加载本地 Django 页面

开发和答辩时这样运行：

```text
1. 启动 Django 服务：http://127.0.0.1:8000
2. 启动 Electron
3. Electron 打开 http://127.0.0.1:8000
```

Electron 主进程大概就是：

```javascript
const { app, BrowserWindow } = require('electron');

function createWindow() {
  const win = new BrowserWindow({
    width: 1200,
    height: 800,
    title: 'SecureChat',
  });

  win.loadURL('http://127.0.0.1:8000');
}

app.whenReady().then(createWindow);
```

这个方案最稳，适合课程演示。缺点是要先手动启动 Django 后端。

#### 方案 B：高级方案，Electron 启动时自动拉起 Django

Electron 启动后自动执行 Python：

```text
Electron 启动
→ child_process 拉起 python manage.py runserver
→ Electron 加载 http://127.0.0.1:8000
→ 关闭 Electron 时结束 Python 进程
```

这个看起来更完整，但容易踩坑：Python 路径、虚拟环境、端口占用、打包后资源路径、Windows 杀进程等问题都会出现。对大作业来说，**不建议一开始就做这个**。先用方案 A，等项目稳定后再考虑方案 B。

------

### 8. Web Crypto API：私聊端到端加密

如果你们保留 E2EE 亮点，那么加密最好放在前端，而不是 Django 后端。Web Crypto API 是浏览器提供的密码学接口，可以让脚本使用密码学原语来构建加密系统。([MDN Web Docs](https://developer.mozilla.org/en-US/docs/Web/API/Web_Crypto_API?utm_source=chatgpt.com)) MDN 的 SubtleCrypto 文档也说明，`encrypt()` 支持 RSA-OAEP、AES-GCM 等算法。([MDN Web Docs](https://developer.mozilla.org/ja/docs/Web/API/SubtleCrypto/encrypt?utm_source=chatgpt.com))

推荐课程版实现：

```text
注册/首次登录：
浏览器生成用户密钥对
→ 公钥上传服务器
→ 私钥保存在本地

发送私聊：
输入明文
→ 前端生成 AES 会话密钥
→ 用 AES-GCM 加密消息
→ 用接收方公钥包装 AES 密钥
→ 把 ciphertext、iv、wrapped_key 发给服务器

接收私聊：
从服务器拿到 ciphertext、iv、wrapped_key
→ 用本地私钥解出 AES 密钥
→ 用 AES-GCM 解密消息
→ 前端显示明文
```

数据库保存的不是明文，而是类似：

```text
ciphertext
iv
wrapped_key
algorithm
sender_id
receiver_id
created_at
```

报告里不要写“绝对安全”，应该写：**系统通过客户端加密、服务端密文存储的方式，降低服务器泄露或数据库泄露时私聊内容暴露的风险。**

------

### 9. 这套技术栈的模块关系

可以这样理解：

| 技术           | 它在项目中的角色 | 一句话解释                   |
| -------------- | ---------------- | ---------------------------- |
| Django         | 主体后端         | 管用户、群组、消息、数据库   |
| HTMX           | 页面局部刷新     | 让普通模板也有 SPA-like 体验 |
| Tailwind CSS   | UI 样式          | 快速做出现代聊天界面         |
| Channels       | 实时通信         | WebSocket 收发消息           |
| SQLite         | 数据库           | 保存用户、群组、密文消息     |
| Electron       | 桌面包装         | 把 Web 系统变成桌面 App      |
| Web Crypto API | 加密模块         | 前端完成私聊加密和解密       |

真正的核心是：

```text
Django + SQLite：业务和数据
Channels：实时消息
HTMX + Tailwind：界面体验
Web Crypto：加密亮点
Electron：展示包装
```

------

### 10. 推荐开发顺序

不要一上来就 Electron，也不要一上来就 E2EE。推荐顺序如下：

**第一阶段：Django 基础系统**
完成注册、登录、退出、用户资料、Django Admin、SQLite 表结构。

**第二阶段：联系人和群组**
完成好友申请、联系人列表、创建群聊、群成员管理。

**第三阶段：普通聊天跑通**
先不加密，先让私聊和群聊可以发送、保存、显示历史消息。

**第四阶段：Channels 实时通信**
把“发送后刷新才能看到”改成 WebSocket 实时显示。

**第五阶段：私聊 E2EE**
只对私聊做端到端加密，群聊先保持普通消息或后续扩展。

**第六阶段：Tailwind 美化界面**
做 Telegram 风格三栏布局、聊天气泡、会话列表、资料面板。

**第七阶段：Electron 包装**
浏览器版稳定后，再用 Electron 包装成桌面端。

这个顺序非常重要。因为一旦先上 Electron 或先上加密，调试成本会很高。

------

### 11. 适合写进报告的系统架构描述

你们报告里可以这样写：

> 本系统采用 Django + SQLite 构建后端业务与数据持久化层，使用 Django Templates 与 HTMX 实现页面渲染和局部刷新，通过 Tailwind CSS 构建类 Telegram 的现代化聊天界面。系统使用 Django Channels 提供 WebSocket 实时通信能力，使用户能够实时发送和接收私聊、群聊消息。在安全性方面，系统在私聊模块中引入端到端加密机制，客户端基于 Web Crypto API 完成消息加密与解密，服务端仅负责密文消息的转发和存储。为提升桌面端使用体验，系统通过 Electron 对 Web 客户端进行封装，使其具备桌面应用形态。

------

### 12. 这套栈的优缺点

| 方面     | 优点                             | 风险                            |
| -------- | -------------------------------- | ------------------------------- |
| 开发效率 | Django 很快，HTMX 减少前端复杂度 | 前端复杂交互不如 Vue/React 灵活 |
| 界面效果 | Tailwind 能快速做出现代 UI       | 需要一定审美和布局能力          |
| 实时通信 | Channels 适合聊天系统            | WebSocket 调试比普通 HTTP 难    |
| 数据库   | SQLite 简单稳定，适合提交        | 不适合高并发生产环境            |
| 桌面端   | Electron 符合 Web 经验           | 打包和后端启动可能踩坑          |
| 加密     | E2EE 是亮点                      | 密钥管理不要做太复杂            |

------

### 13. 最终建议

你们就按这个路线做：

**主线：Django + HTMX + Tailwind CSS + Channels + SQLite**
**亮点：私聊端到端加密**
**包装：Electron 桌面客户端**
**不要做：真正 P2P、完整 Signal 协议、语音视频、复杂多端同步**

这套技术栈的最大好处是：**既能做出像 Telegram Desktop 的产品形态，又不会脱离你们已有的 Web 开发经验；既能体现技术亮点，又能满足软工大作业对需求分析、架构设计、数据库设计、系统实现和测试的文档要求。**