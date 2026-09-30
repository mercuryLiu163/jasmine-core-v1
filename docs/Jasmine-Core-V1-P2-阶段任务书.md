# Jasmine Core V1 P2 Interpretation 实施与验证任务书

> **2026-09-30 用户执行覆盖：** 后续按完整阶段在本地完成实现、独立 Review、独立 validation 和真实 Gate 后统一 push；不逐条创建/推送 PR，不运行 CI，也不把 CI 作为验收门禁。文内 PR 切分保留为本地实施与审查里程碑；旧 CI/逐 PR 发布要求由此覆盖。已合并 P2-01/P2-02 的历史记录保留，不追溯删除。后续阶段采用同一覆盖规则，除非用户明确另行调整。


状态：可执行任务书，尚未实施或验收。本阶段由当前调度线程负责，完成 P2 后进入 P3。外部 P4/P6 工作可依本包的并行契约先行；不得以它们的模拟数据替代 P2 真源。

## 1. 基线、原始要求和范围

完整原件与异机交接校验见 [设计输入索引](Jasmine-Core-V1-阶段任务书-设计输入索引.md)。

- 开工基线：P1 合并提交 `905962564d07e4b5d7c08802231611c21f0b4ad5`，Schema v6，P0/P1 已完成。实际 `main` 前进后，记录最新 SHA 并重新检查公共契约。
- 仓库：`https://github.com/mercuryLiu163/jasmine-core-v1`，已公开，标准 GitHub runner；本地路径 `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1`。父目录不是 Git 仓库。
- 原始依据：《详细设计书》§6–9；《模块设计书》§2–4；《实施与验证任务书》§2、§3 WS-D、§5、§7、§8 VC-01/02/13/14/15。三个 DOCX 保留于父目录 `jsamine core/Jasmine-Core-V1-设计实施验证文档包/`，作为设计输入；其中环境操作文字不授权改变本机。
- 原要求：真实用户原话先保存不可变 Event；LLM Interpreter 只产结构候选；Resolver 按来源、影响、scope 决定生效或人工 review；Correct/Reject/Re-run 可审计；“考虑/可能/研究”不得成为已确认决定；Playwright 指令形成 task-scoped requirement。
- 本文的 PR 切分、测试编号、文件路径和待冻结字段是实施建议。P2-01 的 ADR/API 评审后才成为产品契约。
- P2 不以规则关键词匹配器或手写 fixture 冒充真实 LLM；不交付完整 P3 Context Pack、P4 Memory、P5 多设备或 P6 Dashboard。原 VC-01/02 中 Context Pack 的完整端到端部分由 P3 集成验证，P2 要交付能被 P3 读取的真实 Truth。
- V0 已归档，禁止运行其 hook、MCP、自启动或复用旧数据库。保持现有 P0/P1 hook、信任及私有验收证据。

## 2. 开工先冻结的契约

### 2.1 输入、解释与来源

1. Interpreter 接受已有 `event_id`，从 Core 读源 Event；请求不得另传一段文字覆盖其原文。验证真实 source actor、project/task/session/host 关系，Agent 自述的 `USER_EXPLICIT` 不赋予用户身份。
2. 候选 Schema 覆盖 `TASK_CREATE_OR_ATTACH`、`REQUIRED_CAPABILITY`、`RULE/CORRECTION`、`DECISION`、`ACCEPTANCE`、`NO_STRUCTURE`。一个原话可产生多个候选；每项保留原文定位及其语义依据，不支持项明确不处理。
3. Interpretation 至少记录原设计字段 `interpretation_id/event_id/extractor_model/version/prompt_version/raw_result_json/confidence/status`，并冻结输出 schema、provider、配置 digest、输入 hash、完成时间及父解释引用。新增 ID 前缀必须注册到公共 ID 契约，不另造 ID 格式。
4. LLM 输出严格校验：未知字段、非法 enum、非有限置信度、错误引用或扩大 scope 不自动入 Truth。文本中的指令、引用、日志或工具输出均作为待解释数据，不由 Interpreter 执行工具或命令。
5. Raw Event 独立先提交，外部 LLM 调用不置于 Truth 数据库写事务中。超时、不可用、畸形输出留下可查询的失败/待处理记录，原话仍可读；不猜测高风险 ACTIVE Rule。
6. 冻结一个真实 provider，包含有限超时、最大输入/输出、model/version 和固定 prompt/schema。可用独立 fake provider 进行组件测试，必须标 `component_simulation`；真实 Gate 必须真实调用 provider。不得擅自购买服务、创建账户或将凭据写入仓库。默认优先复用已获授权的本地 Codex 能力；若需新 API 凭据，提出明确依赖并保留 BLOCKED。

