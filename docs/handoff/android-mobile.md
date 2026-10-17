# 模块 8：Android / 移动端（从零立项）

> 交接对象：负责在 Windows/Web 之外增加 Android 端的 Agent。
> 核对日期 2026-10-18，基线 `cfe53be`。规范见 [conventions.md](conventions.md)。

---

## 1. 现状：工程骨架已落地，产品功能仍为 0

> 更新 2026-10-18（分支 `feature/p4-t22-mobile-adr-and-skeleton`）：T22（#149）已定稿
> [ADR-P4-03](../phase4/adr-p4-03-mobile-tech-stack.md) 并落地 `mobile/` 骨架。
> **能力边界没变：登录、WS 收发、E2EE、通知、文件一件都没实现。** T23–T30 全部 0 进度。

```
仓库里现在有：
  mobile/                     Capacitor 8.5.3 工程（package.json / capacitor.config.json / www/）
  mobile/android/             Capacitor CLI 真实生成的 Android 工程（不是占位目录）
  mobile/tests/               node:test 配置契约测试（10 条，纯 Node，无需模拟器）
  docs/phase4/adr-p4-03-*.md  技术决策记录
仓库里仍然没有：
  *.apk / *.aab               （安装包未产出）
  真机或模拟器的运行记录        （0 次）
  任何 Android 仪器化/UI 测试   （0 测试）
```

WP04 的 9 项任务（#149–#157）中 **仅 #149 部分完成**；#150–#157 为 0/9。

**构建前置（本机不具备，见 ADR §6）**：JDK 17+（本机只有 1.8）、Android SDK Platform 36 +
Build-Tools 36（本机 SDK 目录是 Program Files 只读，且只有 33/34）、真机或模拟器。

可复用的是：

| 已有资产 | 复用方式 |
| --- | --- |
| 服务端 HTTP/WS 协议 | 完全不改，Android 作为第三个客户端接入 `ws/chat/` |
| E2EE 协议（ECDH-P256 + HKDF + AES-256-GCM） | 需要原生或 WebView 内的等价实现，**必须能解 Windows/Web 发的密文** |
| 加密文件分片规范 | `docs/iChat Pro 文件传输规范.md` + `/api/files/` 路由 |
| `static/js/chat-connection.js` | 逻辑可移植（退避/心跳/ACK/状态机），但不是现成 Android 代码 |
| `docs/phase4/protocol.md` | 协议契约，Android 必须遵守同一份 |

---

## 2. 必须先做的技术决策（#149）

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

```powershell
# 前置：JDK 17+、Android SDK、Gradle
java -version
adb devices

# Capacitor 路线（示例，尚未在仓库落地）
npm create @capacitor/app
npx cap add android
npx cap sync
npx cap open android
```

**不要**把 `capacitor.config.json` 的 `server.url` 热重载配置当成正式方案——它会让 App 依赖开发机。

---

## 7. 交付要求（来自 `agent-handoff.md` WP04）

- 固定工具链版本（JDK / Gradle / Android SDK / Capacitor 或 AGP）。
- 生成可安装的 APK 或 AAB。
- 提交**两个 API Level 或两种设备配置**的实际执行记录（不是"编译成功"）。
- 未测试项明确列出，不能以"编写了脚本"代替实测。
- 连接算法复用 WP02 的共享实现，不各维护一套状态机。

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
