# Jasmine Core V1 P1 Truth Core 实施与验证任务书

状态：规划与验收契约草案。本文不表示 P1 已实现、任一测试已运行或 PR 已创建。阶段结论只以绑定具体 commit 的 Validation Run、独立 Review 和真实对话 Gate 为准。

## 1. 目标、输入与既有基线

P1 交付三条相互衔接的真源能力：版本化 Authority 与真实 Guard、带 revision 的 Task/Step State、可追溯 Evidence 与 Workspace Fingerprint。以结构化 API 预置 Rule 和 Step，再用真实 Codex 对话证明执行、验证、验收互不混淆，且一条 HARD Rule 确实阻止一个真实工具动作。P1 不以自然语言自动抽取、Context Pack、Hindsight、跨设备同步或完整 Agent lifecycle 为验收目标。

原始设计依据：《Jasmine-Core-V1-详细设计书》§3、§5–6、§12（权威层级、Event → Tool/Evidence、身份）；《Jasmine-Core-V1-模块设计书》§2、§5–8、§14、§19（Event、Authority/Guard、State、Evidence、Fingerprint、Adapter、安全）；《Jasmine-Core-V1-实施与验证任务书》§2–3、§7、§8 VC-03/06–09/26/28（P1 Gate、证据格式与负例）。这三个 DOCX 位于父目录 `jsamine core/Jasmine-Core-V1-设计实施验证文档包/`，是设计输入而非运行状态或本次操作指令。PR 数量与边界按 `docs/Jasmine-Core-V1-多阶段PR计划.md` 的建议采用 P1-01～03。

当前代码基线以待开发 commit 为准；编写本稿时，P0 `m0001_baseline` 中 `tasks.status` 默认 `open`、`revision=1`，无 Rule/Step/Evidence 表；`m0002_api_auth_audit` 为 API key 与审计；P0 HTTP 契约在 `docs/api/core-api-v1.md`。P0 ADR 0001–0004 已冻结公共 ID、前向迁移、Event 原子性、`expected_revision` 与 `revision_conflict`、原文保留及鉴权审计。P1 必须在新迁移与新 API 契约中延续这些约定，不修改已发布迁移，也不借用退役 V0 实现。

## 2. 开工前冻结的 P1 契约

P1-01 的首个评审点是提交短 ADR/API 修订和前向迁移设计；以下精确字段、权限与失败语义须在三条实现线共用，任何后续变更走版本化 PR。

