# 模块 7：测试体系、CI 与质量门禁

> 交接对象：负责测试分层、CI 流水线、覆盖率与质量门禁的 Agent。
> 核对日期 2026-10-18，基线 `cfe53be`。规范见 [conventions.md](conventions.md)。

---

## 1. 测试现状（本次实测，本节是全目录唯一权威数字）

| 层 | 规模 | 运行命令 | 耗时 |
| --- | ---: | --- | ---: |
| Django | **404 条 / 77 类 / 14 模块** | `.\.venv\Scripts\python.exe manage.py test --parallel 1` | 147s（总 152s） |
| Node（node:test） | 62 条（7 文件） | `npm run test:e2ee` | 约 40s |
| Node（自写断言） | 4 文件、约 30 组断言 | 同上 | 含在上面 |
| Electron/Chromium 布局 | 36 项 | `cd desktop && npm run test:layout` | 30–60s |
| 品牌/PE 资源 | 真打包 + 7 帧图标比对 | `npm run test:branding` | <180s |
| NSIS 隔离 | 24 条断言 | `npm run test:installer` | <60s |
| Android | **0** | — | — |

### Django 测试分布

| 文件 | 条数 |
| --- | ---: |
| `accounts/tests.py` | 127 |
| `chat/tests/test_core.py` | 80（文件里有 100 个 `def test_`，其中 3 个类被重复定义、后定义覆盖前定义，见坑 1） |
| `chat/tests/test_phase2_backend.py` | 54 |
| `chat/tests/test_phase2_issues.py` | 47 |
| `chat/tests/test_t32_review.py` | 18 |
| `chat/tests/test_integration.py` | 16 |
| `chat/tests/test_sync_api.py` | 14 |
| `chat/tests/test_messaging_service.py` | 11（含 PG-only 并发用例） |
| `chat/tests/test_protocol.py` | 11 |
| `chat/tests/test_llm.py` | 9 |
| `chat/tests/test_private_realtime.py` | 6 |
| `chat/tests/test_group_realtime.py` | 5 |
| `chat/tests/test_conversation_api.py` | 3 |
| `ichat_pro/tests.py` | 3 |

本次结果：`Ran 404 tests in 147.447s` / `OK (skipped=2)`。2 条 skip 是 PostgreSQL-only 的并发用例。

### Node 测试两种风格

- **node:test 风格（7 文件）**：`desktop_tray` 15、`chat_notifications` 13、`notification_integration` 12、`message_accepted` 12、`desktop_notifications` 7、`public_key_cache` 2、`desktop_branding` 1。
- **自写断言脚本（4 文件，无 runner，行数为总行数）**：

| 文件 | 总行数 | 用例 | 备注 |
| --- | ---: | ---: | --- |
| `chat_connection.test.js` | 688 | 20 个 `test_*`，`tests` 数组只引用 19 个 | 输出 9 条 ✓ |
| `desktop_config.test.js` | 67 | 2 个 `test_*`（`tests` 数组里是函数引用，`node --test` 只报 1 条） | 输出 2 条 ✓ |
| `private_chat_e2ee.test.js` | 220 | `run()` 串联 | 输出 `all tests passed` |
| `group_chat_e2ee.test.js` | 178 | `run()` 串联 | 输出 `all tests passed` |

> 这两个文件用 `node --test` 跑会显示 `tests 1`——因为它们**不是**用 `test()` 注册的，`node --test` 只把整个文件当一个用例。判断它们是否真的逐条执行，要看 `✓` 行数，不要看 `node --test` 的 `tests N`。

`npm run test:e2ee` 是一条 `&&` 长链：4 个自写脚本顺序跑，然后一个 `node --test` 跑 7 个文件。任一非零退出即整链失败。**全部为纯 Node**（`node:vm` + `node:crypto.webcrypto` + stub fetch/localStorage/DOM），不需要 Electron 或浏览器。

---

## 2. 自定义测试 runner（必须理解）

`ichat_pro/test_runner.py` 的 `IChatTestRunner` 把 `channels.db.close_old_connections` 替换成空函数。

**原因**：`channels.db.database_sync_to_async` 每次调用前后都会 `close_old_connections()`；在 Django `TestCase` 的 atomic 块里 `autocommit=False`，`close_if_unusable_or_obsolete` 会判定 autocommit 未恢复而**在事务中途关闭连接**，之后所有语句抛 psycopg "the connection is closed"。

在 `settings.py:157` 全局生效。**改动它会让大量 WS 测试崩溃。**

---

## 3. CI（`.github/workflows/django.yml`，134 行）

触发：`pull_request`（无分支过滤）+ `push` 到 `main`。

