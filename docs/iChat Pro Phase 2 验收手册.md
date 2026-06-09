# iChat Pro Phase 2 验收手册

> 版本：v1.1
> 日期：2026-06-08
> 适用范围：P2 T01-T08 前端设置中心 + P2 T18 手动验收
> 说明：本文用于当前 `main` 已落地的 Phase 2 前端设置中心验收；后端持久化、真实权限策略和完整多端同步以后续后端 Issue 为准。

## 1. 验收准备

### 1.1 环境

- Python 3.12+ with Django dependencies
- Node.js 18+ (for Electron)
- Two browsers (or browser + incognito) for multi-user testing

### 1.2 启动

```powershell
.\.venv\Scripts\Activate.ps1
python manage.py migrate
python demo_setup.py
python manage.py runserver 127.0.0.1:8000
```

Demo accounts: `alice` / `demo1234` | `bob` / `demo1234`

### 1.3 基础检查

```powershell
python manage.py check
```

预期结果：`System check identified no issues`。

---

## 2. P2 T01 — 左侧栏多视图导航

| # | 测试项 | 预期结果 | 通过 |
|---|--------|----------|------|
| 1 | 点击 Settings，左侧栏切换到设置首页 | 右侧聊天区保持可见 | ☐ |
| 2 | 设置首页点击返回，回到聊天列表 | 聊天选中状态保持 | ☐ |
| 3 | 点击 Contacts，左侧栏切换到联系人 | 可搜索/添加/管理联系人 | ☐ |
| 4 | 侧边栏宽度 360px~440px 可拖拽调整 | 拖拽手柄响应正常 | ☐ |
| 5 | 1280px/1440px/1920px 宽度下无溢出 | 布局完整，无文字重叠 | ☐ |

## 3. P2 T02 — 设置中心首页

| # | 测试项 | 预期结果 | 通过 |
|---|--------|----------|------|
| 1 | 设置首页显示用户头像、昵称、在线状态 | 正确显示 | ☐ |
| 2 | 显示手机号、用户名、邮箱 | 正确显示 | ☐ |
| 3 | 列出通知/数据/隐私/文件夹/贴纸/设备/语言/快捷键入口 | 每项有图标+标题+状态值 | ☐ |
| 4 | 点击尚未接入真实后端的功能 | 显示 toast 占位、禁用态或前端本地状态 | ☐ |
| 5 | 点击 Cryptographic Keys | 跳转到密钥管理页 | ☐ |
| 6 | 点击 Sign Out | 弹出退出确认 | ☐ |
| 7 | 切换语言后关闭设置再打开 | 语言切换生效 | ☐ |

## 4. P2 T03 — 编辑个人资料与二维码

| # | 测试项 | 预期结果 | 通过 |
|---|--------|----------|------|
| 1 | 设置首页点击 Edit Profile | 打开编辑资料页 | ☐ |
| 2 | 修改昵称、First/Last name、Bio | 保存成功，刷新可见 | ☐ |
| 3 | 用户名输入非法字符 | 显示验证错误 | ☐ |
| 4 | 用户名少于 5 个字符 | 显示验证错误 | ☐ |
| 5 | 点击 QR Code | 弹出二维码弹窗 | ☐ |
| 6 | 点击 Copy QR Code | 剪贴板写入成功 | ☐ |
| 7 | 关闭 QR 弹窗 | 回到资料页 | ☐ |
| 8 | Birthday 和 Channel 点击 | toast 提示未实现 | ☐ |

## 5. P2 T04 — 通知设置

| # | 测试项 | 预期结果 | 通过 |
|---|--------|----------|------|
| 1 | 设置首页 → Notifications | 打开通知设置页 | ☐ |
| 2 | Desktop Notifications 权限请求 | 按钮可点击 | ☐ |
| 3 | Offline Notifications 开关 | 可切换，状态持久化 | ☐ |
| 4 | Volume 滑块拖动 | 百分比更新 | ☐ |
| 5 | Private Chats / Groups 各自开关 | 独立切换 | ☐ |
| 6 | Channel 通知禁用 | 开关 disabled + opacity | ☐ |
| 7 | 切换开关后刷新页面 | 设置保持 | ☐ |

## 6. P2 T05 — 数据和存储

| # | 测试项 | 预期结果 | 通过 |
|---|--------|----------|------|
| 1 | Settings → Data and Storage | 显示用量仪表 | ☐ |
| 2 | Auto-download 开关（Mobile/WiFi/Roaming） | 可切换 | ☐ |
| 3 | Clear Local Cache | toast 提示 | ☐ |
| 4 | Clear All Cache Settings | 弹窗确认后清除缓存设置 | ☐ |
| 5 | 清理缓存设置后检查 E2EE | 本地加密密钥和必要应用数据不被删除 | ☐ |

## 7. P2 T06 — 隐私和安全

| # | 测试项 | 预期结果 | 通过 |
|---|--------|----------|------|
| 1 | Settings → Privacy and Security | 打开页面 | ☐ |
| 2 | Last Seen / Photo / Phone 可见性 | 显示当前设置 | ☐ |
| 3 | Two-Step Verification / Active Sessions | 占位提示 | ☐ |
| 4 | Delete Synced Contacts | toast 提示 | ☐ |
| 5 | Delete Account | 二次确认弹窗 | ☐ |

## 8. P2 T07 — 聊天文件夹与贴纸

| # | 测试项 | 预期结果 | 通过 |
|---|--------|----------|------|
| 1 | Settings → Chat Folders | 打开页面 | ☐ |
| 2 | Create New Folder 点击 | toast 占位 | ☐ |
| 3 | Team Chats demo 文件夹 | 显示 "3 chats" | ☐ |
| 4 | Sticker Sets / Suggest Emoji / Custom Emoji | 各项可进入或占位 | ☐ |

## 9. P2 T08 — 活跃会话与快捷键

| # | 测试项 | 预期结果 | 通过 |
|---|--------|----------|------|
| 1 | Settings → Sessions & Shortcuts | 打开页面 | ☐ |
| 2 | Active Sessions 列出现有设备 | 显示 current session | ☐ |
| 3 | Terminate 按钮 | toast 提示 | ☐ |
| 4 | Terminate All Other Sessions | toast 提示 | ☐ |
| 5 | Language 切换 | 语言即时变更 | ☐ |
| 6 | Keyboard Shortcuts 列表 | 显示 Ctrl+K, Enter 等 | ☐ |

## 10. 综合验收

| # | 测试项 | 通过 |
|---|--------|------|
| 1 | Django 系统检查无错误 | ☐ |
| 2 | 当前仓库可用的自动化测试通过 | ☐ |
| 3 | 私聊 E2EE 加密收发正常 | ☐ |
| 4 | 群聊逐成员加密正常 | ☐ |
| 5 | Electron 桌面端启动正常 | ☐ |
| 6 | 演示账号 alice/bob/carol 可用 | ☐ |
