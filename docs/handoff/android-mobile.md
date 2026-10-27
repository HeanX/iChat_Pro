# 模块 8：Android / 移动端（从零立项）

> 交接对象：负责在 Windows/Web 之外增加 Android 端的 Agent。
> 核对日期 2026-10-18，基线 `cfe53be`。规范见 [conventions.md](conventions.md)。

---

## 1. 现状：工程骨架已落地，产品功能仍为 0

> 更新 2026-10-18（分支 `feature/p4-t22-mobile-adr-and-skeleton`）：T22（#149）已定稿
> [ADR-P4-03](../phase4/adr-p4-03-mobile-tech-stack.md) 并落地 `mobile/` 骨架。
> **能力边界没变：登录、WS 收发、E2EE、通知、文件一件都没实现。** T23–T30 全部 0 进度。
>
> 更新 2026-10-26（AND-01 整改）：服务地址改为**构建期注入**——`npm run config:generate` 生成
> `mobile/www/js/mobile-env.js`（`window.ICHAT_MOBILE_ENV`），随 `npx cap sync` 进 APK。
> 在此之前"按文档操作"无法离开"未配置"状态，详见 ADR §3.1 与 §5.1.2。

```
仓库里现在有：
  mobile/                     Capacitor 8.5.3 工程（package.json / capacitor.config.json / www/）
  mobile/android/             Capacitor CLI 真实生成的 Android 工程（不是占位目录）
  mobile/tests/               node:test 配置契约测试（30 条，纯 Node，无需模拟器）
  docs/phase4/adr-p4-03-*.md  技术决策记录
  app-debug.apk               已构建成功（AND-01 后 4,138,535 B，SHA256 14B41ADA…EB48E9，见 ADR §5.1）
仓库里仍然没有：
  提交进库的 APK / AAB          （产物在 build/ 下，已 gitignore；不可安装到设备）
  真机或模拟器的运行记录        （0 次：adb devices 为空，无 emulator 包，无 Hyper-V）
  任何 Android 仪器化/UI 测试   （0 测试）
```

> **APK 数字口径**：`mobile/android/app/build/outputs/apk/debug/app-debug.apk` 目前留在工作区的
> 那一份是 **2026-10-18 的修复前产物**（4.22 MB / `67104134…26C878`，已过期），它里面的
> `index.html` 仍是旧文案、没有 `mobile-env.js`。要拿到当前形态必须按 §6 重新构建。
> SHA256 **不能跨环境复现**（Gradle/AGP 时间戳与签名差异），核对产物请比对
> `assets/public/**` 与 `mobile/www/` 的**字节**，不要比 SHA。

WP04 的 9 项任务（#149–#157）中 **仅 #149 部分完成**；#150–#157 为 0/9。

**构建前置（本机已用替代方案补齐，细节见 ADR §5.3/§6）**：系统 JDK 只有 1.8，已改用本机
OpenJDK 23.0.1；系统 SDK 目录是 Program Files 只读且只有 33/34，已用 `--sdk_root` 装到
`mobile/.android-sdk`（含 Platform 36 + Build-Tools 36，已 gitignore）。
**真机/模拟器仍然缺失**，这是下一张卡（T24）的硬阻塞点。

可复用的是：

| 已有资产 | 复用方式 |
| --- | --- |
| 服务端 HTTP/WS 协议 | 完全不改，Android 作为第三个客户端接入 `ws/chat/` |
| E2EE 协议（ECDH-P256 + HKDF + AES-256-GCM） | 需要原生或 WebView 内的等价实现，**必须能解 Windows/Web 发的密文** |
| 加密文件分片规范 | `docs/iChat Pro 文件传输规范.md` + `/api/files/` 路由 |
| `static/js/chat-connection.js` | 逻辑可移植（退避/心跳/ACK/状态机），但不是现成 Android 代码 |
| `docs/phase4/protocol.md` | 协议契约，Android 必须遵守同一份 |

---

## 2. 技术决策（#149）——**已决策，见 ADR-P4-03**

> 2026-10-18 更新：本节原写的是"建议先做技术验证原型"。**该决策已经做出**，结论与理由见
> [ADR-P4-03](../phase4/adr-p4-03-mobile-tech-stack.md)。下表保留为决策依据，不要再当作"待决"。

