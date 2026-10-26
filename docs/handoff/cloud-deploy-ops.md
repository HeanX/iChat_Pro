# 模块 5：云部署与运维（`deploy/` + health + nginx/systemd + 备份恢复）

> 交接对象：负责服务器部署、发布审批/回滚、监控告警、staging 隔离、备份恢复的 Agent 或运维。
> 核对日期 2026-10-26，**核对提交 `2905473`（`iChat_Pro.wt-t176`，分支 `feat/p4-t49-test-environment`）**。
> 相关规范见 [conventions.md](conventions.md)。

---

## 0. 这份文档的依据与边界（先读）

本文所有关于脚本行为的断言都来自**实际阅读提交里的文件**，不是 runbook 转述。核对方式：
从 `iChat_Pro.wt-t176` 的提交 `2905473` 用 `git show <sha>:<path>` 导出全部 `deploy/**` 到只读快照，
逐文件核对后用 `git hash-object` 比对 blob 前缀（14/14 一致）。

| 项 | 值 |
| --- | --- |
| 核对提交 | `2905473d43954d37a0ce21660ec4f5fbccfb2d74`（`fix(deploy): fail a forward release whose tree has no smoke.sh (OPS-03)`） |
| 该提交包含的修复 | OPS-03（前向发布缺 `smoke.sh` 必须失败）、QA-01（`deploy/__init__.py` 使 `deploy.tests` 可被默认发现） |
| **不在**该提交里的修复 | OPS-01 / OPS-02 / OPS-05 / OPS-06 / OPS-07 / OPS-08 / OPS-09 / OPS-10（见 §9）；另有一条本次新发现（DOC-NEW-1，备份/恢复未参数化） |
| `deploy.sh` blob | `5e42d84c133e42a22fb9f48ca719dffbb48d0c06`（17870 B） |
| `smoke.sh` blob | `f3a45b015b453ffb62a57b2b8b14e4f511643ee7`（13774 B） |
| `monitor.sh` blob | `54a698608ffd01fe1bc427aada878470c0832592`（15033 B） |
| `staging/check_isolation.sh` blob | `601e802e87117d2972d364dba7f729a5ae8c9b06`（9044 B） |
| `runbook.md` blob | `afd1727c50563091a67b501e94c88e2682bdff1d`（18854 B） |

> **重要（协作时序）**：写作时 `iChat_Pro.wt-t176` 工作树**正在被另一个 Agent 修改**（未提交的
> `deploy/deploy.sh`、`deploy/smoke.sh`、`deploy/tests/*` 改动）。本文只描述**已提交**的 `2905473`，
> 不描述那些仍在变的未提交内容。合并前请用上表 blob 复核；若 blob 变了，本文的行号与结论需要重读。

---

## 1. 这块现在是什么

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `deploy/deploy.sh` | 406 | 唯一发布入口：审批门禁 → 取码 → venv → 迁移/静态 → 重启 → readiness → 冒烟 → 审计记录 |
| `deploy/smoke.sh` | 330 | 部署后冒烟门禁：health/readiness/契约/登录/会话/核心 API/未认证契约 |
| `deploy/monitor.sh` | 366 | cron 监控：readiness 连续失败、PG 磁盘与连接数、Redis 内存、证书余期、journal 错误 |
| `deploy/staging/check_isolation.sh` | 215 | staging 与生产的隔离断言（秘密/配置/存储/运行时/可选活数据） |
| `deploy/staging/env.staging.example` | 40 | staging `.env` 模板（明确要求每项都与生产不同） |
| `deploy/staging/ichat-staging.service` | 28 | staging systemd 单元（`ichat-staging` 用户，Daphne 8001） |
| `deploy/systemd/ichat.service` | 23 | 生产 systemd 单元（`ichat` 用户，Daphne 8000） |
| `deploy/env.production.example` | 22 | 生产 `.env` 模板 |
| `deploy/nginx/ichat.conf` | 81 | Nginx 站点：80（ACME + 301）/ 8443（TLS、静态、avatars、proxy 到 8000） |
| `deploy/nginx/reload-nginx.sh` | 10 | certbot deploy-hook：`nginx -t` 后 reload |
| `deploy/backup.sh` | 45 | `pg_dump` + media 打包 + SHA256 + META，保留 14 天 |
| `deploy/restore.sh` | 74 | 真实恢复：校验和 → 停服 → drop schema → 回放 → media 换目录 → 起服并探针 |
| `deploy/restore_drill.sh` | 119 | 可复跑恢复演练：seed → backup → 真实 `restore.sh`（注入独立库/目录）→ 逐项校验 |
| `deploy/seed_drill_data.py` | 160 | 演练用的确定性业务数据（用户/关系/私聊/群聊/加密文件+逐持有者密钥） |
| `deploy/tests/` | 5 个模块 **85** 条 | 上述脚本的 Python 回归（见 §8 末尾的计数口径） |

`ichat_pro/settings.py` 中与本模块相关的三段：`DATABASE_URL`/`REDIS_URL` 解析（`:97`、`:161`）、
`STATIC_ROOT`/`MEDIA_ROOT` 可覆盖（`:234`、`:242`）、`DEBUG=False` 时的生产安全设置
（`:270` 起的 `if not DEBUG:` 整块，到文件末 `:302`：`SECURE_PROXY_SSL_HEADER`（`:273`）、
`SECURE_REDIRECT_EXEMPT=[r'^health/']`（`:278`）、`SECURE_SSL_REDIRECT`（`:281`）、
HSTS（`:282-284`）、`CSRF_COOKIE_SECURE`/`SESSION_COOKIE_SECURE`（`:287-288`））。

