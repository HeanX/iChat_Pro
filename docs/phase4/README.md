# Phase4 开发与验收入口

> 更新：2026-10-07；核对基线 f2f60ec。本目录用于当前工作交接，历史首次基线 b3d529f/347 测试见 baseline-report 与 Git 历史。

本学期 Phase4 范围是 Windows/Android 产品化、公网云部署、可靠通信、E2EE 增强与软件测试工程。沿用 P4 T01–T54 与 P4 Test T01–T24 共 78 项任务；早期 Channel/Bot/Agent 的同名 Phase4 是独立远期规划。

## 当前进度

29/78 项闭环：工程 25/54、测试 4/24。云端 M1、可靠消息 M2、Windows P0（T14/T15/T20/T21）以及 P1 品牌 T16、托盘 T17 已有证据；通知 T18 待最终客户端布局复验，文件 T19 待实施。Android 和设备级安全增强未完成，不能宣布全部 P0 完成。

## 阅读顺序

| 文档 | 用途 |
| --- | --- |
| [项目现状](../current-status.md) | 当前部署、CI、验收与限制 |
| [78 项任务](tasks.md) | 原始验收、依赖及 Issue 状态 |
| [114 条需求矩阵](traceability-matrix.md) | 逐条映射和证据边界 |
| [需求基线](requirements.md) | 原始目标与范围冲突处理 |
| [技术方案](technical-design.md) / [现行协议](protocol.md) | 代码入口、已实现契约及待做设计 |
| [Agent 工作包](agent-handoff.md) | 文件边界与交接规则 |
| [风险登记](risk-register.md) / [基线报告](baseline-report.md) | 原缺陷取证、处置与历史测试数据 |
| [完成审计](completion-audit-20261007.md) | 逐卡完成判定与不能关闭的原因 |

## 任务与证据规则

工程 P4 Txx 对应 #127+xx；测试 P4 Test Txx 对应 #181+xx（原卡 #128–#205）。重复卡 #207–#249 已按 Duplicate 关闭，增量证据回评原卡，未删除原任务。

领取前先核对分支、工作区、任务状态和验收缺口。已关闭任务优先回归，不重复领取“重新实现”；共享 chat.js、模型、迁移及依赖串行整合。常规修复在独立分支提交，PR 附实际检查结果。原生、Android、跨实例等缺证据项保持开放。

## 当前验证

基线 CI run 37630595919 的 test、integration、javascript、windows-installer 全绿；Django 双后端各 404 条，Chromium 布局 36/36，NSIS 24/24。生产 f2f60ec 的 live/ready/login 200。历史“npm 损坏”和“没有生产/PG/Redis 验证”只适用于最初交接快照，现行环境已不适用。

尚需完成：#145 最终 UI、#146 文件集成、Android #149–#157、跨端 #164、安全/设备 #165–#170、测试与工程门禁等开放任务。CI 强制合并门禁、覆盖率、登录/业务冒烟阻断和跨实例恢复不能依据现有健康探针宣称完成。
