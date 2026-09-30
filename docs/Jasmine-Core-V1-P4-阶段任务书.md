# Jasmine Core V1 P4 阶段任务书：长期 Memory 与持久异步作业

日期：2026-09-29。状态：可委派的实施任务书，尚未实施或验收。准备时 checkout 为 `main@9059625`，P1 已完成；启动 Agent 必须重新核对 HEAD、工作树、仓库内 AGENTS.md 与实际 P2/P3 合并状态。仓库：`/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1`。父目录不是 Git 仓库。当前工作仅编写任务书，不部署服务。用户明确委派外部 Agent 实施 P4 后，允许必要的项目/临时目录隔离开发测试环境、按官方版本准备依赖及本地 Hindsight 测试实例；禁止擅改/停止用户现有服务、生产自启动、外部付费账户、全局 hooks/config/secret 或 trust。

## 1. 来源、原要求与本任务书的建议

设计输入原件hash、大小和便携交接结构见 [阶段任务书设计输入索引](Jasmine-Core-V1-阶段任务书-设计输入索引.md)。

原设计文件为 `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jsamine core/Jasmine-Core-V1-设计实施验证文档包/Jasmine-Core-V1-详细设计书.docx`、同目录 `Jasmine-Core-V1-模块设计书.docx`、`Jasmine-Core-V1-实施与验证任务书.docx`。外部 Agent 必须收到包含三份原 DOCX 与 SHA manifest 的完整便携任务交接 ZIP，或具有上述原文件访问权；缺设计输入先补交接包，不仅依赖 Git checkout。原 DOCX 不发布到 public Git，设计包保留。`artifacts/phase-planning-20260929/` 三份同名 TXT 仅为可选本地完整抽取，在其他 checkout 可能不存在。设计要求不代表代码存在，也不构成立即执行环境操作的指令。

| 来源章节 | 原要求 | 本任务书落点 |
| --- | --- | --- |
| 详细设计书 §3 数据边界、§7 长期记忆 | Authority 高于 Memory；引擎可替换；五类稳定经验；global/project banks；Recall 常规、Reflect 明确请求；异步写入 | §2、§4、§5 |
| 详细设计书 §8 Context、§9 Dashboard、§12 安全、§14 故障 | Core 统一构建 Context；Memory Inspector；ingestion secret filter；故障仅暂停 Memory | §3、§6、§7 |
| 模块设计书 §11 Hindsight Adapter | retain/recall/reflect、source mapping、source-event 幂等 document、合并去重、obsolete/conflict 过滤、禁自动插件注入 | P4-01 |
| 模块设计书 §12 Memory Job Worker | eligible event/candidate；PENDING/RUNNING/DONE/FAILED；retry_count/source_event_id/last_error；GET jobs 与 POST retry；先提交 State 再 enqueue | P4-02 |
| 模块设计书 §10 Context Builder | 先 HARD Authority/Current State/Checkpoint，后 Memory；保存 exact pack、result IDs、预算；失败 degraded | P3 对接契约 |
| 实施与验证任务书 §1–3、§7–8 VC-14～19、VC-22、§9–10 | Truth 独立后接 Hindsight；WS-F；真实召回帮助任务；旧记忆不覆盖新 Decision；未知不编造；项目隔离；宕机独立 Truth | §7 真实 Gate |
| docs/Jasmine-Core-V1-多阶段PR计划.md | P4-01/P4-02 拆分、独立 Review/验证、候选与最终 commit Gate | §8、§9 |
| ADR 0001–0004、0005–0007；docs/api/core-api-v1.md | 公共 ID、前向迁移、事务、鉴权审计、P1 Truth/Evidence 契约 | 不得改写的基础 |

**新增建议而非原文已经冻结的要求：** 下文候选字段、授权 scope、分页、租约、恢复扫描、错误码、文件分工、PR 顺序细节、证据 manifest、真实收益比较方式。实施前由负责人和独立 Reviewer 明确批准成 ADR/API 契约；没有冻结不得把候选实现冒充已定设计。