健康端点：`ichat_pro/health.py:18-21`（`/health/live/` 只回 `{"status":"ok"}`）、`:24-41`
（`/health/ready/` 做 `SELECT 1` + cache set/get/delete 往返，失败返回 503 `{"status":"unavailable"}`，
细节只进服务端日志）。路由在 `ichat_pro/urls.py:26-27`。

---

## 2. 目录布局与可覆盖变量

### 2.1 生产默认布局（`deploy.sh:43-51`）

```text
/opt/ichat/                    # APP_DIR
├── repo/                      # 代码 checkout（systemd 的 WorkingDirectory）
├── .venv/                     # python3.13 venv
├── .env                       # 秘密（chmod 600, owner ichat）
├── bin/deploy.sh              # 稳定副本（脚本自安装并 re-exec）
├── .deploy-state/{current,previous}   # 最近成功发布的 SHA
└── DEPLOYMENTS.log            # 一行一条的发布审计
/var/lib/ichat/media/{avatars,uploads}
/etc/systemd/system/ichat.service
/etc/nginx/conf.d/ichat.conf
/var/lib/ichat/monitor/        # monitor.sh 状态（连续失败计数、限频时间戳）
/var/backups/ichat/<STAMP>/    # 备份
```

### 2.2 可覆盖变量（只列脚本真正读取的）

| 变量 | 默认 | 被谁读 | 作用 |
| --- | --- | --- | --- |
| `ICHAT_APP_DIR` | `/opt/ichat` | `deploy.sh:43` | 安装树；决定 `.env`/`repo`/venv/日志/状态目录位置 |
| `ICHAT_MEDIA_DIR` | `/var/lib/ichat/media` | `deploy.sh:44` | media 根（`install -d` 的对象） |
| `ICHAT_SERVICE_NAME` | `ichat` | `deploy.sh:261` | `systemctl restart <name>.service` |
| `ICHAT_SERVICE_USER` | `ichat` | `deploy.sh:260` | `install -d -o/-g` 的属主 |
| `ICHAT_APPROVAL` | 空 | `deploy.sh:125-132` | 审批引用；空则按 `none` 处理 |
| `ICHAT_REQUIRE_APPROVAL` | `1` | `deploy.sh:139-150` | `1/true/yes/on` 或 `0/false/no/off`；**其他值直接 exit 2** |
| `ICHAT_DEPLOYER` | 空 | `deploy.sh:93` | CI 调用时的 `actor` 覆盖 |
| `ICHAT_STATUS_LINES` | `5` | `deploy.sh:165` | `--status` 显示行数 |
| `ICHAT_SKIP_SMOKE` | `0` | `deploy.sh:314` | `1` = 跳过冒烟并记 `smoke=skipped` |
| `SMOKE_USERNAME` / `SMOKE_PASSWORD` | 无 | `deploy.sh:335`、`smoke.sh:128` | 冒烟专用账号；缺则**发布失败** |
| `SMOKE_TIMEOUT` | `10` | `smoke.sh:45` | 单请求超时（秒） |
| `SMOKE_INSECURE_TLS` | `0` | `smoke.sh:46-47` | `1` = 加 `-k` |
| `SMOKE_PYTHON` | 自动探测 | `smoke.sh:277` | JSON 解析用解释器（deploy.sh 传 venv python） |
| `BASE_URL` / `AUTO_BASE_URL` | 无 / `0` | `smoke.sh:101` | 冒烟目标；`AUTO_BASE_URL=1` 时按 8443 → 8000 回退 |
| `ICHAT_ENV_FILE` | `/opt/ichat/.env` | `monitor.sh:56` | 监控读取的 env 文件 |
| `ICHAT_MONITOR_STATE` | `/var/lib/ichat/monitor` | `monitor.sh:57` | 状态目录 |
| `ICHAT_BASE_URL` | `https://127.0.0.1:8443` | `monitor.sh:58` | readiness 探测 origin |
| `ICHAT_ALERT_WEBHOOK` | 空 | `monitor.sh:61-68` | webhook；空则只记 syslog/stderr |
| `ICHAT_DISK_PCT` / `ICHAT_PG_MAX_CONN` / `ICHAT_REDIS_MAX_MB` / `ICHAT_READY_MAX_FAILS` / `ICHAT_CERT_MIN_DAYS` / `ICHAT_ALERT_MIN_SECS` | 85 / 40 / 512 / 3 / 14 / 1800 | `monitor.sh:80-85` | 阈值与限频 |
| `ICHAT_PG_DATA_DIR` / `ICHAT_LETLS_DIR` | `/var/lib/postgresql` / `/etc/letsencrypt/live` | `monitor.sh:90-91` | 磁盘与证书检查位置 |
| `ICHAT_DB_URL` | 空 | `monitor.sh:219` | PG 连接数检查用；回退 `DATABASE_URL` |
| `RESTORE_ENV_FILE` / `ICHAT_DB_URL` / `ICHAT_MEDIA_DIR` / `ICHAT_SKIP_SERVICE` / `ICHAT_ASSUME_YES` | 见 `restore.sh:11-15` | `restore.sh` | 演练注入点 |

