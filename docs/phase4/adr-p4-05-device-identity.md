# ADR-P4-05：设备身份模型（P4 T40 / Issue #167）

| | |
| --- | --- |
| 状态 | 提案（Proposed）——已通过独立技术评审（见下方评审记录），**待单一迁移负责人会签后进入实现** |
| 日期 | 2026-10-18 |
| 决策者 | 账号与公钥信任模块负责人（本 ADR 作者）；会签人 = 单一迁移负责人（约束 `accounts/models.py`、`chat/models.py` 与迁移编号，见 conventions §5），会签结论以 Issue #167 评论区留档 |
| 关联 | Issue #167（本 ADR）；下游 #168（设备列表与撤销）、#169（多设备密钥分发）、#170（历史密钥迁移） |
| 前置 | [ADR-P4-04 E2EE 威胁模型](e2ee-threat-model.md)（T38，另一分支交付）——其"不覆盖"一节明确把设备级身份/撤销留白 |
| 基线 | `cfe53be` |

## 1. 背景与问题

### 1.1 现状：身份是"账号级"，不是"设备级"

当前（基线 `cfe53be`）服务端关于"一个账号的加密身份"的事实：

| 事实 | 位置 | 对多设备的含义 |
| --- | --- | --- |
| `UserPublicKey`：每账号多版本公钥历史，唯一键 `(user, key_version)`，同一时刻至多一条 `is_active=True` | `accounts/models.py:293-329` | 一个账号**只有一个**当前身份密钥；第二台设备要么共用（私钥得复制过去），要么触发轮换使第一台立即失聪 |
| `KeyTrust`：信任按 `(user, contact, key_fingerprint)` 记录 | `accounts/models.py:335-383` | 换设备不改指纹时信任不变；一旦轮换，所有联系人的信任全部失效、需逐个重验（这正是 #189 收敛出来的行为） |
| `sender_copy`：发送方用**自己的**当前身份公钥再加密一份 | `chat/models.py:255-258`（私聊）、`chat/models.py:341`（群聊） | 多设备目前唯一的"共余"机制：要求所有设备共享同一把身份私钥 |
| `ichat_peer_identity:<userId>`（localStorage，按版本记录指纹） | `static/js/private-chat-e2ee.js:5` | 信任状态存在**浏览器**里，换浏览器/设备即丢；服务端 `KeyTrust` 与之是两套状态 |
| 设备概念不存在 | — | 会话列表（`/api/sessions/`）基于 Django session，不是设备清单（模块文档已标注） |

结论：**当前架构下"多设备" = "多台设备共享一把私钥"**。这不是可以迭代改进的小缺口，而是 #168（撤销）与 #169（分发）的根本前提：

- 没有设备实体，"撤销某台设备"没有主语（#168 无从谈起）；
- 没有设备实体，"给每台设备分别加密"没有目标（#169 的 `recipients` 无法扩展）。

### 1.2 为什么先写 ADR 而不是先写代码

backlog（issue-backlog.md 第四梯队）与模块文档都明确："**先写 ADR，不要先写代码**"，原因有三：

1. **#169 会改变群聊 `recipients` 的语义**（从"每个成员"到"每个成员 × 设备"），这是消息模块的核心契约，必须串行整合（conventions §5）；
2. `UserPublicKey` 的唯一键、`KeyTrust` 的审计语义都是 #189 刚刚收敛并钉死的行为，设备模型若直接动它们，会把刚闭环的东西再打碎；
3. 迁移编号已到 `chat.0021` / `accounts.0015`，新增模型必须串行排号，先设计可避免与并行 Agent 抢号。

## 2. 目标与非目标

**目标**

- G1 同一账号的多台设备**可独立识别**（#167 的验收原句）；
- G2 每台设备有自己的身份密钥对，私钥不离开设备；
- G3 一台设备的密钥材料被泄露时，可以**只作废那台设备**（为 #168 提供主语）;
- G4 现有单设备用户的行为完全不变（零迁移成本、旧数据无需回填即合法）。

