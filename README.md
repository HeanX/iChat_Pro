# iChat Pro

iChat Pro 是一个基于 Django 的轻量级安全即时通信项目，面向课程小组作业交付。项目实现了账号体系、联系人、私聊、群聊、端到端加密消息、文件转发、会话管理、基础设置页、AI Assistant 面板和 Electron 桌面端包装。

## 功能概览

- 用户注册、登录、登出和个人资料管理
- 联系人关系、私聊会话创建和会话列表
- 群聊创建、成员管理、邀请、公告和静音
- WebSocket 实时消息收发
- 私聊和群聊端到端加密消息流程
- 文件传输、加密文件密钥分发和转发
- 消息已送达、已读、撤回、删除和自动清理
- 搜索、通知、隐私与安全、数据与存储等设置页面
- AI Assistant 配置与对话面板
- Electron 桌面客户端包装

## 技术栈

- Python 3.13+
- Django
- Django Channels
- SQLite
- HTML / CSS / JavaScript
- Tailwind CSS
- Node.js
- Electron

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py migrate
python manage.py runserver 127.0.0.1:8000
```

启动后访问：

```text
http://127.0.0.1:8000/
```

## 演示账号

可以运行演示数据脚本创建 3 个测试账号：

```powershell
python demo_setup.py
```

| 用户名 | 密码 |
| --- | --- |
| `alice` | `demo1234` |
| `bob` | `demo1234` |
| `carol` | `demo1234` |

## 测试

后端测试：

```powershell
python manage.py test
```

只运行 chat 应用测试：

```powershell
python manage.py test chat
```

前端端到端加密逻辑测试：

```powershell
npm run test:e2ee
```

测试代码已统一整理到：

```text
chat/tests/
```

## Electron 桌面端

```powershell
cd desktop
npm install
npm start
```

开发模式：

```powershell
cd desktop
npm run dev
```

默认情况下，桌面端会加载本地 Django 服务：

```text
http://127.0.0.1:8000/
```

也可以通过环境变量跳过自动启动 Django：

```powershell
$env:ICHAT_SKIP_DJANGO = "1"
cd desktop
npm start
```

## 目录结构

```text
accounts/      用户、资料、联系人、密钥信任相关功能
chat/          聊天、会话、消息、群组、AI Assistant 相关功能
chat/tests/    后端与加密逻辑测试
desktop/       Electron 桌面端
docs/          项目文档、设计文档和验收材料
ichat_pro/     Django 项目配置
static/        CSS、JavaScript、图片资源
templates/     Django 页面模板
```

## 重要文档

| 文档 | 说明 |
| --- | --- |
| `docs/README.md` | 文档目录总览和推荐阅读顺序 |
| `docs/iChat Pro 系统性介绍文档.md` | 系统目标、架构、模块、流程和交付边界 |
| `docs/iChat Pro 需求文档_修订版.md` | 项目需求说明 |
| `docs/iChat Pro API 接口文档.md` | API 接口说明 |
| `docs/iChat Pro 数据库设计规范文档.md` | 数据库设计 |
| `docs/iChat Pro 实时通信与端到端加密消息协议设计文档.md` | 实时通信与 E2EE 协议 |
| `docs/iChat Pro UML 与架构图交付文档.md` | UML 与架构图说明 |
| `docs/iChat Pro 演示指南.md` | 演示流程 |

## 环境变量

| 变量 | 说明 |
| --- | --- |
| `DJANGO_SECRET_KEY` | Django 密钥，生产或正式演示环境建议配置 |
| `DEBUG` | 是否启用调试模式 |
| `QWEN_API_KEY` | AI Assistant 使用的 API Key |
| `QWEN_MODEL` | AI Assistant 使用的模型名称 |
| `ICHAT_HOST` | Electron 加载的 Django 主机，默认 `127.0.0.1` |
| `ICHAT_PORT` | Electron 加载的 Django 端口，默认 `8000` |
| `ICHAT_SKIP_DJANGO` | 设置为 `1` 时 Electron 不自动启动 Django |

## 交付说明

提交或打包项目时建议排除以下本地文件和目录：

```text
.venv/
node_modules/
desktop/node_modules/
.idea/
.claude/
db.sqlite3
media/
outputs/
```

这些内容属于本地依赖、运行数据或生成产物，不是项目源码的必要组成部分。
