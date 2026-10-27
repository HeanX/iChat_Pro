# 覆盖率报告（P4 Test T12 / #193）

<!-- coverage-basis: {"num_branches": 1548, "num_files": 32, "num_statements": 5383} -->

> 本文件由 `scripts/coverage_report.py` 生成，**不要手工编辑**。
> 生成时间：2026-10-27T04:42:18Z
> 代码版本：`cc89c11da4c4`（被测代码所在提交；本报告随其后的提交一并入库，两者 accounts/chat/ichat_pro 代码一致）
> 环境：Python 3.13.12 / Django 6.0.5 / coverage 7.16.2 / DB `sqlite3`
> 用例数：672（skip 2）
> 测试命令：`python -I scripts/coverage_report.py -- --parallel 1`
> 测试结果：退出码 0

## 汇总

| 指标 | 覆盖 / 总数 | 覆盖率 |
| --- | ---: | ---: |
| 语句 | 4034 / 5383 | 74.94% |
| 分支 | 930 / 1548 | 60.08% |
| **合计（语句+分支）** | — | **71.62%** |

## 分模块

| 模块 | 语句 covered/total | 语句 | 分支 covered/total | 分支 | 合计 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `accounts` | 1217/1433 | 84.93% | 212/300 | 70.67% | 82.46% |
| `chat` | 2657/3734 | 71.16% | 699/1208 | 57.86% | 67.91% |
| `ichat_pro` | 160/216 | 74.07% | 19/40 | 47.50% | 69.92% |

## 与需求目标对照

目标取自 `docs/phase4/requirements.md`（NEW-TST-006/007/008）。**#193 的验收包含达到评审目标**；阈值门禁的实现另属 P4 T46（#173），不能据此将 #193 的未达标状态改写为完成。

| 需求 | 指标 | 目标 | 实测 | 结论 |
| --- | --- | ---: | ---: | --- |
| NEW-TST-006 | 核心后端语句覆盖率 | ≥85% | 74.94% | **未达标** |
| NEW-TST-007 | 核心后端分支覆盖率 | ≥75% | 60.08% | **未达标** |
| NEW-TST-008 | 高风险逻辑覆盖 | ≥90% | 未统计 | **未验证**（高风险面清单尚未定义，需 P4 Test T11） |

## 口径说明

- 统计口径见仓库根目录 `.coveragerc`：`branch = True`，来源为 `accounts`/`chat`/`ichat_pro`。
- 已排除：`*/migrations/*`（生成代码）、`*/tests/*`、`*/tests.py`（测试自身）、`manage.py`、`ichat_pro/wsgi.py`、`ichat_pro/asgi.py`。
- `ichat_pro/test_runner.py` 是线上启用的 `TEST_RUNNER`，属产品代码，**计入**统计。
- 仅本机 SQLite 结果；PostgreSQL 并发用例在 SQLite 下静默 skip，因此并发分支的真实覆盖需在 CI 的 PostgreSQL job 中复核。
- 必须用 `--parallel 1`（默认）。`--parallel`（含 `auto`、缩写 `--para=N`）会让测试在子进程运行，coverage 采集不到，脚本会拒绝写快照。
- 带测试标签或过滤选项（`--tag`/`--exclude-tag`/`-k`/`--pattern`，含 `--pat` 这类前缀缩写与 `-kfoo` 这类紧凑写法）或自定义 `--testrunner` 的运行只代表子集，默认不写快照。
- 分支覆盖率原始数见"分模块"表的 covered/total 列，可单独核算。
- 发布前独立核对源码文件集合与语句数；语句/分支/文件分母**一律不得小于**Git 中已发布快照（不接受任何百分比容差，因为发布本身会改写基线，容差会跨发布复利），`--allow-subset` 也不能豁免。统计范围确需缩小时须人工修改本文件的`coverage-basis` 注释并记录理由。
