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
tests 118 / pass 118 / fail 0

$ node --check static/js/chat.js
exit=0
```

本卡最初由基线 62 条增至 75 条（新增 `chat/tests/js/key_change_reminder.test.js` 13 条，
已在基线 worktree 上用同一命令核对：`cfe53be` → tests 62 / pass 62）。

Phase4 修复轮（WEB-03 / WEB-04，分支 `codex/web-e2ee-trust-export`）又追加：

| 文件 | 条数 | 覆盖 |
| --- | --- | --- |
| `chat/tests/js/key_trust_decline.test.js` | 34 | 真实 `respondToKeyVerification` / `refreshKeyVerificationAfterChange` / `handleKeyVerificationNew` 的授权链（含自回声抑制） |
| `chat/tests/js/settings_key_export.test.js` | 9 | 从真实模板抽取并执行独立设置页的 `keyMgrExport()` |

口径说明（避免重复计数）：`npm run test:e2ee` = **118** 条，全部来自 `node --test` 阶段；
另有 4 个独立脚本（`private_chat_e2ee`、`group_chat_e2ee`、`chat_connection`、`desktop_config`）
在 `node --test` 之前用 `node <file>` 直接执行，只打印 `all tests passed`、**不产生计数**，
因此不计入 118。逐文件实测：`message_accepted` 12、`public_key_cache` 2、
`key_change_reminder` 13、`key_trust_decline` 34、`settings_key_export` 9、`desktop_branding` 1、
`desktop_tray` 15、`chat_notifications` 13、`desktop_notifications` 7、`notification_integration` 12
= 118。

> 另注：若用 `node --test 'chat/tests/js/*.test.js'` 一次性跑**全部** 14 个文件（含上述 4 个
> 独立脚本，每个算 1 条），总数是 122，与 `test:e2ee` 的 118 不是同一口径，不要混用。

> 数字随代码演进会变，以当次 `npm run test:e2ee` 输出为准；上表为 2026-10-26 实测。

## 3.1 客户端信任规则（WEB-03 / #189）

"新密钥不自动继承旧信任状态"在浏览器端由以下规则保证，实现位于 `static/js/chat.js`：

1. **私聊验证流程只有一条显式授予路径**：`refreshKeyVerificationAfterChange(userId, { grantTrustFor })`
   是私聊验证流程中唯一会调用 `window.iChatPrivateE2EE.trustPeerKey()` 的地方；不传
   `grantTrustFor` 时它只刷新展示，不写信任。
   > 本地信任库并非只有一个写入点，准确说法是"只有一条**显式授权**的验证路径"：
   > - 群聊轮换重试（`chat.js` 的 `iChatGroupE2EE.trustPeerKey`）写入同一命名空间，门禁是
   >   用户点过的 `window.confirm()`；
   > - `private-chat-e2ee.js` 的 `rememberPeerIdentity()` 在**首次**给某人发消息且本地无记录时
   >   会做 TOFU 记录（无交互、无提示）。它只记录**当前**密钥，不会让新密钥继承旧信任
   >   （旧版本被覆盖时会抛 `peer_key_changed`），因此不违背 #189 的验收原文。
2. **授权对象取自本地快照，且是唯一来源**：
   - `respondToKeyVerification()` 以 `pendingKeyVerificationRequests` 中用户实际点击的那条
     请求为准，`verificationConfirmation()` 只用回包核对 `id` / `requester_id` / `responder_id`；
     回包指向其它联系人、其它版本或其它 `id` 时**不授予任何人信任**并提示用户。
   - WebSocket `accepted` 事件同样以托盘快照为授权对象，并须通过
     `verificationIdentifiesSameRequest()`；**没有本地快照时失败关闭**——用户从未在本标签页
     看到该请求，服务端说 accepted 不等于用户同意，因此不授予任何人并提示重新发起验证。
   - **自己刚完成的 accept 的回声**由 `locallyAnsweredVerificationIds` 识别（写入 POST 前记录，
     60 秒过期，POST 失败即移除）：命中时**静默**——不重复授予（HTTP 路径已授予）、不再提示
     成功或失败、也不访问指纹端点。服务端会把 accepted 同时推给双方，没有这条会把正常成功
     路径显示成失败。
3. **双重比对**：`acceptedVerificationPeer()` 要求 `status === "accepted"` 且自己是当事人；
   `keyStatusMatchesAcceptedVerification()` 要求服务端当前 active key 的指纹**和版本**
   都等于快照所 pin 的值。
4. **作用域不扩散**：`peer.userId === userId` 保证授权只作用于被点名的那位联系人；
   当前打开的**另一位**联系人只刷新展示，绝不会被顺带信任。
5. **不授予的情形**：拒绝、取消、过期（含服务端 `409`）、普通刷新、无本地快照的 socket
   事件、`fetchContactKeyStatus` 失败、`respond` 抛错（含 204/空回包），一律不新增本地信任；
   失败时还会清空状态缓存，避免残留"已验证"外观。
6. **拒绝与失败不报成功**：成功文案只在 `refreshKeyVerificationAfterChange()` 返回
   `{granted: true}` 之后显示；未能真正授予时提示"密钥已变化，本次验证未授予信任"或
   "服务端未返回确认信息"，而不是显示成功。
7. **双击幂等**：`keyVerificationResponsesInFlight` 保证同一条请求只发一次 `respond`，
   不会出现两个相反 toast。

对应回归用例见 `chat/tests/js/key_trust_decline.test.js`，其中包含
"回包点名另一位联系人不得授予任何人"、"回包 id 不同视为不匹配"、
"decline 遇回包声称 accepted 仍不授予"、"无本地快照的 socket 事件零授予并提示"、
"快照一致时正常授予并报成功"、"双击只发一次请求"等定向场景。

## 3.2 独立设置页的密钥导出（WEB-04 / #170 依赖）

`templates/components/settings_panel.html` 的 `keyMgrExport()` 在独立设置页
（`templates/pages/settings.html`，不加载 `chat.js`）生效：

- `await kmgr.exportBackup()` 真正完成后才显示 `Backup downloaded. Keep it safe!`；
- 失败（私钥 `non-extractable`、无待导出备份、用户取消保存等）显示原因并给出恢复路径，
  绝不再报成功；
- 处理期间禁用按钮并显示 `Exporting…`，`finally` 恢复原文案；
- 有 in-flight 守卫，重复点击不会并行导出；
- **入口不重复实现前置条件**：是否可导出由 `key-manager.js:exportBackup()`
  （`pendingBackup || record.private_key`）决定，因此"本地记录已清理但本标签页仍持有
  一次性待备份"的设备也能正常导出。

## 4. 测试隔离（不得写脏仓库）

```text
$ git status --short -- media/
（无输出）
```

## 5. 本卡验收项对照

| 验收项 | 状态 | 证据 |
| --- | --- | --- |
| 新密钥不自动继承旧信任状态 | 通过（本机实测 + 多轮独立审查） | §2 三条 `test_rotated_key_is_untrusted_and_old_record_is_kept` / `test_trust_list_after_rotation_reports_current_key` / `test_previous_and_last_endorsed_diverge_after_consecutive_rotations`；第 5 轮审查另以 10 场景矩阵（S1–S10）独立验证两端点逐字段一致；**客户端侧**见 §3.1 与 `key_trust_decline.test.js`（拒绝/取消/过期/刷新不授予，接受且指纹一致才授予，回包不得改变授权对象） |
| 相关代码或配置经过非作者成员评审 | **部分**：多轮独立子 Agent 审查；但审查者是 **AI 子 Agent，不是人类成员**，分支未推送、无 PR 评审记录，人类评审仍缺 | 见 §6 |
| 适用的自动化或人工测试已执行并保存证据 | 自动化：已执行（§1–§3）；**人工 UI 验收：未执行** | 本文件 |
| 需求、部署或测试文档已同步更新 | **部分**：Phase4 修复轮已同步 `docs/phase4/t08-acceptance-evidence.md`（§3 数字与 §3.1/§3.2 新规则）、`docs/iChat Pro API 接口文档.md`（§3.3 验证请求语义与客户端契约）、**以及 `docs/phase4/traceability-matrix.md` 的 NEW-DEV-007 行**。`docs/phase4/tasks.md` 归集成分支统一更新，本分支**未改**。`docs/handoff/accounts-auth.md` 不在本分支（该目录在所有分支上都未被跟踪） | `git diff 716992b..HEAD --stat -- docs` |

## 6. 独立审查记录

| 轮次 | 审查者 | 结论 | 发现项 | 处理 |
| --- | --- | --- | --- | --- |
| 1 | 独立子 Agent（新上下文，仅给 diff + Issue 原文 + 命令） | PASS WITH FINDINGS | 3 MAJOR + 若干 MINOR | 全部修复或说明 |
| 2 | 独立子 Agent（新上下文，复审修复） | PASS WITH FINDINGS | 确认 3 条 MAJOR 已修复；新增 2 MAJOR（其中 1 条为我引入的 DELETE 契约回归）+ 5 MINOR | 已回滚 DELETE 改动，其余修复 |
| 3 | 独立子 Agent（新上下文） | PASS WITH FINDINGS | 前两轮 9 项确认修复；M1 证据命令不可复现、M2 handoff 文档不在分支、M4 API 文档缺新字段 | 已修 M1/M2/M4，并补 DELETE 正路用例 |
| 4 | 独立子 Agent（新上下文） | PASS WITH FINDINGS | M1 证据文件 3 处残留错误、M2 措辞已修；**MAJOR：API 文档 `last_endorsed_*` 的 null 规则与代码不符** | 改代码使语义与字段名一致（`_endorsed_elsewhere()`），另修证据文件 |
| 5 | 独立子 Agent（新上下文，10 场景矩阵） | PASS WITH FINDINGS | 语义变更正确、无回归；MAJOR：DELETE 注释引用了 `agent-handoff.md` 里不存在的规则 | 删除虚构引用，改注释放置位置，补"显式 untrusted"用例 |
| 6 | 独立子 Agent（重开，新上下文） | **PASS**（无 BLOCKER / MAJOR / MINOR） | 仅 2 条过程记录：审查区间口径（ff3ea42..d80c836）、worktree 内 `.review/` 为审查痕迹 | 无需处理 |
| 7 | 独立子 Agent（Phase4 修复轮，新上下文） | PASS WITH FINDINGS | WEB-03 主路径已修，但 3 MAJOR：授权对象取自服务端回包、文档未同步、导出入口门禁与实现不一致；另有 m-1 双击、m-4 死代码等 MINOR | 全部处理：授权改取本地快照并拒绝回包替换、同步本文件与 API 文档、删除重复门禁、加 in-flight 守卫、删除死代码入口；并补上能覆盖 M2/M7 变异的用例 |
| 8 | 独立子 Agent（第 2 轮，新上下文） | PASS WITH FINDINGS | HTTP 路径 35/35 确认修好、M-3 与 m-1/m-4 确认修好；**MAJOR：socket `accepted` 分支仍把事件负载当授权对象**（G2）；MINOR：回包 `id` 维无覆盖（F-1）、本文件数字自相矛盾（F-2）、204 提示不可读（F-3） | socket 改为无快照即失败关闭并给可读提示、成功 toast 移到 `granted` 之后；补 `id` 不匹配与 204 用例；按实测 111/27/9 统一数字 |
| 9 | 独立子 Agent（第 3 轮，新上下文） | PASS WITH FINDINGS | G2 确认修复（基线 45 红 → HEAD 2 红）；**MAJOR：服务端把 accepted 推给双方，本页收到自己那次 accept 的回声 → 成功后又报「未授予信任」（E5）/ 两条成功（E3）**；MINOR：TOFU 第二写入点表述不严谨、HTTP 无快照文案不实、§5 自述矛盾、版本维零覆盖（MU9） | 新增 `locallyAnsweredVerificationIds` 自回声抑制（静默、幂等、60s 过期、POST 失败即移除）；HTTP 无快照改用「不在本页待处理列表」文案；§3.1 明确 TOFU/群聊写入点；§5 改正；补版本维与 `granted===false` 用例 |


**未验证范围**（不得当作通过）：

- UI 人工验收：未启动浏览器核对右栏/指纹弹窗/设置页的真实渲染与交互；
- 跨端：未在 Windows 安装版、Electron、Android 上验证；
- 真实依赖：未在 PostgreSQL / Redis 下复跑，2 条并发用例 skipped；
- CI：分支未推送，无 CI run 记录；
- 非作者**人类**评审：仓库内无第二位提交者，无法自证。
