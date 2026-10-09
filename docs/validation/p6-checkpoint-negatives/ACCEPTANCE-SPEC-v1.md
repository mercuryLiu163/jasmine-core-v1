# P6 旧／异常 Checkpoint 显示与比较：冻结验收规格 v1

状态：**FROZEN_SPEC_ONLY / PENDING_REVIEW**。本文件冻结下一切片的目标、样本与判据；不是实现、执行或验收通过证明。冻结时间及内容摘要见 `SPEC-FROZEN.json`。规格复核、用户确认及后续实现均待进行。

## 1. 绑定与范围

规格基线为 `f8de226ad51e9b7608b4b87dc9bcd60a86db407f`，tree `7897d56992d443c5172d44984350d079aa430a28`，schema 12。代码、合同与历史证据的实读摘要见 `source-inventory.json`。本规格保留 ADR 0011 的 Core API，不增加浏览器写路由、恢复按钮、数据库迁移或新的 Authority 能力。

范围是：旧 Checkpoint 的历史显示；新建 Resume 对当前 Truth 的比较；部分／不一致工作区指纹；Checkpoint 完整性拒绝；历史 Resume 的精确读回；对象切换与相关读入口负例。Memory 与 Authority 冲突、Context 超预算、P3 原生 hook、P7、其他 T05 分支不属于本切片。

旧实跑 `20261008T075256Z-p6-03a-realcore` 的 26/26 和模拟回归 `20261008T075317Z-p6-03a-gate-regression` 的 15/15 保留原提交与原边界。实跑的两个 Checkpoint 均为 task revision 1，第二个用于切换；它们没有证明 STALE、ahead 或 partial 的本次负例。历史文件不修改、不重新冻结为新样本。

## 2. 语义合同

1. Checkpoint 是创建时不可变记录，不是当前 Truth。页面必须显示本条记录的 ID、revision、project/task/session、创建时间、来源／命令 Event、三个 digest、原 projection/workspace 和非权威历史 note。刷新只重新读取该 ID；不得把当前对象值填入旧快照。
2. `POST /v1/tasks/{tsk}/resume` 由隔离验收操作者直接调用 Core，创建新的比较收据。Dashboard 只通过既有 GET 入口读 Checkpoint/Resume，不执行这个 POST，也不执行建议动作。
3. `GET /v1/resumes/{rms}` 及同请求幂等重放返回原收据。创建时间、sample_window、projection、comparison、next_actions 和 note 都保留。只有新幂等键才能再次比较；页面的“重新读取”不是“重新比较”。新收据必须有独立 ID、command Event 和时间窗口。
4. 状态与工作区两轴分开：Task revision 较低 → `STALE`；Task revision 相同且其他完整 Truth 改变 → `CONTEXT_CHANGED`；完整 Truth 相同 → `STATE_EQUAL`。没有 Checkpoint → `ABSENT`。存在且共有的对象 revision 或 Rule version 超前 → HTTP 409 `checkpoint_integrity_error`。
5. 工作区任一端 `complete=false` → `UNKNOWN`，即使 manifest/selected hashes 相同也不是 SAME。算法／coverage／extra_paths 不兼容也不能 SAME。完整且可比但文件发生变化 → `MISMATCH`。`STATE_EQUAL + UNKNOWN/MISMATCH` 合法且必须 `reconciliation_required=true`；不得把两轴合并成无条件的“匹配／可继续”。
6. 存在的比较记录中，工作区非 SAME 必须带 `CHECKPOINT_WORKSPACE_UNKNOWN` 或 `CHECKPOINT_WORKSPACE_MISMATCH`。需对账时 next_actions 为 `RECONCILE_CURRENT_TRUTH`，显示为建议，不能显示为执行成功。ABSENT 返回独立的既有空 diff 合同，不要求凭空生成 warnings。
7. Checkpoint GET 不运行完整性比较。损坏快照 GET 可能为 200 历史原文，不能据此宣布有效／匹配。完整性拒绝由新 Resume POST 证明；拒绝时不得捏造 Resume ID、比较结果或恢复成功。结构不正常的页面也不得崩溃或混入前一对象内容。
8. `latest` 按 Checkpoint **command Event 的 seq** 选取，不按 source Event 的时间、客户端时钟或 note。显式 checkpoint_id 选择 by-ID，不用无关 task/session 草稿更换目标。合法 session 过滤保持原 Task/Session 约束。

## 3. 固定矩阵和阈值

`acceptance-matrix.v1.json` 是机器可读的必需场景、变体、判据与证据清单，表格是其索引。固定 **26 个场景、43 个必需执行变体**：12 个正常对象场景（12 变体）、10 个标记损坏库场景（11 变体）、4 个入口回归场景（20 变体）。一个变体可以有多个请求／点击；不能用断言数冒充样本数。

