# 派工 Prompt 模板（复制即用）

> 配套 [conventions.md](conventions.md)。本文件提供三层模板：**最短版**（单卡快速派工）、**完整版**（正式交接）、**审查版**（独立审查子 Agent 专用）。
> 编写日期 2026-10-18；基线 `cfe53be`（分支 `docs/handoff-guide-and-test-media-isolation`，**不在 main 上**）。

---

## 一、最短版（推荐日常用）

把 `{{}}` 全部替换掉，整段发给接手的 Agent。

```text
你在 iChat_Pro 仓库（D:\Code\PythonProjects\PythonWeb\DjangoProject\iChat_Pro）负责 {{模块名}}。

## 第一步：读文档（先读，别跳）
1. docs/handoff/conventions.md          —— 流程、证据规则、禁止事项
2. {{模块文档}}                          —— 本模块现状、契约、坑
3. docs/handoff/issue-backlog.md        —— 看你这张卡的依赖位置

## 第二步：拿验收标准（不要靠转述）
gh issue view {{Issue 号}} --repo HeanX/iChat_Pro --json title,body --jq '.title, .body'

（PowerShell 里不要用 `'"#\(.title)\n\(.body)"'` 这种带 `\n` 的写法——反斜杠会被 shell 吃掉，
报 `failed to parse jq expression`。用上面的逗号形式，输出是先标题后正文。）

除了业务验收项，模板固定还带 3 条，少一条都不算完成：
- [ ] 相关代码或配置经过非作者成员评审
- [ ] 适用的自动化或人工测试已执行并保存证据
- [ ] 需求、部署或测试文档已同步更新

## 第三步：确认基线
当前 HEAD 应是 cfe53be（分支 docs/handoff-guide-and-test-media-isolation）。
origin/main 是 4703ff3，比它少一个测试隔离修复（头像测试会往仓库 media/ 写文件）。
若你从别处 clone 的，先：git switch -c {{分支名}} cfe53be

## 第四步：实现
只做这张卡的范围。不可破坏的契约见 {{模块文档}} 的「关键契约」一节。

## 第五步：验证（必须真跑，贴原始输出）
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
{{模块测试命令}}

注意：PATH 上裸 python 是 3.13.1 + Django 2.2.4，跑 manage.py 会崩。
一律用 .\.venv\Scripts\python.exe。

## 第六步：开独立审查子 Agent（强制）
写完代码后，自己开一个新的子 Agent，只给它：
- 仓库路径 + 分支 + Issue 号
- gh issue view 的原始输出（不是你的转述）
- .review/pending.diff 的路径
- 上面的验证命令清单
禁止它读你的实现说明。按 conventions.md §1 的 7 项清单逐条给结论，
每个问题带 文件:行 / 触发条件 / 实际 / 预期 / BLOCKER|MAJOR|MINOR。
不通过就修，修完**再开一个新的**（不复用）。连续 3 轮不通过就停下来问我。

## 第七步：交付
按 conventions.md §7 的模板回复，必须包含：
- 改动文件清单
- 实际执行的命令 + 原始结果（没跑就说没跑）
- 独立审查结论与修复轮次
- 未验证的范围（明确写"未验证"，不要写"通过"）

禁止：编造测试结果、把 fixture 当原生验收、为了让流水线变绿而跳过测试、
改历史文档里已写下的旧结论、用新的测试条数替换 CI run 37630595919 的记录。
```

---

## 二、完整版（正式交接 / 需要留档）

