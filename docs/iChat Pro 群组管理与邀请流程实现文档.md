# iChat Pro 群组管理与邀请流程实现文档

## 1. 背景

本轮工作围绕群聊右侧信息面板补齐群组管理、群成员邀请、邀请审批、被邀请人确认、实时通知等能力。目标是让群聊管理流程从“能发起邀请”扩展为完整闭环：

1. 群成员均可发起邀请。
2. 普通成员发起邀请需要群管理审核。
3. 群主或管理员发起邀请可跳过管理审核，但仍需要被邀请人同意。
4. 被邀请人可以实时收到邀请通知，并可同意或拒绝。
5. 退群或被移除的历史成员可以被重新邀请。
6. 不可邀请对象不再静默消失，而是在邀请列表中显示原因。

## 2. 前端界面调整

### 2.1 右侧群信息面板入口

右侧群信息面板的成员卡片中新增两个并列入口：

| 入口 | 打开的界面 | 说明 |
| --- | --- | --- |
| 邀请 | 邀请成员面板 | 所有 active 群成员可进入 |
| 管理 | 群组管理面板 | 查看公告、待审核邀请、成员管理、群设置、退群/解散 |

相关模板：

- `templates/components/right_panel.html`
- `templates/pages/group_invite_sidebar.html`
- `templates/pages/group_manage_sidebar.html`

### 2.2 邀请成员与群组管理拆分

邀请和管理被拆为两个独立右侧子界面：

| 界面 | 主要内容 |
| --- | --- |
| 邀请成员 | 群基础信息、邀请规则说明、联系人/历史成员候选列表、可邀请勾选按钮、不可邀请原因 |
| 群组管理 | 群头像/名称、公告编辑、待管理员审核邀请、成员列表、管理员操作、退群/解散 |

### 2.3 邀请候选列表

邀请候选列表现在由后端接口驱动，前端不再依赖临时 DOM 推断可邀请联系人。

列表中会展示：

| 类型 | 是否可邀请 | 展示方式 |
| --- | --- | --- |
| 普通可邀请联系人 | 是 | 右侧蓝色勾选框 |
| 曾经退群/被移除成员 | 是 | 显示“曾经退群，可重新邀请”，右侧蓝色勾选框 |
| 当前用户自己 | 否 | 显示“当前用户自己”，右侧灰色禁用勾选框 |
| 已在群里的成员 | 否 | 显示“已经在群里的成员”，右侧灰色禁用勾选框 |
| 已有待处理邀请的人 | 否 | 显示“已经有待处理邀请的人”，右侧灰色禁用勾选框 |
| 不允许被邀请进群的人 | 否 | 显示“不允许被邀请进群的人”，右侧灰色禁用勾选框 |

相关前端逻辑：

- `fetchGroupManageState()`
- `renderGroupInviteSidebar()`
- `renderGroupManageInviteList()`
- `groupManageInviteMember()`
- `respondToGroupInvitation()`

## 3. 后端数据模型

新增模型：`GroupInvitation`

位置：`chat/models.py`

### 3.1 字段

| 字段 | 说明 |
| --- | --- |
| `conversation` | 被邀请加入的群聊 |
| `inviter` | 发起邀请的人 |
| `invitee` | 被邀请的人 |
| `status` | 邀请状态 |
| `reviewed_by` | 审核该邀请的管理员 |
| `reviewed_at` | 管理员审核时间 |
| `responded_at` | 被邀请人响应时间 |
| `created_at` / `updated_at` | 创建和更新时间 |

### 3.2 状态

| 状态 | 说明 |
| --- | --- |
| `pending_admin` | 普通成员邀请后，等待群主/管理员审核 |
| `pending_invitee` | 等待被邀请人同意 |
| `accepted` | 被邀请人已同意，成员关系已激活 |
| `rejected` | 管理员或被邀请人已拒绝 |
| `cancelled` | 预留取消状态 |

### 3.3 约束

同一群聊中，同一被邀请人只能有一个待处理邀请：

```text
unique_pending_group_invitation
conversation + invitee where status in (pending_admin, pending_invitee)
```

迁移文件：

- `chat/migrations/0016_groupinvitation.py`

## 4. 后端接口

### 4.1 发起邀请

```http
POST /api/groups/<conversation_id>/invite/
```

请求体：

```json
{
  "user_id": 2
}
```

权限规则：

| 发起者 | 结果 |
| --- | --- |
| 群主/管理员 | 创建 `pending_invitee`，直接等待被邀请人确认 |
| 普通成员 | 创建 `pending_admin`，先等待群管理审核 |
| 非 active 群成员 | 返回 403 |