| ID | 固定场景 | 必须观察到的结果 |
|---|---|---|
| CP01 | 完整相同控制 | STATE_EQUAL / SAME / false |
| CP02 | 旧 Task 快照 | STALE / SAME / true，原旧 revision/note 与当前状态分别读回 |
| CP03 | Task revision 未变、适用 Rule 状态改变 | CONTEXT_CHANGED / SAME / true，Rule changed diff |
| CP04 | Task revision 未变、Evidence 增加 | CONTEXT_CHANGED / SAME / true，evidence_changed=true |
| CP05 | 完整工作区文件改变 | STATE_EQUAL / MISMATCH / true，准确文件 changed diff |
| CP06 | 创建 Checkpoint 时 partial，Resume 时 complete | STATE_EQUAL / UNKNOWN / true |
| CP07 | Checkpoint complete，当前工作区 partial | STATE_EQUAL / UNKNOWN / true |
| CP08 | 两端相同 partial | STATE_EQUAL / UNKNOWN / true，不能靠相同 hash 判 SAME |
| CP09 | 没有 Checkpoint | ABSENT / UNKNOWN / true、checkpoint_id=null、diff={} |
| CP10 | Truth 改变后读回、重放、重新比较 | 原 Resume 不变；重放不扫描、不追加；新键返回 STALE |
| CP11 | 最新选择与历史 by-ID | command seq 最大；session 过滤；旧 ID 保持历史值 |
| CP12 | 浏览器对象切换、迟到响应 | Checkpoint/Resume 两种切换都显示目标记录，不混入旧值 |
| CP13 | Task revision 超前 | 409 checkpoint_integrity_error，无 Resume 写入 |
| CP14 | 共有 Step revision 超前 | 同上，确实命中 object-ahead 分支 |
| CP15 | 共有 Rule version 超前 | 同上，确实命中 Rule-ahead 分支 |
| CP16 | projection_digest 损坏 | 完整性拒绝；历史 GET 不代表有效 |
| CP17 | full_context_digest 损坏 | 同上 |
| CP18 | workspace_digest 损坏 | 同上 |
| CP19 | JSON 合法但 projection 结构异常 | 同上；历史显示不崩溃、不宣称匹配 |
| CP20 | 不支持的 projection 版本 | 两变体：列版本、内层 snapshot_schema；完整性拒绝 |
| CP21 | projection 内 Task 身份与 Checkpoint 不一致 | 完整性拒绝；不得跨对象显示当前状态 |
| CP22 | 旧投影独有成员且其 revision 高 | CONTEXT_CHANGED；removed diff；不能误判共有对象 ahead |
| CP23 | 合法但不存在的 Checkpoint/Resume ID | 各 404 checkpoint_not_found / resume_not_found；不是 dependency pending |
| CP24 | 非法 Checkpoint/Resume ID | 两变体本地拒绝，零对应 Core 请求；没有上一条内容 |
| CP25 | 缺读 scope | Checkpoint 六个 scope、Resume 七个 scope逐个缺失，共13变体；403 forbidden_scope |
| CP26 | P3 读开关关闭及禁止浏览器 build | 三变体：GET gate、POST checkpoints、POST resume；代理拒绝，Core 零对应请求 |

矩阵另固定101个场景级判据定义，对43变体共求值155次，再逐变体核对公共 G1–G7；这些数不能互相替代。所有变体及其列出的判据均必需成功，允许失败数 **0**、缺失数 **0**、BLOCKED 数 **0** 才能提出本切片限定验收。没有真实浏览器、实际扫描、有效前置对象、审计权限或完整收据则标记 BLOCKED，不能降级为模拟 PASS。没有执行记为 NOT_RUN，本规格的结果不是 43/43。

一次正式运行包含完整矩阵；不按成功样本挑选汇总，不删除失败，不在失败后降低断言或更换标签。修复后以新候选、新目录完整运行，并保留所有先前结果。需变更场景／语义／阈值时另建 v2，注明理由、重新审查确认；v1 不覆盖重写。

## 4. 样本产生和证据类型

### 正常历史与 partial

CP01–CP12 和入口读回使用私有数据库、真实 Core HTTP、真实迁移、真实 auth、合法 source Event 和对象 API 创建。正常路径不得 SQL 改 Task/Step/Rule/Evidence revision，也不得替换 ContinuityStore scanner。CP03 先通过 `/v1/rules/proposals` 创建适用的 PROPOSED Rule，再创建 Checkpoint，最后用合法 human/system 操作者 `/v1/rules/{rul}/approve` 改为 ACTIVE；Task 不含 VERIFIED Step，避免前置命令引起其他 stale 连锁。必须读回 Task revision 未变、Rule revision 与 status 变化。当前 Project 没有更新 API，不以 SQL 更新伪装正常场景。CP04 用 trusted-system `/v1/tool-results` 添加真实私有测试运行的 Evidence 并确认 Task revision 未变；若前置 API 无法满足则 BLOCKED 并报告合同问题。