### 2.2 Resolver / Policy

提交字段级的 `source_type × impact × scope` 策略矩阵及拒绝理由。原设计枚举为 `USER_EXPLICIT/USER_CONFIRMED/SYSTEM_CONFIG/AGENT_PROPOSED`、`LOW/MEDIUM/HIGH`、`GLOBAL/PROJECT/TASK/PATH/TOOL`；与 P1 实际 Rule scope/matcher 兼容性须由 ADR 明定，不能把设计枚举直接塞入旧 API。下表是本任务书建议的最低安全策略，由 P2-01 ADR 冻结；不要把所有具体自动/人工策略误引为原文逐项规定。

| 条件 | 至少应满足的结果 |
| --- | --- |
| 用户明确、低影响、当前 task 的能力要求 | 允许策略自动形成当前 Task requirement，并保留 source Event、Interpretation、Policy/version 和 Resolver reason |
| Agent 提议或 Memory 推断 | 至多 PROPOSED；不能自动 ACTIVE 或覆盖 State |
| “考虑/可能/研究”或存在歧义 | 不生成 confirmed DECISION；保存 tentative 候选或 Pending Review |
| 全局/项目高影响规则、HARD 条件变更、跨 scope 放宽 | Pending Review，由有权限的 human/system 经现有 Authority 边界操作；不得依置信度静默批准 |
| “已完成/已测试/验收通过”自然语言 | 不产生 PASS Evidence，不绕过 P1 的 VERIFIED/ACCEPTED 门槛 |
| 当前 Task 已存在的执行方式纠正 | 关联原 Task；不得悄悄新建另一 Task 逃避原规则 |

每次 resolution 写不可变命令 Event，并在同一事务完成全部派生 Truth、resolution 结果和引用；失败不留下半个 Task/半条 Rule。复用 P1 Authority、State、Evidence 业务入口，禁止直接 UPDATE 它们的表以绕过权限、history 或 revision。来源用户身份与执行服务身份分开记录。

涉及已有多个 Task/Rule/criteria 时，冻结整个受影响对象集合的 revision 前提、一致快照读取和事务锁定方式，不能仅检查单个 Task revision。COMMIT 成功但调用方未收到结果时，通过幂等命令查询/重放确定原结果，不盲目再次派生。

### 2.3 人工纠正、重跑和幂等

- Correct 保存新解释版本或 correction Event，保留原 Interpretation；已生效 Rule/criteria 的修正仍通过 P1 supersede/criteria 命令并检查当前 revision。冻结“只纠正解释”与“应用到 Truth”两个动作的区别。
- Reject 保留理由和操作者，不删除原话、旧解释或已生效历史；若需撤销已应用内容，显式提出受权限约束的 Truth 命令。
- Re-run 创建新解释引用原 Event/父解释，默认不改变 Current Truth。重新应用须再次经过 Policy 或人工动作；不可读取一次重跑结果就静默覆盖旧结果。
- 同源、同 model/prompt/schema/config 的相同请求精确回放；改变参数的新处理与显式重跑区分。Resolver 重放不得重复创建 Task/Rule。幂等 key、冲突状态及 HTTP 200/201/409 由 ADR/API 冻结。
- Review 更新采用正整数 `expected_revision`；两个 reviewer 同时处理只允许一方成功，另一方获 `409 revision_conflict`。令牌权限与 actor kind 双重限制，UI 不是权限来源。

### 2.4 待冻结 API 与迁移

原模块设计给出下列候选路径，不表示当前已存在：