**非目标（本 ADR 明确不做）**

- N1 不实现设备列表 UI 与撤销接口（#168）；
- N2 不改变 `recipients` 的线上语义（#169 单独出 ADR 与消息模块会签）；
- N3 不做历史密钥迁移方案（#170）；
- N4 不引入设备间实时同步（在线分发、QR 扫码配对等）；
- N5 不解决 Android 端实现（#149–#157 未落地）。

## 3. 方案概览

### 3.1 核心决策

**D1：新增独立 `Device` 模型，不改造 `UserPublicKey` 的唯一键。**

```text
Device
├── user          FK → User, on_delete=CASCADE, related_name="devices"
├── device_id     CharField(64), 不可空        # 客户端生成的随机 opaque ID（UUID4 hex）
├── name          CharField(120), 不可空       # 用户可见名，如"桌面 Chrome"
├── platform      CharField(32), 不可空        # web / windows / android / ios
├── public_key    OneToOneField → UserPublicKey, null=True, blank=True,
│                 on_delete=SET_NULL, related_name="device"
│                 # 设备的身份密钥；创建设备时可先不绑（见 D3 的两阶段上线）
├── public_key_fingerprint  CharField(64), null=True, blank=True, db_index=True
│                 # 冗余展示列：撤销广播/审计场景避免反复 join UserPublicKey；
│                 # 由服务端在改绑/轮换时写入，不参与任何唯一性
├── created_at    auto_now_add
├── last_seen_at  DateTime(null=True)         # 最近活跃，供列表页展示（阶段 1 无心跳接口，恒为 null；
│                                             # 更新触发方在阶段 2 随会话/心跳机制补）
├── revoked_at    DateTime(null=True)         # 非 null = 已撤销（#168 的主语）
│                 # 审计伴生字段（revoked_by FK→User、revoked_reason CharField(255)）在
│                 # #168 实施时一并补齐并写迁移——阶段 1 没有撤销入口，先立字段会造出
│                 # 永远为空的列
└── 约束：UniqueConstraint(fields=["user", "device_id"], name="unique_user_device")
         索引：Index(fields=["user", "revoked_at"])
```

**为什么是 `OneToOne` 而不是外键多对一？** 一台设备任一时刻只有一把身份密钥；密钥**轮换**时新建 `UserPublicKey` 行并改绑（历史行通过 `device` 反查保留审计，与 #189 确立的"旧记录保留"一致）。

**D2：`UserPublicKey` 增加可选 `device` 外键（`null=True`），唯一键 `(user, key_version)` 不动。**

```text
UserPublicKey.device = FK → Device, null=True, blank=True,
                       on_delete=SET_NULL, related_name="public_keys"
```

- `device=NULL` 的行 = **账号级密钥**（现状），单设备用户、以及历史上传的密钥自动落入此态；
- 这是把"账号有密钥"扩展为"账号或设备有密钥"，而不是替换——G4（零迁移成本）由此成立；
- **不改** `unique_user_public_key_version`：同一设备轮换密钥时，新版本行的 `key_version` 仍按账号内递增。

**D3：分两阶段上线，第一阶段只做"识别"，不做"隔离"。**

| 阶段 | 内容 | 上线后用户体验 |
| --- | --- | --- |
| 阶段 1（本 ADR 范围） | `Device` 模型 + 迁移 + 最小 API（注册自己的设备、列出自己的设备） | 系统能"看见"有哪些设备；加密行为与今天完全一致（共享私钥模式不受影响） |
| 阶段 2（#168/#169） | 绑定设备级密钥、撤销接口、`recipients` 扩展 | 每台设备独立密钥、可单独撤销 |

理由：阶段 1 若直接做"每设备独立密钥"，`upload_public_key_view`、`_active_key()`、前端 `trustPeerKey` 等**全部调用点**都要同时改，一次提交无法通过任何一轮有意义的评审。先落库模型 + 只读/自注册 API，是可以在一轮评审内闭环的最小切片。

