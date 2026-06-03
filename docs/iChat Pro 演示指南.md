# iChat Pro 一期演示指南

> 版本：v1.0
> 适用：Phase 1 验收演示
> 更新日期：2026-06-03

## 1. 演示准备

### 1.1 环境要求

- Python 3.12+，虚拟环境已安装依赖
- Node.js 18+（如需桌面端演示）
- 两个浏览器或两个浏览器 Profile（模拟两个用户）

### 1.2 启动服务

```powershell
# 终端 1 — Django 后端
.\.venv\Scripts\Activate.ps1
python manage.py migrate
python manage.py runserver 127.0.0.1:8000

# 终端 2 — Electron 桌面端（可选）
cd desktop
npm install
npm start
```

### 1.3 创建演示账号

打开 Django shell 创建两个测试用户：

```powershell
python manage.py shell
```

```python
from django.contrib.auth import get_user_model

User = get_user_model()
alice = User.objects.create_user('alice', password='demo1234')
bob = User.objects.create_user('bob', password='demo1234')

# 可选：创建第三个用户用于群聊演示
carol = User.objects.create_user('carol', password='demo1234')
```

或者通过浏览器注册：
1. 打开 http://127.0.0.1:8000/register/
2. 注册 alice / demo1234
3. 打开另一个浏览器（或隐身窗口），注册 bob / demo1234

## 2. 演示流程

### 2.1 注册与登录（约 2 分钟）

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 打开 http://127.0.0.1:8000/ | 自动跳转到登录页 |
| 2 | 点击注册链接，注册 alice | 注册成功，自动跳转到聊天主页 |
| 3 | 在另一个浏览器注册 bob | 同上 |
| 4 | 分别退出后重新登录 | 输入用户名密码，登录成功 |

**展示点：** 注册/登录表单校验、错误提示、登录后跳转

### 2.2 个人资料与密钥管理（约 2 分钟）

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 登录 alice，点击侧边栏 Settings | 显示设置面板 |
| 2 | 展开 Key Management，点击 Generate | 自动生成 ECDH P-256 密钥对 |
| 3 | 点击 Upload 上传公钥到服务器 | 显示密钥指纹和 Active 状态 |
| 4 | 点击 Export 下载密钥备份文件 | 下载 JSON 文件 |
| 5 | 修改昵称和个性签名 | 保存成功 |

**展示点：** 密钥在客户端生成、仅公钥上传、私钥可备份导出

### 2.3 添加联系人（约 2 分钟）

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | alice 点击 Contacts 页面 | 显示空联系人列表 |
| 2 | 搜索 bob 的 username | 显示搜索结果 |
| 3 | 发送好友申请 | 提示发送成功 |
| 4 | 切换到 bob 浏览器 | 看到好友申请通知 |
| 5 | bob 同意申请 | 双方成为联系人 |

**展示点：** 搜索、好友申请、同意/拒绝流程

### 2.4 私聊端到端加密（约 3 分钟）

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | alice 点击侧边栏 + 号，添加联系人 | 输入 bob 的用户名 |
| 2 | 系统自动创建私聊会话 | 侧边栏显示新会话 |
| 3 | alice 发送消息 "Hello Bob, this is E2EE secured" | 消息显示为已发送 |
| 4 | 切换到 bob 浏览器 | bob 实时收到消息 |
| 5 | bob 回复 "Hi Alice, I received your encrypted message" | alice 实时收到 |
| 6 | 点击聊天头部锁图标 | 查看加密协议详情 |
| 7 | 打开浏览器 DevTools → Network 标签 | 确认 WebSocket 数据中无明文 |

**展示点：** 实时收发、密文传输、加密指纹验证

### 2.5 群聊逐成员加密（约 3 分钟）

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | alice 点击 New Group | 打开创建群组弹窗 |
| 2 | 输入群名 "Demo Team"，勾选 bob 和 carol | 创建成功 |
| 3 | alice 发送群消息 "Team meeting at 3pm" | 消息已发送 |
| 4 | 切换到 bob | 收到群消息 |
| 5 | 切换到 carol | 同样收到群消息 |
| 6 | 在 Django Admin 查看消息记录 | 每人的 ciphertext 值不同 |

**展示点：** 群聊创建、逐成员独立密文、实时推送

### 2.6 密钥丢失恢复提示（约 1 分钟）

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 打开浏览器 DevTools → Application → Local Storage | |
| 2 | 删除 `ichat_ecdh_*` 相关键值 | |
| 3 | 刷新页面 | 提示密钥缺失，可导入备份 |
| 4 | 导入之前导出的密钥备份文件 | 恢复成功 |

**展示点：** 密钥丢失检测、备份导入恢复

### 2.7 Electron 桌面端（约 1 分钟，可选）

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | `cd desktop && npm start` | Electron 窗口打开 |
| 2 | Electron 自动启动 Django 或连接已有服务 | 显示登录页 |
| 3 | 登录并使用完整聊天功能 | 与浏览器版一致 |

**展示点：** 桌面端封装、Django 自动启停

## 3. 演示环境快速搭建（一键脚本）

保存为 `demo_setup.py` 在项目根目录运行：

```python
"""一键创建演示账号并建立联系关系"""
import os, django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ichat_pro.settings')
django.setup()

from django.contrib.auth import get_user_model
from accounts.models import Contact, UserPublicKey, FriendRequest

User = get_user_model()

# 创建演示用户
alice, _ = User.objects.get_or_create(username='alice')
alice.set_password('demo1234'); alice.save()

bob, _ = User.objects.get_or_create(username='bob')
bob.set_password('demo1234'); bob.save()

carol, _ = User.objects.get_or_create(username='carol')
carol.set_password('demo1234'); carol.save()

# 建立双向联系人
for u1, u2 in [(alice, bob), (alice, carol), (bob, carol)]:
    for a, b in [(u1, u2), (u2, u1)]:
        Contact.objects.get_or_create(user=a, contact=b)
        FriendRequest.objects.filter(sender=a, receiver=b).update(status='accepted')

print('Demo accounts ready:')
print('  alice / demo1234')
print('  bob   / demo1234')
print('  carol / demo1234')
```

运行：`python demo_setup.py`

## 4. 验收检查清单

| # | 检查项 | 通过 |
|---|--------|------|
| 1 | 用户可以注册、登录、退出 | ☐ |
| 2 | 注册/登录有明确的错误提示 | ☐ |
| 3 | 用户可以修改个人资料（昵称、签名、头像） | ☐ |
| 4 | 客户端可以生成 ECDH 密钥对并上传公钥 | ☐ |
| 5 | 密钥可以导出备份和导入恢复 | ☐ |
| 6 | 用户可以搜索和添加联系人 | ☐ |
| 7 | 好友申请可以发送、同意、拒绝 | ☐ |
| 8 | 私聊消息实时送达 | ☐ |
| 9 | WebSocket 数据和数据库无明文 | ☐ |
| 10 | 群聊可以创建、邀请成员、管理成员 | ☐ |
| 11 | 群聊消息每位成员收到独立密文 | ☐ |
| 12 | 非联系人无法发送私聊消息 | ☐ |
| 13 | 已退出成员不再收到群聊新消息 | ☐ |
| 14 | 密钥丢失后有明确恢复提示 | ☐ |
| 15 | Electron 桌面端可正常启动和登录 | ☐ |
| 16 | 页面刷新后历史消息仍然可加载 | ☐ |
| 17 | 所有自动化测试通过 (181 tests) | ☐ |
