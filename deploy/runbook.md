# iChat Pro 部署 Runbook（AWS 东京节点）

> 更新日期：2026-10-18；核对代码基线：`cfe53be`。现行说明已按代码和验收证据更新。
> 当前状态见 [项目现状](../docs/current-status.md)；文档用途与归档规则见 [文档维护索引](../docs/documentation-status.md)。

## 本次更新

2026-10-18（#204 / P4 Test T23）：发布流程在 readiness 之后加了一道**冒烟门禁** `deploy/smoke.sh`，覆盖登录、CSRF、会话与核心 API；失败不写 `DEPLOYMENTS.log` 且 deploy 退出非零。新增第 7 节说明冒烟账号策略与前置条件。该门禁目前只在本地 SQLite + Django live server 下验证过（19 条用例，其中 6 条锚定 deploy.sh 的日志语义），**未在生产验证**。

2026-10-18（#176 / P4 T49）：deploy.sh 路径/服务名参数化（ICHAT_APP_DIR/ICHAT_MEDIA_DIR/ICHAT_SERVICE_NAME/ICHAT_SERVICE_USER），新增 deploy/staging/（env.staging.example、ichat-staging.service、check_isolation.sh）与第 3c 节；11 条隔离断言用例本地通过。**staging 实机未搭建，未在生产验证**。

2026-10-18（#139 / P4 T12）：新增 `deploy/monitor.sh`（readiness 连续失败/PG 磁盘与连接数/Redis 内存/证书余期/journal 错误，webhook 告警带限频），见第 3b 节；18 条用例在本地通过（第 2 轮审查后补 4 条不可观测场景）。**未在生产验证**。

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
- 发布：`sudo ICHAT_APPROVAL='<ticket-or-PR>' /opt/ichat/bin/deploy.sh`（审批门禁 → 校验配置 → migrate → collectstatic → restart → readiness 门禁 → 冒烟门禁 → 审计记录）。
- **审批门禁（T50 / #177）**：发布必须带 `ICHAT_APPROVAL`（工单/PR/变更号），否则在**改动服务之前**就拒绝。回滚（`--commit`）不需要审批——逃生通道不能依赖纸面流程。`ICHAT_REQUIRE_APPROVAL=0` 是已知良好紧急发布的口子，会被记成 `approval=none` 并在 stderr 警告。
- **readiness 门禁**：带 Host 头经本机 Nginx TLS 请求 `/health/ready/` 并校验 JSON body——对任意被部署版本都有效（旧版本会把 http 探测 301 到 https），301/400 不会误判成功。origin 由解析后的安装配置推导：`https://127.0.0.1:${ICHAT_TLS_PORT:-8443}`，回落 `http://127.0.0.1:${ICHAT_HTTP_PORT:-8000}`（staging 即 8444/8001，不会打到生产端口）；需要非常规 origin 时用 `ICHAT_READY_ORIGIN`/`ICHAT_LOOPBACK_ORIGIN` 覆盖。
- **冒烟门禁（T23 / #204）**：readiness 通过后执行 `deploy/smoke.sh`，断言 `health/live`、`health/ready`、登录表单与 CSRF、登录会话、`/api/conversations/`、`/api/profile/me/`，以及未认证 `/api/` 返回 401。任一失败则**不写 `DEPLOYMENTS.log` 并 exit 1**——"能连上但登录坏了"的版本不算发布成功。目标树**没有** `deploy/smoke.sh` 时，前向发布一律**失败且不写日志**；只有明确的历史回滚（`--commit <40位SHA>`，`IS_ROLLBACK=1`）才允许跳过并记 `smoke=skipped-no-script`。服务此时已重启，需用 `--commit <上一个SHA>` 回退。
- **审计记录（T50 / #177）**：每次成功发布向 `/opt/ichat/DEPLOYMENTS.log` 追加**一行**结构化记录：

  ```text
  ts=<UTC> sha=<sha> target=<branch[@commit]> kind=<deploy|rollback> \
  actor=<发布人> via=<sudo|direct> from=<来源IP> smoke=<passed|skipped|skipped-no-script> approval=<引用>
  ```

  - `actor` 回答"谁发布的"：`sudo` 下取 `SUDO_USER`（不是 root），否则取 `$USER`；CI 调用时用 `ICHAT_DEPLOYER`。字段经白名单清洗，注入字符会变成 `_`。
  - `from` 取 `SSH_CONNECTION` 的客户端地址，本地控制台记 `local-console`。注意：sudo 默认 `env_reset` 常丢弃 `SSH_CONNECTION`，此时远程发布也会记成 `local-console`；需要精确来源时请 `sudo -E` 或从 CI 注入 `ICHAT_DEPLOYER`。
  - 服务器实际运行的 SHA **以该日志最后一条记录为准**（每次发布成功才追加）；`git -C /opt/ichat/repo rev-parse HEAD` 仅作参考——被拒绝的发布不会改日志，但手工 checkout 会改工作树，两者可能不一致。