阶段 PR 计划已由 root 刷新到 P1 完成/PUBLIC/schema 6；实施启动仍须读取当前 Git/API 状态，不能据旧计划推断交付。`9059625` 的 `src/jasmine_core/` 已有 Event、Authority、State、Evidence、Fingerprint、Capture、API 与最小 Validation；未见独立 Memory Adapter/worker 模块或 Memory 专属迁移。P2/P3 依赖须在启动时查实际代码，不凭文档标题判断。

## 2. 目标与不可越过的边界

P4 完成应能从合规 Raw Event/已验证经验异步 retain 到真实 Hindsight，在新真实 Agent Session 的 Core Context Pack 中 Recall，追溯 source，并对实际任务有可观察帮助。Hindsight 故障时 Rules/State/Evidence/Checkpoint 仍可完成正常流程，job 可恢复，Memory 不产生 Truth 权威。

1. 只允许 `CONFIRMED_DECISION`、`STABLE_PROJECT_FACT`、`RESOLVED_ISSUE`、`VERIFIED_OUTCOME`、`FAILURE_LESSON`。类别名字不是可信证明：Decision 必须关联已确认 Authority 来源，Outcome 必须关联合规 Evidence，失败经验须有真实失败来源。禁止把当前 step 状态、task revision、未验证猜测、临时 plan、自述 PASS 当长期真源。
2. Memory 不能 activate/supersede/retire Rule、迁移 State、生成 Evidence、覆盖 Checkpoint 或增加 Task revision。HARD Rule 确定性加载，不能以 top-k Recall 代替。
3. Agent 不能直接选 bank、写 bank、操作 Hindsight 自动注入或凭结果要求晋升权威。使用已有认证 actor 与 Registry；请求 body 自称 human/system 不产生权限。
4. 默认 bank 为 `jasmine-global` 与 `jasmine-project-{project_id}`。项目 A 经验不得出现在 B 的 Recall/Inspector/作业列表；global 只容纳经明确授权的全局内容。task 属于 project 后才可使用该 project bank，不猜 cwd、不绑定 V0 project。
5. Hindsight 和 LLM 网络调用禁止发生在 Truth 写事务、SQLite 写锁或 Guard 决策路径中。不得因 Memory 失败回滚已提交 State，不把远端错误映射成 Truth 不可用。
6. 普通 Context 仅 Recall。Reflect 必须明确历史综合请求及权限检查，结果仍是非权威历史分析；不能因为普通查询问法复杂而自动 Reflect。
7. `_archive/` 是 V0 历史。不得复用旧 service、SQLite、token、launch agent、MCP、hook、绑定或自称旧召回结果证明 V1。允许在明确委派实施范围内准备隔离本地真实 Hindsight 测试实例；生产服务/外部付费账户/全局配置无授权不得改。仍缺真实环境或必要权限则记 BLOCKED。

## 3. 并行边界与启动冻结契约

P4 可在 P2/P3 实施期间编写隔离模块与离线测试；真实 Gate 和最终合并依赖相应真实输入/Context 接入就绪。P6 可读取冻结契约制作 UI，但其 fixtures 只能标 mock/unverified。

| 领域/文件 | 写入负责人 | P4 允许读/调用 | 禁止 P4 自行重写 |
| --- | --- | --- | --- |
| P2 Interpreter/Resolver、candidate/review 数据 | P2 | 消费冻结的已决议 candidate/eligible Event；保存来源与版本 | 自造 source×impact×scope 策略、自然语言解释/人工审批链 |
| P3 Checkpoint/Context/SessionStart 与 compact hooks | P3 | 提供 Memory provider 协议和结果；消费 current Truth 快照；保存 query trace | 改 Context 权威加载顺序、token 总预算、hook 安装或注入 |
| P4 `memory/`、adapter/worker/jobs/provenance | P4 | 追加新实现/专属测试/运维说明 | 直接 UPDATE P1/P2/P3 真源 |
| P6 Dashboard/UI | P6 | 返回 jobs/health/Memory Inspector 专属 API | 写前端、共享 fixture 当真实数据、SQLite 直写 |
| migrations、SCHEMA_VERSION、auth scopes、API route registry、errors | 集成负责人单一写入窗口 | 提交差异提案，由指定 PR owner 应用 | 多 Agent 同时编辑、抢占相同迁移号、修改已发迁移 |
| P1 state/authority/evidence、P0 db/event store | 原领域 owner | 按现有 API 读取/合法调用，审阅候选 integration patch | 扩大业务语义以迎合 Memory、破坏事件不可变或 revision |

