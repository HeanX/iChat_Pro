# Phase4 云端部署架构（P4 T04 / Issue #131）

> 状态：已按 AWS 东京节点实况定稿（2026-10-01）。运维操作细则见 [deploy/runbook.md](../../deploy/runbook.md)。

## 1. 目标部署拓扑

```text
Browser / Electron / (Android 暂缓)
          │  HTTPS 8443 + WSS（Session/Cookie + CSRF）
          ▼
   Nginx（TLS 终止，TCP 80 仅 ACME+301）
          │  HTTP proxy_pass + Upgrade
          ▼
   Daphne 127.0.0.1:8000（systemd 自动重启，多 worker 可水平扩）
          │                │                │
   PostgreSQL 16       Redis 6.2       /var/lib/ichat/media
   (127.0.0.1:5432)  (127.0.0.1:6379)  avatars 公开 / uploads 密文仅鉴权接口
   业务与密文元数据    分发/缓存(非真相)   持久卷，重启不丢
```

事实来源约定：PostgreSQL 是消息与业务数据的事实来源；Redis 只承担 Channel Layer 分发与缓存，`group_send` 成功不代表持久送达，遗漏补取必须读 PG（详见 technical-design §1）。

## 2. 端口与网络边界（与共存服务隔离）

| 端口 | 归属 | 开放 |
| --- | --- | --- |
| TCP 22 | SSH | 管理员 |
| TCP 80 | Nginx：ACME HTTP-01 + 301 → 8443 | 公网（SG 放行） |
| TCP 8443 | Nginx：iChat HTTPS/WSS | 公网（SG 放行） |
| TCP 8000 | Daphne | 仅 127.0.0.1 |
| TCP 5432 | PostgreSQL | 仅 127.0.0.1 |
| TCP 6379 | Redis | 仅 127.0.0.1 |
| TCP/UDP 443、2096、25383 | **Xray/3x-ui（既有共存服务）** | 不属于本项目，禁止占用/改动 |

**决策记录（ADR-P4-01）：聊天 HTTPS 使用 8443。** 目标主机 TCP/UDP 443 已被既有 Xray（VLESS Reality + Hysteria2）占用，交接方明确要求不得覆盖；Reality 不支持向本地 Nginx 的透明回退分流，因此聊天服务独立监听 8443。后果：客户端地址需带端口（`https://chat.20060810.xyz:8443`）；`DJANGO_CSRF_TRUSTED_ORIGINS`、Electron `ICHAT_SERVER_URL` 均使用带端口的完整 Origin。HTTP→HTTPS 跳转由 80 端口 server 块完成（NEW-CLD-002 的“自动跳转”按此口径验收）。

## 3. 数据流要点

- **静态资源**：`collectstatic` 输出（含仓库内预构建 `static/css/tailwind.css`）由 Nginx 直接服务，7 天缓存。
- **头像**：`/media/avatars/` 由 Nginx 直接服务（公开策略）；**`/media/uploads/`（密文分块与合并文件）绝不直接服务**，下载一律走鉴权的 `/api/files/{id}/...` 视图，防止 URL 绕过 `EncryptedFileKey` 权限。
- **上传大小**：Nginx `client_max_body_size 32m` 覆盖分块上传的 base64 JSON 体；WS 心跳 30s，Nginx `proxy_read_timeout 300s`。
- **TLS 信任链**：Daphne 仅监听 loopback，`SECURE_PROXY_SSL_HEADER` 只信任本机 Nginx 注入的 `X-Forwarded-Proto`；`DJANGO_CSRF_TRUSTED_ORIGINS` 显式列出 8443 Origin。

## 4. 进程与恢复

- `ichat.service`（systemd，`Restart=always`，`EnvironmentFile=/opt/ichat/.env`）满足 T07“异常退出自动恢复”；Redis/PostgreSQL 由 systemd 托管开机自启。
- 生产配置失败即拒绝启动：非法 `DATABASE_URL`/`REDIS_URL` 在 settings 解析期抛 `ImproperlyConfigured`（P4 T11，不再静默回退 SQLite）。
- 健康检查：`/health/live/`（进程）、`/health/ready/`（DB SELECT 1 + 缓存往返探测，失败 503），公开输出仅状态字。

## 5. 安全边界摘要

| 层 | 措施 |
| --- | --- |
| 传输 | TLS 1.2/1.3，HTTP→HTTPS 301，HSTS（Django，代理后生效） |
| 认证 | Django Session Cookie（HttpOnly/Secure）；WS 未认证 4401 关闭 |
| 秘密 | 全部在 `/opt/ichat/.env`（600, ichat 属主）；仓库/安装包/日志零秘密 |
| 数据 | 服务端仅存密文+公钥；PG/Redis/媒体不暴露公网；备份产物 700 权限、14 天保留 |
| 审计 | 访问/错误日志分文件；应用日志只记版本、request_id、事件类型、错误码与必要实体 ID |
