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
3. **工程骨架已落地并可构建**：`mobile/` 可 `npm install` + `npx cap sync` + Gradle 构建，
   `mobile/android/` 是 Capacitor CLI **真实生成**（非手写）的原生工程；
   **`app-debug.apk` 已真实产出**（最终产物 4.22 MB / `FCE3182E…66C7AE6`，SHA256 与构建命令见 §5）。
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
> 代码，APK 内也没有注入 `window.ICHAT_MOBILE_ENV` 的代码。也就是说 **"同源加载 Django 页面"是
> T24 要接通的目标，不是本卡已实现的事实**。本卡只落地了"服务地址如何解析、如何校验"这一层。
> 上一版 ADR 在这里用了现在时，属表述错误，已更正。

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
├── scripts/
│   └── write-capacitor-config.js   # 从 www/js/mobile-config.js 契约生成配置；未配置时 exit 1
├── www/                            # WebView 打包进 APK 的本地资源
│   ├── index.html                  # 骨架启动页（不是聊天 UI）
│   ├── css/mobile-shell.css        # 安全区适配；不依赖 Django 静态资源
│   └── js/
│       ├── mobile-config.js        # 纯函数配置契约（Node 可测）
│       └── shell.js                # 启动页渲染，仅读 window.ICHAT_MOBILE_ENV
├── tests/
│   └── mobile-config.test.js       # node:test，10 条
└── android/                        # Capacitor CLI 真实生成的原生工程
```

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
npx cap add android
# -> √ Adding native android project in android
#    [info] Found 3 Capacitor plugins for android:
#           @capacitor/app@8.1.2, @capacitor/network@8.0.1, @capacitor/preferences@8.0.1
#    [success] android platform added!

# 同步 web 资源
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
```

产物与校验（审查整改后重新构建的最终产物）：

```
mobile/android/app/build/outputs/apk/debug/app-debug.apk
  大小    4.22 MB (4,424,830 bytes)
  SHA256  FCE3182ED2200D1B747C7DB060176B55BB0BF40AFDC34B04EBFC8D55866C7AE6
```

> 构建历史：① 初版 3.94 MB / `F10D3570…CDFCD2`；② manifest 禁用备份后 4.22 MB / `0D05C92B…4B4976`；
> ③ 审查整改（收窄 FileProvider、修正仪器化测试断言）后为上面的 `FCE3182E…66C7AE6`。
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