开始 P4-01 前冻结：引擎版本与 transport/SDK 选择、bank mapping/实例边界、调用截止时间、provenance 结果协议、secret filter、Memory 权限、P3 provider 签名。开始 P4-02 前另冻结：P2 eligible candidate/source 接口、enqueue durable 恢复策略、job 状态机与 claim、重试及 Inspector 契约。冻结记录需含 ADR 编号、owner、commit、已决与未决项；只允许已冻结字段跨 Agent 消费。

推荐 provider 输入为 `project_id/task_id/session_id/query/types/max_tokens` 与当前已加载 `authority_refs/state_revision`；身份由 Core 调用上下文传入而非不受信 body。输出为 `status=ok|empty|degraded`、items、查询 banks、filtered IDs/reasons、预算统计与安全的 error_code。P3 写 exact rendered pack 与 `memory_result_ids`，P4 写 Recall trace。**这是候选协议**，必须与 P3 签名对齐，不创建第二套 Context Builder。

## 4. P4-01：Hindsight Adapter、banks、权限及权威隔离

### 4.1 交付

建议新增 `src/jasmine_core/memory/adapter.py`（可替换协议）、`hindsight.py`（真实 transport）、`scope.py`、`provenance.py`、`filtering.py` 与对应测试。命名由 ADR 最终冻结。交付以下可调用行为：

- `retain(bank_id, document_id, content, context)`、`recall(bank_id, query, max_tokens, types)`、`reflect(bank_id, query)` 的 Core 内部 adapter；远端配置只由受信操作员提供，日志不打印 credential。
- Core 从合法 project/task 推导 bank；查询 global+当前 project，先分别保留来源，再 merge/dedupe/filter/budget。去重保留所有合法 provenance；不能吞掉不同来源或把跨 bank ID 冲突误合并。
- 返回每条结果的 `memory_result_id`、engine ID/document ID、bank/scope、memory_type、source_event_ids/evidence_ids、origin rule/version（若有）、obsolete/conflict 标记及过滤理由。source 丢失或无法验证的结果不得混入可追溯 Context；明确降级/过滤。
- token budget 从 P3 分配的 Memory 分区取得；没有 Memory 预算则不查询或不给正文，不裁剪 HARD Authority。长度/预算超限可预测，远端报 token 数不能替代本地审定计量。
- ingestion 前 secret filter；可疑内容拒绝或脱敏策略在 ADR 定义。保留安全摘要与 reason，原始 Event 按已有权限可追溯，secret 不进入 Hindsight 或审计错误字符串。
- 禁止自动 retain/recall 插件模式，禁止 Hindsight 自动注入 Agent Context。健康/可用性独立于 Core health，Memory 不可用不宣称 Core dead。
- 只提供冻结的只读 Inspector/trace 和 health API 候选，不做 P6 页面。

### 4.2 必须 review 的未定项

真实 Hindsight 当前部署 API/SDK、retain 是否异步及完成回执、document upsert 幂等语义、远端来源返回方式、认证/bank 权限、分页/超时/取消、token 单位、删除/obsolete 能力均不能凭经验猜测。实施 Agent 使用当时官方资料核对并附版本/链接；没有服务使用真实测试 BLOCKED，fake 只验证 Core 边界。

`jasmine-global` 的服务实例隔离须明确：该命名只在同一受信使用域中全局，不是所有用户共享。当前 Core scopes 不自动等于项目级 ACL。若无 project authorization 数据，Reviewer 必须选择并记录受信单用户前提或另行批准 ACL 扩展；禁止把 UI 筛选或调用方给 `project_id` 当权限证明。必须区分 deployment mode：受信单用户实例允许该用户访问其所有项目，跨project查询仍限制当前task结果但不构成用户ACL；multiuser/限制项目模式须经批准实现并验证后端 project ACL。G04在单用户mode验证A不污染B、task/project一致性和bank注入拒绝；只有启用真实ACL的mode才测试并声称无权actor访问A被拒绝。不得在没有ACL的单用户mode声称未授权project读取PASS。

