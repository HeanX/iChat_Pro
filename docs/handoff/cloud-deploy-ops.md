# 模块 5：云部署与运维（`deploy/` + health + nginx/systemd + 备份恢复）

> 交接对象：负责服务器部署、发布审批/回滚、监控告警、staging 隔离、备份恢复的 Agent 或运维。
> **pin：`99c9d686120f717f2457600e02e28eb89ade230f`**（`iChat_Pro.wt-t176`，分支 `feat/p4-t49-test-environment`，2026-10-26 冻结）。
> 本文所有行号**以该 sha 为准**，格式统一为 `path:line`。相关规范见 [conventions.md](conventions.md)。

---

## 0. 这份文档的依据与边界（先读）

本文所有关于脚本行为的断言都来自**实际阅读该提交里的文件**，不是 runbook 转述。核对方式：
用 `git show 99c9d68:<path>` 把 `deploy/**` 全部 29 个文件导出到只读快照，逐文件用
`git hash-object` 与该提交的 blob 比对——**29/29 一致**，所以每个行号都可复现。

| 项 | 值 |
| --- | --- |
| pin | `99c9d686120f717f2457600e02e28eb89ade230f`（`test(deploy): follow the resolved restart target in the approval-order check (OPS-01)`） |
| 基线 | `6f702d9`，本分支共 11 个提交（`f999d3f` … `99c9d68`） |
| `deploy/deploy.sh` | `a0386c403ab8780f1acc3c65b3e6c47190aabafa`，23977 B，**526 行**，`100755` |
| `deploy/smoke.sh` | `f96611dfaf8b32c65dd6a737dbe4320753b16ff6`，14584 B，**343 行**，`100755` |
| `deploy/monitor.sh` | `d730b92ebb9b4fd3b6b16b9a4789bd06f23560b0`，18477 B，**441 行**，`100755` |
| `deploy/staging/check_isolation.sh` | `3d59cedee42424440866d983a78221dab8052465`，15847 B，**351 行**，`100755` |
| `deploy/runbook.md` | `4fa16e3513a437bf9f0c4e03282316cd9054c5d7`，21717 B，**210 行** |

**本提交相对 2026-10-24 审查结论的变化**（原始发现见 `.review/phase4-full-review-20261024/cloud-ops-review.md`）：
OPS-01/02 由 `8308dd5` + `d6e2dcd` 关闭，OPS-03 由 `2905473` 关闭，OPS-04 由 `05bb0c6` 关闭，
OPS-05/06/07 由 `753cd9d` 关闭，OPS-08/09/10 由 `82641c2` 关闭，runbook 同步在 `f5ca7be` / `347e6c6`，
默认发现由 `f999d3f` + `6542cdf` 关闭。
**本文上一版（pin `2905473`）把这些写成"仍然成立"，那个结论已经作废**；以 §9 为准。

> **仍然有效的边界**：以上都是**代码与测试层面**的修复。没有任何一次真实发布、回滚、恢复或
> staging 实机运行被本文验证过——见 §10。

---

## 1. 这块现在是什么

| 文件 | 行数 | 权限 | 职责 |
| --- | ---: | --- | --- |
| `deploy/deploy.sh` | 526 | 100755 | 唯一发布入口：审批 → 解析安装身份 → 取码 → venv → 迁移/静态 → 重启 → readiness → 冒烟 → 审计 |
| `deploy/smoke.sh` | 343 | 100755 | 部署后冒烟门禁：health/readiness/POST 契约/登录/CSRF/会话/核心 API/未认证 401 |
| `deploy/monitor.sh` | 441 | 100755 | cron 监控：readiness 连续失败、PG 磁盘与连接数、Redis 内存、证书余期、journal 错误 |
| `deploy/staging/check_isolation.sh` | 351 | 100755 | staging 隔离断言：秘密（角色/密码）、配置、存储、运行时、`--check-data` 活库 |
| `deploy/staging/env.staging.example` | 52 | 100644 | staging `.env` 模板（含 `ICHAT_TLS_PORT`/`ICHAT_HTTP_PORT`） |
| `deploy/staging/ichat-staging.service` | 28 | 100644 | staging 单元（`ichat-staging` 用户，Daphne 8001） |
| `deploy/systemd/ichat.service` | 23 | 100644 | 生产单元（`ichat` 用户，Daphne 8000） |
| `deploy/env.production.example` | 22 | 100644 | 生产 `.env` 模板 |
| `deploy/nginx/ichat.conf` | 81 | 100644 | Nginx：80（ACME + 301）/ 8443（TLS、static、avatars、proxy 到 8000） |
| `deploy/nginx/reload-nginx.sh` | 10 | 100644 | certbot deploy-hook：`nginx -t` 后 reload |
| `deploy/backup.sh` | 45 | 100755 | `pg_dump` + media 打包 + SHA256 + META，保留 14 天 |
| `deploy/restore.sh` | 74 | 100755 | 真实恢复：校验和 → 停服 → drop schema → 回放 → media 换目录 → 起服并探针 |
| `deploy/restore_drill.sh` | 119 | 100755 | 恢复演练：seed → backup → 真实 `restore.sh`（注入独立库/目录）→ 逐项校验 |
| `deploy/seed_drill_data.py` | 160 | 100644 | 演练用确定性数据（用户/关系/私聊/群聊/加密文件+逐持有者密钥） |
| `deploy/tests/` | 139 条 / 8 模块 | 100644 | 上述脚本的 Python 回归（§8） |

`ichat_pro/settings.py` 相关三段：`DATABASE_URL`/`REDIS_URL` 解析（`ichat_pro/settings.py:97`、`:161`）、
`STATIC_ROOT`/`MEDIA_ROOT` 可覆盖（`:234`、`:242`）、`DEBUG=False` 的生产安全段
（`:270` 起 `if not DEBUG:` 到文件末 `:302`：`SECURE_PROXY_SSL_HEADER`（`:273`）、
`SECURE_REDIRECT_EXEMPT=[r'^health/']`（`:278`）、`SECURE_SSL_REDIRECT`（`:281`）、HSTS（`:282-284`）、
`CSRF_COOKIE_SECURE`/`SESSION_COOKIE_SECURE`（`:287-288`））。

健康端点：`ichat_pro/health.py:18-21`（`/health/live/` 只回 `{"status":"ok"}`）、
`ichat_pro/health.py:24-41`（`/health/ready/` 做 `SELECT 1` + cache set/get/delete 往返，失败 503
`{"status":"unavailable"}`，细节只进服务端日志）。路由在 `ichat_pro/urls.py:26-27`。

