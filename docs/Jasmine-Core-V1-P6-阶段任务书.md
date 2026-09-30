# Jasmine Core V1 P6 Dashboard 阶段任务书

状态：实施任务书，未实施、未验收。编制基线：2026-09-29，`main@9059625`（P1 已完成；公开仓库）。本书允许 P6-01 与主线 P2、后续 P3 以及独立 P4 工作并行；不表示 P6 或整个 V1 已完成。执行前重新核对 HEAD、工作树、父级与仓库内 AGENTS；本书的 API 盘点是该提交的静态实现核对，不是服务当前在线或真实浏览器测试的证明。

## 1. 依据、目标与边界

原件 SHA-256、完整目录与 ZIP 交接方式见[设计输入索引](Jasmine-Core-V1-阶段任务书-设计输入索引.md)。

完整设计输入的原始文件位于本机 `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jsamine core/Jasmine-Core-V1-设计实施验证文档包/`：`Jasmine-Core-V1-详细设计书.docx`、`Jasmine-Core-V1-模块设计书.docx`、`Jasmine-Core-V1-实施与验证任务书.docx`。仓库 ignored 目录 `artifacts/phase-planning-20260929/` 下对应三份 `.txt` 仅为可选本地完整抽取，不是 clone 必有的文件。异机 Agent 必须收到这三份原 DOCX 的交接设计包或负责人提供的便携 ZIP，并按其中 SHA-256 manifest 核对文件后完整抽取阅读；缺失或 hash 不符时报告 source 依赖，不能凭任务书摘要宣称读过原设计。原始附件不发布到 public Git。本书按以下章节落实范围：

| 来源 | 本阶段使用的要求 |
| --- | --- |
| 详细设计书 §3、§5–9、§12、§14、§16 | 权威分层、来源链、人工维护、精确 Context、身份和故障降级 |
| 模块设计书 §2–10、§11–13、§17–19 | Event/Rule/State/Evidence/Checkpoint/Context/Memory/Registry 的展示与接口职责；人工修正审计；Validation 下钻 |
| 实施与验证任务书 §1–4、§7–10 | 四线协作、P6/WS-H、分批 Dashboard、证据规范、真实对话与 G4/G8 |
| `docs/api/core-api-v1.md`，ADR 0001–0007 | 已实现 ID、鉴权、错误、revision、Evidence 与 fingerprint 契约；设计示例与实际实现冲突时以已冻结契约为准 |
| `src/jasmine_core/api/handlers.py`、`dispatch.py`、`server.py`，`authority.py`、`state.py`、`evidence.py` | 本书现存端点与权限边界的实现依据 |

目标是让人通过真实 UI 检查并维护 Core，点击一条原话能追溯它的 Interpretation/Resolver、Rule/Task、实际注入 Context、Tool/Evidence 与 State。P6-01 先交付现有 P1 能支撑的只读链；完整链等待 P2/P3/P4 与 P7-01 最小 Validation 读取切片的冻结接口后逐项验收；不等待 P7 整阶段或最终发布 Gate。

V1 实现在独立 Git 仓库 `jasmine-core-v1/`；父目录不是 Git 仓库。`jsamine core/` 的文档是设计输入，保留原设计和验证包。`_archive/` 的 V0 已退役，不连接旧 hooks、服务、数据库、绑定或记忆工作流。已委派的 P6 实现与验证可以准备必要的隔离本地 UI/Core 测试实例、临时测试数据库与局部测试凭据；执行此书不授权付费部署、外部 Site、修改全局 hooks、全局凭据、现有账户、登录、信任设置或启动/重启现有服务。真实验收若缺用户拥有的服务/信任条件，记录具体阻塞，先完成不依赖该条件的交付。

## 2. 当前实现盘点与接口缺口

当前基线 Schema 为 6，API 默认 loopback `http://127.0.0.1:8787`；原设计里的 8765/8766 是规划值。未找到前端 `package.json` 或现成 Web 应用；P6 应新增隔离的前端目录。HTTP adapter 处理 OPTIONS 方法，但未实现 CORS 授权或前端静态资源服务；不能因有 `do_OPTIONS` 就假定浏览器跨源 Bearer 请求能成功。