> **`.env` 的真实语义**：`deploy.sh` 用 `set -a; source "$APP_DIR/.env"; set +a`（`:265`）加载，
> 因此 `.env` 里**任何**变量都会进入子进程环境——包括 `manage.py` 与 `smoke.sh`。
> 这是 staging 能复用同一脚本的前提，也是 OPS-01 的成因（见 §9）。

---

## 3. 发布、回滚、审批、冒烟门禁：顺序与失败语义

### 3.1 顺序（`deploy.sh` 主流程）

```text
[0] 稳定副本 re-exec             :54-58    仓库内副本先 install 到 $APP_DIR/bin/deploy.sh 再 exec
[1] 参数解析                     :64-72    --branch / --commit / --status / --verbose
[2] --status 短路                :153-177  只读日志，不碰服务
[3] 审批门禁（在任何 git 之前）   :189-231  见 3.2
[4] .env 存在性                  :235-238  缺则 exit 1
[5] 取码                         :240-252  bootstrap clone / fetch / checkout --force / reset --hard
[6] venv + 依赖                  :254-257
[7] media 目录                   :259-262  install -d -o $SERVICE_USER
[8] source .env + 配置校验        :264-267  manage.py check + makemigrations --check --dry-run
[9] migrate + collectstatic       :269-271
[10] 重启服务                     :273-275  systemctl restart / enable
[11] readiness 门禁（30 次/30 秒） :277-307  见 3.3
[12] 冒烟门禁                     :309-349  见 3.4
[13] 审计记录                     :352-377  计算 smoke=<result>/kind，追加一行到 DEPLOYMENTS.log
[14] 状态簿记                     :379-405  .deploy-state/{current,previous}
```

### 3.2 审批门禁（T50 / #177）

- **跑在任何 git 命令之前**（`:200-204` 的注释即为理由）：`/opt/ichat/repo` 就是 systemd 运行的树，
  先 fetch 再拒绝会把生产树留在未审批的提交上。
- `ICHAT_APPROVAL` 经白名单清洗（`:79-88`：`tr -cs 'A-Za-z0-9._@#/-' '_'`，再裁首尾、限长 96），
  清洗后必须至少含一个字母数字，否则视为空（`:125-132`）。
- 拒绝条件（`:221`）：`approval == none` 且 `REQUIRE_APPROVAL == 1` 且 `IS_ROLLBACK != 1` → exit 1。
- **回滚免审批**（`:195-198`、`:209-220`）：只有 `--commit` 是**完整 40 位 hex** 才算回滚；
  传分支名/短 ref 直接 exit 1 并提示用 `--branch` 走前向发布。
- `ICHAT_REQUIRE_APPROVAL=0` 是紧急口子，仍会记录 `approval=none` 并在 stderr 警告（`:229-231`）；
  拼错的值（如 `yes please`）直接 exit 2，不会被读成"关"。
- **失败语义**：审批被拒 = **什么都没有发生**（不 fetch、不 checkout、不重启），日志不写。

### 3.3 readiness 门禁

`deploy.sh:286-307`：最多 30 轮、每轮先探 `https://127.0.0.1:8443/health/ready/`（带 `-k` 与
`Host: $DJANGO_ALLOWED_HOSTS 第一段`），body 不含 `"status": "ok"` 时再探 `http://127.0.0.1:8000/health/ready/`。
**两条路径都断言 JSON body**，因为 301/400 的 body 能骗过 `curl -f`。30 轮全失败 → exit 1，
提示 `journalctl -u $SERVICE_NAME -n 50`。

> `DEBUG=False` 时 `SECURE_REDIRECT_EXEMPT = [r'^health/']`（`settings.py:278`）是 http 回退能成立的
> **唯一**原因；它只豁免 health，所以回退不能用于冒烟（见 §3.4）。

### 3.4 冒烟门禁（T23 / #204）

`deploy.sh:309-350` 四种分支，**每种都被记录**：

| 条件 | 行为 | 写入日志的 `smoke=` |
| --- | --- | --- |
| `ICHAT_SKIP_SMOKE=1` | 打印 WARNING 后跳过 | `skipped` | `:314-315` |
| 目标树**没有** `deploy/smoke.sh` 且 `IS_ROLLBACK != 1` | **exit 1，不记录**（OPS-03 修复点，`:323-330`） | 不写 |
| 目标树没有 `deploy/smoke.sh` 且是回滚 | 打印 WARNING，放行 | `skipped-no-script` |
| 有脚本但缺 `SMOKE_USERNAME`/`SMOKE_PASSWORD` | **exit 1，不记录**（`:335-340`） | 不写 |
| 有脚本、有凭据、通过 | 继续 | `passed` |
| 有脚本、有凭据、失败 | **exit 1，不记录**（`:343-348`） | 不写 |

`smoke.sh` 的 11 项检查（全部 `required`，任一项 `fail` 即 `exit 1`）：