---

## 2. 目录布局与可覆盖变量

### 2.1 生产默认布局（`deploy/deploy.sh:43-51`）

```text
/opt/ichat/                    # APP_DIR
├── repo/                      # 代码 checkout（systemd 的 WorkingDirectory）
├── .venv/                     # python3.13 venv
├── .env                       # 秘密（chmod 600, owner ichat）
├── bin/deploy.sh              # 稳定副本（脚本自安装并 re-exec）
├── .deploy-state/{current,previous}
└── DEPLOYMENTS.log
/var/lib/ichat/media/{avatars,uploads}
/etc/systemd/system/ichat.service
/etc/nginx/conf.d/ichat.conf
/var/lib/ichat/monitor/        # monitor.sh 状态（连续失败计数、限频时间戳）
/var/backups/ichat/<STAMP>/
```

### 2.2 可覆盖变量（只列脚本真正读取的）

`deploy.sh` 的优先级**自高到低**为：**调用方进程环境 > `$APP_DIR/.env` > 生产缺省**
（`deploy/deploy.sh:247-269`：先把调用方的 5 个值快照到 `_CALLER_*`，再 `source .env`，最后把快照写回）。
`ICHAT_APP_DIR` **故意不从文件里回读**（`deploy/deploy.sh:252-253`）——调用方选安装树，这个选择决定读哪个 `.env`。

| 变量 | 缺省 | 读取位置 | 作用 |
| --- | --- | --- | --- |
| `ICHAT_APP_DIR` | `/opt/ichat` | `deploy/deploy.sh:43` | 安装树；决定 `.env`/repo/venv/日志/状态目录 |
| `ICHAT_MEDIA_DIR` | `/var/lib/ichat/media` | `deploy/deploy.sh:44`、`:275` | media 根 |
| `ICHAT_SERVICE_NAME` | `ichat` | `deploy/deploy.sh:276`、`:278` | `systemctl restart/enable <name>.service` |
| `ICHAT_SERVICE_USER` | `ichat` | `deploy/deploy.sh:277`、`:376` | media 属主 |
| `ICHAT_TLS_PORT` | `8443` | `deploy/deploy.sh:292` | readiness 主探测端口（经 Nginx） |
| `ICHAT_HTTP_PORT` | `8000` | `deploy/deploy.sh:293` | readiness 回落端口（Daphne loopback） |
| `ICHAT_READY_ORIGIN` | `https://127.0.0.1:$TLS_PORT` | `deploy/deploy.sh:298` | 显式覆盖主探测 origin |
| `ICHAT_LOOPBACK_ORIGIN` | `http://127.0.0.1:$HTTP_PORT` | `deploy/deploy.sh:299` | 显式覆盖回落 origin |
| `ICHAT_PROD_APP_DIR` | `/opt/ichat` | `deploy/deploy.sh:316` | 声明"哪棵树才是生产"，供非生产树的安全校验使用 |
| `ICHAT_APPROVAL` | 空 | `deploy/deploy.sh:125-132` | 审批引用；空即 `none` |
| `ICHAT_REQUIRE_APPROVAL` | `1` | `deploy/deploy.sh:139-150` | `1/true/yes/on` 或 `0/false/no/off`；其他值 **exit 2** |
| `ICHAT_DEPLOYER` | 空 | `deploy/deploy.sh:93` | CI 调用时的 `actor` |
| `ICHAT_STATUS_LINES` | `5` | `deploy/deploy.sh:165`、`:167` | `--status` 行数 |
| `ICHAT_SKIP_SMOKE` | `0` | `deploy/deploy.sh:433` | `1` = 跳过冒烟，记 `smoke=skipped` |
| `SMOKE_USERNAME` / `SMOKE_PASSWORD` | 无 | `deploy/deploy.sh:454`、`deploy/smoke.sh:141` | 冒烟专用账号；缺则**发布失败** |
| `SMOKE_TIMEOUT` | `10` | `deploy/smoke.sh:52` | 单请求超时（秒） |
| `SMOKE_INSECURE_TLS` | `0` | `deploy/smoke.sh:54` | `1` = 加 `-k` |
| `SMOKE_TLS_ORIGIN` | `https://127.0.0.1:8443` | `deploy/smoke.sh:111` | 冒烟首试 origin（`deploy.sh` 传解析值，`deploy/deploy.sh:463`） |
| `SMOKE_LOOPBACK_ORIGIN` | `http://127.0.0.1:8000` | `deploy/smoke.sh:112` | 冒烟回落 origin（同上） |
| `SMOKE_PYTHON` | 自动探测 | `deploy/smoke.sh:290` | JSON 解析解释器（`deploy.sh` 传 venv python，`deploy/deploy.sh:462`） |
| `BASE_URL` / `AUTO_BASE_URL` | 无 / `0` | `deploy/smoke.sh:114-118` | 冒烟目标；`AUTO_BASE_URL=1` 时按 TLS → loopback 回退 |
| `ICHAT_ENV_FILE` | `/opt/ichat/.env` | `deploy/monitor.sh:56` | 监控读取的 env 文件 |
| `ICHAT_MONITOR_STATE` | `/var/lib/ichat/monitor` | `deploy/monitor.sh:57` | 状态目录 |
| `ICHAT_BASE_URL` | `https://127.0.0.1:8443` | `deploy/monitor.sh:125` | readiness 探测 origin（**读完 `.env` 之后**才求值） |
| `ICHAT_ALERT_WEBHOOK` | 空 | `deploy/monitor.sh:75`（白名单） | webhook；空则只记 syslog/stderr |
| `ICHAT_ALERT_MIN_SECS` / `ICHAT_DISK_PCT` / `ICHAT_PG_MAX_CONN` / `ICHAT_REDIS_MAX_MB` / `ICHAT_READY_MAX_FAILS` / `ICHAT_CERT_MIN_DAYS` | 1800 / 85 / 40 / 512 / 3 / 14 | `deploy/monitor.sh:135-140` | 限频与阈值 |
| `ICHAT_PG_DATA_DIR` / `ICHAT_LETLS_DIR` | `/var/lib/postgresql` / `/etc/letsencrypt/live` | `deploy/monitor.sh:147-148` | 磁盘与证书检查位置 |
| `ICHAT_SERVICE_NAME`（监控侧） | `ichat` | `deploy/monitor.sh:149` | journal 检查的单元名（staging 因此看 `ichat-staging`） |
| `ICHAT_DB_URL` | 空 | `deploy/monitor.sh:282` | PG 连接数检查用；回退 `DATABASE_URL` |
| `ICHAT_PYTHON` | `/opt/ichat/.venv/bin/python` | `deploy/monitor.sh:286` | URL 解析解释器 |
| `RESTORE_ENV_FILE` / `ICHAT_DB_URL` / `ICHAT_MEDIA_DIR` / `ICHAT_SKIP_SERVICE` / `ICHAT_ASSUME_YES` | 见 `deploy/restore.sh:9-15` | `deploy/restore.sh` | 演练注入点（**仅 restore.sh 接受**，见 §6 的 DOC-NEW-1） |