| 页面/能力 | 基线确实存在的接口 | 基线限制及处理 |
| --- | --- | --- |
| Core 健康与 Schema | `GET /v1/health`、`/v1/meta/schema`（公开） | 只证明 Core liveness/schema；不推导 Hindsight、Backup、Adapter 健康 |
| Project/Task/Session | `GET /v1/projects`、`/tasks`、`/sessions` 及 `/{id}`；`objects:read` | 列表实际支持 `limit/offset`，并有 project/task 过滤；`count` 是本页长度，不是总数 |
| Task/Step 与历史 | `GET /v1/tasks/{id}/steps`、`/history`；`GET /v1/steps/{id}`、`/history`；`state:read` | Task 状态是 ACTIVE/BLOCKED/ACCEPTED/CANCELLED；看板列是 Step 状态。不得把 Task 误分成 PLANNED/VERIFIED |
| Conversation/Event | `GET /v1/events`、`/{id}`；`events:read` | 有 session/task/project/event_type/source_system 过滤与 `after_seq/limit`；没有专用 task timeline 端点。组合读取必须标明范围和读取时间，不能宣称事务一致快照或完整聊天采集 |
| Rule | `GET /v1/rules/active`、`/{id}`、`/{id}/history`；`authority:read` | active 只列适用 ACTIVE；没有全状态 Rules 列表。已知 Rule 的 history 可以展示旧版本，不能凭历史虚构全局 PROPOSED 列表 |
| Evidence | `GET /v1/evidence/{id}`、`GET /v1/tasks/{id}/evidence`；`evidence:read` | 有 reference_status 与当前引用检查。没有任意 artifact 下载/预览端点；不能把 `workspace:` 当浏览器文件 URL |
| Registry | `GET /v1/hosts`、`/actors`；`objects:read` | 静态身份不是 heartbeat/online/capabilities；在线状态显示“未实现”，而不是绿色在线 |
| 审计 | `GET /v1/audit`；`admin` | 元数据，不含原文/token。实际只有 actor/limit 查询，不能假定有 request_id filter 或完整历史分页；只读 UI 不默认索取 admin |
| Rule 人工写入 | proposals (`authority:propose`)；approve/supersede/retire (`authority:manage` 且 human/system) | 当前已存在，P6-02 才启用受控表单；approve 等带 expected_revision，proposal 是 create |
| Task/Step 人工写入 | Task create；Step create/transition；Task transition/criteria/accept；Step criteria/transition | scopes 分别 `objects:write`、`state:write`、`state:accept`；角色与 Evidence 门槛由服务端检查，UI 不另造状态机 |
| 人工 Evidence confirmation | `POST /v1/evidence/confirm`；human + `evidence:confirm` | 需要真实同任务 human user.prompt、正确来源时序、精确 revision；普通网页“确认”点击不能自行冒充真实来源 |
| Workspace | fingerprint POST（system + `fingerprint:scan`）；compare POST（`fingerprint:read`） | 扫描会使 VERIFIED Step 变 STALE，不是只读按钮；P6-01 不调用扫描 |
| Interpretation/Review | **缺失** | 模块设计 `/interpret`、interpretations correct/reject/rerun、reviews/pending/approve 均未实现；由 P2 冻结读取与人工修改契约 |
| Checkpoint/Resume/Context | **缺失** | checkpoints/latest/resume/context/build/context/{id} 均未实现；P3 提供真实快照、actual injection 标识与存储读取 API |
| Memory/Jobs/Recall 管理 | **缺失** | Hindsight/retain/recall/jobs/retry/obsolete/pin/delete 都不是基线能力；由 P4 冻结，只通过 Core 使用 |
| Validation Inspector | **缺失 Web API** | 有 `validation/recorder.py` 与本机证据记录基础；没有 `/v1/validation/runs`、run 详情/verdict API。P7 owner 负责 Recorder/读取服务与分类，P6 所需 P7-01 最小 run 读取与安全 artifact 契约可提前并行交付；不能由 UI 读服务器任意路径 |
| Backup、Devices、Storage | **缺失 Dashboard 读取契约** | 对应 P5/P7 切片交付前保持明确 unavailable，不造统计；不将 P7 最终发布/备份恢复 Gate 作为 P6 Inspector 读取 Gate 的前置条件 |

