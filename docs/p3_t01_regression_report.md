# P3 T01 — 展示前稳定化回归报告

> 日期：2026-06-16
> 作者：ketter1024
> 关联 Issue：#114 (P3 T01)
> 分支：feature/p3-t01-t07-t08-ketter1024

## 回归检查结果

| 检查项 | 命令 | 结果 |
|--------|------|------|
| Django 系统检查 | `python manage.py check` | ✅ 0 issues |
| 迁移一致性 | `python manage.py makemigrations --check --dry-run` | ✅ No changes detected |
| 自动化测试 | `python manage.py test` | ✅ 313 tests OK |
| E2EE JS 测试 | `npm run test:e2ee` | ✅ all tests passed |
| 演示账号创建 | `python demo_setup.py` | ✅ alice/bob/carol 创建成功 |
| Django 部署检查 | `python manage.py check --deploy` | ⚠️ 6 warnings (expected dev-mode) |

## 部署检查说明

`--deploy` 的 6 个 WARNING 在开发模式下（`DEBUG=True`）符合预期。生产模式下 `settings.py` 已通过 `if not DEBUG:` 块自动启用：
- `SECURE_SSL_REDIRECT`
- `SECURE_HSTS_SECONDS`
- `CSRF_COOKIE_SECURE`
- `SESSION_COOKIE_SECURE`

参见 `docs/iChat Pro 部署安全说明.md`。

## 发现与修复

### 已确认：数据库迁移需手动执行 ✅

**现象：** `git pull` 最新代码后，本地 `db.sqlite3` 可能缺少新迁移表（如 `accounts_userprofile.user_type`），导致 `demo_setup.py` 或应用启动时报 `no such column` 错误。

**原因：** SQLite 数据库文件未纳入版本控制（`.gitignore`），代码中的迁移文件更新后本地数据库不会自动同步。

**解决方案：** 每次拉取新代码后执行 `python manage.py migrate`。已在 `README.md` 和演示文档中明确此步骤。

**状态：** 非代码缺陷，属于开发环境标准操作流程。迁移后所有功能正常。

### 已验证：占位状态清晰 ✅

| 功能 | 占位方式 | 状态 |
|------|----------|------|
| Channels 搜索 | `results['channels'] = []` — 返回空数组 | ✅ 明确空状态 |
| 密码锁 (Passcode Lock) | 模型字段 `passcode_enabled = False` 默认禁用 | ✅ 前端可判断 |
| 两步验证 (2FA) | 模型字段 `two_step_verification_enabled = False` | ✅ 前端可判断 |
| 通行密钥 (Passkey) | 模型字段 `passkey_enabled = False` + 注释 `placeholder` | ✅ |
| AI Assistant (LLM) | Phase 3 规划中，模型/视图尚未创建 | ✅ |

未实现功能的后端接口均返回合理默认值或空结果，不会误导前端为可用能力。

### 已验证：权限校验完整 ✅

- 私聊发送：校验联系人关系 + 拉黑状态
- 群管理：校验 owner/admin 角色
- 消息撤回：仅发送者 + 时间窗口内
- 隐私字段：根据 `UserPrivacySettings` 可见性规则过滤
- Token 隔离：所有 API 从 `request.user` 获取当前用户，不接受前端传入 `user_id`

全部权限路径在自动化测试中覆盖（`accounts/tests.py`，`chat/tests/test_core.py`，`chat/tests/test_phase2_backend.py`，`chat/tests/test_phase2_issues.py`）。

## 自动化测试覆盖统计

```
accounts/tests.py          — 账号、资料、联系人、隐私、密钥测试
chat/tests/test_core.py              — 基础聊天、WebSocket、消息测试
chat/tests/test_phase2_backend.py    — P2 会话管理、消息操作、状态、搜索测试
chat/tests/test_phase2_issues.py     — P2 群管理、自动删除、安全指纹测试
chat/tests/test_integration.py — T20 端到端集成测试
chat/tests/test_private_realtime.py           — 多账号切换测试
```

## 结论

✅ **Phase 2 能力在当前 main 分支上稳定可用。** 全部自动化检查通过，演示账号创建正常，核心权限和安全链路已有测试覆盖。可以进入 Phase 3 演示准备。

⚠️ **注意事项：**
1. 演示前务必运行 `python manage.py migrate` + `python demo_setup.py`
2. 双用户演示需要两个独立浏览器会话
3. WebSocket 依赖 InMemory Channel Layer，重启服务器后连接断开
4. Qwen API Key 未配置时 AI Assistant 自动进入 Mock 模式