> **`.env` 的真实语义（`deploy.sh` 侧）**：`set -a; source "$APP_DIR/.env"; set +a`（`deploy/deploy.sh:263`），
> 因此 `.env` 里**任何**变量都会进入子进程环境，包括 `manage.py` 与 `deploy/smoke.sh`。
> 这是 staging 能复用同一脚本的前提。
>
> **`.env` 的真实语义（`monitor.sh` 侧，与上面不同）**：**不 source**，而是按 `ENV_FILE_KEYS` 白名单
> 逐行解析（清单 `deploy/monitor.sh:74-88`、解析 `:93-120`、调用 `:121`）。只有清单里的名字生效，
> 且**调用方环境优先于文件**（`:113`）；清单外的行一律忽略。所以"写在 `.env` 里"对 monitor 生效的前提是
> 名字在白名单里；`deploy/tests/test_runbook_contract.py` 双向校验"文档写了 ↔ 白名单有"（9 条）。

---

## 3. 发布、回滚、审批、冒烟门禁：顺序与失败语义

### 3.1 顺序（`deploy/deploy.sh` 主流程）

```text
[0]  稳定副本 re-exec              deploy/deploy.sh:54-58
[1]  参数解析                      deploy/deploy.sh:64-72    --branch/--commit/--status/--verbose
[2]  --status 短路                 deploy/deploy.sh:153-177  只读日志，不碰服务
[3]  审批门禁（在任何 git 之前）    deploy/deploy.sh:189-231  见 3.2
[4]  .env 存在性                   deploy/deploy.sh:235-238
[5]  解析安装身份 + 4 道安全校验    deploy/deploy.sh:240-351  见 3.2b
[6]  取码                          deploy/deploy.sh:353-365  clone/fetch/checkout --force
[7]  venv + 依赖                   deploy/deploy.sh:367-370
[8]  media 目录                    deploy/deploy.sh:372-376
[9]  manage.py check + 迁移检查    deploy/deploy.sh:378-382
[10] migrate + collectstatic      deploy/deploy.sh:384-386
[11] 重启服务                     deploy/deploy.sh:388-390
[12] readiness 门禁               deploy/deploy.sh:392-426
[13] 冒烟门禁                     deploy/deploy.sh:428-470  见 3.4
[14] 审计记录                     deploy/deploy.sh:485-498
[15] 状态簿记                     deploy/deploy.sh:500-526
```

### 3.2 审批门禁（T50 / #177）

- **跑在任何 git 命令之前**（`deploy/deploy.sh:200-204` 的注释即理由）：`/opt/ichat/repo` 就是 systemd
  运行的树，先 fetch 再拒绝会把生产树留在未审批的提交上。`deploy/tests/test_release_audit.py`（27 条）钉住这个顺序。
- `ICHAT_APPROVAL` 过白名单（`deploy/deploy.sh:79-88`：`tr -cs 'A-Za-z0-9._@#/-' '_'`，裁首尾，限 96），
  清洗后必须含至少一个字母数字，否则视为空（`:125-132`）。
- 拒绝条件（`deploy/deploy.sh:221`）：`approval == none` 且 `REQUIRE_APPROVAL == 1` 且 `IS_ROLLBACK != 1` → exit 1。
- **回滚免审批**（`:195-198`、`:209-220`）：只有 `--commit` 是**完整 40 位 hex** 才算回滚；
  传分支名/短 ref 直接 exit 1 并提示改用 `--branch`。
- `ICHAT_REQUIRE_APPROVAL=0` 是紧急口子，仍记 `approval=none` 并 stderr 警告（`:229-231`）；
  拼错的值（如 `yes please`）直接 exit 2，不会被读成"关"。
- **失败语义**：审批被拒 = **什么都没发生**（不 fetch、不 checkout、不重启），日志不写。

### 3.2b 安装身份解析与 4 道 fail-closed 校验（OPS-01 / OPS-02 的修复点）

`deploy/deploy.sh:240-351` 是这一轮的核心改动，顺序**必须**保持：

1. **先快照调用方**（`:257-261`）：`_CALLER_SERVICE_NAME/_SERVICE_USER/_MEDIA_DIR/_TLS_PORT/_HTTP_PORT`。
2. **再 source `.env`**（`:263`）——否则文件会静默覆盖操作员的显式覆盖。
3. **回填快照**（`:265-269`）。
4. **解析**（`:271-299`）：`MEDIA_DIR`/`SERVICE_NAME`/`SERVICE_USER`/`SERVICE_UNIT`；
   端口经 `resolve_port`（`:281-291`）校验 1–65535，**不直接信任字符串**（`:292-293`）；
   origin 由同一份解析结果派生（`:298-299`）。
5. **4 道拒绝**（仅当 `normalise_dir "$APP_DIR" != ICHAT_PROD_APP_DIR` 时生效，`:318-349`）：

   | # | 拒绝条件 | 行 | 退出码 |
   | --- | --- | --- | --- |
   | 1 | 非生产树却解析成生产单元 `ichat.service` | `deploy/deploy.sh:319-326` | 2 |
   | 2 | 非生产树却用生产用户建 media | `deploy/deploy.sh:327-333` | 2 |
   | 3 | 非生产树却用生产 media 根 | `deploy/deploy.sh:334-340` | 2 |
   | 4 | 非生产树却探测生产端口 8443/8000 | `deploy/deploy.sh:341-348` | 2 |

   四条提示都以 "Nothing was changed: no fetch, no checkout, no restart." 结尾，即**在任何破坏性动作之前**退出。
6. 打印解析结果（`:350-351`）后，才进入取码。

> 这段行为由 `deploy/tests/test_deploy_config.py` 的 8 条用例覆盖，其中
> `test_staging_env_alone_restarts_the_staging_unit`、`test_caller_environment_still_overrides_the_env_file`、
> `test_incomplete_staging_identity_is_refused_before_any_restart`、`test_staging_readiness_probes_the_staging_ports`、
> `test_bad_port_is_a_config_error_not_port_zero` 正面钉住"只写在 staging `.env` 就够"、"调用方可覆盖"、
> "身份不完整先拒绝"、"端口拼错不是 0"。