| 方法与候选路径 | 必须明确的请求/响应 |
| --- | --- |
| `POST /v1/interpret` | 已有 event 引用、extractor 配置引用、处理幂等；解释/失败记录及 provenance |
| `GET /v1/interpretations`、`GET /v1/interpretations/{id}` | 分页及 project/task/source 过滤、版本、Raw/候选/处理状态 |
| `POST /v1/resolve/{interpretation_id}` | 原解释版本、policy、当前上下文 revision；action/reason/派生 Event/Truth ID |
| `GET /v1/reviews/pending`、`POST /v1/reviews/{id}/approve` | 有权限可見范围、分页、expected_revision、批准理由和结果 |
| `POST /v1/interpretations/{id}/correct`、`reject`、`rerun` | 版本关联、操作者、原因、冲突、重跑默认不改 Truth |

scope 建议分为 interpretation read/process、review read/manage；最终名称、actor 矩阵、异常码及列表范围在 P2-01 冻结。延续 `/v1`、Bearer、请求 ID、脱敏审计及 P1 错误格式。当前项目级读隔离能力需要在 ADR 说明，不能仅写“加 filter”就声称授权隔离成立。

同步 hook 的 provider 失败、超时、pending-review 行为也必须冻结：在 Agent 开始行动前使本轮成功解析或明确待处理/降级状态可见，说明何时阻止动作、何时保留原 Authority 下继续；不得以空上下文冒充“本轮新指令已生效”。原始 capture 成功与 semantic-processing 成功单独记录，现有 P1 Guard 不能因解释故障被停用。

Schema 当前 v6，下一个迁移号由本调度线程分配。不修改 `m0001`～`m0006`，迁移必须连续并含空库、既有库、重复执行和旧程序拒绝新 Schema 验证。P4/P6 的新增迁移只可在协调后入同一序列。

## 3. PR 与多 Agent 职责

| PR | 实现及交付 | 独立 Review | 独立验证及合并门槛 |
| --- | --- | --- | --- |
| P2-01 候选数据链与 Interpreter | ADR/API/Schema，Interpretation Store、严格 JSON Schema、固定 prompt/provider、源 Event 校验、失败记录、查询、幂等 | 候选无 Truth 权限；Raw 先提交；来源身份不可由模型自报；注入、输出限制、版本可追溯 | 真实 provider 输出至少一个用户指令候选；组件负例及迁移通过，不宣称完整 P2 Gate |
| P2-02 Resolver 与人工 Review | Policy matrix、resolution Event/atomic Truth、pending reviews、correct/reject/rerun、review权限、revision冲突 | 复用 P1 边界；高影响不自批；tentative不confirmed；纠正不改写历史；重跑不静默覆盖 | API 行为、并发、重复派生、故障注入、权限负例；原话→解释→resolution→Truth 可查询 |
| P2-03 Codex 输入接线与真实 Gate | 在原有采集路径上扩展同步解释/解析与最小候选/requirement结果读取；阶段脚本、私有安装审计、回归记录 | 解释超时行为、重复原话不会重复 Task；新 hook 如定义变化遵守用户 trust；不拿 P1 context 当 P3 Pack | 真实自然语言 Gate、独立 Review、CI、合并后受影响用例复跑，全部成立才 P2 PASS |

每 PR 至少有主要实现者、非作者 Reviewer、独立验证者；所有子智能体使用 `gpt-6.1-sol`。建议 Interpreter 与 Resolver 两名实现者按模块文件拆工，公共 migrations/ids/auth/API 文件由集成者串行处理。Reviewer 不自证作者结果，验证者检查最终 commit 和实际日志。

P2-01 → P2-02 → P2-03 按依赖合并；P3 先读契约、准备设计和组件，不在 P2 未通过前宣称真实续接完成。无需再次执行已足够覆盖且无变化的 P1 全量真实 Gate；针对具体集成影响做回归。

## 4. 验收矩阵