- **查看发布记录**：`sudo /opt/ichat/bin/deploy.sh --status`（最近 5 条 + 最后一条成功发布的 SHA，后者才是权威的"在跑什么"）；`--status --verbose` 显示完整字段（默认视图隐藏 `via=`/`from=` 以保持可读）。行数可用 `ICHAT_STATUS_LINES` 调整。
- **回滚应用**：`sudo /opt/ichat/bin/deploy.sh --commit <上一个SHA>`（**必须是完整 40 位 SHA**；传分支名/短 ref 会被拒绝，因为那是伪装成回滚的前向发布）——checkout 到指定 commit 并**停在该版本**（不会重置回 origin/main），探针通过后记为 `kind=rollback`。恢复新版本：重跑 `deploy.sh`（不带 --commit，仍需审批）。`/opt/ichat/.deploy-state/{current,previous}` 另外记住当前与上一个 SHA。**数据库不自动降级**；不可逆迁移的恢复走备份（第 4 节）。
- 失败排查：`journalctl -u ichat -n 100`、`tail -50 /var/log/nginx/ichat.error.log`、冒烟失败项见 deploy 输出。

## 3b. 监控告警（T12 / #139）

`deploy/monitor.sh` 以只读方式巡检验收标准点名的四类状态，**不重启任何服务、不碰 443/2096/25383、不读聊天数据**：

| 检查 | 触发条件（默认阈值） | 告警 key |
| --- | --- | --- |
| 服务 | `/health/ready/` 连续 3 次失败（单次失败只计数，发布重启不会误报） | `ready` |
| 资源 | PG 数据盘使用率 ≥ 85%；PG 连接数 ≥ 40 | `disk` / `pg-conn` |
| 资源 | Redis `used_memory` ≥ 512MB | `redis-mem` |
| 证书 | TLS 证书剩余有效期 < 14 天（说明 `certbot renew` 已静默失败） | `cert` |
| 错误 | `journalctl -u ichat -p err` 最近 10 分钟 ≥ 10 条 | `journal` |

安装（root crontab，5 分钟一轮）：

```cron
*/5 * * * * /opt/ichat/repo/deploy/monitor.sh >> /var/log/ichat-monitor.log 2>&1
```

白名单是 `monitor.sh` 的 `ENV_FILE_KEYS`（含 `DJANGO_ALLOWED_HOSTS`，它提供探针的 `Host` 头）：**只有列出的名字生效**，进程环境同名变量优先于 `.env`，其余行一律忽略。取值语法与 systemd 的 `EnvironmentFile` 一致：`KEY=1`、`KEY="1"`、`KEY='1'`、`KEY=1  # 说明` 都有效（引号会被剥掉，行尾 `#` 只在前面是空白且不在引号内时才算注释），所以写在 `.env` 里的阈值不会因为多了一对引号就静默退回默认值（F-05）。值只做文本处理，绝不 `eval`。

'自检：`ICHAT_ALERT_TEST=1 /opt/ichat/repo/deploy/monitor.sh` 发一条测试告警，用于验证 webhook 连通。