| job | runner | 步骤 |
| --- | --- | --- |
| `windows-installer` | windows-latest | checkout → node 24 → `choco install nsis` → `desktop: npm ci` → `test:layout` → `test:branding` → `test:installer` |
| `javascript` | ubuntu-latest | checkout → node 24 → `npm run test:e2ee`（**不跑 `npm ci`**，因为当前只用 Node 内置模块） |
| `test` | ubuntu-latest | python 3.13 + pip cache → `pip install -r requirements.txt` → `check` → `makemigrations --check --dry-run` → `manage.py test` |
| `integration` | ubuntu-latest | postgres:16 + redis:7 services → `DATABASE_URL`/`REDIS_URL` → `check` → `migrate --noinput` → `manage.py test --parallel 1` |

### CI 缺什么（都是已开卡的缺口）

| 缺 | 对应 Issue |
| --- | --- |
| 无 required status checks（主分支保护未配置，rulesets 为空） | #172 |
| 无 lint / format 检查（无 ruff、无 eslint、无 pre-commit） | #171 |
| 无覆盖率统计与阈值 | #173 / #193 |
| 无依赖/秘密扫描（无 pip-audit、npm audit、CodeQL、dependabot） | #174 |
| 无 Android 构建 | #175 / #151 |
| 无 Windows **安装包构建**（只验品牌资源，不跑 `npm run dist`） | #175 |
| 无部署后冒烟（只 readiness） | #204 |
| 无独立测试环境部署 | #176 |
| 无 matrix（Python 只有 3.13、Node 只有 24） | — |
| 无 concurrency 取消旧 run | — |

`--parallel 1` 没有代码注释解释。可确证的关联事实：integration job 要跑 `@skipUnless(postgresql)` 的真实并发测试（`threading` + 独立连接 + `TransactionTestCase`），且共享固定 `REDIS_URL` 的 DB 0。**这是推断，改动前需实测。**

---

## 4. 本地跑全部检查

```powershell
# 1. Django
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test --parallel 1

# 2. Node
npm run test:e2ee

# 3. 桌面（仅 Windows）
cd desktop
npm run test:layout
npm run test:branding
npm run test:installer
cd ..

# 4. 提交前
git diff --check
git status --short
```

---

## 5. 已知坑

| # | 坑 | 影响 |
| --- | --- | --- |
| 1 | `chat/tests/test_core.py` 1096-1346 行是重复粘贴区（imports + 3 个与 26/74/126 行**AST 完全相同**的类）。后定义覆盖前定义 | 文件里写着 100 个 `def test_`，实际收集 **80** 条。**删掉 1096-1307（重复的 3 个类）后仍是 80 条、全量仍是 404**——被覆盖的是同名类，命名空间里本来就只留一份，没有丢覆盖。**注意 1308 行的 `AdminOperationLogTests` 是正常测试，不要一起删**；如果删到 1346，就会连带删掉它 3 条用例，全量变 381，那才是真的少了 |
| 2 | `chat/tests/js/chat_connection.test.js` 定义了 20 个 `test_*`，但 `tests` 数组只引用 19 个——`test_group_active_dedupe_restored_placeholder`（614 行，函数体只有一句 `assert(can("sent","delivered"))`）**从未被调用** | 又一处静默跳过。要么接进 `tests` 数组，要么删掉，不要留着假装覆盖 |
| 3 | PostgreSQL-only 用例在 SQLite 静默 skip | 本地"全绿"不证明并发正确 |
| 4 | `--parallel 1` 无注释 | 改动需自行验证（推断原因见 §3） |
| 5 | `javascript` job 不 `npm ci` | 一旦测试引用第三方包就会在 CI 挂掉，本地却正常 |
| 6 | Chromium 布局测试读的是模板片段自拼 HTML + mock API | **不是**登录态端到端 |
| 7 | NSIS 测试缺 `makensis` 直接失败不跳过 | 本地没装 NSIS 会红，属预期；查找顺序见 [conventions.md §4](conventions.md) |
| 8 | `test:layout` / `test:branding` / `test:installer` 只在 `desktop/package.json` | 根目录跑会报 `Missing script` |
| 9 | 无覆盖率基础设施 | #193 要从零搭 |

---

## 6. 这个模块的开放任务