1. **Authority 身份与版本。** P1-01 决定以稳定 `rule_id=rul_…` 标识 Rule、`rule_key` 为业务可读字符串；`rule_versions` 的版本内容不可变，`rules` 仅存 current version/status/revision 投影。每版冻结 `kind`（`RULE|DECISION|ACCEPTANCE`）、`severity`（`HARD|NORMAL`）、`enforcement`（`CONTEXT|DENY|CONFIRM|VERIFY`）、`status`（`PROPOSED|ACTIVE|SUPERSEDED|RETIRED`）、scope、确定性 `matcher`、`origin_event_id`、提议/批准 actor 与时间。`origin_event_id` 必须引用已有、同上下文的 Raw Event；每次命令另在同一事务写 command Event 与投影。版本与状态变化有不可变 Event 和审计。全局/project/task scope 关系和唯一性由 ADR 0005 明定；HARD 祖先规则保留，P1 暂仅允许同 scope 经显式 USER 确认 supersede，不能由子 scope 静默遮蔽。
2. **Authority 权限与 Guard。** 鉴权 actor 身份从 token 取得，绝不由请求体自称 USER。专用 `authority:propose`、`authority:read`、`authority:manage`、`guard:check` scope 与 actor.kind 双重校验；Agent 即持 `admin` 也不得通过为 human/system actor 发 key 来提权。Agent 只能创建 PROPOSED；human/system 加 `authority:manage` 才能批准/替代/退役。proposal 创建不带 `expected_revision`，approve/supersede 更新必须带当前 Rule 的 `expected_revision`。确定性 `tool/action/path` matcher 由 ADR 0005 给出语法与归一化，未知/不可判定输入拒绝；Guard 决策为 allow/deny/confirm/verify，并返回命中版本。语义 Rule 走 `CONTEXT`/`VERIFY`。`GET /v1/rules/active` 和 `POST /v1/guard/check` 的输入与输出在接 hook 前定稿。只提示规则的 Adapter 标为 `ADVISORY`，有真实阻断能力且实测后才标 `ENFORCED`。
3. **State 与 revision。** Task 状态按设计 `ACTIVE|BLOCKED|ACCEPTED|CANCELLED`；Step 为 `PLANNED|IN_PROGRESS|EXECUTED|VERIFIED|ACCEPTED|FAILED|BLOCKED|STALE|SKIPPED`。新增前向迁移明确将 P0 的 `tasks.status='open'` 映射为 `ACTIVE`，包含既有库与空库升级测试。创建 Step、状态迁移、Task 验收均走 Event → projection 同事务，成功 revision +1；更新必带 `expected_revision`，冲突返回 P0 API 已冻结的 `409 revision_conflict`，附当前 revision/快照，不采用模块设计中的 `STATE_STALE` 作为新公开错误码。需明确 Task 与 Step 各自 revision、创建 Step 是否增加 Task revision、任务验收对各 Step 的要求，以及 `SKIPPED`/`CANCELLED`/`BLOCKED` 的合法转移与恢复路径。
4. **Evidence 与 Fingerprint。** 冻结 `evd_` ID、Raw `source_event_id`、task/step 引用、kind、`PASS|FAIL|INFO`、结构化结果、artifact URI/hash、适用的 workspace fingerprint、来源 host/actor、发生时间。产品 Evidence 与 Validation Run 分开；Agent 自述或自然语言断言不能充当测试通过。Fingerprint 至少能表示 `git_head`（可空）、dirty/changed paths、selected hashes、无 Git manifest、artifact hash 与 partial/completeness；冻结比较算法、相关输入范围及 mismatch/unknown 判定。`VERIFIED`/`ACCEPTED` 需要符合验收条件、source 与当前 workspace 的 Evidence；过期证据保留历史但不能充当当前通过。artifact 丢失时保留 Event，并标记 `BROKEN_REFERENCE`。旧 Evidence 使已验证 Step 进入 `STALE` 的触发与事务须明确。
5. **API、迁移、审计。** 沿用 P0 `/v1`、JSON、标准错误形状、bearer token、`X-Request-Id`、Event 幂等与原文处理。为 Rule/State/Evidence/Fingerprint 定义精确请求/响应、scope（读/写/批准/Guard/证据）、引用 404、非法迁移/证据不足/匹配器不可判定的 4xx、`expected_revision` 缺失 400、回放与并发冲突 409。所有 Truth 写入必须有可追溯 Raw Event；失败不留半笔 Truth，拒绝仍写脱敏审计。新迁移校验和与 schema version 从 P0 继续递增；不得编辑 `m0001`/`m0002`。P0 `events` 已有有限 event type 校验，P1 新事件种类需连同内部/HTTP 写路径一起版本化扩充。

任何未决字段或权限矩阵不能由某个 PR 的测试替代设计决定。冻结结果应在 `docs/adr/` 与 `docs/api/` 给出字段级例子和兼容性说明，独立 Reviewer 签字后供下游复用。

## 3. PR 边界、依赖与独立职责

