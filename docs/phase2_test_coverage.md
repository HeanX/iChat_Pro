# Phase 2 测试覆盖记录 (P2 T40)

> 版本：v1.0  
> 日期：2026-06-15  
> 适用范围：Phase 2 后端自动化测试覆盖记录  
> `python manage.py test` → **306 tests, all OK** ✅

## 1. 测试文件概览

| 测试文件 | 覆盖范围 | 测试数（约） |
|----------|----------|--------------|
| `accounts/tests.py` | 登录/注册/登出/资料/联系人/好友申请/设置API/密钥信任 | ~90 |
| `chat/tests.py` | 会话模型/消息模型/群API/WebSocket消费者/消息API | ~80 |
| `chat/test_t11.py` | P2 T11 私聊实时消息（密文保存/转发/状态/幂等） | ~9 |
| `chat/test_t12.py` | P2 T12 群聊实时消息（密文/成员版本/左群过滤） | ~6 |
| `chat/test_p2_backend.py` | P2 T19-T40 后端 API（会话管理/消息操作/状态/搜索/群管理） | ~60 |
| `chat/test_p2_issues.py` | P2 T27/T31-T34/T37-T38/T40 综合后端测试 | ~40 |
| `chat/test_t16_views.py` | P2 T16 安全指纹/密钥信任视图 | ~5 |
| `chat/test_t20_integration.py` | P2 T20 消息操作集成测试 | ~16 |

## 2. 按 P2 Issue 测试覆盖映射

### T19: 会话管理 API
- [x] 置顶/取消置顶 — `test_p2_backend.py`
- [x] 静音/免打扰 — `test_p2_backend.py`
- [x] 归档/取消归档 — `test_p2_backend.py`
- [x] 隐藏/删除会话 — `test_p2_backend.py`
- [x] 清空聊天 — `test_p2_backend.py`
- [x] 标记已读/未读 — `test_p2_backend.py`
- [x] 会话列表按状态排序 — `chat/tests.py`

### T20: 消息操作 API
- [x] 回复引用 — `test_t20_integration.py`
- [x] 文本转发 — `test_t20_integration.py`
- [x] 用户侧删除 — `test_t20_integration.py`
- [x] 发送者撤回 + 时间限制 — `test_t20_integration.py`
- [x] 重发幂等 — `test_t11.py`
- [x] 复制不经过后端 — 前端本地实现

### T21: 消息状态模型
- [x] sending/sent/delivered/read/failed 语义 — `test_t11.py` + `chat/tests.py`
- [x] 私聊送达/已读 — `test_t11.py`
- [x] 群聊已读/未读 — `test_t12.py`
- [x] 撤回状态 — `test_t20_integration.py`
- [x] 用户侧删除可见性 — `test_t20_integration.py`

### T22: 在线状态与输入中状态
- [x] 在线/离线记录 — `test_p2_backend.py`
- [x] WebSocket 连接建立更新在线状态 — `test_p2_backend.py`
- [x] WebSocket 断开更新离线状态 — `test_p2_backend.py`
- [x] 输入中事件广播 — `test_p2_backend.py`
- [x] 已读回执 — `test_p2_backend.py`
- [x] 未读数刷新 — `chat/tests.py`

### T23: 通知设置模型和 API
- [x] 存储/读取通知偏好 — `accounts/tests.py`
- [x] 默认值 — `accounts/tests.py`

### T24: 数据和存储设置模型
- [x] 自动下载设置 — `accounts/tests.py`
- [x] 缓存统计 API — `accounts/tests.py`
- [x] 缓存清理 API — `accounts/tests.py`

### T25: 隐私和安全设置模型
- [x] 手机号/在线/头像可见性 — `accounts/tests.py`
- [x] 消息权限设置 — `accounts/tests.py`
- [x] 敏感内容过滤 — `accounts/tests.py`

### T26: 拉黑用户与消息权限
- [x] 拉黑/取消拉黑 — `accounts/tests.py`
- [x] 被拉黑后限制发消息 — `accounts/tests.py`
- [x] 搜索结果处理 — `accounts/tests.py`

### T27: 自动删除消息
- [x] 全局默认自动删除 — `test_p2_issues.py`
- [x] 按会话设置自动删除 — `test_p2_issues.py`
- [x] 清理机制 — `test_p2_issues.py`

### T28: 密码锁/两步验证占位模型
- [x] 状态展示/读取接口 — `accounts/tests.py`

