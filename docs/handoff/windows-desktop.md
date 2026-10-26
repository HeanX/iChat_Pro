# 模块 4：Windows 桌面端（`desktop/`）

> 交接对象：负责 Electron 主进程、preload 桥、NSIS 打包与原生验收的 Agent。
> 核对日期 2026-10-18，基线 `cfe53be`。规范见 [conventions.md](conventions.md)。

---

## 1. 这块现在是什么

```
desktop/
├── main.js            480 行  主进程：模式判定、窗口、托盘、IPC、离线恢复
├── preload.js          36 行  contextBridge 窄接口（4 个 invoke 通道 + 1 个下行事件）
├── app-config.js       57 行  纯函数：模式解析 + origin 规范化（Node 可单测）
├── app-branding.js     43 行  产品名、图标路径、关于面板、应用菜单
├── defaults.json        3 行  内置云地址 https://chat.20060810.xyz:8443
├── offline.html        34 行  离线页（带自动恢复轮询）
├── package.json               electron-builder 配置
├── build/
│   ├── installer.nsh          卸载数据保留策略
│   ├── uninstall-helpers.nsh  注册表所有权判定宏
│   ├── icon.ico / icon.png    品牌图标
│   └── README.md
├── scripts/
│   ├── afterPack.cjs           rcedit 写 PE 版本资源
│   ├── test-branding.cjs       真打包 + PE 资源校验
│   ├── test-installer.cjs      真 NSIS 隔离测试（24 条断言）
│   ├── test-sidebar-layout.cjs 真 Chromium 布局 fixture（36 项）
│   ├── verify-icon.cjs         逐帧比对 ICO
│   └── verify-branding.ps1     原生版本元数据
└── dist/                      构建产物（已 gitignore）
    └── iChat-Pro-Setup-1.0.0.exe  SHA-256 ac468df3…d5f9
```

---

## 2. 最短读代码路径

1. `app-config.js` 全文（57 行）—— 三种模式与优先级全在这里。
2. `main.js` 的 440-480 行（启动序列）→ 324-382（窗口）→ 386-419（托盘/恢复）→ 102-125（通知 IPC）→ 57-82（safeStorage IPC）。
3. `preload.js` 全文（36 行）。
4. `package.json` 的 `build` 段。
5. `build/installer.nsh` + `build/uninstall-helpers.nsh`（只在改卸载逻辑时读）。

---

## 3. 运行模式（优先级从高到低）

| 优先级 | 条件 | 模式 | 行为 |
| ---: | --- | --- | --- |
| 1 | 合法 `ICHAT_SERVER_URL` | `cloud` | 加载远端 `/login/`，**不启动 Python** |
| 2 | `--dev` 或 `ICHAT_DEV=1` | `dev` | 启动本地 Django `ICHAT_HOST:ICHAT_PORT`（默认 127.0.0.1:8000） |
| 3 | 给了 URL 但不合法 | `unconfigured` | 显示离线页 + 错误提示 |
| 4 | 什么都没给 | `unconfigured` | 同上 |

`defaults.json` 的内置地址只在**非 dev** 时作为兜底注入，显式 `ICHAT_SERVER_URL` 优先。

**HTTPS 红线**：`http://` 且 host 不在 `localhost`/`127.0.0.1`/`::1` → 直接判 `unconfigured`。

> 已知小坑：`http://[::1]:9000` 的 `URL.hostname` 带方括号（`[::1]`），不在白名单里，会被误判为 unconfigured。

---

## 4. 安全边界（改 main.js 前必须理解）

```js
webPreferences: {
  nodeIntegration: false,
  contextIsolation: true,
  sandbox: true,
  preload: path.join(__dirname, 'preload.js'),
}
```

四项缺一不可。所有 IPC handler 第一步都是 `senderIsTrusted(event)`：

```js
const frameUrl = event.senderFrame?.url || '';
return isAppOrigin(frameUrl) || frameUrl.startsWith('file://');
```

**这不是防 XSS 的边界**：可信同源脚本仍可使用桥。OS 加密不消除"云端脚本被替换"的风险。

### IPC 通道（只有 4 个 + 1 个下行事件）

| 通道 | 方向 | 载荷限制 |
| --- | --- | --- |
| `ichat:secure-storage:is-available` | invoke | 无 |
| `ichat:secure-storage:encrypt` | invoke | 明文 ≤ 2,097,152 UTF-8 字节 |
| `ichat:secure-storage:decrypt` | invoke | 须以 `enc:` 开头，≤ 2,900,000 字符，解码后 ≤ 2,162,688 字节 |
| `ichat:notifications:show` | invoke | `conversationId` 正整数、`conversationType` ∈ single/group、title 截 80、body 截 200、空值拒绝 |
| `ichat:notifications:clicked` | 下行 | `{conversationId, conversationType}` |

**不要新增通用文件读写通道。** #146 需要文件能力时，应设计窄接口（如"保存对话框 + 指定目录写入"），不是 `fs.readFile(path)`。

---

## 5. 打包配置要点

| 项 | 值 |
| --- | --- |
| `appId` | `pro.ichat.desktop`（**必须**等于运行时 `app.setAppUserModelId()`，否则 toast 点击不激活） |
| `productName` | `iChat Pro` |
| `artifactName` | `iChat-Pro-Setup-${version}.${ext}` |
| 目标 | `nsis` / `x64` 唯一 |
| `files` | 6 个文件白名单（打包器自动加 `package.json`，asar 内共 7 个） |
| `extraResources` | `build/icon.ico`、`build/icon.png` → `resources/branding/` |
| `win.signAndEditExecutable` | `false`（**未配置代码签名**，rcedit 只改资源） |
| `afterPack` | `scripts/afterPack.cjs`，仅 win32 |
| NSIS | 非一键安装、允许改目录、允许提权、`include: build/installer.nsh` |

