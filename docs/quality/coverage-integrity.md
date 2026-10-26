# 覆盖率快照的发布边界

覆盖率真实性修复不代表 #193 已完成。#193 的验收还要求达到评审目标；语句
85%、分支 75% 和高风险逻辑的证据必须分别核验。工具测试全绿、运维测试全绿和
能够重复生成报告，都不能替代这些目标。

## 运行与发布

在 Git 工作树内使用安装了 requirements-dev.txt 的项目解释器：

```console
python -I scripts/coverage_report.py -- --parallel 1
python -I scripts/coverage_report.py --no-markdown -- --parallel 1
python -m unittest scripts.test_coverage_report scripts.test_check_deploy_collection scripts.test_coverage_security -v
```

优先使用 `-I`，使 Python 从启动时就忽略调用者的 Python 导入路径和用户 site。
普通脚本入口也会先启动隔离解释器，再加载 Django、设置和覆盖率工具，并透传其
退出码。解释器、venv 依赖及仓库源码是受信输入；这不是任意恶意 Python 启动代码
的沙箱。外部启动代码若在脚本执行前直接改文件，已经超出报告工具的运行边界。

测量、文本报告和 JSON 导出统一使用 `python -I -m coverage`、仓库 `.coveragerc`
和**本次运行独有**的临时数据文件。三个步骤不会读取另一轮留下的 `.coverage`。
子进程环境由显式白名单构造，保留操作系统运行所需设置、数据库/Redis 连接设置、
Django 应用设置和 `ICHAT_TEST_BASH`；调用者的 `COVERAGE_*`、`PYTHONHOME` 和任意
外部 `PYTHONPATH` 不会原样传入。解释器自身的 venv 包由隔离解释器加载。

发布前拒绝以下输入，`--allow-subset` 不能豁免：

- 非仓库的 `COVERAGE_RCFILE`，以及除数据文件定位外的其他 `COVERAGE_*` 配置。
  与仓库 `.coveragerc` 解析后相同的取值可用。
- 仓库根和当前解释器 site-packages 之外的 `PYTHONPATH` 项；不依赖其中是否存在
  某个已知钩子名，也不区分模块、包和 zip。相对项统一按子进程的仓库根解析。
- 自定义 settings、非默认 runner、其他发现根和非默认发现 pattern。显式指定与
  默认相同的 runner、仓库路径或 pattern 不算替换。

显式测试标签、tag 和 `-k` 仍可用 `--allow-subset` 发布标明“部分用例”的结果；
它们仍必须通过相同的统计范围核验。`--min-tests 0` 不能授权零用例发布。

## 分母核验与写盘

发布前先从 accounts/chat/ichat_pro 源文件枚举统计范围，并通过 coverage 的
`analysis2` 独立分析语句数。JSON 必须包含完全相同的文件集合，逐文件计数之和
必须等于汇总，覆盖数不能超过分母，百分比必须与原始计数一致且为有限数。

历史参照读取 **Git HEAD 中已提交的报告**，不读取正在被探针替换的工作树快照。
新报告以 `coverage-basis` 元数据记录语句、分支和文件分母；旧报告的语句/分支
取原始计数表，文件数从同一提交的源码树恢复。任何一项相对已发布值下降超过
20% 都拒写。缺少 Git 基线、无效元数据或源码分析失败也拒写，没有命令行跳过开关。
合理的大规模删代码/调整统计口径需先审查并提交基线变更。

写盘使用同目录临时文件、flush/fsync 和原子替换；若目标快照在测量期间已经改变，
拒绝覆盖。拒写保留已有快照，命令返回 2。运行失败保留测试本身的非零退出码。

## 运维测试前提

QA 分支不重复创建 deploy 测试；`check_deploy_collection.py` 需与含
`deploy/__init__.py`、`deploy/tests/` 的运维分支集成后验收。单独的 QA 分支在这些
模块缺失时应失败。收集守卫检查类级和方法级 skip；CI 显式运维执行步骤还检查
实际日志中的 runtime skip（包括 setUpClass/skipTest），存在 skip 即失败。
`ICHAT_TEST_BASH` 可以指定 PATH 外实际可执行的 bash。

本地 SQLite 允许既有 PostgreSQL 专用用例报告未验证；真实 PostgreSQL/Redis
集成结果必须取对应修复后 HEAD 的 CI，不能借用旧提交绿灯。
