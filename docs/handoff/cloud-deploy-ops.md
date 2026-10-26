# 模块 5：云部署与运维（`deploy/` + health + nginx/systemd + 备份恢复）

> 交接对象：负责服务器部署、发布审批/回滚、监控告警、staging 隔离、备份恢复的 Agent 或运维。
> **pin：`a0e1fafb7d4b2079ee56848f01b6fef4e6d5bab9`**（`iChat_Pro.wt-t176`，分支 `feat/p4-t49-test-environment`，2026-10-26）。
> 本文所有行号**以该 sha 为准**，格式统一为 `path:line`。相关规范见 [conventions.md](conventions.md)。
> **本版相对上一版（pin `99c9d68`）新增了 §3.2a「部署身份主权」**——那是这一轮最重要的安全语义，
> 上一版完全没有；此外 `.env` 取值语法（§2.3）、`--check-data` 的真实实现（§5.2）与测试口径（§8）都已重写。

---

## 0. 这份文档的依据与边界（先读）

本文所有关于脚本行为的断言都来自**实际阅读该提交里的文件**，不是 runbook 转述。核对方式：
用 `git show a0e1faf:<path>` 把 `deploy/**` 全部 31 个文件导出到只读快照，逐文件用
`git hash-object` 与该提交的 blob 比对——**31/31 一致**，所以每个行号都可复现。

| 项 | 值 |
| --- | --- |
| pin | `a0e1fafb7d4b2079ee56848f01b6fef4e6d5bab9`（`docs(deploy): document caller-only identity, .env value syntax and live db selection (F-01, F-05, F-07)`） |
| 基线 | `6f702d9`，本分支共 17 个提交（`f999d3f` … `a0e1faf`） |
| `deploy/deploy.sh` | `951e96770d79033ed9745c8582424ff4c9638628`，30326 B，**676 行**，`100755` |
| `deploy/smoke.sh` | `f96611dfaf8b32c65dd6a737dbe4320753b16ff6`，14584 B，**343 行**，`100755` |
| `deploy/monitor.sh` | `570927842a58e7c43b6f5beb9dab5cac84ed6886`，20316 B，**489 行**，`100755` |
| `deploy/staging/check_isolation.sh` | `f09b97ec761c6f139c54827f0d984a8c3db626e6`，17810 B，**386 行**，`100755` |
| `deploy/runbook.md` | `8ebf9344158f184535dcf3db5ae3273a99eac9a4`，21780 B，**210 行** |
| 运维测试 | `deploy/tests/` **10 个模块 / 171 条**（§8） |

**本提交相对 2026-10-24 审查结论的变化**（原始发现见 `.review/phase4-full-review-20261024/cloud-ops-review.md`，
上一轮的追加发现 F-01…F-08 见 `.review/phase4-remediation-20261026/`）：**OPS-01…OPS-10 与 F-01…F-08 全部已关闭**。

| 批次 | 关闭提交 |
| --- | --- |
| OPS-01 / 02 | `8308dd5` + `d6e2dcd` |
| OPS-03 | `2905473` |
| OPS-04 | `05bb0c6` |
| OPS-05 / 06 / 07 | `753cd9d` |
| OPS-08 / 09 / 10 | `82641c2` |
| QA-01（默认发现） | `f999d3f` + `6542cdf` |
| **F-01 / F-02 / F-03 / F-04**（身份主权、审计冻结、origin 守卫、路径规范化） | `507ff54` |
| **F-05 / F-07**（`.env` 取值语法、真实 `CLIENT INFO` 解析） | `ba76fd3` |
| **F-06 / F-08**（测试脚手架可移植性、bash 缺失即失败） | `5a24b3b`、`328e079`、`5202847` |
| runbook 同步 | `a0e1faf` |

**本文上一版（pin `2905473`）把 OPS-01…OPS-07 写成"仍然成立"，上一版（pin `99c9d68`）又漏了整组
F 发现**——两个结论都已作废；以 §9 为准。

> **仍然有效的边界**：以上都是**代码与测试层面**的修复。没有任何一次真实发布、回滚、恢复或
> staging 实机运行被本文验证过——见 §10。

---

## 1. 这块现在是什么

| 文件 | 行数 | 权限 | 职责 |
| --- | ---: | --- | --- |
| `deploy/deploy.sh` | 676 | 100755 | 唯一发布入口：审批 → **身份主权校验** → 解析安装身份 → 取码 → venv → 迁移/静态 → 重启 → readiness → 冒烟 → 审计 |
| `deploy/smoke.sh` | 343 | 100755 | 部署后冒烟门禁：health/readiness/POST 契约/登录/CSRF/会话/核心 API/未认证 401 |
| `deploy/monitor.sh` | 489 | 100755 | cron 监控：readiness 连续失败、PG 磁盘与连接数、Redis 内存、证书余期、journal 错误 |
| `deploy/staging/check_isolation.sh` | 386 | 100755 | staging 隔离断言：秘密（角色/密码）、配置、存储、运行时、`--check-data` 活库 |
| `deploy/staging/env.staging.example` | 52 | 100644 | staging `.env` 模板（**只放应用配置，身份键必须走命令行**） |
| `deploy/staging/ichat-staging.service` | 28 | 100644 | staging 单元（`ichat-staging` 用户，Daphne 8001） |
| `deploy/systemd/ichat.service` | 23 | 100644 | 生产单元（`ichat` 用户，Daphne 8000） |
| `deploy/env.production.example` | 22 | 100644 | 生产 `.env` 模板 |
| `deploy/nginx/ichat.conf` | 81 | 100644 | Nginx：80（ACME + 301）/ 8443（TLS、static、avatars、proxy 到 8000） |
| `deploy/nginx/reload-nginx.sh` | 10 | 100755 | certbot deploy-hook：`nginx -t` 后 reload |
| `deploy/backup.sh` | 45 | 100755 | `pg_dump` + media 打包 + SHA256 + META，保留 14 天 |
| `deploy/restore.sh` | 74 | 100755 | 真实恢复：校验和 → 停服 → drop schema → 回放 → media 换目录 → 起服并探针 |
| `deploy/restore_drill.sh` | 119 | 100755 | 恢复演练：seed → backup → 真实 `restore.sh`（注入独立库/目录）→ 逐项校验 |
| `deploy/seed_drill_data.py` | 160 | 100644 | 演练用确定性数据（用户/关系/私聊/群聊/加密文件+逐持有者密钥） |
| `deploy/tests/` | **171 条 / 10 模块** | 100644 | 上述脚本的 Python 回归（§8） |

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