### 3.3 readiness 门禁

`deploy/deploy.sh:392-426`：最多 30 轮，每轮先探 `$TLS_ORIGIN/health/ready/`（带 `-k` 与
`Host: $DJANGO_ALLOWED_HOSTS 第一段`），body 不含 `"status": "ok"` 时再探 `$LOOPBACK_ORIGIN/health/ready/`
（`:407-413`）。**两条路径都断言 JSON body**，因为 301/400 的 body 能骗过 `curl -f`。30 轮全失败 → exit 1，
提示里同时给出两个被探测的 origin（`:423-424`）。

> `DEBUG=False` 时 `SECURE_REDIRECT_EXEMPT = [r'^health/']`（`ichat_pro/settings.py:278`）是 http 回落能成立的
> **唯一**原因；它只豁免 health，所以回落不能用于冒烟（见 3.4）。

### 3.4 冒烟门禁（T23 / #204）

`deploy/deploy.sh:428-470` 六种分支，**每种都被记录**：

| 条件 | 行为 | 日志 `smoke=` | 行 |
| --- | --- | --- | --- |
| `ICHAT_SKIP_SMOKE=1` | WARNING 后跳过 | `skipped` | `deploy/deploy.sh:433-434` |
| 目标树无 `deploy/smoke.sh` 且 `IS_ROLLBACK != 1` | **exit 1，不记录** | 不写 | `deploy/deploy.sh:442-449` |
| 目标树无 `deploy/smoke.sh` 且是回滚 | WARNING，放行 | `skipped-no-script` | `deploy/deploy.sh:450-452` |
| 有脚本但缺 `SMOKE_USERNAME`/`SMOKE_PASSWORD` | **exit 1，不记录** | 不写 | `deploy/deploy.sh:454-460` |
| 有脚本、有凭据、通过 | 继续 | `passed` | `deploy/deploy.sh:461-472` |
| 有脚本、有凭据、失败 | **exit 1，不记录** | 不写 | `deploy/deploy.sh:464-469` |

`deploy/smoke.sh` 的 11 项检查（任一 `fail` 即 `exit 1`）：

| # | 检查 | 期望 | 行 |
| --- | --- | --- | --- |
| 1 | `GET /health/live/` | 200 + `"status": "ok"` | `deploy/smoke.sh:186-191` |
| 2 | `GET /health/ready/` | 200 + `"status": "ok"` | `deploy/smoke.sh:193-198` |
| 3 | `POST /health/ready/` 契约 | **403 或 405**，绝不 200 | `deploy/smoke.sh:200-211` |
| 4 | `GET /login/` | 200 | `deploy/smoke.sh:214-220` |
| 5 | 登录页 CSRF | 页面含 `csrfmiddlewaretoken` | `deploy/smoke.sh:222-231` |
| 6 | 登录前无会话 | jar 里没有 `sessionid` | `deploy/smoke.sh:232-236` |
| 7 | `POST /login/` | **302** | `deploy/smoke.sh:262-276` |
| 8 | 登录后有会话 | jar 里有 `sessionid` | `deploy/smoke.sh:277-281` |
| 9 | `GET /api/conversations/` | 200 且 JSON 顶层有 `conversations` | `deploy/smoke.sh:315-320` |
| 10 | `GET /api/profile/me/` | 200 且 JSON 顶层有 `profile` | `deploy/smoke.sh:322-327` |
| 11 | 未认证 `GET /api/conversations/` | **401** | `deploy/smoke.sh:330-335` |

三项容易被忽略的设计：

- **origin 是配置而非常量**（`deploy/smoke.sh:106-112`）：`SMOKE_TLS_ORIGIN`/`SMOKE_LOOPBACK_ORIGIN`
  由 `deploy.sh` 传入（`deploy/deploy.sh:462-463`），staging 因此探测 8444/8001 而不是生产的 8443/8000。
  缺省值仍是生产，单独运行 `deploy/smoke.sh` 行为不变。
- **JSON 用解释器解析**（`deploy/smoke.sh:299-313`），不是 grep 键名——"200 的错误页里恰好含这个词"不能蒙混。
- **CSRF 的 Referer 重建**（`deploy/smoke.sh:147-166`）：`HOST_HEADER` 非空时用 `Host` 头 + `BASE_URL`
  的端口拼出 Django 实际看到的 origin，否则 `Referer` 与请求 host/scheme 不匹配会 403。

失败语义统一为：**服务已重启（旧进程没了），但这次发布不写日志**。回退用
`sudo /opt/ichat/bin/deploy.sh --commit <上一个 SHA>`（提示见 `deploy/deploy.sh:467`）。

### 3.5 回滚与审计

- 回滚：`--commit <40-hex>` → `git checkout --force <sha>`（`deploy/deploy.sh:359-360`），**停在那个提交**，
  不 reset 回 `origin/main`。恢复前向：重跑不带 `--commit` 的发布（仍需审批）。
- 审计行（`deploy/deploy.sh:488-498`）：

  ```text
  ts=<UTC ISO8601> sha=<40-hex> target=<branch[@commit]> kind=<deploy|rollback>
  actor=<who> via=<sudo|direct> from=<host|local-console> smoke=<passed|skipped|skipped-no-script> approval=<ref|none>
  ```

  `actor`：`ICHAT_DEPLOYER` → `SUDO_USER`（≠`root`）→ `$USER`/`$LOGNAME`，全过 `sanitise`。
  `from`：`SSH_CONNECTION` 第一段，否则 `local-console`（sudo `env_reset` 常丢 `SSH_CONNECTION`，
  远程也可能记成 `local-console`；要准确来源用 `sudo -E`）。
- **"在跑什么"的权威答案**：`DEPLOYMENTS.log` 最后一条（`deploy/deploy.sh:158-162` 的注释即此意）。
  `git -C repo rev-parse HEAD` 只是参考（手工 checkout 会改它而不产生日志）。
- `.deploy-state/current` 与 `previous`（`deploy/deploy.sh:500-526`）：写失败只 WARNING，不把成功的发布
  判成失败；`previous` 取自**状态文件的旧值**，不是 git reflog。

---

## 4. 监控检查项与阈值（`deploy/monitor.sh`）

cron（root）：`*/5 * * * * /opt/ichat/repo/deploy/monitor.sh >> /var/log/ichat-monitor.log 2>&1`
（脚本已是 `100755`，**可直接执行**；见 `deploy/runbook.md:92`）