### 4.3 自动化验证清单

| 类别 | 必测行为与可判定结果 |
| --- | --- |
| 正例 | 五类合法内容、global+project 合并、多 source 去重、带真实 Event/Evidence 来源、预算边界、明确请求 Reflect |
| 负例 | agent bank injection；跨 project task；project→global 提升；伪 actor；错误 scope；撤销 token；未知 type；普通 Context 不调用 Reflect |
| 权威隔离 | 旧 Decision 与当前 ACTIVE Decision 冲突被过滤/标 obsolete；历史教训只标历史；Recall 不产生 Rule/State 写入，Task revision 不变 |
| 来源/污染 | 远端伪 source、不存在 Event、项目不符 Evidence、无 provenance、恶意返回文本要求绕过 HARD Rule、secret 输入与远端错误均不会进入安全输出 |
| 失败 | 连接拒绝、deadline、401/403、429、5xx、畸形 JSON、超大结果、部分 bank 失败均有明确状态/理由；Core Truth 可独立工作 |

单个 bank 部分失败是否保留另一 bank 合规结果，须冻结成显式 `degraded` 策略；不得输出 `ok` 掩盖失败。远端故障与合法 empty 必须可区分。

## 5. P4-02：durable asyncMemoryJobs 与故障恢复

### 5.1 交付及候选 Schema

交付 job store、worker 显式启动入口、eligible-event/candidate enqueuer、重试 API、审计、恢复及真实 Gate runner。`asyncMemoryJobs` 是异步职责称呼，原设计持久对象为 `memory_jobs`；不要据任务名另造并列队列。

候选 `memory_jobs` 字段：Core 分配 `job_id`（前缀须公共 ID review）；`source_event_id`/candidate ID/version、project/task、memory_type、resolved bank、document_id、sanitized content hash、status、retry_count、next_attempt_at、lease_owner/expires_at、last_error_code、safe_error_summary、created_at/updated_at、DONE 回执/engine document reference。约束和索引应覆盖 source/type/destination 组合唯一性、合法 status、来源 FK、项目一致性与 due-claim 查询。candidate version、多个 source 的关系表是否必要必须 review；没有证据不允许标 VERIFIED_OUTCOME。

候选 `memory_job_attempts`/job change Events 保存 claim/retain/retry/completion 的不可变记录；不得用覆盖 last_error 代替完整来源追踪。`memory_query_traces` 与 source mapping schema 属 P4 新迁移，同一 migration owner 管理。P4 不写 P6 专属表。

### 5.2 持久性与事务顺序

原设计是 State/Event 先提交、再 enqueue。实现必须解决 **Truth commit 成功、enqueue 前进程崩溃** 的窗口，不能把一句“异步”当 durable 保证。

建议采用已提交不可变 Event 的可恢复扫描：enqueuer 用持久 cursor 和唯一键从冻结的 eligible 来源创建 PENDING，job insert/cursor 更新在同一独立短事务；重启重扫/补扫所有符合条件且未存在 job 的来源。P2 candidate 的批准/纠正资格须由 P2 可追溯事件暴露。扫描不改源 Event，已回滚/未提交事件不会被消费。其他方案如 outbox 需负责人单独 review 与原设计的先提交语义如何一致；不得自行改 State transaction。

worker 顺序：短事务原子 claim due PENDING 或到期 RUNNING → 提交 claim → 无数据库写锁地调用 retain → 短事务更新 DONE/失败与 attempt。多个 worker 竞争不得双 claim；lease 到期恢复不能遗失 job。远端 retain 成功、本地 DONE 前崩溃必须用稳定 document_id 复送，不产生重复语义记忆。Exactly-once 远端无法证明时诚实描述 at-least-once + 幂等，不虚称全链 exactly-once。

状态默认原文 `PENDING/RUNNING/DONE/FAILED`；建议 retryable failure 回 PENDING 并设置 backoff，耗尽或永久错误 FAILED。retry_count 的首次执行/手工 retry 计数、最大次数、截止时间、jitter、租约与异常分类须冻结。手动 retry 不得改变来源/content/bank，不重试 DONE 或制造副本；重复请求具幂等和并发冲突语义。Memory job lifecycle 不增加 task revision。