规则使用真实 `rul_`、对象使用 `prj_/tsk_/stp_/ses_`、Event 使用 `evt_`、Evidence 使用 `evd_`，不能照抄设计里的 `task-184/step-7` 建新公共 ID。实际 revision 冲突是 `409 revision_conflict`，不是设计示例的 STATE_STALE。当前 Evidence 写入为 `/tool-results`、`/codex-exec-observations`、`/evidence/confirm`，不存在泛用 `POST /v1/evidence`。

## 3. 协作、目录与接口冻结

每个 PR 使用自己的分支和 worktree；不要多人在同一 checkout 切分支。开始前列 HEAD/dirty/status 与 file ownership，保留用户已有改动。提交和 PR 绑定独立 commit，禁止顺手合并其他阶段代码。每 PR 至少实现 Agent、独立 Reviewer、独立验证 Agent；实现者不能替代独立审查/验证。总负责人处理跨阶段契约和合并冲突。这里的角色可由外部 Agents 分别承担，不要求本任务即时创建聊天或启动进程。

| 所有者 | 默认独占文件/职责 | 不得越界 |
| --- | --- | --- |
| P6 实现 | 新 `web/`（包含 API client、types、views、UI tests）、`tests/p6/`、`scripts/p6/`、本阶段说明 | 不改 Core Schema/Authority/State/Evidence/Resolver、capture/hooks、P3 Context Builder、P4 worker |
| P2 主线 | Interpreter/Resolver、Extraction/Review 领域 API/ADR/migrations | P6 不为 UI 方便重写 auto-apply/approval policy |
| P3 主线 | Checkpoint、Resume、Context、实际注入生命周期 | P6 只读 exact persisted pack，不自行 build 一份当注入结果 |
| P4 独立线 | Memory Adapter/Jobs/Recall/管理 API 与数据映射 | UI 不直连 Hindsight，不自选 bank、不把 Memory 写入 Truth |
| Core 接口负责人 | `src/jasmine_core/api/{handlers,dispatch,server}.py`、共享 API 文档与新增 ADR | 同源服务、浏览器鉴权、列表分页及缺失读取 API 须单独 API PR；P6 不与 P2/P4 同时抢改 handlers |
| P5/P7 | Registry 在线能力、Validation/Backup/Storage；P7 owner 提前交付 P7-01 最小 Validation 读取切片 | P6 不代写 heartbeat、fabricated run 或 PASS verdict |

若现有结构随后变化，可由负责人更新 ownership，但先记录路径与 PR，不能默默越界。`ids.py`、`auth.py`、共享 API、`SCHEMA_VERSION` 与 migrations 必须由集成负责人协调；前端缺少列表/分页/query API 可以提出单独 Core 只读 PR，不能自行抢改。新增 ADR 和迁移编号由集成负责人按当前仓库分配，不能复用已有 0007 或按设计预定。UI client/types 中仅保留冻结接口；未冻结接口放 draft contract 文档/fixtures，禁止当作真实 API 默认调用。

依赖交接表必须包含：提供方、冻结 commit/ADR、路径与 scope、字段/枚举、稳定错误码、列表排序/分页/空集、actor 权限、command event/provenance、expected_revision/幂等语义、secret/redaction、真实 readback 样本、版本与变更策略。通过提供方评审后才连真实视图。每次 API 升级由领域 owner 更新后端契约，P6 更新 client；不得靠 `as any` 或猜字段绕过。

前端建议 React + TypeScript + Vite，工具型布局，shadcn/ui 可选。这是设计书建议与新建项目的合理默认，不是必须引入的新产品/外部账户依赖；若仓库届时已有维护良好的前端，优先复用。先冻结 package manager/lockfile、组件风格、测试与启动方式，避免无关框架迁移。Web 不可用必须不影响 Core/Agent。

## 4. P6-01：现有 P1 只读 Dashboard（可立即实施）

完成定义：本地浏览器通过真实 Core API 查看现有 Project/Task/Step/Conversation/Rule/Event/Evidence，能够从当前状态及历史跳回来源；鉴权/错误/分页/脱敏与可访问性可用。这个 PR 只验收 P1 范围，不验收 Interpretation、实际 Context 或 Memory。

