# Jasmine Core V1 多阶段 PR 计划

> **2026-09-30 用户执行覆盖：** 后续按完整阶段在本地完成实现、独立 Review、独立 validation 和真实 Gate 后统一 push；不逐条创建/推送 PR，不运行 CI，也不把 CI 作为验收门禁。文内 PR 切分保留为本地实施与审查里程碑；旧 CI/逐 PR 发布要求由此覆盖。已合并 P2-01/P2-02 的历史记录保留，不追溯删除。后续阶段采用同一覆盖规则，除非用户明确另行调整。


状态：阶段计划。P0 的 PR #1–#3 和 P1 的 PR #4–#6 已合并；P1 最终基线为 `905962564d07e4b5d7c08802231611c21f0b4ad5`，真实 Gate、独立审查、PR/main CI 与合并后验证已通过。P2 已完成限定验收；P3 Run34 七项真实 Gate 已记录 PASS，详见 [P3 结果](p3-results.md)。P4–P7 保留规划范围，不表示已实施。P0/P1 原始验收证据在本机 ignored validation/artifacts 保留，下表描述未来阶段目标。

P2/P3 由当前调度线程顺序负责，P4 Memory 和 P6 Dashboard 委派其他 Agent 按依赖并行。可执行文档：[并行协作说明](Jasmine-Core-V1-P2-P3-P4-P6-并行协作说明.md)、[P2](Jasmine-Core-V1-P2-阶段任务书.md)、[P3](Jasmine-Core-V1-P3-阶段任务书.md)、[P4](Jasmine-Core-V1-P4-阶段任务书.md)、[P6](Jasmine-Core-V1-P6-阶段任务书.md)。文档交付本身不构成这些阶段的实现或验收。

## 当前实施记录（2026-10-03）

P2-01/P2-02 已合历史保留；P2-03 在 `468918a803dc8bb44700d6d6043afd5e3c7c4204`、Schema8 完成本地独立验收，结论 PASS_P2_SCOPED_ADMISSION_ONLY，详见[P2结果摘要](p2-results.md)。以下逐 PR 条件按顶部用户覆盖解释为本地实施/Review/validation 里程碑，whole-stage 完成后一次发布，不执行 CI 门禁。P3 在源码 `6ec64362401bc92d52aed531e15d95a9d1441fb4` 的 Run34 完成真实 compact、exact Context 和 Playwright 联合 G01–G07 PASS 记录；最终独立交付审查 PASS；阶段统一发布由 Git/PR 元数据记录，详见 [P3 结果](p3-results.md)。

## 依据与边界