### 5.3 API 候选与 P6 边界

原文已给出 `GET /v1/memory/jobs` 与 `POST /v1/memory/jobs/{id}/retry`，路径须保留。以下为扩展候选：job detail、Memory item/trace、独立 health；不自动新增公开 retain/global-write 端点。

| API | 候选权限/行为 | 必须冻结 |
| --- | --- | --- |
| GET jobs/detail | `memory:read`，project/status 过滤，稳定 cursor 分页，只返回已授权 scope，安全错误 | 跨项目权限、空结果/不存在与权限错误防泄漏 |
| POST job retry | `memory:manage` + trusted human/system；append 命令 Event/audit；不改 Truth | revision/幂等 request ID；RUNNING/DONE/FAILED 语义；审计 actor |
| GET Memory items/trace | `memory:read`；非权威、source link、last recalled/obsolete/pinned 字段 | pinned 是否仅 display/排序；不得影响 Authority；raw Event 另查 events:read |
| GET Memory health | 安全状态与 last success/error、pending/failed 计数 | 是否需认证；不得回 endpoint credential 或原始异常 |

项目权限必须由后端判定。P6 通过这些 API 读与 retry，不能借 Inspector 直接写 SQLite/Hindsight。完整P4须交付authenticated/audited obsolete/delete/pin管理API，不能仅以只读Inspector满足原设计。最终路径/生命周期须ADR冻结，见下节；pin不等于Rule activation。

### 5.4 Memory 管理生命周期（P4-02 的必要交付）

详细设计§9要求Memory Inspector可做obsolete/delete/pin；P6消费相同Core API。建议 `POST /v1/memory/{id}/obsolete`、`/delete`、`/pin`（含unpin），路径/请求/错误尚为候选。由认证human/system + memory:manage（以及配置mode中的project ACL）管理，agent不得借admin或伪actor绕过；每操作追加命令Event/audit，记录actor、来源、before/after、revision/幂等ID。更新需expected_revision并检查并发；错误/重复命令语义冻结。

逻辑obsolete保留Memory元数据/所有Raw Event/Evidence来源，默认Recall/Context排除并显示原因；delete定义为删除目标远端Memory/document并本地保留tombstone与provenance，不删除Core source、Rule/State/Evidence。异步远端删除失败时本地先排除召回并记录pending/failed deletion作业，不能宣称远端已物理删除；恢复/幂等重试有回执。pin/unpin只影响Inspector排序/允许的Memory选择，不能使obsolete/conflicting结果复活，不能改变Authority或压缩HARD预算。re-retain同一来源对tombstone的策略须review防止误复活。

Lifecycle Gate P4-G07：真实新Memory执行pin/unpin、obsolete及delete，actual Context分别确认非权威/obsolete不注入、远端delete读回不存在并保留Core来源，管理无权actor拒绝，重复/过期revision拒绝或replay，远端删除故障保留排除状态并恢复成功。需真实engine receipts、管理Event/audit、source读回/pack和tombstone，fake-only不验收。纳入P4-02最后PR scope与独立Review/验证/merge；如负责人决定拆独立P4-03，必须以P4-03为阶段最后PR并等G01～G07完整PASS，不把P4-02提前宣称完整P4。

### 5.5 自动化/故障验证

- 正例：合法 committed source 自动 enqueue；同 source 重放仅一 job；retain 回执后 DONE；重启 jobs/attempts/source mapping 可读；授权 retry 有 actor/audit 与幂等；合法无记录返回 empty。
- 负例：未批准 candidate、未验证 Outcome、自述 PASS、未提交/回滚 Event、跨项目 source、secret 内容均不 retain；agent 手动 global write/retry 被拒绝；DONE 重试不重复 retain。
- durable 失败点：Truth commit 后 enqueue 前崩溃；job insert 后 cursor 前事务失败；claim 后网络前退出；远端成功本地 DONE 前退出；两个 worker 同时 claim；lease 到期；重试期间服务恢复。每点检查来源、job 数、状态、回执及远端 document 不重复。
- 降级：worker 未启动/Hindsight 不可达、超时、失败耗尽、队列堆积时 Truth API/Guard/State/Evidence/Checkpoint 不等待 Memory deadline、不回滚。检查无网络调用跨 SQLite write transaction、无锁占用随远端时延增长。
- 迁移：空库、新增迁移、已有 P1/P2/P3 数据库升级与重复执行；已发 migration checksum 不变；Schema_VERSION 和 contiguous migrations 一致；索引/约束能防绕过 store 写入。