隐私规则：

- 若目标用户 `who_can_add_me_to_groups = nobody`，返回 403。
- 若目标用户仅允许联系人邀请，且发起者不是其联系人，返回 403。
- 若目标用户已经是 active 成员，返回 409。

### 4.2 邀请候选列表

```http
GET /api/groups/<conversation_id>/invite-candidates/
```

返回当前用户可见的邀请候选及不可邀请原因。

候选来源：

1. 当前用户联系人。
2. 当前群所有历史成员，包括 `left` 和 `removed`。
3. 已在群成员。
4. 已有待处理邀请的人。
5. 当前用户自己。

响应示例：

```json
{
  "candidates": [
    {
      "user_id": 5,
      "username": "test_bot",
      "display_name": "test_bot",
      "user_type": "bot",
      "can_invite": true,
      "reason_code": "former_member",
      "reason": "曾经退群，可重新邀请",
      "membership_status": "left",
      "invitation_status": null
    }
  ]
}
```

`reason_code` 取值：

| `reason_code` | 说明 |
| --- | --- |
| `available` | 可邀请 |
| `former_member` | 曾经退群或被移除，可重新邀请 |
| `self` | 当前用户自己 |
| `already_member` | 已经在群里的成员 |
| `pending_invitation` | 已经有待处理邀请 |
| `not_allowed` | 对方隐私设置不允许被邀请 |

### 4.3 管理员审批邀请

```http
POST /api/group-invitations/<invitation_id>/approve/
```

仅群主/管理员可操作。审批后邀请状态从 `pending_admin` 变为 `pending_invitee`，等待被邀请人确认。

### 4.4 拒绝邀请

```http
POST /api/group-invitations/<invitation_id>/reject/
```

可由两类人操作：

1. 群主/管理员拒绝 `pending_admin` 邀请。
2. 被邀请人拒绝 `pending_invitee` 邀请。

### 4.5 被邀请人同意邀请

```http
POST /api/group-invitations/<invitation_id>/accept/
```

仅被邀请人可操作。

同意后：

1. `ConversationMember` 通过 `update_or_create()` 激活成员关系。
2. 若用户曾经退群或被移除，会将原成员关系恢复为 `active`。
3. 邀请状态更新为 `accepted`。
4. 群聊 `membership_version` 增加。
5. 通过 `group.members.changed` 通知相关客户端刷新成员状态。

### 4.6 被邀请人待确认列表

```http
GET /api/group-invitations/pending/
```

返回当前用户所有 `pending_invitee` 邀请，用于前端邀请通知托盘。

响应示例：

```json
{
  "invitations": [
    {
      "id": 12,
      "status": "pending_invitee",
      "group_id": 3,
      "group_name": "G1",
      "group_avatar": "",
      "group_initials": "G1",
      "group_avatar_color": "#5c6bc0",
      "inviter_id": 1,
      "inviter_username": "HeanX",
      "inviter_display_name": "HeanX",
      "created_at": "2026-06-19T10:00:00+00:00"
    }
  ]
}
```

## 5. 实时通知

### 5.1 WebSocket 事件

新增事件：

```text
group.invitation.new
```

### 5.2 Consumer

位置：`chat/consumers.py`

| 方法 | 说明 |
| --- | --- |
| `group_invitation_new()` | 将 channel layer 事件转发给前端 WebSocket |
| `broadcast_group_invitation()` | 向被邀请人推送邀请通知 |
| `broadcast_group_invitation_to_admins()` | 向群主/管理员推送待审核通知 |

注意：推送给被邀请人的 payload 在同步 view 中组装为纯 dict，再交给 channel layer，避免 async consumer 中触发同步 ORM 查询。

### 5.3 推送触发点

位置：`chat/views.py`

| 触发点 | 推送对象 | 说明 |
| --- | --- | --- |
| 群主/管理员发起邀请 | 被邀请人 | 创建 `pending_invitee` 后推送 |
| 普通成员发起邀请 | 群主/管理员 | 创建 `pending_admin` 后推送 |
| 管理员审批通过 | 被邀请人 | 状态改为 `pending_invitee` 后推送 |

### 5.4 前端处理

位置：`static/js/chat.js`

| 函数 | 说明 |
| --- | --- |
| `handleIncomingMessage()` | 将 `group.invitation.new` 路由到邀请处理函数 |
| `handleGroupInvitationNew()` | 收到推送后刷新通知托盘或管理面板 |
| `fetchPendingGroupInvitations()` | 拉取当前用户待确认邀请 |
| `renderPendingGroupInvitations()` | 渲染邀请通知卡 |
| `respondToGroupInvitation()` | 同意或拒绝邀请 |
| `startPendingGroupInvitationPolling()` | 启动 20 秒轮询作为兜底 |