**优先级自高到低**：**调用方进程环境 > `$APP_DIR/.env` > 生产缺省**。
但这条规则有一个**关键例外**：**身份类键只认调用方**，`.env` 里出现它们会让整个部署被拒绝（§3.2a）。
`deploy.sh` 的实现是"快照 → source → 回填"（`deploy/deploy.sh:318-354`），
`ICHAT_APP_DIR` 也**故意不从文件里回读**（`deploy/deploy.sh:264` 的注释即此意）。

| 变量 | 缺省 | 读取位置 | 能写在 `.env` 里？ | 作用 |
| --- | --- | --- | --- | --- |
| `ICHAT_APP_DIR` | `/opt/ichat` | `deploy/deploy.sh:43` | **❌ 主权键** | 安装树；决定读哪个 `.env` |
| `ICHAT_PROD_APP_DIR` | `/opt/ichat` | `deploy/deploy.sh:354`、`:431` | **❌ 主权键** | 声明"哪棵树才是生产"，供非生产树的校验使用 |
| `ICHAT_SERVICE_NAME` | `ichat` | `deploy/deploy.sh:365`、`:367` | **❌ 主权键** | `systemctl restart/enable <name>.service` |
| `ICHAT_SERVICE_USER` | `ichat` | `deploy/deploy.sh:366`、`:522` | **❌ 主权键** | media 属主 |
| `ICHAT_MEDIA_DIR` | `/var/lib/ichat/media` | `deploy/deploy.sh:364` | **❌ 主权键** | media 根 |
| `ICHAT_TLS_PORT` | `8443` | `deploy/deploy.sh:381` | **❌ 主权键** | readiness 主探测端口（经 Nginx） |
| `ICHAT_HTTP_PORT` | `8000` | `deploy/deploy.sh:382` | **❌ 主权键** | readiness 回落端口（Daphne loopback） |
| `ICHAT_READY_ORIGIN` | `https://127.0.0.1:$TLS_PORT` | `deploy/deploy.sh:387` | **❌ 主权键** | 显式覆盖主探测 origin |
| `ICHAT_LOOPBACK_ORIGIN` | `http://127.0.0.1:$HTTP_PORT` | `deploy/deploy.sh:388` | **❌ 主权键** | 显式覆盖回落 origin |
| `ICHAT_DEPLOYER` | 空 | `deploy/deploy.sh:93`、`:335` | **❌ 主权键** | CI 调用时的 `actor` |
| `ICHAT_APPROVAL` | 空 | `deploy/deploy.sh:125-132`、`:205` | **❌ 主权键** | 审批引用；空即 `none` |
| `ICHAT_REQUIRE_APPROVAL` | `1` | `deploy/deploy.sh:139-150` | **❌ 主权键** | `1/true/yes/on` 或 `0/false/no/off`；其他值 exit 2 |
| `ICHAT_SKIP_SMOKE` | `0` | `deploy/deploy.sh:350`、`:579` | **❌ 主权键** | `1` = 跳过冒烟，记 `smoke=skipped` |
| `ICHAT_STATUS_LINES` | `5` | `deploy/deploy.sh:165`、`:167` | ✅ | `--status` 行数 |
| `SMOKE_USERNAME` / `SMOKE_PASSWORD` | 无 | `deploy/deploy.sh:600`、`deploy/smoke.sh:141` | ✅ | 冒烟专用账号；缺则**发布失败** |
| `SMOKE_TIMEOUT` | `10` | `deploy/smoke.sh:52` | ✅ | 单请求超时（秒） |
| `SMOKE_INSECURE_TLS` | `0` | `deploy/smoke.sh:54` | ✅ | `1` = 加 `-k` |
| `SMOKE_TLS_ORIGIN` | `https://127.0.0.1:8443` | `deploy/smoke.sh:111` | ✅（由 `deploy.sh` 传值，`deploy/deploy.sh:609`） | 冒烟首试 origin |
| `SMOKE_LOOPBACK_ORIGIN` | `http://127.0.0.1:8000` | `deploy/smoke.sh:112` | ✅（同上） | 冒烟回落 origin |
| `SMOKE_PYTHON` | 自动探测 | `deploy/smoke.sh:290` | ✅（`deploy.sh` 传 venv python，`deploy/deploy.sh:608`） | JSON 解析解释器 |
| `BASE_URL` / `AUTO_BASE_URL` | 无 / `0` | `deploy/smoke.sh:114-118` | ✅ | 冒烟目标；`AUTO_BASE_URL=1` 时 TLS → loopback 回退 |
| `ICHAT_ENV_FILE` | `/opt/ichat/.env` | `deploy/monitor.sh:56` | —（这是**选择文件**的变量） | 监控读取的 env 文件 |
| `ICHAT_MONITOR_STATE` | `/var/lib/ichat/monitor` | `deploy/monitor.sh:57` | ✅ | 状态目录 |
| `ICHAT_BASE_URL` | `https://127.0.0.1:8443` | `deploy/monitor.sh:173` | ✅（白名单内） | readiness 探测 origin（**读完 `.env` 之后**才求值） |
| `ICHAT_ALERT_WEBHOOK` | 空 | `deploy/monitor.sh:75`（白名单） | ✅（白名单内） | webhook；空则只记 syslog/stderr |
| `ICHAT_ALERT_MIN_SECS` / `ICHAT_DISK_PCT` / `ICHAT_PG_MAX_CONN` / `ICHAT_REDIS_MAX_MB` / `ICHAT_READY_MAX_FAILS` / `ICHAT_CERT_MIN_DAYS` | 1800 / 85 / 40 / 512 / 3 / 14 | `deploy/monitor.sh:183-188` | ✅（白名单内） | 限频与阈值 |
| `ICHAT_PG_DATA_DIR` / `ICHAT_LETLS_DIR` | `/var/lib/postgresql` / `/etc/letsencrypt/live` | `deploy/monitor.sh:195-196` | ❌（不在白名单） | 磁盘与证书检查位置 |
| `ICHAT_SERVICE_NAME`（监控侧） | `ichat` | `deploy/monitor.sh:197` | ✅（白名单内） | journal 检查的单元名（staging 看 `ichat-staging`） |
| `ICHAT_DB_URL` | 空 | `deploy/monitor.sh:330` | ✅（白名单内） | PG 连接数检查用；回退 `DATABASE_URL` |
| `ICHAT_PYTHON` | `/opt/ichat/.venv/bin/python` | `deploy/monitor.sh:334` | ❌（不在白名单） | URL 解析解释器 |
| `RESTORE_ENV_FILE` / `ICHAT_DB_URL` / `ICHAT_MEDIA_DIR` / `ICHAT_SKIP_SERVICE` / `ICHAT_ASSUME_YES` | 见 `deploy/restore.sh:9-15` | `deploy/restore.sh` | — | 演练注入点（**仅 `restore.sh` 接受**，见 §6 / §9.2） |