| 工作项 | 实施要求 | 验证结果 |
| --- | --- | --- |
| P6-A01 应用骨架与认证 | loopback API 配置有明确来源；页面输入已发放只读 token，内存中保存，刷新/退出清空；不自动 mint/admin bootstrap | 无 token 401 可见；退出后不再发送 Authorization；token 不出现截图、日志、URL、错误或 bundle |
| P6-A02 浏览器接入 | 同源静态路由或开发同源 proxy 由 Core 接口负责人冻结。proxy 只转发 operator 配置 loopback Core，路径 allowlist，禁止任意 URL/SSRF、注入共享 admin token；不开放通配 CORS | 真实浏览器 GET 与预期认证成功；跨源/未知目标拒绝可测；Core 仍 loopback |
| P6-A03 Overview/Project | 项目/任务数仅声明已载入范围；可过滤 ACTIVE/BLOCKED；Last activity 只根据真实已读取 Event；Hindsight/Backup/online 等 unavailable 分别展示 | 空库显示空，失败显示错误，不用 0 冒充未知；Project→Task→source 可点击 |
| P6-A04 Task Board/Detail | Task 状态与 Step 列分开；revision、criteria、history、Evidence、来源 ID 同屏；历史 State event 反查 Evidence IDs | Task/Step 当前值与 GET readback 一致；没有“全部 done”掩盖 EXECUTED/STALE |
| P6-A05 Conversation/Event | 用 session/task 过滤 Event、显示原文与 actor/source/seq/时间；异构 Tool 事件不能装作用户发言；Interpretation 区提示 P2 未接入 | source Event 深链接可刷新定位；没有 Event 缺失时凭本地对话填充 |
| P6-A06 Rule/Evidence | 当前适用 Rule、scope/status/version/enforcement、origin/change Event 与 history；Evidence kind/result/fingerprint/reference、raw source；未知 Rule 按 ID 读取，不造全状态列表 | source/provenance 跳转；BROKEN_REFERENCE/INFO/FAIL 清楚；不以测试文字把 INFO 显示成 PASS |
| P6-A07 列表与一致性 | 对象 limit/offset，Event after_seq；稳定排序按真实契约；按钮继续加载、取消旧请求、防跨项目请求回流；不可分页列表声明有限/完整范围由接口确认 | 多页无重复/漏 seq；切换 Project 不短暂显示上一项目数据；上限数据不宣称总量 |
| P6-A08 人工可用性 | 键盘导航、焦点、表单 label、可读对比、非颜色唯一状态、长 JSON/文本折叠与复制脱敏摘要、加载/空/失败/未实现明确区别 | 小窗口和长文本不遮挡来源按钮；键盘能完成 source trace；自动扫描加人工检查 |

Raw Event 在 Core 中逐字保留；浏览器不改写真源。展示层默认掩码凭据，权限允许的原文查看按冻结策略显式展开，展开不意味着允许导出/private transcript。使用 text renderer 禁止把 Event/Memory 内容作为 HTML；DOM/Markdown 链接只允许冻结安全协议，拒绝 script/危险 URL。禁用浏览器 localStorage/sessionStorage/IndexedDB/service worker/cache 持久保存 token；静态 bundle、环境 `VITE_*`、源码、HAR、测试快照均不得嵌入 token。使用带掩码的网络记录证明状态与 request_id，不能提交完整 Authorization。

P6-01 不需要自动扫描 fingerprint、不调用 Guard 授权执行、不写确认 Evidence、不创建 Rule/Task、不签发/吊销 key。当前只读 token 权限集按页面需要发放 `objects:read/events:read/state:read/authority:read/evidence:read`；没有对应 scope 则对应视图显示 forbidden，不能转用更高权限凭据补洞。

## 5. P6-02：Extraction Review 与 audited 人工操作

可先做：表单布局、差异查看、字段校验、error/revision conflict 状态、模拟交互。必须等：P2 读取/Review/correct/reject/rerun 契约及真实 Resolver policy Gate。现有 Rule/Task 写 API 的 UI 可单独开发，但合并前须完成独立权限与人工修改审计 Gate。所有模拟页面永久明显标记“模拟数据 / 未接真实后端 / 未验收”，fixture 与生产入口隔离。

