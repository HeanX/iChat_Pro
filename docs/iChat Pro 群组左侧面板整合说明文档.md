# iChat Pro 群组左侧面板整合说明文档

## 1. 背景

原有群组管理入口位于独立页面 `/groups/`，用户需要从聊天主界面跳出到单独页面查看群组、创建群组或进入群组详情。新的产品方向是将群组能力整合进聊天左侧面板，参考设置页的弹出面板体验，让群组浏览、创建和邀请成员都在主聊天工作区内完成。

本次调整保留原 `/groups/` 页面与路由作为兼容入口，同时新增左侧 Groups 面板，并将主菜单中的 Groups 入口改为打开该面板。

## 2. 本次完成内容

### 2.1 新增左侧 Groups 面板

新增模板：

- `templates/pages/groups_sidebar.html`

该模板负责左侧群组面板主体内容，包括：

- 新建群组入口。
- 群组数量展示。
- 群组列表容器。
- 空状态展示。

面板结构复用设置页的视觉体系：

- `settings-template-body`
- `settings-template-card`
- `settings-template-row`
- `settings-template-row-icon`
- `settings-template-row-main`
- `settings-template-chevron`

这样可以保持左侧面板的层级、圆角、列表行高度和 hover 行为与设置页一致。

### 2.2 将 Groups 入口从页面跳转改为面板切换

修改文件：

- `templates/components/sidebar.html`

原入口：

```html
<a href="{% url 'groups' %}" class="main-menu-item-custom" onclick="toggleMainMenu(event);">
```

调整为：

```html
<a href="{% url 'groups' %}" class="main-menu-item-custom" onclick="navigateSidebar('groups'); toggleMainMenu(event); return false;">
```

行为变化：

- 保留 `href="{% url 'groups' %}"` 作为兼容和无脚本降级入口。
- 正常前端运行时阻止默认跳转。
- 使用 `navigateSidebar('groups')` 打开左侧 Groups 面板。

### 2.3 在 sidebar 中注册 Groups 视图

修改文件：

- `templates/components/sidebar.html`

新增：

```html
<div id="sidebar-view-groups" class="sidebar-view settings-view hidden h-full flex flex-col bg-bgSidebar">
```

该视图包括：

- 返回聊天列表按钮。
- 群组搜索框。
- `groups_sidebar.html` 模板引入。

搜索框通过：

```html
oninput="renderGroupsSidebar(this.value)"
```

实时筛选当前用户已有群聊。

### 2.4 前端渲染群组列表

修改文件：

- `static/js/chat.js`

新增/调整函数：

- `getSidebarGroups()`
- `renderGroupsSidebar(query)`
- `startGroupCreationFromGroups()`
- `openGroupFromSidebar(groupId)`
- `openGroupInviteFromSidebar(groupId)`

数据来源：

- 直接使用现有 `conversations` 缓存。
- 过滤条件为 `conv.type === "group"`。

这样无需新增后端列表接口，也不会引入额外页面请求。

群组列表项支持：

- 展示头像、群名称、最近消息预览、成员数量。
- 点击主区域打开群聊。
- 点击邀请按钮进入该群的邀请成员流程。

### 2.5 将 Groups 加入左侧导航状态机

修改文件：

- `static/js/chat.js`

在 `navigateSidebar(viewName)` 的 `views` 列表中加入：

```js
'groups'
```

并在进入该视图时刷新群组列表：

```js
if (viewName === 'groups') {
  setTimeout(function() { renderGroupsSidebar(); }, 50);
}
```

同时在 `renderChatList()` 末尾调用：

```js
renderGroupsSidebar();
```

保证会话列表刷新后，左侧 Groups 面板同步更新。

### 2.6 邀请成员入口联动

新增函数：

```js
async function openGroupInviteFromSidebar(groupId) {
  if (!groupId || !conversationsById[groupId]) return;
  navigateSidebar("chat");
  await selectChat(groupId);
  await openGroupInviteFromDetails(groupId);
}
```

设计意图：

- 群邀请流程已存在于右侧群管理/邀请面板中。
- 左侧 Groups 面板不重复实现邀请逻辑。
- 点击邀请按钮后先选中群聊，再打开已有邀请面板。

复用已有函数：

- `selectChat(groupId)`
- `openGroupInviteFromDetails(groupId)`
- `refreshGroupManageSidebar()`
- `renderGroupInviteSidebar()`
- `groupManageInviteMember(userId)`

### 2.7 新建群组入口联动

新增函数：

```js
function startGroupCreationFromGroups() {
  resetGroupCreationFlow();
  navigateSidebar("group-add-members");
}
```

设计意图：

- 复用现有左侧两步建群流程。
- 不再弹出旧的独立模态框。
- 与联系人页 FAB 中的新建群组体验保持一致。

### 2.8 样式补充

修改文件：

- `static/css/chat.css`

新增样式前缀：

- `.groups-sidebar-*`

核心样式包括：