assets/public/index.html           1962
assets/public/js/mobile-config.js  5152
assets/public/js/shell.js          1646
assets/public/css/mobile-shell.css 2346
```

→ 说明 `webDir: www` 生效，`www/` 里的骨架页确实被打进 APK。

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


### 5.2 四条验收逐项对齐

| 验收项 | 本卡状态 | 证据 |
| --- | --- | --- |
| 能复用现有页面并生成 Android 工程 | **工程已生成且可构建出 APK**；"复用现有页面"的**运行时**验证需 T24 出包后在设备上加载 Django 页面 | §5.1 的 `npx cap add android` 输出 + APK 产物 + `launchable-activity` |
| 相关代码或配置经过非作者成员评审 | 见交付记录（独立审查子 Agent 结论，§8） | 同上 |
| 适用的自动化或人工测试已执行并保存证据 | `node --test mobile/tests/mobile-config.test.js` **10/10 通过**；Gradle 构建成功；**设备安装/启动/截图未执行** | §5.1 |
| 需求、部署或测试文档已同步更新 | 本 ADR + `docs/phase4/tasks.md` T22 行 + `docs/handoff/android-mobile.md` §1 + `docs/documentation-status.md` | 同上 |

### 5.3 明确未完成的项（不得当作通过）

| 项 | 原因 |
| --- | --- |
| `adb install` + 启动 + 截图 | `adb devices` 无设备；SDK 无 `emulator` 包；`HypervisorPresent=False`（虚拟化未开），模拟器不可用 |
| 两个 API Level / 两种设备配置的运行记录 | 同上，无任何可运行目标 |
| WebView 内登录 → WS → 收发一条消息 | 属 T24 及之后范围，本卡不含 |
| 用固定 wrapper Gradle 8.14.3 构建 | 该发行版在 GitHub Releases，本机到该主机连接被重置；改用本机缓存的 Gradle 9.5.1。**CI 必须用 `./gradlew` 复跑** |
| WebView 实际 `Origin` 头行为 | 当前构建不加载远端页面，测试前提不存在；需 T24 接通同源加载 + 设备抓包，见 §3.2 |
| **debug APK 含 `debuggable=true`** | debug 构建标志位。`capacitor.config.json` 的 `webContentsDebuggingEnabled:false` **压不住它**——`CapConfig.java` 的 fallback 是 `isDebug`，读的正是 `FLAG_DEBUGGABLE`。**该 APK 可被 Chromium DevTools 远程调试，不得分发给任何人试用。** release 构建 `debuggable=false`，无此问题 |

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

# 3) 同步 + 构建（有网络访问 services.gradle.org 时用 wrapper，这是正式路径）
cd mobile
npm install
npx cap sync android
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
| 1 | 独立审查子 Agent（非作者上下文，只给 Issue 原文 + diff + 命令） | **PASS WITH FINDINGS** | 2 BLOCKER / 5 MAJOR / 6 MINOR | 见下表 |

### 8.1 第 1 轮发现项与整改

| 级别 | 问题 | 整改 |
| --- | --- | --- |
| BLOCKER | `mobile/android/gradlew` 以 mode `100644` 入库（Windows `core.filemode=false`），Linux/macOS CI 的 `./gradlew` 会 permission denied | `git update-index --chmod=+x`，现为 `100755` |
| BLOCKER | ADR 写"JDK 17+ 必需"，但 Capacitor 8 生成的 `app/capacitor.build.gradle` 硬编码 `JavaVersion.VERSION_21`，JDK 17 会在 `compileJava` 失败 | §0/§4.2/§6/§7 与本模块交接文档统一改为 **JDK 21+**，并注明依据 |
| MAJOR | `docs/documentation-status.md` 仍写"未产出 APK"，与 ADR/handoff 矛盾 | 已改为"APK 已构建 + 真机未执行" |
| MAJOR | `tasks.md` T22 行写"APK 产物…未执行 / 缺 JDK 17+"，与事实冲突；链接错指 §6 | 重写该行，链接改 §5.3 |
| MAJOR | §0 结论 2 与 §3.1 把"同源加载 Django 页面"写成现在时，实际 `server.url=null` 只加载本地 assets | §0/§3.1 改为"目标形态"，明确 T22 未接通、属 T24 |
| MAJOR | debug APK 含 `debuggable=true`，`webContentsDebuggingEnabled:false` 压不住（`CapConfig` fallback 是 `isDebug`） | §5.3 增列：该 APK 可被 DevTools 远程调试，**不得分发试用** |
| MAJOR | `write-capacitor-config.js` 失败时仍写文件，可能覆盖受版本控制的 `capacitor.config.json` | 失败路径改为**先退出、不写文件**（实测 exit 1 且文件未变） |
| MINOR | 仪器化测试断言 `com.getcapacitor.app`，与 `applicationId=com.ichat.pro` 不符，首跑必红 | 改为 `com.ichat.pro` |
| MINOR | `file_paths.xml` 的 `external-path path="."` 把整个外部存储暴露给 FileProvider | 收窄为 `ichat/` 子目录的 cache/files/external-files 三条 |
| MINOR | live-reload 配 `http://localhost` 却写 `cleartext:false`，WebView 会拒绝加载 | `cleartext` 改为镜像 scheme；新增第 11 条测试守护 |
| MINOR | `@capacitor/cli` 仅 devDependency，`npm ci --omit=dev` 下 `cap` 不可用 | 脚本改用 `npx --no-install cap`，失败时明确报错而非静默装包 |
| MINOR | `data_extraction_rules.xml` 是否漏 `device_*` | 复查结论：**未漏**，前五个 domain 是官方最小写法，保留 |

整改后复验：`node --test` **11/11 通过**；重新构建 `BUILD SUCCESSFUL`，并从 APK 二进制清单反查确认
`allowBackup=false`、`dataExtractionRules` 生效、FileProvider 仅三条收窄路径。

> 按 `conventions.md` §1：审查者只拿到 Issue 原文 + diff + 命令，不给作者说明。
> 第 1 轮已用尽一轮，**整改后需开新的审查子 Agent 复核**（不复用本轮会话）。