> **两侧的 `.env` 语义不同，别混**：
> - `deploy.sh` 侧：`set -a; source "$APP_DIR/.env"; set +a`（`deploy/deploy.sh:339`）——**真 source**，
>   `.env` 里任何变量都会进子进程环境（包括 `manage.py` 与 `deploy/smoke.sh`）。正因为它真 source，
>   身份键必须在 source **之前**被拒绝并被快照覆盖（§3.2a）。
> - `monitor.sh` 侧：**不 source**，按 `ENV_FILE_KEYS` 白名单逐行解析（清单 `deploy/monitor.sh:74-88`、
>   取值 `strip_env_value` `:105-139`、装载 `:141-168`、调用 `:169`），并用 `printf -v` 赋值（`:162`），
>   **绝不 `eval`**。只有清单里的名字生效，调用方环境优先（`:161`）。
>   `deploy/tests/test_runbook_contract.py` 双向校验"文档写了 ↔ 白名单有"（12 条）。

### 2.3 `.env` 的取值语法（F-05）

`monitor.sh` 支持的语法**刻意与 systemd 的 `EnvironmentFile` 对齐**——因为 systemd 读的是同一个文件：
一个 systemd 认、monitor 不认的写法，会让阈值静默回落成缺省值（`deploy/monitor.sh:94-96` 的注释即此动机）。

| 写法 | 结果 | 行 |
| --- | --- | --- |
| `KEY=value` | `value` | `deploy/monitor.sh:97` |
| `KEY="value"` | 去掉一层双引号，值**原样**采用 | `deploy/monitor.sh:98` |
| `KEY='value'` | 去掉一层单引号，**不做任何展开** | `deploy/monitor.sh:99` |
| `KEY=value  # comment` | 注释被剥掉，**仅当 `#` 前面是空白且不在引号内** | `deploy/monitor.sh:100-102` |
| `KEY=pa#ssword` | `#` 紧贴值时是**数据**，不当地注释 | `deploy/monitor.sh:119-124` |

- **不支持**变量展开（`$OTHER`）、**不支持**命令替换（``` `cmd` ``` 或 `$(cmd)`）、**不支持**跨行续写。
- 这是刻意的：取值经 `strip_env_value` 逐字符处理后用 `printf -v` 赋值（`deploy/monitor.sh:103-104`
  的注释写明"never passed to eval, so a hostile line stays data"），所以 `.env` 里写什么都不会被执行。
- 覆盖这些语法的测试：`deploy/tests/test_env_syntax_and_redis.py` 的 `EnvFileSyntaxTests` 7 条
  （裸值 / 双引号 / 单引号 / 行尾注释 / 引号+注释 / `#` 在值内 / **绝不求值**）。
- `KEY="1"` 这类写法在 `deploy.sh` 侧由 `source` 自己处理（bash 语义），无需本文说明。

---

## 3. 发布、回滚、审批、冒烟门禁：顺序与失败语义

### 3.1 顺序（`deploy/deploy.sh` 主流程）

```text
[0]  稳定副本 re-exec              deploy/deploy.sh:54-58
[1]  参数解析                      deploy/deploy.sh:64-72    --branch/--commit/--status/--verbose
[2]  --status 短路                 deploy/deploy.sh:153-177  只读日志，不碰服务
[3]  审批门禁（在任何 git 之前）    deploy/deploy.sh:189-231  见 3.2
[4]  .env 存在性                   deploy/deploy.sh:235-238
[5]  ★ 身份主权校验（.env 扫描）    deploy/deploy.sh:265-312  见 3.2a；不通过即 exit 2
[6]  ★ 冻结审计身份 + 快照         deploy/deploy.sh:318-337  见 3.2a；在 source 之前
[7]  source .env + 回填调用方值    deploy/deploy.sh:339-354
[8]  解析身份 + 5 道安全校验       deploy/deploy.sh:360-497  见 3.2b
[9]  取码                          deploy/deploy.sh:499-513  clone/fetch/checkout --force
[10] venv + 依赖                   deploy/deploy.sh:513-516
[11] media 目录                    deploy/deploy.sh:518-522
[12] manage.py check + 迁移检查    deploy/deploy.sh:524-528
[13] migrate + collectstatic      deploy/deploy.sh:530-534
[14] 重启服务                     deploy/deploy.sh:534-537
[15] readiness 门禁               deploy/deploy.sh:538-572
[16] 冒烟门禁                     deploy/deploy.sh:574-616  见 3.4
[17] 审计记录（只用冻结值）        deploy/deploy.sh:631-648  见 3.2a
[18] 状态簿记                     deploy/deploy.sh:650-676
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

### 3.2a 部署身份主权：被部署的树不得自我授权（F-01 / F-02 / F-03 / F-04）

**这是本轮最重要的安全语义，上一版文档完全没有。** 三句话概括：

1. **`$APP_DIR/.env` 里出现任何身份键 → 整个部署 exit 2，且一条 git 命令都不会执行。**
2. **审计字段（actor / via / from / approval）在 `source .env` 之前就冻结**，日志只写冻结值。
3. **每条路径 / origin 都先规范化再比较**，`//`、`/.`、`../` 绕不过去。

#### 为什么

`/opt/ichat/repo` 就是 systemd 正在运行的那棵树（`deploy/deploy.sh:200-204` 注释已说明"审批必须早于 git"）。
如果 `.env` 能决定"重启哪个单元、探测哪个端口、以谁的名义记账"，那么**一个被投毒的仓库就等于
自己给自己签了发布令**：攻击者只要把 `ICHAT_SERVICE_NAME` 写进 `.env`，就能让下一次常规发布
去重启另一个单元；把 `ICHAT_APPROVAL=#177` 写进去，就能让无审批的发布在日志里长成有审批的样子。
所以这一组键被划为**主权（sovereign）**：只认调用方，文件里的出现本身就是拒绝理由。
`deploy/deploy.sh:252-258` 的注释原文即此意（"must never be able to authorise its own deployment"）。

#### SOVEREIGN_KEYS 的准确清单（13 个）

定义在 `deploy/deploy.sh:265-279`，逐字如下（**这是脚本里唯一的一份清单**，`reject_sovereign_in_env`
与回填逻辑都只引用它）：