- `.groups-sidebar-body`
- `.groups-sidebar-summary`
- `.groups-sidebar-list-card`
- `.groups-sidebar-section-head`
- `.groups-sidebar-list`
- `.groups-sidebar-row`
- `.groups-sidebar-main`
- `.groups-sidebar-avatar`
- `.groups-sidebar-copy`
- `.groups-sidebar-title`
- `.groups-sidebar-subtitle`
- `.groups-sidebar-meta`
- `.groups-sidebar-action`
- `.groups-sidebar-empty`

设计原则：

- 不新增独立页面风格。
- 不使用营销式布局。
- 保持左侧工具面板紧凑、可扫描。
- 列表项采用稳定高度，避免内容变化导致布局跳动。
- 长群名和消息预览使用省略处理，避免溢出。

## 3. 当前交互流程

### 3.1 查看群组

1. 用户点击左上主菜单。
2. 点击 `Groups`。
3. 左侧面板切换到 `sidebar-view-groups`。
4. 前端从 `conversations` 中筛选 `type === "group"` 的会话并渲染列表。

### 3.2 搜索群组

1. 用户在 Groups 面板顶部搜索框输入关键词。
2. `renderGroupsSidebar(query)` 重新过滤群组。
3. 匹配范围包括群名称和最近消息预览。

### 3.3 打开群聊

1. 用户点击群组列表行主区域。
2. 调用 `openGroupFromSidebar(groupId)`。
3. 面板返回聊天列表视图。
4. 调用 `selectChat(groupId)` 打开对应群聊。

### 3.4 邀请成员

1. 用户点击群组列表行右侧邀请按钮。
2. 调用 `openGroupInviteFromSidebar(groupId)`。
3. 前端先打开该群聊。
4. 再调用既有的 `openGroupInviteFromDetails(groupId)`。
5. 右侧群邀请面板展示可邀请联系人列表。

### 3.5 新建群组

1. 用户打开 Groups 面板。
2. 点击 `New Group`。
3. 调用 `startGroupCreationFromGroups()`。
4. 重置建群状态。
5. 进入现有 `group-add-members` 左侧建群流程。

## 4. 与旧 `/groups/` 页面的关系

本次没有删除旧页面：

- `accounts/urls.py`
- `accounts/views.py`
- `templates/pages/groups.html`
- `templates/pages/group_detail.html`

保留原因：

- 避免外部链接失效。
- 避免已有测试或后续后端验收依赖被破坏。
- 为渐进式迁移保留回退路径。

后续如果确认左侧面板完全替代独立页面，可以再做清理：

- 将 `/groups/` 重定向到聊天页。
- 删除旧模板。
- 删除旧表单式加成员页面。
- 更新相关测试。

## 5. 涉及文件清单

新增：

- `templates/pages/groups_sidebar.html`
- `docs/iChat Pro 群组左侧面板整合说明文档.md`

修改：

- `templates/components/sidebar.html`
- `static/js/chat.js`
- `static/css/chat.css`

保留未改：

- `accounts/urls.py`
- `accounts/views.py`
- `templates/pages/groups.html`
- `templates/pages/group_detail.html`
- `chat/urls.py`
- `chat/views.py`
- `chat/models.py`
- `templates/pages/group_invite_sidebar.html`
- `templates/pages/group_manage_sidebar.html`

## 6. 验证结果

已执行：

```powershell
.\.venv\Scripts\python.exe manage.py check
```

结果：

```text
System check identified no issues (0 silenced).
```

已执行：

```powershell
.\.venv\Scripts\python.exe manage.py check --tag templates
```

结果：

```text
System check identified no issues (0 silenced).
```

已执行：

```powershell
node --check static\js\chat.js
```

结果：

```text
无语法错误输出。
```

未完成：

- 本地浏览器烟测未执行成功。

原因：

- 启动本地开发服务时被当前 Codex 环境审批器拦截，提示使用额度限制。
- 该问题不是项目代码问题。

## 7. 后续建议

### 7.1 UI 验收

建议手动打开：

```text
http://127.0.0.1:8000/chat/
```

检查：

- 主菜单 `Groups` 是否打开左侧面板。
- 搜索框是否能筛选群组。
- 群组列表是否显示头像、名称、预览和成员数。
- 点击群组是否能打开聊天。
- 点击邀请按钮是否能进入右侧邀请成员面板。
- 空群组状态是否正常。
- 移动端左侧面板切换是否正常。

### 7.2 设计清理

当前项目中建群流程已有部分中文出现编码异常，例如 `娣诲姞鎴愬憳` 这类乱码。建议后续统一修复：

- `templates/components/sidebar.html` 中的建群步骤标题。
- 建群输入 placeholder。
- 空状态文案。

### 7.3 产品收敛

如果新左侧 Groups 面板验收通过，可以将独立 `/groups/` 页面标记为兼容入口，并在后续版本中：

- 改为跳转 `/chat/`。
- 或在聊天页自动打开 Groups 面板。
- 清理旧页面模板和旧表单加成员流程。
