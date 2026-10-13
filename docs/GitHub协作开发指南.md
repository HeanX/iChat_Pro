# iChat Pro GitHub 协作开发指南

> 编写日期：2026-10-12。适用于 [HeanX/iChat_Pro](https://github.com/HeanX/iChat_Pro) 的多人开发。命令使用 PowerShell；本次只编写流程，未更改 GitHub 权限、保护规则或远程分支。

## 1. 协作约定

采用 **Issue → 独立分支 → 本地验证 → Pull Request → 他人评审 → 合并 → 验收证据** 的流程。`main` 保持可演示、可测试；每个任务有一名主责成员，协议、迁移和共享文件由相关成员协调。

仓库已有 [任务模板](../.github/ISSUE_TEMPLATE/task.md) 与 [PR 模板](../.github/pull_request_template.md)。课程任务沿用 `P4 Txx` / `P4 Test Txx` 和 `NEW-*` 编号；新缺陷关联原任务，避免重复建卡或混用编号。

## 2. 加入仓库：选择一种方式

### 团队成员直接协作

仓库所有者在仓库 **Settings → Collaborators** 邀请成员，成员接受邀请后克隆原仓库。具体入口以 GitHub 当前界面为准；没有邀请权限的成员联系仓库所有者。

```powershell
git clone https://github.com/HeanX/iChat_Pro.git
cd iChat_Pro
git remote -v
git config user.name '你的提交姓名'
git config user.email '你的 GitHub 已验证邮箱或 noreply 邮箱'
```

每位成员使用自己的 GitHub 账号与认证，不共享密码或 Token，不把凭据写入远程 URL、代码或文档。开发环境按 [项目交接文档](项目交接文档.md#4-从零启动开发环境) 安装。

### 外部贡献者通过 Fork

没有原仓库写权限时，在 GitHub Fork 到自己的账号，clone 自己的仓库，添加原仓库为 upstream。把下面 `YOUR-USERNAME` 替换为自己的用户名。

```powershell
git clone https://github.com/YOUR-USERNAME/iChat_Pro.git
cd iChat_Pro
git remote add upstream https://github.com/HeanX/iChat_Pro.git
git remote -v
git fetch upstream
git switch main
git merge --ff-only upstream/main
git push origin main
```

此模式 `origin` 指个人 Fork，`upstream` 指原仓库。任务分支推到个人 Fork，PR 的 base 选 `HeanX/iChat_Pro:main`。参考 [GitHub 官方 Fork 远程配置](https://docs.github.com/en/pull-requests/how-tos/work-with-forks/configuring-a-remote-repository-for-a-fork)。

## 3. 领取任务与确定边界

开始前在 Issue 明确目标、主责人、范围、验收标准、依赖和涉及文件。已有任务先在原 Issue 协调；未完成工作不要提前关闭 Issue。

可补充以下任务信息：

```text
任务 / 需求编号：
负责人 / 协作人：
目标和用户行为：
本次范围 / 后续范围：
拟修改目录和公共文件：
前置依赖 / 协议与迁移影响：
验收步骤和通过条件：
测试环境 / 证据链接：
```

| 范围 | 协作方式 |
| --- | --- |
| 消息 HTTP/WS、统一服务、同步 | 共同评审协议和错误码，保持共用服务路径 |
| 加密、公钥、信任与平台存储 | 同时通知消息和平台模块维护人，验证历史兼容 |
| Windows / Android | 使用共享连接契约；Android 仍属待实现范围 |
| 模型、migration、公共路由 | 指定本轮整合人，迁移最终串行合并 |
| `settings.py`、requirements、package/lock | 协调依赖更新与配置模板，避免各自覆盖 |
| 公共模板、CSS、chat.js | 提前说明改动区域，避免同时大范围重构 |
| CI、部署、证据 | 分清代码检查、原生验收和生产发布责任 |

具体工作包与依赖见 [Phase4 分工与交接](phase4/agent-handoff.md)。成员姓名、职责和截止时间由团队实际确认，不根据文档自动指定。

## 4. 每次开发的完整流程

以下示例为原仓库协作者，任务分支采用 `feature/p4-t19-native-files`；替换任务号和名称后使用。

### 4.1 同步主分支并创建任务分支

```powershell
git status
git switch main
git pull --ff-only origin main
git switch -c feature/p4-t19-native-files
```

先处理自己的未提交改动再切换分支；可提交到原任务分支或用 `git stash push -u -m '切换任务前暂存'` 保存，恢复用 `git stash pop` 并检查结果。不要用 `git reset --hard` 清理协作工作区。`--ff-only` 失败表示本地主分支与远程分叉，应先检查历史。

Fork 用户用 `git fetch upstream` 和 `git merge --ff-only upstream/main` 同步本地 main，再创建分支；后续仍推送到自己的 origin。

| 类型 | 分支示例 |
| --- | --- |
| 功能 | `feature/p4-t19-native-files` |
| 修复 | `fix/p4-t18-preview-layout` |
| 测试 | `test/p4-test-t14-integration` |
| 文档 | `docs/handoff-guide` |
| Codex 执行环境默认 | `codex/p4-t19-native-files`，保留任务号即可 |

同一分支只处理一个明确任务或一组紧密关联问题。不要在 main 上开发后直接推送。

### 4.2 开发、验证并提交

根目录的标准检查：

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
npm run test:e2ee
git diff --check
git status --short
git diff
```

依赖按交接文档先安装。模型变更先生成并检查实际 migration；CSS 输入变更执行 `npm run build:css`；桌面变更另外执行 desktop 的 layout、branding、installer 检查。涉及并发、锁和跨进程广播时，需要真实 PostgreSQL/Redis 的 integration 验证。

只选择本任务文件暂存，例如：

```powershell
git add desktop/main.js
git diff --cached
git commit -m 'feat(desktop): add native file handling for P4 T19'
git push -u origin feature/p4-t19-native-files
```

示例中的文件仅说明显式暂存方式；按实际变更逐个添加必要代码、迁移、测试、文档和 lockfile，检查暂存内容是否完整。后续提交同一分支时用 `git push`。

提交建议使用 `feat`、`fix`、`test`、`docs`、`refactor`、`chore` 前缀，并说明具体行为。不要提交 `.env`、数据库、媒体、私钥、Token、node_modules 或安装包；`.gitignore` 不能保护已经跟踪的秘密文件。

### 4.3 创建 PR 与接受评审

在 GitHub 创建 PR：base 为原仓库 `main`，compare 为自己的任务分支。尚未具备合并条件时创建 Draft PR，用于尽早讨论契约和范围。

填写已有 PR 模板，并补充：

```text
问题与结果：具体触发场景、修改后的行为。
关联任务：Refs #实际Issue号；完整完成时才使用 Closes #实际Issue号。
范围：代码入口、协议 / 模型 / 配置变更。
验证：实际命令、环境、结果、CI 链接。
人工验收：真实操作步骤、必要截图、尚未验证范围。
兼容与风险：迁移、历史密文、平台行为、回退条件。
文档：更新了哪些现行说明与需求证据。
```

请至少一名相关成员评审。评审人检查权限与输入边界、消息幂等和事务、历史加密兼容、IPC、迁移、失败路径及实际测试范围。作者在原分支补充提交，回复评审意见；修复或说明后再解决讨论。新提交需重新核对最新 CI。

### 4.4 合并与后续记录

由具备权限的成员在评审通过、相关 CI 成功、冲突解决和证据齐备后合并；合并方式由团队统一约定，现有仓库历史包含 merge commit。部分实现只用 `Refs #...`，保留任务开放。

合并后在 Issue 补充 commit/PR、验证环境和验收证据，按实际结果更新 [任务清单](phase4/tasks.md) 与 [需求矩阵](phase4/traceability-matrix.md)。代码合并不自动代表原生验收、任务关闭或生产发布完成。

```powershell
git switch main
git pull --ff-only origin main
```

确认不再需要任务分支后再删除。发布由运维职责成员按 [Runbook](../deploy/runbook.md) 执行，分别记录 main、生产与安装包版本。

## 5. 同时开发、冲突与 migration

长时间开发时，将主分支最新改动合入任务分支。以下命令执行前先确认工作区干净；Fork 用户把 `origin/main` 改为 `upstream/main`。

```powershell
git switch feature/p4-t19-native-files
git fetch origin
git merge origin/main
git status
```

有冲突时打开标记文件，结合双方意图合并，移除 `<<<<<<<`、`=======`、`>>>>>>>` 标记，逐个 `git add`，然后 `git merge --continue`。重新执行受影响检查后推送。尚未理解冲突时可 `git merge --abort` 返回合并前状态；不要整文件只选一方来掩盖冲突。

共享分支不要随意 rebase 或强推，以免重写他人历史。采用 merge 同步即可保留双方提交。

两个分支修改同一 Django app 模型时，提前协调迁移编号与依赖。合并后先 `python manage.py showmigrations`；只有确认是可兼容的迁移分叉时才考虑 `python manage.py makemigrations --merge`，生成后审查依赖与操作，再在隔离数据库执行迁移和测试。不要删除已经上线的 migration、随意重编号或用 `--fake` 掩盖问题。

依赖有冲突时保留正确的 package/requirements 意图，用项目对应工具重新生成 lockfile，检查差异并验证；根目录与 desktop 依赖分别维护。

## 6. CI 与主分支保护

当前代码中的 [django.yml](../.github/workflows/django.yml) 对 PR 和 main push 运行：

| job | 检查内容 |
| --- | --- |
| `test` | Django check、迁移一致性、SQLite 测试 |
| `integration` | PostgreSQL 16 / Redis 7、迁移与 Django 测试 |
| `javascript` | E2EE、连接、ACK、通知等 JavaScript 回归 |
| `windows-installer` | Chromium 布局、品牌资源与隔离 NSIS 检查 |

已有 2026-10-07 资料记录当时未配置 required status checks，rulesets 为空；本次未读取在线设置。团队应把“CI 失败不能合并”作为协作约定，并由管理员核实是否已设置技术约束。

建议管理员为 main 配置：合并前必须 PR、至少一名他人批准、讨论解决、上述四项检查成功；限制强推和删除，按团队需要设置新提交后的重新批准与管理员绕过策略。这些是待管理员落实的建议，不是本次已生效设置。功能可用范围与配置说明见 [GitHub 官方受保护分支文档](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)。

流水线存在不能证明覆盖率门禁、安全扫描、Android 构建或自动生产发布已经实现。NSIS 隔离检查和 Chromium fixture 也不能代替真实安装版业务验收。

## 7. 常见问题与提交前清单

| 问题 | 处理方式 |
| --- | --- |
| push 被拒绝 / 403 | 核对账号、邀请、origin；无写权限时使用 Fork |
| push 提示 non-fast-forward | 先 fetch 检查同名远程分支；协调后合并，不直接强推 |
| main 更新失败 | 检查本地提交和未提交改动，将自己的开发保留在任务分支 |
| 本地通过但 CI 失败 | 查看失败 job 的原始日志，核对 Python/Node、大小写路径和 PG/Redis 差异 |
| 没有平台或服务器测试资源 | 写明未验证范围，提供代码检查证据，专项验收保留开放 |
| 误提交秘密 | 立即撤销/轮换凭据，联系仓库管理员处理历史；后续删文件不能撤销泄露 |

提交 PR 前确认：

- [ ] Issue 目标、负责人、范围与验收条件清晰，公共文件已协调。
- [ ] 分支基于主分支，变更只包含本任务必要内容。
- [ ] 代码、迁移、测试、配置模板与现行文档齐全，无秘密与用户数据。
- [ ] 实际执行的检查和未执行范围如实记录，最新 CI 已核对。
- [ ] 界面、原生、协议与生产行为按真实验收范围附证据。
- [ ] 关联 Issue 正确，只有完整满足验收标准才使用关闭语句。

详细编码要求沿用 [代码规范、版本管理与测试工具说明](course-testing/代码规范、版本管理与测试工具说明.md)。历史实验报告保留原结果，新的状态和证据附带日期更新。
