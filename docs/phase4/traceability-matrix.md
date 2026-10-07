# Phase4 需求追踪矩阵（P4 Test T02）

> 生成日期：2026-10-01（修订：测试映射逐行补全）；需求来源：[SRS V2.0 第 3 节](../course-testing/iChat%20Pro%20软件需求规格说明书.md)（114 条 NEW-*）。
> 任务映射展开自 [requirements.md §3](requirements.md) 的分组映射；测试任务映射（含工程族）为整理口径，依据 requirements.md §3 的配对关系与 [tasks.md](tasks.md) 依赖列推导，非原始拆解原文。
> 状态更新：2026-10-07，基线 f2f60ec。下列为需求级证据判断；工程卡关闭不自动使其关联需求通过。“部分”须保留未完成范围，详见 current-status.md 和 tasks.md。
> 优先级为 SRS 原始行值；与任务拆解优先级的少量冲突见 [requirements.md §7](requirements.md#7-原始材料冲突与处理)。

## WIN — Windows 客户端（16 条）

| 需求编号 | 名称 | 优先级 | 工程任务 | 测试任务 | 状态 |
| --- | --- | --- | --- | --- | --- |
| NEW-WIN-001 | Windows 安装包 | P0 | P4 T15、T16 | P4 Test T03 | 已验收：[#142](https://github.com/HeanX/iChat_Pro/issues/142)；Windows 原生证据 |
| NEW-WIN-002 | 安装与卸载 | P0 | P4 T15、T16 | P4 Test T03 | 已验收：[#142](https://github.com/HeanX/iChat_Pro/issues/142)；Windows 原生证据 |
| NEW-WIN-003 | 系统入口 | P1 | P4 T15、T16 | P4 Test T03 | 已验收：[#142](https://github.com/HeanX/iChat_Pro/issues/142)；Windows 原生证据 |
| NEW-WIN-004 | 产品标识 | P1 | P4 T15、T16 | P4 Test T03 | 已验收：[#143](https://github.com/HeanX/iChat_Pro/issues/143)；Windows 原生证据 |
| NEW-WIN-005 | 云服务连接 | P0 | P4 T14 | P4 Test T03、T23 | 已验收：[#141](https://github.com/HeanX/iChat_Pro/issues/141)；Windows 原生证据 |
| NEW-WIN-006 | 窗口管理 | P1 | P4 T17 | P4 Test T03 | 已验收：[#144](https://github.com/HeanX/iChat_Pro/issues/144)；Windows 原生证据 |
| NEW-WIN-007 | 系统托盘 | P1 | P4 T17 | P4 Test T03 | 已验收：[#144](https://github.com/HeanX/iChat_Pro/issues/144)；Windows 原生证据 |
| NEW-WIN-008 | 系统通知 | P1 | P4 T18、T19 | P4 Test T03 | 部分：通知原生门控/点击通过；最终布局复验待记录（#145） |
| NEW-WIN-009 | 通知跳转 | P2 | P4 T18、T19 | P4 Test T03 | 部分：正式安装版点击恢复/定位通过；#145 尚未最终闭环 |
| NEW-WIN-010 | 文件集成 | P1 | P4 T18、T19 | P4 Test T03 | 待实施：原生文件集成 #146 |
| NEW-WIN-011 | 连接状态 | P0 | P4 T20、T31～T36 | P4 Test T19、T03 | 已验收：[连接恢复 #147](https://github.com/HeanX/iChat_Pro/issues/147) |
| NEW-WIN-012 | 自动重连 | P0 | P4 T20、T31～T36 | P4 Test T19、T03 | 已验收：[断网恢复 #147](https://github.com/HeanX/iChat_Pro/issues/147)，记录 9.2s/3.5s |
| NEW-WIN-013 | 状态恢复 | P1 | P4 T21、T39 | P4 Test T03、T17 | 部分：会话/草稿恢复已有；完整切号及设备契约验收待 #166/#184 |
| NEW-WIN-014 | 安全存储 | P0 | P4 T21、T39 | P4 Test T03、T17 | Windows 存储已验收 [#148](https://github.com/HeanX/iChat_Pro/issues/148)；Web 回退及旧迁移边界见 protocol §7 |
| NEW-WIN-015 | 诊断日志 | P1 | P4 T21、T39 | P4 Test T03、T17 | 部分：现有日志约束；用户诊断导出/专项脱敏验收待核对 |
| NEW-WIN-016 | 版本更新 | P2 | P4 T16/T48 关联子任务（增强） | P4 Test T03、T20 | 部分：版本/关于面板已验收 #143；自动更新未实现 |

## AND — Android 客户端（16 条）

| 需求编号 | 名称 | 优先级 | 工程任务 | 测试任务 | 状态 |
| --- | --- | --- | --- | --- | --- |
| NEW-AND-001 | Android 安装包 | P0 | P4 T22、T24、T37 | P4 Test T04 | 未完成：Android 工程/APK 与原生证据未交付（#149–#157） |
| NEW-AND-002 | 核心账号功能 | P0 | P4 T22、T24、T37 | P4 Test T04 | 未完成：Android 工程/APK 与原生证据未交付（#149–#157） |
| NEW-AND-003 | 核心聊天功能 | P0 | P4 T22、T24、T37 | P4 Test T04 | 未完成：Android 工程/APK 与原生证据未交付（#149–#157） |
| NEW-AND-004 | 移动端布局 | P0 | P4 T23、T25 | P4 Test T04 | 未完成：Android 工程/APK 与原生证据未交付（#149–#157） |
| NEW-AND-005 | 触摸交互 | P1 | P4 T23、T25 | P4 Test T04 | 未完成：Android 工程/APK 与原生证据未交付（#149–#157） |
| NEW-AND-006 | 系统返回键 | P1 | P4 T23、T25 | P4 Test T04 | 未完成：Android 工程/APK 与原生证据未交付（#149–#157） |
| NEW-AND-007 | 生命周期恢复 | P0 | P4 T25、T26 | P4 Test T04 | 未完成：Android 工程/APK 与原生证据未交付（#149–#157） |
| NEW-AND-008 | 网络变化检测 | P0 | P4 T25、T26 | P4 Test T04 | 未完成：Android 工程/APK 与原生证据未交付（#149–#157） |
| NEW-AND-009 | 前台消息通知 | P0 | P4 T27、T30 | P4 Test T04 | 未完成：Android 工程/APK 与原生证据未交付（#149–#157） |
| NEW-AND-010 | 后台系统通知 | P1 | P4 T27、T30 | P4 Test T04 | 未完成：Android 工程/APK 与原生证据未交付（#149–#157） |
| NEW-AND-011 | 离线云推送 | P2 | P4 T27、T30 | P4 Test T04 | 未完成：Android 工程/APK 与原生证据未交付（#149–#157） |
| NEW-AND-012 | 文件选择 | P1 | P4 T28 | P4 Test T04 | 未完成：Android 工程/APK 与原生证据未交付（#149–#157） |
| NEW-AND-013 | 文件保存 | P1 | P4 T28 | P4 Test T04 | 未完成：Android 工程/APK 与原生证据未交付（#149–#157） |
| NEW-AND-014 | 权限管理 | P0 | P4 T28 | P4 Test T04 | 未完成：Android 工程/APK 与原生证据未交付（#149–#157） |
| NEW-AND-015 | 安全存储 | P0 | P4 T29、T39 | P4 Test T04、T06 | 未完成：Android 工程/APK 与原生证据未交付（#149–#157） |
| NEW-AND-016 | Android 兼容性 | P1 | P4 T29、T39 | P4 Test T04（自动化 T16） | 未完成：Android 工程/APK 与原生证据未交付（#149–#157） |

## XP — 跨平台一致性（8 条）

| 需求编号 | 名称 | 优先级 | 工程任务 | 测试任务 | 状态 |
| --- | --- | --- | --- | --- | --- |
| NEW-XP-001 | 统一账号 | P0 | P4 T31、T37 | P4 Test T17、T05 | 部分：Web/Windows 复用云端协议；Android 跨端 #164/#186 未验收 |
| NEW-XP-002 | 数据一致性 | P0 | P4 T31、T37 | P4 Test T17、T05 | 部分：Web/Windows 复用云端协议；Android 跨端 #164/#186 未验收 |
| NEW-XP-003 | 协议一致性 | P0 | P4 T31、T37 | P4 Test T17、T06 | 部分：Web/Windows 复用云端协议；Android 跨端 #164/#186 未验收 |
| NEW-XP-004 | 文本互通 | P0 | P4 T31、T37 | P4 Test T17、T05 | 部分：Web/Windows 复用云端协议；Android 跨端 #164/#186 未验收 |
| NEW-XP-005 | 文件互通 | P1 | P4 T31、T37 | P4 Test T05、T17 | 部分：Web/Windows 复用云端协议；Android 跨端 #164/#186 未验收 |
| NEW-XP-006 | 时间与顺序 | P0 | P4 T35 | P4 Test T13 | 服务端/Web/Windows 排序已回归 [#162](https://github.com/HeanX/iChat_Pro/issues/162)；Android 跨端待 #164 |
| NEW-XP-007 | 版本兼容 | P1 | P4 T31、T33～T36 | P4 Test T13 | 部分：协议 1.0 门禁与客户端终态已回归；Android 兼容验收待做 |
| NEW-XP-008 | 状态同步 | P1 | P4 T31、T33～T36 | P4 Test T13 | 部分：消息状态/补取已回归；撤回/删除事件同步未实现 |

## CLD — 云端与运维（18 条）

| 需求编号 | 名称 | 优先级 | 工程任务 | 测试任务 | 状态 |
| --- | --- | --- | --- | --- | --- |
| NEW-CLD-001 | 公网域名 | P0 | P4 T04～T07、T11 | P4 Test T23 | 已验证：公网域名/生产入口 #132 |
| NEW-CLD-002 | HTTPS/WSS | P0 | P4 T04～T07、T11 | P4 Test T23 | 已验证：TLS、认证 WSS 101、续期 reload #133 |
| NEW-CLD-003 | 反向代理 | P0 | P4 T04～T07、T11 | P4 Test T23 | 已实现并验证：Nginx TLS/代理 #131/#133 |
| NEW-CLD-004 | 正式 ASGI 服务 | P0 | P4 T04～T07、T11 | P4 Test T23 | 已实现并验证：Daphne/systemd #134 |
| NEW-CLD-005 | PostgreSQL | P0 | P4 T08、T09 | P4 Test T14 | 已验证：PostgreSQL 16 迁移与集成 [#195](https://github.com/HeanX/iChat_Pro/issues/195) |
| NEW-CLD-006 | 数据迁移 | P1 | P4 T08、T09 | P4 Test T14 | 部分：当前 migrations 已在 PG 应用；旧 SQLite 数据迁移专项待核对 |
| NEW-CLD-007 | Redis Channel Layer | P0 | P4 T08、T09 | P4 Test T14 | 已验证：真实 Redis Channel Layer [#195](https://github.com/HeanX/iChat_Pro/issues/195) |
| NEW-CLD-008 | 持久化文件 | P0 | P4 T10 | P4 Test T23、T14 | 已验证：持久媒体与鉴权下载 #137 |
| NEW-CLD-009 | 配置与密钥 | P0 | P4 T11、T49 | P4 Test T17 | 生产配置已验证 #138；独立测试环境 #176 待完成 |
| NEW-CLD-010 | 服务托管 | P0 | P4 T04～T07、T11 | P4 Test T23、T17 | 已验证：systemd 重启与开机自启 #134 |
| NEW-CLD-011 | 环境隔离 | P0 | P4 T11、T49 | P4 Test T17 | 部分：生产/本地配置分离；独立测试环境 #176 未完成 |
| NEW-CLD-012 | 健康检查 | P1 | P4 T12 | P4 Test T23 | 已实现并探测：live/ready；DB+缓存失败 503，#139 部分范围 |
| NEW-CLD-013 | 日志 | P1 | P4 T12 | P4 Test T23 | 部分：journald/Nginx 日志已有；专项日志验收待核对 |
| NEW-CLD-014 | 监控告警 | P1 | P4 T12 | P4 Test T23 | 待完成：监控告警 #139 |
| NEW-CLD-015 | 数据备份 | P0 | P4 T13、T50 | P4 Test T23 | 已验证：DB/media/SHA/来源版本及篡改检测 #140 |
| NEW-CLD-016 | 恢复演练 | P0 | P4 T13、T50 | P4 Test T23 | 部分：同主机独立库/目录恢复通过；跨实例恢复未验收 |
| NEW-CLD-017 | 部署与回滚 | P1 | P4 T13、T50 | P4 Test T23 | 应用回滚演练通过 #140；审批及完整发布流程 #177 待验收 |
| NEW-CLD-018 | 网络边界 | P0 | P4 T04～T07、T11 | P4 Test T23、T17 | 已验证：8443 公网，DB/Redis/Daphne loopback #131/#138 |

## REL — 消息可靠性（11 条）

| 需求编号 | 名称 | 优先级 | 工程任务 | 测试任务 | 状态 |
| --- | --- | --- | --- | --- | --- |
| NEW-REL-001 | 跨网络聊天 | P0 | P4 T31、T37 | P4 Test T05、T17 | 待专项验收：见对应开放任务及需求原文 |
| NEW-REL-002 | 唯一消息 ID | P0 | P4 T32、T36 | P4 Test T13 | 已回归：稳定 client_message_id [#159](https://github.com/HeanX/iChat_Pro/issues/159) |
| NEW-REL-003 | 消息幂等 | P0 | P4 T32、T36 | P4 Test T13 | 已回归：统一服务、重放/409/savepoint [#159](https://github.com/HeanX/iChat_Pro/issues/159) |
| NEW-REL-004 | 发送状态机 | P0 | P4 T33 | P4 Test T13 | Web/Windows 已回归：[状态机 #160](https://github.com/HeanX/iChat_Pro/issues/160)；Android 待接入 |
| NEW-REL-005 | 失败重试 | P0 | P4 T33 | P4 Test T13 | Web/Windows 同 ID 重发已验收 #147；首次无公钥缓存不能离线加密 |
| NEW-REL-006 | 自动重连 | P0 | P4 T20、T26、T34 | P4 Test T19、T13 | Windows <30s 恢复已验收 #147；Android T26 待做 |
| NEW-REL-007 | 遗漏消息补取 | P0 | P4 T20、T26、T34 | P4 Test T19、T13 | 服务端/Web/Windows 补取已回归 #161/#147；Android 待接入 |
| NEW-REL-008 | 消息排序 | P0 | P4 T35 | P4 Test T13 | 历史/实时/晚 ACK 排序已回归 #162/#147；Android 专项待做 |
| NEW-REL-009 | 状态最终一致 | P1 | P4 T31、T33～T36 | P4 Test T13 | 部分：ACK/回执单调与新增消息补取；变更事件同步未实现 |
| NEW-REL-010 | 重启恢复 | P0 | P4 T13、T34 | P4 Test T23 | 部分：服务持久消息/增量可恢复；内存待发箱不保证进程重启恢复 |
| NEW-REL-011 | 明确故障反馈 | P0 | P4 T33（另涉 T20、T26、T34） | P4 Test T13、T19 | Web/Windows 已有故障徽标/横幅/失败反馈；Android 待验收 |

## DEV — 设备与 E2EE 边界（9 条）

| 需求编号 | 名称 | 优先级 | 工程任务 | 测试任务 | 状态 |
| --- | --- | --- | --- | --- | --- |
| NEW-DEV-001 | 设备标识 | P1 | P4 T40、T41、T42 | P4 Test T06、T08 | 未完成：设备级身份/分发/撤销或历史迁移 #167–#170 |
| NEW-DEV-002 | 独立设备密钥 | P1 | P4 T40、T41、T42 | P4 Test T06、T08 | 未完成：设备级身份/分发/撤销或历史迁移 #167–#170 |
| NEW-DEV-003 | 设备列表 | P1 | P4 T40、T41、T42 | P4 Test T06、T08 | 未完成：设备级身份/分发/撤销或历史迁移 #167–#170 |
| NEW-DEV-004 | 撤销设备 | P1 | P4 T40、T41、T42 | P4 Test T06、T08 | 未完成：设备级身份/分发/撤销或历史迁移 #167–#170 |
| NEW-DEV-005 | 多设备分发 | P1 | P4 T40、T41、T42 | P4 Test T06、T08 | 未完成：设备级身份/分发/撤销或历史迁移 #167–#170 |
| NEW-DEV-006 | 新设备限制 | P0 | P4 T21、T29、T38、T39 | P4 Test T07 | 部分：新身份不能自动读取旧历史；跨设备策略/测试 #188 待完成 |
| NEW-DEV-007 | 密钥变化提示 | P1 | P4 T40、T41、T42 | P4 Test T06、T08 | 未完成：设备级身份/分发/撤销或历史迁移 #167–#170 |
| NEW-DEV-008 | 安全存储适配 | P0 | P4 T21、T29、T38、T39 | P4 Test T07、T17 | 部分：Windows safeStorage 已验收 #148；Android/平台统一契约待 #156/#166 |
| NEW-DEV-009 | 历史迁移 | P2 | P4 T43 | P4 Test T06 | 未完成：设备级身份/分发/撤销或历史迁移 #167–#170 |

## TST — 测试与质量（26 条）

| 需求编号 | 名称 | 优先级 | 工程任务 | 测试任务 | 状态 |
| --- | --- | --- | --- | --- | --- |
| NEW-TST-001 | 测试计划 | P0 | — | P4 Test T09 | 待验收：完整测试计划 #190 |
| NEW-TST-002 | 需求追踪 | P0 | — | P4 Test T02 | 映射已完成 [#183](https://github.com/HeanX/iChat_Pro/issues/183)；114 条需求的通过证据继续维护 |
| NEW-TST-003 | 黑盒测试 | P0 | — | P4 Test T10 | 待专项验收：见对应开放任务及需求原文 |
| NEW-TST-004 | 探索性测试 | P1 | — | 并入 P4 Test T03/T04/T19/T20 执行记录 | 待专项验收：见对应开放任务及需求原文 |
| NEW-TST-005 | 白盒测试 | P0 | — | P4 Test T11 | 待专项验收：见对应开放任务及需求原文 |
| NEW-TST-006 | 后端覆盖率 | P0 | — | P4 Test T12 | 待专项验收：见对应开放任务及需求原文 |
| NEW-TST-007 | 分支覆盖率 | P0 | — | P4 Test T12 | 待专项验收：见对应开放任务及需求原文 |
| NEW-TST-008 | 高风险覆盖率 | P0 | — | P4 Test T12 | 待专项验收：见对应开放任务及需求原文 |
| NEW-TST-009 | 单元测试 | P0 | — | P4 Test T10～T13 | 部分：现有 404 Django + JS 回归；完整需求覆盖待各专项任务 |
| NEW-TST-010 | 接口测试 | P0 | — | P4 Test T13 | 部分：HTTP 接口回归已执行；完整专项索引/验收 #194 待补 |
| NEW-TST-011 | WebSocket 测试 | P0 | — | P4 Test T13 | 部分：WS/并发/协议回归已执行；完整专项验收 #194 待补 |
| NEW-TST-012 | 数据库集成测试 | P0 | — | P4 Test T14 | 已验证：真实 PostgreSQL 16 CI [#195](https://github.com/HeanX/iChat_Pro/issues/195) |
| NEW-TST-013 | Redis 集成测试 | P0 | — | P4 Test T14 | 已验证：真实 Redis 7 CI [#195](https://github.com/HeanX/iChat_Pro/issues/195) |
| NEW-TST-014 | 浏览器 E2E | P1 | — | P4 Test T15 | 部分：Chromium 布局 36/36；完整浏览器业务 E2E #196 待验收 |
| NEW-TST-015 | Windows App 测试 | P0 | — | P4 Test T03（自动化部分 T15） | 部分：安装/品牌/托盘/断网原生验收；通知/文件及整套 #184 待闭环 |
| NEW-TST-016 | Android App 测试 | P0 | — | P4 Test T04（自动化部分 T16） | 待专项验收：见对应开放任务及需求原文 |
| NEW-TST-017 | 跨端测试 | P0 | — | P4 Test T05、T17 配合 | 待专项验收：见对应开放任务及需求原文 |
| NEW-TST-018 | 安全测试 | P0 | — | P4 Test T17 | 待专项验收：见对应开放任务及需求原文 |
| NEW-TST-019 | 依赖安全 | P1 | — | P4 Test T17（工具链 P4 T47） | 待专项验收：见对应开放任务及需求原文 |
| NEW-TST-020 | 性能测试 | P1 | — | P4 Test T18 | 待专项验收：见对应开放任务及需求原文 |
| NEW-TST-021 | 弱网测试 | P0 | — | P4 Test T19 | 部分：Windows 断网恢复已验收；系统性弱网/长时 #200 待完成 |
| NEW-TST-022 | 稳定性测试 | P1 | — | P4 Test T18 | 待专项验收：见对应开放任务及需求原文 |
| NEW-TST-023 | 备份恢复测试 | P0 | — | P4 Test T23（配合 P4 T13） | 部分：同主机恢复/篡改/回滚有证据；完整 #204 验收未完成 |
| NEW-TST-024 | 缺陷管理 | P0 | — | P4 Test T21 | 过程已闭环：[缺陷管理 #202](https://github.com/HeanX/iChat_Pro/issues/202) |
| NEW-TST-025 | 回归测试 | P0 | — | P4 Test T21 | 过程已闭环：[回归记录 #202](https://github.com/HeanX/iChat_Pro/issues/202)；不代表所有测试任务通过 |
| NEW-TST-026 | 测试总结 | P0 | — | P4 Test T22 | 待专项验收：见对应开放任务及需求原文 |

## CI — 持续集成与发布（10 条）

| 需求编号 | 名称 | 优先级 | 工程任务 | 测试任务 | 状态 |
| --- | --- | --- | --- | --- | --- |
| NEW-CI-001 | 静态检查 | P0 | — | P4 T44 | 未完成：静态/格式工具门禁 #171 |
| NEW-CI-002 | Django 检查 | P0 | — | P4 T45 | 部分：check/migration CI 已运行；强制合并门禁 #172 未配置 |
| NEW-CI-003 | 自动化测试 | P0 | — | P4 T45（Node 测试并入） | 部分：Django/Node CI 已运行；失败强制阻断合并 #172 未配置 |
| NEW-CI-004 | 覆盖率门禁 | P0 | — | P4 T46 | 待专项验收：见对应开放任务及需求原文 |
| NEW-CI-005 | 安全扫描 | P1 | — | P4 T47 | 待专项验收：见对应开放任务及需求原文 |
| NEW-CI-006 | 客户端构建 | P1 | — | P4 T48 | 部分：Windows 构建已运行；Android 与完整 #175 验收未完成 |
| NEW-CI-007 | 测试环境部署 | P1 | — | P4 T49 | 待专项验收：见对应开放任务及需求原文 |
| NEW-CI-008 | 云端冒烟 | P0 | — | P4 Test T23 | 部分：readiness 自动门禁；登录/核心 API 冒烟失败阻断 #204 未实现 |
| NEW-CI-009 | 生产发布审批 | P1 | — | P4 T50 | 待专项验收：见对应开放任务及需求原文 |
| NEW-CI-010 | 回滚能力 | P1 | — | P4 T50 | 部分：应用指定 SHA 回滚演练通过；DB 不自动降级，完整 #177 待验收 |

## 使用规则

1. 每项 P0 需求在对应任务验收时回填状态（完成/部分/未验证）并附证据位置；P0 需求不得出现「无关联任务、无用例」的空档（NEW-TST-002 验收标准）。
2. Android 未交付期间，AND 族明确标为“未完成”；启动 Android 时（P4 T22–T30）再逐行回填实际证据。
3. 同一任务覆盖多条需求（如 P4 T31 覆盖 XP/REL 多行）时，任务验收证据须能分别支撑每条需求的验收标准，不能以任务完成替代需求验收。
4. 优先级冲突处理遵循 [requirements.md §7](requirements.md)；本表优先级为 SRS 原值，任务优先级以 [tasks.md](tasks.md) 原值为准。

## 统计

- WIN：16 条（P0 6 条）
- AND：16 条（P0 9 条）
- XP：8 条（P0 5 条）
- CLD：18 条（P0 13 条）
- REL：11 条（P0 10 条）
- DEV：9 条（P0 2 条）
- TST：26 条（P0 21 条）
- CI：10 条（P0 5 条）
- 合计：114 条，其中 P0 71 条。
