# iChat Pro 分模块交接文档（接手总入口）

> 编写日期：2026-10-18（Asia/Shanghai）。**基线提交是 `cfe53be`，它在分支 `docs/handoff-guide-and-test-media-isolation` 上，不在 `main`。** `main` 与 `origin/main` 都是 `4703ff3`；本地领先 origin 3 个提交（`78e7f54` / `452c73c` / `cfe53be`）且**未推送**。
>
> **2026-10-26 状态更新（只追加，不改写上面 2026-10-18 的历史结论）**：本目录的 11 份文档已随
> `codex/phase4-design-docs-fixes` 分支**纳入版本控制**（此前全部未跟踪），第 5 份
> `cloud-deploy-ops.md` 按 `deploy/**` 实际实现补写（DOC-01）。当前工作基线已不是 `cfe53be`：
> `origin/main` 仍是 `4703ff3`，而修复分支基于它合并了 t38/t40/t43/t13 四个分支。
> 各模块文档正文仍按 `cfe53be` 勘察，行号会随合并漂移——按符号名检索，不要只信行号。
>
> **开工前先确认基线**：如果你从 `origin/main` clone，会得到 `4703ff3`，不含测试隔离修复 `78e7f54`（头像测试会往仓库 `media/` 写文件）。要么先用上游分支，要么从 `cfe53be` 起新分支：
>
> ```powershell
> git switch -c feature/<任务号>-<短名> cfe53be
> ```
>
> 核对时点工作区只有本目录与 `.agents/` 未跟踪。
>
> 本文是"按模块派活给其他 Agent / Session"用的交接包入口。项目级现状仍以 [docs/current-status.md](../current-status.md) 和 [docs/phase4/tasks.md](../phase4/tasks.md) 为准；本目录不重复它们的全部结论，只补"接手某个模块时你需要知道的全部具体细节"。
>
> **强制的第一条规则**：接手的 Agent 只要写了代码，就必须另开一个独立子 Agent 做审查。见 [conventions.md §1](conventions.md#1-硬性要求编码必须开独立审查子-agent)。

## 这套文档解决什么问题

原来的 [项目交接文档](../项目交接文档.md) 是**按人**写的（从零启动、代码入口、签收表），[agent-handoff.md](../phase4/agent-handoff.md) 是**按工作包**写的（WP00–WP08 的边界与提示词）。两者都停在"这个包大概要做什么"，缺少"这个模块现在长什么样、改哪里、怎么验证、哪些坑不能踩"。

本目录按**代码边界**重新切成 8 个模块，每个模块一份文档，任何 Agent（或 Human）拿到一份就能独立开工，不需要先读另一个模块。

## 模块清单

| # | 文档 | 覆盖范围 | 对应 Issue（开放项） | 谁最适合接 |
| --- | --- | --- | --- | --- |
| 1 | [accounts-auth.md](accounts-auth.md) | `accounts/`：用户、资料、联系人、公钥与信任、隐私、多账号、设置 API | #167 #168 #169 #170（设备模型扩展） | 后端 + 熟悉 Django 认证 |
| 2 | [messaging-protocol.md](messaging-protocol.md) | `chat/services/`、`chat/consumers.py`、`chat/errors.py`、sync 补取、协议契约 | #164 #194 #200 | 后端 + 协议严谨型 |
| 3 | [web-frontend-e2ee.md](web-frontend-e2ee.md) | `static/js/`、`templates/`、`ichat_pro/csp_middleware.py`、E2EE 与通知门控 | #145 #165 #189 #196 | 前端 + Web Crypto |
| 4 | [windows-desktop.md](windows-desktop.md) | `desktop/`：Electron 主进程、preload 桥、NSIS 打包、原生测试 | #145 #146 #184 | Electron/Windows |
| 5 | [cloud-deploy-ops.md](cloud-deploy-ops.md) | `deploy/`、`ichat_pro/settings.py`、health、nginx/systemd、备份恢复 | #130 #139 #176 #177 #204 | 运维 |
| 6 | [data-storage-file.md](data-storage-file.md) | `chat/models.py`、迁移、加密文件分片与下载、media 边界、清理任务 | #155 #188 #193 #198 | 后端 + 数据建模 |
| 7 | [testing-quality-ci.md](testing-quality-ci.md) | Django/Node/Electron/NSIS 四层测试、`.github/workflows`、覆盖率与门禁 | #171 #172 #173 #174 #190–#205 | QA/CI |
| 8 | [android-mobile.md](android-mobile.md) | **尚不存在**的 Android 端：从零立项需要什么、与现有协议/存储/服务的接口 | #149–#157 #185 #197 | 移动端 |

> **2026-10-26 补齐（DOC-01）**：第 5 份 `cloud-deploy-ops.md` 此前**只被引用、文件不存在**（本目录当时
> 只有 10 份，且全部未跟踪）。现已按 `deploy/**` 的实际实现重写并纳入版本控制；核对提交
> `2905473`（`iChat_Pro.wt-t176`），逐文件 blob 校验见该文 §0。
> 本目录实有 **12 个文件** = 上面 8 份模块文档 + 3 份跨模块文档（`conventions.md`、
> `issue-backlog.md`、`prompt-template.md`）+ 本 `README.md`。

跨模块共用的内容单独放在：

- [conventions.md](conventions.md)：分支/提交/PR/评审/证据格式、文档维护规则、禁止事项（**每份模块文档都假设你已读过这一份**）。
- [issue-backlog.md](issue-backlog.md)：49 个开放 Issue 的完整清单、依赖关系、建议推进顺序（可直接当派工单用）。
- **[prompt-template.md](prompt-template.md)**：派工用的 Prompt 模板（最短版 / 完整版 / 审查者专用），含模块参数表和常见误用。**不想自己写提示词就直接用这个。**

## 每份模块文档的统一结构

为保证可执行性，8 份文档都按同一骨架写：

1. **这块现在是什么**：文件清单、行数、职责、真实的调用关系。
2. **读代码的最短路径**：先读哪 3 个文件，再读哪些，跳过哪些。
3. **关键契约**：不能改的东西（协议字段、错误码、存储键名、IPC 通道名、DB 约束）。
4. **本地跑起来 + 验证命令**：精确命令、预期输出、耗时。
5. **已知坑**：代码里真实存在的缺陷、注释与实现不一致的地方、文档与代码不符的地方。
6. **这个模块的开放任务**：对应 Issue 号 + 缺口 + 建议起点。
7. **交给下一个 Agent 的提示词模板**：可直接复制。

> **第 6 条不含验收标准原文**。所有开放的 Phase4 Issue 用同一套模板，验收标准在 Issue 正文的 `## 验收标准` 段。开工前必须自己拉：

```powershell
gh issue view <编号> --repo HeanX/iChat_Pro --json body --jq '.body'
```

除了每张卡自己的业务验收项，模板固定还带 3 条（少一条都不算完成）：非作者评审、自动化或人工测试留证据、文档同步更新。

## 与其他文档的关系

| 文档 | 什么时候用 |
| --- | --- |
| [docs/current-status.md](../current-status.md) | 想知道"整体到什么程度了" |
| [docs/phase4/tasks.md](../phase4/tasks.md) | 想知道"某个 P4 Txx 关没关、证据是什么" |
| [docs/phase4/traceability-matrix.md](../phase4/traceability-matrix.md) | 想知道"某条 NEW-* 需求被哪张卡覆盖" |
| [docs/项目交接文档.md](../项目交接文档.md) | 人接手项目：环境、权限、签收 |
| [docs/GitHub协作开发指南.md](../GitHub协作开发指南.md) | 多人协作流程细节 |
| **本目录** | **Agent 接手某个模块：改哪里、怎么验证、有什么坑** |

## 使用约束（重要）

1. **本目录是勘察快照，不是承诺**。文中的行号对应 `cfe53be` 工作树；代码变了行号会漂移，但文件名和函数名相对稳定，检索时优先用符号名。
2. **不要在模块文档里写"已完成"**。完成状态只有一个出口：`docs/phase4/tasks.md` + 对应 Issue 证据。模块文档只描述现状与缺口。
3. **文档中出现的任何编号（测试数、SHA、覆盖率）都必须可复跑**。本次编写时实际执行过的命令与结果记在 [testing-quality-ci.md](testing-quality-ci.md) 的"本次实测"一节。
4. **未在本地验证的事项明确标注**。生产服务器、真实设备、Android、安装版交互均未在本次核对中复验。
5. **本目录是互相引用的**。`conventions.md` 是全部 8 份的前置；`data-storage-file.md` ↔ `messaging-protocol.md`（事件模型）、`accounts-auth.md` → `messaging-protocol.md`（群 recipients 语义）、`android-mobile.md` → 四份。说"只看一份就能开工"是不成立的——**至少要先读 `conventions.md`**。

### 未验证清单（来源可信度较低的部分）

下面这些写成了陈述句，但**只能从文档/runbook 转述，无法从仓库证实**：

| 断言 | 来源 | 状态 |
| --- | --- | --- |
| 生产部署 `f2f60ec`（2026-10-07T13:42:16Z） | `deploy/runbook.md` + 历史记录 | 未复验 |
| AWS ap-northeast-1d / t3.small / EIP `13.158.121.80` | 同上 | 未复验 |
| 共存服务占 443/2096/25383 | 同上 | 未复验（按 runbook 陈述） |
| 证书自动续期钩子实际生效 | 同上 | 未复验 |
| #145 "只差最终复验" | Issue 评论 | 未复验（需安装版登录态操作） |
| 安装包 SHA-256 `ac468df3…d5f9` | 本地 `desktop/dist/` 实际文件 | 本次未重算 |
| [android-mobile.md](android-mobile.md) 引用的 `../phase4/adr-p4-03-mobile-tech-stack.md` | 该文件**不在** `4703ff3` / `origin/main` 上 | 2026-10-26 核对：仅存在于分支 `feature/p4-t22-mobile-adr-and-skeleton`（如 `d5892f9`）。本目录按原样收编该文档，**未**改写其中的链接；合并该分支后链接自动恢复 |

## 本次编写实际执行的验证

环境：Windows / Python **3.13.12**（仓库内 `.venv`）/ Django 6.0.5 / Node v24.14.1 / npm 11.13.0，本地 SQLite + InMemoryChannelLayer（**不是** PostgreSQL/Redis）。

| 命令 | 结果 | 耗时 |
| --- | --- | --- |
| `.\.venv\Scripts\python.exe manage.py check` | `System check identified no issues (0 silenced).` | <5s |
| `.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run` | `No changes detected` | <5s |
| `.\.venv\Scripts\python.exe manage.py test --parallel 1` | `Ran 404 tests in 147.447s` / `OK (skipped=2)` | 151.7s |
| `npm run test:e2ee` | 11 个文件全通过；其中 `node --test` 部分 62 项通过、0 失败 | 约 40s |
| `node chat/tests/js/chat_connection.test.js` | 输出 9 项 ✓（文件定义 20 个 `test_*`，`tests` 数组只引用 19 个） | 5.2s |

未执行：`desktop` 的 `test:layout` / `test:branding` / `test:installer`、任何 Android 构建、任何生产服务器操作（本机也没有 PostgreSQL/Redis）。

**用哪个解释器**：`PATH` 上裸 `python` 是 3.13.1 + Django 2.2.4，跑 `manage.py` 会 `ModuleNotFoundError: No module named 'cgi'`。**一律用 `.\.venv\Scripts\python.exe`。**