| # | 检查 | 期望 | 行 |
| --- | --- | --- | --- |
| 1 | `GET /health/live/` | 200 + `"status": "ok"` | `:173-178` |
| 2 | `GET /health/ready/` | 200 + `"status": "ok"` | `:180-185` |
| 3 | `POST /health/ready/` 契约 | **403 或 405**，绝不 200（否则门禁可被 POST 骗过） | `:187-198` |
| 4 | `GET /login/` | 200 | `:201-207` |
| 5 | 登录页 CSRF | 页面含 `csrfmiddlewaretoken` | `:209-218` |
| 6 | 登录前无会话 | cookie jar 里没有 `sessionid` | `:219-223` |
| 7 | `POST /login/` | **302** | `:249-263` |
| 8 | 登录后有会话 | cookie jar 里有 `sessionid` | `:264-268` |
| 9 | `GET /api/conversations/` | 200 且 JSON 顶层有 `conversations` | `:302-307` |
| 10 | `GET /api/profile/me/` | 200 且 JSON 顶层有 `profile` | `:309-314` |
| 11 | 未认证 `GET /api/conversations/` | **401**（不是 302） | `:317-322` |

两项容易被忽略的设计：

- **JSON 用解释器解析**（`:286-300`），不是 grep 键名——"200 的错误页里恰好含这个词"不能蒙混。
- **CSRF 的 Referer 重建**（`:134-153`）：`HOST_HEADER` 非空时用 `Host` 头 + `BASE_URL` 的端口
  拼出 Django 实际看到的 origin（`:146-150`），否则 `Referer` 与请求 host/scheme 不匹配会 403。

失败语义统一为：**服务已经重启（旧进程没了），但这次发布不写日志**。回退手段是
`sudo /opt/ichat/bin/deploy.sh --commit <上一个 SHA>`（提示见 `:345-348`）。

### 3.5 回滚与审计

- 回滚：`--commit <40-hex>` → `git checkout --force <sha>`（`:246-247`），**停在那个提交**，
  不会 reset 回 `origin/main`。恢复前向：再跑一次不带 `--commit` 的发布（仍需审批）。
- 审计行（`:367-377`）：

  ```text
  ts=<UTC ISO8601> sha=<40-hex> target=<branch[@commit]> kind=<deploy|rollback>
  actor=<who> via=<sudo|direct> from=<host|local-console> smoke=<passed|skipped|skipped-no-script> approval=<ref|none>
  ```

  `actor`：`ICHAT_DEPLOYER` → `SUDO_USER`（不等于 `root`）→ `$USER`/`$LOGNAME`，全部过 `sanitise`。
  `from`：`SSH_CONNECTION` 第一段，否则 `local-console`（sudo `env_reset` 常丢掉 `SSH_CONNECTION`，
  远程也可能记成 `local-console`；要准确来源用 `sudo -E`）。
- **"在跑什么"的权威答案**：`DEPLOYMENTS.log` 最后一条。`git -C repo rev-parse HEAD` 只是参考
  （手工 checkout 会改它而不产生日志）。
- `.deploy-state/current` 与 `previous`（`:379-405`）：写失败只 WARNING，不把成功的发布判成失败；
  `previous` 取自**状态文件的旧值**，不是 git reflog。

---

## 4. 监控检查项与阈值（`monitor.sh`）

cron（root）：`*/5 * * * * /opt/ichat/repo/deploy/monitor.sh >> /var/log/ichat-monitor.log 2>&1`

| # | 检查 | 触发 | key | 行 |
| --- | --- | --- | --- | --- |
| 1 | readiness | `/health/ready/` **连续** ≥ `ICHAT_READY_MAX_FAILS`（默认 3）次失败；单次只计数 | `ready` | `:167-192` |
| 2 | 磁盘 | PG 数据盘（`ICHAT_PG_DATA_DIR`，不存在则 `/var`）使用率 ≥ `ICHAT_DISK_PCT`（85） | `disk` | `:195-213` |
| 3 | PG 连接数 | `SELECT count(*) FROM pg_stat_activity` ≥ `ICHAT_PG_MAX_CONN`（40） | `pg-conn` | `:252-272` |
| 4 | Redis 内存 | `used_memory` ≥ `ICHAT_REDIS_MAX_MB`（512 MB） | `redis-mem` | `:275-292` |
| 5 | 证书 | `ICHAT_LETLS_DIR` 下**每一条** lineage 的 `cert.pem` 剩余 < `ICHAT_CERT_MIN_DAYS`（14）天 | `cert` | `:298-324` |
| 6 | journal | `journalctl -u ichat --since "-10 min" -p err` 行数（减 2 行 banner）≥ 10 | `journal` | `:327-341` |
**"查不到"≠"一切正常"**（这是本脚本最重要的设计）：`check_failed` 把检查名收进 `CHECK_FAILURES`
（`:105-107`），任何一项跑不成都会在结尾触发 `monitor` 告警并 **exit 1**（`:356-362`）。
具体触发点：`df` 缺失/读不到（`:198-207`）、`psql` 缺失（`:252-256`）、`DATABASE_URL` 不可用
（`:257-260`）、查询失败（`:262-266`）、`redis-cli` 缺失（`:275-279`）、`journalctl` 缺失（`:327-331`）、
证书目录为空（`:318-323`）、状态目录不可写导致"连续失败"无法累计（`:180-185`）。

**告警与限频**（`:134-164`）：

