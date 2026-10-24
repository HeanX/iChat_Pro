# P4 T19：Windows 原生文件选择、下载与打开

> Issue：[#146](https://github.com/HeanX/iChat_Pro/issues/146)，优先级 P1，工作域 E2。
> 实现日期 2026-10-18；基线 `cfe53be`，分支 `fix/p4-t19-native-files`，提交 `94646b7`。
> 验收项原文：**文件跨端收发成功，异常路径有提示**。

---

## 1. 改了什么、为什么

改造前桌面端没有任何文件系统集成。收到文件后，`chat.js` 用一个 `<a download>` 触发下载；在 Electron 里这个锚点绑定在应用源上，落盘位置由浏览器下载管理器决定，用户既看不到也没法确认文件到底有没有写下去。发送侧本来就是 `<input type=file>`，在桌面端可用，所以本卡只处理"接收 → 落盘 → 打开"。

新增一层**窄 IPC**，刻意不做通用文件读写：

| 通道 | 方向 | 入参 | 说明 |
| --- | --- | --- | --- |
| `ichat:files:pick-save-target` | invoke | `{fileName, totalBytes}` | 打开系统保存对话框，返回一次性授权令牌 |
| `ichat:files:write-chunk` | invoke | `{token, offset, dataBase64}` | 追加写入，偏移必须等于已写入字节数 |
| `ichat:files:seal` | invoke | `{token}` | 校验声明大小已到齐，收尾 |
| `ichat:files:release` | invoke | `{token}` | 放弃本次下载并归还配额（失败重试时调用） |
| `ichat:files:open-downloaded` | invoke | `{token}` | `shell.openPath`，只认令牌，不认路径 |

**返回值约定（审查第 1 轮 BLOCKER 后修改）**：`ipcMain.handle` 跨进程只透传 `message`，自定义 `code` 会丢（Electron 39.8.10 实测：渲染端只拿到 `Error invoking remote method '...': Error: write_failed`）。因此预期失败一律 **resolve** 为 `{ok:false, code, detail}`，成功为 `{ok:true, value}`；只有未受信发送者与程序缺陷才 reject。渲染端在 `callNativeFileBridge` 里拆包并复原 `code`，中文提示才真正可达。

**没有**：任意路径读、任意路径写、目录列举、删除、重命名。渲染端从头到尾拿不到一个可写的路径。

代码分布：

```
desktop/file-io.js       +456 行  访问策略（纯 Node 可测，不 import electron）
desktop/main.js          +116 行  五条 IPC + 对话框 + shell.openPath
desktop/preload.js        +17 行  iChatDesktop.files.{pickSaveTarget,writeChunk,seal,release,openDownloaded}
static/js/file-transfer.js +169 行 saveBlobToDisk / openSavedFile / nativeSaveAvailable
static/js/chat.js        +156/-48 行 saveOrDownloadBlob / offerOpenSavedFile 与两处调用点
```

---

## 2. 安全模型（不可放宽的部分）

1. **路径只来自系统对话框。** `createSaveGrant` 的 `filePath` 是 `dialog.showSaveDialog` 的返回值，渲染端无法提供。覆盖同名文件由系统自带的"是否替换"确认。
2. **一个目标路径同时只有一个写入者。** 活动授权独占其规范化路径（Windows 下大小写不敏感），第二个下载选同一文件会被 `target_in_use` 拒绝；成功收尾的旧授权在路径被新下载接管时立即失效，避免"打开"指向别人刚写的内容。**占用不会赖到 30 分钟 TTL**：活动授权若 5 分钟没收到任何分块，按"渲染端已消失"回收（`STALE_ACTIVE_GRANT_MS`），路径与配额立即释放。识别不到的是符号链接、硬链接与 8.3 短名别名——这属于文档边界，不属本卡要拦的"我们自己的两个下载互相覆盖"。

3. **令牌是 128-bit 随机 UUID，并绑定 `webContents.id`。** 同一应用内另一个 webContents 拿到令牌写不进去（`wrong_sender`）。注意 `event.sender.id` 是**窗口级**标识：同一窗口里的子 frame 共享它。子 frame 默认不加载 preload、拿不到桥，所以当前不构成可利用缺口；若要 frame 级隔离需改用 `senderFrame.routingId`，本卡未做。
3. **只追加且必须连续。** `offset !== writtenBytes` 直接 `offset_mismatch`，所以既不能跳写、不能回放、也不能预分配制造空洞文件。
4. **声明大小强制。** `seal` 要求 `written === total`，否则 `incomplete`；单块上限 2 MiB，总大小上限 100 MiB（与 `file-transfer.js` 的 `MAX_FILE_SIZE` 一致）。
5. **令牌会过期、有数量上限。** 30 分钟 TTL；同时最多 8 个**尚未收尾**的授权（已 `seal` 的下载不再占配额，否则 30 分钟内保存第 9 个文件就会无故失败）。已收尾的授权继续保留，供“打开”使用直到 TTL 到期。
6. **打开只认令牌。** `openTarget` 从令牌反查路径，并要求该授权已 `seal`、大小吻合、文件仍存在。
7. **打开走白名单，不是黑名单。** 只有已知的文档/**被动**图片/音视频/压缩包类型允许 `shell.openPath` 一键打开；其余一律保存成功但拒绝自动打开（`blocked_type`）。**`.svg` 与 `.xml` 都不在白名单**：本地 SVG 被 Shell 打开时会在 `file://` 起源里执行自带脚本（Electron 39.8.10 实测可改 `document.title`）；`.xml` 能携带 XHTML 命名空间的 `<script>`，而系统对 XML 的默认关联程序可能是浏览器。两者都仍是活动内容，应用内预览照常渲染 SVG，只是不替用户去启动它们。
   黑名单在这里是错的形状：文件名来自对端加密元数据，而 Windows Shell 会执行的东西远比 `.exe/.bat` 多——`.scf`、`.url`、`.pyw`、`.py`、`.sh`、`.pl`、`.rb` 都属于"双击即执行"。枚举不完，所以反过来只放行确定的文档类型。这仍然不阻止用户自己在资源管理器里打开这些文件，只是**应用不会替他执行对端代码**。
8. **文件名净化。** 去掉目录部分、Win32 非法字符替换为 `_`、去掉尾随点与空格、`CON/PRN/AUX/NUL/COM1-9/LPT1-9` 加前缀、长度截到 180 且保留扩展名。

### 已知边界（不是缺陷，但必须知道）

- 本方案保护的是**路径与大小**，不是**内容**：解密后的字节由渲染端产出，主进程不校验哈希。完整性由 AES-GCM 的 auth tag 在上游保证。
- 令牌在内存里，应用重启即失效；已保存的文件仍在磁盘上，但"打开"按钮不再有效。
- 主进程不校验写入内容与文件扩展名是否匹配。这是刻意的：用户选什么名字就写什么名字。
- 覆盖已有文件时按**绝对偏移**写入（首块 `w+` 会让旧文件先被清空，后续 `r+` 定位）。截断语义完全由首块承担，`seal` 不再重复截断。选择这个行为而不是"写临时文件再改名"，是为了让"用户选的目标路径"始终等于"磁盘上的文件"；代价是中途失败会留下部分内容（不会回滚到旧文件），此时按钮显示 `Retry` 而不是 `Done`。

---

## 3. 失败路径与提示文案

**分层证据**：策略层的每个错误码都有断言；错误码到中文文案的映射有断言；按钮三态（`Done`/`Retry`/`Download`）有 DOM 级断言。真实 toast 在安装版里的观感属于未验证项（见 §5）。

| 触发 | 错误码 | 用户看到的文案 |
| --- | --- | --- |
| 用户在对话框点取消 | — | 按钮回到 `Download`，不弹提示、不报错 |
| 磁盘满 / 文件被占用 | `write_failed` | 写入失败：磁盘已满或文件被占用。 |
| 偏移对不上 / 主进程没推进 | `offset_mismatch` | 写入位置不一致，已中止，请重新下载。 |
| 令牌过期 | `expired` | 下载超时，请重新下载。 |
| 令牌不存在（重启后） | `not_found` | 上次的保存凭证已失效，请重新下载。 |
| 目标是安装包/脚本 | `blocked_type` | 出于安全考虑，安装包和脚本不能直接打开，请在下载目录中查看。 |
| 系统没有关联应用 | `open_failed` | 打开失败：系统没有关联的应用。文件已保存，可在下载目录中打开。 |
| 大小没到齐就收尾 | `incomplete` | 文件没有接收完整，已中止，请重新下载。 |
| 同时收尾的授权过多 | `too_many_grants` | 同时进行的下载过多，请等待其他下载完成或稍后重试。 |
| 文件超过上限 | `too_large` | 文件超过允许的大小上限，无法保存。 |
| 对话框已打开 | `dialog_in_progress` | 已有一个保存对话框打开，请先处理它。 |
| 同一路径已有下载在写 | `target_in_use` | 该文件正在被另一个下载写入，请等它完成、换一个文件名，或稍后再试。 |

`write_failed` 时**不推进偏移**，且写入是定位写，所以修复磁盘空间后重试同一偏移会**覆盖**而不是**追加**那部分字节（有断言：写一半失败后重试，文件长度与内容都正确）。

---

## 4. 本地验证（本机 Windows 实测，2026-10-18）

环境：Windows 10.0.26200 / Node v24.14.1 / Electron 39.8.10 / NSIS 3.0.4.1（electron-builder 缓存）。

| 命令 | 结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe manage.py check` | `System check identified no issues (0 silenced).` |
| `.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run` | `No changes detected` |
| `npm run test:e2ee` | **111 tests / 111 pass / 0 fail**（改造前同一基线为 62 pass，本卡新增 49 条） |
| `cd desktop && npm run test:layout` | `Sidebar layout: 36/36 browser checks passed` |
| `cd desktop && npm run test:branding` | 打包 asar 内 **71 tests 全通过**、`Native PE icon: 7 source frames matched exactly`。注意：新增的 `desktop_file_io` 套件读的是**源码**（不认 `ICHAT_DESKTOP_TEST_ARCHIVE`），asar 内只验证到 `file-io.js` 确实被打包 |
| `cd desktop && npm run test:installer` | `NSIS runtime: 24/24 assertions passed` |
| `cd desktop && npm run test:files` | **49 tests / 49 pass**（第 1 轮审查发现该入口曾指向不存在的文件） |
| 真实 Electron 39.8.10 复核（`verify-fixes.cjs`） | F1 第二个下载被 `target_in_use` 拒绝、落盘 3 MiB 全属 A；F1 旧令牌 `not_found`；F2 11 MiB 图片 `nativeCalls=1` 且预览仍显示；F3 旧 URL 给旧模块、新 URL 给新模块；F4 `.xml` 保存成功但 `blocked_type`；F5 回报名 = 磁盘名 |

新增套件 `chat/tests/js/desktop_file_io.test.js` 共 49 条，分四层：

1. **策略层**（`desktop/file-io.js`）：真实临时目录里写字节，覆盖文件名净化、严格字节数解析、顺序追加、越界、重放、过期、绑定发送者、授权上限、失败不推进偏移、**写一半失败后重试不重复追加**、**覆盖更长旧文件后按声明大小截断**、空文件、**白名单外一律拒绝打开**（`.scf/.url/.pyw/.py/.sh/.pl/.rb/.exe/'无扩展名'/双扩展名`）、**已 seal 不占配额**、**失败后可释放配额**、**对话框路径尾随点/空格不绕过白名单（且合法文档仍可打开）**。
2. **主进程层**（真实 `main.js` 在 vm 沙箱里跑，只替换 OS 网络/对话框）：五条通道注册（含 `release`）、非受信 frame 拒绝、**校验先于弹框**、取消不建授权、同 frame 并发对话框拒绝、端到端（对话框→两块→seal→打开）、跨 sender 拒绝、可执行不调用 `openPath`、`openPath` 返回错误串时上报。
3. **桥与渲染层**（真实 `preload.js` + `static/js/file-transfer.js`）：无桥时退回原行为、5 MiB 分 3 块且偏移为 `0/2/4 MiB`、字节可按序重组且完全相等、截断写入被拒绝、**`{ok:false,code}` 信封拆包**、错误码到中文文案的映射、0 字节文件仍走原生保存、**失败时释放授权**、释放本身失败不掩盖原始错误。
4. **按钮与下载路径**（真实 `static/js/chat.js` 的 `offerOpenSavedFile` / `runFileDownload`，最小 DOM 桩）：`saved` 才显示 `Done` 并附“打开”、`failed` 显示 `Retry` 且不保留 `downloaded` 样式、取消回到 `Download`、重复下载不叠加“打开”按钮；**11 MiB 图片必须走到原生保存**（`nativeCalls === 1`）。
5. **资源版本**（读 `templates/pages/chat.html`）：三个改动过的静态资源都必须带 `2026…-t19` 版本参数，且 JS 与 CSS 共用同一版本标签；否则 nginx 的 `expires 7d` 会让旧客户端继续用旧逻辑。
6. **同路径独占**（策略层）：第二个活动下载在创建时被 `target_in_use` 拒绝，落盘内容只属于第一个下载；新下载接管路径后旧“打开”令牌失效（`not_found`）。

---

## 5. 尚未验证（明确未验证，不是"通过"）

以下需要**安装版 + 真实账号 + 真人操作对话框**，本机自动化无法替代：

- [ ] 安装版登录后接收一个真实文件，点下载 → 系统保存对话框弹出 → 保存 → 资源管理器里字节与发送端一致
- [ ] 保存后点"打开"，默认应用正确拉起（含中文文件名、含空格路径）
- [ ] 对话间传输 > 2 MiB（需要多块）的真实文件
- [ ] 100 MiB 上限与磁盘满的用户体感（真实满盘环境）
- [ ] `blocked_type` 的文案在真实 toast/UI 中的呈现
- [ ] 跨端：Android 发 → Windows 保存（依赖 #155/#186，未做）
- [ ] 与 #145 的原生验收一起在安装版上复跑一遍

**这 7 项没有证据，不得写成通过。** 另注：`test:layout` 是 Chromium fixture，`test:branding` 是打包产物校验，二者都不等于登录态安装版实测。

---

## 6. 静态资源与客户端缓存（升级路径）

`deploy/nginx/ichat.conf` 对 `/static/` 设 `expires 7d`，且仓库没有按内容哈希命名的静态资源。这意味着**改了 JS 却不改 URL，旧客户端可能七天都用旧逻辑**——T19 第一版就踩了这个坑（用户审查 F3）：`file-transfer.js` 与 `chat.js` 都改了，模板还带 `?v=20261003-t20b` / `?v=20261005-t18-review1`，安装版沿用原用户缓存时新能力不会出现。

本轮把三个改动过的资源统一升到 `?v=20261024-t19-files`：

| 资源 | 位置 |
| --- | --- |
| `static/js/file-transfer.js` | `templates/pages/chat.html:332` |
| `static/js/chat.js` | `templates/pages/chat.html:338` |
| `static/css/chat.css` | `templates/pages/chat.html:7` |

约束：`desktop_file_io.test.js` 里有一条测试读取模板，要求这三个资源都带 `2026…-t19` 版本参数且 JS/CSS 共用同一版本标签。**下次改动这些文件时必须同时改版本号**，否则测试会失败——这是故意留的闸门。

长期方案（不在本卡范围）：改用内容哈希文件名（Django `ManifestStaticFilesStorage`），从根上消除“忘记改版本号”的可能。

---

## 7. 兼容与回滚

- 浏览器端行为完全不变：没有 `window.iChatDesktop.files` 时 `nativeSaveAvailable()` 返回 `false`，`saveOrDownloadBlob` 走原来的 `<a download>`。
- 老版本客户端连新服务端、新客户端连旧服务端都没有关系：本卡不涉及任何服务端协议、接口、模型或迁移改动（`makemigrations --check` 为 `No changes detected`）。
- 回滚：revert 该提交即可。已保存到用户磁盘的文件属于用户数据，不随回滚删除。
- 打包白名单新增 `file-io.js`；`test:branding` 现在会断言该文件确实进了 asar，漏打包会直接失败而不是在首次保存时才发现。

---

## 8. 独立审查记录

按 [agent-handoff.md](agent-handoff.md) 所列的强制流程，实现完成后由**独立上下文的子 Agent**复核（只给 Issue 原文、diff 路径与命令清单，不给作者说明）。

| 轮次 | 审查者 | 结论 | BLOCKER / MAJOR / MINOR | 修复提交 |
| --- | --- | --- | --- | --- |
| 1 | 独立子 Agent（无作者上下文） | PASS WITH FINDINGS | 3 / 8 / 5 | `8b3038a` |
| 2 | 另一个独立子 Agent（全新上下文，不复用第 1 轮） | FAIL | 1 / 3 / 5 | `78f1d13` |
| 3 | 第三个独立子 Agent（全新上下文，不复用前两轮） | PASS WITH FINDINGS | 0 / 3 / 4（另 1 条 MINOR 遗留到第 4 轮） | 4549f80 |
| 4 | 第四个独立子 Agent（收尾轮，逐条核实第 3 轮遗留项） | PASS WITH FINDINGS | 0 / 0 / 3（均为文档级） | 本提交 |
| 5 | 用户复审（2026-10-24，附复现脚本） | FAIL | 1 / 3 / 1 | `585613b` |
| 6 | 第五个独立子 Agent（聚焦复核 5 项修复） | PASS WITH FINDINGS | 0 / 0 / 1 | 本提交 |

### 第 1 轮发现与处置

| 严重度 | 问题 | 处置 |
| --- | --- | --- |
| BLOCKER | `ipcMain.handle` 跨进程丢弃自定义 `err.code`，6 个中文提示全部不可达（审查者用真实 Electron 39.8.10 探针实测） | 改为 resolve `{ok:false, code, detail}` 信封；渲染端拆包复原 `code`。已用变异测试确认修复前测试会失败 |
| BLOCKER | 保存失败仍显示绿色 `Done` | `offerOpenSavedFile` 只在 `saved` 时显示 `Done`，失败显示 `Retry`；补 DOM 级断言 |
| BLOCKER | 已 `seal` 的授权仍占配额，30 分钟内第 9 次保存无故失败 | 配额只统计**未收尾**授权 |
| MAJOR | `desktop/package.json` 的 `test:files` 指向不存在的文件 | 改为 `node --test ../chat/tests/js/desktop_file_io.test.js` |
| MAJOR | 测试用会抛 `code` 的假桥，掩盖了真实 IPC 语义 | 假桥改为返回信封，与生产一致 |
| MAJOR | `chat.js` 新增函数零覆盖 | 补按钮状态机 DOM 测试（saved / failed / canceled / 重复下载） |
| MAJOR | 0 字节文件在三层判定矛盾 | 统一走原生保存，补断言 |
| MAJOR | 分块追加在"写一半失败"后会重复写入 | 改绝对偏移定位写 + 收尾按声明大小截断；补"二次写失败后重试"用例 |
| MAJOR | 文档称令牌绑定 frame，实际是 webContents 级 | 更正措辞并说明子 frame 默认无 preload |
| MAJOR | 文档死链、README/索引未同步 | 修死链，同步 `docs/README.md`、`docs/phase4/README.md`、`docs/documentation-status.md` |
| MINOR | `.file-open-btn` 无样式 | 在 `chat.css` 补齐（含 hover/disabled） |
| MINOR | `evil.exe ` 尾随空格可绕过打开限制 | 取扩展名前按 Win32 规则归一化 |
| MINOR | 多个错误码缺中文文案 | 补齐 8 个映射 |
| MINOR | 图片查看器下载不走统一后处理 | 与文件气泡共用 `saveOrDownloadBlob` + 打开入口 |
| MINOR | 覆盖已有文件后失败不回滚 | 文档明示该行为，按设计保留（不留临时文件） |

第 1 轮后重新真跑全部 7 条命令，均通过（见 §4）。

### 第 2 轮发现与处置

第 2 轮审查者用**变异测试**（把仓库复制到临时目录逐项改动，验证测试是否会失败）发现第 1 轮的三个修复里有两个没有回归保护，并找出一个更严重的架构问题：

| 严重度 | 问题 | 处置 |
| --- | --- | --- |
| BLOCKER | 打开黑名单不完整：`.scf/.url/.pyw/.py/.sh/.pl/.rb` 全部放行，对端可用一个文件名换来一键执行 | 改为**白名单**（仅文档/图片/音视频/压缩包）；补 29 个拒绝用例 + 6 个放行用例 |
| MAJOR | 写入失败后授权永久占配额，连续 8 次失败后 30 分钟内所有保存都失败 | 新增 `ichat:files:release`，渲染端失败时归还；补 3 条断言 |
| MAJOR | 浏览器版下载成功后按钮被重置回 `Download`（与 §6"行为不变"矛盾） | `unsupported` 分支保留 `Done`；断言**当时未补上**，第 3 轮发现后已补（分支抽成 `applySaveResultToDownloadButton`） |
| MAJOR | 文档声称的"已 seal 不占配额""尾随空格绕过"实际无测试（变异存活） | 两条都补了能真正失败的用例 |
| MINOR | 图片查看器的"打开"按钮被 append 到游离 div，永不显示 | 查看器只保留保存结果提示（图片已在屏幕上） |
| MINOR | 代码量表、测试条数、"打包 asar 内 57 tests"表述过期/不准 | 全部按 `git diff --numstat` 与实测输出更正；明确新增套件读源码而非 asar |
| MINOR | 链接文字 `conventions.md` 指向 `agent-handoff.md` | 更正 |

第 2 轮后再次真跑 7 条命令：`test:e2ee` 97/97、`test:files` 35/35、`test:branding` 57/57、`test:layout` 36/36、`test:installer` 24/24，`check` 与 `makemigrations --check` 无问题。

### 第 3 轮发现与处置

第 3 轮用真实 Electron 探针确认前两轮修复在生产代码中确实成立（白名单拦截、配额释放、信封语义、按钮三态），剩下的是**测试回归保护缺口**与**文档把没测的写成测了**：

| 严重度 | 问题 | 处置 |
| --- | --- | --- |
| MAJOR | `ichat:files:release` 未列入通道断言，删掉注册仍 35/35 全绿 | 通道列表补上 release，并断言释放后令牌失效 |
| MAJOR | 文档称浏览器 `unsupported` 分支"已补断言"，实际没有；变异存活 | 分支抽成 `applySaveResultToDownloadButton` 并补 DOM 断言 |
| MAJOR | 白名单放行 `.svg`，而本地 SVG 被 Shell 打开会执行自带脚本 | 移出白名单；补断言；应用内预览仍渲染 SVG |
| MINOR | IPC 表漏 release | 补上 |
| MINOR | 图片查看器保存成功弹两次 toast | 去掉重复提示 |
| MINOR | 测试头注释写"Three layers"实为四层 | 更正 |
| MINOR | seal 里的 `truncateSync` 分支不可达（首块 `w+` 已截断） | 删除死代码 |

第 3 轮后真跑 7 条命令：`test:e2ee` 100/100、`test:files` 38/38、`test:branding` 60/60、`test:layout` 36/36、`test:installer` 24/24，`check` 与 `makemigrations --check` 无问题。第 3 轮存活的三条变异（关掉 `unsupported` 分支、删除 release 注册、SVG 放回白名单）现已各自被抓到。

### 第 4 轮（收尾轮）发现与处置

第 4 轮逐条核实第 3 轮遗留项：A/B/C 三条 MAJOR 全部确认关闭，且各自有能失败的变异测试保护；D/E/F/G 均已落实；H 的守卫存在且 `finally` 正确递减。剩余 3 条均为文档级：

| 严重度 | 问题 | 处置 |
| --- | --- | --- |
| MINOR | 文中仍写"四条通道"（实际 5 条），与同文档"五条 IPC"矛盾 | 改为五条 |
| MINOR | 仍称 `seal` 会"按声明大小截断"，而该分支已删 | 改为截断由首块 `w+` 承担 |
| MINOR | 第 3 轮计数 `0/3/5` 与实际表格（3 MAJOR + 4 MINOR）不符 | 改为 `0/3/4` 并注明 H 遗留到第 4 轮 |
| MINOR | B 的回归保护只到函数级：删掉/改回生产调用点仍全绿 | 增加源码级接线断言（统计出现次数，必须恰好 2 次 = 定义 + 调用），两种变异均已抓到 |

第 4 轮后真跑 7 条命令：`test:e2ee` 101/101、`test:files` 39/39、`test:branding` 61/61、`test:layout` 36/36、`test:installer` 24/24，`check` 与 `makemigrations --check` 无问题。

### 第 5 轮（用户审查，2026-10-24）发现与处置

用户按 `47772d2` 复审，提出 1 项 P1、3 项 P2、1 项 P3，并附可复现脚本。审查时 101 条自动化仍全绿——说明这五项都在既有测试的盲区里。

| 编号 | 级别 | 问题 | 处置 |
| --- | --- | --- | --- |
| F1 | P1 | 同路径两个授权混写文件：A 首块 + B 首块 + A 尾块，两个 seal 都成功 | 活动授权独占规范化路径（Windows 大小写不敏感、忽略尾随分隔符）；新下载接管路径时废弃旧的已收尾授权；新增 `target_in_use` |
| F2 | P2 | >10 MiB 图片点 Download 显示 `Done` 但从未落盘（保存调用 0 次） | 下载处理器抽成 `runFileDownload`，**所有 MIME 类型**都执行保存；预览降级为纯展示 |
| F3 | P2 | 改动的 JS 未更新 `?v=`，nginx `expires 7d` 会让旧客户端继续用旧逻辑 | 三个改动资源（`chat.js`、`file-transfer.js`、`chat.css`）统一升到 `20261024-t19-files` |
| F4 | P2 | `.xml` 在白名单里，可携带 XHTML 命名空间的脚本 | `.xml` 移出白名单（与 `.svg` 同一原则），保存不受影响 |
| F5 | P3 | 另存为改名后，成功提示仍报发送方原名 | 回报文件名取对话框确认路径的 `basename` |

修复后：复现脚本 `reproduce.cjs` 在第一步即被 `target_in_use` 拒绝（它断言的是旧缺陷，属预期）；新增 `verify-fixes.cjs` 用真实 Electron 39.8.10 验证 5 项的正确行为，全部通过。

新增 8 条回归用例（合计 47 条），并对每条修复做变异验证，全部能被测试抓到：

| 变异 | 结果 |
| --- | --- |
| 去掉 `target_in_use` 守卫 | 3 条失败 ✔ |
| 路径比较改成大小写敏感 | 1 条失败 ✔ |
| 接管路径时不废弃旧授权 | 1 条失败 ✔ |
| 图片跳过保存 | 1 条失败 ✔ |
| 撤回 `chat.js` 版本号 | 1 条失败 ✔ |
| `.xml` 放回白名单 | 1 条失败 ✔ |
| 回报发送方原名 | 7 条失败 ✔ |

### 第 6 轮（独立复核，2026-10-24）发现与处置

独立子 Agent 核实 5 项全部修复（47/47 通过、`verify-fixes.cjs` 7 项全绿、`reproduce.cjs` 因 F1 生效而在第二步报 `target_in_use`），并提出 1 条 P3：

| 级别 | 问题 | 处置 |
| --- | --- | --- |
| P3 | F1 的路径独占把"同路径锁"绑在 30 分钟 TTL 上：渲染端崩溃/刷新未调用 `release` 时，该路径最长被占 30 分钟，且提示没说时长 | 活动授权 5 分钟无分块即视为死亡并回收（`STALE_ACTIVE_GRANT_MS`）；每收到一块刷新 `lastActivityAt`；提示文案补"或稍后再试" |

新增 2 条用例（慢速但活跃的下载不会被误回收；放弃的下载在静默超时后可被重新占用），合计 49 条。

### 变异测试结果（各轮问题修复后复验）

每个修复都做过"改回去看测试是否失败"的验证：

| 变异 | 结果 |
| --- | --- |
| 已 seal 的授权重新占配额 | 1 条失败 ✔ |
| 去掉尾随点/空格归一化 | 1 条失败 ✔ |
| 白名单改回黑名单 | 3 条失败 ✔ |
| 失败时不释放授权 | 1 条失败 ✔ |
| 信封改回 throw（第 1 轮 BLOCKER 退化） | 1 条失败 ✔ |
| 裸 append + 去 offset 校验 | 4 条失败 ✔ |
| 按钮恒显示 Done | 2 条失败 ✔ |
| 去掉 pick 的发送者校验 | 1 条失败 ✔ |
| 关掉浏览器 `unsupported` 分支（第 3 轮补测） | 1 条失败 ✔ |
| 删除 `ichat:files:release` 注册（第 3 轮补测） | 2 条失败 ✔ |
| `.svg` 放回白名单（第 3 轮补测） | 1 条失败 ✔ |
| 去掉同路径独占守卫（第 5 轮） | 3 条失败 ✔ |
| 路径比较改成大小写敏感（第 5 轮） | 1 条失败 ✔ |
| 接管路径时不废弃旧授权（第 5 轮） | 1 条失败 ✔ |
| 图片跳过保存（第 5 轮） | 1 条失败 ✔ |
| 撤回 `chat.js` 版本号（第 5 轮） | 1 条失败 ✔ |
| `.xml` 放回白名单（第 5 轮） | 1 条失败 ✔ |
| 回报发送方原名（第 5 轮） | 7 条失败 ✔ |
| 取消静默超时回收（第 6 轮） | 2 条失败 ✔ |

---

## 9. 相关文档

- 任务交接与模块边界：[agent-handoff.md](agent-handoff.md)（`docs/handoff/windows-desktop.md` 在交接分支上，未并入本分支）
- 通知（T18）：[windows-notifications.md](windows-notifications.md)
- 托盘（T17）：[windows-tray.md](windows-tray.md)
- 品牌（T16）：[windows-branding.md](windows-branding.md)
