# iChat Pro 文档总览

本文档作为 `docs/` 目录入口，用于说明各文档的阅读顺序、当前交付口径和维护规则。

## 推荐阅读顺序

| 顺序 | 文档 | 用途 |
| --- | --- | --- |
| 1 | `iChat Pro 系统性介绍文档.md` | 从整体理解系统目标、架构、模块和演示边界 |
| 2 | `iChat Pro 需求文档_修订版.md` | 查看需求范围、阶段边界和验收标准 |
| 3 | `iChat Pro 技术栈.md` | 查看主要技术选型和运行依赖 |
| 4 | `iChat Pro API 接口文档.md` | 查看后端接口设计 |
| 5 | `iChat Pro 数据库设计规范文档.md` | 查看核心数据模型 |
| 6 | `iChat Pro 实时通信与端到端加密消息协议设计文档.md` | 查看 WebSocket 和消息协议 |
| 7 | `iChat Pro 端到端加密通信设计文档.md` | 查看 E2EE 密钥、加密和信任设计 |
| 8 | `iChat Pro UML 与架构图交付文档.md` | 查看 UML、架构和交付图表 |
| 9 | `iChat Pro 演示指南.md` / `iChat Pro Phase 3 演示脚本与验收文档.md` | 查看演示流程和验收操作 |

## 文档分类

| 分类 | 文档 |
| --- | --- |
| Phase4 开发交接 | [开发入口](phase4/README.md)、[需求与验收](phase4/requirements.md)、[技术方案与代码入口](phase4/technical-design.md)、[Agent 工作包](phase4/agent-handoff.md)、[78 项任务](phase4/tasks.md) |
| 软件测试课程 | `course-testing/iChat Pro 软件需求规格说明书.md`、`course-testing/第一次实验-需求分析与任务拆解报告.md`、`course-testing/第二次实验-软件工程全链路与AI实践报告.md`、`course-testing/代码规范、版本管理与测试工具说明.md`、`course-testing/AI实践记录.md`、`course-testing/小组分工表.md` |
| 总览与需求 | `iChat Pro 系统性介绍文档.md`、`iChat Pro 需求文档_修订版.md`、`iChat Pro Phase 规划与一期交付审查文档.md` |
| 架构与设计 | `iChat Pro 技术栈.md`、`iChat Pro 后端设计规范文档.md`、`iChat Pro 前端设计规范文档.md`、`iChat Pro UML 与架构图交付文档.md` |
| 数据与接口 | `iChat Pro API 接口文档.md`、`iChat Pro 数据库设计规范文档.md` |
| 通信与安全 | `iChat Pro 实时通信与端到端加密消息协议设计文档.md`、`iChat Pro 端到端加密通信设计文档.md`、`iChat Pro 浏览器端安全威胁模型.md`、`iChat Pro 部署安全说明.md` |
| 功能专题 | `iChat Pro 文件传输规范.md`、`iChat Pro 群组管理与邀请流程实现文档.md`、`iChat Pro 群组左侧面板整合说明文档.md`、`iChat Pro 转发界面与文件转发问题梳理.md`、`iChat Pro AI Assistant 增强整理文档.md`、`iChat Pro Twemoji 表情渲染方案文档.md` |
| 扩展规划 | `iChat Pro Bot、LLM Agent 与 Channel 扩展方案文档.md` |
| 验收与测试 | `iChat Pro Phase 2 验收手册.md`、`iChat Pro Phase 3 演示脚本与验收文档.md`、`phase2_test_coverage.md`、`p3_t01_regression_report.md` |

## 当前交付口径

- 上学期版本作为本学期的回归基线，已有能力包括账号、联系人、私聊、群聊、实时通信、端到端加密消息、文件转发、设置页、AI Assistant 面板和 Electron 桌面端原型。
- 本学期软件测试实践的新增交付重点为可安装的 Windows App、Android App、公网云部署、E2EE 与消息可靠性增强，以及覆盖黑盒、白盒、安全、性能和兼容性的测试体系。
- Channel、完整 Bot、Agent Gateway、语音/视频通话和高级 Signal Protocol 不属于本学期必须完成的核心范围；若核心任务提前完成，再作为扩展项评估。
- 测试代码已统一整理到 `chat/tests/`，JS 加密测试位于 `chat/tests/js/`。

需要交给其他 Agent 开发本学期 Phase4 时，从 [Phase4 开发交接入口](phase4/README.md) 开始。该目录采用最新课程的跨平台/云端/可靠性/测试范围，并明确区分早期 Channel、Bot 与 Agent 生态规划。

## 常用验证命令

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
npm run test:e2ee
```

## 文档维护规则

- 新增功能时优先更新需求、API、数据库、演示和系统介绍文档。
- 涉及 WebSocket 或加密协议时，同步更新实时通信协议文档和 E2EE 设计文档。
- 涉及前端布局和页面交互时，同步更新前端设计规范和相关专题文档。
- 交付包中不包含本地依赖、数据库、媒体文件和生成产物；这些内容按 README 的交付说明排除。