**D4：`device_id` 由客户端生成、服务端不解析含义。**

opaque UUID，服务端只做唯一性约束。**不**用 fingerprint、不用 IP、不用 UA 派生——这些既不稳定也不该成为身份。同账号同 `device_id` 重复注册 = 幂等更新（更新 name/platform/last_seen_at）。

**D5：设备注册要求已登录，且只能操作自己的设备。**

- `POST /api/devices/register/`（登录必需）：注册/更新**自己**的设备；
- `GET /api/devices/`（登录必需）：列出**自己**的设备（含撤销状态）；
- 两个接口都挂在 `ApiAuthMiddleware` 之下（未登录返回 401 JSON，契约 R-06）；
- **每账号活跃（未撤销）设备数有上限**（建议 ≤10，具体值待会签）：幂等键是 `(user, device_id)`，
  认证后的脚本可以在限流窗口内不断用新 `device_id` 造行——没有上限时这是无限的存储放大；
  超限返回拒绝（错误码与限流码一起在阶段 2 注册进 `chat/errors.py`）；
- 不提供跨用户查询，不需要新的权限模型。

### 3.2 明确拒绝过的替代方案

| 被拒方案 | 拒绝理由 |
| --- | --- |
| 在 `UserPublicKey` 上加 `device_id` 字符串字段，不建 `Device` 表 | #168 的"撤销设备"需要设备级状态（revoked_at、名称、平台）与审计时间线，散在公钥表里无法表达"设备存在但还没传过密钥"；也无法给 #169 的"每设备信封"提供稳定主语 |
| 复用 Django `Session` 当设备 | session 生命周期由浏览器 cookie 决定，登出/过期即消失，无法承载"这台设备被永久撤销"；且 session 表不记录平台/名称 |
| 每设备一个 `User`（子账号） | 波及全部外键、权限、联系人模型，等于重写账号系统；与"设备属于用户"的常识相反 |
| 直接把 `unique_user_public_key_version` 改成 `(user, device, key_version)` | 破坏 #189 刚钉死的迁移与既有数据；且账号级密钥（device=NULL）无法参与唯一约束的语义 |

## 4. 影响分析

### 4.1 数据与迁移

- 新增 `accounts.0016_device`（编号顺延，不动既有迁移）；
- 新增 `UserPublicKey.device` 的迁移 `accounts.0017_userpublickey_device`（或合并为单个迁移，实施时按单一迁移负责人惯例串行）；
- **零数据回填**：所有既有 `UserPublicKey` 行 `device=NULL`，语义即"账号级密钥"，合法。

### 4.2 对既有契约的影响

| 契约（accounts-auth.md §3） | 影响 |
| --- | --- |
| 指纹 = SHA-256(SPKI) 大写 hex | 无影响（设备不改变指纹算法） |
| `KeyTrust` 不自动继承 | 无影响。设备轮换密钥 = 新指纹 = 未信任，规则不变 |
| `/api/` 未登录 401 JSON | 新接口同样遵守 |
| 上传公钥不带私钥 | 无影响 |
| 旧信任记录保留审计 | **强化**：设备改绑/撤销不改写任何 `KeyTrust` 行 |

### 4.3 对下游卡片的影响

- **#168**：撤销 = 置 `revoked_at` + 作废该设备绑定的密钥（阶段 2）；本 ADR 的 `Device` 即其主语；
- **#169**：`recipients` 扩展为"成员 × 未撤销设备"，**必须**由消息模块负责人会签本 ADR 后再动；`sender_copy` 的语义届时从"发给自己账号"细化为"发给自己的其它未撤销设备"；
- **#170**：`Device` 使"历史密钥迁移"有了明确的授权粒度（按设备授权），但方案仍由 #170 自己出。

### 4.4 安全考量（对齐 ADR-P4-04 的威胁模型）