## 6. 实现、Review、验证 Agent 分工及 PR 顺序

每个 PR 用独立实现、Review、验证角色；同一主要实现者不得代替独立 Review/验证。角色可依次在同一槽位执行，不要求同时开很多 Agent。

1. **冻结小步**：P4负责人整合 P2/P3/P6 接口意见，Reviewer 审 candidate schemas/API/权限/durable 缝隙，登记共享文件和迁移 owner。未决阻断项先关闭。
2. **P4-01实现 Agent**：只写 adapter/scope/provenance/filter、专属 tests、ADR/API 文档提案；依冻结接口提交可独立 review 的 PR。真实 service 未就绪可做 fake transport boundary，但结论为部分交付，不能称 P4 通过。
3. **P4-01 Review Agent**：重点 bank/project/global 权限、Memory 非权威、secret、Reflect、P3 调用边界与官方真实 transport 语义。检查所有问题固定在候选 commit。
4. **P4-01 验证 Agent**：独立复跑 boundary tests，使用已授权真实服务验证 retain/readback/recall/provenance；无真实服务记 BLOCKED。P4-01 合并需要其功能真实路径可证，不能把假引擎 adapter 当真实 Hindsight adapter 已验收。
5. **P4-02 实现 Agent**：基于已合并 P4-01 和冻结的 P2/P3 依赖提交 durable jobs/worker/API/integration/真实 Gate；另一个实现 Agent 可写互不重叠的 test/runner，但不得共改共享模块和 migration。
6. **P4-02 Review 与验证 Agent**：审/测崩溃窗口、幂等、lease、权限、remote call 无锁、真实 Recall 帮助与 Truth 降级。最后 PR 候选 commit 做完整 §7 Gate及P4-G07管理Gate，合并后最终 commit 复跑受合并影响用例。

准备时实际 schema 为 6；下一版本由 root/integration 负责人统筹。公共 ID、auth、API、migrations 共享文件变更也由 root/integration 协调。P4 不建立 P3 的 Checkpoint/Context 表。

不预分配 `m0007` 等编号：P2/P3 正并行增加迁移，负责人按最终 rebase 的连续序列分配。只改尚未发布的新迁移编号与 checksum，禁止修改历史迁移。共享 routes/auth/errors/SCHEMA_VERSION 的变更由 designated owner 串行应用，各 PR 提供明确补丁；重排后重新跑迁移与依赖集成检查。

## 7. 必须通过的真实对话 Gate

### 7.1 环境前置

操作员已提供受控真实 Hindsight 与合法凭证、允许的隔离验证 bank/数据域、测试 actor/项目；P2 eligible 来源链、P3 exact Context build/实际 SessionStart 注入已冻结并实装。使用新的 V1 来源与随机 run 标记，禁止 archived V0 数据、旧活动或手工数据库行替代。服务版本、commit、bank namespace、调用 transport、Agent/session ID 记录入 manifest，凭证仅安全引用。

可按§2授权边界准备隔离的真实Hindsight实例补足开发测试环境；禁止以fake引擎替代真实Gate或伪造Context。完成必要隔离环境准备后仍缺服务/凭证、实际hook信任/入口、P2/P3 API/来源则输出BLOCKED与精确缺项，停止受阻依赖路径并继续独立隔离测试。

### 7.2 Gate 场景与 PASS 证据