| # | 键 | 它决定什么 |
| --- | --- | --- |
| 1 | `ICHAT_APP_DIR` | 读哪个 `.env`、改哪棵树 |
| 2 | `ICHAT_PROD_APP_DIR` | 哪棵树才叫"生产"（决定下面 5 道校验是否生效） |
| 3 | `ICHAT_SERVICE_NAME` | 重启哪个 systemd 单元 |
| 4 | `ICHAT_SERVICE_USER` | 以哪个用户建 media |
| 5 | `ICHAT_MEDIA_DIR` | 写哪个 media 根 |
| 6 | `ICHAT_TLS_PORT` | readiness 主探测端口 |
| 7 | `ICHAT_HTTP_PORT` | readiness 回落端口 |
| 8 | `ICHAT_READY_ORIGIN` | 显式主探测 origin（可绕过端口号） |
| 9 | `ICHAT_LOOPBACK_ORIGIN` | 显式回落 origin（同上） |
| 10 | `ICHAT_DEPLOYER` | 审计行里的 `actor` |
| 11 | `ICHAT_APPROVAL` | 审计行里的 `approval`，且是门禁的判据 |
| 12 | `ICHAT_REQUIRE_APPROVAL` | 门禁本身开不开 |
| 13 | `ICHAT_SKIP_SMOKE` | 冒烟门禁开不开 |

> 注意 #8/#9 的必要性：**只查端口号是不够的**——`ICHAT_READY_ORIGIN=https://127.0.0.1:8443`
> 会让端口变量看起来正常、探测却指向生产（F-03）。因此 origin 守卫另外独立检查
> （`deploy/deploy.sh:483-494`，见 3.2b 第 5 条）。

#### 检测与拒绝

`reject_sovereign_in_env()`（`deploy/deploy.sh:284-311`）逐行扫描 `.env`：跳过注释与无 `=` 的行，
取 `=` 左边的键名、裁掉空白，与清单逐个比对；收集到任意命中就打印**命中清单**、可执行的修复命令
（`sudo $BIN_COPY ICHAT_SERVICE_NAME=<value> …`）与 `Nothing was changed: no fetch, no checkout, no restart.`，
然后 **exit 2**（`:298-310`）。它只**读**文件，不 `source`、不 `eval`，恶意行只是数据。

调用点在 `deploy/deploy.sh:312`，位置很关键：在 `.env` 存在性检查（`:235`）之后、**在 `source`（`:339`）
之前、在取码（`:499`）之前**。所以拒绝时：**没有 fetch、没有 checkout、没有 restart、日志无新行**。

#### 优先级与"身份只认调用方"的例外

| 键类 | 优先级 | 说明 |
| --- | --- | --- |
| 身份/主权键（13 个） | **只有调用方环境**，其次生产缺省 | `.env` 里的出现 = 拒绝，不是"忽略"——静默忽略会掩盖一个恶意或被改坏的树（`deploy/deploy.sh:256-258`） |
| 其余键（应用配置：`SMOKE_*`、`DJANGO_*`、`DATABASE_URL`、阈值…） | 调用方 > `.env` > 缺省 | 正常继承 |

实现上分两层（纵深防御）：
1. **第一层**：`reject_sovereign_in_env` 拒绝（`:312`）；
2. **第二层**：即便如此，仍在 `source` 之前把 13 个主权值快照进 `_SOV_*`（`deploy/deploy.sh:318-330`），
   `source` 之后无条件回填（`:342-354`）。这样即使有人把清单改漏了一个键，`source` 也无法在
   **任何时刻**改动这些变量（`:314-317` 的注释明写"second line of defence"）。
   注意 `APP_DIR` 本身也在快照里（`:330`、`:342`），因为"读哪个 `.env`"不能由被读的文件决定。

**唯一会从 `.env` 继承的身份类值**：`ICHAT_SERVICE_NAME` 在 **`monitor.sh` 侧**仍可从 `.env` 读
（`deploy/monitor.sh:85` 的白名单、`:197` 的使用）——因为 monitor 是只读巡检、不重启任何东西，
被投毒的 `.env` 最多让监控去看另一个单元的日志。这与 `deploy.sh` 的严格程度不同，**不是矛盾，是威胁模型不同**。

#### FROZEN_ACTOR / FROZEN_VIA / FROZEN_ORIGIN：审计值必须等于门禁值

`deploy/deploy.sh:335-337` 在 `source` 之前计算并冻结三个值：

```text
FROZEN_ACTOR  = deploy_actor()     # ICHAT_DEPLOYER > SUDO_USER(≠root) > USER/LOGNAME，过 sanitise
FROZEN_VIA    = deploy_via()       # 有 SUDO_USER 即 sudo，否则 direct
FROZEN_ORIGIN = deploy_origin()    # SSH_CONNECTION 第一段，否则 local-console
```

审计行只写这些冻结值（`deploy/deploy.sh:638-648`，参数是 `"$FROZEN_ACTOR"` `"$FROZEN_VIA"`
`"$FROZEN_ORIGIN"`），**绝不在写日志时重算**——`:632-637` 的注释给出理由：重算会让被部署的树
改写"是谁发布的、依据哪个审批"，也会让**记录的审批**与**门禁校验的审批**不一致。
`APPROVAL` 同理：它在门禁处由调用方环境算出（`:205`），`:355-358` 明确写着 **"NOT recomputed here:
the value the gate validated is the value the log records"**。

这一点正是 #177 的验收点（"可追踪发布人、版本、时间"）——**门禁判据与审计记录必然是同一个值**，
否则日志再漂亮也无法证明发布被谁批准过。覆盖它的测试是
`deploy/tests/test_deploy_sovereign.py` 的 `test_env_cannot_forge_the_recorded_actor`、
`test_env_cannot_forge_the_recorded_approval`、`test_recorded_actor_and_approval_are_the_callers`（17 条模块）。

### 3.2b 安装身份解析与 5 道 fail-closed 校验（OPS-01 / OPS-02 / F-03 / F-04 的修复点）

`deploy/deploy.sh:360-497` 是解析与守卫，顺序**必须**保持：

1. **解析默认值**（`:360-367`）：`PROD_SERVICE_NAME/PROD_SERVICE_USER/PROD_MEDIA_DIR`，再由回填后的
   `ICHAT_*` 算出 `MEDIA_DIR`/`SERVICE_NAME`/`SERVICE_USER`/`SERVICE_UNIT`。
2. **端口解析**（`:369-382`）：`resolve_port` 校验 1–65535，**不直接信任字符串**（拼错 → exit 2，不是 port 0）。
3. **origin 派生**（`:384-388`）：`TLS_ORIGIN`/`LOOPBACK_ORIGIN` 由同一份解析结果得出。
4. **路径规范化工具**（`:390-421`）：`normalise_dir`（折叠重复分隔符、去 `.`、解 `..`，纯词法）
   与 `paths_overlap`（相等或互为祖先前缀，带尾 `/` 以保证组件级）。