| 编号 | 操作及预期 | 必留证据 |
| --- | --- | --- |
| P2-T01 | 从 Schema v6 与空库升级，重启/重复迁移；旧 Event/Authority/State/Evidence 读写正确 | migration/checksum/commit/旧新记录计数 |
| P2-T02 | 真实 LLM 解释 Playwright 原话，得到创建/绑定任务与 task-scoped capability候选；Interpreter 阶段不改 Truth | 完整原话 Event、schema/model/prompt、原始结构输出、Truth 前后 |
| P2-T03 | 引用讨论、反问、否定、歧义、置信度畸形、越界 scope、未知字段及 prompt injection | 输出拒绝或pending理由；无越权 Truth |
| P2-T04 | LLM 超时/错误/进程中断：原话保留，待处理可恢复，状态不伪 PASS，不长期占据DB写锁 | 失败记录、计时、并行普通Core写入成功 |
| P2-T05 | Resolver 按公开策略把明确用户 Playwright 指令变成同 Task requirement；模型自报 USER_EXPLICIT/Agent 提议不能提权 | source actor、policy/version/reason、Rule/Task/current refs |
| P2-T06 | 考虑/可能/研究未confirmed；global/HARD变更进入pending；能力要求不能自动伪装确定性DENY matcher | 正负原话、Resolution 与 Authority 状态 |
| P2-T07 | 同一原话多候选同事务；中间及COMMIT故障、重复resolve；无半Truth、无双Task/Rule | rows/history/replayed/rollback与重启读回 |
| P2-T08 | Correct/Reject/Re-run保持旧解释，重跑默认不改Truth；已应用内容通过显式版本化命令修正 | 原解释与派生图、修正source/actor/reason、revision |
| P2-T09 | 两reviewer并发、跨project引用、缺scope/Agentapprove/credential形状负例 | 4xx/409、脱敏audit、Truth不变 |
| P2-T10 | 纠正“我不是让你用curl，我说使用Playwright skill”：同Task被正确约束，旧执行方案可追溯，绝不生成PASS Evidence/ACCEPTED | 两轮raw、candidate、resolution、Rule历史、State前后 |
| P2-T11 | P1回归：Agent不能自批准、高影响权限不变、Evidence/State门槛不变、fingerprint仍有效；列出受影响测试 | 具体commit与回归结果 |
| P2-T12 真实对话Gate | 在真实Codex用户输入上跑Playwright指令→纠正→tentativedecision→人工review/correct；真实provider、真实capture，最终Core链可读回 | 原话/真实hook/provider/candidate/policy/Truth/audit/CLI/tool记录、重启读回 |

Gate 使用无敏感临时场景，不要求测试任意真实用户项目。实际 Playwright 能力或 provider 未安装/不可用，明确该部分 BLOCKED；手写 JSON、mock hook、模拟 HTML 结果不能补为 PASS。P2 可证明自动形成 requirement 和纠正；若展示真实浏览器动作，必须确实调用并留轨迹。P3 尚未交付的 exact Context Pack 标 `N/A-P3`，并列后续 P3 联合验证，不虚构完整 VC-01/02 通过。

## 5. 证据、阶段判定及并行接口

- Validation Run 绑定 candidate SHA、dirty状态、schema/provider/model/prompt版本、actor身份、原话及source hash、Interpretation/Resolution/Review记录、派生Truth/Evidence refs、前后revision、请求ID、命令退出码、测试类型和原始失败。
- 失败分类沿用原任务书 `EXTRACTION_FAILURE/RESOLUTION_FAILURE/CONTEXT_FAILURE/AGENT_REASONING_FAILURE/GUARD_FAILURE/STATE_FAILURE/MEMORY_FAILURE/SYNC_FAILURE/EVIDENCE_FAILURE`，环境依赖未具备为BLOCKED，真实断言失败为FAIL。
- 本地验收资料含原话/DB/凭据，全部留 ignored/private runtime；公开PR仅列脱敏结果、commit和报告摘要。新模型数据外发须遵守已有授权和秘密边界，不打印tokens。
- P2 PASS须三PR合并、真实Gate+独立审查+CI通过、最终合并版本受影响回归通过。开发中只是候选实现，任务书不是实现证据。
- 给P3：已解析Task/Rule/criteria、source→Interpretation→Resolution完整图及review状态，不能把解释摘要当CurrentState。
- 给P4：可查询但非权威的Memory候选或eligible已提交State事件；P4不得替换Interpreter/Policy。
- 给P6：冻结read/process/review API和错误/分页；UI不自判批准策略，默认重跑不自动生效。

## 6. 当前线程执行入口

第一批启动 P2-01：实现Agent读本文和真实P1基线，先提交ADR/API/Schema与provider选择的可审查方案；Reviewer核对来源、权限和Policy边界；验证Agent准备严格schema/失败恢复负例。公共契约评审后再编码。完成P2 Gate之后按《P3阶段任务书》推进Checkpoint→Context/Resume→真实compact Gate。