partial 使用 Core 自带 `scan_workspace` / fingerprint 子进程：私有 Git 根固定 `JASMINE_CORE_FINGERPRINT_EXTRA_PATHS=["declared-input.txt"]`，声明文件缺失产生真实 `unreadable file`。CP06 创建时缺失、比较前创建；CP07 反向；CP08 两次缺失且其他文件不变。coverage、算法、extra_paths、root 均记录，CP08 要求 selected_hashes 与 manifest 相同。不能用固定 partial 回调、伪造返回值、任意调低 scanner 上限或模拟 fixture 顶替。CP05 只改变私有 workspace 的普通被扫描文件。数据、构建输出、日志、DB、浏览器 profile 均在 workspace 外，避免验收工具自身改变指纹。

CP10 用同一个原请求重放（200、replayed=true），再用新键（201、replayed=false）。记录 scanner 调用／子进程证据证明 GET/重放未再次扫描。比较收据 JSON 与数据库 result_json 解析后完整一致，业务字段逐项相等；允许传输 wrapper 的 replay 标记不同。绝不以新时点事实解释旧收据的 STATE_EQUAL。

CP11 至少两 Task、两合法 Session、一无 Checkpoint Task；同 Task 的 Checkpoint source Event occurred_at 故意与创建顺序不同，选择以 command seq 为准。CP12 用浏览器请求排队／延迟释放制造旧 GET 迟到，必须记录干预方式；不改响应体、不换 Core 状态。两类切换都核对 ID、note 标记、来源链接、draft 和最终 DOM。超时、取消与迟到响应均保留。

### 损坏库与不支持版本

CP13–CP22 只能用**明确标记 `REAL_CORE_CORRUPT_STORE` 的私有离线备份分叉**。先通过真实 API 建立带 Step、适用 Rule、来源链的合法 Checkpoint，停掉自有 Core，SQLite backup 到各变体独立 DB；原件不改。每次只注入对应故障，提交离线注入 SQL、对象 JSON diff、原／分叉 SHA、schema/trigger 清单。

不得改 Core 服务、handler、compare 函数或浏览器返回体。仅注入时允许在私有分叉暂移 Checkpoint UPDATE 防护 trigger，结束后恢复同样定义；禁止部署库 SQL、writable_schema、删除迁移或持久放宽 DDL。CP20 列版本需要临时 connection-local `ignore_check_constraints=ON`，注入完恢复 OFF 并关闭连接。正式 Core 启动时外键启用、schema/trigger 与基线一致。该变体的 integrity_check 应明确保留预期 Checkpoint CHECK 失败，其余分叉要求 integrity_check=ok，全部要求 foreign_key_check 无意外失败；不得把 CP20 列版本说成合法旧 schema 数据或兼容迁移证明。

ahead 的 projection/task_revision/objects/rules 必须只改所需 revision/version，并重算 projection_digest/full_context_digest；workspace_digest 不改。CP15 保持 object revision 不超前，仅让共有 Rule version 超前，避免被较早分支遮蔽。CP19 用合法 JSON 数组替代 projection 并重算两 digest。CP20 内层版本、CP21 身份错误也重算 digest，验证各自语义 guard。CP16–CP18 只损坏对应 digest，不重算掩盖故障。

CP22 在分叉的旧 projection.objects 加一个明确 synthetic、当前投影不存在的 Step 成员及高 revision，并重算 digest。必须报告“标记合成成员的比较分支”，不宣称真实 Step 曾被删掉或真实来源支持该虚构历史。这个对照验证 removed 与 common-ahead 的区别。

每个损坏变体仍经过真实 auth/HTTP/事务/比较代码；浏览器读取其历史原文，测试真实显示的稳定性与非有效性表述。409 在操作者 Core POST 出现，浏览器没有这个写入口；只证明拒绝和未误显成功，不能制造可供浏览器读取的失败 Resume。损坏库证据不升级为合法 API 历史、恢复效果或真实项目经历。

## 5. 不变量、来源与审计

矩阵每个变体都绑定执行前／后的读回和数据库交叉核对。Truth 比较覆盖完整 Project/Task/Step/Rule/Evidence/source binding，而非只选 UI 显示字段。