| Gate | 对应原 VC | 操作与判据 |
| --- | --- | --- |
| P4-G01 历史故障真实收益 | VC-16 | 在真实小任务产生可验证失败/修复教训，commit source→job→真实 retain 完成/readback；新 Session 问类似问题；Core Recall 命中，exact injected pack 含来源；Agent 引用历史教训安排有用的诊断/真实工具动作并改善当前任务，不把历史原因断言为当前原因 |
| P4-G02 当前 Truth 优先 | VC-14/15 | 创建历史候选与后续经合法 Authority 确认的新 Decision；Recall 返回旧记忆亦须过滤/标历史；实际新 Session 回答当前 Decision，未部署候选不能说已部署；Rule/Task revision 未被 Recall 改写 |
| P4-G03 未知诚实 | VC-17 | 提问不存在记录的协议测试；保存真实 query/empty evidence trace；Agent 回答未找到，不能编造 PASS/历史验证 |
| P4-G04 scope 隔离 | VC-18/19 | A/B 各有可区分事实/规则，global 合法偏好；B 新 Session 的 bank 查询及 pack 仅 B+global，遵守 B HARD Authority；单用户mode做跨task/project不一致与bank注入拒绝；启用真实multiuser ACL时另验证无权actor直接访问A后端拒绝，并明确mode；不是只检查前端显示 |
| P4-G05 Truth 降级独立 | VC-22 | 在仅影响已授权测试服务的故障注入下真实连接不可用，Context 标 degraded且精确 pack 无 Memory；仍读取 HARD Rule/State、捕获真实 Tool Evidence、做合法 State/Checkpoint 流程，未回滚/悬挂；服务恢复后同 job 恢复 DONE，无重复 document |
| P4-G06 durable 恢复 | 模块 §12 + 本文建议 | 实际 worker 进程在 source commit/enqueue 缝隙及 retain 成功/DONE 缝隙退出并重启，补扫和幂等产生一条可追溯结果；保存真实远端 document readback与本地 attempt链 |

G01 的“帮助”需预先写具体任务、预期可观察动作及完成 Evidence，不以 Agent 自述“有帮助”判 PASS。建议保留不含 lesson 的基线 Context 与含 lesson 的测试 pack/diff，指出省去的重复调查或正确诊断步骤；不声称一次示范建立统计收益。P4 阶段不冒充 §9 综合长任务或 P7 全量发布通过。

G05 优先对隔离客户端配置注入不可达 endpoint/timeout，真实调用真实服务的恢复前后路径保留；若要求实际停服，仅在操作员明确允许的隔离服务进行。未经授权不能停止现有服务；只能做 fake exception 的故障测试不得满足真实 Gate。

## 8. 验证证据、结论及 merge 门槛

运行数据按仓库 `.gitignore` 保留本机/授权证据存储，不 commit SQLite、Hindsight 数据、原始私密对话、credentials 或完整运行 artifacts。PR 给安全 manifest/摘要/文件 hash/访问说明。沿用最小 Validation Recorder 的 user_raw/agent_response/tools/state_before/state_after/interpretation/context，扩展采用附加证据而非重写 P7 Recorder。

建议每次 run 的 manifest：

```json
{
  "run_id": "p4-<unique>",
  "phase": "P4",
  "candidate_commit": "<full-sha>",
  "final_commit": null,
  "schema_version": "<actual>",
  "dependency_commits": {"P2": "<sha-or-blocked>", "P3": "<sha-or-blocked>"},
  "engine": {"name": "Hindsight", "version": "<verified>", "transport": "<verified>"},
  "cases": [{
    "case_id": "P4-G01",
    "verdict": "PASS|FAIL|BLOCKED",
    "mode": "real|boundary-fake",
    "reason": "<specific>",
    "project_ids": [], "session_ids": [],
    "source_event_ids": [], "evidence_ids": [],
    "job_ids": [], "document_ids": [], "memory_result_ids": [],
    "context_pack_ids": [], "rule_versions": [],
    "state_revision_before": null, "state_revision_after": null,
    "receipts": [{"path": "<absolute-local-path>", "sha256": "<hash>", "kind": "<receipt>"}]
  }]
}
```

每 Gate 保存真实 user raw、Agent response、源 Event/Evidence 读回、candidate 与决议版本、job/attempt 和 retain 回执、engine readback、query params/results/filtered reasons、bank/scope、exact Context pack/render hash、实际 SessionStart 注入回执、tools/evidence、before/after Authority/State/Checkpoint 与 degraded/health、测试 stdout/stderr/exit。禁止把 HTTP 200、enqueue accepted、job RUNNING、old memory、fixture recall 当成功 retained/召回受益的证明。私密内容经已定证据脱敏策略处理，hash/link仍能供受权 reviewer 追溯。

