# iChat Pro 文档总览

> 更新：2026-10-13；事实快照 2026-10-07。代码基线仍为 f2f60ec：本地 HEAD 与基线之间只有文档改动与测试隔离修复，无产品代码逻辑差异。原始 Phase4 任务 29/78 闭环，仍有 49 项开放。

## 从哪里开始

2026-10-12 新增：[项目交接文档](项目交接文档.md)（接手、环境、代码入口与签收）、[GitHub 协作开发指南](GitHub协作开发指南.md)（成员加入、Issue、分支、PR 与冲突处理）。两份文档依据本地代码整理，生产和验收状态引用 2026-10-07 记录，未重新验证在线状态。

2026-10-13 新增（第四次实验）：[第四次实验-单元测试与代码评审报告](course-testing/%E7%AC%AC%E5%9B%9B%E6%AC%A1%E5%AE%9E%E9%AA%8C-%E5%8D%95%E5%85%83%E6%B5%8B%E8%AF%95%E4%B8%8E%E4%BB%A3%E7%A0%81%E8%AF%84%E5%AE%A1%E6%8A%A5%E5%91%8A.md)、[项目中期完成情况报告](course-testing/%E9%A1%B9%E7%9B%AE%E4%B8%AD%E6%9C%9F%E5%AE%8C%E6%88%90%E6%83%85%E5%86%B5%E6%8A%A5%E5%91%8A.md)，并提供同名 PDF/Word。测试与评审数据为 2026-10-13 本机实测与 Git 历史统计。

| 顺序 | 文档 | 用途 |
| --- | --- | --- |
| 1 | [当前实现与交付状态](current-status.md) | 已完成、待验收、部署/安装包/CI 基线和限制 |
| 2 | [全部文档维护索引](documentation-status.md) | 区分现行说明、原始需求、历史记录和归档附件 |
| 3 | [Phase4 任务](phase4/tasks.md) / [需求追踪](phase4/traceability-matrix.md) | 78 项任务及 114 条 NEW-* 的证据状态 |
| 4 | [现行技术方案](phase4/technical-design.md) / [协议](phase4/protocol.md) | 代码入口、模型、幂等、补取、状态机和存储边界 |
| 5 | [部署 Runbook](../deploy/runbook.md) / [部署架构](phase4/deploy-architecture.md) | 发布、备份、恢复、续期及端口 |
| 6 | [课程交付现状报告](course-delivery/交付状态报告-20261007.md) | 可提交的日期快照，另有同名 Word/PDF |

本学期范围是 Windows/Android、公网部署、可靠消息、E2EE 与软件测试实践。早期 Phase4 的 Channel/Bot/Agent 生态规划单独保留，不复用本学期任务号。

## 需求与验收

[SRS V2.0](course-testing/iChat%20Pro%20软件需求规格说明书.md)、[原始任务拆解](course-testing/第一次实验-需求分析与任务拆解报告.md)、[Phase4 需求](phase4/requirements.md) 保留要求与优先级。需求存在不等于实现完成；单项代码测试通过不等于跨端专项验收完成。

Windows 安装、品牌、托盘、连接恢复、敏感存储已闭环；通知最终布局复验 #145、文件集成 #146、Android、多设备和测试工程仍开放。准确状态请读上方任务和矩阵。

## 专题与旧版本

API、数据库、前后端设计、E2EE、群组、文件与 AI 文档均增加本次实现补充。旧协议示意、规划图和 SQL 示例需要结合现行补充与代码阅读，不能直接当生产迁移脚本。带 Phase2/Phase3、实验日期的验收和测试报告保留原结果；维护索引列出其历史用途。

## 验证与维护

根目录运行 `python manage.py check`、`python manage.py makemigrations --check --dry-run`、`python manage.py test`、`npm run test:e2ee`。Windows 的 desktop 目录另外运行 `npm run test:layout`、`npm run test:branding`、`npm run test:installer`。完整依赖和环境说明见 [项目 README](../README.md)。

更新功能时同步改现行 API/模型/协议与验收证据；历史记录仅追加日期说明，不覆盖当时的测试结果。变更状态须有对应 Issue、PR、CI 或原生记录。不要把 fixture 测试写成正式安装版验收，也不要把安装包 SHA 写成服务器版本。文档不包含真实账号密码、私钥、Token 或生产 .env。