**结论：采用 Capacitor 8 混合外壳。** 决定性理由是 E2EE 与连接算法已在 JS 中实现并通过测试，
原生重写必须与 Web/Windows 逐字节等价（属 Test T06 范围），成本远高于混合路线。

**转原生的触发条件（ADR §2.3，任一不通过即转向）**——这三条仍然是"未验证"，必须在 T24 出包后补做：

1. WebView 内 WebCrypto 生成的 ECDH 私钥能否受 Android Keystore 保护（大概率**不能**按原生口径通过，ADR 给了三条出路）。
2. WS 在后台/息屏时的存活与重连表现。
3. Cookie/Session 在 WebView 与 `wss://` 下是否正常工作。

Issue #149 的验收是"能复用现有页面并生成 Android 工程"。两条路线的真实差异：

| 维度 | Capacitor / WebView 混合 | 原生 Kotlin + Compose |
| --- | --- | --- |
| 复用现有页面 | 几乎全部（直接加载云端 `/login/` → `/chat/`） | 0，全部重写 |
| 开发速度 | 快 | 慢 |
| E2EE 密钥存储 | 需 Android Keystore 桥（WebCrypto 生成的不可导出密钥在 WebView 里能否受 Keystore 保护是**关键问题**） | 原生 Keystore 直接支持 |
| 后台推送 | 需 FCM + WebView 唤醒，复杂 | 原生 FCM |
| 系统返回键/生命周期 | 需桥接 | 原生 |
| 应用体积 | 小 | 中 |
| 与现有 CSP/CSRF 兼容 | WebView 跨 Origin 认证需处理（`server.url` 热重载不能当正式方案） | 走标准 API，无此问题 |
| 官方倾向（`agent-handoff.md` WP04） | "可选等价原生 WebView 混合外壳" | — |

**建议**：先用 Capacitor 做**技术验证原型**（登录 + WS + 收发一条消息），验证三件事后再决定是否正式采用：

1. WebView 内 WebCrypto 生成的 ECDH 私钥能否存进 Android Keystore（或至少受其保护）。
2. WS 在后台/息屏时的存活与重连表现。
3. Cookie/Session 在 WebView 与 `wss://` 下是否正常工作。

这三条任何一个不通过，就应转原生。

---

## 3. 必须遵守的接口契约

### 认证

现有登录是 **Django Session + CSRF Cookie**。Android 有两种选择：

- **WebView**：直接走页面登录，Session Cookie 自动管理；但需要处理 `DJANGO_CSRF_TRUSTED_ORIGINS` 与服务端同源。
- **原生**：需要新增 token 认证端点（**当前不存在**），这会是一次协议扩展，必须走 ADR 并与消息/账号模块串行整合。

### WebSocket

```
wss://chat.20060810.xyz:8443/ws/chat/
必须带 Session Cookie（AuthMiddlewareStack）
Origin 必须通过 AllowedHostsOriginValidator → 需要把 App 的 Origin 加入 DJANGO_ALLOWED_HOSTS 允许范围
未登录 → close code 4401
协议版本不符 → close code 4003
```

**注意**：`AllowedHostsOriginValidator` 会校验 `Origin` 头。原生 OkHttp 默认不发 `Origin`，WebView 会发 `https://chat...`。这两个行为不同，必须分别验证。

### 消息信封

与 Web 完全一致（见 [messaging-protocol.md](messaging-protocol.md) §5）。事件名、错误码、close code **一个都不能改**。

---

## 4. 9 项任务的依赖顺序

```
#149 T22 技术方案 + 工程骨架        ← 起点，产物是 ADR + 能跑起来的空壳
  └── #150 T23 响应式适配            ← 可直接在 Web 端先做（不依赖 Android）
        └── #151 T24 生成 APK/AAB    ← 第一个可安装产物
              ├── #152 T25 返回键与生命周期
              ├── #153 T26 网络变化检测与重连
              ├── #155 T28 文件选择与保存
              ├── #156 T29 Android 安全存储
              │     └── #154 T27 前台/后台通知
              │           └── #157 T30 完全退出云推送（P2）
              └── #164 T37 Windows—Android 跨端   ← 最终验收
```