- 同一 key 两次告警间隔 ≥ `ICHAT_ALERT_MIN_SECS`（1800 秒）；限频时间戳**只在 webhook 投递成功后**
  写入（`:152`），投递失败下一轮会重试（`:153-157`）；无 webhook（日志模式）也写时间戳（`:161`），
  避免每 5 分钟刷屏。
- 告警同时进 stderr 与 `logger -t ichat-monitor`（`:146-147`），webhook 用 `{"text": "[ichat <key>] ..."}`
  JSON（`:150`），`json_escape` 把换行/回车替换成空格（`:125-132`），防止伪造多行。
- 自检：`ICHAT_ALERT_TEST=1 /opt/ichat/repo/deploy/monitor.sh`（`:344-347`），只发一条测试告警后 exit 0。
- **不做**的事：不重启任何服务、不碰 443/2096/25383、不读聊天数据、不把 DB 密码放进 `psql` argv。

> **`monitor.sh` / `check_isolation.sh` 在 `2905473` 里是 `100644`**（`git ls-tree` 实测），
> 而 runbook 直接写 `/opt/ichat/repo/deploy/monitor.sh` 当命令。**必须**按 §8 的命令用 `bash` 调用，
> 或先 `chmod 755`。这是未修的 OPS-04。

---

## 5. staging 隔离检查（`check_isolation.sh`）

```bash
sudo bash deploy/staging/check_isolation.sh \
    --prod-env /opt/ichat/.env --staging-env /opt/ichat-staging/.env [--check-data]
```

退出码：`0` 全隔离；`1` 有 violation（逐条打印 `[FAIL]`）；`2` 参数错或文件不可读。

### 5.1 检查维度（全部在 `:87-187`）

| 段 | 断言 | 不满足时 |
| --- | --- | --- |
| 秘密 | `DJANGO_SECRET_KEY`、`DATABASE_URL` 两侧都非空且**不相等**（`:89-95`） | violation；**任一侧为空也算 violation**（`:72-78`） |
| 配置 | staging 库名（从 URL path 取）必须是 `ichat_staging`（`:106-111`） | violation（只打印库名/URL 的 SHA 前缀） |
| 配置 | `REDIS_URL` 逻辑库编号必须不同（`:114-125`） | violation；缺一侧也 violation |
| 配置 | `DJANGO_CSRF_TRUSTED_ORIGINS` 必须不同（`:127-129`） | violation |
| 存储 | `DJANGO_MEDIA_ROOT`、`DJANGO_STATIC_ROOT` 原值必须不同（`:133-135`） | violation |
| 存储 | media root **互不嵌套**（先 `normalise_path` 处理 `/`、`..`、重复分隔符，`:48-65`，再做前缀比较，`:136-150`） | violation |
| 运行时 | `ICHAT_APP_DIR` 不能规范化成 `/opt/ichat`（`:154-157`） | violation |
| 运行时 | `ICHAT_SERVICE_NAME` / `ICHAT_SERVICE_USER` 不能是 `ichat`（`:158-163`） | violation |
| 运行时 | staging unit 里 Daphne 端口不能是 8000；**unit 找不到时报 `port UNVERIFIED` 并 violation**（`:165-187`） | violation |
| 活数据（可选） | `--check-data` 时比较库名与 Redis 逻辑库（`:189-207`） | violation |

> **`--check-data` 的真实行为（未修的 OPS-08）**：它只做 `command -v psql` / `command -v redis-cli`
> **存在性判断**后再比一遍 URL 字符串尾部（`:192-206`），**从不真正连接**数据库或 Redis，
> 所以"活库已隔离"不能由它得出。工具缺失时它**不**登记 violation（静默跳过，`:192`、`:201`）。
> 真正的隔离证据只能靠两侧数据库角色/权限的实际核查（未做，见 §10）。
> 静态 URL 差异（§5.1 前四段）是有效断言，`--check-data` 的"活数据"结论不是。

### 5.2 秘密边界

脚本**从不打印秘密值**：比较只用等值判断，需要标识时打印 `sha256sum | cut -c1-16`（`:68`、`:75`）。
数据库 URL 也可能带密码，所以库名是从 URL 里**解析**出来后单独打印的（`:105-111`）。
注意 `:75` 打印的是**生产值**的哈希，`:108` 打印的是 staging 库名的哈希——都不是明文。

---

## 6. 备份与恢复

| 动作 | 命令 | 要点 |
| --- | --- | --- |
| 每日备份 | `30 17 * * * /opt/ichat/repo/deploy/backup.sh`（UTC 17:30 = 东京 02:30） | `pg_dump --no-owner --no-privileges` + `tar -C $MEDIA_DIR` 的**内容**（不带父目录前缀）+ 相对路径 `SHA256SUMS` + `META`（commit/时间）；目录 700；`find -mtime +14 -exec rm -rf` |
| 真实恢复 | `sudo bash deploy/restore.sh /var/backups/ichat/<STAMP>` | 先 `sha256sum -c`；再要求**手输库名**确认（`ICHAT_ASSUME_YES=1` 可跳过）；停 `ichat.service`；`pg_terminate_backend` + `DROP SCHEMA public CASCADE; CREATE SCHEMA public;`；`psql --single-transaction` 回放；旧 media 改名为 `.pre-restore-<UTC>`；起服后探针 30 次 |
| 恢复演练 | `sudo bash deploy/restore_drill.sh` | seed 数据 → 真实 backup → **调用真实 `restore.sh`**（注入 `RESTORE_ENV_FILE`/`ICHAT_MEDIA_DIR`/`ICHAT_SKIP_SERVICE=1`/`ICHAT_ASSUME_YES=1`）→ 校验行数 parity、关系完整性、密文逐字节、文件摘要 |