1. Conversation 左 Raw Event，右 Interpretation；显示 model/extractor/prompt/version/confidence/status、Resolver reason、派生 Truth IDs 与 source。pending count 来自 P2 API，不通过文案/关键词自行计算。
2. Correct 显示旧/新候选与将影响的 Truth；提交 reason 与冻结 API 所需版本。Reject 保留原解释和来源，不删除 Raw Event。Re-run 创建新的解释记录，默认不自动改 Current Truth；必须显示新旧 extractor 与实际 apply 状态。P2 定义 lineage、幂等、冲突和人工权限，UI 不自定。
3. Rule proposal 与 approve/supersede/retire 分开；显示影响 scope、当前 version/revision、source 和不可改历史。Supersede 不扩大 scope；409 后展示当前值与用户草稿，重新审阅再提交，不自动用新 revision 重试原意。
4. Task/Step create/transition/criteria 按真实状态机提供动作。expected_revision 来源于最近 readback，命令 event_id 幂等重试保留；timeout 状态显示“结果未知”，查询原命令/当前对象后判断，不立刻换 ID 重做。
5. VERIFIED/ACCEPTED 表单展示 criteria→Evidence matrix、规则版本、fingerprint、确认来源和缺项。接受动作只发请求，成功后从 Core 读回；HTTP 200 或前端 optimistic update 不能制造验收。`422 missing_evidence` 保持原状态；Agent credential 即使 UI 显示 human 按钮也应被服务端拒绝。
6. `evidence/confirm` 仅在真正 human 同任务来源与权限链已由后端认可时接入。若只有网页点击、没有满足 ADR 0007 的 human prompt，禁用并写明缺少来源；不得先伪造 `user.prompt` 给自己产生 confirmation。新增 Web 原生 human confirmation 的必要性由 Core owner 单独 ADR/API Gate 决定。
7. 每个人工改动必须有不可变 command Event（及对象 revision/provenance）和审计元数据；验证审计 readback 使用授权的验证者，前端不为展示审计额外获取 admin。按钮确认不是权限提升，后端 token/Registry actor 才是身份来源。

## 6. P6-03：按领域分批接 Inspector

本 PR 可拆 P6-03a（P3）、P6-03b（P4）、P6-03c（P7-01 最小读取切片），保持独立提交和依赖；Evidence 基础已提前归 P6-01，不必等所有依赖再交付。不能将未接入部分写成已通过 P6。

| 视图 | 前置冻结与显示 | 明确失败/非目标 |
| --- | --- | --- |
| Checkpoint Compare | P3 checkpoint read API、task_revision/current/fingerprint、快照来源、freshness/integrity；比较以服务器结果为准 | 旧 cp 只作历史；不提供回滚 Current State；revision 超前/partial fingerprint 不显示 match |
| Context Inspector | P3 persisted pack、exact rendered content、分区/总 token 与计数器版本、rule versions、memory IDs、state revision、注入 attempt/receipt | built/persisted 不等于 injected；未取得真实 Agent 接收凭证显示“注入未验证”；禁止 UI 重新 build 冒充实际文本 |
| Memory Jobs/Recall | P4 jobs/detail/retry、bank mapping、source Event/Evidence、query/result trace/degraded、管理权限 | Memory 是 NON_AUTHORITATIVE；不能改 Rule/State；Reflect 只明确用户请求。retry 不重复 retain；Hindsight 凭据不进浏览器 |
| Memory Inspector 管理 | P4 明确 obsolete/delete/pin 的 Core API、鉴权/审计、软删除/保留 provenance/幂等策略 | 没有接口则只读并禁用操作；不直连引擎/绕过 secret filter；历史 Memory 与当前 Authority 冲突醒目显示 |
| Validation Inspector | P7-01 提前冻结的最小 run list/detail、bundle manifest、安全 artifact read、已记录 FAIL 分类与链路缺项；由 P7 owner 并行交付 | 当前 recorder 不等于完整 API；只展示真实文件/记录，缺 Extraction/Context 说缺失；不生成“占位 PASS”或伪 Agent 响应 |
| online/backup/storage | P5/P7 可读契约与实际健康戳 | 超时/未知区别于离线/健康；不能靠 hosts 列表假装在线 |

