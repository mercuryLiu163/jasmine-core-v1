# Jasmine Core V1 第一阶段 P0 Baseline 实施与验证任务书

状态：待执行。目标仓库已创建；本文给出可分派的任务、Review 和验收标准；当前未创建 P0 PR、未实现 P0、未运行 P0 验收。

## 1 目标和依据

本阶段建立可供后续 Truth Core、Interpreter、续接和多设备模块共用的最小真源底座。交付统一 ID、版本化 SQLite Schema/迁移、Core API 骨架与不可变事件模型。完成时应能创建 project/task/session/event，重启 Core 后完整读回，并以一条**真实对话**证明 Raw Event 入口可追溯。

依据：《Jasmine-Core-V1-实施与验证任务书》§1–3、§7（P0 Gate 与每阶段真实对话要求）；《Jasmine-Core-V1-模块设计书》§1–2、§19（公共约定、Event Store、安全）；《Jasmine-Core-V1-详细设计书》§2–6、§12（真源、权威、输入链与审计）。这些文档是本阶段的设计输入；本任务书中的 PR 划分和 Agent 分工是本次规划建议。

## 2 启动条件与待定契约

**创建 PR 的前置条件：**目标为私有仓库 [mercuryLiu163/jasmine-core-v1](https://github.com/mercuryLiu163/jasmine-core-v1)，远端已建立，默认分支为 `main`，含初始 README/`.gitignore`。2026-09-29 本 Jasmine Mesh 工作目录并非该仓库的 Git checkout；实施前要准备干净 checkout、CI 与代码归属。明确现有 `jasmine_memory` V0 与 V1 Core 的包名、数据和 API 边界，禁止直接把 V0 的已实现功能算作 V1 验收。

P0-01 合并前写出简短 ADR/接口契约，裁定以下未被设计书细化的点：

1. `project_id`、`task_id`、`step_id`、`session_id`、`host_id`、`event_id`、`evidence_id` 的格式、唯一性、引用与生成者。
2. 事务中的 `expected_revision` 与 `task.revision` 语义；没有 Task 的 Raw Event 如何追加；失败时 Event 与 projection 如何一起回滚。
3. Event 与基础对象 API 的请求、响应、错误码、幂等冲突语义和 Schema 版本策略。
4. Raw Event 保留原文与敏感信息处理的边界，以及设备/actor 的鉴权与审计要求。不得把敏感数据写入测试证据包。

这些是**待决设计点**，不是已由原文给出的精确表结构或接口。

## 3 范围

**本阶段实现：**版本化 `core.db` Schema 和 SQLite WAL；空库迁移；公共 ID 和事务封装；project/task/session 的最小创建与查询，其创建操作关联源 Event；Raw Event 追加、查询与 `event_id` 幂等；Core API 骨架；最小 Codex UserPromptSubmit 捕获入口；最小设备/actor 鉴权、审计和 Validation Run 证据格式；重启恢复与真实对话 P0 Gate。

**留给后续阶段：**Rule/Guard、Task 状态机的完整状态迁移、Evidence 充分性判定、LLM Interpreter/Resolver、Context Pack/Hindsight、完整 Agent lifecycle Adapter/离线同步、Dashboard 页面及 30 个场景验收。P0 可定义扩展字段和接口，但不可把空壳标作已实现。现有 V0 `codex_hook.py` 等仅可作为迁移评估对象，不算 P0 交付。

## 4 PR 和多 Agent 工作安排

| PR | 依赖、实现 Agent 任务 | 必须交付的代码与契约 | 独立 Review 与验证 |
| --- | --- | --- | --- |
| **P0-01 基线与公共契约** | 阶段负责人从已建立的 `main` 创建开发分支和干净 checkout。Agent A 负责 ID/Schema/迁移；Agent B 负责事务/revision 契约与开发环境，可在互不修改同一文件的前提下并行。 | 目录与包边界、ADR、Schema version、空库迁移、WAL/事务配置、迁移测试和最小 CI。 | Agent C Review 数据关系、迁移可重放、V0/V1 边界及敏感路径；Agent D 在干净环境复验空库启动和失败回滚。 |
| **P0-02 Event 与基础对象** | 依赖 P0-01 的已评审契约。Agent A 实现不可变、幂等 Event Store；Agent B 实现 project/task/session 最小持久化与关联。 | 追加/查询 Raw Event、基础对象创建/查询；每次对象创建关联源 Event，并在同一事务写入 Event 与对象 projection；稳定 ID、actor/source/session 关联、重复提交语义与事务测试。 | Agent C Review 原文不可改、越权和重复 `event_id`；Agent D 复验重启读回、幂等、冲突与注入失败不留半笔数据。 |
| **P0-03 API 与阶段 Gate** | 依赖 P0-02。Agent A 实现 Core API 骨架与鉴权/审计；Agent B 实现最小 Codex UserPromptSubmit 捕获入口及 Validation Run 记录，绑定真实 source session、host、actor 和原文到 `event_id`。 | 可调用的基础对象/Event API、接口文档、错误响应、真实接入路径、阶段验收脚本或步骤、原始结果证据包。此入口只覆盖 P0 原文捕获，不冒称完整 Agent Adapter。 | Agent C 独立 Review 权限、错误语义和“只验证 P0”的声明；Agent D 执行真实对话、重启恢复和负例，给出逐用例 PASS/FAIL、失败类别或未执行 BLOCKED 及证据。 |

阶段负责人处理 PR 间冲突、维护依赖关系与未决问题清单。Reviewer 与验证 Agent 不把实现者的自测结论直接当作独立结果。Agent 可轮换人员，但每个 PR 的 Review 和验证者须与该 PR 的主要实现者分开。

## 5 可执行验收矩阵

| 编号 | 操作与预期 | 必留证据 |
| --- | --- | --- |
| P0-T01 | 从空库运行迁移，核对 Schema 版本；重复启动不重复建表或破坏数据。 | 迁移命令、退出码、Schema 版本查询和日志。 |
| P0-T02 | 使用授权身份经 Core API 创建 project、task、session、event；前三类创建各有关联的源 Event，并与对象 projection 原子提交。查询所得 ID、关联、来源和原文一致。 | 请求/响应、源 event_id、脱敏后的 Raw Event 与查询结果。 |
| P0-T03 | 停止并重启 Core，重新查询 T02 的四类对象，ID、关系和 Raw Event 原文保持一致。 | 重启前后查询与进程/数据库路径记录。 |
| P0-T04 | 同一 `event_id` 同内容重复提交不会新增第二条；同 ID 不同内容按已冻结契约返回冲突。 | 两次响应、事件数及冲突响应。 |
| P0-T05 | 在一次性测试库中尝试修改或删除已存 Raw Event，并对对象创建事务注入中途失败；前者被拒，后者不留下半笔源 Event/object projection 或虚假成功。 | 负例请求、错误响应、前后查询和故障注入输出。 |
| P0-T06 | 未授权或错误 scope 的 actor 写入被拒，Truth 不变；审计记录不泄露 token/原文敏感片段。 | 脱敏请求、审计结果、前后状态查询。 |
| **P0-T07 真实对话 Gate** | 在已授权真实 Codex 对话中发出一条测试消息，经 P0-03 的 UserPromptSubmit 捕获入口成为 Raw Event；核对真实 source session、host、actor 与 event_id 的关联，重启后读回原文。只判定捕获与持久化，不判定解释或状态机。若无真实入口，记 BLOCKED。 | Validation Run：原始对话片段、接入调用与响应、session/host/actor/event ID、Raw Event、重启前后查询、执行者/Reviewer/验证者结论。 |

T01–T06 可用自动化测试覆盖，但 T07 必须使用真实交互。P0-T07 的测试内容应是无敏感数据的固定短句；证据包保留必要原文、响应和版本信息，避免保存 token、私钥或私人对话。

P0 的 Validation Run 使用 §7 的证据结构：USER RAW、AGENT RESPONSE、TOOLS、STATE BEFORE/AFTER、Interpretation、Context 和 Verdict 均有字段；P0 尚不存在的处理环节逐项标 `N/A（P0 未实现）` 并写明理由，不能填造假的 PASS。最小记录器同时保存代码版本、Schema 版本、运行环境、原始命令/输出和失败记录；P7 再完成完整 Recorder 与 Scenario Runner。

已执行用例的 Verdict 为 PASS 或 FAIL；FAIL 附 failure class、原始输出和复现步骤。环境或真实入口缺失、导致用例没有运行时记 BLOCKED，不伪造 Verdict。阶段整体只有在所有用例 PASS 后才为 PASS；否则为 BLOCKED，并保留其中已运行用例的 FAIL。

## 6 Review 清单和阶段完成定义

Review Agent 在每个 PR 记录阻断项和最终结论，重点检查：公共 ID 与 Schema 未分叉；迁移能从空库启动；原始事件不可改且可追溯；写入原子性与 revision 约定一致；权限失败不写 Truth；日志/证据脱敏；测试测量了真实行为而非复述实现。任何跨 PR 契约改动回到 P0-01 的 ADR 和迁移评审。

**阶段 PASS 条件：**P0-01～03 均已合并到确定的目标仓库；P0-T01～T07 在候选 commit 上通过，合并后在最终 commit 复跑受合并影响的验收；各 PR 的独立 Review 阻断项关闭；Validation Run 绑定 commit、保存原始请求、响应、输出、版本、失败记录和结论；重启恢复得到直接证据。验收记录明确列出未实现的 P1–P7 能力。

**阶段 BLOCKED 条件：**干净 checkout/代码边界未就绪、真实对话入口不可用、任一 T01～T07 失败、证据不全，或把模拟输入写成真实对话。BLOCKED 时保留现有证据、失败原因、下一步和责任人，不以重跑后的单个成功信号覆盖原失败。此时不得宣布进入 P1 的阶段 Gate。

## 7 向 P1 交接

交付 Schema/迁移版本、公共 ID 与 revision 契约、Core API 文档、Event Store 实现、P0 Validation Run 和已知限制。P1 的 Authority、Guard、State 与 Evidence Agent 从这些契约开始；若发现无法满足 HARD Rule 或 Evidence 的字段需求，先提版本化变更 PR，不在下游私改公共模型。
