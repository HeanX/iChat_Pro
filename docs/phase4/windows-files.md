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
| `ichat:files:open-downloaded` | invoke | `{token}` | `shell.openPath`，只认令牌，不认路径 |

**返回值约定（审查第 1 轮 BLOCKER 后修改）**：`ipcMain.handle` 跨进程只透传 `message`，自定义 `code` 会丢（Electron 39.8.10 实测：渲染端只拿到 `Error invoking remote method '...': Error: write_failed`）。因此预期失败一律 **resolve** 为 `{ok:false, code, detail}`，成功为 `{ok:true, value}`；只有未受信发送者与程序缺陷才 reject。渲染端在 `callNativeFileBridge` 里拆包并复原 `code`，中文提示才真正可达。

**没有**：任意路径读、任意路径写、目录列举、删除、重命名。渲染端从头到尾拿不到一个可写的路径。

代码分布：

```
desktop/file-io.js        297 行  访问策略（纯 Node 可测，不 import electron）
desktop/main.js           +82 行  四条 IPC + 对话框 + shell.openPath
desktop/preload.js        +15 行  iChatDesktop.files.{pickSaveTarget,writeChunk,seal,openDownloaded}
static/js/file-transfer.js +112 行 saveBlobToDisk / openSavedFile / nativeSaveAvailable
static/js/chat.js         +132 行 saveOrDownloadBlob / offerOpenSavedFile 与两处调用点
```

---

## 2. 安全模型（不可放宽的部分）

1. **路径只来自系统对话框。** `createSaveGrant` 的 `filePath` 是 `dialog.showSaveDialog` 的返回值，渲染端无法提供。覆盖同名文件由系统自带的"是否替换"确认。
2. **令牌是 128-bit 随机 UUID，并绑定 `webContents.id`。** 同一应用内另一个 webContents 拿到令牌写不进去（`wrong_sender`）。注意 `event.sender.id` 是**窗口级**标识：同一窗口里的子 frame 共享它。子 frame 默认不加载 preload、拿不到桥，所以当前不构成可利用缺口；若要 frame 级隔离需改用 `senderFrame.routingId`，本卡未做。
3. **只追加且必须连续。** `offset !== writtenBytes` 直接 `offset_mismatch`，所以既不能跳写、不能回放、也不能预分配制造空洞文件。
4. **声明大小强制。** `seal` 要求 `written === total`，否则 `incomplete`；单块上限 2 MiB，总大小上限 100 MiB（与 `file-transfer.js` 的 `MAX_FILE_SIZE` 一致）。
5. **令牌会过期、有数量上限。** 30 分钟 TTL；同时最多 8 个**尚未收尾**的授权（已 `seal` 的下载不再占配额，否则 30 分钟内保存第 9 个文件就会无故失败）。已收尾的授权继续保留，供“打开”使用直到 TTL 到期。
6. **打开只认令牌。** `openTarget` 从令牌反查路径，并要求该授权已 `seal`、大小吻合、文件仍存在。
7. **可执行类型不自动打开。** `.exe/.bat/.cmd/.msi/.ps1/.vbs/.js/.lnk/.reg/...` 仍然允许保存（用户要的就是这个文件），但 `shell.openPath` 被拒绝并返回 `blocked_type`。理由是：打开这类文件等于执行**聊天对端**发来的代码，这一判定应该由用户在资源管理器里自己做。
8. **文件名净化。** 去掉目录部分、Win32 非法字符替换为 `_`、去掉尾随点与空格、`CON/PRN/AUX/NUL/COM1-9/LPT1-9` 加前缀、长度截到 180 且保留扩展名。

### 已知边界（不是缺陷，但必须知道）

- 本方案保护的是**路径与大小**，不是**内容**：解密后的字节由渲染端产出，主进程不校验哈希。完整性由 AES-GCM 的 auth tag 在上游保证。
- 令牌在内存里，应用重启即失效；已保存的文件仍在磁盘上，但"打开"按钮不再有效。
- 主进程不校验写入内容与文件扩展名是否匹配。这是刻意的：用户选什么名字就写什么名字。
- 覆盖已有文件时按**绝对偏移**写入（首块 `w+` 截断，后续 `r+` 定位），收尾时再按声明大小截断一次。选择这个行为而不是"写临时文件再改名"，是为了让"用户选的目标路径"始终等于"磁盘上的文件"；代价是中途失败会留下部分内容（不会回滚到旧文件），此时按钮显示 `Retry` 而不是 `Done`。

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