**pg-conn 的认证前提**：root 的 cron 环境走 peer 认证不会映射到 `ichat` 角色，所以脚本从 `.env` 的 `DATABASE_URL`（或 `ICHAT_DB_URL`）解析连接参数，经 `PGPASSWORD` 环境变量交给 libpq（密码不进 argv/ps）；解析器本身也**只从 stdin 读 URL**——命令行参数会出现在 `ps` 与任何 argv 记录器里。若该项缺失或认证失败，该轮以 `CHECK FAILED: pg-conn` 告警并以 exit 1 结束——"查不到连接数"不会伪装成"一切正常"。**任何检查跑不成**（工具缺失、证书不可读、状态目录不可写、`journalctl` 查询失败）都会触发 `monitor` 告警并 exit 1。

投递失败的补偿：限频时间戳只在 webhook **成功**后写入，投递失败下一轮会重试（日志模式下仍写入时间戳，避免每 5 分钟刷屏）。'

说明：证书检查**不跑** `certbot renew --dry-run`（它请求 LE staging 且有速率限制），而是读已装证书的 `notAfter`——若余额跌破 14 天，说明续期链路已静默失败，告警里会指向 letsencrypt 日志。状态计数在 `/var/lib/ichat/monitor`，"连续失败"跨 cron 轮次累计。

## 3c. 独立测试环境（T49 / #176）

staging 与生产共用一台 t3.small，但**不共享任何配置、数据、密钥或运行时**。`deploy.sh` 的安装身份**从 `$ICHAT_APP_DIR/.env` 解析**（与 systemd 的 `EnvironmentFile` 同一个文件），调用进程的环境变量优先：

| 变量 | 作用 | 生产缺省 | staging 应设 |
| --- | --- | --- | --- |
| `ICHAT_APP_DIR` | 安装树（决定读哪个 `.env`） | `/opt/ichat` | `/opt/ichat-staging` |
| `ICHAT_SERVICE_NAME` | 重启/启用的单元 | `ichat` | `ichat-staging` |
| `ICHAT_SERVICE_USER` | media 属主 | `ichat` | `ichat-staging` |
| `ICHAT_MEDIA_DIR` | media 根 | `/var/lib/ichat/media` | `/var/lib/ichat-staging/media` |
| `ICHAT_TLS_PORT` | readiness 经 Nginx 的端口 | `8443` | `8444` |
| `ICHAT_HTTP_PORT` | readiness 回落 Daphne 的端口 | `8000` | `8001` |

**这些身份键只能由调用方提供**（review finding F-01）：`deploy.sh` 会先在 `$APP_DIR/.env` 里扫描 `ICHAT_APP_DIR`/`ICHAT_PROD_APP_DIR`/`ICHAT_SERVICE_NAME`/`ICHAT_SERVICE_USER`/`ICHAT_MEDIA_DIR`/`ICHAT_TLS_PORT`/`ICHAT_HTTP_PORT`/`ICHAT_READY_ORIGIN`/`ICHAT_LOOPBACK_ORIGIN`/`ICHAT_DEPLOYER`/`ICHAT_APPROVAL`/`ICHAT_REQUIRE_APPROVAL`/`ICHAT_SKIP_SMOKE`，**只要出现任一键就以 exit 2 拒绝执行**（绝不静默忽略：能被部署的树若能自己选择重启哪个单元、探测哪个端口、审计行记谁发布，OPS-01 的守卫就形同虚设）。其余键（`DATABASE_URL` 等应用配置）仍从 `.env` 读取。

布局（全部独立于生产）：

