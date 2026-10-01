# Phase4 基线验证报告（WP00 / P4 Test T01）

> 执行日期：2026-10-01；代码基线：main `9d0a660`。
> 环境：Windows 11（10.0.26200）；`.venv` Python 3.13.12；node v24.14.1；npm 11.13.0；git 2.45.1。
> 本报告只证明当前主分支的自动化检查结果，不代表云端部署、Windows/Android 安装或任何 Phase4 新增能力已完成。

## 执行结果

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| Django 系统检查 | `.venv\Scripts\python.exe manage.py check` | 通过，0 issues |
| 迁移一致性 | `.venv\Scripts\python.exe manage.py makemigrations --check --dry-run` | 通过，No changes detected |
| Django 全量测试 | `.venv\Scripts\python.exe manage.py test` | **347 条全部通过（OK）**，未跳过 |
| 私聊 E2EE 测试 | `node chat/tests/js/private_chat_e2ee.test.js` | all tests passed |
| 群聊 E2EE 测试 | `node chat/tests/js/group_chat_e2ee.test.js` | all tests passed |
| npm 脚本入口 | `npm run test:e2ee` | **通过**（内部串行执行上述两个 node 脚本） |

## 与交接文档口径的修正

[technical-design.md §12](technical-design.md) 记录"本机 npm 启动器找不到 npm-cli.js，`npm run test:e2ee` 无法执行"。本次在同一台机器实测 **npm 11.13.0 的 `npm run` 已恢复正常**（根 `package.json` 的 `test:e2ee` 完整跑通），该环境问题不再存在；后续 WP07 接入 CI Node job 时可直接使用 npm 脚本。同机存在两份 npm 安装（`D:\Program Files\nodejs\` 与 `%APPDATA%\npm\`），PATH 重叠但当前均可执行，如遇诡异行为优先核对实际解析到的 npm 路径。

## 已知限制

- 全量 Django 测试**未生成覆盖率数字**；语句/分支覆盖率统计（NEW-TST-006/007）待 P4 Test T12 建立 coverage 配置后才有首次结果。历史文档中的覆盖率/条数不作为当前基线引用。
- 本机未安装 PostgreSQL/Redis/Docker，以上测试运行于 SQLite + 内存 Channel Layer；真实依赖集成验证按计划走 CI 服务容器（P4 Test T14）。
- 未验证：Windows 安装包、Android APK、公网 HTTPS/WSS、真实 Redis 多进程分发。这些属于 Phase4 待交付项，不在基线范围。

## 基线复核命令

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test
node chat/tests/js/private_chat_e2ee.test.js
node chat/tests/js/group_chat_e2ee.test.js
npm run test:e2ee
```

CI（`.github/workflows/django.yml`）目前包含 Python 3.13 下的 check / makemigrations --check / test 三步，与本报告前三行一致；PR #206 合并时 CI 通过（4m21s）。