```text
# 任务
仓库：D:\Code\PythonProjects\PythonWeb\DjangoProject\iChat_Pro
模块：{{模块名}}
Issue：{{#编号}} {{标题}}
优先级：{{P0/P1/P2}}
预计边界：{{要改的目录/文件}}

# 背景
iChat Pro 是 Django 6 + Channels + 原生 JS + Electron 的加密即时通信项目。
Phase4 原始 78 项任务中 29 项已闭环、49 项开放。
项目级现状见 docs/current-status.md，任务状态见 docs/phase4/tasks.md。

# 必读（按顺序）
1. docs/handoff/conventions.md            流程与证据规则（强制）
2. {{模块文档}}                            本模块现状
3. docs/phase4/protocol.md                （消息/前端/Android 模块必读）
4. gh issue view {{编号}}                   验收标准原文

# 不可破坏的契约
{{从模块文档「关键契约」一节复制 3-5 条最关键的}}

# 环境
- Python：.\.venv\Scripts\python.exe（3.13.12 / Django 6.0.5）
- Node：v24.14.1；npm 11.13.0
- 数据库：本地 SQLite；PostgreSQL 并发用例会静默 skip（无 PG 必须声明）
- 基线：cfe53be（分支 docs/handoff-guide-and-test-media-isolation）
  注意 origin/main=4703ff3，不含测试隔离修复 78e7f54

# 分支
git switch -c {{feature|fix|test|docs}}/{{p4-txx-短名}} cfe53be

# 实现要求
- 只做本卡范围，不顺手重构无关代码
- 新增接口/模型/协议字段先给契约说明，再实现
- 迁移编号需与现有 0021 之后串行，不得重编号或删除已上线迁移
- 不提交 .env / 数据库 / media / 私钥 / node_modules / dist 安装包

# 验证（逐条真跑）
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test --parallel 1
npm run test:e2ee
{{桌面/其他专项命令}}

# 独立审查（强制）
见 conventions.md §1。写完开新子 Agent，只给 diff + Issue 原文 + 命令。
结论三态：PASS / PASS WITH FINDINGS / FAIL；问题分级 BLOCKER|MAJOR|MINOR。
3 轮不通过停下来问人。开不出来必须写"未执行独立审查"。

# 交付格式
工作包与任务号：
关联 NEW-* 需求：
本轮完成：
关键改动及文件：
新增/变更接口、字段、错误码、配置变量：
迁移、兼容与回滚方式：
验证环境、命令、实际结果：
独立审查：审查者 / 结论 / 发现项 / 修复轮次
证据文件或流水线链接：
尚未执行/仍失败的验收项：
遗留风险与缺陷编号：
需要其他模块提供的契约/资源：

# 反向要求（重要）
- 没跑过的检查写"未执行"，不要写"通过"
- 没有设备/服务器/Android 环境时，明写缺什么
- 不要修改 docs/phase4/tasks.md 的已完成行，除非有真实证据
- 不要替用户决定关闭 Issue；只提交证据，由用户确认
```

---

## 三、模块参数表（填 `{{模块名}}` / `{{模块文档}}` 时查这张）

| 模块名 | 模块文档 | 必读代码（最短路径） | 模块测试命令 |
| --- | --- | --- | --- |
| 账号与公钥信任 | `docs/handoff/accounts-auth.md` | `accounts/models.py` → `accounts/views.py` 认证与公钥段 → `ichat_pro/middleware.py` | `manage.py test accounts` |
| 消息与协议 | `docs/handoff/messaging-protocol.md` | `chat/errors.py` → `chat/services/messaging.py` 两个 `send_*` → `chat/services/sync.py` → `chat/consumers.py` | `manage.py test chat.tests.test_messaging_service chat.tests.test_t32_review chat.tests.test_sync_api chat.tests.test_protocol` |
| Web 前端与 E2EE | `docs/handoff/web-frontend-e2ee.md` | `static/js/chat-connection.js` 头部 → `chat-notifications.js` 全文 → `private-chat-e2ee.js` 常量与导出 | `npm run test:e2ee` |
| Windows 桌面 | `docs/handoff/windows-desktop.md` | `desktop/app-config.js` 全文 → `main.js` 启动序列与 IPC → `preload.js` | `cd desktop` 后分别跑 `npm run test:layout` / `test:branding` / `test:installer` |
| 云部署运维 | `docs/handoff/cloud-deploy-ops.md` | `deploy/deploy.sh` → `restore.sh` → `restore_drill.sh` → `settings.py` 配置段 | `manage.py check`（改配置时加 `check --deploy`） |
| 数据与存储 | `docs/handoff/data-storage-file.md` | `chat/models.py` 四个核心模型 → `cleanup_expired_uploads.py` → `nginx/ichat.conf` media 段 | `manage.py test chat.tests.test_integration chat.tests.test_t32_review` |
| 测试与 CI | `docs/handoff/testing-quality-ci.md` | `.github/workflows/django.yml` → `ichat_pro/test_runner.py` → 两份 `package.json` 的 scripts | 全量（见文档 §1） |
| Android | `docs/handoff/android-mobile.md` | `docs/phase4/protocol.md` 全文 → `static/js/chat-connection.js` → `private-chat-e2ee.js` 常量 | 无（从零建） |

> 表中所有 `manage.py` 都要写成 `.\.venv\Scripts\python.exe manage.py`——裸 `python` 是 Django 2.2.4，会崩。

---

## 四、审查子 Agent 专用 Prompt

**给审查者用的，不要把它和作者的说明混在一起发。**