`write_failed` 时**不推进偏移**，且写入是定位写，所以修复磁盘空间后重试同一偏移会**覆盖**而不是**追加**那部分字节（有断言：写一半失败后重试，文件长度与内容都正确）。

---

## 4. 本地验证（本机 Windows 实测，2026-10-18）

环境：Windows 10.0.26200 / Node v24.14.1 / Electron 39.8.10 / NSIS 3.0.4.1（electron-builder 缓存）。

| 命令 | 结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe manage.py check` | `System check identified no issues (0 silenced).` |
| `.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run` | `No changes detected` |
| `npm run test:e2ee` | **91 tests / 91 pass / 0 fail**（改造前同一基线为 62 pass，本卡新增 29 条） |
| `cd desktop && npm run test:layout` | `Sidebar layout: 36/36 browser checks passed` |
| `cd desktop && npm run test:branding` | 打包 asar 内 **51 tests 全通过**（含新增的 `desktop_file_io` 套件）、`Native PE icon: 7 source frames matched exactly` |
| `cd desktop && npm run test:installer` | `NSIS runtime: 24/24 assertions passed` |
| `cd desktop && npm run test:files` | **29 tests / 29 pass**（新增入口，第 1 轮审查发现它曾指向不存在的文件） |

新增套件 `chat/tests/js/desktop_file_io.test.js` 共 29 条，分四层：

1. **策略层**（`desktop/file-io.js`）：真实临时目录里写字节，覆盖文件名净化、严格字节数解析、顺序追加、越界、重放、过期、绑定发送者、授权上限、失败不推进偏移、**写一半失败后重试不重复追加**、**覆盖更长旧文件后按声明大小截断**、空文件、可执行类型拒绝打开（含 `evil.exe ` 尾随空格绕过）。
2. **主进程层**（真实 `main.js` 在 vm 沙箱里跑，只替换 OS 网络/对话框）：四条通道注册、非受信 frame 拒绝、**校验先于弹框**、取消不建授权、同 frame 并发对话框拒绝、端到端（对话框→两块→seal→打开）、跨 sender 拒绝、可执行不调用 `openPath`、`openPath` 返回错误串时上报。
3. **桥与渲染层**（真实 `preload.js` + `static/js/file-transfer.js`）：无桥时退回原行为、5 MiB 分 3 块且偏移为 `0/2/4 MiB`、字节可按序重组且完全相等、截断写入被拒绝、**`{ok:false,code}` 信封拆包**、8 个错误码到中文文案的映射、0 字节文件仍走原生保存。
4. **按钮状态机**（真实 `static/js/chat.js` 的 `offerOpenSavedFile`，最小 DOM 桩）：`saved` 才显示 `Done` 并附“打开”、`failed` 显示 `Retry` 且不保留 `downloaded` 样式、取消回到 `Download`、重复下载不叠加“打开”按钮。

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

## 6. 兼容与回滚

- 浏览器端行为完全不变：没有 `window.iChatDesktop.files` 时 `nativeSaveAvailable()` 返回 `false`，`saveOrDownloadBlob` 走原来的 `<a download>`。
- 老版本客户端连新服务端、新客户端连旧服务端都没有关系：本卡不涉及任何服务端协议、接口、模型或迁移改动（`makemigrations --check` 为 `No changes detected`）。
- 回滚：revert 该提交即可。已保存到用户磁盘的文件属于用户数据，不随回滚删除。
- 打包白名单新增 `file-io.js`；`test:branding` 现在会断言该文件确实进了 asar，漏打包会直接失败而不是在首次保存时才发现。

---

## 7. 独立审查记录

按 [conventions.md](agent-handoff.md) 的强制要求，实现完成后由**独立上下文的子 Agent**复核（只给 Issue 原文、diff 路径与命令清单，不给作者说明）。

| 轮次 | 审查者 | 结论 | BLOCKER / MAJOR / MINOR | 修复提交 |
| --- | --- | --- | --- | --- |
| 1 | 独立子 Agent（无作者上下文） | PASS WITH FINDINGS | 3 / 8 / 5 | `8b3038a` |

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

---

## 8. 相关文档

- 任务交接与模块边界：[agent-handoff.md](agent-handoff.md)（`docs/handoff/windows-desktop.md` 在交接分支上，未并入本分支）
- 通知（T18）：[windows-notifications.md](windows-notifications.md)
- 托盘（T17）：[windows-tray.md](windows-tray.md)
- 品牌（T16）：[windows-branding.md](windows-branding.md)