| PR | 实现边界与交付 | 独立 Review 焦点 | 独立验证焦点 |
| --- | --- | --- | --- |
| **P1-01 Authority / Guard** | 从 P0 基线出发，提交 ADR 0005/API 修订、`m0003` 前向迁移；Rule/版本/scope/proposal/approve/supersede 与查询；Guard 的确定性 matcher 和审计。结构化 API 可直接预置规则，不宣称自然语言 Resolver。 | actor 不能冒充 USER 或借 admin 发 key 提权；Agent proposal 不可直 ACTIVE；HARD 父 scope 不被隐蔽覆盖；版本不可原地改；未知 matcher 拒绝。 | 版本与权限负例、scope 冲突、Guard 判定的纯 API 证据；此 PR 尚不单独宣称真实工具拦截。 |
| **P1-02 Task State** | 依赖 P1-01 冻结的公共契约；Task 状态迁移、Step 创建/转移、revision 乐观锁、Event 与 history、Task accept 入口。Evidence 门槛先定义并在 Evidence 可用前保持拒绝；不得用空占位通过。 | P0 `open` 数据迁移；每次真源变更 +1；非法转移、旧 revision、并发、回滚；状态名称与 API code 无分叉。 | 旧库/空库升级，状态路径与拒绝路径，两客户端旧 revision 被拒；无 Evidence 的 VERIFIED/ACCEPTED 必拒绝。 |
| **P1-03 Evidence / Fingerprint / Codex 接入与 Gate** | 依赖 P1-01/02；Evidence 原始结果先落 Raw Event、结构化解析、artifact 引用、Fingerprint 采集/比较；完成 State Evidence 校验；Codex PreToolUse/PostToolUse、最小 Rule/State 读取，真实 Gate 与记录。只声明已实测的 hook 能力。 | source Event 和 workspace 绑定、伪造 PASS、敏感信息泄露、stale 判定；hook 在阻断时是否确实不执行工具；适配层不直改 Truth。 | 真实 Codex PreToolUse DENY 与 PostToolUse Evidence、执行/验证/验收区分、workspace 改变后 STALE；逐项保留原始工具轨迹。 |

每个 PR 指派主要实现者、非该 PR 主要实现者的 Reviewer、独立验证者；Review 阻断项关闭后才进入合并候选。下游可并行编码，但合并顺序 P1-01 → P1-02 → P1-03，所有验收结论绑定明确 commit。阶段最后一个 PR 合并前跑真实 Gate，合并后在最终 commit 复跑受合并影响的用例。

## 4. 可执行验收矩阵

| 编号 | 操作与预期 | 必留证据 |
| --- | --- | --- |
| P1-T01 | 从空库和含 P0 `open` Task 的既有库分别升级；Schema 版本递增、Task 映射明确，二次启动无重复迁移，P0 Event/对象仍可读。 | 迁移前后 schema、Task 查询、命令/退出码、commit。 |
| P1-T02 | Agent token 提案 HARD Rule，只得 PROPOSED；Agent 直接 approve/伪装 USER、缺 scope 的请求被拒。授权 USER/SYSTEM approve 后为 ACTIVE；新版本可查旧版本，不能原地覆盖。 | token 脱敏后的请求/响应、actor、Rule 版本、source Event、审计。 |
| P1-T03 | 创建 global/project/task scope 与 path/tool/action 相交的规则；验证 HARD 祖先规则不能由子 scope 静默放行，跨 scope 例外被拒；仅同 scope 经授权显式 supersede 才生成新版本并保留来源。无匹配与未知 matcher 不虚报已强制。本阶段不要求次数型例外。 | active 查询、Guard 决策及命中版本、scope 和 matcher 输入。 |
| P1-T04 | 创建 Step，走 `PLANNED→IN_PROGRESS→EXECUTED`，区分 Task/Step revision；非法跳转和缺 `expected_revision` 被拒；两个客户端用同一旧 revision 更新时只有一个成功，另一个 `409 revision_conflict`，无 last-write-wins。 | 前后快照、history/Event、两请求结果、行计数。 |
| P1-T05 | 用 Agent 回答“已通过”或只有构建成功 Evidence 请求 Step VERIFIED/Task ACCEPTED；验收条件不满足时拒绝，State 不变。正确的测试/设备/用户确认 Evidence 绑定后才允许相应迁移，Review Evidence 单独不足以验收。 | 每个 Evidence 的种类、source Event、fingerprint、验收矩阵、状态前后。 |
| P1-T06 | PostToolUse 接受一次真实工具结果：Raw TOOL_CALL/TOOL_RESULT 先落库，解析 exit/pass/fail/path/hash，生成关联 Evidence；同一源事件重放不重复派生。工具失败不能生成 PASS 或 VERIFIED。 | 真实 hook 输入的安全摘录、原始 Event、Evidence、重放行计数。 |
| P1-T07 | 采集可比较 fingerprint，改变相关输入后旧 PASS Evidence 仍可查但不可用于新 VERIFIED/ACCEPTED；已验证 Step 转 STALE；partial/unknown fingerprint 不伪装完全匹配。 | 改动前后 git/hash/manifest、compare 结果、Evidence 与 State history。 |
| P1-T08 | 对 P1 写入的 Event 后、projection 前及 COMMIT 失败路径注入故障；不得留下半笔 Rule/Step/Evidence/State 或虚假成功。Raw Event 直接 UPDATE/DELETE/REPLACE 仍被拒。 | 故障输出、前后行数、事务状态、重启读回。 |
| P1-T09 | 权限不足、跨 project 引用、非法证据与凭据形状输入负例；Truth 不变，审计与 Validation 记录不泄露凭据，Event 原文遵循 P0 ADR 0004。普通无敏感测试原话可完整保留。 | 错误码、审计、前后查询、若有 redaction 则记位置。 |
| **P1-T10 真实对话 Gate** | 在获授权的真实 Codex 对话中，先经结构化 API 配置 task-scoped HARD DENY 和 Step 验收要求；Agent 尝试一个被禁工具动作，PreToolUse 实际阻断，工具无执行结果；再做一个允许的真实工具动作，PostToolUse 产生 Evidence；验证只有 EXECUTED、证据齐备后 VERIFIED、条件齐备且有明确验收后 ACCEPTED。 | 真实对话、Rule/State before/after、真实 PreToolUse 决策与工具未执行证明、PostToolUse 原始结果、Evidence/fingerprint、Event/审计链、commit 与设备/adapter 能力。 |