- 《Jasmine-Core-V1-实施与验证任务书》§1–3、§7–10 给出 P0–P7 顺序、WS-A–J、真实对话验收和发布门禁。
- 《Jasmine-Core-V1-详细设计书》§2–15 与《Jasmine-Core-V1-模块设计书》§1–20 给出权威边界、20 个模块及依赖。
- 文档没有规定 PR 数量、PR 边界或“四线并行”的具体分组。下列 PR、分工和合并次序是本次建议。文档中的操作文字作为设计要求阅读，不作为本次立即实施或改变环境的命令。
- 目标仓库为公开的 [mercuryLiu163/jasmine-core-v1](https://github.com/mercuryLiu163/jasmine-core-v1)，默认分支为 `main`，CI 使用标准 GitHub runner。Jasmine Mesh 父目录不是 Git 仓库，实际 checkout 是其下的 `jasmine-core-v1/`。V0 已归档退役，旧全局 hook、MCP 与自启动服务已移除；V1 不复用其运行状态。

## 所有阶段共用的协作与合并规则

建议设置四条工作线：① Schema 与 Truth，② 输入、续接与 Memory，③ 设备与 Dashboard，④ 验证、安全与运维。这是对任务书“四线并行、共享 Schema”的具体化建议，不是文档已指定的分组。

每个 PR 至少有实现 Agent、独立 Review Agent 和独立验证 Agent。阶段负责人负责集成与决策记录，不以自己的代码审查替代独立 Review。实现 Agent 可按文件/接口拆为两人；Reviewer 检查契约、失败路径、权限与文档一致性；验证 Agent 在待合并提交上复跑测试并提交原始输出与结论。下游 Agent 只使用已冻结的公共 ID、Schema 和 API 契约，不另造 `project/task/session/event` 字段。

单个 PR 的完成条件：变更范围与依赖写清；迁移/接口/错误语义有版本记录；必要的自动化测试通过；独立 Review 的阻断项关闭；验证证据附在 PR 并绑定明确 commit；未实现或未运行的能力明确标为未验证。每阶段最后一个 PR 合并前在候选 commit 执行该阶段的**真实对话 Gate**，合并后在最终 commit 复跑受合并影响的验收；仅单元或集成测试通过不算阶段完成。Validation Recorder 在 P0 先做最小记录能力，在 P7 完成完整模块。

## 阶段与 PR

| 阶段 | 建议 PR（按依赖顺序） | 阶段验收，与任务书 §2 对齐 |
| --- | --- | --- |
| **第一阶段 P0 Baseline** | **P0-01** 仓库开发基线、公共 ID/Schema、`core.db` WAL 与空库迁移；**P0-02** 不可变 Raw Event、幂等写入及 project/task/session 基础持久化；**P0-03** Core API 骨架、最小 Codex UserPromptSubmit 捕获、鉴权/审计、重启恢复与真实对话 Gate | 能创建 project/task/session/event，重启后可读回；Schema 有版本，原始 Event 不可改；真实对话的原文能作为 Raw Event 追溯。详见《Jasmine-Core-V1-P0-阶段任务书》。 |
| **P1 Truth Core** | **P1-01** Authority 版本、scope、proposal/approve 与 Guard；**P1-02** Task State、revision 与状态迁移；**P1-03** Evidence、Workspace Fingerprint、Codex PreToolUse/PostToolUse 与最小 Rule/State 读取路径 | 以结构化 API 预置 Rule/Task 状态，在真实对话中能区分执行、验证、验收；HARD Rule 阻止至少一个真实工具动作；VERIFIED/ACCEPTED 有相应 Evidence。自然语言自动抽取不在本阶段宣称通过。 |
| **P2 Interpretation** | **P2-01** 输入事件链与可追溯 Interpreter 候选；**P2-02** Resolver/Policy、人工 review/correct API；**P2-03** 自然语言抽取真实对话 Gate | Playwright 示例从原话形成 Task + requirement；“考虑”不误判成“决定”，纠正可追溯。P0 的最小 Event Store 在此扩展，不另建真源。 |
| **P3 Continuity** | **P3-01** Checkpoint 与 Codex PreCompact/Stop 接入，交接通过 Stop 或显式 checkpoint 接口触发；**P3-02** Context Pack、Codex SessionStart 注入、token 预算与 Resume revision/fingerprint 校验；**P3-03** compact/交接真实对话 Gate | 真实 Agent compact 后准确继续，不需重释全部历史；旧 Checkpoint 不覆盖 Current State。 |
| **P4 Memory** | **P4-01** Hindsight Adapter、global/project bank 与权威隔离；**P4-02** 异步 Memory Jobs、故障降级、召回 Gate | 召回确实帮助真实任务；Hindsight 故障不阻断 Task/Truth 事务；Memory 不晋升为权威。 |
| **P5 Multi-device** | **P5-01** Device/Agent Registry 与真实能力声明；**P5-02** 整合并扩展 P0–P3 的增量 Codex 接入为完整 lifecycle Adapter，并接入 Claude、离线 pending 与幂等同步；**P5-03** revision 冲突处理与跨设备接力 Gate | Windows Codex → Mac Claude 接力，revision 正确刷新；离线客户端不能本地产生 ACCEPTED，冲突不采用 last-write-wins。 |
| **P6 Dashboard** | **P6-01** 只读 Overview/Project/Task/Conversation 视图；**P6-02** Extraction Review、Rule/Task 人工操作与 audited Core API；**P6-03** Evidence、Checkpoint、Memory、Context、Validation Inspector | 人工能从原话追溯 Rule、实际 Context Pack、Tool/Evidence；UI 不直写 SQLite。 |
| **P7 Validation 与发布** | **P7-01** 完整 Validation Recorder、Scenario Runner 与失败分类；**P7-02** VC-01～VC-30 和综合长任务验证；**P7-03** backup/restore、retention、安全审计与 G1–G8 发布证据 | 全量证据包、综合长任务和 G1–G8 通过后才声明 V1 完成；明确记录 Critical 用例判定，不能从任务书猜测其清单。 |

每阶段由两名实现 Agent 按所列 PR 的接口/模块拆工；独立 Reviewer 和验证 Agent 使用下表聚焦，不复用该 PR 的主要实现者。

| 阶段 | 独立 Review 焦点 | 独立验证焦点 |
| --- | --- | --- |
| P0 | 共享 ID、事件不可变、事务/鉴权、V0/V1 边界 | 空库迁移、失败回滚、真实消息捕获和重启读回 |
| P1 | Rule 权限与版本、状态迁移、Evidence 门槛 | 真实 HARD Guard 拦截及执行/验证/验收区分 |
| P2 | 候选不能直改 Truth、Resolver 来源与人工纠正 | 原话抽取、误判负例与纠正回放 |
| P3 | 旧 revision、fingerprint、HARD Rule 与 token 裁剪 | compact/Stop/Handoff 后的真实续接 |
| P4 | Memory 非权威、项目隔离、异步作业 | 真实召回收益及 Hindsight 宕机降级 |
| P5 | 身份与能力声明、离线幂等、冲突处理 | 跨设备接力、pending 重放与 revision 刷新 |
| P6 | UI 只走 Core API、人工操作审计、trace 完整性 | 从原话点击追溯到 Context、Tool 与 Evidence |
| P7 | 场景覆盖、失败分类、备份恢复与发布证据 | VC-01～30、综合长任务、裸机恢复和 G1–G8 |

## 跨阶段依赖与评审重点

1. P0 的 ID、迁移、事件和事务约定先冻结；后续 PR 可并行开发，但涉及同一真源的合并须遵守依赖顺序。所有真实交互先落 Raw Event；Interpreter 只产候选，Resolver 决定 Truth 更新。
2. Authority 高于 Memory；Current State 高于旧 Checkpoint；Agent 只能提出 Rule proposal。任何 VERIFIED/ACCEPTED 必须有合规 Evidence 与 Workspace Fingerprint。Reviewer 每阶段检查这些边界。
3. Dashboard、Validation Recorder、安全审计和备份是 V1 一级能力。P0 建立最小可观测与证据格式，P6/P7 补全功能，不等到最后才开始记录验收。
4. 每阶段的真实对话 Gate 只验证当时已实现的能力。例如 P0 只验证真实用户消息进入 Raw Event、对象可创建并重启恢复；不能把它写成 Interpreter、Guard 或跨设备接力通过。
5. P7 综合长任务须满足任务书 §9 的时长、工具调用、compact/Agent 或设备切换、真实 FAIL、HARD Rules 与 Memory Recall 条件。发布时核对 §10 G1–G8；其中 G5 的 Critical 场景范围需在 Validation 计划中明定。
6. Codex Adapter 按阶段增量交付：P0 UserPromptSubmit 原文捕获，P1 PreToolUse/PostToolUse 与最小 Rule/State 读取，P3 SessionStart/PreCompact/Stop 和实际 Context Pack 注入；P5 才扩展完整 lifecycle、多客户端、多设备和离线同步。每阶段的能力声明只覆盖已实现且实测的 hook；没有原生阻断能力时不能声称 Guard 已 ENFORCED。

## 实施输入与后续门禁

- checkout 与 V0 边界已确定，见本文依据与边界。P0/P1 已完成，P2 从核对过的当前 `main` 开发。
- P0/P1 契约已在 ADR 0001–0007 和 Schema v6 冻结；后续扩展须通过新 ADR 和前向迁移。Resolver 的 source×impact×scope 策略矩阵由 P2 定义；P3 拥有 Checkpoint/Context，P4 拥有 Memory/jobs，P6 消费已冻结 API。迁移编号、公共 ID/auth/API 文件由集成 owner 串行协调。
- P0 捕获和 P1 真实 PreToolUse 阻断、Post/native Evidence/State Gate 已完成。P3 新生命周期 hook 的信任和实际能力须单独验证，已有 P1 信任不能代替新增定义。缺少入口或用户信任时记录 BLOCKED，不能用合成请求冒充。验收 CLI 采用命令级 Memory 隔离，避免内部记忆整理会话继承 nonce 抢占测试绑定；不改用户全局设置。
