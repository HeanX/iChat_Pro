# iChat Pro 部署 Runbook（AWS 东京节点）

> 适用主机：AWS ap-northeast-1d / t3.small / Amazon Linux 2023 / EIP `13.158.121.80`。
> 本机与该主机共存代理服务（Xray 占用 TCP/UDP 443、面板 25383、订阅 2096），**这些端口不属于聊天系统，操作时禁止占用或重启它们**。
> 秘密（DB 密码、SECRET_KEY、3x-ui 凭据）只保存在服务器上；本文不记录任何真实秘密。

## 1. 架构与端口

```
Browser / Electron ──HTTPS(8443)/WSS──> Nginx(TLS) ──HTTP──> Daphne 127.0.0.1:8000
                                                              │
                                              PostgreSQL 5432 (localhost) + Redis 6379 (localhost)
```

| 端口 | 用途 | 开放范围 |
| --- | --- | --- |
| TCP 22 | SSH | 管理员 |
| TCP 80 | ACME HTTP-01 + 301 跳转 HTTPS | 公网 |
| TCP 8443 | iChat HTTPS / WSS | 公网 |
| TCP 8000 | Daphne（loopback） | 仅 127.0.0.1 |
| TCP 5432 / 6379 | PostgreSQL / Redis | 仅 127.0.0.1 |
| TCP 443 / UDP 443 / 2096 / 25383 | Xray 与 3x-ui（共存服务） | 勿动 |

AWS 安全组需放行 TCP 80、8443（入站 0.0.0.0/0）。TLS 证书由 Let's Encrypt HTTP-01 签发（webroot 指向 `/var/www/acme`），certbot 自动续期。

## 2. 首次部署

1. 安装依赖：`sudo dnf install -y python3.13 python3.13-pip git nginx postgresql16-server redis6 certbot`
2. 初始化 PostgreSQL：`sudo postgresql-setup --initdb && sudo systemctl enable --now postgresql`
3. 创建数据库与账号（密码用 `openssl rand -hex 24` 生成，写入 .env，勿回显）：
   `sudo -u postgres psql -c "CREATE USER ichat PASSWORD '...'; CREATE DATABASE ichat OWNER ichat;"`
4. Redis：`sudo systemctl enable --now redis6`（默认仅监听 127.0.0.1）
5. 建服务账号与目录：
   `sudo useradd -r -d /opt/ichat -s /sbin/nologin ichat`
   `sudo install -d -o ichat -g ichat /opt/ichat /var/lib/ichat/media /var/www/acme`
6. 配置：把 `deploy/env.production.example` 填好放到 `/opt/ichat/.env`（owner ichat，chmod 600）；`deploy/systemd/ichat.service` 复制到 `/etc/systemd/system/`。
7. **首次引导仓库**：`sudo git clone --branch main https://github.com/HeanX/iChat_Pro.git /opt/ichat/repo`（deploy.sh 在仓库内，首次需手动克隆；之后 deploy.sh 会自行 fetch/重置）。
8. **Nginx 必须先于首次部署**：替换 `deploy/nginx/ichat.conf` 中的 `__PLACEHOLDER__` 放到 `/etc/nginx/conf.d/`，用临时自签证书起 8443——因为 deploy.sh 的 readiness 门禁默认经 `https://127.0.0.1:8443` 全前门路径（Nginx 不可达时自动回退 loopback http，仅对带 health 免跳转的版本有效）。`sudo nginx -t && sudo systemctl enable --now nginx`。
9. **首次部署**：`sudo bash /opt/ichat/repo/deploy/deploy.sh`（自动 clone 兜底、建 venv、migrate、collectstatic、启动、探针；并安装稳定副本到 `/opt/ichat/bin/deploy.sh`，此后统一用该入口）。
10. 签发正式证书：`sudo certbot certonly --webroot -w /var/www/acme -d sub.20060810.xyz`，把 8443 server 块指向 `/etc/letsencrypt/live/.../fullchain.pem`，`sudo nginx -s reload`。把 `deploy/nginx/reload-nginx.sh` 安装到 `/etc/letsencrypt/renewal-hooks/deploy/ichat-reload-nginx.sh`（chmod 755）：`certbot renew` 成功续期后自动 `nginx -t` + reload，避免 Nginx 继续使用旧证书。用 `sudo certbot renew --force-renewal` 一次性验证整条链路（受 LE 每周重复证书限额约束，勿频繁执行）。