5. **5 道拒绝**（仅当 `normalise_dir "$APP_DIR" != PROD_APP_NORM` 时生效，`:431-495`）：

   | # | 拒绝条件 | 行 | 退出码 |
   | --- | --- | --- | --- |
   | 1 | 非生产树却解析成生产单元 `ichat.service` | `deploy/deploy.sh:433-444` | 2 |
   | 2 | 非生产树却用生产用户建 media | `deploy/deploy.sh:445-451` | 2 |
   | 3 | 非生产树却用生产 media 根（**规范化后**比较） | `deploy/deploy.sh:452-458` | 2 |
   | 4 | 非生产树却探测生产端口 8443/8000 | `deploy/deploy.sh:459-466` | 2 |
   | 5 | **非生产树却通过显式 origin 指向生产端口**（8443/8000） | `deploy/deploy.sh:467-494` | 2 |

   五条提示都以 "Nothing was changed: no fetch, no checkout, no restart." 结尾，即**在任何破坏性动作之前**退出。
   第 5 条是 F-03：只查 `ICHAT_TLS_PORT` 不够，`ICHAT_READY_ORIGIN=https://127.0.0.1:8443`
   能绕过前四条。`origin_port()`（`:471-482`）把无端口 URL 按 scheme 映射成 443/80。
6. 打印解析结果（`:496-497`）后，才进入取码。

> 覆盖情况：`deploy/tests/test_deploy_config.py`（8 条）负责"身份只走命令行"与端口解析
> （含 `test_identity_on_the_command_line_restarts_the_staging_unit`、
> `test_explicit_caller_identity_is_used_verbatim`、`test_incomplete_staging_identity_is_refused_before_any_restart`、
> `test_staging_guard_names_the_missing_media_root`、`test_staging_guard_names_the_production_ports_it_would_probe`、
> `test_staging_readiness_probes_the_staging_ports`、`test_production_install_keeps_the_production_ports`、
> `test_bad_port_is_a_config_error_not_port_zero`）；
> `deploy/tests/test_deploy_sovereign.py`（17 条）负责主权键、审计冻结、
> **MediaRootSpellingTests 6 条**（`//`、`/.`、`../`、`../`+多分隔符都能被拒）与
> **ProbeOriginGuardTests 3 条**（origin 指向 8443/8000 被拒，指向 8444 通过）。

### 3.3 readiness 门禁

`deploy/deploy.sh:538-572`：最多 30 轮，每轮先探 `$TLS_ORIGIN/health/ready/`（带 `-k` 与
`Host: $DJANGO_ALLOWED_HOSTS 第一段`），body 不含 `"status": "ok"` 时再探 `$LOOPBACK_ORIGIN/health/ready/`
（`:552-560`）。**两条路径都断言 JSON body**，因为 301/400 的 body 能骗过 `curl -f`。30 轮全失败 → exit 1，
提示里同时给出两个被探测的 origin（`:568-571`）。

> `DEBUG=False` 时 `SECURE_REDIRECT_EXEMPT = [r'^health/']`（`ichat_pro/settings.py:278`）是 http 回落能成立的
> **唯一**原因；它只豁免 health，所以回落不能用于冒烟（见 3.4）。

### 3.4 冒烟门禁（T23 / #204）

`deploy/deploy.sh:574-616` 六种分支，**每种都被记录**：

| 条件 | 行为 | 日志 `smoke=` | 行 |
| --- | --- | --- | --- |
| `ICHAT_SKIP_SMOKE=1` | WARNING 后跳过 | `skipped` | `deploy/deploy.sh:579-580` |
| 目标树无 `deploy/smoke.sh` 且 `IS_ROLLBACK != 1` | **exit 1，不记录** | 不写 | `deploy/deploy.sh:588-595` |
| 目标树无 `deploy/smoke.sh` 且是回滚 | WARNING，放行 | `skipped-no-script` | `deploy/deploy.sh:596-598` |
| 有脚本但缺 `SMOKE_USERNAME`/`SMOKE_PASSWORD` | **exit 1，不记录** | 不写 | `deploy/deploy.sh:600-606` |
| 有脚本、有凭据、通过 | 继续 | `passed` | `deploy/deploy.sh:607-618` |
| 有脚本、有凭据、失败 | **exit 1，不记录** | 不写 | `deploy/deploy.sh:610-615` |

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
  由 `deploy.sh` 传入（`deploy/deploy.sh:608-609`），staging 因此探测 8444/8001 而不是生产的 8443/8000。
  缺省值仍是生产，单独运行 `deploy/smoke.sh` 行为不变。
- **JSON 用解释器解析**（`deploy/smoke.sh:299-313`），不是 grep 键名——"200 的错误页里恰好含这个词"不能蒙混。
- **CSRF 的 Referer 重建**（`deploy/smoke.sh:147-166`）：`HOST_HEADER` 非空时用 `Host` 头 + `BASE_URL`
  的端口拼出 Django 实际看到的 origin，否则 `Referer` 与请求 host/scheme 不匹配会 403。

失败语义统一为：**服务已重启（旧进程没了），但这次发布不写日志**。回退用
`sudo /opt/ichat/bin/deploy.sh --commit <上一个 SHA>`（提示见 `deploy/deploy.sh:613`）。

### 3.5 回滚与审计

- 回滚：`--commit <40-hex>` → `git checkout --force <sha>`（`deploy/deploy.sh:505-506`），**停在那个提交**，
  不 reset 回 `origin/main`。恢复前向：重跑不带 `--commit` 的发布（仍需审批）。
- 审计行（`deploy/deploy.sh:638-648`）：

  ```text
  ts=<UTC ISO8601> sha=<40-hex> target=<branch[@commit]> kind=<deploy|rollback>
  actor=<who> via=<sudo|direct> from=<host|local-console> smoke=<passed|skipped|skipped-no-script> approval=<ref|none>
  ```

  `actor`/`via`/`from` **只写 §3.2a 冻结的那三个值**（`FROZEN_ACTOR`/`FROZEN_VIA`/`FROZEN_ORIGIN`），
  `approval` 只写门禁算出的 `APPROVAL`（`:355-358` 明确"NOT recomputed"）。
  `from` 的注意点：`SSH_CONNECTION` 在 sudo `env_reset` 下常被丢弃，远程也可能记成 `local-console`；
  要准确来源用 `sudo -E`。
- **"在跑什么"的权威答案**：`DEPLOYMENTS.log` 最后一条（`deploy/deploy.sh:158-162` 的注释即此意）。
  `git -C repo rev-parse HEAD` 只是参考（手工 checkout 会改它而不产生日志）。
- `.deploy-state/current` 与 `previous`（`deploy/deploy.sh:650-676`）：写失败只 WARNING，不把成功的发布
  判成失败；`previous` 取自**状态文件的旧值**，不是 git reflog。