| Issue | 内容 | 优先级 | 起点建议 |
| --- | --- | --- | --- |
| [#171](https://github.com/HeanX/iChat_Pro/issues/171) | P4 T44 静态检查与格式门禁 | P0 | 引入 `ruff`（Python）+ `eslint` 或 `biome`（JS），先只报错不阻断一周，再设为必过 |
| [#172](https://github.com/HeanX/iChat_Pro/issues/172) | P4 T45 Django/迁移/测试门禁 | P0 | 代码侧已就绪，缺的是**仓库设置**：main 分支加 required status checks（需管理员权限） |
| [#173](https://github.com/HeanX/iChat_Pro/issues/173) | P4 T46 覆盖率门禁 | P0 | 先 #193 建统计，再定阈值（建议先 70% 起步，不要直接 85%） |
| [#174](https://github.com/HeanX/iChat_Pro/issues/174) | P4 T47 依赖与秘密扫描 | P1 | `pip-audit` + `npm audit --audit-level=high` + gitleaks |
| [#193](https://github.com/HeanX/iChat_Pro/issues/193) | P4 Test T12 覆盖率统计 | P0 | `coverage run --source=accounts,chat,ichat_pro manage.py test`，先出报告不定阈值 |
| [#190](https://github.com/HeanX/iChat_Pro/issues/190) | P4 Test T09 总体测试计划 | P0 | **不依赖 Android，可立刻写**；是 T10/T11/T12/T22 的前置 |
| [#191](https://github.com/HeanX/iChat_Pro/issues/191) | P4 Test T10 黑盒测试设计 | P0 | 等价类/边界值/判定表/状态迁移/场景法，基于现有 API 写用例集 |
| [#192](https://github.com/HeanX/iChat_Pro/issues/192) | P4 Test T11 白盒测试设计 | P0 | 基于 `messaging.py` 的分支覆盖写路径说明 |
| [#203](https://github.com/HeanX/iChat_Pro/issues/203) | P4 Test T22 最终测试总结 | P0 | 最后做，数据必须能由原始 run 复核 |
| [#184](https://github.com/HeanX/iChat_Pro/issues/184) | P4 Test T03 Windows 安装/E2E | P0 | 见 [windows-desktop.md](windows-desktop.md) |
| [#196](https://github.com/HeanX/iChat_Pro/issues/196) | P4 Test T15 浏览器/Electron E2E | P1 | 引入 Playwright，先做登录→发消息→收消息一条链路 |
| [#198](https://github.com/HeanX/iChat_Pro/issues/198) | P4 Test T17 安全测试 | P0 | SAST（ruff 安全规则）+ 依赖扫描 + 手工越权用例 |
| [#199](https://github.com/HeanX/iChat_Pro/issues/199) | P4 Test T18 性能与稳定性 | P1 | 阻塞于跨端完成 |
| [#200](https://github.com/HeanX/iChat_Pro/issues/200) | P4 Test T19 弱网与故障 | P0 | 可用 `tc netem` 或 Chrome DevTools throttling |
| [#201](https://github.com/HeanX/iChat_Pro/issues/201) | P4 Test T20 兼容性矩阵 | P1 | 依赖 T03/T04 |
| [#185](https://github.com/HeanX/iChat_Pro/issues/185) | P4 Test T04 Android UI | P0 | 阻塞于 Android |
| [#197](https://github.com/HeanX/iChat_Pro/issues/197) | P4 Test T16 Android UI 自动化 | P1 | 阻塞于 Android |
| [#186](https://github.com/HeanX/iChat_Pro/issues/186) | P4 Test T05 群聊与文件跨端 | P1 | 阻塞于跨端 |
| [#187](https://github.com/HeanX/iChat_Pro/issues/187) | P4 Test T06 三端加密向量 | P0 | 可先做"两个浏览器端 + 桌面端"的向量一致性，Android 部分后补 |
| [#189](https://github.com/HeanX/iChat_Pro/issues/189) | P4 Test T08 密钥指纹与变化提醒 | P0 | 见 [web-frontend-e2ee.md](web-frontend-e2ee.md) |
| [#205](https://github.com/HeanX/iChat_Pro/issues/205) | P4 Test T24 AI 辅助需求与用例评审 | P1 | 有真实 AI 使用记录后整理 |

**不依赖任何外部资源的四项**（建议先做）：#190 测试计划 → #193 覆盖率统计 → #191/#192 黑白盒设计 → #171 静态检查。

---

## 7. 给下一个 Agent 的提示词

```text
你在 iChat_Pro 仓库负责【测试体系与 CI 模块】。

必读：docs/handoff/conventions.md、docs/handoff/testing-quality-ci.md、
.github/workflows/django.yml、ichat_pro/test_runner.py。
再读代码：根 package.json 的 test:e2ee 链、desktop/package.json 的三个测试脚本。

任务：【填写 Issue 号与标题】。

禁止：
- 删除或跳过失败测试来让流水线变绿。
- 把 fixture/Chromium 布局结果写成原生验收。
- 在没有实测的情况下填写覆盖率或性能数字。
- 在 CI 里引用本地不存在、仓库也没有的脚本。

必须执行：
.\.venv\Scripts\python.exe manage.py test --parallel 1
npm run test:e2ee
（改 CI 时）核对四个 job 的命令与本地一致

新增门禁必须先在本地产出真实结果，再设为 CI 必过；
阈值初期应低于实际值，避免长期红灯。

完成前必须另开独立审查子 Agent 复核 diff（见 conventions.md §1），
审查重点：门禁是否真的会阻断、是否有被静默跳过的分支。
```