**#150 响应式适配可以先做**：它改的是 `templates/` 与 CSS，不依赖 Android 工程存在。验收标准是"核心操作无溢出、遮挡或鼠标悬停依赖"，可以用浏览器 400×720 视口验证。

---

## 5. 与现有模块的接口点

| 需要什么 | 找谁 / 看哪里 |
| --- | --- |
| 新增 token 认证端点 | [accounts-auth.md](accounts-auth.md) + 需 ADR |
| 密钥存储契约 | [web-frontend-e2ee.md](web-frontend-e2ee.md) §4 + #166 |
| 复用的连接算法 | `static/js/chat-connection.js` 的常量与状态机 |
| 文件分片协议 | [data-storage-file.md](data-storage-file.md) §3 |
| 设备身份与撤销 | [accounts-auth.md](accounts-auth.md) §6（#167–#169，**当前没有设备模型**） |
| Android 构建进 CI | [testing-quality-ci.md](testing-quality-ci.md)（#175） |

**重要**：多设备密钥分发（#169）的前提是设备身份模型（#167）。如果 Android 先于设备模型落地，会出现"同一账号在 Android 与 Windows 上都用同一个 `UserPublicKey`"的问题——新设备无法解密旧消息，且撤销粒度只能到账号级。这是产品层面的真实限制，需要在 ADR 里写清楚。

---

## 6. 本地环境（Windows 开发 Android）

> 2026-10-18 更新：以下命令**已在仓库落地并真实执行过**（T22）。完整步骤、实测版本与
> 本机的三处环境坑（JDK 1.8、Program Files 只读 SDK、wrapper 发行版下载被阻断）见
> [ADR-P4-03 §5/§6](../phase4/adr-p4-03-mobile-tech-stack.md)。

```powershell
# 前置：JDK 21+（不是 17；Capacitor 8 的 capacitor.build.gradle 固定 VERSION_21）、
#       Android SDK Platform 36、Gradle。本机实测 OpenJDK 23.0.1 可用。
java -version
adb devices

# 本仓库的实际入口（不要用 npm create，工程已存在）
cd mobile
npm install

# 【必经】注入服务地址。读的是进程环境变量，不读 mobile/.env。
# 不设这一步 = 打出没有 origin 的包 = 启动页永远停在"未配置"（AND-01 原缺陷）。
$env:ICHAT_MOBILE_SERVER_URL = 'https://<你的服务器>:8443'
npm run config:generate            # 写出 www/js/mobile-env.js + capacitor.config.json（后者仍无 server 块）

npx cap sync android               # 把 www/（含 mobile-env.js）复制进原生工程
cd android
.\gradlew.bat assembleDebug      # 产物 app/build/outputs/apk/debug/app-debug.apk
```

**不要**把 `capacitor.config.json` 的 `server.url` 热重载配置当成正式方案——它会让 App 依赖开发机。
骨架已用 `mobile/www/js/mobile-config.js` + 30 条测试把这条规则固化：发布解析结果恒为
`serverUrl: null`，生成的 `capacitor.config.json` 不含 `server` 块，只有显式 `--live-reload` 才会写入。
若上一次跑过 `--live-reload`、随后又以**无效配置**运行，生成器会把 `capacitor.config.json`
**重置回发布形态**并把 `mobile-env.js` 删掉（ADR §5.1.2），所以不会把热重载配置带进包。

> **`www/js/mobile-env.js` 是构建产物，已 gitignore**（形态见 `www/js/mobile-env.example.js`）。
> 未配置时生成器 `exit 1`、不写该文件，并**删除**上一次构建遗留的副本，避免旧地址随
> `npx cap sync` 进包。所以 `git status` 里看不到它属于正常现象。

> **改 `mobile/www/**` 之后必须先 `npx cap sync android` 再 `assembleDebug`。**
> Gradle 只打包已经同步进 `android/app/src/main/assets/public/` 的副本，不会回头看 `www/`。
> T22 整改时就漏了这一步，导致 APK 内嵌的是旧版资源（ADR §5.1 构建历史 ③→④）。

### 6.1 FileProvider 路径约定（T28 会用到）

