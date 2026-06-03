# iChat Pro 部署安全说明

> 版本 1.0 — 2026 年 6 月 4 日
> 适用环境：生产部署 / 预发布环境

---

## 1. 前置要求

### 1.1 HTTPS / TLS

**必须启用 HTTPS。** 所有 WebSocket 连接使用 `wss://`，所有 API 请求通过 HTTPS 传输。未加密的 HTTP 连接会导致：
- 身份公钥在传输中被替换（中间人攻击）
- CSRF Token 泄露
- 会话 Cookie 被劫持

**Nginx 反向代理示例：**

```nginx
server {
    listen 443 ssl http2;
    server_name chat.example.com;

    ssl_certificate     /etc/ssl/certs/ichat.crt;
    ssl_certificate_key /etc/ssl/private/ichat.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
    }
}
```

### 1.2 Django 安全设置

在 `ichat_pro/settings.py` 中确认以下配置：

```python
# 强制 HTTPS
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000  # 1 年
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Cookie 安全
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True

# 防止浏览器 MIME 嗅探
SECURE_CONTENT_TYPE_NOSNIFF = True

# 防止点击劫持
X_FRAME_OPTIONS = 'DENY'
```

---

## 2. Content-Security-Policy (CSP)

### 2.1 推荐策略

在 Django 中通过中间件或 Nginx 添加以下 CSP 头：

```
Content-Security-Policy:
  default-src 'self';
  script-src 'self' https://cdn.tailwindcss.com https://unpkg.com;
  style-src 'self' 'unsafe-inline';
  img-src 'self' data:;
  connect-src 'self' wss://chat.example.com;
  font-src 'self';
  object-src 'none';
  base-uri 'self';
  form-action 'self';
  frame-ancestors 'none';
```

**注意：**
- `script-src` 中包含 `cdn.tailwindcss.com`（开发用 Play CDN）和 `unpkg.com`（lucide 图标库）
- **生产环境建议**：使用 Tailwind CLI 构建静态 CSS 文件后，从 CSP 中移除 `cdn.tailwindcss.com`，此时 `script-src` 仅需 `'self' https://unpkg.com`
- 当前模板中仍有少量 inline script（主题切换等），若需去除 `'unsafe-inline'`，需将这些脚本迁移至外部 JS 文件并使用 nonce/hash

### 2.2 Django CSP 中间件

本项目已内置 CSP 中间件（`ichat_pro/csp_middleware.py`）。在 `settings.py` 中已注册：

```python
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'ichat_pro.csp_middleware.CSPMiddleware',  # ← 自定义 CSP 中间件
    # ... 其他中间件 ...
]
```

**工作模式：**
- `DEBUG=True`（开发）：发送 `Content-Security-Policy-Report-Only` 头，违规仅记录不拦截
- `DEBUG=False`（生产）：发送 `Content-Security-Policy` 头，违规会被浏览器拦截
- 生产环境下 `TAILWIND_CDN=False` 时，CSP 头中会自动移除 `cdn.tailwindcss.com` 白名单

---

## 3. 依赖管理

### 3.1 浏览器端 CDN 依赖

| 依赖 | 版本 | 固定方式 | SRI |
| --- | --- | --- | --- |
| **lucide** (图标库) | `0.462.0` | 固定版本 URL | ✅ 已添加 |
| **tailwindcss** | 生产：静态 CSS 构建 / 开发：Play CDN | `TAILWIND_CDN` 配置开关 | N/A（生产自托管） |

### 3.2 Tailwind CSS 生产构建

**开发环境**（`DEBUG=True`）：自动使用 Tailwind Play CDN（实时类名变更）。

**生产环境**（`DEBUG=False` / `TAILWIND_CDN=False`）：使用预构建的静态 CSS 文件。

```bash
# 首次部署或更新 Tailwind 配置后执行
npm ci                    # 安装 Tailwind CLI（固定版本）
npm run build:css         # 构建并压缩 CSS → static/css/tailwind.css
```

构建产物 `static/css/tailwind.css`（约 43 KB 压缩后）已纳入版本控制。部署时无需运行 npm，直接使用预构建文件即可。

**更新依赖：**
1. 修改 `package.json` 中的 `tailwindcss` 版本
2. 运行 `npm install && npm run build:css`
3. 提交更新后的 `package-lock.json` 和 `static/css/tailwind.css`