- **G1 业务不变**：比较／GET／重放／拒绝不改变业务状态、revision、Rule version/status、Evidence、bindings 或其业务 history。用于产生前置变化的合法命令单独记录，不能计入比较副作用。
- **G2 正常创建**：新 Resume 恰好一个 `resume.built` command Event、一个 resume_results 和一个 request key；来源、actor、host、project/task/session、Checkpoint 指针及 request hash 精确匹配。Checkpoint 创建同样核对一次命令／记录／key。
- **G3 拒绝／重放**：409/403/404、代理拒绝及本地拒绝没有新 Resume/Checkpoint/幂等键或业务 command Event；重放不追加、不扫描。正常 API 读取允许既有 auth key usage 和 audit 操作记录变化，不得把这些当作业务 Truth 改写。
- **G4 浏览器只读**：UI 点击／刷新／切换只有允许的 GET；不调用恢复、状态修改、fingerprint 刷新、Checkpoint build 或 Resume build。CP26 的主动 POST 拒绝探针单列为测试操作者行为，不能冒充 UI 自发写入。
- **G5 完整来源**：source Event → Checkpoint 创建 command → Checkpoint → Resume 创建 command → Resume 与每次读回同源，核对原对象详情／Event/readback，而非标题或静态字符串。note authority=false/historical=true 从本条收据核实。注入差异明确与原有效链分开。
- **G6 审计与拒绝**：授权验证者直接读取本轮 audit，关联 request_id、method/path、actor/key_id、status/error 与结果；浏览器仍不能访问 /v1/audit 或 /v1/auth/*。代理／本地拒绝没有 Core audit 时，以代理记录与 Core 入站零请求共同证明，不能伪造 Core audit。
- **G7 显示忠实**：保存展开 comparison/projection/workspace 后的 DOM 快照、字段值和截图，核对 response/db/DOM 三方。旧 revision/note、diff、warnings、reconcile、next_actions 及链接目标正确；error/empty/loading 不保留错对象成功内容。不以静态“历史／未恢复”提示替代动态记录证据。

读 scope 为共享五项 `objects:read,state:read,authority:read,evidence:read,events:read`，Checkpoint 加 checkpoint:read，Resume 再加 resume:read。CP25 每变体只移除一个所需 scope；其他前置权限与身份合法，403 details 指向被移除 scope。操作者 build 使用独立最小 key 和 checkpoint:write / resume:build + 所需读取 scopes，admin audit key 与浏览器 key 分离。只记录 key_id、actor_id 和 scopes，不落盘 bearer。

## 6. 未来执行隔离与收据

后续候选必须固定完整 commit/tree 和 clean git archive SHA，在新目录解包，独立安装依赖并构建；不能借用已有 dist/node_modules 作为 clean 构建证明。记录 Python/Node/npm/Chrome 版本、lockfile、迁移与 health schema。实现、非作者代码评审、非作者隔离复验分别绑定同一候选；本轮尚未开展任何独立评审。

新 Core/Dashboard 使用自有 DB、profile、token、端口和启动 PID/PGID。只在自有 Dashboard 启动环境设置 `P6_ENABLE_P3_READS=1`；CP26 的 off gate 在另一个自有实例验证。item 6 保持 0，其余可选 P4/P7 开关保持 0，不触碰现有实例／固定部署／main。结束只清理本轮进程和 profile，保留故障收据；无法证明清理则 BLOCKED。禁止通配 pkill、复用现有浏览器 profile 或默认服务端口。

每次正式运行的新 bundle 至少包含：spec commit/hash 与候选 commit/tree/archive、依赖构建原始输出、environment/owned-process receipts、fixture creation requests、fault injection inventory、每变体 before/after DB 快照与逻辑摘要、脱敏 Core request/response/audit、proxy/browser request trace、实际 click trace、按时间标注的 DOM/screenshot、source mappings、扫描与重放证据、清理收据、逐场景逐变体逐判据 result。所有失败／BLOCKED 也有这些适用项；不可收集项逐项列明原因。最后 manifest 绑定全部文件、bytes 和 SHA-256。

结果生成器必须拒绝漏场景、漏变体、漏字段、重复 ID、模拟值冒充实际值、引用另一候选／另一次运行、缺来源或不变量证据。对结果至少做：删除必需记录、倒置 UNKNOWN→SAME、丢失拒绝后计数、换旧对象 ID、缺 fault 标签、缺 DOM、错候选绑定的 fail-closed 自检；这些是 harness 组件检验，不计入43个正式执行变体。断言总数由冻结矩阵 predicates 决定，公共 G1–G7 不以单个 boolean 替代原始证据。

## 7. 交付与进入下一步

本次仅新增规格／矩阵／来源清单／冻结收据，并在剩余任务清单登记“规格已冻结，未实现／未执行”。完整内容可供审查，不签署限定验收，不把整项 P6/V1 改为完成，原11项跳过继续保留，日常使用不放行。

下一步是审查本规格，尤其正常／损坏库证据界限和43变体；确认后再实现必要的显示修补及隔离验收工具。出现规格歧义先提交带具体 diff 的修订，不在验收运行中自行重新解释。实现与独立结论齐备后，才能对本切片提出 `SIGNED_SCOPED_ACCEPTANCE`；任何部署入口验收仍需另行授权。