**恢复不自动降级数据库**：`restore.sh` 恢复的是备份里的 schema 与数据，应用回滚（`deploy.sh --commit`）
不会倒推迁移。不可逆迁移只能靠备份回退。

> **注意（三个脚本不接受 `ICHAT_APP_DIR`）**：`backup.sh:6-7`（`APP_DIR=/opt/ichat`、
> `MEDIA_DIR=/var/lib/ichat/media`）、`restore.sh:18`（`APP_DIR=/opt/ichat`）、
> `restore_drill.sh:15`（`APP_DIR=/opt/ichat`，`:25` 的 `VENV` 也被写死）把路径**写死**；
> 只有 `restore.sh` 的三个 drill 变量（`RESTORE_ENV_FILE`/`ICHAT_MEDIA_DIR`/`ICHAT_DB_URL`）能改目标，
> 而 `restore_drill.sh` 自己就是**对着生产 `.env` 与生产库**做 seed 的（`:24`、`:28-31`）。
> 所以 §2.2 的可覆盖变量**只对 `deploy.sh` 成立**：备份/恢复不随 staging 参数化，
> 给 staging 做备份必须另写入口（`backup.sh` 只接受一个 `backup_dir` 位置参数，media 根仍取生产路径）——
> 这是尚未处理的缺口，**不要在 staging 主机上直接跑 `restore_drill.sh`**。

---

## 7. 秘密边界（不进 argv / 不进日志）

| 秘密 | 怎么传 | 证据 |
| --- | --- | --- |
| 冒烟密码 | 写进 0600 临时文件，`curl --data-binary @file` | `smoke.sh:226-256`（`chmod 600` 在 `:247`、`:75`） |
| DB 密码（监控） | 解析成 `PGPASSWORD` 等 libpq **环境变量**，`psql` 不收 URL 参数 | `monitor.sh:248`、`:262` |
| DB URL（恢复） | `psql "$DB_URL"` —— **会进 argv**（`restore.sh:42-44`），属已知边界 | 见 §10 |
| `DJANGO_SECRET_KEY` 等 | 只在 `.env`（600），脚本只 `source`，不 echo | `deploy.sh:265` |
| 审计日志 | 所有字段过 `sanitise` 白名单，换行无法伪造第二条记录 | `deploy.sh:79-88` |
| 告警消息 | `json_escape` 去控制字符；不含密码/cookie/载荷 | `monitor.sh:125-132` |
| 隔离报告 | 秘密只以 SHA-256 前缀出现 | `check_isolation.sh:68`、`:75` |

**监控状态目录与日志**里没有任何秘密；`DEPLOYMENTS.log` 只有 `sha/target/kind/actor/via/from/smoke/approval`。

> 已知缺口：`monitor.sh:236-244` 用 `python -c '...' "$url"` 把**完整 DATABASE_URL（含密码）** 作为
> argv 传给解析器——违反本表第 2 行的原则（未修的 OPS-07）。`restore.sh` 的 `psql "$DB_URL"`
> 同理。两者都只在 root 进程内可见，但 `ps -ef` 可读。

---

## 8. 常用命令速查

```bash
# —— 发布 ——
sudo ICHAT_APPROVAL='#177' /opt/ichat/bin/deploy.sh          # 发布 origin/main
sudo ICHAT_APPROVAL='#177' /opt/ichat/bin/deploy.sh --branch main
sudo ICHAT_REQUIRE_APPROVAL=0 /opt/ichat/bin/deploy.sh       # 紧急口子（记 approval=none）

# —— 查询与回滚 ——
sudo /opt/ichat/bin/deploy.sh --status
sudo /opt/ichat/bin/deploy.sh --status --verbose
sudo /opt/ichat/bin/deploy.sh --commit <40-hex-sha>          # 回滚，免审批，kind=rollback

# —— 冒烟（单独复跑）——
sudo -u ichat env BASE_URL=https://chat.example.com:8443 \
    SMOKE_USERNAME=deploy_smoke SMOKE_PASSWORD=... \
    bash /opt/ichat/repo/deploy/smoke.sh

# —— 监控 ——
sudo bash /opt/ichat/repo/deploy/monitor.sh                  # 注意 bash（脚本是 100644）
sudo ICHAT_ALERT_TEST=1 bash /opt/ichat/repo/deploy/monitor.sh

# —— staging 隔离 ——
sudo bash /opt/ichat/repo/deploy/staging/check_isolation.sh \
    --prod-env /opt/ichat/.env --staging-env /opt/ichat-staging/.env

# —— 备份 / 恢复 / 演练 ——
sudo bash /opt/ichat/repo/deploy/backup.sh
sudo bash /opt/ichat/repo/deploy/restore.sh /var/backups/ichat/<STAMP>
sudo bash /opt/ichat/repo/deploy/restore_drill.sh

# —— 运维测试（仓库根目录，必须显式点名五个模块）——
.\.venv\Scripts\python.exe manage.py test \
  deploy.tests.test_deploy_gate deploy.tests.test_isolation_check deploy.tests.test_monitor \
  deploy.tests.test_release_audit deploy.tests.test_smoke_gate --parallel 1 -v 2
```