| 项 | 生产 | staging |
| --- | --- | --- |
| 代码树 | `/opt/ichat` | `/opt/ichat-staging` |
| systemd 单元 | `ichat.service`（Daphne 127.0.0.1:8000） | `ichat-staging.service`（Daphne 127.0.0.1:8001） |
| 数据库 | `ichat`（角色 ichat） | `ichat_staging`（角色 ichat_staging，独立密码） |
| Redis 逻辑库 | db 0 | db 1 |
| media | `/var/lib/ichat/media` | `/var/lib/ichat-staging/media` |
| static | `/opt/ichat/staticfiles` | `/opt/ichat-staging/staticfiles`（**不得**在生产根目录之内） |
| 发布端口 | `ICHAT_TLS_PORT=8443` / `ICHAT_HTTP_PORT=8000` | `ICHAT_TLS_PORT=8444` / `ICHAT_HTTP_PORT=8001` |
| 秘密 | `/opt/ichat/.env` | `/opt/ichat-staging/.env`（**全新 SECRET_KEY，绝不复制生产**） |
| 入口 | `https://chat.20060810.xyz:8443` | staging 专用 origin:8444（nginx server 块单独配） |

搭建（首次）：

```bash
sudo useradd -r -s /sbin/nologin ichat-staging
sudo install -d -o ichat-staging -g ichat-staging /opt/ichat-staging /var/lib/ichat-staging
sudo -u postgres psql -c "CREATE ROLE ichat_staging LOGIN;"   # 交互 \password，勿进 shell 历史
sudo -u postgres psql -c "CREATE DATABASE ichat_staging OWNER ichat_staging;"
# 填 deploy/staging/env.staging.example -> /opt/ichat-staging/.env (600, ichat-staging)
sudo cp deploy/staging/ichat-staging.service /etc/systemd/system/
sudo systemctl daemon-reload
# 身份/端口**只能**在命令行给：deploy.sh 见到 .env 里出现这些键会 exit 2，
sudo ICHAT_APP_DIR=/opt/ichat-staging \
     ICHAT_MEDIA_DIR=/var/lib/ichat-staging/media \
     ICHAT_SERVICE_NAME=ichat-staging \
     ICHAT_SERVICE_USER=ichat-staging \
     ICHAT_TLS_PORT=8444 \
     ICHAT_HTTP_PORT=8001 \
     ICHAT_REQUIRE_APPROVAL=0 \
     /opt/ichat-staging/bin/deploy.sh --branch main
```

隔离证据（每次 staging 变更后复跑，可归档进 Issue）：

```bash
sudo deploy/staging/check_isolation.sh --check-data
```

它逐项断言（任一违反即 exit 1）：SECRET_KEY 与生产不同；`DATABASE_URL` **逐维度**比较——登录角色名与角色密码各自必须不同（只改库名不算秘密隔离），staging 库名（从 URL path 解析）必须是 `ichat_staging`；CSRF origin 不同；Redis 逻辑库不同；**media 与 static 两类根目录**互不相等、互不为祖先（先做 `/` 与 `..` 规范化，尾斜杠、重复分隔符与相对跳转都绕不过；static 用 `collectstatic --clear`，嵌套会互删文件）；代码树/服务单元/服务用户不同；daphne 端口非 8000（unit 找不到时报 "port UNVERIFIED" 而非静默通过）。任何 key 缺失都会报告而不是静默中断；秘密值只以 SHA-256 前缀形式出现，绝不明文打印。

`--check-data` 会**真的连接**：用 `psql` 读两库各自的 `current_user`（登录角色）；用 `redis-cli -n <URL 里的库号> client info` 读连接实际所在的逻辑库（`db=` 出现在单行输出的中间，按字段解析而不是按行首匹配），再比较。URL 里没有库号、输出里没有 `db=`、或服务器实际库号与 URL 不符时，该维度报 `UNVERIFIED` 并 exit 1——不会把“没查”或“查不准”写成 `ISOLATED`。凭据只走 `PGPASSWORD` 环境变量，不出现在 argv。

staging 的发布走同一条 deploy.sh（审批门禁可用 `ICHAT_REQUIRE_APPROVAL=0`，因为 staging 本身就是审批前验证环节），冒烟门禁照常运行（staging 有自己的 `SMOKE_USERNAME/SMOKE_PASSWORD`）。
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
