# 交接通用约定（所有模块共用）

> 适用对象：接下 iChat Pro 任一模块的 Agent 或 Session。**在动代码之前读完本文**，然后只读你那份模块文档。
> 编写日期：2026-10-18；核对基线 `cfe53be`。注意这个提交**不在 main 上**：`main` 与 `origin/main` 都是 `4703ff3`，`cfe53be` 在分支 `docs/handoff-guide-and-test-media-isolation` 上（本地领先 origin 3 个提交，未推送）。

## 0. 本文的边界（先读这段，避免误用）

| 本文负责 | 本文**不**负责 |
| --- | --- |
| 流程与纪律：分支/提交/PR、独立审查、证据规则、文档同步、安全红线 | 某个模块的代码在哪、怎么改、有哪些坑（见 8 份模块文档） |
| 环境与命令的**正确写法**（用哪个解释器、哪些命令是必跑的） | 测试条数、覆盖率、CI run 号等**具体数字**（唯一权威：[testing-quality-ci.md](testing-quality-ci.md)） |
| 交付时该怎么写、哪些话不许说 | 任务是否完成（唯一出口：`docs/phase4/tasks.md` + Issue 证据） |

**本文不对应任何一张任务卡**：它是一切模块的共同前置，没有自己的 Issue 号（§3 里出现的 `146` 只是演示命令写法用的示例）。你那张卡的验收标准必须自己去 `gh issue view <n>` 拉原文（§3）。