| # | 检查 | 触发 | key | 行 |
| --- | --- | --- | --- | --- |
| 1 | readiness | `/health/ready/` **连续** ≥ `ICHAT_READY_MAX_FAILS`（缺省 3）次失败；单次只计数 | `ready` | `deploy/monitor.sh:226-251` |
| 2 | 磁盘 | PG 数据盘（`ICHAT_PG_DATA_DIR`，不存在则 `/var`）使用率 ≥ `ICHAT_DISK_PCT`（85） | `disk` | `deploy/monitor.sh:254-272` |
| 3 | PG 连接数 | `SELECT count(*) FROM pg_stat_activity` ≥ `ICHAT_PG_MAX_CONN`（40） | `pg-conn` | `deploy/monitor.sh:318-338` |
| 4 | Redis 内存 | `used_memory` ≥ `ICHAT_REDIS_MAX_MB`（512 MB） | `redis-mem` | `deploy/monitor.sh:341-358` |
| 5 | 证书 | `ICHAT_LETLS_DIR` 下**每一条** lineage 的 `cert.pem` 剩余 < `ICHAT_CERT_MIN_DAYS`（14）天 | `cert` | `deploy/monitor.sh:364-390` |
| 6 | journal | `journalctl -u $ICHAT_SERVICE_NAME --since "-10 min" -p err` 行数（减 2 行 banner）≥ 10 | `journal` | `deploy/monitor.sh:393-417` |

**"查不到"≠"一切正常"**：`check_failed` 把检查名收进 `CHECK_FAILURES`（`deploy/monitor.sh:163-166`），
任何一项跑不成都会在结尾触发 `monitor` 告警并 **exit 1**（`:432-438`）。触发点：
`df` 缺失/读不到（`:257-266`）、`psql` 缺失（`:319-322`）、`DATABASE_URL` 不可用（`:323-326`）、
查询失败（`:328-332`）、`redis-cli` 缺失（`:342-345`）、`journalctl` 缺失（`:394-397`）、
**`journalctl` 查询非零退出**（`:403-408`）、证书目录为空或不可读（`:369-389`）、
状态目录不可写导致"连续失败"无法累计（`:239-244`）。

**告警与限频**（`deploy/monitor.sh:183-223`）：

- 同一 key 两次告警间隔 ≥ `ICHAT_ALERT_MIN_SECS`（1800 秒）；限频时间戳**只在 webhook 投递成功后**写入
  （`:211`），投递失败下一轮重试（`:212-216`）；无 webhook（日志模式）也写时间戳（`:220`）。
- 告警同时进 stderr 与 `logger -t ichat-monitor`（`:205-206`），webhook 用
  `{"text": "[ichat <key>] ..."}`（`:208-210`），`json_escape` 把换行/回车换成空格（`:184-191`）。
- 自检：`ICHAT_ALERT_TEST=1 /opt/ichat/repo/deploy/monitor.sh`（`:420-423`）。
- **不做**的事：不重启任何服务、不碰 443/2096/25383、不读聊天数据。

> **`monitor.sh` 与 `deploy.sh` 共用安装身份**：`JOURNAL_UNIT=${ICHAT_SERVICE_NAME:-ichat}`
> （`deploy/monitor.sh:149`），所以 staging 的监控看 `ichat-staging` 的日志，而不是生产的。

---

## 5. staging 隔离检查（`deploy/staging/check_isolation.sh`）

```bash
sudo deploy/staging/check_isolation.sh \
    --prod-env /opt/ichat/.env --staging-env /opt/ichat-staging/.env [--check-data]
```

脚本已是 `100755`，**可直接执行**（`deploy/runbook.md:152` 即这样写）。
退出码：`0` 全隔离；`1` 有 violation；`2` 参数错或文件不可读
（`deploy/staging/check_isolation.sh:23-30`、`:346-351`）。

### 5.1 检查维度

| 段 | 断言 | 不满足时 | 行 |
| --- | --- | --- | --- |
| 秘密 | `DJANGO_SECRET_KEY`、`DATABASE_URL` 两侧非空且不相等 | violation；任一侧为空也算 | `deploy/staging/check_isolation.sh:87-95` |
| 秘密 | **登录角色名**必须不同（从 URL userinfo 解析，按 digest 报告） | violation | `:102-131` |
| 秘密 | **角色密码**必须不同；任一侧无密码也算"无法比较" | violation | `:133-141` |
| 配置 | staging 库名（从 URL path 解析）必须是 `ichat_staging` | violation | `:146-159` |
| 配置 | `REDIS_URL` 逻辑库编号必须不同 | violation | `:161-172` |
| 配置 | `DJANGO_CSRF_TRUSTED_ORIGINS` 必须不同 | violation | `:174-176` |
| 存储 | media / static 原值必须不同 | violation | `:178-182` |
| 存储 | **两类根**都做"互不相等且互不为祖先"（`normalise_path` 处理 `/`、`..`、重复分隔符） | violation | `:184-215` |
| 运行时 | `ICHAT_APP_DIR` 不能规范化成 `/opt/ichat` | violation | `:219-222` |
| 运行时 | `ICHAT_SERVICE_NAME` / `ICHAT_SERVICE_USER` 不能是 `ichat` | violation | `:223-228` |
| 运行时 | staging 单元里 Daphne 端口不能是 8000；unit 找不到 → `port UNVERIFIED` + violation | violation | `:230-252` |
| 活库（`--check-data`） | 真连两库读 `current_user` 并比较 | violation / UNVERIFIED | `:305-324` |
| 活库（`--check-data`） | 真连两 Redis 读**实际**逻辑库并比较 | violation / UNVERIFIED | `:326-342` |

### 5.2 `--check-data` 的真实行为（OPS-08/09/10 的修复点）

现在是**真连接**，不是比字符串：

- `live_psql_role`（`deploy/staging/check_isolation.sh:267-286`）用 `PGPASSWORD` 环境变量 +
  `psql -X -qAt -c 'SELECT current_user;'` 读**服务端确认的登录角色**，不读 URL；
- `live_redis_db`（`:288-303`）用 `redis-cli client info` 的 `db=` 字段读**实际所在逻辑库**；
- 三方失败都报 `UNVERIFIED` 并 exit 1：客户端缺失（`:307-308`、`:326-327`）、URL 缺一侧（`:309-310`、`:328-329`）、
  认证/查询失败（`:315-318`、`:333-336`）。

> 与上一版（pin `2905473`）相反：**"活库已隔离"这句话现在有代码支撑**，但仍**没有真实 PG/Redis 实测证据**
> （§10）。静态 URL 比较只覆盖秘密/配置两段，不能替代活库。