### T29: 个人资料扩展字段
- [x] 头像/简介/生日/用户名 — `accounts/tests.py`
- [x] 用户名唯一性/格式校验 — `accounts/tests.py`

### T30: QR Code 名片数据接口
- [x] QR payload 生成 — `accounts/tests.py`
- [x] 敏感信息不泄露 — `accounts/tests.py`

### T31: 联系人搜索与新建私聊接口
- [x] 按用户名/昵称/用户ID搜索 — `test_p2_issues.py`
- [x] 打开/创建私聊 — `test_p2_issues.py`
- [x] 关系状态返回 — `test_p2_issues.py`
- [x] 权限校验 — `test_p2_issues.py`

### T32: 群组创建和成员选择
- [x] 创建群组 — `test_p2_issues.py`
- [x] 成员选择/去重 — `test_p2_issues.py`
- [x] 创建者自动成为群主 — `test_p2_issues.py`
- [x] 事务/失败回滚 — `test_p2_issues.py`

### T33: 搜索 API
- [x] 搜索会话名/联系人/群名 — `test_p2_issues.py`
- [x] 结果分组 — `test_p2_issues.py`
- [x] 不接触聊天明文 — `test_p2_issues.py`

### T34: 搜索范围筛选
- [x] All/Private/Group/Channel 范围 — `test_p2_issues.py`
- [x] 权限过滤 — `test_p2_issues.py`

### T35: 多账号本地上下文设计
- [x] Token/账号绑定 — `accounts/tests.py`
- [x] 数据隔离 — `accounts/tests.py`
- [x] 禁止冒充其它账号 — `accounts/tests.py`

### T36: 账号退出和会话管理
- [x] 退出当前账号 — `accounts/tests.py`
- [x] 注销其它设备 — `accounts/tests.py`
- [x] 活跃会话列表 — `accounts/tests.py`

### T37: 群聊管理补全
- [x] 设置/取消管理员 — `test_p2_issues.py`
- [x] 群主转让 — `test_p2_issues.py`
- [x] 成员搜索 — `test_p2_issues.py`
- [x] 群公告 — `test_p2_issues.py`
- [x] 退群/解散 — `test_p2_issues.py`
- [x] 系统消息生成 — `test_p2_issues.py`
- [x] membership_version 刷新 — `test_p2_issues.py`

### T38: 安全指纹和密钥信任
- [x] 公钥 fingerprint — `accounts/tests.py`
- [x] key version — `accounts/tests.py`
- [x] 密钥信任状态保存 — `accounts/tests.py`
- [x] 密钥变更检测 — `accounts/tests.py`

### T39: 资料同步事件
- [x] profile_version — `accounts/tests.py`
- [x] 资料变化后刷新 — `accounts/tests.py`

### T40: Phase 2 测试覆盖 (本文档本身)
- [x] 会话管理 API 覆盖 — ✅
- [x] 消息操作和消息状态 — ✅
- [x] 通知/隐私/存储设置 — ✅
- [x] 联系人搜索/资料入口/新建私聊 — ✅
- [x] 多账号隔离 — ✅
- [x] 群管理权限 — ✅
- [x] 安全指纹/密钥信任 — ✅

## 3. 测试类型分布

| 类型 | 覆盖 |
|------|------|
| 模型/数据库层 | ✅ Conversation, EncryptedMessage, GroupRecipient, UserSettings, KeyTrust |
| API 视图层 | ✅ 所有 Phase 2 API endpoint 有正向和权限失败测试 |
| WebSocket 层 | ✅ ChatConsumer 认证/连接/断线/Ping/事件路由 |
| E2EE 保护 | ✅ 密文保存/转发测试，确认服务端不接触明文 |
| 权限/安全 | ✅ 未认证拒绝、非成员拒绝、非管理员拒绝、拉黑限制 |
| 幂等性 | ✅ 消息重发去重、会话创建去重 |
| 数据隔离 | ✅ 用户侧删除/清空只影响当前用户，多账号隔离 |

## 4. 未通过测试

**无。** `python manage.py test` 全量 306 tests 通过（2026-06-15 验证）。

## 5. 已知测试缺口（Phase 3 收口或后续补强）

- [ ] WebSocket 断线重连压力测试
- [ ] 大量并发消息加密/解密性能测试
- [ ] 前端 E2EE 模块集成测试 (Jest/Cypress)
- [ ] Electron 桌面端端到端测试
- [ ] 浏览器兼容性测试矩阵
- [ ] 多设备同时在线冲突测试