## 6. 前端通知托盘

页面新增容器：

```html
<div id="group-invite-notification-tray" class="group-invite-notification-tray hidden"></div>
```

位置：`templates/pages/chat.html`

样式位置：`static/css/chat.css`

通知卡展示：

1. 群头像或群名缩写。
2. 群名称。
3. 邀请人。
4. “同意 / 拒绝”按钮。

用户同意后会刷新会话列表；用户拒绝后会移除通知卡。

## 7. 数据流

### 7.1 管理员直接邀请

```text
群主/管理员点击邀请
→ POST /api/groups/<id>/invite/
→ 创建 GroupInvitation(status=pending_invitee)
→ WebSocket group.invitation.new 推送给被邀请人
→ 被邀请人通知托盘展示
→ 被邀请人同意
→ POST /api/group-invitations/<id>/accept/
→ ConversationMember 激活
→ membership_version +1
→ group.members.changed 推送
→ 会话和成员列表刷新
```

### 7.2 普通成员邀请

```text
普通成员点击邀请
→ POST /api/groups/<id>/invite/
→ 创建 GroupInvitation(status=pending_admin)
→ WebSocket group.invitation.new 推送给群主/管理员
→ 管理面板显示待审核邀请
→ 管理员批准
→ status 改为 pending_invitee
→ WebSocket group.invitation.new 推送给被邀请人
→ 被邀请人同意后入群
```

### 7.3 退群成员重新邀请

```text
用户曾经是 ConversationMember(status=left/removed)
→ invite-candidates 返回该用户，can_invite=true
→ 前端显示“曾经退群，可重新邀请”
→ 被邀请人同意
→ update_or_create() 恢复 ConversationMember(status=active)
```

## 8. 权限与安全要点

1. 只有 active 群成员可以发起邀请。
2. 普通成员邀请需要管理审核。
3. 被邀请人必须显式同意才会成为群成员。
4. 群邀请遵守被邀请人的隐私设置。
5. 同一群聊同一用户不能重复存在待处理邀请。
6. 邀请接受后会更新 `membership_version`，后续群聊 E2EE 发送必须基于新的成员版本。
7. 管理员只能审核 `pending_admin` 邀请，不能替被邀请人同意。
8. 被邀请人只能接受属于自己的 `pending_invitee` 邀请。

## 9. 主要文件清单

| 文件 | 内容 |
| --- | --- |
| `chat/models.py` | `GroupInvitation` 模型 |
| `chat/migrations/0016_groupinvitation.py` | 邀请模型迁移 |
| `chat/views.py` | 群邀请、候选列表、审批、同意/拒绝、待确认列表、WebSocket 推送触发 |
| `chat/urls.py` | 群邀请相关 API 路由 |
| `chat/consumers.py` | `group.invitation.new` 实时通知 |
| `chat/admin.py` | 后台注册 `GroupInvitation` |
| `chat/tests.py` | 群邀请流程测试 |
| `templates/components/right_panel.html` | 群信息右侧入口 |
| `templates/pages/group_invite_sidebar.html` | 邀请成员右侧面板 |
| `templates/pages/group_manage_sidebar.html` | 群组管理右侧面板 |
| `templates/pages/chat.html` | 邀请通知托盘容器 |
| `static/js/chat.js` | 群邀请前端逻辑、通知托盘、WebSocket 事件处理 |
| `static/css/chat.css` | 群管理、邀请列表、通知卡样式 |

## 10. 验证结果

已执行并通过：

```powershell
node --check static\js\chat.js
.venv\Scripts\python.exe manage.py check
.venv\Scripts\python.exe manage.py test chat.tests.GroupAPITests chat.test_p2_issues.GroupManagementTests chat.test_p2_backend.PrivacySecurityAPITests.test_group_invite_respects_target_privacy
```

当前群管理/邀请相关测试共 36 个通过。

## 11. 后续建议

1. 将邀请通知接入统一通知中心，避免后续与系统通知、好友申请通知分散。
2. 增加 WebSocket 推送的集成测试，覆盖 `group.invitation.new` 到前端事件的完整链路。
3. 增加邀请过期或撤回能力，对长期 pending 的邀请做清理。
4. 为邀请列表增加分页或服务端搜索，避免联系人和历史成员数量较大时一次性返回过多数据。
5. 在 E2EE 群聊发送前更明确提示成员版本变化，避免邀请同意后旧成员快照继续发送。
