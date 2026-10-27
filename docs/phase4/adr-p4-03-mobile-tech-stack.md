# ADR-P4-03：移动端技术路线与工程骨架（P4 T22 / Issue #149）

> 状态：**已决策 Capacitor 混合外壳（附带否决理由与转原生的触发条件）**
> 编写日期：2026-10-18；代码基线 `cfe53be`（分支 `feature/p4-t22-mobile-adr-and-skeleton`）
> 关联：Issue [#149](https://github.com/HeanX/iChat_Pro/issues/149)、[android-mobile.md](../handoff/android-mobile.md)、[protocol.md](protocol.md)（ADR-P4-02）
> 规范：[conventions.md](../handoff/conventions.md)

---

## 0. 结论先行

1. **技术路线**：采用 **Capacitor 8 混合外壳**（**目标**是 WebView 加载仓库已有的 Django 模板页面），
   **不重写 UI**。理由与否决理由见 §2；**若 §2.3 的三个验证问题中任何一个不通过，立即转原生
   Kotlin + Compose**，转向前不投入 T25–T30。
   > 注意：T22 只落地了工程骨架与配置解析，**当前构建尚未加载任何 Django 页面**（加载的是本地 assets
   > 启动页），"复用现有页面"的接通属 T24。见 §3.1 的更正说明。
2. **认证方式**：**不新增 token 端点**。**目标形态**是 Android 复用现有 **Django Session + CSRF Cookie**，
   WebView 与 Web 同源；**该形态需 T24 接通同源加载后才成立**，目前未实现也未实测。
   需要 token 的情形与前置条件见 §3.3。
3. **工程骨架已落地并可构建**：`mobile/` 可 `npm install` + `npm run config:generate` + `npx cap sync` + Gradle 构建，
   `mobile/android/` 是 Capacitor CLI **真实生成**（非手写）的原生工程；
   **`app-debug.apk` 已真实产出**（AND-01 整改后产物 4,138,535 B / `14B41ADA…EB48E9`；
   修复前的旧产物 4.22 MB / `67104134…26C878` 已标注为过期。SHA256 与构建命令见 §5）。
4. **本机工具链**：JDK 21+ 与 **Android SDK Platform 36 缺失**（系统只有 JDK 1.8 与 SDK 33/34）。
   已通过 **workspace 内可写 SDK 根目录**（`mobile/.android-sdk`）+ 本机已有的 **OpenJDK 23** 补齐，
   构建成功。**真机 / 模拟器安装与启动未执行**（无设备、无 emulator 包、无 Hyper-V），属"未验证"。
5. **一处工具链偏差（必须披露）**：`gradle-wrapper.properties` 指定 Gradle **8.14.3**，但该发行版托管在
   GitHub Releases，本机网络到该主机被重置（`curl: (56) Recv failure`），wrapper 无法下载。
   实际构建改用**本机已缓存的 Gradle 9.5.1** 完成。**因此本次产物不是用固定 wrapper 版本构建的**，
   CI / 正式出包必须在能访问 `services.gradle.org` 的环境下用 `./gradlew` 复跑，见 §6。

---

## 1. 背景与约束

| 项 | 事实 |
| --- | --- |
| 现有客户端 | Web（原生 JS + Django 模板）、Windows（Electron）。E2EE 在浏览器侧用 WebCrypto 实现 |
| 服务端协议 | `ws/chat/` + `/api/*`，协议版本 `1.0`、错误码注册表见 ADR-P4-02。**本 ADR 不改协议** |
| 认证 | Django Session（HttpOnly Cookie）+ CSRF。`AllowedHostsOriginValidator` 校验 `Origin` |
| 设备模型 | **不存在**（#167 未做）。撤销粒度只能到账号级，见 §7 |
| 可复用资产 | 模板/CSS/`static/js/*`（E2EE、连接状态机、补取）整体可被 WebView 复用 |
| WP04 官方倾向 | `agent-handoff.md` 写的是"可选等价原生 WebView 混合外壳" |

**本卡范围**：技术决策 + 工程骨架 + 工具链固定。**不含**登录、WS 收发、E2EE、通知、文件（T23–T30）。

---

## 2. 决策：Capacitor 混合 vs 原生 Kotlin

### 2.1 对比矩阵（基于本仓库实际情况，不是通用结论）

| 维度 | Capacitor 混合 | 原生 Kotlin + Compose | 对本项目的权重 |
| --- | --- | --- | --- |
| 复用现有页面 | **约 100%**（直接加载 `/login/`、`/chat/`） | 0，需重写全部 UI 与交互 | 高：T22 验收原文就是"能复用现有页面" |
| E2EE 复用 | 直接复用 `private-chat-e2ee.js`，**密文互通零成本** | 需在 Kotlin 重实现 ECDH-P256/HKDF-SHA256/AES-256-GCM，且必须逐字节对齐 | **极高**：协议不互通就等于三端失败（Test T06） |
| 补取/状态机复用 | 复用 `chat-connection.js`（UMD，T33 产物） | 重写状态机 → 违反 `agent-handoff` §2 "不各维护一套状态机" | 极高 |
| E2EE 密钥存储 | 私钥在 WebView（WebCrypto 非导出 CryptoKey）→ **不在 Keystore 内**，需自建桥 | 原生 Keystore / StrongBox 直接保护 | 高：T29 的核心难点 |
| 后台 WS 存活 | WebView 进程被系统回收即断；需前台服务保活 | 同样受 Doze 限制，但进程内可控性更强 | 中：两端都要做前台服务 |
| FCM 推送 | 需原生插件桥接 | 原生直接 | 中（P2，T30） |
| 返回键/生命周期 | 需桥接（T25 的一张卡） | 原生 | 中 |
| 安装包体积 | 小 | 中 | 低 |
| 与 CSP/CSRF 兼容 | 同源加载时天然兼容；跨 Origin 热重载不兼容 | 走标准 API，无 CSP 问题 | 中 |
| 开发速度（T23–T30） | 快：大部分卡片是桥 + 配置 | 慢：每张卡都要重写等价 UI | 高 |

### 2.2 决策

**采用 Capacitor 8**。决定性因素只有一条：**E2EE 与连接算法已经在 JS 里实现且经过测试
（`npm run test:e2ee` 覆盖 `private_chat_e2ee` / `chat_connection`）**。原生路线要把这两块在 Kotlin
里重写一遍并保证与 Web/Windows **逐字节等价**，这属于 Test T06（三端加密向量）的范围，
而 T06 的依赖恰恰是 T24（Android APK）。选原生等于把 T06 的成本翻倍并推迟它。

### 2.3 转原生的触发条件（不满足即转向，不允许含糊过去）

T24 出包后，**必须先做**下列三项验证；任何一项失败，**停止 T25–T30，转原生**：

| # | 验证问题 | 判定标准 | 对应卡 |
| --- | --- | --- | --- |
| V1 | WebView 内 WebCrypto 生成的 ECDH 私钥能否受 Android Keystore 保护 | 私钥可**完全不出现**在可导出形态；或被 Keystore 派生的密钥包裹后落盘（包裹密钥不可导出、不随备份迁移） | #156 T29 |
| V2 | WS 在息屏/后台的存活与重连 | 息屏 10 分钟后回前台，能在 5 秒内恢复并补到断线期间的消息 | #153 T26 |
| V3 | Cookie/Session 在 WebView + `wss://` 下是否正常 | 登录后 WS 握手不返回 4401；CSRF 写请求不 403 | #151 T24 |

**V1 的预期（诚实声明）**：WebCrypto 的**非导出** `CryptoKey` 存在 WebKit/Chromium 存储中，
**不受 Android Keystore 保护**，且 Android 系统备份可能带走它。因此 V1 大概率**不能按"原生 Keystore"
口径通过**。三种可接受结局，需在 T29 前定稿：

- (a) 接受"WebView 存储 + 禁用备份（`android:allowBackup="false"`）+ 生物识别门槛"的降级方案，
      并在文档里写明**不等于 Keystore 保护**；
- (b) 把长期私钥移出 WebCrypto：原生侧 Keystore 生成密钥对，私钥永不出原生；JS 通过窄桥请求
      ECDH 运算。**这会改变 `private-chat-e2ee.js` 的密钥来源**，属 WP05 契约，需与 #166 串行；
- (c) 转原生。

> **本 ADR 不预设 (a) 一定通过验收。** Issue #166（T39）要求"普通存储、日志中无法直接读取私钥"，
> (a) 是否满足该条需要单独评审，不得用本 ADR 的结论代替。

---

## 3. 认证方式决策

### 3.1 决策：复用 Django Session，不新增 token 端点

**目标形态（T24 之后）**：Capacitor 外壳在发布模式下从 `https://<服务器>` 加载 Django 页面（同源），
Cookie 由 WebView 的 CookieManager 自动管理，`wss://` 握手复用同一 Cookie。这与 Web 端行为一致，
**服务端零改动**。

> **当前骨架不是这个形态。** T22 的发布构建 `server.url` 为 `null`，WebView 加载的是打包进 APK 的
> 本地 assets（`mobile/www/index.html` 启动页），`shell.js` 里没有任何 `location`/`navigate`/WebSocket
> 代码。**服务地址是构建期注入的，不是运行时注入**：`npm run config:generate`
> （`mobile/scripts/write-capacitor-config.js`）把解析后的 origin 写成
> `mobile/www/js/mobile-env.js`（`window.ICHAT_MOBILE_ENV = {...}`），该文件是 gitignore 的构建产物，
> 由 `npx cap sync android` 一起复制进 `android/app/src/main/assets/public/`，最终出现在 APK 内。
> 也就是说 **"同源加载 Django 页面"是 T24 要接通的目标，不是本卡已实现的事实**；本卡只落地了
> "服务地址如何解析、如何校验、如何注入打包资源"这一层。运行时注入（安装后在 App 内改地址、
> 或由服务端下发）**尚未实现**。
> 上一版 ADR 在这里用了现在时，属表述错误；AND-01 整改前这里还写着"APK 内也没有注入
> `window.ICHAT_MOBILE_ENV` 的代码"，该句已被 T22 AND-01 整改证伪（APK 内确有该文件），现更正。

关键约束（写进 `mobile/www/js/mobile-config.js` 并由 `mobile/tests/mobile-config.test.js` 守护）：

1. 非 loopback 必须 HTTPS。`http://` 远程地址直接判 `unconfigured`。
   实测未被绕过：大小写 scheme、`localhost.evil.com`、`127.0.0.1.nip.io`、`localhost@evil.com`
   全部落 `unconfigured`；`0x7f.0.0.1`/`2130706433` 会被 `URL` 归一化成 `127.0.0.1`，语义上确为 loopback。
2. `server.url`（热重载）**只允许开发模式显式开启**；发布构建解析结果为 `null`，
   `capacitor.config.json` 不带 `server` 块——避免把开发机地址打进 APK。
3. Origin 归一化必须与 `desktop/app-config.js` 一致（小写 host、去默认端口），否则跨端测试无法比较。

### 3.2 `AllowedHostsOriginValidator` 的两种行为

| 客户端 | `Origin` 头 | 结论 |
| --- | --- | --- |
| WebView（**T24 之后**同源加载页面） | 预期自动带 `https://<origin>` | 与 Web 相同，服务端现有 `ALLOWED_HOSTS` / Origin 校验应直接放行——**尚未实测** |
| 原生 OkHttp（若将来转原生） | **默认不发** | `AllowedHostsOriginValidator` 对缺失 `Origin` 的处理需实测；转原生时必须单独验证 |

**本卡未验证**：WebView 在 `wss://` 下的真实 `Origin` 行为。当前构建根本不加载远端页面，
所以这一条**连测试的前提都不存在**，要等 T24 接通同源加载后才谈得上验证。
交付里按"未验证"记，不写"已通过"。

### 3.3 何时才需要 token 端点（记录，不在本卡实施）

- 需要**免 WebView** 的原生后台服务（前台服务里跑 OkHttp）时；
- 需要**设备级身份**（#167）取代账号级 Session 时；
- 需要**第三方/无浏览器环境**接入时。

新增 token 端点属协议扩展：**必须先写 ADR**，并与账号模块（[accounts-auth.md](../handoff/accounts-auth.md)）
和消息模块串行整合；本 ADR **不授权**任何 `accounts/` 改动。

---

## 4. 工程骨架

### 4.1 目录结构（已落地）

```
mobile/
├── package.json                    # 依赖固定为精确版本，不用 ^
├── capacitor.config.json           # 由 scripts/write-capacitor-config.js 生成，发布模式无 server 块
├── .gitignore                      # 忽略构建产物 www/js/mobile-env.js(+.tmp) 与 capacitor.config.json.tmp
├── scripts/
│   └── write-capacitor-config.js   # 从 www/js/mobile-config.js 契约生成配置；未配置时 exit 1 且不写任何文件
├── www/                            # WebView 打包进 APK 的本地资源
│   ├── index.html                  # 骨架启动页（不是聊天 UI），按 env → config → shell 顺序加载脚本
│   ├── css/mobile-shell.css        # 安全区适配；不依赖 Django 静态资源
│   └── js/
│       ├── mobile-config.js        # 纯函数配置契约（IIFE，Node 可测；浏览器只暴露 window.iChatMobileConfig）
│       ├── mobile-env.js           # 【构建产物，gitignore】config:generate 写入的 window.ICHAT_MOBILE_ENV
│       ├── mobile-env.example.js   # 上面那份产物的形态说明（入库）
│       └── shell.js                # 启动页渲染，仅读 window.ICHAT_MOBILE_ENV
├── tests/
│   ├── mobile-config.test.js       # node:test，13 条：origin/HTTPS/WSS/live-reload 契约
│   └── mobile-env.test.js          # node:test，17 条：注入链路、未配置/陈旧产物、行尾无关、脚本顺序与位置、不可读 config
└── android/                        # Capacitor CLI 真实生成的原生工程
```

> 测试共 **30 条**（13 + 17，实测值）。`npm run test:mobile` 同时加载两个文件；手写 `node --test` 时
> 两个文件都要给，只跑 `mobile-config.test.js` 会漏掉注入链路那 17 条。
> 行尾无关性单独有 3 条用例（`normaliseEol` 让整套断言在 `core.autocrlf=true` 的 CRLF 工作树下等价），
> 见 §8.5。

### 4.2 固定工具链版本

| 组件 | 版本 | 来源 |
| --- | --- | --- |
| Node | v24.14.1 | 本机实测 |
| npm | 11.13.0 | 本机实测 |
| Capacitor CLI / core / android | **8.5.3** | `mobile/package.json`（精确版本） |
| `@capacitor/app` | 8.1.2 | 同上 |
| `@capacitor/network` | 8.0.1 | 同上 |
| `@capacitor/preferences` | 8.0.1 | 同上 |
| Gradle | wrapper 期望 **8.14.3** | `mobile/android/gradle/wrapper/gradle-wrapper.properties`（CLI 生成） |
| Gradle（本次实际使用） | **9.5.1**（本机已缓存；wrapper 8.14.3 下载被阻断，见 §5.3） | 非固定版本，CI 须用 `./gradlew` 复跑 |
| Android Gradle Plugin | 8.13.0 | `mobile/android/build.gradle`（CLI 生成） |
| compileSdk / targetSdk | 36 | `mobile/android/variables.gradle` |
| minSdk | 24 | 同上 |
| JDK | **21+ 必需** | `mobile/android/app/capacitor.build.gradle:5-6` 硬编码 `JavaVersion.VERSION_21` |
| JDK（本次实际使用） | **OpenJDK 23.0.1** | §5 |
| Android SDK | Platform 36 + Build-Tools 36.0.0，安装在 `mobile/.android-sdk`（已被 gitignore） | §5 |

> **JDK 版本是 21，不是 17。** AGP 8.13 本身只要 17，但 Capacitor 8 生成的
> `app/capacitor.build.gradle` 把 `sourceCompatibility`/`targetCompatibility` 都写成
> `JavaVersion.VERSION_21`。装 JDK 17 会在 `compileJava` 阶段失败。§6 的复现步骤已按此更正。
>
> 依赖**故意不用 `^`**：`package-lock.json` 已提交，`^` 会让构建机在 lock 缺失时漂移。

### 4.3 与现有代码的接口面

| 骨架产物 | 说明 |
| --- | --- |
| `www/js/mobile-config.js` | 只做配置解析；不改协议、不引依赖 |
| `www/index.html` | 骨架页；**正式聊天页仍是 Django 模板**，由 WebView 加载 |
| `android/` | CLI 生成，仅 `variables.gradle` / `AndroidManifest.xml` 会在后续卡按需改动 |

---

## 5. 验收对齐（Issue #149 原文四条）

### 5.1 本次真实执行的命令与结果

```powershell
# 依赖安装
cd mobile
npm install --no-audit --no-fund
# -> added 101 packages in 14s

# 生成原生工程（真实执行，非手抄）
# 【仅首次创建工程时需要】mobile/android/ 已在仓库里；对既有工程重复执行会
# exit 1（"android platform already exists"）。日常构建请从下面的 config:generate 开始。
npx cap add android
# -> √ Adding native android project in android
#    [info] Found 3 Capacitor plugins for android:
#           @capacitor/app@8.1.2, @capacitor/network@8.0.1, @capacitor/preferences@8.0.1
#    [success] android platform added!

# 【AND-01 起新增的必经步骤】解析服务地址并生成运行时注入文件
# 读的是**进程环境变量**，不读 mobile/.env；不设变量时 exit 1，两个文件都不写。
$env:ICHAT_MOBILE_SERVER_URL = 'https://chat.example.com:8443'
npm run config:generate
# -> [iChat mobile] 已写入 ...\mobile-env.js（运行时注入 window.ICHAT_MOBILE_ENV）
#    [iChat mobile] origin=https://chat.example.com:8443 ws=wss://chat.example.com:8443/ws/chat/
#    [iChat mobile] server.url=(未设置，发布模式)

# 同步 web 资源（含 www/js/mobile-env.js）
npx cap sync android
# -> √ Copying web assets from www to android\app\src\main\assets\public
#    √ Sync finished in 0.082s

# 构建调试包（Gradle 9.5.1，见 §5.3 偏差）
$env:JAVA_HOME="$env:USERPROFILE\.jdks\openjdk-23.0.1"
$env:ANDROID_HOME="<repo>\mobile\.android-sdk"
cd android
gradle assembleDebug --no-daemon --console=plain
# -> BUILD SUCCESSFUL in 2m 23s
#    183 actionable tasks: 183 executed

# manifest 加固（禁用备份）后重新构建并复验
gradle assembleDebug --no-daemon --console=plain
# -> BUILD SUCCESSFUL in 14s  （183 tasks: 13 executed, 170 up-to-date）

# 第 2 轮整改：改了 www/js/mobile-config.js 之后，必须先 cap sync 再构建！
# （漏这一步会让 APK 内嵌旧版 web 资源——第 2 轮审查正是抓住了这点）
cd ..                      # 回到 mobile/
npx cap sync android
# -> √ Copying web assets from www to android\app\src\main\assets\public
#    √ Sync finished in 0.113s
cd android
gradle assembleDebug --no-daemon --console=plain
# -> BUILD SUCCESSFUL in 12s  （183 tasks: 27 executed, 156 up-to-date）
```

> **改 `mobile/www/**` 后必须 `npx cap sync android` 再构建。** `gradle assembleDebug` 只编译
> 已经 synced 进 `android/app/src/main/assets/public/` 的副本，不会自己回头看 `www/`。
>
> **`npm run config:generate` 同样是必经步骤**（AND-01 起）。跳过它，或只写了 `mobile/.env`
> 而没有导出进程环境变量，结果是：生成器 `exit 1`、不写 `www/js/mobile-env.js`，此时若直接
> `cap sync` + 构建，得到的是**不带服务地址的包**，启动页永远停在"未配置"。这是 AND-01 的原始缺陷，
> 整改后由 `config:generate` 补齐，并由 `mobile/tests/mobile-env.test.js` 守护。

产物与校验：

**⚠️ 下面这个 `67104134…` 是修复前（2026-10-18）的产物，已过期，不要拿它当当前状态。**
它的包里 `index.html` 仍是旧文案、没有 `www/js/mobile-env.js`，也就是启动页永远停在"未配置"的那个包。
仓库里 `mobile/android/app/build/outputs/apk/debug/app-debug.apk` 目前仍是这一份（`build/` 已 gitignore，不入库）。

```
# 已过期（T22 初版 / AND-01 修复前产物）
mobile/android/app/build/outputs/apk/debug/app-debug.apk
  大小    4.22 MB (4,424,830 bytes)
  SHA256  671041340377964EC10EBC495D4CC087F390B2E4A0C6F8BB5080BAEAFA26C878
```

AND-01 整改后重新构建的产物（当前形态，2026-10-26，`config:generate` → `cap sync` → `assembleDebug`）：

```
  大小    4,138,535 bytes
  SHA256  14B41ADAF2C5A7E8A2C78D817D7E73C8BC63EA97553C3E9B66040A84C5EB48E9
  构建环境  Gradle 9.5.1 + OpenJDK 23.0.1（wrapper 8.14.3 未解包，见 §5.3）
```

> **这个 SHA 只在同一环境下可复现**：它含 Gradle/AGP 的时间戳与签名差异，换机器、换 Gradle 版本
> 都会变（前几轮的 ①–④ 也是同一原因各不相同）。要复核"包内资源对不对"，**比对 SHA 无用，比对
> 包内 `assets/public/**` 的字节才有意义**，见下面 §5.1 的资源清单。
> 复现命令：`npm run config:generate` → `npx cap sync android` → `cd android; .\gradlew.bat assembleDebug`。

> 构建历史（每次的差异都是实质改动，不是重建噪声）：
> ① 初版 3.94 MB / `F10D3570…CDFCD2`；
> ② manifest 禁用备份后 4.22 MB / `0D05C92B…4B4976`；
> ③ 审查整改（收窄 FileProvider、修仪器化断言）后 `FCE3182E…66C7AE6`
> —— **但这一步漏跑了 `npx cap sync`**，APK 内嵌的 `mobile-config.js` 仍是 5152 B 的旧版
> （`cleartext:false`），所以该 SHA 无法按文档复现。**这是第 2 轮审查发现的证据缺陷，已修**；
> ④ 重跑 `cap sync` + 重构 `buildCapacitorConfig`（不再信任 `insecure` 标志）后为
> `67104134…26C878`：APK 内 `mobile-config.js` 为 **6064 B**，与当时的 `mobile/www/js/mobile-config.js`
> 逐字节一致（第 3 轮审查从 zip 解出后做过全字节比对，四个资源文件全部 identical）；
> ⑤ **AND-01 整改**（构建期注入 `window.ICHAT_MOBILE_ENV`、修正启动页步骤、`mobile-config.js` 改 IIFE）
> 后为 `14B41ADA…EB48E9`：包内多出 `assets/public/js/mobile-env.js`（389 B），`index.html` 由
> 2015 B 增至 4017 B，`mobile-config.js` 由 6064 B 增至 8766 B。
> 第③步那个包里的才是旧实现（`cleartext: resolved.insecure === true`），现已不存在于产物中。
> **产物不入库**（`build/` 已 gitignore）。

APK 内容核验（`aapt2 dump badging` + `aapt2 dump xmltree` + zip 目录，**真实输出**）：

```
package: name='com.ichat.pro' versionCode='1' versionName='1.0'
compileSdkVersion='36'
minSdkVersion:'24'
targetSdkVersion:'36'
uses-permission: name='android.permission.INTERNET'
uses-permission: name='android.permission.ACCESS_NETWORK_STATE'
application-label:'iChat Pro'
launchable-activity: name='com.ichat.pro.MainActivity'  label='iChat Pro'

AndroidManifest 中被加固的三项（从 APK 内反查，不是看源码）：
  android:allowBackup=false
  android:fullBackupContent=false
  android:dataExtractionRules=@0x7f100001

assets/public/index.html              4017
assets/public/js/mobile-config.js     8766
assets/public/js/mobile-env.js         389   <- 构建期注入，含本次 origin；未配置构建无此文件
assets/public/js/mobile-env.example.js 1037  <- 形态说明，不被 index.html 加载
assets/public/js/shell.js             1702
assets/public/css/mobile-shell.css    2478
assets/public/cordova.js                 0   <- Capacitor 占位（空文件）
assets/public/cordova_plugins.js         0   <- Capacitor 占位（空文件）
```

> **为什么上面是 8 条而不是 5 条**（第 5 轮 F-07）：`assets/public/` 下共 8 个条目，其中 3 条不是我们
> 手写的页面资源——`mobile-env.example.js` 是随 `www/` 一起被 `cap sync` 复制的形态说明，
> `cordova.js` / `cordova_plugins.js` 是 Capacitor 自身生成的空占位。此前只列 5 条，容易让人误以为
> APK 里就这些文件。**5 条"我们自己的"资源仍逐字节核对**；另外 3 条不参与核对（两条是 0 字节占位，
> 一条是示例文件）。
>
> 8 个数字是**从 AND-01 产物 `14B41ADA…EB48E9` 的 zip 目录直接读出的未压缩字节数**，
> 并已逐字节比对确认与 `mobile/www/` 下同名文件完全一致（`mobile-env.js` 除外——它是构建产物，
> 与 `mobile/www/js/mobile-env.js` 一致，而后者不入库）。
> 注意 `assets/public/js/mobile-env.example.js`（1037 B）也会随 `www/` 一起进包：它只是形态说明、
> 不会被 `index.html` 加载，无功能影响；若后续要精简体积，可在 `cap sync` 前排除。
>
> 口径提醒：早期草稿写过 1962/5152/1646/2346，那是**误把 git blob 大小（LF 规范化后）当成
> APK 内文件大小**。其中 `index.html`/`shell.js`/`mobile-shell.css` 三个文件的 blob 在 `3cc4fc7`
> 到 `ac14a37` 之间没变过，所以那两个数字从一开始就不是"旧包大小"；`5152` 是 `mobile-config.js`
> 早期 blob 的 LF 字节数，而包内 CRLF 版本实际是 5317 B。**跨平台复现时字节数会因 CRLF/LF 不同而变化**，
> 引用这些数时请注明是"本机产物、未压缩字节数"。

→ 说明 `webDir: www` 生效，`www/` 里的骨架页确实被打进 APK，且**服务地址随包走**（构建期注入）。

### 5.1.1 骨架阶段就做掉的加固：禁用备份

Capacitor CLI 生成的 `AndroidManifest.xml` 默认 `android:allowBackup="true"`。结合本 ADR §2.3
的结论（长期 E2EE 私钥在 WebView 存储、**不受 Keystore 保护**），默认值意味着 `adb backup`
或云备份可以直接把私钥带离设备。本卡已改为：

- `android:allowBackup="false"`
- `android:fullBackupContent="false"`
- `android:dataExtractionRules="@xml/data_extraction_rules"`，其中 root/file/database/sharedpref/external
  全部 `exclude`（覆盖 Android 12+ 的云备份与设备间迁移）

这是**降低风险**，不是解决 T29：它挡的是"备份带走密钥"，挡不了"设备已 root"或"WebView 存储被读取"。
T29 仍需在 §2.3 的 (a)/(b)/(c) 中做出选择。

### 5.1.2 "未配置构建"的行为（AND-01 整改后）

不设置 `ICHAT_MOBILE_SERVER_URL`（或写成非法值）时，`npm run config:generate` 的行为是**失败关闭**：

```
exit code = 1
www/js/mobile-env.js          -> 不写；若上一次构建留下过，会被删除
capacitor.config.json         -> 保持"发布形态"：不含 server 块
stderr                        -> 配置无效 + 原因 + 对 config 与 mobile-env.js 的实际处置
```

`capacitor.config.json` 这一行**不是**"逐字节不变"。2026-10-27 第 3 轮审查 F-08 更正：
本段此前写的是 `逐字节不变（sha256 与运行前相同）`，并引用了一句 stderr
`"现有 capacitor.config.json 保持不变"`——那句话已经从生成器里删掉了，而这个行为
本身也已被同一轮提交改掉。实际行为是：

- 上一次是**发布形态**（无 `server` 块）→ 内容逐字节不变；
- 上一次是 `--live-reload` 形态（含 `server` 块）→ **被改写成发布形态**，
  `server` 键被移除，sha256 因此改变（实测 `86b6fdb6…` → `595be125…`）。

这是**故意**的：否则 hot-reload 的 `server.url` 会随 `cap sync` 进包。改写失败时会明确告警。
残留缺口见 §5.1.3。

### 5.1.3 config 存在但不可读时的处置（已修）

`mobile/scripts/write-capacitor-config.js` 读取现有 `capacitor.config.json` 失败时，
**不再**把它当成"文件不存在"。三态：

| 状态 | 行为 |
| --- | --- |
| 不存在（`ENOENT`） | 无需重置，正常继续 |
| 可读 | 解析后判断是否含 `server` 块；含则改写成发布形态，不含则逐字节不动 |
| **不可读** | **告警**：说明无法读取、无法判断是否含 `server` 块、可能产出热重载形态的 APK；并在汇总行注明"未做任何处置" |

在 `3d902de` 修复之前这里是两态：`catch` 把 `previousConfig` 一律置 `null`，于是"不可读"
被当成"不存在"，整个重置块**静默跳过、零输出**。

> 2026-10-27 第 4 轮复审 F-13 更正：本节最初（同一提交内）写成"**未修**…留作后续"，
> 而那个提交本身就把 F-10 修了，描述的修法与实现逐字一致。属自造的文档漂移，已改。

用例 `an unreadable capacitor.config.json is reported, not silently skipped` 覆盖此路径；
审查者另用真实 `EPERM`（`attrib +R` + `icacls /deny`）验证过与 `EISDIR` 走同一分支。

于是随后 `npx cap sync` + `assembleDebug` 得到的是一个**不带 origin 的包**：APK 里没有
`assets/public/js/mobile-env.js`，`www/index.html` 的 `<script src="js/mobile-env.js">` 404（经典脚本，
不影响后续脚本），`window.ICHAT_MOBILE_ENV` 为 `undefined`，`shell.js` 渲染 **"未配置"** 面板。
这是**预期行为**，不是缺陷——但它必须被看见，所以：

- 生成器在 stderr 明确说明是否删除了陈旧产物；
- 启动页在"未配置"面板里给出完整可执行步骤（见 `mobile/www/index.html`）；
- `mobile/tests/mobile-env.test.js` 覆盖三条：未配置不写、未配置删陈旧、写入失败清理 `.tmp`。

> **为什么未配置要删旧产物而不是留着**：`npx cap sync` 只复制文件，不会判断内容。若旧
> `mobile-env.js` 还在，包会带着**上一次的 origin** 发布，启动页显示"就绪"却指向错误服务器——
> 这比"未配置"更糟，因为它不会报错。删除是唯一安全的默认。


### 5.2 四条验收逐项对齐

| 验收项 | 本卡状态 | 证据 |
| --- | --- | --- |
| 能复用现有页面并生成 Android 工程 | **部分满足**：工程已生成且可构建出 APK；"复用现有页面"的**运行时**验证需 T24 出包后在设备上加载 Django 页面 | §5.1 的 `npx cap add android` 输出 + APK 产物 + `launchable-activity` |
| 相关代码或配置经过非作者成员评审 | **证据不足，不能由本卡判定通过**：七轮独立审查均有结论，但审查者是子 Agent、产物在 `.review/`（已 gitignore），**本卡全部提交的作者只有一人**（`git log cc89c11..HEAD --format='%an' \| sort -u` 只有 `HeanX`；提交数会随补修增长，故不写死）。可审计性上这条**不成立**，需真人评审后在 Issue 留痕 | §8（各轮记录是文字，不是可审计产物） |
| 适用的自动化或人工测试已执行并保存证据 | `cd mobile && npm run test:mobile`（同时加载 `tests/mobile-config.test.js` + `tests/mobile-env.test.js`）**30/30 通过**；仓库根 `node --test mobile/tests/*.test.js` 同为 30/30；**CRLF 工作树（`core.autocrlf=true`）下同为 30/30**。`manage.py check` / `makemigrations --check` 均实跑；`npm run test:e2ee` 实测 **170/170**（2026-10-27 更正：此前写 62/62，那是更早一轮的旧值，现已作废）；**设备安装/启动/截图未执行** | §5.1 |
| 需求、部署或测试文档已同步更新 | 本次（2026-10-26 建立，2026-10-27 第 4 轮复审 F-14 后同步到 **30**）同步了**四份已跟踪文档**：本 ADR、`docs/handoff/android-mobile.md`（§1 清单/§6 命令块）、`docs/phase4/tasks.md`（T22 行）、`docs/documentation-status.md`（两段增补），另加 `docs/phase4/traceability-matrix.md` 的 NEW-TST-009 行。四份均已 `git ls-files` 确认在版本控制内，条数统一为实测 **30 条**（13 + 17）、旧 SHA 已标注过期、`config:generate` 已写入必经步骤。**但必须承认**：前六轮里有四轮出现过"文档数字与事实不符"——第 4 轮把 `android-mobile.md` 误判为未跟踪而漏改（§8.5 F-01）；第 5 轮（`07815e6`）把 27 加到 29 时**同步做了文档**，但同轮改错了另一处（§8.6 F-13）；**`3d902de` 把 29 加到 30 却一处文档都没更新**（§8.6 F-14）；第 6 轮修 F-14 时又把历史记录一起改了（§8.6 N-01）。所以这条在本卡内**反复失守过**，不能凭本轮通过就认为流程可靠 | §8.1–§8.6 |

### 5.3 明确未完成的项（不得当作通过）

| 项 | 原因 |
| --- | --- |
| `adb install` + 启动 + 截图 | `adb devices` 无设备；SDK 无 `emulator` 包；`HypervisorPresent=False`（虚拟化未开），模拟器不可用 |
| 两个 API Level / 两种设备配置的运行记录 | 同上，无任何可运行目标 |
| WebView 内登录 → WS → 收发一条消息 | 属 T24 及之后范围，本卡不含 |
| 用固定 wrapper Gradle 8.14.3 构建 | 该发行版在 GitHub Releases，本机到该主机连接被重置；改用本机缓存的 Gradle 9.5.1。**CI 必须用 `./gradlew` 复跑** |
| WebView 实际 `Origin` 头行为 | 当前构建不加载远端页面，测试前提不存在；需 T24 接通同源加载 + 设备抓包，见 §3.2 |
| **`deploy/tests` 的 7 条冒烟用例** | 本机无 `bash`（`Get-Command bash` 为空），`manage.py test deploy.tests` 输出 `Ran 11 tests / OK (skipped=7)`。**`OK` 带 skip 不等于通过**，这 7 条在本机从未真正执行 |
| **`ExampleUnitTest` 没有构成任何覆盖** | 它仍是模板的 `assertEquals(4, 2+2)`，只证明 JVM 单测能跑通，不验证任何 iChat 行为。不要把它算进"测试已执行"的数量 |
| **CI 用 `./gradlew` 复跑** | 本卡未能执行（见上一行"固定 wrapper"），因此"工程能在标准 CI 上构建"这句话**目前没有证据** |
| **审查者独立性无法审计** | 三轮审查都是子 Agent，产物在 gitignore 的 `.review/` 里，提交作者单一。见 §5.2 第 2 条 |
| **debug APK 含 `debuggable=true`** | debug 构建的标志位。**真正的风险不是 WebView 远程调试**：`capacitor.config.json` 已显式写 `webContentsDebuggingEnabled: false`，Capacitor 会据此调用 `setWebContentsDebuggingEnabled(false)`，DevTools 附加不上（`isDebug` 只是**缺省值**，显式值优先）。**实际风险是 `adb run-as com.ichat.pro`**：`debuggable=true` 允许通过 adb 以应用身份读取私有目录，而这正是 WebView 存储 E2EE 私钥的地方。因此**该 debug APK 不得分发给任何人试用，也不得用于任何真实账号**。release 构建 `debuggable=false`，无此问题；T29 落地后仍需重新评估。 |

---

## 6. 复现构建的步骤（下一位接手者 / CI 执行）

```powershell
# 1) JDK 21 或更高（不是 17！Capacitor 8 的 capacitor.build.gradle 固定 VERSION_21）
$env:JAVA_HOME = 'C:\Program Files\Eclipse Adoptium\jdk-21'
java -version          # 期望 21.x+；本机实测 OpenJDK 23.0.1 可用

# 2) Android SDK Platform 36 + Build-Tools 36（Capacitor 8 的 compileSdk=36）
#    注意：C:\Program Files (x86)\Android\android-sdk 不可写且只有 33/34，
#    必须换一个可写根目录（本卡用 mobile/.android-sdk，已在 .gitignore）
$sdk = '<repo>\mobile\.android-sdk'
& "C:\Program Files (x86)\Android\android-sdk\cmdline-tools\11.0\bin\sdkmanager.bat" `
    --sdk_root="$sdk" --licenses
& "C:\Program Files (x86)\Android\android-sdk\cmdline-tools\11.0\bin\sdkmanager.bat" `
    --sdk_root="$sdk" "platforms;android-36" "build-tools;36.0.0" "platform-tools"
$env:ANDROID_HOME = $sdk

# 3) 生成配置 → 同步 → 构建（有网络访问 services.gradle.org 时用 wrapper，这是正式路径）
cd mobile
npm install

# 3a) 【必经】注入服务地址。读进程环境变量，不读 mobile/.env。
#     不设这一步 = 打出没有 origin 的包 = 启动页永远"未配置"（AND-01 原缺陷）。
$env:ICHAT_MOBILE_SERVER_URL = 'https://<你的服务器>:8443'
npm run config:generate
#     期望：exit 0，写出 www/js/mobile-env.js 与 capacitor.config.json（后者仍无 server 块）
#     CI 里应显式断言 exit 0，否则 config:generate 失败后 cap sync 仍会继续并打包出"未配置"包。

npx cap sync android               # 把 www/（含 mobile-env.js）复制进原生工程
cd android
.\gradlew.bat assembleDebug        # 期望 app/build/outputs/apk/debug/app-debug.apk

# 无外网时（仅本地验证）：用本机已缓存的 Gradle 代替 wrapper
$env:GRADLE_HOME = "$env:USERPROFILE\.gradle\wrapper\dists\gradle-9.5.1-bin\<hash>\gradle-9.5.1"
& "$env:GRADLE_HOME\bin\gradle.bat" assembleDebug --no-daemon --console=plain

# 4) 真机/模拟器（本机当前不具备）
adb devices
adb install -r app\build\outputs\apk\debug\app-debug.apk
adb shell am start -n com.ichat.pro/.MainActivity
```

> `C:\Program Files (x86)\Android\android-sdk` **不可写**（Program Files 受保护），
> 且只装了 SDK 33/34，`sdkmanager` 在该目录下无法安装 36（尝试写入会 `访问被拒绝`）。
> 必须用 `--sdk_root` 指向可写目录，或安装 Android Studio 默认 SDK。
>
> 模拟器不可用的实测依据：`HypervisorPresent=False`（无 Hyper-V / WHPX），
> 且 SDK 中无 `emulator` 包。开启虚拟化后可 `sdkmanager "emulator" "system-images;android-36;google_apis;x86_64"`。

---

## 7. 风险与遗留（诚实声明）

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 无非导出私钥的 Keystore 保护 | T29 可能不达"普通存储读不到"的严格口径 | §2.3 方案 (b)，需与 #166 串行 |
| 无设备身份模型（#167） | Android 与 Windows 共用同一 `UserPublicKey`；新设备解不开旧消息；撤销只到账号级 | 本 ADR 记录为产品限制，不做假的设备级承诺 |
| 后台 WS 被系统杀 | 断线期间收不到推送 | 前台服务（T27）+ 补取接口（已有 `sync`）兜底 |
| 备份带走 WebView 私钥 | 私钥随 `adb backup` / 云备份泄露 | **本卡已挡**：`allowBackup=false` + `dataExtractionRules` 全 exclude（§5.1.1）。仍不覆盖 root/运行时读取，最终由 T29 决定 |
| WebView 版本碎片（minSdk 24 对应 WebView 可能很旧） | WebCrypto / ES2020 能力不足 | T24 出包后按 WebView 版本设下限 |
| 本机无 JDK 21 / SDK 36 / 真机 | 构建与安装证据缺失 | 已用 OpenJDK 23 + 可写 SDK 补上构建；**真机仍缺失**，见 §5.3 |
| wrapper Gradle 版本无法下载 | 产物非固定版本构建 | §5.3；CI 必须用 `./gradlew` 复跑 |
| 生成的模板代码未被替换 | `MainActivity.java`、launcher 图标仍是 Capacitor 默认 | T24 会用正式图标/品牌替换（参考 Windows `windows-branding.md` 的做法） |

---

## 8. 审查记录

| 轮次 | 审查者 | 结论 | 发现项 | 整改 |
| --- | --- | --- | --- | --- |
| 1 | 独立审查子 Agent（非作者上下文，只给 Issue 原文 + diff + 命令） | **PASS WITH FINDINGS** | 2 BLOCKER / 5 MAJOR / 5 MINOR | 见 §8.1 |
| 2 | 新的独立审查子 Agent（不复用第 1 轮会话） | **PASS WITH FINDINGS** | 0 BLOCKER / 3 MAJOR / 6 MINOR | 见 §8.2 |
| 3 | 又一个新的独立审查子 Agent（复核第 2 轮整改） | **PASS WITH FINDINGS** | 0 BLOCKER / 3 MAJOR / 4 MINOR | 见 §8.3 |
| 4 | 又一个新的独立审查子 Agent（复核 AND-01 整改 `ac14a37`） | **PASS WITH FINDINGS** | 0 BLOCKER / 1 MAJOR / 4 MINOR | 见 §8.4 |
| 5 | 又一个新的独立审查子 Agent（复核第 4 轮整改 `3129122`/`62d673e`） | **PASS WITH FINDINGS** | 0 BLOCKER / 1 MAJOR / 5 MINOR | 见 §8.5 |

### 8.1 第 1 轮发现项与整改

| 级别 | 问题 | 整改 |
| --- | --- | --- |
| BLOCKER | `mobile/android/gradlew` 以 mode `100644` 入库（Windows `core.filemode=false`），Linux/macOS CI 的 `./gradlew` 会 permission denied | `git update-index --chmod=+x`，现为 `100755` |
| BLOCKER | ADR 写"JDK 17+ 必需"，但 Capacitor 8 生成的 `app/capacitor.build.gradle` 硬编码 `JavaVersion.VERSION_21`，JDK 17 会在 `compileJava` 失败 | §0/§4.2/§6/§7 与本模块交接文档统一改为 **JDK 21+**，并注明依据 |
| MAJOR | `docs/documentation-status.md` 仍写"未产出 APK"，与 ADR/handoff 矛盾 | 已改为"APK 已构建 + 真机未执行" |
| MAJOR | `tasks.md` T22 行写"APK 产物…未执行 / 缺 JDK 17+"，与事实冲突；链接错指 §6 | 重写该行，链接改 §5.3 |
| MAJOR | §0 结论 2 与 §3.1 把"同源加载 Django 页面"写成现在时，实际 `server.url=null` 只加载本地 assets | §0/§3.1 改为"目标形态"，明确 T22 未接通、属 T24 |
| MAJOR | debug APK 含 `debuggable=true`，ADR 未披露其风险 | §5.3 增列：风险是 `adb run-as` 可读应用私有目录（WebView 存储 E2EE 私钥），**不得分发试用、不得配真实账号**。第 2 轮更正：原先写成"可被 DevTools 远程调试"是错的，配置里显式 `false` 会生效 |
| MAJOR | `write-capacitor-config.js` 失败时仍写文件，可能覆盖受版本控制的 `capacitor.config.json` | 失败路径改为**先退出、不写文件**（实测 exit 1 且文件未变） |
| MINOR | 仪器化测试断言 `com.getcapacitor.app`，与 `applicationId=com.ichat.pro` 不符，首跑必红 | 改为 `com.ichat.pro` |
| MINOR | `file_paths.xml` 的 `external-path path="."` 把整个外部存储暴露给 FileProvider | 收窄为 `ichat/` 子目录的 cache/files/external-files 三条 |
| MINOR | live-reload 配 `http://localhost` 却写 `cleartext:false`，WebView 会拒绝加载 | `cleartext` 改为镜像 scheme；新增测试守护 |
| MINOR | `@capacitor/cli` 仅 devDependency，`npm ci --omit=dev` 下 `cap` 不可用 | 脚本改用 `npx --no-install cap`，失败时明确报错而非静默装包 |
| MINOR | `data_extraction_rules.xml` 是否漏 `device_*` | 复查结论：**未漏**，前五个 domain 是官方最小写法，保留 |

整改后复验：`node --test` 通过；重新构建 `BUILD SUCCESSFUL`，并从 APK 二进制清单反查确认
`allowBackup=false`、`dataExtractionRules` 生效、FileProvider 仅三条收窄路径。

### 8.2 第 2 轮发现项与整改

第 2 轮由**新的**审查子 Agent 执行，它没有拿到第 1 轮的结论，全部自行验证。结果：0 BLOCKER，
但它抓出 3 处**我（作者）的整改声明本身不实/不全**，值得记下来：

| 级别 | 问题 | 整改 |
| --- | --- | --- |
| MAJOR | 测试条数只同步了一半：handoff 两处、ADR 两处仍写 10 条，而 §8.1 与 `tasks.md` 已写 11/11（实际 11） | 当时补齐为 11；后续又增至 **13 条**。**注意：`tasks.md` 的 `11/11` 这一轮漏改，由第 3 轮审查抓出（§8.3）——所以"四处已一致"这句当时是假的** |
| MAJOR | §5.3 的 `debuggable` 安全披露**机制写反了**：原称 `webContentsDebuggingEnabled:false` 压不住、APK 可被 DevTools 调试。实际配置里显式 `false` 会生效，DevTools 附加不上；**真正风险是 `adb run-as` 读应用私有目录（WebView 里的 E2EE 私钥）**，原稿完全没提 | 已重写 §5.3 该条与 §8.1 对应行，写明正确机制与真实风险 |
| MAJOR | **APK 内嵌 web 资源是旧版**：`mobile-config.js` 仍是旧 `cleartext:false` 实现，因改完源码后漏跑 `npx cap sync`，而 §5.1 命令块里根本没有这一步 → 该 SHA 按文档复现不出来 | 补跑 `cap sync` 重建；§5.1 补入 `cap sync` 步骤并加粗警告；构建历史记为 ③→④ |
| MINOR | `file_paths.xml` 的 `ichat/` 子目录约定只写在 XML 注释里，T28 会踩 `IllegalArgumentException` | 写进本模块交接文档 §6.1，含"改 `www/` 必须先 `cap sync`"的同类提醒 |
| MINOR | 本轮编辑给 3 份文档新加了 UTF-8 BOM（原文件无 BOM） | 已用 `UTF8Encoding($false)` 全部剥除；第 3 轮复验四份 BOM=False、全仓 317 个 tracked 文本文件零 BOM |
| MINOR | `buildCapacitorConfig` 直接信任传入的 `insecure` 标志，伪造对象可得 `cleartext:true` | 改为从 `serverUrl` 自校验（`normalizeOrigin` + `isLoopback`），新增 2 条测试。第 3 轮用**变异测试**证明这两条测试有区分度（回退实现后立刻 2 条失败） |
| MINOR | `ExampleUnitTest` 仍是 `assertEquals(4,2+2)` 模板垃圾 | 保留（不属本卡功能范围），在 §5.3 未验证项注明它不构成任何覆盖 |
| MINOR | 新文档链接的 `conventions.md` / `testing-quality-ci.md` 在本分支未被 git 跟踪 | 属工作区既有状况（交接文档整体尚未入库），**不是本卡引入**；提 PR 时需一并提交 |
| MINOR | 无 `.gitattributes` 钉住换行/二进制处理 | 新增根 `.gitattributes`。第 3 轮核实其技术断言**正确**：执行位只能靠 index 改，gitattributes 无 `mode` 属性 |

第 2 轮整改后复验：`node --test` **13/13 通过**；`cap sync` + `assembleDebug` 成功，APK 内
`mobile-config.js` 为 **6064 B** 且与 `mobile/www/` 逐字节一致（已从 zip 内解出比对）。

### 8.3 第 3 轮发现项与整改

第 3 轮又换了新上下文。0 BLOCKER。它的核心判断是：**代码与产物是真材实料，问题全在
"我这个作者对自己证据的描述"上**——这一点值得完整记下来，因为它是本次交付最真实的教训。

| 级别 | 问题 | 整改 |
| --- | --- | --- |
| MAJOR | `tasks.md` T22 行本轮换了 SHA 却没改同一行的 `11/11`（实际 13），而 §8.2 刚写完"四处表述已一致" | 改为 `13/13`；§8.2 那句已加注"当时是假的" |
| MAJOR | ADR §8 汇总表两轮的 MINOR 计数（6/7）与 §8.1、§8.2 表内实际行数（5/6）各多报 1 | 汇总表改为 5/5/6，与表格实数对齐 |
| MAJOR | `docs/handoff/issue-backlog.md` 仍残留 `10/10` 与旧 SHA `0D05C92B…4B4976` | 该文件在写这一行时**尚未入库**，故当时判为"不属本卡改动范围"，并在 §8.2 记录"提 PR 时需一并同步"。**2026-10-27 第 5 轮复审指出此判断已失效**：该文件随后在 `ff31f5e` 入库（`git log --diff-filter=A` 可证），那两处陈旧数字因此成为**已跟踪文档的现行错误**，本轮一并改正（`30/30` + 旧 SHA 标注过期） |
| MINOR | §5.1 第 262 行把"旧实现含 `resolved.insecure === true`"写进了描述第④步的句子，字面自相矛盾（第 3 轮实测产物中确实不含该串） | 改为"第③步那个包里才是旧实现，现已不存在于产物中" |
| MINOR | §5.1 把 1962/5152/1646/2346 说成"step ③ 的旧包大小"——实为把 **git blob（LF）大小**误当 APK 内（CRLF）大小；三个文件 blob 从未变过 | 已改写为口径说明：明确是"本机产物未压缩字节数"，并解释 CRLF/LF 差异 |
| MINOR | `deploy/deploy.sh` 有两处 UTF-8 乱码（`鈥?`/`搂7`），在本分支 HEAD 里 | 属 T23/#204 范围，**不是本卡引入**；记录备查，不代改 |
| MINOR | `write-capacitor-config.js` 在父目录不存在时抛裸 `ENOENT` | 正常失败模式，不影响验收；保留 |

第 3 轮独立验证通过的关键项（值得记录，因为它证明整改不是自说自话）：
- APK 的 SHA256、字节数、四个资源文件与源码**逐字节 identical**，与 ADR 声称值精确匹配；
- `buildCapacitorConfig` 对 6 组伪造对象全部挡住，**变异测试**（回退旧实现）能立刻让 2 条测试转红；
- `gradlew` 为 `100755`；`git show --stat 5302646` 证明只有 mode 变更、0 行增删；
- 全仓 317 个 tracked 文本文件零 BOM；
- 未触碰 `accounts/`、`chat/`、`settings.py`、迁移；无 `*.apk`/`*.jks`/`.env` 入库；
- `file_paths.xml` 的收窄已溯源到 `@capacitor/cli/assets/android-template.tar.gz` 的原始模板，确认是真整改。

> 按 `conventions.md` §1：三轮审查各自独立开新上下文，均未复用会话，且都没拿到作者的实现说明。
> **三轮结论都是 PASS WITH FINDINGS，从未出现纯 PASS。** 按规则第 3 轮已到上限（连续 3 轮不通过
> 应停下问人），因此本卡**就此停下，不自行放行**：只提交证据与未验证范围，由人来决定是否关闭 #149。

### 8.4 第 4 轮（AND-01 整改复核）发现项与整改

第 4 轮是**全新上下文**的独立审查，对象是 AND-01 整改提交 `ac14a37`。它用三种手段（sha256 复算、
`vm` 执行、自写严格 DOM 桩）独立复现了整条链路，确认"按启动页步骤操作现在确实能生效"，
**0 BLOCKER**。1 MAJOR + 4 MINOR 全部是**证据与边界**问题，不是功能问题：

| 级别 | 问题 | 整改 |
| --- | --- | --- |
| **MAJOR (F-06)** | **ADR / 交接文档整体停在修复前**：① §3.1 仍写"APK 内也没有注入 `window.ICHAT_MOBILE_ENV` 的代码"，被本轮实现证伪；② 测试条数写 13（实际 25），且验收表里的命令根本不加载新测试文件；③ §5.1 资源清单是 4 个文件、`index.html 2015 B`；④ 旧 APK SHA 未标注过期；⑤ **§5.1 命令序列缺 `npm run config:generate`**——照抄会打出没有 origin 的包，正是 AND-01 的原缺陷形态 | 本 ADR 六处全部改正：§3.1 改述构建期注入（并说明运行时仍未实现）、§4.1 目录树与条数、§5.1 命令序列插入生成步骤+过期标注+新 SHA、§5.1 资源清单按当前产物重写、**新增 §5.1.2 "未配置构建"**、§5.2 测试条数、§6 复现步骤 3a。`docs/handoff/android-mobile.md` 是未跟踪的用户材料，**未改**，需交接文档负责人同步（见 §8.4 末尾） |
| MINOR (F-01) | `mobile-env.test.js` 的顺序断言**恒真**：`indexOf('js/mobile-env.js')` 命中的是散文（offset 936）而不是标签（offset 3182），6/6 变异全部漏检，包括"把脚本挪到 `shell.js` 之后" | 改为真正解析 `<script src>` 序列并断言 `['js/mobile-env.js','js/mobile-config.js','js/shell.js']`，另拒绝 `defer`/`async`/`type=module`；**新增一条自检测试**用 3 个变异证明这个守卫有区分度。变异复核：修复前 7/7 漏检 → 修复后 6/6 检出 |
| MINOR (F-02) | `writeAtomic` 不清理 `.tmp`；**未配置分支不删陈旧的 `mobile-env.js`**（陈旧 origin 会被 `cap sync` 打进包，页面还会显示"就绪"指向错误服务器）；`capacitor.config.json.tmp` 未进 `.gitignore` | 未配置分支**删除**陈旧产物（删不掉则显式告警），`writeAtomic` 失败必清 `.tmp`；写入顺序改为 env 先、config 后，config 写失败则回滚删除 env（失败关闭）；`.gitignore` 补 `capacitor.config.json.tmp`。新增 2 条测试覆盖 |
| MINOR (F-04) | `mobile-config.js` 顶层函数泄漏成 7 个 window 全局（基线已有 6 个，本轮 +1） | 整体包进 IIFE，浏览器只暴露 `window.iChatMobileConfig`；Node 侧 `module.exports` 不变（测试与生成器仍 `require`）。正文**故意不重新缩进**，`git diff -w` 只有包装层，便于审查。实测全局从 7 个降为 1 个 |
| MINOR (F-05) | 作者的 before/after 探针 DOM 桩过宽：`getElementById` 对不存在的 id **伪造**元素，且 `DOMContentLoaded` 在注册时立即触发 | 改为缺失 id 返回 `null` 并记录到 `accessedMissing`（断言必须为空），事件在全部脚本执行完后才派发。收紧后结论不变（before=未配置 / after=就绪），`accessedMissing=[]` |

第 4 轮整改后复验：`npm run test:mobile` **27/27**（该轮实测值；第 5 轮增至 29）、仓库根
`node --test mobile/tests/*.test.js` **27/27**、F-01 变异 6/6 检出、完整文档步骤在临时目录重走一遍并产出
`14B41ADA…EB48E9`（包内资源逐字节一致、无 `server` 块、`allowBackup=false`）。
**仍无真机/模拟器，未调用 `.\gradlew.bat`（wrapper 8.14.3 未解包）**。

> **第 4 轮记录更正（第 5 轮审查指出，已修）**：本节上一版写着"`docs/handoff/android-mobile.md`
> 是未跟踪的用户材料，本卡未改"。**这是错的**：该文件在 `3cc4fc7`（T22 首个提交）就已入库，
> `git ls-files docs/handoff/android-mobile.md` 可证。工作区里另有一份同名未跟踪副本属于用户，
> 与已跟踪版本不是同一份。因此下面三份**已跟踪**文档当时仍停在修复前，第 5 轮一并改掉，见 §8.5。

### 8.5 第 5 轮（最终复核）发现项与整改

第 5 轮仍是全新上下文。**0 BLOCKER**。它复核了第 4 轮的五项整改，确认 F-06 六处、F-01 假守卫、
F-02 陈旧产物、F-04 全局泄漏、F-05 DOM 桩**全部真修好**（11 组 HTML 变异 10/10 应红全红），
并独立复测了 `62d673e` 的行尾修复（`3129122` 在 `autocrlf=true` 检出下 24/25 红，`62d673e` 下 27/27 绿）。
剩余 1 MAJOR + 5 MINOR：

| 级别 | 问题 | 整改 |
| --- | --- | --- |
| **MAJOR (F-01)** | 第 4 轮误判 `docs/handoff/android-mobile.md` 为"未跟踪用户材料"而未改，导致**三份已跟踪文档**仍停在修复前，而本 ADR §5.2 却声称文档"已更新" | 改 `docs/handoff/android-mobile.md`（§1 清单、§6 命令块、§6.1 前提示）、`docs/phase4/tasks.md` T22 行、`docs/documentation-status.md` 2026-10-18 段：条数统一 29、补 `config:generate` 步骤与"不读 `.env`"、旧 SHA 标注过期、新产物写清复现口径 |
| MINOR (F-02b) | `62d673e` 把测试改成 27 条后，本 ADR 多处仍写 25 | 全部改为实测值（当时收敛为 **29**，现为 **30**，见 §8.6）；§8.4 保留"当时是 27"的历史口径并注明后续变化 |
| MINOR (F-03) | `--live-reload` 成功后再跑**无效配置**，`capacitor.config.json` 仍带 `server.url`，`cap copy` 会把它写进 APK | 无效分支检测到 `server` 块时**重置为发布形态**（无 `server` 块）；已是发布形态则不动（不做无谓的行尾改写）。新增用例覆盖三段：live-reload → 无效 → 断言无 `server`；再看一次仍不变 |
| MINOR (F-04) | "config 写失败时回滚 env"的逻辑**零覆盖**，删掉它测试仍全绿 | 新增用例：用同名目录占位让 config rename 失败，断言 `mobile-env.js` 被回滚删除且无 `.tmp` 残留。变异复核：删掉回滚 → 该用例转红 |
| MINOR (F-05) | 守卫只比相对次序，把 env 标签挪进 `<head>` 仍绿 | 断言三个 `<script>` 都在 `<body>` 之后、`</body>` 之前；变异（挪进 head）实测 `insideBody=false` 能被检出 |
| MINOR (F-06) | §5.1 复现命令写成 `gradlew assembleDebug`，Windows 下 CommandNotFoundException | 改为 `.\gradlew.bat assembleDebug` |
| MINOR (F-07/09) | §5.1 资源清单只列 5 项，`assets/public/` 实际 8 条；`npx cap add android` 在既有工程上 exit 1 未注明 | 清单补齐并说明另 3 条来源；`cap add android` 标注"仅首次创建工程时需要" |

第 5 轮整改后复验（**历史记录，当时为 29 条**）：`npm run test:mobile` **29/29**（LF 与模拟 CRLF 工作树各一次）、
仓库根 `node --test mobile/tests/*.test.js` **29/29**、F-03/F-04/F-05 三条新用例各有变异证明。
`docs/handoff/android-mobile.md` 里指向本 ADR 的相对链接 `../phase4/adr-p4-03-mobile-tech-stack.md`
已核实可解析（ADR 随本分支入库）。**仍无真机/模拟器；本轮未重打包 APK。**

> 2026-10-27 第 5 轮复审（N-01）更正：本行上一版被改成 `30/30`，与 §8.4 `:619` 的
> "第 5 轮增至 29" 直接冲突。`07815e6` 那一轮**确实是 29 条**（`mobile-config` 13 +
> `mobile-env` 16），30 是后来 `3d902de` 加了一条不可读 config 用例才到的。
> 历史段落保留当时的真实值，不随当前值改写。

### 8.6 第 6 轮（对 `3d902de` 的复审）发现项与整改

第 3 轮（`review-android-r3.md`）确认 R2 的 F-01..F-07 **全部真修好**，另提出 3 条 MINOR。
`3d902de` 修了这 3 条，第 4 轮复审（`review-android-r4.md`）判 **FAIL**，原因是：

| 编号 | 级别 | 内容 | 整改 |
| --- | --- | --- | --- |
| **F-14** | **MAJOR** | `3d902de` 把套件从 29 条加到 **30** 条（env 16→17），却**一处文档都没更新**：11 处仍写 29（`adr:170/174/175/438/439/642-643`、`handoff:22/181`、`tasks:40`、`documentation-status:19`、`traceability-matrix:128`），而 §5.2 还称 29 是"实测"值。这与它正在修的 F-01 是同一类错误，且正落在 #149 第三条验收（文档同步）上 | 11 处**当前值**改为 **30**（13 + 17）；`§8.4/:619` 与 `§8.5/:646` 属**历史记录**，保留当时的 **27/29** 并各自注明后续变化，不随当前值改写。本条失守记录在 §8.6 本身 |
| F-13 | MINOR | 同一提交新增的 §5.1.3 把 F-10 写成"**未修**…留作后续"，而该提交本身已修，描述的修法与实现逐字一致 | §5.1.3 改写为"已修"，列出三态表，并留下这条更正说明 |
| F-12 | MINOR | F-09 只堵了注释与 `<template>`，另有多种"脚本不会执行"的形态仍 30/30 绿 | **未修**，记录为已知边界：本守卫针对的是"标签存在但被包裹"这一类，不是通用 HTML 语义分析。见下方说明 |

**F-12 的边界说明（不掩饰）**：`stripInert` 是正则级的注释/模板剥离，不是 HTML 解析器。
它挡住的是**最可能被误写出来的两种**（整段注释掉、整段放进 `<template>`）。
以下形态**仍会漏检**，每一种都在第 5、6 轮复审中被实测为 `30/30` 全绿：

| 形态 | 为什么不执行 |
| --- | --- |
| `<noscript>` 内（含未闭合写法） | 只在禁用脚本时渲染，正常环境不执行 |
| 未闭合的 `<!--` | 其后整段成为注释 |
| 未闭合的 `<template>` | 其后整段进入文档片段 |
| `<svg>` 内 | 命名空间不同 |
| `<textarea>` / `<title>` / `<xmp>` 内 | RAWTEXT / RCDATA 元素，内容是文本不是标记 |
| `<plaintext>` 内 | 其后整份文档都变成纯文本（RAWTEXT） |
| `<script type="text/template">` 数据块内 | 非可执行脚本类型，内容是数据 |

**这是一份"已实测到的"清单，不是穷举。** 第 4 轮报告列了 4 种，第 5 轮找到 8 种，
第 6 轮的探针又找到上面最后两种（`<plaintext>`、非可执行 `<script>` 数据块）。
只要守卫仍是正则级，这个数字就可能继续增长——**每加一种就说明它是用一个黑名单在追一类问题**，
而不是在判定"这段标记会不会执行"。

它们都要求刻意写出畸形或语义微妙的标记，且其中多类在现代浏览器里本身就会让整页渲染异常——
所以定为 MINOR 而非 MAJOR。真正的修法是引入 HTML 解析器做"是否会执行"的判定，
代价远超本卡范围，**留作后续**。