`deploy/tests` 的条数（按 `2905473` 的 `def test_` 计）：`test_deploy_gate` **9**、
`test_isolation_check` 18、`test_monitor` 18、`test_release_audit` 27、`test_smoke_gate` 13，**合计 85**。
相对 2026-10-24 审查时的 82 条，多出来的 3 条是 OPS-03 的回归（`test_forward_release_without_smoke_sh_fails_closed`、
`test_rollback_with_a_smoke_script_still_runs_it`、`test_rollback_with_a_failing_smoke_script_is_not_recorded`）——
所以"82 条"是**历史数字**，不要在新证据里继续引用。

**必须显式点名五个模块**：`2905473` 之前 `deploy/` 是 PEP 420 namespace package，默认 `manage.py test`
不会递归进去（历史实测：收集 404 条、0 条 ops 测试）。`f999d3f` 加了 `deploy/__init__.py` 修这个问题，
但**该提交之后的 CI 是否真的收到这 85 条，本次未用 CI 运行验证**（§10）。

---

## 9. 已知问题与未修的审查发现

`2905473` 只带了两项修复。以下来自 `.review/phase4-full-review-20261024/cloud-ops-review.md`，
**在 `2905473` 上仍然成立**（行号对应该提交）：

| 编号 | 级别 | 现象 | 位置 |
| --- | --- | --- | --- |
| OPS-01 | MAJOR | `MEDIA_DIR`（`:44`）、`SERVICE_USER`/`SERVICE_NAME`（`:260-261`）都在 `source .env`（`:265`）**之前**定值，所以只在 `.env` 里声明 `ICHAT_SERVICE_NAME=ichat-staging` / `ICHAT_MEDIA_DIR=...` 的 staging 安装**既不会**切换 systemd 单元（仍 `systemctl restart ichat.service`，`:274`）**也不会**改 media 根（仍 `install -d` 到 `/var/lib/ichat/media`，`:262`）——即**重启生产单元、写生产 media** | `:44`、`:260-265`、`:274` |
| OPS-02 | MAJOR | readiness 与冒烟把 `8443`/`8000` 写死（`:291`、`:295`；`smoke.sh:105`、`:113`），staging 的 8444/8001 不会被使用 | 同左 |
| OPS-04 | MAJOR | `monitor.sh`、`staging/check_isolation.sh` 提交为 `100644`，runbook 却直接以路径执行 | `git ls-tree 2905473 deploy/` |
| OPS-05 | MINOR | `monitor.sh` 只从 `.env` 载入 4 个键（`:62-68`），写在 `.env` 的 `ICHAT_DISK_PCT` 等阈值**不生效**——它们由 `:80-85` 从**进程环境**读，cron 不会提供 | `:61-69`、`:80-85` |
| OPS-06 | MAJOR | `journalctl ... 2>/dev/null \| grep -c . \|\| true` 把查询失败吞成 0 错误（`:333`，仅在 `journalctl` **不存在**时才 `check_failed`） | `:327-341` |
| OPS-07 | MAJOR | 完整 DATABASE_URL（含密码）作为 argv 传给辅助 python（`:236-244` 的 `"$url"`） | `:236-244` |
| OPS-08 | MAJOR | `--check-data` 不连接任何数据库/Redis，工具缺失也不登记 violation | `check_isolation.sh:189-207` |
| OPS-09 | MAJOR | staging 只要改库名就能带着**相同的数据库用户名/密码**通过隔离检查（`same_or_diff` 只比整条 URL） | `check_isolation.sh:93-95` |
| OPS-10 | MAJOR | static root 只比原值不比嵌套；`collectstatic --clear` 下嵌套的 static 会互相删除 | `check_isolation.sh:133-135`、`deploy.sh:271` |
| C11/QA-01 | MAJOR | 运维测试默认不被 `manage.py test` 收集 | 已由 `f999d3f` 部分修复 |
| **DOC-NEW-1**（本次写文档时发现，**不在**原审查清单里） | MAJOR | `backup.sh` / `restore.sh` / `restore_drill.sh` 把 `APP_DIR`/`MEDIA_DIR`/`VENV` 写死，且 `restore_drill.sh:24` 直接 source 生产 `.env`、`:28-31` 往**生产库** seed | `backup.sh:6-7`、`restore.sh:18`、`restore_drill.sh:15`、`:24-26` |

> **DOC-NEW-1 的影响**：`deploy.sh` 已参数化到可以发布 staging，但备份/恢复链路完全没有。
> 在 staging 主机上跑 `restore_drill.sh` 会 seed 生产库；这比 OPS-01/02 更直接。
> 建议在 #176 里与本轮其他 staging 修复一起处理（先修，再搭实机）。

**未修就不要按 runbook 直接上线 staging**：OPS-01 + OPS-02 的组合会让 staging 发布**重启生产服务
并探测生产端口**，而脚本仍可能 exit 0。这是本文档反复标注"staging 实机未搭建"的原因。