---

## 4. 监控检查项与阈值（`deploy/monitor.sh`）

cron（root）：`*/5 * * * * /opt/ichat/repo/deploy/monitor.sh >> /var/log/ichat-monitor.log 2>&1`
（脚本已是 `100755`，**可直接执行**；见 `deploy/runbook.md:92`）

| # | 检查 | 触发 | key | 行 |
| --- | --- | --- | --- | --- |
| 1 | readiness | `/health/ready/` **连续** ≥ `ICHAT_READY_MAX_FAILS`（缺省 3）次失败；单次只计数 | `ready` | `deploy/monitor.sh:274-299` |
| 2 | 磁盘 | PG 数据盘（`ICHAT_PG_DATA_DIR`，不存在则 `/var`）使用率 ≥ `ICHAT_DISK_PCT`（85） | `disk` | `deploy/monitor.sh:302-364`（含 `load_pg_env`） |
| 3 | PG 连接数 | `SELECT count(*) FROM pg_stat_activity` ≥ `ICHAT_PG_MAX_CONN`（40） | `pg-conn` | `deploy/monitor.sh:366-387` |
| 4 | Redis 内存 | `used_memory` ≥ `ICHAT_REDIS_MAX_MB`（512 MB） | `redis-mem` | `deploy/monitor.sh:389-406` |
| 5 | 证书 | `ICHAT_LETLS_DIR` 下**每一条** lineage 的 `cert.pem` 剩余 < `ICHAT_CERT_MIN_DAYS`（14）天 | `cert` | `deploy/monitor.sh:412-438` |
| 6 | journal | `journalctl -u $JOURNAL_UNIT --since "-10 min" -p err` 行数（减 2 行 banner）≥ 10 | `journal` | `deploy/monitor.sh:441-465` |

**"查不到"≠"一切正常"**：`check_failed` 把检查名收进 `CHECK_FAILURES`（`deploy/monitor.sh:211-214`），
任何一项跑不成都会在结尾触发 `monitor` 告警并 **exit 1**（`deploy/monitor.sh:480-486`）。触发点：
`df` 缺失/读不到（`:305-314`）、`psql` 缺失（`:367-370`）、`DATABASE_URL` 不可用（`:371-374`）、
查询失败（`:376-380`）、`redis-cli` 缺失（`:390-393`）、`journalctl` 缺失（`:442-445`）、
**`journalctl` 查询非零退出**（`:451-456`）、证书目录为空或不可读（`:417-437`）、
状态目录不可写导致"连续失败"无法累计（`:287-292`）。

**告警与限频**（`deploy/monitor.sh:231-271`）：

- 同一 key 两次告警间隔 ≥ `ICHAT_ALERT_MIN_SECS`（1800 秒）；限频时间戳**只在 webhook 投递成功后**写入
  （`:259`），投递失败下一轮重试（`:260-264`）；无 webhook（日志模式）也写时间戳（`:268`）。
- 告警同时进 stderr 与 `logger -t ichat-monitor`（`:253-254`），webhook 用
  `{"text": "[ichat <key>] ..."}`（`:256-258`），`json_escape` 把换行/回车换成空格（`:232-239`）。
- 自检：`ICHAT_ALERT_TEST=1 /opt/ichat/repo/deploy/monitor.sh`（`:468-471`）。
- **不做**的事：不重启任何服务、不碰 443/2096/25383、不读聊天数据。

> **`monitor.sh` 与 `deploy.sh` 的严格度不同**：`JOURNAL_UNIT=${ICHAT_SERVICE_NAME:-ichat}`
> （`deploy/monitor.sh:197`），而这个值**可以从 `.env` 来**（白名单 `deploy/monitor.sh:85`）——
> `deploy.sh` 侧它是主权键。这不是遗漏：monitor 只读巡检、不重启任何东西，被投毒的 `.env`
> 最多让监控去看别的单元日志；而 `deploy.sh` 一旦被投毒就会重启错的单元。威胁模型不同，规则不同。

---

## 5. staging 隔离检查（`deploy/staging/check_isolation.sh`）

```bash
sudo deploy/staging/check_isolation.sh \
    --prod-env /opt/ichat/.env --staging-env /opt/ichat-staging/.env [--check-data]
```

脚本已是 `100755`，**可直接执行**（`deploy/runbook.md:152` 即这样写）。
退出码：`0` 全隔离；`1` 有 violation；`2` 参数错或文件不可读
（`deploy/staging/check_isolation.sh:23-30`、`:380-386`）。

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
| 活库（`--check-data`） | 真连两库读 `current_user` 并比较 | violation / UNVERIFIED | `:335-354` |
| 活库（`--check-data`） | 真连两 Redis，按 `-n <URL 库号> client info` 读**实际**库并比较 | violation / UNVERIFIED | `:356-377` |

### 5.2 `--check-data` 的真实行为（OPS-08 / OPS-09 / OPS-10 + F-07 的修复点）

现在是**真连接**，不是比字符串。两层实现：

1. **`live_psql_role`**（`deploy/staging/check_isolation.sh:267-286`）：用 `PGPASSWORD`/`PGUSER`/`PGHOST`/
   `PGPORT`/`PGDATABASE` 环境变量执行 `psql -X -qAt -c 'SELECT current_user;'`（`:282-284`），
   读**服务端确认的登录角色**，不读 URL。
2. **`live_redis_db`**（`:288-333`）：先用 URL 尾部的库号作 `-n`（`:302-310`，**没有 `-n` 时每次都连 db 0**，
   会把两个正确隔离的实例都读成 0 而误判——F-07 的前半），再 `redis-cli … -n "$from_url" client info`
   （`:316-317`），用 `grep -o 'db=[0-9][0-9]*'`（`:318`）从**单行输出的中间**取 `db=`——
   上一版用 `sed -n 's/^db=...'` 只匹配测试桩的形状，对真实服务器永远失败（F-07 的后半）。
   拿不到 `db=`（ACL 限制等）→ `return 1`（`:320-324`）；服务器报的库号与 URL 不符 → 返回
   `mismatch:<wanted>:<actual>`（`:325-330`），由调用方报 UNVERIFIED。

四条失败路径**全部 fail-closed**（`violation` + 非零退出 = UNVERIFIED，绝不静默放行）：

| 情况 | 判定 | 行 |
| --- | --- | --- |
| `psql` 未安装 | `live database: … UNVERIFIED` | `:337-338` |
| `psql` 在但认证/查询失败（任一侧） | `… UNVERIFIED` | `:345-348` |
| `redis-cli` 未安装 | `live redis: … UNVERIFIED` | `:356-357` |
| `redis-cli` 查询失败 / 解析不出 `db=` / 报的库与 URL 不符 | `… UNVERIFIED`（mismatch 单独成句） | `:366-371` |