P7-01 最小 Validation 读取 API 与安全 artifact 契约由 P7 owner 提前冻结、实现并验证，可与 P6 并行交付；它读取真实记录，支持链路缺项与既有 verdict 展示，不需要先完成 P7 的 30 场景、完整 Scenario Runner 或最终发布 Gate。P6-03c 可先开发明显标识的 fixture，但验收必须使用该切片的真实读取链。完整 P6 trace 与 G4 依赖真实 P2→P3→Tool/Evidence→State 记录、P4 相关记录以及该最小读取切片；不依赖 P7 整阶段完成，避免 P7 发布等待 P6 而 P6 等待 P7 的循环。此时 Inspector 按最小读取范围验收，完整 P7 能力与 30 场景/发布 verdict 另由 P7 验证，不归前端决定。

## 7. 真实点击验收 Gate

验证 Agent 使用明确 commit 和本地安全测试项目，记录浏览器/前端/Core 版本、scope 集合（不记 token）、真实 UI URL、API base、fixture/real mode、时间与环境。通过用户既有服务或授权隔离实例操作；不得连接 V0。使用无敏感真实测试话术；真实消息由实际采集路径产生，不用测试脚本捏造 Raw 后叫“真实对话”。

| Gate | 真实动作与证据链 | PASS 条件 |
| --- | --- | --- |
| P6-T01 P1 source trace | 浏览器 Project→Task→Step history→所用 Evidence→tool source Event；Rule→origin/change Event；切换 session/filter | 截图/交互记录中的 ID、seq、revision、Evidence/fingerprint 与真实 GET 一致；source 深链接刷新可用 |
| P6-T02 权限与秘密 | 无/撤销/缺 scope key 请求，Agent 尝试管理/验收，token 清理，恶意原文与 URL | 401/403 明确；无 Truth 写入；原文不执行脚本；bundle/log/browser storage/static bundle 无秘密 |
| P6-T03 人工维护（P2 后） | 真实消息→Interpretation→Correct/Reject/Re-run；Rule approve/supersede；state 修改；浏览器提交→request_id→command Event→audit→readback | 修改可追溯 authenticated actor/host/source/reason/revision；rerun 不默认改 Truth；拒绝不改变 Truth |
| P6-T04 实际 Context（P3 后） | 真实原话→Interpretation/Resolver→Rule/Task→按实际轮次的 Context Pack→Agent 收到注入→Tool result→Evidence→State | 每一步可真实点击，exact pack 与实际注入记录匹配，rule version 不串轮次；缺 receipt 不能 PASS |
| P6-T05 Memory 降级（P4 后） | Recall result→bank/source/Evidence；failed job→真实 retry→同 document mapping；Hindsight unavailable 时读 Task | 历史不被画成当前事实；重试无重复；Memory 故障不挡 P1 视图，degraded 明确 |
| P6-T06 Validation 下钻（P7-01 最小读取切片后） | 选择真实 FAIL Run→failure class→Raw→Extraction→Resolution→Context→Agent→Tool→Evidence→State | 缺项可定位，原始记录可核对；未验证链不变 PASS；artifact 下载受权与脱敏 |
| P6-T07 可维护性 | 键盘从 task 到 source；长列表多页、快速切 project、长 Event、断 API 后恢复 | 无项目污染、重复/漏页或敏感缓存；失败/unknown 不变空集；恢复仍读服务器 Truth |

P6-T01/T02/T07 是 P6-01 Gate；T03 是 P6-02 Gate；T04/T05/T06 按依赖是 P6-03 Gate。真实 UI 点击测试、截图和网络/readback 证据缺一不得仅凭 build/unit PASS 宣称可用。完整 P6 通过要求三批真实 Gate 都完成；阶段缺依赖可交付 PR 并标 PARTIAL/DEPENDENCY_PENDING，不能写 P6 DONE。

必须覆盖的可测负例：