Merge 条件：冻结 ADR 与 API 更新完整；只在允许领域改动；迁移升级及公共契约 regression 通过；PR 对应正负/失败测试通过；独立 Review 阻断项关闭并注明 review SHA；独立验证注明待合并 SHA与证据；P4-02 的 G01～G07 全 PASS；合并后最终 SHA 重跑受影响真实 Gate。任一真实依赖 BLOCKED 或 fake-only，只能报告阶段部分实施/未验收，不标 P4 完成，不通过阶段最后 PR 门槛。P4-01 可按其较小范围单独完成，不能替代 P4-02 Gate。

## 9. 外部 Agent 启动 prompts

### 9.1 P4-01 实现 Agent

> 在 `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1` 执行 P4-01。先读父 AGENTS.md、仓库实际指令、本任务书、ADR/API 和现有代码；核对当前 main 与依赖，不使用 V0 运行状态。读设计输入的 §7、模块 §11及 Context §10。先列权限/transport/provider 未定项供独立 reviewer 冻结，再按授权文件实现 Hindsight adapter、bank mapping、provenance、过滤与预算、真实 API 集成。与 P2/P3/P6 通过冻结契约协作，不改其模块、不抢迁移/shared routes。允许必要的项目/临时目录隔离本地测试实例和官方依赖准备；禁止改用户现有服务、生产自启动、外部付费账户、全局hooks/config/secret/trust。fake tests 单独标识，缺真实环境为 BLOCKED。提交实现、专属测试、ADR/API 补丁、风险/证据及可审 PR；不凭计划宣称真实验收。

### 9.2 P4-02 实现 Agent

> 在同仓库执行 P4-02，依赖已合并 P4-01 与冻结的 P2 eligible-source、P3 Memory provider/Context。交付authenticated/audited obsolete/delete/pin生命周期与G07，明确单用户/ACL mode；实现持久 memory_jobs、短事务 claim、锁外 retain、恢复扫描、稳定 document_id 幂等、backoff、lease与失败/手工 retry审计。显式覆盖 Truth commit/enqueue 前崩溃和远端成功/DONE 前崩溃。任务/Rule/Evidence 真源不受 Memory 失败影响。只写分配的 P4 文件，迁移共享文件由单一 owner 串行合并。运行本文正负/失败测试与实际 G01～G07，依赖缺失输出 BLOCKED，不用 fake-only 替代真实 Gate。提供完整候选 SHA、原始 receipts与最小必要 PR。

### 9.3 独立 Review Agent

> 独立审查指定 P4 PR 的明确 candidate SHA，不承担该 PR主要实现。对照原设计与本任务书区分原要求/候选建议/已冻结决定。逐项审 Memory 非权威、project/global 授权、source/evidence、secret、Reflect explicit、旧记忆过滤、P3预算、异步持久崩溃窗口、远端调用无 SQLite 锁、retry幂等、migration/public contract以及 P2/P3/P6 ownership。核对当前官方 Hindsight 版本与真实 retain/document 语义证据，不凭 fake SDK判断。给 blocking/nonblocking 问题、文件行号及证据；不得替实现者改领域契约后自判通过。

### 9.4 独立验证 Agent

> 从指定候选提交独立验证 P4 对应 PR。记录 SHA/schema/dependency/engine/session，复跑 meaningful 正负失败测试；使用操作员已授权真实服务、新 V1来源与新 Session执行本任务书 Gate，读取 source/job/engine/Recall/exact实际注入 pack/Tool/Evidence全链。验证obsolete/delete/pin真实G07、source保留和项目ACL mode；验证项目隔离及真实故障时 Truth独立，崩溃恢复后无丢 job/重复文档。可准备受委派范围内隔离真实测试实例；不得改/停用户现有服务、生产自启动、创建外部付费账户、改全局hook/config/secret/trust或借V0旧数据补证。缺权限/信任/依赖按具体项 BLOCKED；仅 fake/HTTP200不能 PASS。提交 receipts hashes、逐项 verdict与未验证边界给负责人，合并后按最终 SHA复跑受影响路径。
