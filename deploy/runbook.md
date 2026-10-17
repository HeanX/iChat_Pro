# iChat Pro 部署 Runbook（AWS 东京节点）

> 更新日期：2026-10-18；核对代码基线：`cfe53be`。现行说明已按代码和验收证据更新。
> 当前状态见 [项目现状](../docs/current-status.md)；文档用途与归档规则见 [文档维护索引](../docs/documentation-status.md)。

## 本次更新

2026-10-18（#204 / P4 Test T23）：发布流程在 readiness 之后加了一道**冒烟门禁** `deploy/smoke.sh`，覆盖登录、CSRF、会话与核心 API；失败不写 `DEPLOYMENTS.log` 且 deploy 退出非零。新增第 7 节说明冒烟账号策略与前置条件。该门禁目前只在本地 SQLite + Django live server 下验证过（19 条用例，其中 6 条锚定 deploy.sh 的日志语义），**未在生产验证**。

2026-10-18（#177 / P4 T50）：发布加入**审批门禁**与**审计记录**。`DEPLOYMENTS.log` 由旧的 4 字段格式升级为结构化单行（`ts/sha/target/kind/actor/via/from/smoke/approval`），新增 `--status` 查询与 `/opt/ichat/.deploy-state` 状态目录。旧日志行仍是原样前缀，但字段不再对齐——解析旧行请按空格切分取前 4 段。43 条用例覆盖审计字段、审批语义、注入防护与状态跟踪，**未在生产验证**。

现行生产入口 chat.20060810.xyz:8443；代码与部署基线 f2f60ec，最后核对 DEPLOYMENTS.log 为 2026-10-07T13:42:16Z。live/ready/login HTTPS 200，readiness 自动检查 DB+cache JSON，登录/核心接口冒烟失败阻断已实现代码（#204 待真实环境证据）；监控告警（#139）和完整发布审批（#177）仍待验收。服务器 SHA、GitHub main 和安装包 SHA 分别记录，回滚不自动降级数据库。

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
3. 创建数据库与账号：先 `sudo -u postgres psql`，分别执行 `CREATE ROLE ichat LOGIN;`、交互 `\password ichat`（避免密码进入命令/日志），再执行 `CREATE DATABASE ichat OWNER ichat;`。将连接凭据安全写入 .env；CREATE DATABASE 必须在事务块之外执行，不能把它和建角色合并成同一条 `psql -c`。
4. Redis：`sudo systemctl enable --now redis6`（默认仅监听 127.0.0.1）
5. 建服务账号与目录：
   `sudo useradd -r -d /opt/ichat -s /sbin/nologin ichat`
   `sudo install -d -o ichat -g ichat /opt/ichat /var/lib/ichat/media /var/www/acme`
6. 配置：把 `deploy/env.production.example` 填好放到 `/opt/ichat/.env`（owner ichat，chmod 600）；`deploy/systemd/ichat.service` 复制到 `/etc/systemd/system/`。
7. **首次引导仓库**：`sudo git clone --branch main https://github.com/HeanX/iChat_Pro.git /opt/ichat/repo`（deploy.sh 在仓库内，首次需手动克隆；之后 deploy.sh 会自行 fetch/重置）。
8. **Nginx 必须先于首次部署**：替换 `deploy/nginx/ichat.conf` 中的 `__PLACEHOLDER__` 放到 `/etc/nginx/conf.d/`，用临时自签证书起 8443——因为 deploy.sh 的 readiness 门禁默认经 `https://127.0.0.1:8443` 全前门路径（Nginx 不可达时自动回退 loopback http，仅对带 health 免跳转的版本有效）。`sudo nginx -t && sudo systemctl enable --now nginx`。
9. **首次部署**：`sudo bash /opt/ichat/repo/deploy/deploy.sh`（自动 clone 兜底、建 venv、migrate、collectstatic、启动、探针；并安装稳定副本到 `/opt/ichat/bin/deploy.sh`，此后统一用该入口）。
10. 签发正式证书：`sudo certbot certonly --webroot -w /var/www/acme -d chat.20060810.xyz`，把 8443 server 块指向 `/etc/letsencrypt/live/.../fullchain.pem`，`sudo nginx -s reload`。把 `deploy/nginx/reload-nginx.sh` 安装到 `/etc/letsencrypt/renewal-hooks/deploy/ichat-reload-nginx.sh`（chmod 755）：`certbot renew` 成功续期后自动 `nginx -t` + reload，避免 Nginx 继续使用旧证书。用 `sudo certbot renew --force-renewal` 一次性验证整条链路（受 LE 每周重复证书限额约束，勿频繁执行）。

## 3. 日常发布与回滚（T50 路径）