- 设备注册接口是**新的认证后攻击面**：需限流（复用 chat 侧 `_rate_limit` 模式；注意 `rate_limited`
  错误码现未在 `chat/errors.py` 注册——正式注册属跨客户端契约变更，放阶段 2，见 §7）；
- **每账号活跃设备数上限**（见 D5）：没有它，认证后的脚本可在限流窗口内用新 `device_id` 无限造行；
- `device_id` 客户端生成 → 服务端只校验格式（≤64 字符、hex/UUID 形状），防注入与超长；
- **跨账号 `device_id` 冲突的信息泄露**：若 A 账号注册了某 `device_id`，B 账号再用同一 ID 时返回的
  409 会向 B 证明"该 ID 已被别的账号占用"。选择：仍返回 409（错误体不区分"已被占用"与"格式/上限"原因），
  理由：UUID4 空间下正常碰撞概率可忽略，能撞上即意味着客户端在伪造或复用他人 ID，这类行为本身
  值得被拒绝；不选择"静默 201"，因为它会让 B 误以为设备已注册、随后列表里却看不到，排障更糟；
- `revoked_at` 在阶段 1 只落库、不参与任何鉴权判断（避免"看似撤销、实则无效"的假安全）；
- 日志不记录 `device_id` 全文与用户代理串（安全红线 §6）。

### 4.5 对 #169 的额外提醒（来自风险登记册）

`sender_copy` 的投影在服务端有 **REST 与 WS consumer 两条规则不同的路径**
（risk-register.md **R-03**："HTTP 与 WS 私聊序列化路径漂移"，已标注"双路径单边修改易泄漏"，
附代码证据 `chat/consumers.py:120-135`、`chat/views.py:2457-2477`）。#169 把它从"发给自己账号"
细化为"发给自己的其它未撤销设备"时，**两条路径必须同一 PR 内一起改**，否则出现
"WS 能解、REST 不能解"的设备级泄漏。R-03 的既有处置建议（统一服务层返回 `{viewer payloads}`）
与 #169 的改造方向一致，宜合并实施。

## 5. 验收对照（#167 验收原句）

| 验收项 | ADR 阶段（本卡） | 实现阶段（后续 PR） |
| --- | --- | --- |
| 同一账号不同设备可独立识别 | D1 的 `Device` 模型 + `unique_user_device` 约束 + 阶段 1 的注册/列表接口**设计**（本 ADR 即交付物） | 模型、迁移、接口落地并有测试 |
| 相关代码或配置经过非作者成员评审 | **已满足（ADR 阶段）**：本 ADR 已由独立子 Agent（全新上下文、非作者）评审，记录见本文档末尾"评审记录"一节。注意：AI 子 Agent 评审不能替代人类成员评审，最终仍需人工确认 | 迁移负责人会签（回答 Q1–Q5）+ 按 conventions §1 开独立审查子 Agent |
| 适用的自动化或人工测试已执行并保存证据 | 不适用（无代码）；文档一致性由评审者核对代码行号等 6 条事实性声明替代 | 注册/列表接口的越权、幂等、限流测试 + 证据文件 |
| 需求、部署或测试文档已同步更新 | 本 ADR 登记 documentation-status.md；tasks.md / traceability-matrix.md 标注 ADR 已交 | API 文档、tasks.md、traceability-matrix.md 随实现 PR 同步 |

## 6. 待决问题（需要会签时回答）

1. **Q1**：阶段 2 里，一台设备可否同时持有"账号级密钥"与"设备级密钥"？（影响 `UserPublicKey.device`
   是否允许多行指向同一设备）**并请一并回答**：设备上传**自己的**密钥时走既有的
   `upload_public_key_view`（账号级轮换语义：轮换会把全账号 `is_active` 置 False、`key_version`
   按账号内递增）还是新接口？走旧视图会把设备密钥和账号密钥搅进同一条版本序列。