> **与上一版（pin `2905473`）相反**：那时 `--check-data` 只说"live database names differ"却从不连接（OPS-08）。
> 现在它**真的连**；但**仍然没有真实 PG/Redis 实测证据**（§10）——代码路径与 34 条隔离测试
> （其中 `LiveDataCheckTests` 7 条，`deploy/tests/test_isolation_check.py:364-455`）不等于实机验收。
> 静态 URL 比较只覆盖秘密/配置两段，不能替代活库。

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
| DB 密码（监控） | 解析成 `PGPASSWORD` 环境变量，`psql` 不收 URL 参数 | `deploy/monitor.sh:362`、`:376` |
| DB URL（监控解析器） | **走 stdin**，不在 argv（OPS-07 的修复点） | `deploy/monitor.sh:348-358`（`printf '%s\n' "$url" \| python -c …`） |
| DB URL（恢复） | `psql "$DB_URL"` —— **仍会进 argv** | `deploy/restore.sh:42-44`，见 §9.2 的 DOC-NEW-1 |
| `DJANGO_SECRET_KEY` 等 | 只在 `.env`（600），`deploy.sh` 只 `source`，不 echo | `deploy/deploy.sh:339` |
| 审计日志 | 字段过 `sanitise` 白名单（`deploy/deploy.sh:79-88`），且**只用冻结值**（§3.2a） | `deploy/deploy.sh:638-648` |
| 告警消息 | `json_escape` 去控制字符；不含密码/cookie/载荷 | `deploy/monitor.sh:232-239` |
| 隔离报告 | 秘密只以 SHA-256 前缀出现；角色/密码按 digest 报告 | `deploy/staging/check_isolation.sh:68`、`:130`、`:140` |

**监控状态目录与日志**没有秘密；`DEPLOYMENTS.log` 只有 `sha/target/kind/actor/via/from/smoke/approval`。

> **`deploy.sh` 侧的 `.env` 现在有一个额外好处**：因为 `SOVEREIGN_KEYS` 会被扫描拒绝、13 个值又被
> `_SOV_*` 覆盖（§3.2a），被投毒的 `.env` 既不能改部署目标，也不能改审计口径——
> 但**它仍然能改应用配置**（`SMOKE_*`、`DJANGO_*`、`DATABASE_URL`）。这是有意保留的：那些键描述的是
> 应用而不是部署，且改它们需要仓库写权限，与"能改 `.env`"是同一信任级。

---

## 8. 常用命令速查

```bash
# —— 发布（生产：身份走缺省，只需审批）——
sudo ICHAT_APPROVAL='#177' /opt/ichat/bin/deploy.sh          # 发布 origin/main
sudo ICHAT_APPROVAL='#177' /opt/ichat/bin/deploy.sh --branch main
sudo ICHAT_REQUIRE_APPROVAL=0 /opt/ichat/bin/deploy.sh       # 紧急口子（记 approval=none）

# —— 发布（staging：身份必须走命令行，写进 .env 会被 exit 2 拒绝）——
sudo ICHAT_APP_DIR=/opt/ichat-staging \
     ICHAT_SERVICE_NAME=ichat-staging ICHAT_SERVICE_USER=ichat-staging \
     ICHAT_MEDIA_DIR=/var/lib/ichat-staging/media \
     ICHAT_TLS_PORT=8444 ICHAT_HTTP_PORT=8001 \
     ICHAT_REQUIRE_APPROVAL=0 \
     /opt/ichat-staging/bin/deploy.sh --branch main

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

# —— 运维测试（仓根）——
.\.venv\Scripts\python.exe manage.py test deploy.tests --parallel 1 -v 2   # 171 条（10 模块）
.\.venv\Scripts\python.exe manage.py test --parallel 1                     # 默认发现 575 条（含这 171）
# 旧的"五模块"子集仍是 117 条（见下方口径说明）
.\.venv\Scripts\python.exe manage.py test \
  deploy.tests.test_deploy_gate deploy.tests.test_isolation_check deploy.tests.test_monitor \
  deploy.tests.test_release_audit deploy.tests.test_smoke_gate --parallel 1 -v 2
```

**测试条数（pin `a0e1faf`，按 `def test_` 计，共 10 个模块 / 171 条）**：

| 模块 | 条数 | 覆盖 |
| --- | ---: | --- |
| `test_deploy_config` | 8 | 身份只走命令行、端口解析、5 道守卫 |
| `test_deploy_gate` | 9 | 冒烟门禁六分支的日志语义 |
| `test_deploy_sovereign` | **17** | **主权键 8 条 + origin 守卫 3 条 + 媒体路径拼写 6 条**（F-01/02/03/04） |
| `test_env_syntax_and_redis` | **12** | **`.env` 取值语法 7 条 + `CLIENT INFO` 解析 5 条**（F-05/F-07） |
| `test_exec_bits` | 5 | 直接执行脚本必须 100755（OPS-04） |
| `test_isolation_check` | 34 | 隔离 5 段 + 活库（OPS-08/09/10） |
| `test_monitor` | 31 | 六项检查 + 不可观测路径（OPS-05/06/07） |
| `test_release_audit` | 27 | 审批门禁、审计字段、状态跟踪（#177） |
| `test_runbook_contract` | **12** | 文档↔`ENV_FILE_KEYS` 双向校验、runbook 描述的失败语义 |
| `test_smoke_gate` | 16 | 冒烟 11 项检查 |
| **合计** | **171** | |

**三种口径，别混成一个数字**：

| 口径 | 命令 | 实测条数 |
| --- | --- | ---: |
| 全部运维测试 | `manage.py test deploy.tests --parallel 1` | **171**（10 模块） |
| 默认全量发现 | `manage.py test --parallel 1` | **575**（其中 `deploy.*` 就是那 171） |
| 旧的"五模块"子集 | `deploy.tests.test_deploy_gate` + `test_isolation_check` + `test_monitor` + `test_release_audit` + `test_smoke_gate` | **117** |

在本 pin 的树上用 `DiscoverRunner.build_suite()` 实测（`default_total` 与 `deploy_label`）：

```text
default_total 575
deploy_total 171
deploy_modules 10
```

即 `manage.py test` 的默认 suite 已经收进全部 171 条，**"必须显式点名五个模块"的旧要求作废**；
而"五模块 117 条"只是**历史子集**，它漏掉 `test_deploy_config`(8)、`test_deploy_sovereign`(17)、
`test_env_syntax_and_redis`(12)、`test_exec_bits`(5)、`test_runbook_contract`(12) 共 54 条——
**新代码的回归恰好全在这 54 条里**，所以不要再用五模块子集当"运维测试全绿"的证据。
仍建议显式点名 `deploy.tests`，唯一理由是**独立性**：默认发现受 `settings` 与 runner 配置变动影响。

