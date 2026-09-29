# ADR 0001 — 公共 ID、Schema 版本与包边界

- 状态：Accepted（P0-01 冻结）
- 日期：2026-09-29
- 影响范围：P0–P7 全部阶段。下游阶段不得私造 `project/task/step/session/host/event/evidence` 字段或 ID 格式；需要变更时先提版本化变更 PR 并更新本 ADR。

## 1 背景

《实施与验证任务书》与设计书要求所有真源对象引用统一 ID，但未规定格式、唯一性范围与生成者。V0 `jasmine_memory` 已有一套自己的标识与本地存储，V1 不继承 V0 的格式。

## 2 决定

### 2.1 包名与目录边界

| 层 | 路径 | 责任 |
| --- | --- | --- |
| 公共契约 | `docs/adr/`、`docs/api/` | ID、Schema、事务、API、鉴权契约 |
| 真源实现 | `src/jasmine_core/` | ID、迁移、事务、Event Store、对象投影、API |
| 接入适配 | `src/jasmine_core/capture/` | 只把外部 Agent 事件翻译为 Raw Event；不含真源逻辑 |
| 验收记录 | `src/jasmine_core/validation/` | 最小 Validation Recorder |
| 测试 | `tests/` | 测量真实行为，不复述实现 |

- Python 包名固定为 `jasmine_core`。**不得**导入或复用 `jasmine_memory`（V0）代码，V0 仅为迁移评估对象，其已实现功能不计为 V1 验收。
- V1 数据只存在于 V1 的 `core.db`；V0 状态目录（`jasmine_memory` 的 state dir、`*.sqlite`）不是 V1 真源，V1 也不写 V0 的表。
- 运行时零第三方依赖（仅 Python 3.11+ 标准库），测试用 `unittest`。理由：P0 是供所有下游阶段共用的底座，依赖越少越容易被独立复现；引入框架必须由单独的版本化变更 PR 说明。

### 2.2 ID 格式

所有公共 ID 形如 `<prefix>_<26 位 Crockford Base32 大写字符>`，共 27 字符：

```
prj_01K5S0Q8H4M9ABCD7FGH2JKMNP
tsk_01K5S0QAB2E5WXYZ01QRST3VWX6
ses_01K5S0QAC7Z8CVBNM0LKJHGFDE2
evt_01K5S0QAD3D4ERFGH5TYUIOPAS8
hst_01K5S0QAE9F1GHJKL2ZXCVBNMQ4
act_01K5S0QAF5T6HJKLM3NCVZXWQER7
evd_01K5S0QAG8R7JKLMN4QWERTYUIO
aud_01K5S0QAJ1S2D3FGH4ZXCVBNMQWE
key_01K5S0QAK6P9QWERTY0ASDFGHJK
```

- 前 10 位为 base32 编码的 48 位 Unix 毫秒时间戳（最高有效位在前），后 16 位为 `secrets.token_bytes(10)` 的随机值。**ID 按字典序即时间序**，便于分页与调试。
- ID 不携带主机、用户、路径、标题等可推断信息；随机部分不可由时间推算。

| 前缀 | 对象 | 生成者 | 引用规则 |
| --- | --- | --- | --- |
| `prj_` | project | Core 服务端 | 全局唯一主键；被 task/session 引用 |
| `tsk_` | task | Core 服务端 | 全局唯一主键；必须属于恰好一个 project |
| `stp_` | step | Core 服务端（P1 起启用） | 全局唯一主键；必须属于恰好一个 task。P0 只冻结格式与校验，不建表、不声明已实现 |
| `ses_` | session | Core 服务端 | 全局唯一主键；可无 task（Raw Event 仍可入链） |
| `hst_` | host | Core 服务端 | 全局唯一主键；`host_id` 标识采集设备 |
| `act_` | actor | Core 服务端 | 全局唯一主键；`actor_id` 标识人或 Agent 身份 |
| `evt_` | event | Core 服务端 | 全局唯一主键；**Raw Event 身份** |
| `evd_` | evidence | Core 服务端（P1 起启用） | 全局唯一主键；P0 只冻结格式 |
| `aud_` | audit record | Core 服务端 | 只追加，不可改 |
| `key_` | API key | Core 服务端 | 只存哈希，明文仅在创建时返回一次 |

唯一性由数据库主键强制，而非仅靠生成器的概率保证。生成器在插入冲突时最多重试 8 次（`IntegrityError`），超过则报错，不做静默降级。

客户端**不得**自行构造 `prj_/tsk_/ses_/evt_` 之外的 ID。客户端可自带 `event_id`（仅在需要幂等重试时），但必须是合法 `evt_` ID，且 Core 校验其未与他人抢占；见 ADR 0003。

### 2.3 Schema 版本

- 版本是一个单调递增整数 `schema_version`，存于 `core_meta` 表的 `schema_version` 键。当前值：**1**。
- 迁移是**只进**的、有序的、带内容校验和的 Python 模块（`jasmine_core.migrations.mNNNN_*`）。每个已发布迁移的 SQL 一经合入 `main` 不得修改；修正必须新增迁移。
- 启动时 Core 读取 `schema_version`：低于代码期望 → 依次执行缺失迁移；高于代码期望 → **拒绝启动**（防止旧二进制打开新 Schema 造成静默损坏），退出码非 0。
- 迁移在单个 `BEGIN IMMEDIATE` 事务内执行；同一数据库上的迁移由 `core_meta` 中的 `migration_lock` 行串行化。
- 已记录迁移的校验和与代码不一致 → 拒绝启动，报 `migration_drift`。

## 3 V0 / V1 边界（明确不做的事）

- 不迁移 V0 的 `jasmine_memory` 数据表，不复用其 ID，不兼容其 API 形状。
- P0 不实现 Rule/Guard、Task 状态机完整迁移、Evidence 充分性判定、Interpreter/Resolver、Context Pack/Hindsight、完整 Agent lifecycle Adapter、离线同步、Dashboard、30 场景验收。这些在 P0 只定义扩展位与接口，不建立空壳实现后声称已完成。

## 4 后果

- 所有下游阶段共享同一 ID 与 Schema 契约；跨阶段改动必须版本化。
- 零依赖使任何干净环境都能复跑 P0 验收；代价是 HTTP 层暂用标准库（见 ADR 0003）。
