# 覆盖率报告（P4 Test T12 / #193）

> 本文件由 `scripts/coverage_report.py` 生成，**不要手工编辑**。
> 生成时间：2026-10-17T19:06:41Z
> 代码版本：`20ccc659f797`（提交前生成时该值为工作区 HEAD）
> 环境：Python 3.13.12 / Django 6.0.5 / coverage 7.16.2 / DB `sqlite3`
> 用例数：404（skip 2）
> 测试命令：`python -m coverage run manage.py test --parallel 1`
> 测试结果：退出码 0

## 汇总

| 指标 | 覆盖 / 总数 | 覆盖率 |
| --- | ---: | ---: |
| 语句 | 3636 / 5158 | 70.49% |
| 分支 | 838 / 1538 | 54.49% |
| **合计（语句+分支）** | — | **66.82%** |

## 分模块

| 模块 | 语句 | 分支 | 合计 |
| --- | ---: | ---: | ---: |
| `accounts` | 81.87% | 69.31% | 79.44% |
| `chat` | 66.60% | 51.16% | 62.83% |
| `ichat_pro` | 74.07% | 47.50% | 69.92% |

## 口径说明

- 统计口径见仓库根目录 `.coveragerc`：`branch = True`，来源为 `accounts`/`chat`/`ichat_pro`。
- 已排除：`*/migrations/*`（生成代码）、`*/tests/*`、`*/tests.py`（测试自身）、`manage.py`、`ichat_pro/wsgi.py`、`ichat_pro/asgi.py`。
- `ichat_pro/test_runner.py` 是线上启用的 `TEST_RUNNER`，属产品代码，**计入**统计。
- 仅本机 SQLite 结果；PostgreSQL 并发用例在 SQLite 下静默 skip，因此并发分支的真实覆盖需在 CI 的 PostgreSQL job 中复核。