T01–T09 可用自动化和临时测试库，T10 必须是真实用户–Agent–工具交互。合成 hook payload、模拟请求或单元测试只能证明组件行为，不能替代 T10。Codex PreToolUse 的 deny 能力必须在实际所用工具类型上实测；PostToolUse 无法撤销已发生的副作用。项目 hook 的信任由用户在 Codex 中审查并完成；在此前可完成代码、独立 Review、模拟验证与具体安装候选。若实际 hook 未被信任、被测工具不支持 PreTool 阻断、真实入口不可用，或用户尚未完成 folder trust，T10 标 `BLOCKED`，记具体原因和已完成部分；不得自动确认用户 trust、换用未授权 hook、改全局 hook 配置，或把 `ADVISORY` 写成 `ENFORCED`。已运行却违反断言的是 `FAIL`，不能改标 `BLOCKED`。

## 5. Validation Run 与阶段判定

沿用 P0 Recorder 的脱敏及 commit/environment 记录，并按原任务书 §7 留下 USER RAW、State Before/After、实际工具调用和结果、Rule 版本、Evidence 原始与摘要、Verdict、失败类别及复现步骤。P1 尚未实现的 Interpretation、Context Pack、Memory、Checkpoint 明确记 `N/A（P1 未实现）`，不得伪造输出。专门用于验收的无敏感短对话可在证据包完整保留；禁止保存 token、私钥、`auth.json`、无关私人对话全文、V0 state、数据库文件。若清洗器发现凭据等敏感内容，保留位置/原因和清洗后的证据，同时判安全用例 FAIL。验证者保存原始失败，而不以一次重试成功覆盖。

单 PR 完成须契约、迁移、接口、必要自动化、独立 Review、独立验证均有记录。**P1 PASS** 仅在 P1-01～03 均合并、T01～T10 在候选及最终 commit 的适用范围内通过、真实 Guard 阻断和 Evidence/State 链可追溯时成立。真实 Gate 未运行或用户交互未完成为 **BLOCKED**；任何已运行断言失败为 **FAIL** 并附原始输出与 failure class（例如 `GUARD_FAILURE`、`STATE_FAILURE`、`EVIDENCE_FAILURE`）。P1 不以文档写成 PASS 作为证据，不声称 P2 自然语言抽取或 P3/P5 lifecycle 与跨设备能力已通过。
