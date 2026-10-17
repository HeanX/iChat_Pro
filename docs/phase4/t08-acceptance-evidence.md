# #189 P4 Test T08 验收证据（本机实测）

> 采集时间：2026-10-18（Asia/Shanghai）
> 分支：`fix/p4-test-t08-key-reminder-189`；数字采集于本卡最后一个提交（基线 `cfe53be` 之上第 7 个）。
> 复验时请先跑 `git log --oneline cfe53be..HEAD` 确认实际 HEAD；本节数字随代码演进会变，以当次输出为准。刻意不写死 commit SHA——本卡经多轮修订，写死反而容易过期。
> 环境：Windows；Python 3.13.12 / Django 6.0.5；Node v24.14.1 / npm 11.13.0；SQLite
> 说明：**CI 未运行**（该分支未推送、无 upstream、无 PR）。以下均为**本机**实测，不代表 CI 或跨端验收。

## 0. 如何复现（重要）

本卡在**独立 git worktree** 中开发（`git worktree add`），该目录**没有自己的 `.venv`**，
所以解释器要借用主仓库的虚拟环境。

**真正的约束是「当前工作目录必须在 worktree 内」。** 若 cwd 落在主仓库，
Python 会把 `accounts` 解析到主仓库那一份（主仓库当前在别的分支、没有本卡改动），
报 `ImportError: 'tests' module incorrectly imported from ...`。
`manage.py` 用相对路径或绝对路径都可以，cwd 对了就不会串。

```powershell
# 解释器：主仓库 venv 的绝对路径
$py = 'D:\Code\PythonProjects\PythonWeb\DjangoProject\iChat_Pro\.venv\Scripts\python.exe'

# 工作目录：本 worktree（必须）
Set-Location 'D:\Code\PythonProjects\PythonWeb\DjangoProject\iChat_Pro_wt_t08'

& $py manage.py check
& $py manage.py test accounts --parallel 1
npm run test:e2ee
```

> **不要在只用主仓库复现本节数字**：主仓库当前分支不含本卡改动，
> `manage.py test accounts` 只有 127 条，且 `accounts.test_key_change_reminder` 会
> `ModuleNotFoundError`。本节所有数字都来自上面的 worktree。

> 下方命令块为简洁起见仍写作 `.\.venv\Scripts\python.exe`，**执行时请按 §0 替换**。

PowerShell 中 Django 把进度写到 stderr，Django 命令的 `$LASTEXITCODE` 可能为 1；
判断是否通过请看尾部 `OK` / `FAILED` / `Ran N tests`，不要只看退出码。

## 1. 构建与迁移

```text
$ .\.venv\Scripts\python.exe manage.py check
System check identified no issues (0 silenced).

$ .\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
No changes detected
```

本卡**未新增/修改任何模型或迁移**（`accounts/migrations/` 无变化）。

## 2. 后端测试

```text
$ .\.venv\Scripts\python.exe manage.py test accounts --parallel 1
Ran 142 tests in 36.602s
OK

$ .\.venv\Scripts\python.exe manage.py test accounts.test_key_change_reminder
Ran 15 tests in 5.665s
OK

$ .\.venv\Scripts\python.exe manage.py test chat.tests.test_phase2_issues
Ran 47 tests in 22.433s
OK

$ .\.venv\Scripts\python.exe manage.py test --parallel 1
Ran 419 tests in 151.082s
OK (skipped=2)
```

`accounts` 由 127 条增至 142 条（本卡新增 15 条，见 `accounts/test_key_change_reminder.py`）。

**2 条 skip 明细**（本机无 PostgreSQL，属预期）：

```text
test_concurrent_same_id_creates_one_message (chat.tests.test_messaging_service.ConcurrentSameIdTests)
  ... skipped 'requires PostgreSQL concurrency'
test_concurrent_conflicting_id_across_conversations (chat.tests.test_t32_review.ConcurrentConflictAcrossConversationsTests)
  ... skipped 'requires PostgreSQL concurrency'
```

→ 这两条并发用例**未执行**，不能视为通过。

## 3. 前端测试

```text
$ npm run test:e2ee
tests 75 / pass 75 / fail 0

$ node --check static/js/chat.js
exit=0
```

`test:e2ee` 由基线 62 条增至 75 条（本卡新增 `chat/tests/js/key_change_reminder.test.js` 13 条，
已在基线 worktree 上用同一命令核对：`cfe53be` → tests 62 / pass 62）。

## 4. 测试隔离（不得写脏仓库）

```text
$ git status --short -- media/
（无输出）
```

## 5. 本卡验收项对照

| 验收项 | 状态 | 证据 |
| --- | --- | --- |
| 新密钥不自动继承旧信任状态 | 通过（本机实测） | §2 三条 `test_rotated_key_is_untrusted_and_old_record_is_kept` / `test_trust_list_after_rotation_reports_current_key` / `test_previous_and_last_endorsed_diverge_after_consecutive_rotations` |
| 相关代码或配置经过非作者成员评审 | **未满足**：已做 3 轮独立子 Agent 审查（每轮新上下文），但审查者是 **AI 子 Agent，不是人类成员**，且分支未推送、无 PR 评审记录 | 见 §6 |
| 适用的自动化或人工测试已执行并保存证据 | 自动化：已执行（§1–§3）；**人工 UI 验收：未执行** | 本文件 |
| 需求、部署或测试文档已同步更新 | **部分**：已更新本分支内的 `docs/phase4/tasks.md`、`docs/phase4/traceability-matrix.md`、`docs/phase4/t08-acceptance-evidence.md`。`docs/handoff/accounts-auth.md` **不在本分支**（该目录属另一分支的未跟踪内容），相关结论只写在本分支外，合并时需单独落地 | `git diff cfe53be..HEAD --stat` |

## 6. 独立审查记录

| 轮次 | 审查者 | 结论 | 发现项 | 处理 |
| --- | --- | --- | --- | --- |
| 1 | 独立子 Agent（新上下文，仅给 diff + Issue 原文 + 命令） | PASS WITH FINDINGS | 3 MAJOR + 若干 MINOR | 全部修复或说明 |
| 2 | 独立子 Agent（新上下文，复审修复） | PASS WITH FINDINGS | 确认 3 条 MAJOR 已修复；新增 2 MAJOR（其中 1 条为我引入的 DELETE 契约回归）+ 5 MINOR | 已回滚 DELETE 改动，其余修复 |
| 3 | 独立子 Agent（新上下文，末轮复审） | PASS WITH FINDINGS | 前两轮 9 项确认修复；M1 证据命令不可复现、M2 handoff 文档不在分支、M4 API 文档缺新字段 | 已修 M1/M2/M4，并补 DELETE 正路用例 |

**未验证范围**（不得当作通过）：

- UI 人工验收：未启动浏览器核对右栏/指纹弹窗/设置页的真实渲染与交互；
- 跨端：未在 Windows 安装版、Electron、Android 上验证；
- 真实依赖：未在 PostgreSQL / Redis 下复跑，2 条并发用例 skipped；
- CI：分支未推送，无 CI run 记录；
- 非作者**人类**评审：仓库内无第二位提交者，无法自证。
