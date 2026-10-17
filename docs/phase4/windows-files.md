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
2. **令牌是 128-bit 随机 UUID，并绑定 `webContents.id`。** 同一应用内的另一个 frame 拿到令牌也写不进去（`wrong_sender`）。
3. **只追加且必须连续。** `offset !== writtenBytes` 直接 `offset_mismatch`，所以既不能跳写、不能回放、也不能预分配制造空洞文件。
4. **声明大小强制。** `seal` 要求 `written === total`，否则 `incomplete`；单块上限 2 MiB，总大小上限 100 MiB（与 `file-transfer.js` 的 `MAX_FILE_SIZE` 一致）。
5. **令牌会过期、有数量上限。** 30 分钟 TTL，同时最多 8 个未过期授权（`too_many_grants`）。
6. **打开只认令牌。** `openTarget` 从令牌反查路径，并要求该授权已 `seal`、大小吻合、文件仍存在。
7. **可执行类型不自动打开。** `.exe/.bat/.cmd/.msi/.ps1/.vbs/.js/.lnk/.reg/...` 仍然允许保存（用户要的就是这个文件），但 `shell.openPath` 被拒绝并返回 `blocked_type`。理由是：打开这类文件等于执行**聊天对端**发来的代码，这一判定应该由用户在资源管理器里自己做。
8. **文件名净化。** 去掉目录部分、Win32 非法字符替换为 `_`、去掉尾随点与空格、`CON/PRN/AUX/NUL/COM1-9/LPT1-9` 加前缀、长度截到 180 且保留扩展名。

### 已知边界（不是缺陷，但必须知道）

- 本方案保护的是**路径与大小**，不是**内容**：解密后的字节由渲染端产出，主进程不校验哈希。完整性由 AES-GCM 的 auth tag 在上游保证。
- 令牌在内存里，应用重启即失效；已保存的文件仍在磁盘上，但"打开"按钮不再有效。
- 主进程不校验写入内容与文件扩展名是否匹配。这是刻意的：用户选什么名字就写什么名字。
- 覆盖已有文件时会先 `writeFileSync` 截断再追加。若首块之后失败，目标文件会停在部分内容状态（不会回滚到旧文件）。选择这个行为而不是"写临时文件再改名"，是为了让"用户选的目标路径"始终等于"磁盘上的文件"。

---

## 3. 失败路径与提示文案

每条都在 `desktop_file_io.test.js` 里有断言。

| 触发 | 错误码 | 用户看到的文案 |
| --- | --- | --- |
| 用户在对话框点取消 | — | 按钮回到 `Download`，不弹提示、不报错 |
| 磁盘满 / 文件被占用 | `write_failed` | 写入失败：磁盘已满或文件被占用。 |
| 偏移对不上 / 主进程没推进 | `offset_mismatch` | 写入位置不一致，已中止，请重新下载。 |
| 令牌过期 | `expired` | 下载超时，请重新下载。 |
| 令牌不存在（重启后） | `not_found` | 上次的保存凭证已失效，请重新下载。 |
| 目标是安装包/脚本 | `blocked_type` | 出于安全考虑，安装包和脚本不能直接打开，请在下载目录中查看。 |
| 系统没有关联应用 | `open_failed` | 打开失败：系统没有关联的应用。文件已保存，可在下载目录中打开。 |
| 大小没到齐就收尾 | `incomplete` | 走通用失败分支，下载按钮显示 `Failed` |

`write_failed` 时**不推进偏移**，所以修复磁盘空间后同一偏移可以重试（有断言）。

---

## 4. 本地验证（本机 Windows 实测，2026-10-18）

环境：Windows 10.0.26200 / Node v24.14.1 / Electron 39.8.10 / NSIS 3.0.4.1（electron-builder 缓存）。

| 命令 | 结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe manage.py check` | `System check identified no issues (0 silenced).` |
| `.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run` | `No changes detected` |
| `npm run test:e2ee` | 84 tests / 84 pass / 0 fail（改造前同一基线为 62 pass，本卡新增 22 条） |
| `cd desktop && npm run test:layout` | `Sidebar layout: 36/36 browser checks passed` |
| `cd desktop && npm run test:branding` | 打包 asar 内 44 tests 全通过（含新增的 `desktop_file_io` 套件）、`Native PE icon: 7 source frames matched exactly` |
| `cd desktop && npm run test:installer` | `NSIS runtime: 24/24 assertions passed` |

新增套件 `chat/tests/js/desktop_file_io.test.js` 共 22 条，分三层：

1. **策略层**（`desktop/file-io.js`）：真实临时目录里写字节，覆盖文件名净化、严格字节数解析、顺序追加、越界、重放、过期、绑定发送者、授权上限、失败不推进偏移、空文件、可执行类型拒绝打开。
2. **主进程层**（真实 `main.js` 在 vm 沙箱里跑，只替换 OS 网络/对话框）：四条通道注册、非受信 frame 拒绝、**校验先于弹框**、取消不建授权、同 frame 并发对话框拒绝、端到端（对话框→两块→seal→打开）、跨 sender 拒绝、可执行不调用 `openPath`、`openPath` 返回错误串时上报。
3. **桥与渲染层**（真实 `preload.js` + `static/js/file-transfer.js`）：无桥时退回原行为、5 MiB 分 3 块且偏移为 `0/2/4 MiB`、字节可按序重组且完全相等、截断写入被拒绝、错误码到中文文案的映射。

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

## 7. 相关文档

- 交接文档：[docs/handoff/windows-desktop.md](../handoff/windows-desktop.md) §4（IPC 通道表）、§8（任务表）
- 通知（T18）：[windows-notifications.md](windows-notifications.md)
- 托盘（T17）：[windows-tray.md](windows-tray.md)
- 品牌（T16）：[windows-branding.md](windows-branding.md)