| 负例 | 期望 |
| --- | --- |
| stale expected_revision，重复点击/timeout 后重试、事件 ID 内容变更 | 409/readback/原 ID 幂等，禁止覆盖与双写；保存草稿供人重新审阅 |
| criteria/Evidence 缺项，旧 fingerprint、BROKEN_REFERENCE、INFO tool result、无真实 human confirmation | State 请求拒绝或 stale 明示；UI 不提升 PASS/VERIFIED/ACCEPTED |
| Agent proposed Rule、tentative“先研究”、跨 scope supersede | 保留 PROPOSED/非 confirmed，服务端拒越界；没有“自动批准”按钮绕 policy |
| project A 的慢请求在切 B 后返回；同 ID 不同 revision | 丢弃旧页面响应，显示当前 scope/revision；来源关系不能凭标题拼接 |
| API 404/401/403/422/503、失联、未知 enum、分页截断 | 区分 missing/forbidden/invalid/degraded；未知 status 原样醒目展示；不转换成健康或零数量 |
| secret-looking 原文、HTML/script、artifact path traversal、外部危险链接 | UI 转义/脱敏、安全协议与受控 artifact API；不能从浏览器打开服务器文件路径 |
| Memory unavailable/旧 Memory 冲突/旧 Checkpoint，Context 超预算 | 非权威/旧快照标识，HARD Rule 不静默隐藏；AUTHORITY_TOO_LARGE 按 P3真实错误显示 |
| Validation 缺 Context 或 Agent transcript、仅有 mocked response | 链缺项与证据等级明确，判 PARTIAL/FAIL；不能补造 run 数据 |

## 8. Evidence 包与合并门禁

运行证据保存在 gitignored `validation-runs/p6/<run-id>/`，不得提交数据库、凭据、私人完整对话、auth.json 或 V0 runtime。必要保留真实短测试原文，其余脱敏遵循 ADR 0004；脱敏记位置/原因，若产品泄露秘密则记录 FAIL，不删除唯一失败证据。

每包至少含：`manifest.json`（commit/版本/时间/依赖 commits/模式），`scenario.md`，`click-trace.jsonl`（视图动作、object/source IDs 与 scope），脱敏 `api-requests.jsonl`/`api-responses.jsonl`（request_id/status/error），`truth-before-after.json`，必要源 Event/Rule/State/Evidence/Interpretation/Context/Memory/Validation 映射，`screenshots/`，自动测试原始输出，`result.md`（每 Gate PASS/FAIL/BLOCKED/PARTIAL、未验收范围、错误分类、Reviewer/Validator）。截图和 HAR 脱敏；绝不录明文 token 或私人全聊天。没有可用专门 failure enum 的 UI 故障注明 `UI_FAILURE` 是本地诊断标签，不能冒充 P7 冻结枚举。

PR 必须说明范围、file ownership、接口冻结版本、实际 API vs draft、运行方式、依赖/未接入视图、真实 Gate receipts 与包的本机路径、审查阻断项。自动测试覆盖 API client 错误/项目隔离、revision conflict/幂等交互、安全 render、列表和关键浏览器交互；mock 是开发辅助，结论注明 synthetic。构建通过与可访问性扫描是基础，不替代真实浏览器链。

合并顺序：P6-01 可先合；P6-02 依赖 P2 API 合并及其 Gate；P6-03a 等 P3，03b 等 P4，03c 等 P7 owner 提前并行交付的 P7-01 最小 Validation 读取 API/安全 artifact 契约及其真实测试，不等 P7 全阶段。共享 Core API PR 由领域 owner 先合并，随后 rebase client。独立 Review 重点是权限/来源/secret、Truth 边界、无伪健康或伪数据；独立 Validator 在候选 commit 跑适用真实 Gate。阻断项关闭后合并，最终 commit 对受影响路径做 readback 与真实 click smoke；若 merge 改动身份/provenance/revision/trace，再跑对应完整 Gate。完整 P6 verdict 绑定最终 commit 与依赖，不能从前一分支继承 PASS。

## 9. 可交给外部 Agent 的启动 prompt

### P6 实现 Agent（第一批）