## 3. 日常发布与回滚（T50 路径）

- **统一入口：`/opt/ichat/bin/deploy.sh`**（仓库 checkout 之外的稳定副本）。调用仓库内副本时脚本会自动 `install` 自身到该路径并 re-exec——回滚到旧 commit 会把仓库里的 deploy.sh 一并回退，稳定副本保证工具行为不受影响。
- 发布：`sudo /opt/ichat/bin/deploy.sh`（校验配置 → migrate → collectstatic → restart → readiness 门禁）。
- **readiness 门禁**：带生产 Host 头经本机 Nginx TLS（`https://127.0.0.1:8443`）请求 `/health/ready/` 并校验 JSON body——对任意被部署版本都有效（旧版本会把 http 探测 301 到 https），301/400 不会误判成功。
- **每次部署把 `<UTC时间> <commit SHA> <目标>` 追加到 `/opt/ichat/DEPLOYMENTS.log`**；服务器实际运行的 SHA 以该文件和 `git -C /opt/ichat/repo rev-parse HEAD` 为准，可能与仓库 main 不同（回滚期间）。
- **回滚应用**：`sudo /opt/ichat/bin/deploy.sh --commit <上一个SHA>`——checkout 到指定 commit 并**停在该版本**（不会重置回 origin/main），探针通过后记录日志。恢复新版本：重跑 `deploy.sh`（不带 --commit）。**数据库不自动降级**；不可逆迁移的恢复走备份（第 4 节）。
- 失败排查：`journalctl -u ichat -n 100`、`tail -50 /var/log/nginx/ichat.error.log`。

## 4. 备份与恢复（T13）

- 每日备份（root crontab）：`30 17 * * * /opt/ichat/repo/deploy/backup.sh`（UTC 17:30 = 东京 02:30）。
- 备份内容：`pg_dump` 全库 + `media/` 打包 + SHA256 + 来源 commit，保留 14 天，目录权限 700。
- 恢复演练要求：在**独立实例**上恢复，核对用户/关系/会话/消息数量、文件抽样与客户端解密（详见 requirements.md §3.3）。可复跑演练：`sudo bash deploy/restore_drill.sh`——seed 业务数据（用户/联系人/私聊/群聊/加密文件+逐持有者密钥）→ backup → **运行真实 restore.sh**（drill 参数注入独立库与独立媒体目录，不碰在线服务）→ 校验行数 parity、关系完整性、密文逐字节 parity、文件摘要；同主机独立库口径，跨独立实例完整演练属 P1。
- 恢复命令：`sudo bash deploy/restore.sh /var/backups/ichat/<STAMP>`（需要二次输入数据库名确认；恢复前自动停服、旧 media 移至 `.pre-restore-*`）。演练可用环境变量注入目标：`RESTORE_ENV_FILE`、`ICHAT_DB_URL`、`ICHAT_MEDIA_DIR`、`ICHAT_SKIP_SERVICE=1`、`ICHAT_ASSUME_YES=1`。

## 5. 共存服务红线

- 不修改 `/etc/x-ui/`、Xray 配置与 443 监听；不动 2096/25383。
- 聊天服务任何组件不得监听 TCP 443、UDP 443、2096、25383。
- 系统内存紧张（t3.small 1.9GiB）时优先检查 Xray 与 PostgreSQL 的 `shared_buffers`，不要通过停掉共存服务腾内存。

## 6. 健康与日志

- `GET /health/live/`（进程存活）、`GET /health/ready/`（DB+Redis 探测，失败 503）。
- 应用日志在 `journalctl -u ichat`；Nginx 访问/错误日志在 `/var/log/nginx/ichat.*.log`。日志不得记录 Cookie、密文载荷或完整请求体。