```text
你是独立审查者，不参与实现。

仓库：D:\Code\PythonProjects\PythonWeb\DjangoProject\iChat_Pro
分支：{{分支名}}
Issue：{{#编号}}

## 验收标准（原文，未经过滤）
如果你能联网/有 gh，自己跑：
  gh issue view {{编号}} --repo HeanX/iChat_Pro --json body --jq '.body'
拿不到就使用下面这段（作者从 Issue 原文复制的，未经改写）：
{{粘贴 gh issue view 输出的「## 验收标准」整段}}

## 待审 diff
.review/pending.diff（自己读；如果不存在，先问作者要，不要猜）
另可自行：git diff origin/main..HEAD --stat

## 你必须自己执行的命令
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
{{模块测试命令}}

## 审查清单（逐条给结论，不许跳过）
1. 验收对齐：改动是否真的满足验收标准？有没有只做一半？
2. 权限与输入边界：新入参是否校验类型/范围/长度？换成别人的 id 会怎样？
3. 事务与幂等：写路径是否走统一服务层？重放会不会重复落库/计未读/广播？
4. 加密兼容：有没有破坏历史密文可读性？有没有明文落库或落日志？
5. 失败路径：网络错误、DB 锁、超时、部分成功时行为是什么？有没有吞异常？
6. 测试真实性：声称通过的测试是否真跑过？是否真的覆盖了新行为？
   有没有把 fixture/隔离环境当成原生验收？
7. 证据完整：命令、环境、SHA、结果、未验证范围是否如实记录？

## 输出格式
## 结论：PASS / PASS WITH FINDINGS / FAIL
## BLOCKER（必须修，否则不能合并）
| 文件:行 | 触发条件 | 实际 | 预期 |
## MAJOR（应该修）
## MINOR（可以后修）
## 我实际执行了什么（命令 + 原始结果）

规则：
- 你可以读代码、跑只读命令与测试；不得修改任何文件
- 不要相信 commit message 或代码注释里的自我评价，只信代码与测试
- 找不到证据的断言，判为"未验证"，不要判为通过
```

---

## 五、按场景选模板

| 场景 | 用哪个 | 额外注意 |
| --- | --- | --- |
| 让 Agent 认领一张普通任务卡 | 最短版 | 填好模块名与 Issue 号即可 |
| 任务涉及协议/模型/迁移 | 完整版 | 必须加"先给契约说明再实现" |
| **只要证据、不要代码**（如 #145） | 用下面的"证据版"，不要用最短版 | 最短版的第六步是"写完代码后审查"，纯证据任务没有代码可审，会卡住 |
| 生产运维类（#139/#176/#177/#204） | 完整版 | 加红线：不动 443/2096/25383，不加 `location /media/` |
| 让 Agent 审查别人的代码 | 第四节模板 | 绝不给作者说明 |
| 从零立项 Android | 完整版 | 加"先写 ADR，不改服务端协议" |

### 证据版（不改代码，只补验收证据）

```text
你在 iChat_Pro 仓库（D:\Code\PythonProjects\PythonWeb\DjangoProject\iChat_Pro）为 {{Issue 号}} 补充验收证据。

## 硬性约束：本任务禁止修改任何代码
只允许：截图、录屏、复制日志、写 Issue 评论、更新 docs/phase4/tasks.md 的证据列。
如果你发现代码有缺陷——**停下来报告**，不要顺手修。

## 第一步：读
1. docs/handoff/conventions.md §3（状态与证据规则）
2. {{模块文档}} 的「已知坑」一节
3. gh issue view {{Issue 号}} --repo HeanX/iChat_Pro --json body --jq '.body'

## 第二步：确认这一步到底缺什么
Issue 评论里通常已经写明"待人工验收 N 项"。逐条确认：
- 哪些已有证据（CI run、截图、日志）→ 直接引用，不要重做
- 哪些缺真实环境证据 → 只有这些才需要你动手

## 第三步：按验收项逐条取证
每一条必须给：操作步骤 → 实际现象 → 证据（截图路径或日志原文）→ 结论（通过/不通过/未验证）

禁止：
- 用 fixture / 隔离环境的结果冒充安装版或生产环境
- 用健康探针 200 冒充业务冒烟
- 把"代码已实现"写成"验收通过"
- 编造截图或日志

## 第四步：交付
- Issue 评论：逐条验收结果 + 证据链接
- 如果全部通过：在评论里写"所有验收项已闭环，建议关闭"，但**不要自己关 Issue**
- 如果有不通过项：写清楚复现步骤与期望行为，交给实现方

## 第五步：审查（轻量版）
证据类任务不需要代码审查，但**必须**让一个独立上下文核对：
"这些证据是否真的证明了 Issue 里写的验收标准？有没有把不相干的证据当成通过依据？"
把它的结论贴进 Issue 评论。
```

---

## 六、常见误用（会导致返工）

| 误用 | 后果 | 正确做法 |
| --- | --- | --- |
| 把「验收标准」用自己的话转述给 Agent | 它会朝着你的理解优化，漏掉 Issue 原文里的隐藏要求 | 直接给 `gh issue view` 输出 |
| 只发"实现 #146" | 它会自己猜范围，可能顺手重构 `main.js` | 用完整版，写清边界 |
| 忘了说基线是 `cfe53be` | 从 `origin/main` 开工，头像测试写脏仓库 | 明确 `git switch -c ... cfe53be` |
| 说"跑一下测试"但没给命令 | 它可能只跑 `manage.py test`，漏掉 `npm run test:e2ee` | 列全命令 |
| 让审查者在同一个 session 里"复查" | 共享上下文 → 只会复述作者想法 | 开全新会话，只给 diff + Issue |
| 允许"测试全绿即完成" | 用 Node 单测冒充桌面原生验收 | 明确写出未验证范围必须标注 |