> 在 `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1` 执行 P6-01。先读父/仓库 AGENTS、本任务书、Core API 与 ADR 0001–0007，核对 HEAD/worktree，并建立独立分支/worktree。完整设计输入按本书 §1 从原 DOCX 或交接 ZIP 取得并核 SHA-256；ignored TXT 仅为可选抽取，clone 不保证含有它。设计不是现存端点证明。只实现 P1 只读 Overview/Project/Task/Conversation/Rule/Event/Evidence 及 UI/auth，默认 ownership 为 web/、tests/p6/、scripts/p6/。不得修改 P2/P3/P4 领域代码、共享 handlers、Schema 或 hooks；浏览器同源/API 缺口提交契约请求给 Core owner。可用框架默认 React+TS+Vite，shadcn 可选。token 只在内存、不进 storage/bundle/log，UI 只走 Core API，不提升权限、Evidence 或验收。模拟数据显著标模拟/未验收。完成独立 PR、真实 P6-T01/T02/T07 证据包、明确缺失依赖；可准备必要隔离本地 UI/Core 测试实例，不部署外部 Site/付费环境、不修改全局凭据或账户/启停现有服务，不宣称 P6 完成。

### P6 增量实现 Agent（第二、三批）

> 从已合并 P6-01 与冻结依赖开始，按本书 P6-02/P6-03a/b/c 的一个范围工作，先写所选 PR、依赖 commit/API 与 file ownership。P2 主线拥有 Interpretation/Resolver，P3 主线拥有 Context/Checkpoint，P4 拥有 Memory，P7 拥有 Validation，P6-03c 只依赖其可提前并行交付的 P7-01 最小读取 API/安全 artifact 契约，不等 30 场景/最终发布；不得代替其领域规则。人工写入必须 authenticated/audited、带真实 revision 与来源；rerun 默认不修改 Truth。Context 显示实际注入记录，Memory 非权威，Validation 缺项不可伪造。依赖缺失先做显著模拟 fixture/契约请求，合并和验收结论准确标范围。提交相应真实点击 Gate、错误负例、PR 和原始脱敏 receipts。

### 独立 Review Agent

> 审查指定 P6 PR/commit；按本书逐项检查实际 API、ownership、角色/scope、token/storage、XSS/链接、revision 幂等、Event provenance、human confirmation、模拟标识、分页/一致性、Context 实际注入和 Memory 非权威。验证后端仍是唯一 Truth 与权限来源，所有人工操作有 command Event/audit/readback。指出可执行的阻断项与代码位置，检查未验收范围。不要代写实现、运行 V0 或用 mock 的 PASS 替代真实 Gate。

### 独立验证 Agent

> 在指定候选 commit 上核对依赖与环境，运行本 PR 对应 P6-T Gate 和负例，使用真实浏览器点击和真实 Core readback，保存本书 §8 脱敏证据包；有真实采集/Context 注入/Memory/P7-01 最小读取切片依赖缺失时准确标 BLOCKED/PARTIAL，说明缺哪一步及 source ID。记录构建/自动化结果与真实 trace 的区别。不能创建伪 human prompt、伪 Context receipt、伪 Validation verdict，不能部署/修改 hooks/账户/启动退役服务。报告每 Gate 和最终 commit 是否可合并，不替主负责人宣布 V1 发布。

## 10. 风险与阶段结论模板

主要风险是现有只读接口覆盖不完整、无浏览器同源通道、缺完整 Rule 列表与审计分页、P2/P3/P4/P7-01 最小读取契约变化、UI 无意把 source/role/status 推断成真源、Context 构建被误当实际注入、网页确认被误当真实 human prompt，以及秘密进入浏览器/证据包。通过领域 owner 独占共享接口、冻结交接、有限范围标识、安全凭据处理和真实点击 readback Gate 控制。

结束报告写：`P6-01/02/03x: PASS|FAIL|BLOCKED|PARTIAL`，候选/最终 commit、已执行 Gate、证据包绝对路径、独立 Review/验证结论、接口冻结 commits、未实现/未验证依赖与下一 owner。只有真实端到端链及各批 Gate 完成才写“P6 已完成”；P7 整阶段未完成不阻挡 P6 的既定 Gate；Validation Inspector 明确已验收的最小读取范围与未实现的完整 P7 能力。若 P7-01 最小读取切片缺失，则相应 Gate 标依赖待交付。本书自身交付只是可执行计划。
