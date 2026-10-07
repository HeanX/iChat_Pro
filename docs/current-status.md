# iChat Pro 当前实现与交付状态

> 核对日期：2026-10-07（Asia/Shanghai）；代码与生产基线：`f2f60ecc0e6110fd5df81ffcde4c5594a862d7b9`。这是本次文档更新的事实入口，后续变更需重新核对，不能把此快照当成实时状态。

## 完成情况

原始任务 #128–#205 共 78 项，**29 项关闭、49 项开放（37.2%）**；工程 25/54，测试 4/24。#207–#249 是已关闭的 Duplicate，不计入分母。Issue 关闭反映该任务证据闭环，不等于所有 114 条 NEW-* 或全部 P0 已验收。

| 工作包 | 完成/总数 | 当前边界 |
| --- | --- | --- |
| WP00 基线与统筹 | 4/5 | 计划的负责人、时间及降级方案仍待完整验收（#130） |
| WP01 云端配置与运维 | 9/10 | 监控告警未完成；健康与日志已实现（#139） |
| WP02 消息可靠性 | 6/7 | T31–T36 闭环；Android 跨端 T37 未完成（#164） |
| WP03 Windows | 6/8 | T14/T15/T16/T17/T20/T21 闭环；T18 最终验收、T19 文件集成开放 |
| WP04 Android | 0/9 | 尚无 Android 工程/APK 与原生验收 |
| WP05 安全与设备 | 0/6 | 跨平台威胁模型、KeyStore、多设备和历史迁移待做 |
| WP06 测试与缺陷 | 2/20 | 真实 PG/Redis 集成与缺陷回归流程闭环，其余专项验收待完成 |
| WP07 CI 与发布 | 0/8 | 四项 CI 已运行，但原卡完整门禁/部署/审批范围尚未验收 |
| WP08 证据与材料 | 2/5 | AI 使用与缺陷分析闭环，最终跨端证据与报告任务开放 |

逐项状态见 [78 项任务](phase4/tasks.md)，需求证据见 [114 条追踪矩阵](phase4/traceability-matrix.md)，最近的逐卡审计见 [完成情况审计](phase4/completion-audit-20261007.md)。

## 代码与部署