### 5.3 秘密边界

脚本从不打印秘密值：比较只用等值判断，需要标识时打印 `sha256sum | cut -c1-16`
（`deploy/staging/check_isolation.sh:68`、`:75`、`:130`、`:140`）。库名从 URL 解析后单独打印（`:153-158`），
且解析前先注释说明"绝不打印 URL，因为 userinfo 段可能带密码"（`:151-152`）。

---

## 6. 备份与恢复

| 动作 | 命令 | 要点 | 位置 |
| --- | --- | --- | --- |
| 每日备份 | `30 17 * * * /opt/ichat/repo/deploy/backup.sh` | `pg_dump --no-owner --no-privileges` + `tar -C $MEDIA_DIR` **内容** + 相对路径 `SHA256SUMS` + `META`；目录 700；`find -mtime +14` 清理 | `deploy/backup.sh:8-44` |
| 真实恢复 | `sudo bash deploy/restore.sh /var/backups/ichat/<STAMP>` | `sha256sum -c` → 手输库名确认 → 停 `ichat.service` → `pg_terminate_backend` + `DROP SCHEMA public CASCADE` → `psql --single-transaction` 回放 → 旧 media 改名 `.pre-restore-<UTC>` → 起服探针 30 次 | `deploy/restore.sh:22-74` |
| 恢复演练 | `sudo bash deploy/restore_drill.sh` | seed → 真实 backup → 调用真实 `restore.sh`（注入 `RESTORE_ENV_FILE`/`ICHAT_MEDIA_DIR`/`ICHAT_SKIP_SERVICE=1`/`ICHAT_ASSUME_YES=1`）→ 行数 parity/关系/密文逐字节/文件摘要 | `deploy/restore_drill.sh:28-119` |

**恢复不自动降级数据库**：`restore.sh` 恢复的是备份里的 schema 与数据；应用回滚（`--commit`）不会倒推迁移。
不可逆迁移只能靠备份回退。

> **已知缺口（DOC-NEW-1，见 §9.2）**：三个脚本都**不接受** `ICHAT_APP_DIR`——`deploy/backup.sh:6-7`、
> `deploy/restore.sh:18`、`deploy/restore_drill.sh:15`/`:25` 把路径写死；`deploy/restore_drill.sh:24`
> 直接 `source $APP_DIR/.env`（生产 `.env`），`:28-31` 就往**生产库** seed。备份/恢复链路**没有**跟着
> `deploy.sh` 参数化，所以 §2.2 的可覆盖变量**只对 `deploy.sh` 成立**。
> **不要在 staging 主机上直接跑 `restore_drill.sh`**。

---

## 7. 秘密边界（不进 argv / 不进日志）

| 秘密 | 怎么传 | 证据 |
| --- | --- | --- |
| 冒烟密码 | 写进 0600 文件，`curl --data-binary @file` | `deploy/smoke.sh:256-260`、`:268`（`chmod 600` 在 `:82`、`:260`） |
| DB 密码（监控） | 解析成 `PGPASSWORD` 环境变量，`psql` 不收 URL 参数 | `deploy/monitor.sh:314`、`:328` |
| DB URL（监控解析器） | **走 stdin**，不在 argv（OPS-07 的修复点） | `deploy/monitor.sh:300-310` |
| DB URL（恢复） | `psql "$DB_URL"` —— **仍会进 argv** | `deploy/restore.sh:42-44`，见 §9.2 的 DOC-NEW-1 |
| `DJANGO_SECRET_KEY` 等 | 只在 `.env`（600），`deploy.sh` 只 `source`，不 echo | `deploy/deploy.sh:263` |
| 审计日志 | 字段过 `sanitise` 白名单，换行无法伪造第二条记录 | `deploy/deploy.sh:79-88` |
| 告警消息 | `json_escape` 去控制字符；不含密码/cookie/载荷 | `deploy/monitor.sh:184-191` |
| 隔离报告 | 秘密只以 SHA-256 前缀出现；角色/密码按 digest 报告 | `deploy/staging/check_isolation.sh:68`、`:130`、`:140` |

**监控状态目录与日志**没有秘密；`DEPLOYMENTS.log` 只有 `sha/target/kind/actor/via/from/smoke/approval`。

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
    /opt/ichat/repo/deploy/smoke.sh

# —— 监控（100755，可直接执行）——
sudo /opt/ichat/repo/deploy/monitor.sh
sudo ICHAT_ALERT_TEST=1 /opt/ichat/repo/deploy/monitor.sh

# —— staging 隔离（100755，可直接执行）——
sudo /opt/ichat/repo/deploy/staging/check_isolation.sh \
    --prod-env /opt/ichat/.env --staging-env /opt/ichat-staging/.env

# —— 备份 / 恢复 / 演练 ——
sudo /opt/ichat/repo/deploy/backup.sh
sudo bash /opt/ichat/repo/deploy/restore.sh /var/backups/ichat/<STAMP>
sudo bash /opt/ichat/repo/deploy/restore_drill.sh

# —— 运维测试（仓根，三种等价写法）——
.\.venv\Scripts\python.exe manage.py test deploy.tests --parallel 1 -v 2   # 139 条
.\.venv\Scripts\python.exe manage.py test --parallel 1                     # 默认发现 543 条（含这 139）
.\.venv\Scripts\python.exe manage.py test \
  deploy.tests.test_deploy_config deploy.tests.test_deploy_gate deploy.tests.test_exec_bits \
  deploy.tests.test_isolation_check deploy.tests.test_monitor deploy.tests.test_release_audit \
  deploy.tests.test_runbook_contract deploy.tests.test_smoke_gate --parallel 1 -v 2