**bash 缺失时是失败，不是跳过（F-06 / F-08）**：`deploy/tests/support.py:3-10` 明确写着
"the honest outcome is a FAILED test that says so: `unittest.skip` would report"，
`find_bash()`（`deploy/tests/support.py:32-57`）在找不到可用 shell 时 `raise BashUnavailable`
（`support.py:24-30` 说明"Deliberately *not* a `SkipTest`"）。查找顺序是
**`ICHAT_TEST_BASH`（显式全路径）→ `shutil.which("bash")`（PATH）**（`support.py:34`）；
给了 `ICHAT_TEST_BASH` 但它不是文件/不能执行，也会 `raise`（`support.py:43`、`:50-51`），不会回退到 PATH。
所以 CI 里 bash 装在 PATH 之外时，要显式设 `ICHAT_TEST_BASH=/usr/bin/bash`。
受影响的是**9 个模块**（`test_deploy_config`、`test_deploy_gate`、`test_deploy_sovereign`、
`test_env_syntax_and_redis`、`test_isolation_check`、`test_monitor`、`test_release_audit`、`test_smoke_gate`），
只有 `test_exec_bits` 与 `test_runbook_contract` 是纯文本检查、不需要 shell。
10 个模块里已无任何 `@skipUnless`/`skipIf`（`test_release_audit.py` 的 `skipUnless` 由 `328e079` 移除）。

---

## 9. 已知问题与审查发现状态

### 9.1 已关闭（以 `a0e1faf` 为准；行号见各条）

| 编号 | 原现象 | 关闭提交 | 现在的证据 |
| --- | --- | --- | --- |
| OPS-01 | 只写在 staging `.env` 的服务身份在 `source` 前已回落生产默认值 → 重启生产单元 / 写生产 media | `8308dd5` | 快照→source→回填（`deploy/deploy.sh:257-269`）；解析（`:271-299`）；4 道拒绝（`:318-349`）；`deploy/tests/test_deploy_config.py` 8 条 |
| OPS-02 | staging 重启后探针仍请求生产 8443/8000 | `d6e2dcd` + `8308dd5` | 端口解析（`deploy/deploy.sh:292-293`）、origin 派生（`:298-299`）、readiness 用解析值（`:407-413`）、smoke 传 origin（`:462-463`）、`deploy/smoke.sh:106-112` |
| OPS-03 | 前向发布缺 `smoke.sh` 被历史回滚例外放行 | `2905473` | `deploy/deploy.sh:442-449` exit 1 不记录；`test_deploy_gate.py::test_forward_release_without_smoke_sh_fails_closed` |
| OPS-04 | `monitor.sh` / `check_isolation.sh` 提交为 100644，runbook 却直接执行 | `05bb0c6` | `git ls-tree a0e1faf deploy/`：`deploy.sh`/`smoke.sh`/`monitor.sh`/`check_isolation.sh`/`backup.sh`/`restore.sh`/`restore_drill.sh` 全 **100755**；`deploy/tests/test_exec_bits.py` 5 条钉住 |
| OPS-05 | 写在 `.env` 的监控阈值不生效 | `753cd9d` | `ENV_FILE_KEYS` 白名单（`deploy/monitor.sh:74-88`）+ 逐行解析（`:93-120`）+ 阈值取解析结果（`:135-140`）；`test_runbook_contract.py` 双向校验文档↔白名单 |
| OPS-06 | `journalctl` 查询失败被吞成"0 错误" | `753cd9d` | 先取 `rc`（`deploy/monitor.sh:398-408`），非 0 → `check_failed "journal"` + 结尾 exit 1（`:432-438`） |
| OPS-07 | 完整 DATABASE_URL（含密码）进辅助 python 的 argv | `753cd9d` | URL 从 **stdin** 进（`deploy/monitor.sh:300-310`），argv 里没有 URL |
| OPS-08 | `--check-data` 不连接任何库，工具缺失也静默跳过 | `82641c2` | `live_psql_role`（`:267-286`）、`live_redis_db`（`:288-303`）、UNVERIFIED→violation（`:305-343`） |
| OPS-09 | 改库名就能带着相同 DB 用户名/密码通过隔离 | `82641c2` | 角色名（`:125-131`）与密码（`:133-141`）分别比较 |
| OPS-10 | static root 只比原值不比嵌套 | `82641c2` | `check_no_nesting`（`:190-213`）对 `DJANGO_STATIC_ROOT` 与 `DJANGO_MEDIA_ROOT` 各调一次（`:214-215`） |
| C11 / QA-01 | 82 条运维测试默认不被 `manage.py test` 收集 | `f999d3f` + `6542cdf` | 默认发现 **575** 条 / `deploy.tests` **171** 条（pin `a0e1faf` 实测；见 §8） |

### 9.2 仍未修的（按 `a0e1faf` 复核后仅剩这一条）

| 编号 | 级别 | 现象 | 精确位置 |
| --- | --- | --- | --- |
| **DOC-NEW-1**（本修复轮新发现，不在 2026-10-24 审查清单里） | MAJOR | 备份/恢复链路**没有**参数化：`APP_DIR`、`MEDIA_DIR`、`VENV`、unit 名全部写死，因此**无法对 staging 做备份或演练**；更严重的是 `restore_drill.sh` 会对**生产** `.env` 与生产库动手 | 见下方逐条 |

**DOC-NEW-1 的逐条位置（pin `a0e1faf`；三个脚本自 `2905473` 起 blob 未变）**：

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
| 真实 PostgreSQL / Redis | 本机没有；**171** 条运维测试用桩与 SQLite，**不是** PG/Redis 集成证据。 |
| `--check-data` 的活库比较 | 代码路径存在且有 7 条测试（`deploy/tests/test_isolation_check.py:364-517` 的 `LiveDataCheckTests`），但**未连真实服务器**。 |
| Linux cron / systemd / nginx | 未运行；shell 侧只做了 `bash -n`。 |
| 171 条是否真进 CI | **未用 CI run 验证**；只在本机实测默认发现 575 / `deploy` 171。 |
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
- 运维测试：manage.py test deploy --parallel 1（**171** 条）；默认发现也含它，但仍建议显式点名。

交付：分支 fix/<卡号>-<短名>、一 finding 一 commit、`manage.py check` +
`makemigrations --check --dry-run` + deploy.tests 全绿，并把证据写进 Issue。
提交前另开一个独立审查子 Agent（见 conventions.md §1）。
```