PR [#328](https://github.com/HeanX/iChat_Pro/pull/328) 修复内嵌设置样式泄漏、侧栏 flex 尺寸及聊天顶部布局；PR [#329](https://github.com/HeanX/iChat_Pro/pull/329) 回填完成审计和 AI 过程证据。两者已合并，服务器部署至 `f2f60ec`；部署日志记录 `2026-10-07T13:42:16Z f2f60ecc0e6110fd5df81ffcde4c5594a862d7b9 main`。

公网入口为 [登录页](https://chat.20060810.xyz:8443/login/)。Nginx TLS 8443 → Daphne loopback 8000 → PostgreSQL 16 / Redis 6 系列 loopback；媒体在 `/var/lib/ichat/media`。Redis 7 用于 CI；Python redis 8.1.0 是客户端依赖。

`/health/live/`、`/health/ready/`、`/login/` 的 HTTPS 检查均为 200，部署 readiness 校验 JSON 的 DB+缓存状态。此结果不证明全部聊天业务冒烟或通知 UI 验收。操作细则见 [Runbook](../deploy/runbook.md)。

最后核对的安装包 SHA-256 为 `ac468df305bd43c73179555671b3aaf80fca06773427f64a42e14a2ad299d5f9`，文件在 `desktop/dist/iChat-Pro-Setup-1.0.0.exe`。这份本地包包含通知 AUMID 修复；PR #328 的模板/CSS 从云端加载，未因这次文档更新重新构建包。不要把 main SHA、服务器 SHA 和本地安装包 SHA 混为同一版本。

## 消息可靠性

`chat/services/messaging.py` 是 HTTP/WS 文本、文件、转发的事务写入路径。身份、会话权限及请求一致性先于合法重放；群聊重放不受之后的成员版本变化阻挡。两处 create 使用嵌套 atomic savepoint，IntegrityError 恢复在健康事务执行。created 分支才写附件密钥、计未读、广播和生成持久事件。

`Conversation.sync_sequence` 与 `ConversationEvent` 在同一事务、会话行锁内分配序号。迁移 `0021_conversation_sync_sequence_conversationevent` 已上线。sync 按序号推进，游标绑定用户和会话，TTL 7 天；走完释放快照，下次增量取新事件。读取复用历史可见性，包括清空、个人删除、自动删除、拉黑和群 pre-join 边界。

前端共享连接模块负责退避、心跳、ACK 和内存待发箱；实时与补取共享应用队列和会话内去重。游标只在整页成功应用后保存；空 items 仍按 has_more 翻页；过期游标重置一次。时间线按 `(created_at, id)`，晚 ACK 使用服务端时间及稳定行身份。完整契约见 [protocol.md](phase4/protocol.md)。

**限制**：事件仅覆盖部署后的新增消息；撤回/删除/成员变更事件属于后续工作。待发箱不持久化，页面重载/进程重启会丢未确认项。离线加密需要已缓存且符合信任规则的公钥；首次无缓存时不能离线建立新的加密收件关系。

## Windows 与安全存储

安装版默认加载云地址，不启动本地 Django；dev 显式启用。托盘隐藏/恢复同一窗口，不重载页面；X 与菜单退出真退出，单实例锁先于网络和启动服务。品牌名称、版本 1.0.0、图标与 AUMID `pro.ichat.desktop` 对齐，未配置商业数字签名。

卸载默认“否”保留数据，交互“是”删除当前用户 `%APPDATA%\ichat-pro-desktop`，静默路径保留数据。登记按安装所有权匹配清理；真实是/否路径已验收，隔离回归不触碰正式数据。

长期私钥是 IndexedDB 不可导出 CryptoKey；桌面待导出 JWK、聊天草稿和 AI 历史通过 safeStorage/Windows DPAPI 加密。新写入在桥不可用时不回退明文。旧明文迁移失败会保留原记录，未解密成功的密文也保留，因此不能承诺所有旧数据已经加密。Web 的 pending JWK 在 sessionStorage，草稿/AI 历史仍可明文存 localStorage。

main IPC：encrypt 上限 2,097,152 UTF-8 字节；decrypt 输入含 enc 前缀最多 2,900,000 字符，解码密文最多 2,162,688 字节（2 MiB + 64 KiB）。当前 sender frame 判断接受应用 Origin 或 file URL；它不是任意第三方页面的通用加解密接口。可信同源脚本仍能使用 CryptoKey/桥，OS 加密不消除 XSS 或云端脚本被替换的风险。

## 通知与布局验收

#145 保持开放。用户已确认正式安装版通知点击恢复/定位、应用显示 iChat Pro、OS 通知开关、设置即时生效；之后切换预览暴露布局回归，PR #328 已修。

Chromium fixture 在三种窗口尺寸、明暗主题下 36/36 通过，线上 CSS 参数为 `20261005-t18-layout`，内容核对一致。**尚未记录修复后正式登录客户端的最终预览切换、返回聊天和顶部位置复验**；不能用 fixture 或健康探针关闭该项。T19 原生文件选择/保存/打开（#146）仍待实施。

## 验证与质量缺口

基线 [CI run 37630595919](https://github.com/HeanX/iChat_Pro/actions/runs/37630595919) 四项全绿：

| job | 证据 |
| --- | --- |
| test | Django check、迁移一致性、SQLite 404 条测试通过（222.012s） |
| integration | PostgreSQL 16 / Redis 7、应用迁移、404 条测试通过（312.851s） |
| javascript | npm run test:e2ee，含加密、连接、ACK、公钥缓存、桌面和三套通知回归 |
| windows-installer | Chromium 36/36、打包品牌资源校验、NSIS 24/24 |

这些次数属于上述 run；后续以具体 run 日志为准，不用 ✓ 日志行数推定测试数。主分支保护未配置 required_status_checks，rulesets 为空：已有 CI 不等于“失败强制禁止合并”（#172）。覆盖率与静态/安全扫描、Android 构建、独立测试部署、发布审批仍待验收。

备份包括数据库、媒体、来源 SHA 和相对路径校验和，14 天保留；已做篡改检测、应用回滚及同主机独立库/目录恢复。跨实例完整恢复仍未验收。readiness 是现行自动发布门禁，登录/核心 API 失败阻断尚未实现（#204）。

## 文档使用规则

现行运行、协议、技术方案以本文件及所链的现行文档为准；旧阶段设计、实验报告、旧 PDF/Word 保留原日期与结果，在 [维护索引](documentation-status.md) 中明确归档。SRS 和原任务验收目标不因为暂未实现而删减；人员姓名、学号、责任和时间表没有证据时保留待填写。