2. **Q2**：`recipients` 扩展（#169）采用"每设备一个信封"还是"设备组密钥"？前者流量 N 倍，后者需要新的群密钥协商，倾向前者起步。
3. **Q3**：设备撤销后，其历史 `KeyTrust` 记录是否需要向对端广播"该设备密钥已作废"？涉及协议新增事件类型，需消息模块同意。
4. **Q4**：`device_id` 在客户端卸载重装后会变（浏览器 localStorage 清空即变），是否允许"同设备不同 ID"产生两条记录？倾向允许 + 以 `last_seen_at` 自动归档僵尸记录（需 #168 定义阈值）。**需同时决定归档形态**：
   软删除（隐藏但保留审计）还是物理保留——撤销审计与"设备数上限"都依赖这个口径。
5. **Q5**（账号模块自答，不等会签）：设备记录（`device_id`/`name`/`last_seen_at`）的数据保留与删除口径。
   现状是账号删除**不删**公钥/资料/消息（模块文档已知坑），设备记录若同样永久保留，需在
   文档里写明这是有意的审计取舍，并给出用户可见的"移除设备"路径（阶段 2 UI）作为出口。

## 7. 实施清单（阶段 1，供实现 PR 使用）

```text
[ ] accounts.0016_device 迁移（含 unique_user_device 约束与索引）
[ ] UserPublicKey.device 可空外键迁移（可并入上一条）
[ ] POST /api/devices/register/（幂等：同 (user, device_id) 更新而非重复插入）
[ ] GET  /api/devices/（仅返回自己的设备，含 revoked 状态）
[ ] 每账号活跃（未撤销）设备数上限，超限拒绝（上限值待会签，建议 ≤10）
[ ] 越权测试：改别人的 device_id 必须 404/403，绝不写库
[ ] 未登录 401 JSON 测试（契约 R-06）
[ ] 幂等测试：同 device_id 重复注册不产生第二行
[ ] device_id 形状校验测试（超长 / 非法字符 / 空）
[ ] tasks.md / traceability-matrix.md / API 文档同步
```

> 限流在阶段 1 若要落地，只能用现有可表达的方式（如 429 + 未注册错误码）并**显式标注临时性**；
> 正式的错误码注册（`device_limit_reached` 等）属于 `chat/errors.py` 跨客户端契约变更，
> 移到阶段 2 与 #169 一起过评审，不进本卡清单。

> 本 ADR 本身不含任何代码与迁移。实现 PR 必须引用本 ADR 并列出上述清单的勾选状态。

## 8. 评审记录（ADR 阶段的非作者评审证据）

| 轮次 | 审查者 | 结论 | 发现项与处理 |
| --- | --- | --- | --- |
| 1 | 独立子 Agent（全新上下文，仅给文档路径 + Issue 原文 + 核对清单） | PASS WITH FINDINGS | 6 条事实性声明全部属实（`UserPublicKey`/`KeyTrust` 模型与约束位置、`sender_copy` 字段、迁移编号 0015/0021、`rate_limited` 未注册、session 不是设备）。**MAJOR-1**：§5 把"非作者评审"全部推给实现阶段，与 conventions §1"独立评审已完成即构成证据"矛盾——已按建议拆分为"ADR 阶段 / 实现阶段"两列并补本节。MINOR：设备表缺公钥指纹冗余列（已采纳，见 D1）、缺撤销审计字段（已列为 #168 待补）、缺每账号设备数上限（已采纳进 §7 清单与 §4.4）、跨账号 device_id 409 信息泄露未定义（已采纳进 §4.4）、错误码注册不应进阶段 1 清单（已采纳，见上方说明）、Q 漏数据保留问题（已补 Q5）并充实 Q1。 |
| 待定 | 单一迁移负责人（会签） | — | 回答 Q1–Q5 后进入实现 |

> 本次评审由 AI 子 Agent 完成，构成 conventions §1 意义上的独立技术评审记录；
> **不替代**人类成员评审，也不构成 #167 验收项的最终闭环。