### 卸载数据策略

- `StrCpy $R4 "no"` 起始 → `IfSilent` 直接跳保留 → 交互弹框默认"否" → 选"是"才删 `%APPDATA%\ichat-pro-desktop`。
- 静默/升级路径**一律保留**数据。
- 注册表清理走 `iChatUninstallCommandMatches` 完整路径比对，避免误删 `INSTDIR-old` 之类的兄弟目录。

---

## 6. 本地验证（仅 Windows）

```powershell
cd desktop
npm ci                    # electron 39.x / electron-builder 25.x / rcedit 5.x

npm start                 # 云模式启动
npm run dev               # 开发模式（需本机 Python）
npm run dist              # 打安装包 → dist/iChat-Pro-Setup-1.0.0.exe

npm run test:layout       # 真 Chromium，36 项，约 30-60s
npm run test:branding     # 真 electron-builder + PE 资源，超时 180s
npm run test:installer    # 真 NSIS，24 条断言；缺 NSIS 直接失败（不跳过）
```

根目录另跑：

```powershell
npm run test:e2ee
# 与桌面相关的四套：desktop_config 1 项（自写脚本）+ desktop_branding 1 +
# desktop_tray 15 + desktop_notifications 7 = 24 项
```

`--dev` 模式按优先级找 Python：`ICHAT_PYTHON` 环境变量 → 仓库 `.venv\Scripts\python.exe` → PATH 里的 `python`。都找不到时表现为离线页而非明确报错——排障先查 `ICHAT_PYTHON`。

---

## 7. 已知坑

| # | 坑 | 影响 |
| --- | --- | --- |
| 1 | `win-unpacked/` 与隔离构建**收不到 toast 点击** | AUMID 必须与开始菜单快捷方式匹配，只有安装版具备。所有通知验收必须用安装版 |
| 2 | 未配置代码签名 | SmartScreen 会警告；这是已知边界不是缺陷 |
| 3 | 待发箱在内存 | 页面重载/进程重启丢未确认消息（与 Web 端同源问题） |
| 4 | 运行时断线只注入横幅不重载 | 刻意设计（保护待发箱）；`executeJavaScript` 失败被 `.catch(()=>{})` 静默吞掉 |
| 5 | 托盘创建失败静默降级 | `createTray()` 有返回值但 `whenReady` 里没用；失败时只是普通最小化 |
| 6 | `defaults.json` 把云地址写进产物 | 换服务器要重新打包（用户不会设环境变量） |
| 7 | `app.setName()` 会改 userData 路径 | `configureBranding` 已保存并恢复，改动这段注意别破坏 |
| 8 | 布局测试是 fixture | 不能替代登录态页面实测（#145 就是卡在这个区别上） |

---

## 8. 这个模块的开放任务

| Issue | 内容 | 优先级 | 现状 |
| --- | --- | --- | --- |
| [#145](https://github.com/HeanX/iChat_Pro/issues/145) | P4 T18 系统通知最终复验 | P1 | 代码与自动化全绿，缺**安装版登录态**的预览切换/返回聊天/顶部布局截图 |
| [#146](https://github.com/HeanX/iChat_Pro/issues/146) | P4 T19 原生文件选择/下载/打开 | P1 | **未实施**。需要新增窄 IPC 通道（`dialog.showSaveDialog` + 受限写入），不是通用 fs |
| [#184](https://github.com/HeanX/iChat_Pro/issues/184) | P4 Test T03 Windows 安装/E2E | P0 | 有 NSIS 隔离测试与品牌校验，缺"安装 → 核心聊天 → 通知 → 文件 → 恢复"的完整 E2E 报告 |

### #146 的实现边界（建议）

```
新增 IPC：
  ichat:files:pick-save-target  → 打开系统保存对话框，返回用户选定路径（仅此一次授权）
  ichat:files:write-chunk       → 写入已授权句柄的偏移（分块，限大小）
  ichat:files:open-downloaded   → shell.openPath，仅限已下载目录内的路径
不要提供：任意路径读、任意路径写、目录遍历。
```

必须验证的失败路径：用户取消、磁盘满、路径越界、文件被占用。

---

## 9. 给下一个 Agent 的提示词

```text
你在 iChat_Pro 仓库负责【Windows 桌面端模块】。

必读：docs/handoff/conventions.md、docs/handoff/windows-desktop.md、
docs/phase4/windows-notifications.md、docs/phase4/windows-tray.md。
再读代码：desktop/app-config.js 全文 → desktop/main.js 的启动序列与 IPC →
desktop/preload.js → desktop/package.json 的 build 段。

任务：【填写 Issue 号与标题】。

不可破坏：
- webPreferences 四项（nodeIntegration:false / contextIsolation:true / sandbox:true / preload）。
- 所有 IPC 的 senderIsTrusted 校验与载荷上限。
- appId 与运行时 AUMID 必须一致（pro.ichat.desktop）。
- 不得新增通用文件读写通道。
- 卸载默认保留用户数据。

必须执行（Windows）：
cd desktop
npm run test:layout
npm run test:branding
npm run test:installer
cd .. && npm run test:e2ee

原生交互验收必须用安装版（dist/iChat-Pro-Setup-1.0.0.exe），
win-unpacked 收不到 toast 点击。未做原生验收的项写"未验证"。

完成前必须另开独立审查子 Agent 复核 diff（见 conventions.md §1）。
```