**怎么判断本文里的话过期了**：本文中的行号、分支名、版本号都会漂移。凡是可以重新跑一遍验证的断言，都在文末 [§8](#8-本文自身的核对记录) 留了命令与实测结果。**如果你跑出来的结果和 §8 不一致，以你的实测为准，并把差异回报给文档模块**——不要沿用过期结论。

---

## 1. 硬性要求：编码必须开独立审查子 Agent

这是本项目的**强制流程**，不是建议：

> **任何写代码、改配置、改迁移、改测试的 Agent，在宣称"完成"之前，必须另外开一个独立的子 Agent 对自己的改动做审查。**

具体含义：

| 项目 | 要求 |
| --- | --- |
| 谁开 | 写代码的那个 Agent 自己开（不是等人来催） |
| 审查者是什么 | 一个**全新上下文**的子 Agent，不继承作者的推理过程与自我辩解 |
| 审查者拿到什么 | 只给：任务号 + 验收标准 + `git diff` 全文 + 需要跑的命令。**不要**给它作者的实现说明，否则它只会复述作者的想法 |
| 审查范围 | 见下方"审查清单"7 项 |
| 审查结论 | 必须明确输出 `PASS` / `PASS WITH FINDINGS` / `FAIL`，以及可复现的问题列表（文件:行 + 触发条件 + 实际/预期 + 严重度 `BLOCKER`/`MAJOR`/`MINOR`） |
| 不通过时 | 作者修复后**重新开一个新的审查子 Agent**（不复用旧的那个，避免锚定）；**连续 3 轮不通过必须停下来问人**，不许自行放行 |
| 记录 | 审查结论与修复轮次写进 PR 描述和 Issue 评论；合并前最后一轮的 `PASS` 必须来自**非作者** |
| 开不出来时 | 如果执行环境确实无法新建独立上下文，**必须在交付里写"未执行独立审查"，不得静默跳过、不得用自审冒充** |

### 怎么开这个审查子 Agent

如果执行环境提供 `subagent` / `Task` / `Agent` 类工具，直接用下面的骨架；如果没有，见本节末尾的降级方案。

```text
第一步：先把改动落成可读的 diff（未 commit 的改动三点 diff 取不到！）

  git status --short
  # 先显式暂存本任务范围的文件（见 §2：不要用 git add -A 把别人的改动卷进来）
  git add <本任务的具体文件>
  git diff --cached --stat                 # 核对暂存范围
  git commit -m "wip: <任务号> 待审查"      # 或先正常提交
  New-Item -ItemType Directory -Force .review | Out-Null
  git diff origin/main..HEAD > .review/pending.diff      # 注意是两点，不是三点
  git diff --stat origin/main..HEAD > .review/pending.stat

  （`.review/` 已在工作区 .gitignore 中（第 47 行，尚未提交）；用完删除。）

> **两个实测过的坑**：
> 1. **`.review/` 在基线提交 `cfe53be` 里并不存在**，`.gitignore` 里的忽略规则也是未提交的工作区改动。新 clone / 新分支上直接 `> .review/pending.diff` 会报 `DirectoryNotFoundException: 未能找到路径...的一部分`，`pending.diff` 根本不会生成，而审查者拿到的是空文件。**先 `New-Item -ItemType Directory -Force .review`。**
> 2. `git diff origin/main..HEAD` 是**两点**，会把 `origin/main` 之后**所有**提交都算进去。在共享分支上这意味着你审查的 diff 里会混进别人的改动（实测：17 个文件、1477 行那次，HEAD 是 `bfe6076` 而不是基线）。**先用 `git status --short` 与 `git diff --cached --stat` 确认范围**，必要时改用 `git diff <你分叉出去的那个提交>..HEAD`。

第二步：开一个全新会话/子 Agent，初始提示词只包含：

  ---
  你是独立审查者。仓库：<绝对路径>。分支：<branch>。
  任务编号：<#nnn>。
  验收标准原文（下面是从 gh issue view <n> 直接复制的，不要替我解读）：
  <粘贴 Issue 正文的验收段落>
  diff：.review/pending.diff（自己读，不要问我要说明）
  必须自己执行的命令：
    .\.venv\Scripts\python.exe manage.py check
    .\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
    <本模块的测试命令>
  按 docs/handoff/conventions.md §1 的 7 项清单逐条给结论。
  每个问题给：文件:行 / 触发条件 / 实际 / 预期 / BLOCKER|MAJOR|MINOR。
  你可以读代码、跑命令；不得修改任何文件。
  最后一行必须是：PASS 或 PASS WITH FINDINGS 或 FAIL
  ---

第三步：作者按问题清单修复 → 再开**新的**审查会话（不是继续同一个）。

第四步：PR 描述里记录：

  | 轮次 | 审查者 | 结论 | BLOCKER/MAJOR/MINOR | 修复提交 |
  | --- | --- | --- | --- | --- |
```

**降级方案（没有 subagent 工具时）**：新建一个空白会话，只把上面的提示词与 diff 路径给它，不继承当前上下文。**仍然不行**就在交付模板里写"未执行独立审查"，并说明原因。

**审查者拿不到 Issue 原文时**：用 `gh issue view <n> --json title,body` 拿，不要用作者转述。

### 审查清单（每轮都要过）

1. **验收对齐**：改动是否真的满足该 Issue 的验收标准？有没有只做了一半就宣称完成？
2. **权限与输入边界**：所有新入参是否校验类型/范围/长度？越权路径（换成别人的 id 会怎样）是否被挡？
3. **事务与幂等**：写路径是否走统一服务层？重放会不会重复落库/重复计未读/重复广播？
4. **加密兼容**：有没有破坏历史密文可读性？有没有引入明文落库/落日志？
5. **失败路径**：网络错误、DB 锁、超时、部分成功时行为是什么？有没有吞异常？
6. **测试真实性**：声称"通过"的测试是否真跑过？测试是否真的覆盖了新行为（还是只覆盖了旧行为）？有没有把 fixture/隔离环境当成原生验收？
7. **证据完整**：命令、环境、SHA、结果、未验证范围是否如实记录？

### 反模式（出现即打回）

- 作者自己说"我检查过了"代替独立审查。
- 审查子 Agent 和作者是同一个 session（会共享偏见上下文）。
- 用"测试全绿"代替原生/跨端验收（Chromium fixture ≠ 安装版实测；健康探针 ≠ 业务冒烟）。
- 为了让流水线变绿而删除/跳过失败测试。
- 把未运行的检查写成"通过"。

---

## 2. 分支、提交、PR

### 分支

```
feature/p4-t19-native-files     功能
fix/p4-t18-preview-layout       修复
test/p4-test-t14-integration    测试
docs/handoff-guide              文档
codex/<任务号>-<短名>            Codex 等执行环境默认前缀
```

一个分支只做一件事。**不要在 main 上开发后直接推**。

**基线分支与 `main` 的状态（2026-10-18 实测）**：`main` 与 `origin/main` **都在 `4703ff3`，二者相差 0 个提交**——`main` 并不领先。未推送的 3 个提交（`78e7f54` / `452c73c` / `cfe53be`）在分支 `docs/handoff-guide-and-test-media-isolation` 上，不在 `main` 上。核对命令：

```powershell
git rev-list --count origin/main..main     # 期望 0
git rev-list --count origin/main..HEAD     # 本目录基线分支上是 3
```

**这个工作区可能是共享的**：多人/多 Agent 会同时在同一份 checkout 里切换分支。开工前先跑 `git rev-parse --abbrev-ref HEAD` 确认自己在哪个分支上，不要假设还停在基线。`git status` 里出现不属于你的改动（别人的分支、别人的未跟踪文件）时，**只提交你范围里的文件**，不要顺手 `add` 别人的东西。

### 提交

前缀：`feat` / `fix` / `test` / `docs` / `refactor` / `chore` / `ci`，带范围与任务号：

```
feat(desktop): add native file handling for P4 T19
fix(messaging): keep replay ordering ahead of membership version check
test(accounts): isolate avatar crop test from the real MEDIA_ROOT
```

显式 `git add <具体文件>`，再 `git diff --cached` 核对。**不要** `git add -A`。

### PR

- base = `HeanX/iChat_Pro:main`；未就绪用 Draft。
- `Refs #n`（部分完成）vs `Closes #n`（完整满足验收才用）。
- 必须写：问题与结果、范围、验证（命令 + 环境 + CI 链接）、人工验收（步骤 + 截图 + 未验证范围）、兼容与风险、文档更新。
- 模板已存在：`.github/pull_request_template.md`（含"文档与证据同步"三项勾选）。

### 禁止

- `git reset --hard` 清理协作工作区；强推共享分支；rebase 他人历史。
- 提交 `.env`、数据库、`media/`、私钥、Token、`node_modules`、`dist/` 安装包。
- `--fake` 掩盖迁移问题、删除已上线 migration、随意重编号。

---

## 3. 状态与证据规则

### 来源优先级（不可倒置）

```
实际代码/部署  >  具体 CI run  >  原生/专项验收  >  对应 Issue 文字
```

**需求文档、规划文档、设计文档不构成完成证据。**

### 三个 SHA 必须分开记

| 对象 | 当前值 | 记录位置 |
| --- | --- | --- |
| 代码 `main` | `4703ff3`（origin） | `git rev-parse origin/main` |
| 生产部署 | `f2f60ec`（2026-10-07T13:42:16Z） | `/opt/ichat/DEPLOYMENTS.log` |
| Windows 安装包 | `ac468df3…d5f9` | `desktop/dist/iChat-Pro-Setup-1.0.0.exe` |

三者**不可互相替代**。回滚应用不会自动降级数据库。

### 不许做的事

- 把 Chromium 布局 fixture 写成"正式安装版验收通过"。
- 把 `/health/ready/` 200 写成"聊天业务冒烟通过"。
- 把 CI 四个 job 全绿写成"强制合并门禁已生效"（主分支 `required_status_checks` 至今未配置）。
- 改写历史报告里已经写下的旧次数（404 / 36 / 24 属于 CI run `37630595919`，**不要改这些历史结论**）。

### 测试数字的唯一口径（重要）

历史上测试数字会变（例如修好 `test_core.py` 的重复类后，文件里的 `def test_` 从 100 降到 80，但收集数不变）。规则是：

| 场景 | 做法 |
| --- | --- |
| 历史报告/旧 Issue 里已经写下的 404 | **不改**，追加一句"截至 YYYY-MM-DD，现行口径见 docs/handoff/testing-quality-ci.md" |
| 现行口径（本目录 `testing-quality-ci.md` 的"本次实测"表） | **全量替换成新数字**，并写明测量命令与日期 |
| 其他 7 处引用 | 改为"见 testing-quality-ci.md"，不再各自写死数字 |

**唯一权威位置**：[testing-quality-ci.md](testing-quality-ci.md) 的"本次实测"。任何其他文档不要重复写具体条数。

### 更新哪些文件

| 动作 | 必须同步 |
| --- | --- |
| 关闭/推进某个 P4 任务 | `docs/phase4/tasks.md` 对应行 + Issue 评论 |
| 影响某条 NEW-* 需求 | `docs/phase4/traceability-matrix.md` 对应行 |
| 改变整体交付状态 | `docs/current-status.md` |
| 新增文档 | `docs/documentation-status.md` 登记一行 |
| 改接口/模型/协议/存储 | 对应现行专题文档（API、数据库、protocol、technical-design） |

历史报告只**追加带日期的后续结论**，不覆盖原结果。

### 拿 Issue 的验收标准（每张卡都要做）

所有开放的 Phase4 Issue 用同一套模板，正文含 `## 验收标准` 段落。**不要靠转述**——直接拉原文：

```powershell
# 标题 + 正文（PowerShell 里用逗号形式；带 \n 的 jq 表达式会被 shell 吃掉反斜杠）
gh issue view 146 --repo HeanX/iChat_Pro --json title,body --jq '.title, .body'

# 只取验收段
gh issue view 146 --repo HeanX/iChat_Pro --json body --jq '.body' |
  Select-String -Pattern '## 验收标准' -Context 0,8

# 纯 PowerShell 版（不依赖 jq）
(gh issue view 146 --repo HeanX/iChat_Pro --json title,body | ConvertFrom-Json).body
```

除了每张卡自己的业务验收项，模板还**固定**带这 3 条（少写一条就不算完成）：

- [ ] 相关代码或配置经过非作者成员评审
- [ ] 适用的自动化或人工测试已执行并保存证据
- [ ] 需求、部署或测试文档已同步更新

因此"代码写完"离开"验收通过"还差：独立评审（本文 §1）+ 真实证据 + 文档同步。

GitHub 上**没有** P0/P1/P2 标签（实测 `gh issue list --label P0` 返回空集）。优先级只在 Issue 正文的"任务信息"里，以及 [docs/phase4/tasks.md](../phase4/tasks.md) 的表格里。

---

## 4. 环境与命令速查

### 先看这一条：用哪个 Python

**本机唯一可用的解释器是仓库内的 venv**：

```powershell
.\.venv\Scripts\python.exe --version    # 期望 Python 3.13.12
.\.venv\Scripts\python.exe -c "import django; print(django.get_version())"   # 期望 6.0.5
```

PATH 上裸 `python` 是 **3.13.1 + Django 2.2.4**，跑 `manage.py check` 会直接 `ModuleNotFoundError: No module named 'cgi'`（Django 2.2 不兼容 3.13）。

**因此本文与 8 份模块文档里的所有 Django 命令都写 `.\.venv\Scripts\python.exe`，不要用裸 `python`。** 想用短命令就先激活：

```powershell
.\.venv\Scripts\Activate.ps1
```

### 从零搭建（新机器）

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

`settings.py` **不会**自动读 `.env`；必须显式设置进程环境变量（`$env:DJANGO_DEBUG='True'`）。本地无最小环境变量集的要求——不设也能跑（默认 `DEBUG=True`、SQLite、InMemoryChannelLayer）。本地无 `DATABASE_URL`/`REDIS_URL` 时用 SQLite + InMemoryChannelLayer——**不能**据此证明 PG 并发或 Redis 跨进程语义。

### 必跑的检查

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test --parallel 1      # 全量约 150–170s
npm run test:e2ee                                           # 基线 cfe53be 上 11 个文件，约 12s
```

> 具体条数不在这里写死（按 §3，唯一权威是 [testing-quality-ci.md](testing-quality-ci.md) 的"本次实测"）。
>
> **`test:e2ee` 的文件数会随分支变**：基线 `cfe53be` 上是 **11 个**（4 个自写断言脚本 + 7 个 `node --test` 文件）；`bfe6076` 起新增了 `key_change_reminder.test.js`，变成 12 个。所以你看到的数字和这一行不一样时，**先 `git rev-parse --short HEAD` 确认自己在哪个提交上**，不要急着改文档。

桌面改动另外（**仅 Windows，且必须 `cd desktop`**）：

```powershell
cd desktop
npm run test:layout      # 真 Chromium 布局
npm run test:branding    # 真 electron-builder + PE 资源
npm run test:installer   # 真 NSIS；缺 makensis 直接失败，不跳过
```

根目录跑 `npm run test:layout` 会报 `Missing script`——这三个脚本只存在于 `desktop/package.json`。

**NSIS 来源**（`test:installer` 按此**顺序**找，见 `desktop/scripts/test-installer.cjs:22-25`）：`MAKENSIS` 环境变量 → `%LOCALAPPDATA%\electron-builder\Cache\nsis\nsis-3.0.4.1\makensis.exe` → `Program Files (x86)\NSIS\makensis.exe` → `Program Files\NSIS\makensis.exe`。CI 用 `choco install nsis`（`.github/workflows/django.yml:22`）。都没装就会失败——这是预期行为，不要改成跳过。

### PostgreSQL 并发验证

多处要求"并发改动必须用真实 PostgreSQL 复跑"，但**本机目前没有 PG**（5432 未监听、无 docker、仓库无 compose）。可选做法：

```powershell
# 有 PG 时
$env:DATABASE_URL='postgres://ichat:ichat-ci@127.0.0.1:5432/ichat'
$env:REDIS_URL='redis://127.0.0.1:6379/0'
.\.venv\Scripts\python.exe manage.py test --parallel 1
```

没有 PG 时：SQLite 会**静默 skip** 掉 `@skipUnless(connection.vendor=="postgresql")` 的并发用例。**必须在交付里写明"并发用例未执行（无 PG）"，不得写成"全部通过"。**

### 本次环境实测值

Python 3.13.12 / Django 6.0.5 / Node v24.14.1 / npm 11.13.0 / SQLite。Linux 生产为 Python 3.13 + PostgreSQL 16 + Redis 6；CI 为 Redis 7。

具体测试条数只写在 [testing-quality-ci.md](testing-quality-ci.md) 的"本次实测"表里，本文不重复。

---

## 5. 模块间协调

| 公共位置 | 谁负责整合 | 其他模块怎么做 |
| --- | --- | --- |
| `chat/services/*`、`chat/consumers.py`、消息视图 | 消息模块 | 只提契约需求，不并行重构 |
| `accounts/models.py`、`chat/models.py`、迁移编号 | 单一迁移负责人 | 先提交模型设计，确认后串行生成 |
| `chat/views.py`、`chat/urls.py`、`accounts/urls.py` | 消息/账号各自整合 | 健康接口只加独立小模块 + 最小路由改动 |
| `settings.py`、`requirements.txt` | 运维模块 | 提需求，由运维统一合并 |
| 根 `package.json` / lock | 指定一人 | desktop 用自身目录的依赖 |
| 模板公共脚本区、CSS、`chat.js` | 前端模块 | 提前说明改动区域，避免同时大改 |
| `.github/workflows` | 测试/CI 模块 | 交命令、结果格式、阈值 |
| 任务状态与证据 | 文档模块 | 开发侧交实际结果 |

**共享文件最终合并必须串行。** 没有契约时，先做不相干的骨架/测试，别用假接口凑验收。

---

## 6. 安全红线

不得为了"跑通"而：

- 关闭 Origin 校验、`ALLOWED_HOSTS` 校验、CSRF。
- 明文存私钥/Token/聊天内容；把服务端密钥打进安装包。
- 公开整个 `media/`（只允许 `media/avatars/` 公开；`media/uploads/` 只能经认证的 `/api/files/` 读取）。**不要**在 nginx 新增 `location /media/` 或 `alias`，**不要**为了调试打开 `DJANGO_DEBUG`——生产的安全性依赖 `DEBUG=False` 时 Django 不给 media 注册路由（实测 `ichat_pro/urls.py:32-33`：`if settings.DEBUG:` 才 `static(settings.MEDIA_URL, ...)`）。nginx 配置文件的真实路径是 **`deploy/nginx/ichat.conf`**（仓库根下没有 `nginx/` 目录）。细节见 [cloud-deploy-ops.md](cloud-deploy-ops.md) §8 坑 6。
- 在日志中记录 Cookie、密文载荷、完整请求体。
- 向外部 AI 提交生产密码、私钥、用户隐私、E2EE 明文。

前端 CSP 注意（**本地验证不出这个问题，最容易踩**）：

- `script-src` 默认**不含** `'unsafe-inline'`（`CSP_ALLOW_UNSAFE_INLINE_SCRIPT` 缺省 `'false'`，位置在 `ichat_pro/settings.py:252`）。注意 `..._STYLE` 缺省是 `'true'`（`:258`），**两者不是同一个开关**（本地 Tailwind 内联样式要靠它）。
- **`DEBUG=True` 时 CSP 不拦截，只上报**：`ichat_pro/csp_middleware.py:52-56` 在 `settings.DEBUG` 为真时把 `Content-Security-Policy` 响应头**删掉**，改成 `Content-Security-Policy-Report-Only`。也就是说：本地默认配置下你写的内联 `onclick=` **照样能点**，只在浏览器控制台报违规、不阻断。只有 `DEBUG=False`（生产）才真正拦掉。
  → **结论：不要在本地"点得动"就断定 CSP 没问题。** 涉及内联事件处理器的改动，必须在 `DEBUG=False` 下自查（或至少去看控制台的 CSP 违规日志）。
- 迁移方向是用 `data-ichat-on*` + `static/js/event-dispatcher.js` 做事件委派。现状（实测 2026-10-18）：
  - **`onclick` 在模板里已迁完**：`templates/**/*.html`（40 个文件）裸 `onclick=` **0 处**；`data-ichat-onclick=` **193 处**，`data-ichat-on*` 合计 219 处（click 193 / input 16 / change 6 / submit 4）。**不要**把 193 当成"待迁移量"。
  - **`static/js/chat.js` 仍有 20 处裸 `onclick=`**，全部在模板字符串拼 `innerHTML` 时写入，属**未迁移**；另有 1 处 `[data-ichat-onclick=...]` 选择器不是 handler（所以 `Select-String 'onclick='` 会数出 21，口径差 1）。这正是 [issue-backlog.md](issue-backlog.md) 记的"`chat.js` 里约 20 处动态 `innerHTML` 的 `onclick=` 在生产 CSP 下失效"。
  - **仍未覆盖的事件类型**：模板里还有 **2 处裸 `onfocus=`**（`templates/components/sidebar.html`、`templates/components/standalone_contacts.html` 各 1）。`event-dispatcher.js` 的 `EVENT_MAP` **只注册了 click / input / change / submit 四个通道，没有 focus**，所以这两处**无法**用 `data-ichat-on*` 迁移，`DEBUG=False` 下同样会被 CSP 拦。改动涉及这些元素时按已知缺陷处理。
- 改到这些区域时：要么顺手改成委派（可能超出你那张卡的范围，先问），要么**明确记进"未验证/已知缺陷"**，不要当成"我的改动弄坏了"。

---

## 7. 交付模板（每个工作单元结束时填）

```text
模块 / 任务号：
关联 NEW-* 需求：
本轮完成的 P0/P1/P2：
关键改动及文件：
新增/变更接口、字段、错误码、配置变量：
迁移、兼容与回滚方式：
验证环境、命令、实际结果：
独立审查：审查者 session、结论、发现项与修复轮次
证据文件或流水线链接：
尚未执行/仍失败的验收项：
遗留风险与缺陷编号：
需要其他模块提供的具体契约/资源：
```

---

## 8. 本文自身的核对记录

本节存在的理由：本文其余部分都在要求别人"每条断言都要可复跑"，那本文自己也得交代哪些话是真跑出来的。

**环境**：Windows；`.\.venv\Scripts\python.exe` = Python 3.13.12 / Django 6.0.5；Node v24.14.1 / npm 11.13.0；本地 SQLite + InMemoryChannelLayer。**没有** PostgreSQL（5432 未监听、无 docker、仓库无 compose）与 Redis。

**核对时点与一个必须说明的失误**：本文声称的基线是 `cfe53be`。但核对过程中这个工作区**被其他 Agent 切走过**（`test/p4-test-t07-ciphertext-boundary-audit` @ `bfe6076`），我第一次测 `test:e2ee` 时测的是**漂移后的 HEAD**，却把结果署成了 `cfe53be` 的值，并据此"修正"了原本正确的旧数字。这个问题由独立审查者当场发现，已按基线实测值回退并加注说明。**下表凡是会随提交变化的项，都标明了测在哪个提交上**；其余命令请在 `cfe53be` 上复跑。

### 实跑过的命令与结果

| 断言（本文哪一节） | 复跑命令 | 实测结果 |
| --- | --- | --- |
| §0/§2 `main` 与 `origin/main` 同步 | `git rev-list --count origin/main..main` | `0`（两者都是 `4703ff3`） |
| §2 基线分支领先 3 个提交 | `git rev-list --count origin/main..HEAD`（在基线分支上） | `3` |
| §4 用对解释器 | `.\.venv\Scripts\python.exe --version` | `Python 3.13.12` |
| §4 Django 版本 | `.\.venv\Scripts\python.exe -c "import django; print(django.get_version())"` | `6.0.5` |
| §4 裸 `python` 会崩 | `python --version` / `python manage.py check` | `3.13.1` / `2.2.4` / `ModuleNotFoundError: No module named 'cgi'` exit=1 |
| §4 必跑检查 | `.\.venv\Scripts\python.exe manage.py check` | `System check identified no issues (0 silenced).` exit=0 |
| §4 迁移一致性 | `.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run` | `No changes detected` exit=0 |
| §4 全量 Django 测试（`cfe53be`） | `.\.venv\Scripts\python.exe manage.py test --parallel 1` | `Found 404 test(s)` / `Ran 404 tests in 168.329s` / `OK (skipped=2)` |
| §4 全量 Django 测试（`bfe6076`，对照） | 同上 | `Found 418 test(s)` / `Ran 418 tests in 164.919s` / `OK (skipped=2)` |
| §4 `test:e2ee` 文件数与条数（**`cfe53be`，本文基线值**） | `npm run test:e2ee` | **11 个 `.js`**（4 自写 + 7 `node --test`）；`tests 62 / pass 62 / fail 0`；约 **11.4s** |
| §4 同上（`bfe6076`，对照） | 同上 | 12 个 `.js`（多出 `key_change_reminder.test.js`）；`tests 70 / pass 70`；约 11.6s |
| §4 `desktop` 脚本只在 desktop | 两份 `package.json` 的 `scripts`；根目录跑 `npm run test:layout` | 根只有 `build:css`/`watch:css`/`test:e2ee`；`npm run test:layout` 报 `Missing script`；三个脚本只在 `desktop/package.json` |
| §4 NSIS 来源顺序 | `desktop/scripts/test-installer.cjs:22-25` | `MAKENSIS` → `%LOCALAPPDATA%\…\nsis-3.0.4.1` → `Program Files (x86)\NSIS` → `Program Files\NSIS`；找不到直接断言失败，不跳过 |
| §4 无 PG 时并发用例静默 skip | 同上全量结果 | `skipped=2`；全仓 `@skipUnless(vendor=="postgresql")` 共 **2 处**（`test_messaging_service.py:363`、`test_t32_review.py:107`） |
| §4 无 PG / 无 docker / 无 compose | `Test-NetConnection 127.0.0.1:5432`、`:6379`；`Get-Command docker`；递归找 `docker-compose*` | `False` / `False`；不存在；0 个 |
| §4 `settings.py` 不自动读 `.env` | 在 `settings.py` 里检索 `load_dotenv` / `dotenv` | 0 命中，只有 `os.environ.get` 直读 |
| §4 不设环境变量也能跑 | 见上面 check / test 结果 | `DEBUG` 默认 `True`（`DJANGO_DEBUG` 缺省 `'True'`）；无 `DATABASE_URL`/`REDIS_URL` 时回落到 SQLite + `InMemoryChannelLayer`（`settings.py:161-187`） |
| §3 `gh issue view` 三种写法都可用 | 对 #146 分别跑 jq 逗号式、`Select-String` 式、`ConvertFrom-Json` 式 | 三种都取到正文；验收段共 4 条（3 条模板固定项 + 1 条业务项） |
| §3 抽检模板一致性 | 另抽 #130 / #139 / #205 | 同为 4 条 `- [ ]`，与 #146 一致。**其余 45 项未逐条核对** |
| §3 GitHub 没有 P0/P1/P2 标签 | `gh label list`；`gh issue list --label P0` | 无 P0/P1/P2 标签；查询返回空集 |
| §3 开放 Issue 数 | `gh issue list --state open --limit 300 --json number` | `49`（带 `phase-4` 标签同为 49）；未合并 PR `0` |
| §3 主分支门禁未配置 | `gh api repos/…/branches/main/protection` | 顶层响应**不含** `required_status_checks` / `required_pull_request_reviews` 两个 key ⇒ 两者均未生效。见"未验证范围"里关于子端点证据冲突的说明 |
| §3 历史数字 404/36/24 的归属 | `gh run view 37630595919` | 结论 success；日志含 `Ran 404 tests`、`Sidebar layout: 36/36`、`NSIS runtime: 24/24` |
| §6 media 路由依赖 `DEBUG` | `ichat_pro/urls.py:32-33` | `if settings.DEBUG:` 才 `static(settings.MEDIA_URL, ...)` |
| §6 CSP `script-src` 默认不含 `unsafe-inline` | `ichat_pro/settings.py:252`、`:258` | `..._SCRIPT` 默认 `'false'`；`..._STYLE` 默认 `'true'`（两者不同） |
| §6 `DEBUG=True` 时 CSP 只上报不拦截 | `ichat_pro/csp_middleware.py:52-56` + 实测响应头 | `DEBUG=True`：`Content-Security-Policy` 被删、只剩 `Content-Security-Policy-Report-Only`；`DEBUG=False` 才发强制头 |
| §6 内联 handler 的真实分布 | 统计 `onclick=`（区分裸 handler 与 `data-ichat-onclick=`）| 模板 40 个文件：裸 **0** 处、`data-ichat-onclick=` **193** 处、`data-ichat-on*` 合计 219；`chat.js`：裸 **20** 处 + 1 处选择器（子串口径会数成 21） |
| §1 `.review/` 需自建 | `Test-Path .review` + 按文档原样重定向 | 基线提交里**不存在**；目录缺失时重定向报 `DirectoryNotFoundException`，文件不会生成 |
| §1 `.review/` 已被忽略 | `git check-ignore -v .review/pending.diff` | 命中 `.gitignore:47:.review/`；该规则在 `cfe53be` 上**不存在**（未提交） |
| §3 安装包 SHA | `Get-FileHash desktop\dist\iChat-Pro-Setup-1.0.0.exe` | `AC468DF3…D299D5F9`，与文中的 `ac468df3…d5f9` 一致 |
| §1 两点 diff 会混入他人提交 | `git diff --shortstat origin/main..<该 HEAD>` | `17 files, 1477 insertions`（该次 HEAD 是 `bfe6076`；同一比较在 `cfe53be` 上是 12 files / 969 insertions） |

### 明确未验证的范围

下列内容本文只能转述，**本次没有也无法在本地复验**，不要当作已证实：

- 生产部署 SHA `f2f60ec` 的**运行时事实**、AWS 区域/机型/EIP、共存服务占用 443/2096/25383、证书自动续期钩子——只核实了 `deploy/runbook.md` 里确有这些文字、`f2f60ec` 确是 CI run `37630595919` 的 headSha；运行时状态无生产访问权。
- `desktop` 的 `test:layout` / `test:branding` / `test:installer` 三个脚本**本次未执行**，只核对了脚本存在与 NSIS 解析顺序。`testing-quality-ci.md` 里的 36 / 24 是从 CI 日志核实的，**不是本地复跑**。
- 任何 Android 构建。
- 真实 PostgreSQL / Redis 下的并发语义（本机两者都没有）。
- "所有 49 个开放 Issue 都用同一套模板"——只抽检了 4 项（#146/#130/#139/#205），其余 45 项**未验证**。
- **§8 上表里 `cfe53be` 的 `62 项` 与 `bfe6076` 的 `70 项`**：这两个数字是实跑得到的（`npm run test:e2ee`），但 `Ran 404 tests`（`cfe53be`）与 `Ran 418 tests`（`bfe6076`）是**多次运行中记录的**，机器抖动会导致条数以外的耗时不稳；以条数为准，耗时不作承诺。
- 主分支 protection 的**其余**字段（`enforce_admins` / `required_signatures` 等）未逐一核对。另有一处**证据冲突未解决**：顶层响应无 `required_pull_request_reviews` key、GraphQL 报 `requiresApprovingReviews=false`，但子端点 `/protection/required_pull_request_reviews` 却返回 200 且 `required_approving_review_count=1`。`required_status_checks` 侧三处证据一致（顶层无 key、子端点 404、GraphQL false）⇒ 可以判定未配置；**PR review 的实态记为未验证**，不要据此断言"门禁已配/未配"中的任何一方。
- §1 引用的 `.gitignore:47` **是工作区未提交改动的行号**。一旦该改动被提交或工作区被切换，行号会变（实测：HEAD 漂移后该行一度消失，`git check-ignore` 返回非 0）。行的**内容**（`.review/`）是稳定的，定位请按内容检索，不要死记行号。
- §8 里 `test:e2ee` 的耗时（约 11.4s / 11.6s）与 [testing-quality-ci.md](testing-quality-ci.md) 记的"约 40s"不一致，两者测的环境/时机不同，**耗时不要当作验收口径**，只认条数。

### 本记录对应的改动

本轮只改本文，不动任何代码、配置、迁移与测试。修正了以下**失效断言与内部矛盾**：

1. §2 原文称"`main` 领先 `origin/main` 3 个未推送提交"——实测为 0，已更正，并补"共享工作区"提醒。
2. §4 原文在正文写死后端测试的具体条数——与 §3"唯一权威是 testing-quality-ci.md"自相矛盾，已改为指向该节。
3. §1 原文要求 `git add -A`，§2 原文又禁止 `git add -A`——同一文档自相矛盾，统一为显式 `git add <文件>`。
4. §1 的 `.review/` 流程在基线提交上会直接失败（目录不存在），已补 `New-Item -ItemType Directory -Force .review` 前置步骤。
5. §1 的 `git diff origin/main..HEAD` 在共享分支上会混入他人提交，已补充核对范围的做法与实测例子。
6. §6 的 nginx 配置路径写成 `nginx/ichat.conf`——真实路径是 `deploy/nginx/ichat.conf`，已更正。
7. §6 原称内联 `onclick=` "会被静默拦掉"——本地 `DEBUG=True` 下**不会拦**（只上报），已按实际行为更正，这是最容易让接手者"本地通过、上线炸"的一条。
8. §6 原称内联 handler "已迁移"——实测模板已迁完（裸 `onclick=` 为 0），缺口只在 `chat.js` 的 20 处动态拼接，已按实际情况重写（**第一版曾把模板的 193 处 `data-ichat-onclick` 误当成"未迁移的 193 处 `onclick=`"**，由第 2 轮审查者指出并更正）。
9. §0 原称"本文不含 Issue 号"与 §3 的 `146` 示例自相矛盾，已改为"不对应任何任务卡"。
10. §4 的 NSIS 解析顺序与代码不一致（`Program Files` / `Program Files (x86)` 次序相反），已按 `test-installer.cjs:22-25` 更正。

> **`test:e2ee` 的"11 vs 12"说明**：这不是文档错误。基线 `cfe53be` 是 11 个文件 / 62 项；`bfe6076` 起加了 `key_change_reminder.test.js` 变 12 个 / 70 项。本文按 §3 只写基线值，并把对照值留在上表——**写死任何一个都会让另一批人以为文档错了**。