- **统一入口：`/opt/ichat/bin/deploy.sh`**（仓库 checkout 之外的稳定副本）。调用仓库内副本时脚本会自动 `install` 自身到该路径并 re-exec——回滚到旧 commit 会把仓库里的 deploy.sh 一并回退，稳定副本保证工具行为不受影响。
- 发布：`sudo ICHAT_APPROVAL='<ticket/PR>' /opt/ichat/bin/deploy.sh`（审批门禁 → 校验配置 → migrate → collectstatic → restart → readiness 门禁 → 冒烟门禁 → 审计记录）。
- **审批门禁（T50 / #177）**：发布必须带 `ICHAT_APPROVAL`（工单/PR/变更号），否则在**改动服务之前**就拒绝。回滚（`--commit`）不需要审批——逃生通道不能依赖纸面流程。`ICHAT_REQUIRE_APPROVAL=0` 是已知良好紧急发布的口子，会被记成 `approval=none` 并在 stderr 警告。
- **readiness 门禁**：带生产 Host 头经本机 Nginx TLS（`https://127.0.0.1:8443`）请求 `/health/ready/` 并校验 JSON body——对任意被部署版本都有效（旧版本会把 http 探测 301 到 https），301/400 不会误判成功。
- **冒烟门禁（T23 / #204）**：readiness 通过后执行 `deploy/smoke.sh`，断言 `health/live`、`health/ready`、登录表单与 CSRF、登录会话、`/api/conversations/`、`/api/profile/me/`，以及未认证 `/api/` 返回 401。任一失败则**不写 `DEPLOYMENTS.log` 并 exit 1**——"能连上但登录坏了"的版本不算发布成功。服务此时已重启，需用 `--commit <上一个SHA>` 回退。
- **审计记录（T50 / #177）**：每次成功发布向 `/opt/ichat/DEPLOYMENTS.log` 追加**一行**结构化记录：

  ```text
  ts=<UTC> sha=<sha> target=<branch[@commit]> kind=<deploy|rollback> \
  actor=<发布人> via=<sudo|direct> from=<来源IP> smoke=<passed|skipped|skipped-no-script> approval=<引用>
  ```

  - `actor` 回答"谁发布的"：`sudo` 下取 `SUDO_USER`（不是 root），否则取 `$USER`；CI 调用时用 `ICHAT_DEPLOYER`。字段经白名单清洗，注入字符会变成 `_`。
  - `from` 取 `SSH_CONNECTION` 的客户端地址，本地控制台记 `local-console`。
  - 服务器实际运行的 SHA 以该文件与 `git -C /opt/ichat/repo rev-parse HEAD` 为准，可能与仓库 main 不同（回滚期间）。
- **查看发布记录**：`sudo /opt/ichat/bin/deploy.sh --status`（最近 5 条 + 最后一条成功发布的 SHA，后者才是权威的"在跑什么"）；`--status --verbose` 显示完整字段（默认视图隐藏 `via=`/`from=` 以保持可读）。行数可用 `ICHAT_STATUS_LINES` 调整。
- **回滚应用**：`sudo /opt/ichat/bin/deploy.sh --commit <上一个SHA>`——checkout 到指定 commit 并**停在该版本**（不会重置回 origin/main），探针通过后记为 `kind=rollback`。恢复新版本：重跑 `deploy.sh`（不带 --commit，仍需审批）。`/opt/ichat/.deploy-state/{current,previous}` 另外记住当前与上一个 SHA。**数据库不自动降级**；不可逆迁移的恢复走备份（第 4 节）。
- 失败排查：`journalctl -u ichat -n 100`、`tail -50 /var/log/nginx/ichat.error.log`、冒烟失败项见 deploy 输出。

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

## 7. 部署冒烟账号（T23 / #204）

冒烟门禁需要一个**专用**账号来验证登录与核心 API。不要用真实用户账号：那会把生产会话写进真人账号的登录记录，且账号被改密/禁用后所有发布都会失败。

创建（在服务器上，一次性）：

```bash
sudo -u ichat -E env DIR=$(mktemp -d) \
    /opt/ichat/.venv/bin/python /opt/ichat/repo/manage.py shell -c "
from django.contrib.auth import get_user_model
U = get_user_model()
u, created = U.objects.get_or_create(username='deploy_smoke')
u.set_password('<强随机密码>'); u.is_active = True; u.save()
print('created' if created else 'updated', u.username)
"
```

然后把账号密码写进 `/opt/ichat/.env`（该文件已 chmod 600、owner ichat）：

```text
SMOKE_USERNAME=deploy_smoke
SMOKE_PASSWORD=<与上面 set_password 相同的强随机密码>
```

注意：

- 账号需要 `is_active=True` 且无 `must_change_password` 类限制，否则登录会失败。
- 每次发布冒烟会为它创建并立刻销毁一个会话（脚本 EXIT 时 `POST /logout/`）；发布日志里因此会出现该账号的登录/登出记录，属预期。
- **前置条件**：`.env` 的 `DJANGO_CSRF_TRUSTED_ORIGINS` 必须含带端口的 8443 Origin（如 `https://chat.20060810.xyz:8443`）。冒烟走真实登录表单，Referer 是带端口的 Origin，而 nginx 的 `$host` 会去掉端口；不匹配时登录 POST 被 CSRF 拒绝，冒烟判失败。浏览器登录的前置条件与此相同。
- **`http://127.0.0.1:8000` 回退不能用于冒烟**：`DEBUG=False` 下 `SECURE_REDIRECT_EXEMPT` 只豁免 `health/`，且 `CSRF_COOKIE_SECURE`/`SESSION_COOKIE_SECURE` 已开，登录在纯 http 上必然失败。回退只够跑健康检查，脚本会打印 WARNING。首次部署请先起 Nginx。
- **回滚到 #204 之前的版本**：目标树没有 `deploy/smoke.sh`，门禁打印 WARNING 并记 `smoke=skipped-no-script`，**不会**让回滚失败。
- **紧急例外**：若登录链路已确认损坏、需要先发布修复，可在该次发布前 `export ICHAT_SKIP_SMOKE=1`。这会把 `smoke=skipped` 明确写进 `DEPLOYMENTS.log`，属于可追溯的例外，**不是常规路径**。
- 缺 `SMOKE_USERNAME`/`SMOKE_PASSWORD` 时 deploy.sh **直接失败**（不会跳过），这是刻意设计：没有冒烟证据的发布不允许静默通过。