### 3.2 Python 依赖

```bash
# 固定所有依赖版本
pip freeze > requirements.txt

# 或使用 pip-tools 管理
pip-compile requirements.in
```

关键依赖及最低版本建议：
- Django >= 5.0
- channels >= 4.0
- daphne >= 4.0

### 3.3 Node.js 依赖（如有 Tailwind 构建）

```bash
# package.json 中固定版本
npm ci  # 使用 package-lock.json 精确安装
```

---

## 4. 密钥管理

### 4.1 用户身份密钥

- **存储位置：** 浏览器 `localStorage`
- **格式：** JWK (JSON Web Key)
- **算法：** ECDH P-256
- **备份方式：** JSON 文件下载（通过"导出密钥备份"功能）
- **恢复方式：** JSON 文件导入（会验证公私钥一致性）

### 4.2 管理员操作须知

- 不要在服务器端存储用户私钥 — 用户私钥仅存在于浏览器 `localStorage` 中
- 服务器仅存储用户**公钥**（`IdentityPublicKey` 模型），用于其他用户查询
- 如果用户清除浏览器数据，私钥将丢失 — 必须提前导出备份
- 密钥轮换时（上传新公钥），`key_version` 递增，旧版本公钥保留用于解密历史消息

### 4.3 用户安全指南

部署时应向用户提供以下指导：

1. **首次登录后立即导出密钥备份**（设置 → 导出密钥备份）
2. **将备份文件存储在安全位置**（加密 U 盘、个人密码管理器等）
3. **不要将备份文件存储在云盘公开目录**
4. **更换设备时使用"导入密钥备份"恢复**
5. **若怀疑密钥泄露，立即在设置中重置密钥**

---

## 5. 环境变量清单

部署时应设置以下环境变量：

| 变量 | 说明 | 必须 |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | Django SECRET_KEY | ✅ |
| `DJANGO_DEBUG` | 调试模式（生产环境必须为 `False`） | ✅ |
| `ALLOWED_HOSTS` | 允许的域名列表，逗号分隔 | ✅ |
| `DATABASE_URL` | 数据库连接字符串（如使用 django-environ） | ✅ |
| `REDIS_URL` | Redis 连接字符串（Channels 层必需） | ✅ |

**严禁将 SECRET_KEY 硬编码在代码中或提交至版本控制。**

---

## 6. 检查清单

部署前逐项确认：

- [ ] HTTPS 已启用，HTTP 自动跳转至 HTTPS
- [ ] `DJANGO_DEBUG = False`
- [ ] `SECURE_SSL_REDIRECT = True`
- [ ] `SECURE_HSTS_SECONDS` 已设置
- [ ] CSP 头已配置（至少 `script-src` 限制）
- [ ] CSRF Cookie 标记为 `Secure`
- [ ] Session Cookie 标记为 `Secure` 和 `HttpOnly`
- [ ] `lucide` CDN 链接使用固定版本 + SRI
- [ ] `TAILWIND_CDN=False` 且 `static/css/tailwind.css` 已构建
- [ ] Django SECRET_KEY 已从代码中移除，改为环境变量
- [ ] Redis 已配置且 Channels 层正常工作
- [ ] 数据库已备份
- [ ] 域名 DNS 解析正确
- [ ] 防火墙规则已审核（仅暴露 80/443 端口）
- [ ] 用户密钥备份流程已测试

---

## 7. 应急响应

### 7.1 疑似私钥泄露

1. 通知受影响用户立即重置身份密钥
2. 用户密钥重置后，旧密钥加密的消息将无法解密（设计如此）
3. 审计服务器日志，确认是否存在异常 API 调用

### 7.2 CDN 资源被篡改

1. 立即从 `templates/base.html` 中移除被篡改的 CDN 链接
2. 切换至备用 CDN 或本地托管
3. 通知用户清除浏览器缓存
4. 审计是否存在用户数据泄露

### 7.3 发现 XSS 漏洞

1. 立即修复漏洞并部署更新
2. 通知所有用户导出最新密钥备份
3. 建议用户重置身份密钥（因为无法确认私钥是否已被窃取）
4. 进行安全审计以排查类似漏洞
