# 开放 Issue 派工清单（49 项）

> 数据来源：GitHub `HeanX/iChat_Pro` 实时查询（2026-10-18）。开放 **49 项**（全部带 `phase-4` 标签），其中 Phase4 原始 78 项里已关闭 29 项。仓库当前**没有未合并的 PR**，远端最后一次活动是 2026-10-07T14:21:32Z。
>
> 状态口径以 [docs/phase4/tasks.md](../phase4/tasks.md) 与 Issue 内证据为准；本表只做派工索引。
>
> **优先级不在 GitHub 标签上**：49 项都没有 P0/P1/P2 优先级 label（`gh issue list --label P0/P1/P2` 三者均返回 `[]`）。优先级来自 Issue 正文的"任务信息"段与 `tasks.md` 表格，本表已转录。
>
> **别被 `p1:` 开头的标签误导**：仓库里确实存在 `p1: core-domain-auth`、`p1: product-loop`、`p1: realtime-e2ee`、`p1: release-acceptance` 这 4 个标签，但它们表示的是**Phase-1 的主题分组**，不是优先级；且**只挂在 15 个已关闭的旧 Issue 上，49 个开放 Issue 一个都没有**。`phase-1`/`phase-2`/`phase-3` 同理，是阶段标签。
>
> **本表核对与数据保存（2026-10-18）**：下表的总数、分组、优先级与"还差什么"已逐行对 `gh` 实时输出复核，复核口径与偏差见文末 [§核对记录](#核对记录2026-10-18)。文末"已知缺口"表的代码类断言的 `文件:行` 已按当前工作树重定位——`cfe53be` 之后代码有改动，早期写的行号已漂移（**定位一律以符号名为准**）。

> **并发提示（2026-10-18 实测）**：本次核对期间，同一工作树里检测到**多个 Agent 并发写入**，且有 6 个新提交陆续落地。因此**本表中任何"尚未提交/工作树状态"的描述都可能在你读到时就已过期**；已在相关行标注提交号。**接手前务必先 `git status --short` + `git log --oneline -5`**，不要假设他人未提交的改动是你自己的。

## 总览

> **验收标准的统一结构（已对 49 张卡逐张核实）**：每张卡的 `## 验收标准` 段都**恰好 4 条**——第 1 条是业务验收项（各卡不同，例如 #204 是"健康、登录和核心接口失败时阻断发布"），后 3 条是固定模板（非作者评审 / 测试留证据 / 文档同步）。因此"代码写完"离"能关单"永远差这 3 条。各卡的第 1 条业务验收项见 [§附录：49 张卡的业务验收项](#附录49-张卡的业务验收项)。

| 工作包 | 开放/总数 | 说明 |
| --- | ---: | --- |
| WP00 基线与统筹 | 1/5 | 负责人/时间/降级方案未验收 |
| WP01 云端运维 | 1/10 | 仅缺监控告警 |
| WP02 消息可靠性 | 1/7 | 仅缺跨端 |
| WP03 Windows | 2/8 | 通知差最终复验；文件集成未实施 |
| WP04 Android | **9/9** | **不是"全部未开始"**：T22 已有 ADR + `mobile/` 骨架 + 构建出的 APK，但**无真机运行**（见下） |
| WP05 安全与设备 | **6/6** | 全部未开始 |
| WP06 测试与缺陷 | 18/20 | 仅 Test T14、Test T21 完成 |
| WP07 CI 与发布 | **8/8** | **虽有 CI 在跑，8 张卡的验收门禁都未生效**（见 #172 一行） |
| WP08 证据与材料 | 3/5 | 最终证据与材料 |

按优先级：**P0 28 项、P1 19 项、P2 2 项**（由 49 个 Issue 正文的"优先级"字段直接计数复核）。

**WP04 的准确状态**：9 张卡仍全部开放，但 T22（#149）已有实质产出——ADR-P4-03 已定稿（Capacitor 方案 + 转原生触发条件），`mobile/` 骨架与 `mobile/android/` 原生工程已由 Capacitor CLI 8.5.3 真实生成并**已提交**（`3cc4fc7`，后续 `7156453`/`4d179da` 加固），配置契约测试 **30/30** 通过（2026-10-27 更正：本节写于 2026-10-18，当时是 **10/10**；此后 `ac14a37`/`62d673e`/`3d902de` 逐步加到 30），**`app-debug.apk` 也已构建成功**（4.22 MB，SHA256 `0D05C92B…4B4976`；**该 SHA 属修复前旧产物，已过期**，AND-01 后的产物见 ADR §5.1）。

**但仍不能算 T22 完成**，缺的是验收标准要的那一半：APK 产物在 `build/` 下且**已被 gitignore**、**从未装到真机或模拟器上运行过**（`adb devices` 为空、无 emulator 包、无 Hyper-V），WebView 复用现有页面的运行时验证为 **0 次**。详见 [android-mobile.md](android-mobile.md) §1。

> 派工提醒：接手 T23–T30 时**不要从零搭骨架**，骨架与 ADR 都在了；直接进"响应式适配 / 返回键 / 重连 / 通知 / 文件 / 安全存储"。同时注意 `mobile/android/app/build/` 是 gitignore 的，**别指望 clone 下来就有 APK**。

---

## 第一梯队：只差证据，投入最小（建议立刻做）

> 口径：这 4 张卡**有实现基础**，但**没有一张是"代码零改动 / 只补截图"就能关闭的**。"只差证据"严格来说只对 #145 成立；#139 要新写告警代码，#172 是仓库设置（非代码），#204 的脚本已落地但尚未在生产验证。

| Issue | 任务 | 还差什么 | 预计工作量 |
| --- | --- | --- | --- |
| [#145](https://github.com/HeanX/iChat_Pro/issues/145) | P4 T18 Windows 通知 | **只差证据**。实现与 AUMID 修复已合并部署（安装包 SHA-256 `ac468df3…d5f9`）。核心一项：用**正式安装版**从开始菜单启动 → 最小化进托盘 → 另一账号发消息 → 点 toast → 确认窗口恢复并定位会话；另加通知来源显示为正式应用入口、系统级关闭通知的对照。参考页面布局那项依据在 Issue 评论里，但验收标准原文只要求"遵守静音设置 + 点击可定位会话" | 30 分钟 |
| [#139](https://github.com/HeanX/iChat_Pro/issues/139) | P4 T12 监控告警 | `/health/live/`、`/health/ready/`、journalctl 与 nginx 日志已上线。**缺的是新东西，不是证据**：资源阈值告警 + 证书续期失败告警 + readiness 连续失败告警（仓库内现无任何告警实现）。验收标准只写"能发现服务、资源、证书和关键错误状态" | 半天 |
| [#172](https://github.com/HeanX/iChat_Pro/issues/172) | P4 T45 合并门禁 | **核实结论：required status checks 至今未配置**（`GET /branches/main/protection` 返回 `required_status_checks: null`、`required_pull_request_reviews: null`）。代码侧四项 CI（`test` / `integration` / `javascript` / `windows-installer`）在 main 上确为 success（run 37636022966）。缺口是仓库设置，需管理员权限；**注意这不只是"点一下"**：任务正文要的是"Django、迁移和自动化测试"接入，而 Django 与迁移检查已在 `test`、`integration` 两个 job 中覆盖，需确认关单口径是否按"加上 required checks"即可 | 10 分钟（有权限时） |
| [#204](https://github.com/HeanX/iChat_Pro/issues/204) | P4 Test T23 冒烟 | **有实质进展，但仍不能关单**。冒烟门禁已落地并提交（`46e2cea`，后续 `d756af4` 修正了 CSRF Referer origin 的构造）：`deploy/smoke.sh` 断言 health/live、health/ready、登录表单 CSRF → `POST /login/` 302 + `sessionid` → `/api/conversations/`、`/api/profile/me/` → 匿名 `/api/` 401；`deploy/deploy.sh` 在 readiness 之后、写 `DEPLOYMENTS.log` 之前跑它，失败退出非零且不记录，缺 `SMOKE_USERNAME/PASSWORD` 时 fail-closed。`deploy/runbook.md` 已补第 7 节。**缺口**：runbook 自述只在本地 SQLite + Django live server 下验证过（7 条用例），**未在生产验证**，且生产 `.env` 里还没有冒烟账号。剩余 = 建冒烟账号 + 生产真跑一次 + 留证据。另注意：那 7 条用例在**没有 `bash` 的 Windows 环境会静默 skip**（本机须把 Git Bash 加进 PATH 才会真跑） | 半天（建账号 + 生产验证） |

---

## 第二梯队：不依赖外部资源，可并行开工

| Issue | 任务 | 依赖（Issue 原文） | 说明 |
| --- | --- | --- | --- |
| [#190](https://github.com/HeanX/iChat_Pro/issues/190) | P4 Test T09 测试计划 | 无（原文写 `P4 T01～P4 Test T02`，均已关闭） | **是 T10/T11/T12/T22 的前置**，最优先 |
| [#191](https://github.com/HeanX/iChat_Pro/issues/191) | P4 Test T10 黑盒设计 | P4 Test T09（#190） | 等价类/边界值/判定表/状态迁移/场景法 |
| [#192](https://github.com/HeanX/iChat_Pro/issues/192) | P4 Test T11 白盒设计 | P4 Test T09（#190） | 基于 `messaging.py` 分支 |
| [#193](https://github.com/HeanX/iChat_Pro/issues/193) | P4 Test T12 覆盖率 | **P4 Test T01、P4 Test T11（#192）**——不是 #191 | 仓库当前无任何覆盖率配置。**此前本表误写为依赖 #191（黑盒），会派错工** |
| [#171](https://github.com/HeanX/iChat_Pro/issues/171) | P4 T44 静态检查 | 无（原文 `P4 T02`，已关闭） | ruff + eslint/biome |
| [#174](https://github.com/HeanX/iChat_Pro/issues/174) | P4 T47 依赖与秘密扫描 | **P4 T11（已完成）、P4 Test T17（#198，仍开放）**——原文有依赖，不是"无" | pip-audit + npm audit + gitleaks。可先落地扫描链路（不依赖 #198 完成）；#198 是"执行安全测试"，与扫描工具有交集但不等同 |
| [#188](https://github.com/HeanX/iChat_Pro/issues/188) | P4 Test T07 密文边界审计 | 无（原文 `P4 T07～P4 T11`，WP01 已全部完成） | 已有断言基础，缺审计报告 |
| [#130](https://github.com/HeanX/iChat_Pro/issues/130) | P4 T03 迭代计划 | 无（原文 `P4 T01`，已关闭） | 补负责人、时间、降级方案 |
| [#150](https://github.com/HeanX/iChat_Pro/issues/150) | P4 T23 响应式适配 | **P4 T22（#149）**——原文有依赖，不是"无" | 改模板/CSS，浏览器 400×720 可验。虽被 #149 卡，但 #149 的骨架已在，可先做 |

---

## 第三梯队：Windows 相关

| Issue | 任务 | 依赖（Issue 原文） | 说明 |
| --- | --- | --- | --- |
| [#146](https://github.com/HeanX/iChat_Pro/issues/146) | P4 T19 原生文件 | P4 T14（已完成） | 需新增窄 IPC 通道，不是通用 fs |
| [#184](https://github.com/HeanX/iChat_Pro/issues/184) | P4 Test T03 Windows E2E | P4 T15～P4 T21 | 安装→聊天→通知→文件→恢复完整报告 |
| [#196](https://github.com/HeanX/iChat_Pro/issues/196) | P4 Test T15 浏览器/Electron E2E | P4 Test T03（#184） | 引入 Playwright |

---

## 第四梯队：安全与设备（依赖设计先行）

| Issue | 任务 | 依赖 | 说明 |
| --- | --- | --- | --- |
| [#165](https://github.com/HeanX/iChat_Pro/issues/165) | P4 T38 威胁模型 | P4 T01、P4 T04（均已完成） | 浏览器版已有，需跨平台完整版。**不依赖 #167**，可先开工 |
| [#166](https://github.com/HeanX/iChat_Pro/issues/166) | P4 T39 平台安全存储 | P4 T21（已完成）、P4 T29（#156，Android 侧缺） | Windows 已完成一半，Android 侧缺 |
| [#167](https://github.com/HeanX/iChat_Pro/issues/167) | P4 T40 设备身份模型 | P4 T38（#165） | **先写 ADR，不要先写代码** |
| [#168](https://github.com/HeanX/iChat_Pro/issues/168) | P4 T41 设备列表与撤销 | P4 T40（#167） | 被撤销设备不再接收后续消息密钥 |
| [#169](https://github.com/HeanX/iChat_Pro/issues/169) | P4 T42 多设备密钥分发 | P4 T40、P4 T41（#167/#168） | 会改变群聊 recipients 语义，需与消息模块串行 |
| [#170](https://github.com/HeanX/iChat_Pro/issues/170) | P4 T43 历史密钥迁移 | P4 T42（#169） | P2，只做设计与原型 |
| [#189](https://github.com/HeanX/iChat_Pro/issues/189) | P4 Test T08 密钥指纹提醒 | P4 Test T06（#187） | UI + 接口 + 测试 |
| [#187](https://github.com/HeanX/iChat_Pro/issues/187) | P4 Test T06 三端加密向量 | P4 T14（已完成）、P4 T24（#151，Android 未做） | 可先做 Web+Windows 两端 |
| [#198](https://github.com/HeanX/iChat_Pro/issues/198) | P4 Test T17 安全测试 | 相关功能完成（无具体编号） | SAST/DAST/手工越权 |

---

## 第五梯队：Android（从零，最长路径）

| Issue | 任务 | 依赖（Issue 原文） |
| --- | --- | --- |
| [#149](https://github.com/HeanX/iChat_Pro/issues/149) | P4 T22 技术方案 + 骨架 | P4 T01（已完成）——**实际起点**；ADR-P4-03 与 `mobile/` 骨架已提交（`3cc4fc7`），APK 也已构建，但仍缺**真机/模拟器运行**与 WebView 页面复用验证 |
| [#151](https://github.com/HeanX/iChat_Pro/issues/151) | P4 T24 APK/AAB | P4 T22、P4 T23（#149/#150） |
| [#152](https://github.com/HeanX/iChat_Pro/issues/152) | P4 T25 返回键与生命周期 | P4 T24（#151） |
| [#153](https://github.com/HeanX/iChat_Pro/issues/153) | P4 T26 网络变化与重连 | P4 T24（#151） |
| [#155](https://github.com/HeanX/iChat_Pro/issues/155) | P4 T28 文件选择与保存 | P4 T24（#151） |
| [#156](https://github.com/HeanX/iChat_Pro/issues/156) | P4 T29 Android 安全存储 | P4 T24（#151） |
| [#154](https://github.com/HeanX/iChat_Pro/issues/154) | P4 T27 前后台通知 | P4 T24（#151） |
| [#157](https://github.com/HeanX/iChat_Pro/issues/157) | P4 T30 云推送 | P4 T27（#154）；P2 |
| [#164](https://github.com/HeanX/iChat_Pro/issues/164) | P4 T37 跨端聊天 | P4 T20、P4 T26、P4 T31～P4 T36 |
| [#185](https://github.com/HeanX/iChat_Pro/issues/185) | P4 Test T04 Android UI | P4 T23～P4 T29 |
| [#197](https://github.com/HeanX/iChat_Pro/issues/197) | P4 Test T16 Android 自动化 | P4 Test T04（#185） |

---

## 第六梯队：发布、环境与最终材料

| Issue | 任务 | 依赖（Issue 原文） |
| --- | --- | --- |
| [#173](https://github.com/HeanX/iChat_Pro/issues/173) | P4 T46 覆盖率门禁 | P4 Test T12（#193） |
| [#175](https://github.com/HeanX/iChat_Pro/issues/175) | P4 T48 标准化构建 | P4 T15、P4 T24（#151，Android） |
| [#176](https://github.com/HeanX/iChat_Pro/issues/176) | P4 T49 独立测试环境 | P4 T07～P4 T11（WP01 已完成）→ **实际可开工** |
| [#177](https://github.com/HeanX/iChat_Pro/issues/177) | P4 T50 发布审批与回滚 | P4 T13、P4 Test T23（#204） |
| [#194](https://github.com/HeanX/iChat_Pro/issues/194) | P4 Test T13 HTTP/WS 集成测试 | P4 T31～P4 T36（WP02 除 T37 外均已完成）→ **实际可开工** |
| [#199](https://github.com/HeanX/iChat_Pro/issues/199) | P4 Test T18 性能稳定性 | **P4 T07～P4 T12、P4 T37**——本表此前漏了前半段；T37 仍开放，但 WP01 的 T07–T12 已基本完成 |
| [#200](https://github.com/HeanX/iChat_Pro/issues/200) | P4 Test T19 弱网故障 | P4 T34～P4 T37 |
| [#201](https://github.com/HeanX/iChat_Pro/issues/201) | P4 Test T20 兼容性矩阵 | P4 Test T03、P4 Test T04（#184/#185） |
| [#186](https://github.com/HeanX/iChat_Pro/issues/186) | P4 Test T05 群聊文件跨端 | P4 T19、P4 T28、P4 T37 |
| [#180](https://github.com/HeanX/iChat_Pro/issues/180) | P4 T53 证据整理 | 各 Epic P0 完成（无具体编号） |
| [#181](https://github.com/HeanX/iChat_Pro/issues/181) | P4 T54 最终材料 | P4 Test T22、P4 T50、P4 T53 |
| [#203](https://github.com/HeanX/iChat_Pro/issues/203) | P4 Test T22 测试总结 | P4 Test T10～P4 Test T21 |
| [#205](https://github.com/HeanX/iChat_Pro/issues/205) | P4 Test T24 AI 评审 | P4 Test T02、P4 Test T09（#190） |

---

## 依赖导致的阻塞链

```
#165 威胁模型 → #167 设备模型 → #168 撤销 → #169 多设备分发 → #170 历史迁移
                              ↘ #166 平台安全存储（Windows 侧已完成一半，卡 Android #156）
#149 Android 方案 → #150 响应式 → #151 APK → #152/#153/#155/#156 → #154 通知 → #157 推送
                                             → #164 跨端 → #186 群聊文件跨端
                                                          → #199 性能
#190 测试计划 → #191 黑盒 / #192 白盒 → #193 覆盖率 → #173 覆盖率门禁
                                      ↘ #203 最终测试总结
#204 冒烟门禁 → #177 发布审批与回滚
```

> 上面这条链只画**真实存在的依赖**。原表链条本身的边是对的，但漏了 #165→#167（威胁模型是设备模型的前置）、#149→#150、#204→#177 三处；另外 #173/#203 的正确汇入点是 #193 与 "#192(+#190)"，原表画成从 #193 再分出 #203，接错了上游。

**关键判断**：WP04（Android）与 WP05（设备/安全）是"零起步"的 15 项（9 + 6，已复核），它们同时卡住了 #164、#185、#186、#197、#199 等下游任务。如果本学期资源有限，应优先保证 WP06 的测试文档（#190–#193、#203 共 5 项）与 WP07 的门禁（#171–#177 共 7 项），这两组共 **12 项**（原表写"13 项"有误）不依赖 Android。

**但先看下面这几条纠偏**（原表把它们全归到"不依赖外部资源"，实际不是）：

| Issue | 原表判断 | 实际情况 |
| --- | --- | --- |
| #166 | 放在 WP05"全部未开始" | 依赖 P4 T21（已完成）+ P4 T29（#156）。**Windows 侧已完成一半**，可先做 Windows 段 |
| #174 | "无依赖" | 原文依赖 P4 Test T17（#198，开放）。扫描链路可先落地，但关单需 #198 口径确认 |
| #176 | 未标明是否可开工 | 原文依赖 `P4 T07～P4 T11`，**已全部完成** → 可直接开工，是 WP07 里最该先做的 |
| #194 | 在第六梯队 | 原文依赖 `P4 T31～P4 T36`，**除 T37 外全部完成** → 可先覆盖已完成的 HTTP/WS 面 |
| #150 | "无依赖" | 原文依赖 P4 T22（#149，开放）。骨架已在，可先做 CSS/模板 |
| #199 | "依赖 T37" | 原文是 `P4 T07～P4 T12、P4 T37`，前半段（云端）已基本完成 |

---

## 不在 Issue 里但已知的缺口

这些在文档/代码中被记录，但**没有对应 Issue**，接手时需要自行决定是否建卡。

> **行号说明**：下列 `文件:行` 已于 2026-10-18 逐条复核，其中 4 条原文引用有误或已漂移，已改正。**但代码一直在动（核对期间 HEAD 从 `cfe53be` 走到 `4d179da`），行号随时会再漂**：定位时请优先搜符号名/错误码字符串，行号只作指路。特别是"前端/桌面"表里那几处**没有**行号锚点的，一律以符号名为准。

### 后端 / 协议

| 缺口 | 位置（已复核） |
| --- | --- |
| 撤回/删除/成员变更不进 `ConversationEvent`，断线补不回来 | `chat/models.py:71-73` 起是 `ConversationEvent.Kind`，**枚举里只有 `MESSAGE` 一个值**；`chat/services/messaging.py:574`、`:722` 也只写 `Kind.MESSAGE` |
| `file_unavailable` 在 views 返回 410，注册表是 409 | 注册表 `chat/errors.py:90` 为 409；views 返回 **410** 的**共 6 处**：`chat/views.py:823`、`:3315`、`:3906`、`:4199`、`:4257`、`:4286`（另 `:3516` 的 `upload_expired` 也是 410）。代码同源的 409/410 分裂会让客户端按错误码分支时行为不一致 |
| `message_type_mismatch` / `invalid_json` / `rate_limited` 未注册 | 三者均不在 `chat/errors.py` 的错误表里；实际抛出处：`views.py:3912`（`message_type_mismatch`）、`views.py:243`（`rate_limited`，429）、`views.py:3338`/`:3894`（`invalid_json`，400） |
| `views.py` 存在读/序列化旁路，与 consumer 实现重复 | `chat/views.py` 4703 行；`_serialize_file_private_message` 在 `views.py:4149`，对应 consumer 的 `serialize_private_message` 在 `consumers.py:700`。**注意**：主消息发送路径已统一走 `messaging.send_private_message/send_group_message`（`views.py:895/962/2234/2281`），这条指的主要是**序列化**重复，不是写路径旁路 |
| `chat/tests/test_core.py` 20 条用例被重复类遮蔽 | **已核实为精确 20 条**（不是约数）：`ConversationModelTests`（def@26 / 重复@1103，丢 7）、`ConversationMemberModelTests`（def@74 / 重复@1151，丢 5）、`EncryptedMessageModelTests`（def@126 / 重复@1203，丢 8）。文件里 14 个 `class` 只有 11 个唯一名 |
| 待发箱不持久化，页面重载/进程重启丢未确认项 | `docs/current-status.md:41`，属于该文件 `## 消息可靠性`（第 33 行）一节。原表写"§消息可靠性"是对的，只是没有行号；这里补上行号以便检索 |
| 首次离线无法建立新的加密关系 | 同一条 `docs/current-status.md:41`：离线加密需已缓存且符合信任规则的公钥 |
| `cleanup_expired_uploads` 没有 cron 调度 | 命令存在：`chat/management/commands/cleanup_expired_uploads.py`；**没有任何 cron / systemd timer 调度它**（`deploy/runbook.md` 未提及）。注意区分：文档里**有引用**（`data-storage-file.md:16/120/121/137/193`、`prompt-template.md:154`），但那只是"提到这个命令"，不等于"定时跑了它" |

### 前端 / 桌面

| 缺口 | 位置（已复核） |
| --- | --- |
| `chat.js` 里约 20 处动态 `innerHTML` 的 `onclick=` 在生产 CSP 下失效 | **`static/js/chat.js` 恰好 21 处 `onclick=`**（全 `static/js/` 也只有这 21 处，均在 chat.js）。**不要按行号找**——并发改动使它们一直在漂移；正确做法是 `Select-String -Path static\js\chat.js -Pattern 'onclick='` 现数现用。行内拼接字符串形态，如 `<button type="button" class="groups-sidebar-main" onclick="openGroupFromSidebar(...)">` |
| `window.localKeyVersion` 全仓无赋值，文件消息 key_version 兜底退化为 1 | **已核实**：读取点在 `chat-message-actions.js:652/659/737`、`group-chat-e2ee.js:564`，全部写成 `window.localKeyVersion \|\| 1`；全仓库（排除 venv/node_modules）**没有任何一处给它赋值**，只有同名局部变量（`group-chat-e2ee.js:634`、`private-chat-e2ee.js:394/398/596`），与 `window.` 属性无关 |
| `iChatFileTransfer._onUploadCompleted` 未导出，`file.upload.completed` 事件无效果 | **已核实，且比原文更严重**：`chat.js:1806-1807` 检查并调用它，但 `file-transfer.js:1061` 的 `window.iChatFileTransfer` 导出对象里**根本没有 `_onUploadCompleted`**（导出清单见 `:1061-1091`）。因此该分支永远不执行，`file.upload.completed` 事件无任何效果 |
| 群聊"永久静音"实为 7 天 | **已核实，是会话右键菜单而非群聊**：`chat-conversation-actions.js:305-309` 的 "Mute forever" 调用 `muteConversation(conv, 10080)`，`10080` 分钟 = **7 天**。与 `muteConversation` 定义在 `:92` 一致 |
| 仓库有 3 个本地提交未推送（`78e7f54`/`452c73c`/`cfe53be`） | `git log origin/main..HEAD`。**这类断言极易过期**：核对期间另一 Agent 连续提交了 `46e2cea`、`d756af4`、`3cc4fc7`、`56d3fde`、`7156453`、`4d179da`，未推送提交数从 3 变成 9，HEAD 也从 `cfe53be` 一路走到 `4d179da`。**不要在别的文档里引用本行数字**，自己跑一次 |

### 本次核对新发现（原表没有）

| 缺口 | 位置 |
| --- | --- |
| **`deploy/smoke.sh` 与 `deploy/tests/` 未提交**，但 `deploy/deploy.sh` 已引用它做门禁 | **已解决**：核对过程中该修复由另一 Agent 提交为 `46e2cea`（含 `deploy/smoke.sh`、`deploy/tests/test_smoke_gate.py`、`deploy/runbook.md`、`deploy/deploy.sh`）。保留此行是因为它说明**并发派工时这类"只在工作树里"的改动极易被误判为已完成/未完成**——判断前先 `git log --oneline -5` |
| `tasks.md` 的 P4 T22 行容易被误读为"已完成" | `docs/phase4/tasks.md` P4 T22 行 vs `gh issue view 149` 为 open。**这不是矛盾**：Issue 开放 + tasks.md 写"部分完成"是自洽的。但该行的产出描述已相当丰富（ADR、骨架、APK 校验值），**不读到后半句就会以为 T22 可以关了**。派工时要强调：缺的是真机运行 |
| `tasks.md` 与 GitHub 状态**其余 77 项完全一致**（已逐项比对） | 复核方法见 §核对记录 |
| **`deploy/tests` 的 7 条冒烟用例在无 `bash` 的 Windows 上静默 skip** | `deploy/tests/test_smoke_gate.py:38` 用 `shutil.which("bash")` 判定。本机默认 PATH 无 bash → `OK (skipped=7)`，**看起来是绿的但其实一条没跑**；把 Git Bash 加进 PATH 后实测 `Ran 7 tests ... OK`（24.4s）。CI 若是 Windows runner 需注意同样问题 |

---

## 核对记录（2026-10-18）

复核者为 **issue-backlog 模块**（文档维护，未改任何业务代码）。目的是把本表从"勘察快照"校正到"可与 `gh` 输出逐行对账"。

### 独立审查记录（conventions.md §1）

| 轮次 | 审查者 | 范围 | 结论 | 发现项 | 处理 |
| --- | --- | --- | --- | --- | --- |
| 1 | 独立事实核查子 Agent（全新上下文，只给断言清单，不给作者说明） | 13 条关键断言：49/29/111 三个总数、P0/P1/P2 分布、7 张卡的依赖、6 条代码断言、门禁 API | **PASS** | 2 条非致命：① `file_unavailable`→410 实为 6 处，本文只列了 4 处；② `onclick=` 的举例行号已漂移 | 均已修：补全 6 处行号；删掉会过期的行号，改成"现数现用"的检索命令 |
| 2 | 独立的**一致性**审查子 Agent（另一个全新上下文，只看文档内部自洽与交付诚实） | 7 个问题：总览/正文矛盾、同卡多处结论冲突、未验证写成通过、行号自相矛盾、锚点有效性、统计自洽、过期断言 | **PASS WITH FINDINGS** | 6 条 MAJOR（`mobile/` 与 ADR 实际**已提交**却仍写成"未提交"；APK **已构建**却写成"未产出"；`cleanup_expired_uploads` 写成"全仓库无任何引用"，实际有 5 处文档引用）+ 4 条 MINOR（自称"唯一过期断言"不成立、`current-status.md` 章节号判断错、未反映 `d756af4`） | 全部修完：`mobile/` 与 APK 状态改按 `3cc4fc7`/`4d179da` 的真实情况重写；`cleanup_expired_uploads` 区分"无引用"与"无调度"；删掉"唯一"的错误量词；纠正 `current-status.md:41` 确实属于 `## 消息可靠性`（第 33 行）；补 `d756af4` |

> 两个审查者都是**自己跑命令/读文件**核对的，**没有看到作者的推理过程**。轮次 1 的核查结果是"事实全对"；轮次 2 发现的是**另一类问题**——事实没错，但**状态已过期**（并发提交把"未提交"变成了"已提交"）。这类偏差方向是"把已做的写成未做"，不会让人误以为完成，但仍会让接手者重复搭骨架，所以按 MAJOR 修。

**教训（值得写进流程）**：在多个 Agent 并发写同一工作树时，"谁提交了什么"是**最容易过期的一类断言**，比数字和行号更容易错。写这类文档时应当：要么在每处标上提交号，要么干脆不写状态、只写"请自己跑 `git log` 确认"。

### 复核口径（可复跑）

```powershell
# 1) 开放/关闭总数与标签分布
gh issue list --repo HeanX/iChat_Pro --state open  --limit 200 --json number,labels,body
gh issue list --repo HeanX/iChat_Pro --state closed --limit 400 --json number,labels

# 2) 优先级 / 前置依赖（直接读 Issue 正文，不要转述）
gh issue view <编号> --repo HeanX/iChat_Pro --json body --jq '.body'

# 3) 合并门禁是否真的生效（#172 的关键证据）
gh api repos/HeanX/iChat_Pro/branches/main/protection

# 4) 最近一次 main 的 CI 结论
gh run list --repo HeanX/iChat_Pro --limit 8
gh run view 37636022966 --repo HeanX/iChat_Pro --json jobs --jq '.jobs[] | {name, conclusion}'
```

### 复核结果

| 项目 | 原表 | 复核值 | 结论 |
| --- | --- | --- | --- |
| 开放 Issue 总数 | 49 | **49**（全部 `phase-4`） | ✅ 一致 |
| Phase4 已关闭 | 29 | **29**（`phase-4` 历史共 58 项 → 49 开放 + 29 关闭；仓库另有 82 个非 phase-4 关闭 Issue） | ✅ 一致（重要区分：`gh` 的 closed 总数是 111，不是 29） |
| 优先级分布 | P0 28 / P1 19 / P2 2 | **P0 28 / P1 19 / P2 2** | ✅ 一致 |
| 未合并 PR | 0 | **0** | ✅ 一致 |
| 远端最后活动 | 2026-10-07T14:21:32Z | **2026-10-07T14:21:32Z**（`pushed_at`） | ✅ 一致 |
| 无 P0/P1/P2 标签 | 是 | **是**（标签只有 `phase-4`/`enhancement`/`testing`/`desktop`/`frontend`/`tooling`/`documentation`/`realtime`/`backend`） | ✅ 一致 |
| 各 WP 开放/总数 | WP00 1/5 … WP08 3/5 | **逐项一致** | ✅ 一致 |
| WP04"全部未开始" | 是 | **否**（T22 已有 ADR + `mobile/` 骨架 + APK，均已提交：`3cc4fc7`/`4d179da`） | ❌ 已改 |
| 依赖列 | 见各梯队 | **10 处需订正**：#193、#150、#165、#174、#149、#167–#170、#189、#199 | ❌ 已改 |
| 不依赖 Android 的两组项数 | 13 项 | **12 项**（5 + 7） | ❌ 已改 |
| "已知缺口"代码行号 | 9 条 | **4 条有误/漂移**：#145 的 `models.py:71-73` 指向的是枚举不是"没写入"、`current-status.md` 章节号错、mute 菜单文件写错、"约 20 处"实为 21 处 | ❌ 已改 |

### 本轮实际执行过的命令（原始结果）

```text
# 1) 冒烟门禁用例（默认 PATH，无 bash）
.\.venv\Scripts\python.exe manage.py test deploy.tests.test_smoke_gate --parallel 1
Found 7 test(s).
System check identified no issues (0 silenced).
sssssss
Ran 7 tests in 0.000s
OK (skipped=7)          <-- 一条都没跑，但显示 OK

# 2) 同一命令，先把 Git Bash 加进 PATH
$env:PATH = "C:\Program Files\Git\bin;" + $env:PATH
.\.venv\Scripts\python.exe manage.py test deploy.tests.test_smoke_gate --parallel 1
Found 7 test(s).
System check identified no issues (0 silenced).
.......
Ran 7 tests in 24.427s
OK                      <-- 真跑，7/7 通过
```

> 上面第 1 条是本次最重要的可复用教训：**`OK (skipped=7)` 不是通过**。同类问题在 `conventions.md` §3"测试数字的唯一口径"里已有原则，但这里给了一个具体可复现的实例。

### 明确未验证的范围

- **生产服务器状态未复验**：`f2f60ec` 部署、TLS、证书续期 hook 均来自文档转述。
- **#145 的原生验收未执行**：本机未装该安装版，无法登录操作通知；本轮只核对了 Issue 评论里的既有证据。
- **#204 的生产验证未执行**：冒烟门禁只在本地 SQLite + Django live server 验证过（7/7），生产 `.env` 尚无冒烟账号。
- **#172 关单口径未定**：只能证明 `required_status_checks` 为空，无法替项目决定"加上 checks 是否即满足该卡验收"。
- **`mobile/` 的真机行为未验证**：`mobile/` 与 ADR-P4-03 已提交（`3cc4fc7`），`app-debug.apk` 也已构建成功（4.22 MB，SHA256 `0D05C92B…4B4976`），但**从未安装到真机或模拟器上运行**（`adb devices` 为空、无 emulator 包、无 Hyper-V），WebView 复用现有页面的运行时验证为 0 次。所以 T22 的"能复用现有页面并生成 Android 工程"只完成了"生成工程"这一半。
- **本轮未运行 Django 全量测试或 `npm run test:e2ee`**：本次只改文档，未触碰业务代码，因此全量回归不在范围内；只执行了与本次结论直接相关的 `deploy.tests.test_smoke_gate`。

### 并发说明（务必先读）

本次核对全程，**同一工作树里有多个 Agent 在并行写入**。实测在 10 分钟内观察到：

- 工作树文件被反复改写：`deploy/smoke.sh`、`deploy/tests/`、`mobile/`、`chat/tests/test_ciphertext_boundary.py`、`docs/phase4/tasks.md`、`deploy/deploy.sh`、`static/js/chat.js`；
- 期间新增了提交 `46e2cea`（smoke gate），使本表对 #204 的结论在核对过程中发生了变化；
- 行号因此不稳定。本表所有代码行号都是**核对当时**的值，`git rev-parse HEAD` 当时为 `46e2cea`。

**给下一个接手者的要求**：

1. 先 `git status --short` + `git log --oneline -5`，确认当前 HEAD 与未提交改动归属；
2. 不要把别人未提交的改动当成自己的成果，也不要在并发期间 patch 同一文件；
3. 本表与 `tasks.md` 冲突时，**以 `gh issue view` 与 `git log` 为准**，不要反过来。

---

## 附录：49 张卡的业务验收项

> 全部来自 `gh issue view <编号> --json body` 的 `## 验收标准` 段第 1 条，**原文照抄，未改写**（2026-10-18 取）。每张卡另有固定 3 条模板项（非作者评审 / 测试留证据 / 文档同步），见 §总览。派工时直接把这行连同 Issue 原文一起交给接手者。

| Issue | 任务 | 业务验收项（原文第 1 条） |
| --- | --- | --- |
| [#130](https://github.com/HeanX/iChat_Pro/issues/130) | 制定迭代计划和风险清单 | 范围、负责人、时间和降级方案明确 |
| [#139](https://github.com/HeanX/iChat_Pro/issues/139) | 配置健康检查、日志和监控 | 能发现服务、资源、证书和关键错误状态 |
| [#145](https://github.com/HeanX/iChat_Pro/issues/145) | 接入 Windows 系统通知 | 新消息通知遵守静音设置，点击可定位会话 |
| [#146](https://github.com/HeanX/iChat_Pro/issues/146) | 完善文件选择、下载和打开 | 文件跨端收发成功，异常路径有提示 |
| [#149](https://github.com/HeanX/iChat_Pro/issues/149) | 确认 Capacitor 等移动端技术方案 | 能复用现有页面并生成 Android 工程 |
| [#150](https://github.com/HeanX/iChat_Pro/issues/150) | 完成移动端响应式适配 | 核心操作无溢出、遮挡或鼠标悬停依赖 |
| [#151](https://github.com/HeanX/iChat_Pro/issues/151) | 生成 Android APK/AAB | 真机或独立模拟器安装并启动 |
| [#152](https://github.com/HeanX/iChat_Pro/issues/152) | 适配返回键和应用生命周期 | 后台恢复不串号、不重复发送、不丢失草稿 |
| [#153](https://github.com/HeanX/iChat_Pro/issues/153) | 实现网络变化检测和重连 | Wi-Fi/移动网络切换后自动恢复聊天 |
| [#154](https://github.com/HeanX/iChat_Pro/issues/154) | 接入前台/后台通知 | 权限申请、静音和点击跳转正确 |
| [#155](https://github.com/HeanX/iChat_Pro/issues/155) | 接入文件选择和保存 | 权限拒绝、取消和有效文件操作均有正确结果 |
| [#156](https://github.com/HeanX/iChat_Pro/issues/156) | 接入 Android 安全存储 | 普通存储、日志中无法直接读取敏感值 |
| [#157](https://github.com/HeanX/iChat_Pro/issues/157) | 研究完全退出后的云推送 | 不泄露 E2EE 明文，失败不影响消息持久化 |
| [#164](https://github.com/HeanX/iChat_Pro/issues/164) | 完成 Windows—Android 跨端聊天 | 两台独立设备双向文本消息通过 |
| [#165](https://github.com/HeanX/iChat_Pro/issues/165) | 编写 E2EE 威胁模型和数据流 | 明确资产、攻击面、信任边界和不可保存数据 |
| [#166](https://github.com/HeanX/iChat_Pro/issues/166) | 适配平台安全存储 | 普通存储或日志中无可直接读取私钥 |
| [#167](https://github.com/HeanX/iChat_Pro/issues/167) | 设计并实现设备身份模型 | 同一账号不同设备可独立识别 |
| [#168](https://github.com/HeanX/iChat_Pro/issues/168) | 实现设备列表与撤销 | 被撤销设备不接收后续消息密钥 |
| [#169](https://github.com/HeanX/iChat_Pro/issues/169) | 实现多设备新消息密钥分发 | 每个有效设备可解密新消息 |
| [#170](https://github.com/HeanX/iChat_Pro/issues/170) | 研究历史密钥迁移 | 仅经用户授权，失败和撤销路径明确 |
| [#171](https://github.com/HeanX/iChat_Pro/issues/171) | 接入静态检查和格式门禁 | 检查失败时流水线失败 |
| [#172](https://github.com/HeanX/iChat_Pro/issues/172) | 接入 Django、迁移和自动化测试 | 任一核心测试失败时禁止合并 |
| [#173](https://github.com/HeanX/iChat_Pro/issues/173) | 接入覆盖率门禁 | 低于阈值时阻断或产生审批例外记录 |
| [#174](https://github.com/HeanX/iChat_Pro/issues/174) | 接入依赖与秘密扫描 | 高危漏洞或密钥泄露阻断发布 |
| [#175](https://github.com/HeanX/iChat_Pro/issues/175) | 自动/标准化构建客户端 | 安装包带版本号、校验值和构建记录 |
| [#176](https://github.com/HeanX/iChat_Pro/issues/176) | 部署独立测试环境 | 与生产配置、数据和密钥隔离 |
| [#177](https://github.com/HeanX/iChat_Pro/issues/177) | 建立生产发布审批与回滚 | 可追踪发布人、版本、时间并可恢复稳定版本 |
| [#180](https://github.com/HeanX/iChat_Pro/issues/180) | 整理安装、云端和跨端证据 | 每项 P0 成果有可复核证据 |
| [#181](https://github.com/HeanX/iChat_Pro/issues/181) | 整理最终实验材料 | 文档、代码和实测结果一致 |
| [#184](https://github.com/HeanX/iChat_Pro/issues/184) | 编写 Windows 安装/E2E 测试 | 安装、核心聊天、通知、文件和恢复场景通过 |
| [#185](https://github.com/HeanX/iChat_Pro/issues/185) | 编写 Android UI 与兼容性测试 | 两个 API Level 或设备配置完成核心流程 |
| [#186](https://github.com/HeanX/iChat_Pro/issues/186) | 完成群聊和文件跨端验证 | 群消息和加密文件在各端结果一致 |
| [#187](https://github.com/HeanX/iChat_Pro/issues/187) | 复核三端加密协议兼容性 | Windows、Android、浏览器对相同向量结果一致 |
| [#188](https://github.com/HeanX/iChat_Pro/issues/188) | 审计服务端密文边界 | 无消息明文、私钥和文件密钥明文 |
| [#189](https://github.com/HeanX/iChat_Pro/issues/189) | 验证密钥指纹和变化提醒 | 新密钥不自动继承旧信任状态 |
| [#190](https://github.com/HeanX/iChat_Pro/issues/190) | 编写总体测试计划 | 范围、环境、方法、人员、准入/准出标准完整 |
| [#191](https://github.com/HeanX/iChat_Pro/issues/191) | 设计黑盒测试 | 包含等价类、边界值、判定表、状态和场景法 |
| [#192](https://github.com/HeanX/iChat_Pro/issues/192) | 设计白盒测试 | 覆盖关键条件、分支、循环和异常路径 |
| [#193](https://github.com/HeanX/iChat_Pro/issues/193) | 建立覆盖率统计 | 语句/分支覆盖率可重复生成并达到评审目标 |
| [#194](https://github.com/HeanX/iChat_Pro/issues/194) | 完善 HTTP/WebSocket 集成测试 | P0 接口成功、异常、边界和越权场景有覆盖 |
| [#196](https://github.com/HeanX/iChat_Pro/issues/196) | 建立浏览器/Electron E2E | 失败保留截图、日志或跟踪文件 |
| [#197](https://github.com/HeanX/iChat_Pro/issues/197) | 建立 Android UI 自动化 | 安装、权限、登录、聊天和生命周期可执行 |
| [#198](https://github.com/HeanX/iChat_Pro/issues/198) | 执行安全测试 | 高危缺陷清零，中危缺陷有处理结论 |
| [#199](https://github.com/HeanX/iChat_Pro/issues/199) | 执行性能和稳定性测试 | 记录环境、负载、P50/P95/P99、错误率和资源 |
| [#200](https://github.com/HeanX/iChat_Pro/issues/200) | 执行弱网和故障测试 | 延迟、丢包、断网、重启和恢复场景有证据 |
| [#201](https://github.com/HeanX/iChat_Pro/issues/201) | 执行兼容性测试 | 指定环境核心流程均有结论 |
| [#203](https://github.com/HeanX/iChat_Pro/issues/203) | 编写最终测试总结 | 数据可由原始执行结果复核，无虚构结果 |
| [#204](https://github.com/HeanX/iChat_Pro/issues/204) | 部署后冒烟测试 | 健康、登录和核心接口失败时阻断发布 |
| [#205](https://github.com/HeanX/iChat_Pro/issues/205) | AI 辅助需求与用例评审 | AI 建议经人工确认，不直接作为结论 |
