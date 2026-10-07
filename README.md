# iChat Pro

iChat Pro 是基于 Django、Channels 和 Web Crypto 的安全即时通信课程项目，支持账号、联系人、私聊、群聊、加密文件、消息管理、设置和独立 AI Assistant。Windows 客户端通过 Electron 连接同一云服务。

截至 2026-10-07，核对基线为 `f2f60ec`：Phase4 原始 78 项任务中 29 项关闭、49 项开放。云端部署、消息可靠性及 Windows 的云模式、安装、品牌、托盘、连接恢复和敏感存储已有验收；通知尚待最后的客户端布局复验，文件桌面集成、Android 和多设备增强仍未完成。详情见 [项目现状](docs/current-status.md) 和 [文档总览](docs/README.md)。

## 运行依赖

| 部分 | 当前实现 |
| --- | --- |
| 服务端 | Python 3.13+、Django 6.0.5、Channels 4.3.2、Daphne（由 channels[daphne] 安装，本地复核为 4.2.1） |
| 数据库 | 本地默认 SQLite；生产 PostgreSQL 16，驱动 psycopg 3.3.6 |
| 分发与缓存 | 本地内存；生产 Redis 6 系列、channels-redis 4.3.0；CI Redis 7 |
| Web | Django 模板、JavaScript、Web Crypto、生产自托管 Tailwind CSS |
| Windows | Electron 39 系列、electron-builder 25 系列、NSIS，应用版本 1.0.0 |

具体 Python 版本锁定见 [requirements.txt](requirements.txt)，Node 依赖以两份 package-lock.json 为准。Python `redis==8.1.0` 是客户端库版本，不是服务器版本。

## 本地开发

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 127.0.0.1:8000
```

浏览器访问 `http://127.0.0.1:8000/`。本地未指定 DATABASE_URL/REDIS_URL 时使用 SQLite 和内存通道，不能据此证明 PostgreSQL 并发或 Redis 跨进程语义。

settings.py 从进程环境读取配置，不会自动加载 `.env`。`.env.example` 是占位说明；复制文件不会使配置生效。PowerShell 可明确设置 `$env:DJANGO_DEBUG = 'True'` 等变量。生产配置用 [deploy/env.production.example](deploy/env.production.example)，由部署脚本和 systemd 加载，说明见 [Runbook](deploy/runbook.md)。

## Windows 客户端

```powershell
cd desktop
npm ci
npm start
```

默认云地址为 `https://chat.20060810.xyz:8443`，来自 [defaults.json](desktop/defaults.json)。云模式不启动 Python/Django。需要覆盖服务地址时，在启动前设置 `$env:ICHAT_SERVER_URL = 'https://your-server.example:8443'`。

本地开发使用 `npm run dev` 或 `ICHAT_DEV=1`；未显式指定云地址时，开发模式启动本地 Django，默认 `127.0.0.1:8000`。`ICHAT_HOST`、`ICHAT_PORT`、`ICHAT_PYTHON` 用于该分支。显式 ICHAT_SERVER_URL 优先于 dev 请求；远程 HTTP 被拒绝，loopback HTTP 允许用于本地测试。旧变量 ICHAT_SKIP_DJANGO 已不适用。

```powershell
cd desktop
npm run dist
```

产物在 `desktop/dist/iChat-Pro-Setup-1.0.0.exe`。安装包只包含桌面壳与 Electron 资源，聊天页面和脚本由云端提供。卸载默认保留数据；交互选择“是”删除当前用户 `%APPDATA%\ichat-pro-desktop`，静默卸载保留数据。真实安装/卸载验收已完成，隔离 NSIS 测试不能代替原生验收。EXE 名称、图标和版本已校验，未配置商业代码签名证书。

## 验证

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
npm ci
npm run test:e2ee
```

Windows 桌面专用检查：

```powershell
cd desktop
npm ci
npm run test:layout
npm run test:branding
npm run test:installer
```

最后一条需要 NSIS 编译器，可用 MAKENSIS 指定路径。基线 [CI run 37630595919](https://github.com/HeanX/iChat_Pro/actions/runs/37630595919) 的 test、integration、javascript、windows-installer 全绿：SQLite 和 PostgreSQL/Redis 各执行 404 条 Django 测试，Chromium 布局 36/36，NSIS 隔离断言 24/24。覆盖率门禁、扫描和强制合并检查仍待实施。

## 功能与边界

- HTTP/WS 文本、文件、转发共用事务消息服务；同 ID 原请求重放不重复落库、计未读或生成事件，冲突返回 409。
- ConversationEvent 记录新增消息，sync API 用签名游标补取，按当前权限投影；撤回/删除事件同步和历史事件回填尚未实现。
- 待发箱位于内存，断网及托盘隐藏/恢复保留；重启、崩溃或页面重载不保证恢复未确认消息。
- 长期私钥在 IndexedDB 的不可导出 CryptoKey 中；桌面待导出备份、草稿和 AI 历史经 safeStorage 加密，Web 回退限制见 [协议存储边界](docs/phase4/protocol.md#7-敏感数据存储边界)。
- Android APK、设备级身份/分发/撤销、自动更新及生产监控告警不能作为当前已交付能力。

## 本地演示数据

仅在隔离的开发数据库中运行 `python demo_setup.py`，创建 alice、bob、carol，演示密码均为 `demo1234`。这些是本地演示数据，不是公网测试账号；不要在生产执行此脚本。

## 目录

```text
accounts/       账号、公钥与设置
chat/           模型、HTTP、WS、统一服务及测试
ichat_pro/      配置、ASGI、认证中间件、健康检查
static/         前端、加密与连接模块
templates/      Django 页面
desktop/        Windows 桌面壳、IPC、打包及原生测试
deploy/         云部署、备份、恢复和续期脚本
docs/           现行说明、课程需求、历史报告及证据
```

交付不包含 `.env`、私钥、数据库、用户媒体、本地依赖或 dist 二进制。原始任务范围见 [Phase4 需求](docs/phase4/requirements.md)，实时完成状态以 [任务清单](docs/phase4/tasks.md) 和对应 Issue 证据为准。