T22 把 Capacitor 模板默认的 `<external-path path="." />`（**暴露整个外部存储**）收窄成了三条
子目录规则，见 `mobile/android/app/src/main/res/xml/file_paths.xml`：

| name | 实际目录 |
| --- | --- |
| `ichat_cache` | `cacheDir/ichat/` |
| `ichat_files` | `filesDir/ichat/` |
| `ichat_external_files` | `getExternalFilesDir()/ichat/` |

**约束**：写文件时目标路径必须落在上述 `ichat/` 子目录内，否则 `FileProvider.getUriForFile()`
会抛 `IllegalArgumentException: Failed to find configured root`。
T28 若要新增位置，**追加一条具体子目录，不要退回 `path="."`**。
注意模板原有的 `cache-path path="."` 已被删除，所以 `cacheDir` 根下的文件不再可共享。

---

## 7. 交付要求（来自 `agent-handoff.md` WP04）

- 固定工具链版本（JDK / Gradle / Android SDK / Capacitor 或 AGP）。→ T22 已完成，见 ADR §4.2。
- 生成可安装的 APK 或 AAB。→ **debug APK 已构建**，但**未安装到任何设备**，因此"可安装"未经验证。
- 提交**两个 API Level 或两种设备配置**的实际执行记录（不是"编译成功"）。→ **未完成，0 条记录。**
- 未测试项明确列出，不能以"编写了脚本"代替实测。→ ADR §5.3 已列。
- 连接算法复用 WP02 的共享实现，不各维护一套状态机。→ 混合路线天然复用 `chat-connection.js`。

---

## 8. 已知风险

| 风险 | 说明 |
| --- | --- |
| WebCrypto 私钥与 Keystore 的鸿沟 | WebView 里生成的不可导出 CryptoKey 存在 WebKit 的存储里，**不一定**受 Android Keystore 保护。这是 #156 的核心难点 |
| 后台 WS 存活 | Android 会杀后台；Doze 模式会断网。前台服务通知是常见解法但会常驻通知栏 |
| 完全退出后的推送 | 端到端加密下，FCM 不能带明文；只能推"有新消息"再唤醒拉取。这是 #157 的 P2 范围 |
| Origin 校验 | 原生客户端的 `Origin` 头与 WebView 不同，需服务端确认 |
| 无设备模型 | 见 §5，撤销粒度只到账号级 |
| 测试资源 | 真机或独立模拟器是硬要求，Agent 环境可能不具备 |

---

## 9. 给下一个 Agent 的提示词

> T22 已完成，下面的提示词是**历史记录**（写它的时候工程还不存在）。
> 下一张卡应从 **#151 T24 生成 APK/AAB** 或 **#150 T23 响应式适配**（后者不依赖 Android 工程，可并行）开始。
> 当前状态以 §1 与 [ADR-P4-03](../phase4/adr-p4-03-mobile-tech-stack.md) 为准。

```text
你在 iChat_Pro 仓库负责【Android 移动端】，从零立项。

必读：docs/handoff/conventions.md、docs/handoff/android-mobile.md、
docs/phase4/protocol.md 全文、docs/phase4/agent-handoff.md 的 WP04 段。
再读代码：static/js/chat-connection.js（连接语义）、
static/js/private-chat-e2ee.js 的常量（加密参数）。

任务：P4 T22（Issue #149）—— 移动端技术方案 + 工程骨架。

要求：
1. 先写 ADR：Capacitor 混合 vs 原生 Kotlin，必须回答三个问题——
   WebCrypto 私钥能否受 Android Keystore 保护、后台 WS 存活策略、认证方式（Session vs 新 token 端点）。
2. 生成能跑起来的空壳工程，固定工具链版本，写进文档。
3. 不要修改服务端协议；如需 token 端点，先提 ADR，不要直接改 accounts。
4. 本机没有 Android SDK 或真机时，明确写"未验证"，不要编造构建/安装结果。

必须执行（有环境时）：
npx cap sync && ./gradlew assembleDebug
adb install && adb shell am start ...
两个 API Level 或两种设备配置的实际运行记录。

完成前必须另开独立审查子 Agent 复核 diff（见 conventions.md §1）。
```