### 9.1 坑 6：`media/` 不能在 nginx 整体公开（回链 `conventions.md:340`）

`conventions.md` §6 指向的是"本文 §8 坑 6"，那个编号来自旧目录结构；对应内容在这里：

- 只允许 `media/avatars/` 公开——`deploy/nginx/ichat.conf:58-62` 用 `location /media/avatars/ { alias __MEDIA_AVATARS__/; }`。
- `media/uploads/` **绝不能**公开：`deploy/nginx/ichat.conf:64-66` 显式 `location = /media/uploads/ { return 403; }`，
  下载必须走认证的 `/api/files/`。
- **不要**新增 `location /media/` 或 `alias` 指向整个 media 根。
- **不要**为了调试打开 `DJANGO_DEBUG`：生产安全依赖 `DEBUG=False` 时 Django 根本不注册 media 路由——
  `ichat_pro/urls.py:32-33`：`if settings.DEBUG:` 才 `urlpatterns += static(settings.MEDIA_URL, ...)`。
  `DEBUG=True` 会同时让 `SECURE_*`/`*_COOKIE_SECURE`/`SECURE_SSL_REDIRECT` 整段失效（`settings.py:270`）。
- 静态文件走 `location /static/ { alias __STATIC_ROOT__/; }`（`ichat.conf:50-54`），
  其根必须与 `DJANGO_STATIC_ROOT` 一致（`deploy.sh:271` 的 `collectstatic --clear` 会清空该目录）。

---

## 10. 未验证范围（明确声明）

| 项 | 状态 |
| --- | --- |
| 真实 AWS 主机 / 生产部署 | **未执行**。本文没有任何一次真实发布、回滚或恢复。 |
| staging 实机 | **未搭建**。隔离检查只在合成 env 文件与假工具下跑过。 |
| 真实 PostgreSQL / Redis | 本机没有；85 条运维测试用桩与 SQLite，**不是** PG/Redis 集成证据。 |
| Linux cron / systemd / nginx | 未运行。`bash -n` 语法检查通过（4 个脚本），仅此而已。 |
| `--check-data` 的"活数据隔离" | 由 OPS-08 决定：**该结论不成立**，只有静态 URL 差异是有效断言。 |
| 发布审批的 CI 侧 | `ICHAT_DEPLOYER` 路径未在 CI 中实跑。 |
| 85 条测试是否进 CI | 未验证（见 §8 末）。 |
| 冒烟失败的真实阻断 | 只有单元级证据（`deploy.tests.test_smoke_gate` 等），**没有**一次真实"登录坏了但 readiness 正常"的发布。 |
| 服务器 SHA / 安装包 SHA | 未复核。`deploy/runbook.md:16` 记的 `f2f60ec` / 2026-10-07T13:42:16Z 是**历史陈述**，本次未连服务器验证。 |

---

## 11. 这个模块的开放任务

| Issue | 卡片 | 建议起点 |
| --- | --- | --- |
| #130 | P4 T08 备份恢复流程 | 跨实例（非同一主机）恢复演练；`restore.sh` 的 `psql "$DB_URL"` argv 暴露 |
| #139 | P4 T12 监控告警 | OPS-05/06/07：把阈值从 `.env` 真正载入、区分"查不到"与"没问题"、密码走 stdin/env |
| #176 | P4 T49 独立测试环境 | **先修 OPS-01/02/04/08/09/10**，再搭实机；否则 staging 会打生产 |
| #177 | P4 T50 发布审批与回滚 | 真实发布演练 + `DEPLOYMENTS.log` 的 CI 侧写入 |
| #204 | P4 Test T23 部署冒烟门禁 | 在真实 host 上制造一次登录故障，确认发布被阻断且日志无新行 |

---

## 12. 给下一个 Agent 的提示词

```text
你在 iChat Pro 仓库工作。你的模块是「云部署与运维」：deploy/**、ichat_pro/health.py、
ichat_pro/settings.py 的数据库/Redis/静态/媒体/生产安全段、nginx 与 systemd 模板、备份恢复。

先读（按顺序）：
1. docs/handoff/conventions.md（全文，硬性规则）
2. docs/handoff/cloud-deploy-ops.md（本文，尤其 §9 未修发现与 §10 未验证范围）
3. deploy/runbook.md（人写的手册，注意它可能比代码旧）
4. deploy/deploy.sh 全文 406 行——它是唯一发布入口，审批/readiness/冒烟三道门禁都在里面

铁律：
- 不改 static/js/**、accounts/**、.github/**、mobile/**（不是你的边界）。
- 任何"发布成功"的断言必须有 DEPLOYMENTS.log 的一行或部署测试的一条作为证据。
- 绝不在真实生产主机上试 staging 相关改动：OPS-01/02 未修时，staging 发布会重启生产单元。
- 秘密不进 argv/日志/Issue；DB 密码用 env 或 stdin。
- 声称"已隔离"之前，先说明 --check-data 并不连接数据库（OPS-08）。

交付：分支 fix/<卡号>-<短名>、一 finding 一 commit、`manage.py check` +
`makemigrations --check --dry-run` + 五个 deploy.tests 模块全绿，并把证据写进 Issue。
提交前另开一个独立审查子 Agent（见 conventions.md §1）。
```