```

**测试条数（pin `99c9d68`，按 `def test_` 计）**：`test_deploy_config` 8、`test_deploy_gate` 9、
`test_exec_bits` 5、`test_isolation_check` 34、`test_monitor` 31、`test_release_audit` 27、
`test_runbook_contract` 9、`test_smoke_gate` 16，**合计 139**。

**默认发现已含 `deploy.tests`**：`f999d3f` 加了 `deploy/__init__.py`，把 `deploy` 从 PEP 420 namespace
package 变成常规包。在本 pin 的树上实测 `DiscoverRunner.build_suite()`：

```text
default_total 543
deploy_label 139
```

即 `manage.py test` 的默认 suite 已收进这 139 条，**"必须显式点名五个模块"的旧要求作废**。
仍建议显式点名 `deploy.tests`（或八模块全列），唯一理由是**独立性**：默认发现受 `settings` 与 runner
配置变动影响，显式点名不受。条数从 82 涨到 139，增量来自 OPS-01/02/04/05/06/07/08/09/10 的回归
（新增 `test_deploy_config` 8 + `test_exec_bits` 5 + `test_runbook_contract` 9；`test_isolation_check` 18→34、
`test_monitor` 18→31、`test_smoke_gate` 13→16、`test_deploy_gate` 6→9）。

---

## 9. 已知问题与审查发现状态

### 9.1 已关闭（以 `99c9d68` 为准，附新核对行号）

| 编号 | 原现象 | 关闭提交 | 现在的证据 |
| --- | --- | --- | --- |
| OPS-01 | 只写在 staging `.env` 的服务身份在 `source` 前已回落生产默认值 → 重启生产单元 / 写生产 media | `8308dd5` | 快照→source→回填（`deploy/deploy.sh:257-269`）；解析（`:271-299`）；4 道拒绝（`:318-349`）；`deploy/tests/test_deploy_config.py` 8 条 |
| OPS-02 | staging 重启后探针仍请求生产 8443/8000 | `d6e2dcd` + `8308dd5` | 端口解析（`deploy/deploy.sh:292-293`）、origin 派生（`:298-299`）、readiness 用解析值（`:407-413`）、smoke 传 origin（`:462-463`）、`deploy/smoke.sh:106-112` |
| OPS-03 | 前向发布缺 `smoke.sh` 被历史回滚例外放行 | `2905473` | `deploy/deploy.sh:442-449` exit 1 不记录；`test_deploy_gate.py::test_forward_release_without_smoke_sh_fails_closed` |
| OPS-04 | `monitor.sh` / `check_isolation.sh` 提交为 100644，runbook 却直接执行 | `05bb0c6` | `git ls-tree 99c9d68 deploy/`：`deploy.sh`/`smoke.sh`/`monitor.sh`/`check_isolation.sh`/`backup.sh`/`restore.sh`/`restore_drill.sh` 全 **100755**；`deploy/tests/test_exec_bits.py` 5 条钉住 |
| OPS-05 | 写在 `.env` 的监控阈值不生效 | `753cd9d` | `ENV_FILE_KEYS` 白名单（`deploy/monitor.sh:74-88`）+ 逐行解析（`:93-120`）+ 阈值取解析结果（`:135-140`）；`test_runbook_contract.py` 双向校验文档↔白名单 |
| OPS-06 | `journalctl` 查询失败被吞成"0 错误" | `753cd9d` | 先取 `rc`（`deploy/monitor.sh:398-408`），非 0 → `check_failed "journal"` + 结尾 exit 1（`:432-438`） |
| OPS-07 | 完整 DATABASE_URL（含密码）进辅助 python 的 argv | `753cd9d` | URL 从 **stdin** 进（`deploy/monitor.sh:300-310`），argv 里没有 URL |
| OPS-08 | `--check-data` 不连接任何库，工具缺失也静默跳过 | `82641c2` | `live_psql_role`（`:267-286`）、`live_redis_db`（`:288-303`）、UNVERIFIED→violation（`:305-343`） |
| OPS-09 | 改库名就能带着相同 DB 用户名/密码通过隔离 | `82641c2` | 角色名（`:125-131`）与密码（`:133-141`）分别比较 |
| OPS-10 | static root 只比原值不比嵌套 | `82641c2` | `check_no_nesting`（`:190-213`）对 `DJANGO_STATIC_ROOT` 与 `DJANGO_MEDIA_ROOT` 各调一次（`:214-215`） |
| C11 / QA-01 | 82 条运维测试默认不被 `manage.py test` 收集 | `f999d3f` + `6542cdf` | 默认发现 543 条 / `deploy.tests` 139 条（§8 实测） |

### 9.2 仍未修的（按 `99c9d68` 复核后仅剩这一条）

| 编号 | 级别 | 现象 | 精确位置 |
| --- | --- | --- | --- |
| **DOC-NEW-1**（本修复轮新发现，不在 2026-10-24 审查清单里） | MAJOR | 备份/恢复链路**没有**参数化：`APP_DIR`、`MEDIA_DIR`、`VENV`、unit 名全部写死，因此**无法对 staging 做备份或演练**；更严重的是 `restore_drill.sh` 会对**生产** `.env` 与生产库动手 | 见下方逐条 |

**DOC-NEW-1 的逐条位置（pin `99c9d68`，三个文件相对 `2905473` 的 blob 均未变）**：

| 文件:行 | 内容 | 后果 |
| --- | --- | --- |
| `deploy/backup.sh:6` | `APP_DIR=/opt/ichat` | 永远读生产 `.env` |
| `deploy/backup.sh:7` | `MEDIA_DIR=/var/lib/ichat/media` | 永远打包生产 media |
| `deploy/restore.sh:18` | `APP_DIR=/opt/ichat` | 默认读生产 `.env` |
| `deploy/restore.sh:25` | `source "${RESTORE_ENV_FILE:-$APP_DIR/.env}"` | 只有显式传 `RESTORE_ENV_FILE` 才能改 |
| `deploy/restore.sh:42-44` | `psql "$DB_URL" …` | URL（含密码）进 argv |
| `deploy/restore_drill.sh:15` | `APP_DIR=/opt/ichat` | 演练脚本自身钉在生产树 |
| `deploy/restore_drill.sh:16` | `REPO_DIR=$APP_DIR/repo` | 用生产 checkout 跑 `manage.py` |
| `deploy/restore_drill.sh:24` | `source $APP_DIR/.env` | **无条件读生产 `.env`** |
| `deploy/restore_drill.sh:25` | `VENV=/opt/ichat/.venv/bin` | 用生产 venv |
| `deploy/restore_drill.sh:28-31` | `manage.py shell < deploy/seed_drill_data.py` | **向生产库写入 seed 数据** |
| `deploy/restore_drill.sh:34` | `"$REPO_DIR/deploy/backup.sh"` | 调生产备份 |
| `deploy/restore_drill.sh:35` | `BK=/var/backups/ichat/$(ls -1t …)` | 读生产备份目录 |
| `deploy/restore_drill.sh:39-40` | `sudo -u postgres psql -c "DROP/CREATE DATABASE $SCRATCH_DB OWNER ichat"` | 用 postgres 超级用户绕过角色隔离，且 owner 固定 `ichat` |
| `deploy/restore_drill.sh:48` | `psql_live() { sudo -u postgres psql -d ichat …}` | 校验端直连生产库名 `ichat` |

**影响与建议**：`deploy.sh` 已能发布 staging，但备份/恢复完全没跟上——这不是"缺功能"，而是
**在 staging 主机上执行演练会污染生产库**（`:24` 读生产秘密 + `:28-31` 往生产库 seed）。
修复方向与 OPS-01 同形：把 `APP_DIR`/`MEDIA_DIR`/`VENV`/unit 名改成"调用方环境 > `.env` > 生产缺省"，
并对非生产树加同样的 fail-closed 校验（否则 `restore_drill.sh` 在 staging 主机上仍是生产爆炸半径）。
建议与 #176 一起做，且在实机搭建**之前**完成。

**这一条不在原 10 项清单里**：原审查只覆盖 `deploy.sh` / `smoke.sh` / `monitor.sh` / `check_isolation.sh`，
没有审备份恢复链路，所以它既不是"回归"也不是"已修"。本轮**只记录，不修实现**。

### 9.3 坑 6：`media/` 不能在 nginx 整体公开（回链 `conventions.md:340`）

`conventions.md` §6 指向"本文 §8 坑 6"，那个编号来自旧目录结构；对应内容在这里：

- 只允许 `media/avatars/` 公开——`deploy/nginx/ichat.conf:58-62` 用
  `location /media/avatars/ { alias __MEDIA_AVATARS__/; }`。
- `media/uploads/` **绝不能**公开：`deploy/nginx/ichat.conf:64-66` 显式
  `location = /media/uploads/ { return 403; }`，下载必须走认证的 `/api/files/`。
- **不要**新增 `location /media/` 或 `alias` 指向整个 media 根。
- **不要**为了调试打开 `DJANGO_DEBUG`：生产安全依赖 `DEBUG=False` 时 Django 根本不注册 media 路由——
  `ichat_pro/urls.py:32-33`：`if settings.DEBUG:` 才 `urlpatterns += static(...)`。
  `DEBUG=True` 会同时让 `SECURE_*`/`*_COOKIE_SECURE`/`SECURE_SSL_REDIRECT` 整段失效
  （`ichat_pro/settings.py:270` 是 `if not DEBUG:` 的入口）。
- 静态文件走 `location /static/ { alias __STATIC_ROOT__/; }`（`deploy/nginx/ichat.conf:50-54`），
  其根必须与 `DJANGO_STATIC_ROOT` 一致（`deploy/deploy.sh:386` 的 `collectstatic --clear` 会清空该目录）。

---

## 10. 未验证范围（明确声明）

| 项 | 状态 |
| --- | --- |
| 真实 AWS 主机 / 生产部署 | **未执行**。本文没有任何一次真实发布、回滚或恢复。 |
| staging 实机 | **未搭建**。隔离检查只在合成 `.env` 与桩工具下跑过。 |
| 真实 PostgreSQL / Redis | 本机没有；139 条运维测试用桩与 SQLite，**不是** PG/Redis 集成证据。 |
| `--check-data` 的活库比较 | 代码路径存在且有 7 条测试（`deploy/tests/test_isolation_check.py:364-517` 的 `LiveDataCheckTests`），但**未连真实服务器**。 |
| Linux cron / systemd / nginx | 未运行；shell 侧只做了 `bash -n`。 |
| 139 条是否真进 CI | **未用 CI run 验证**；只在本机实测默认发现 543 / `deploy` 139。 |
| 冒烟失败的真实阻断 | 只有单元级证据（`test_smoke_gate.py` 16 条、`test_deploy_gate.py` 9 条），**没有**一次真实"登录坏了但 readiness 正常"的发布。 |
| 服务器 SHA / 安装包 SHA | 未复核。`deploy/runbook.md:16` 的 `f2f60ec` / 2026-10-07T13:42:16Z 是**历史陈述**，未连服务器验证。 |
| DOC-NEW-1 的实际爆炸半径 | 由代码路径推断（`source` 生产 `.env` + `psql -d ichat`），**未在真实主机复现**。 |

---

## 11. 这个模块的开放任务

| Issue | 卡片 | 建议起点 |
| --- | --- | --- |
| #130 | P4 T08 备份恢复流程 | **DOC-NEW-1**：先参数化备份/恢复，再做跨实例恢复演练 |
| #139 | P4 T12 监控告警 | OPS-05/06/07 已修；剩真实 PG/Redis 上的阈值验证与告警通道联调 |
| #176 | P4 T49 独立测试环境 | `deploy.sh` 侧已 fail-closed；先修 DOC-NEW-1 再搭实机，然后跑 `--check-data` 取真实隔离证据 |
| #177 | P4 T50 发布审批与回滚 | 真实发布演练 + `DEPLOYMENTS.log` 的 CI 侧写入 |
| #204 | P4 Test T23 部署冒烟门禁 | 在真实 host 上制造一次登录故障，确认发布被阻断且日志无新行 |

---

## 12. 给下一个 Agent 的提示词

```text
你在 iChat Pro 仓库工作。你的模块是「云部署与运维」：deploy/**、ichat_pro/health.py、
ichat_pro/settings.py 的数据库/Redis/静态/媒体/生产安全段、nginx 与 systemd 模板、备份恢复。

先读（按顺序）：
1. docs/handoff/conventions.md（全文，硬性规则）
2. docs/handoff/cloud-deploy-ops.md（本文，尤其 §9.2 的 DOC-NEW-1 与 §10 未验证范围）
3. deploy/runbook.md（人写的手册；§3/§3b/§3c 已与本轮代码同步）
4. deploy/deploy.sh 全文 526 行——唯一发布入口，审批/身份解析/4 道 fail-closed/readiness/冒烟都在里面

铁律：
- 不改 static/js/**、accounts/**、.github/**、mobile/**（不是你的边界）。
- 任何"发布成功"的断言必须有 DEPLOYMENTS.log 的一行或 deploy/tests 的一条作为证据。
- 绝不在真实生产主机上跑 staging 相关的备份/演练：DOC-NEW-1 未修时 restore_drill.sh 会 seed 生产库。
- 秘密不进 argv/日志/Issue；DB 密码用 env 或 stdin。
- 引用代码行为时给 path:line，并用 git show <sha>:<path> 自己核；不要转述 runbook。
- 运维测试：manage.py test deploy.tests --parallel 1（139 条）；默认发现也含它，但仍建议显式点名。

交付：分支 fix/<卡号>-<短名>、一 finding 一 commit、`manage.py check` +
`makemigrations --check --dry-run` + deploy.tests 全绿，并把证据写进 Issue。
提交前另开一个独立审查子 Agent（见 conventions.md §1）。
```
