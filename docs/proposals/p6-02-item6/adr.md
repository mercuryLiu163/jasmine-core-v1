# P6-02 item 6 — Core ADR 草案：Web 原生人工确认

状态：PROPOSED / AWAITING_USER_CONFIRMATION；评审结论及文档摘要以 review/ 和交付清单为准。
日期：2026-10-08（America/Los_Angeles）。仅设计，未实现、未迁移、未验收。
正式 ADR 编号、迁移编号由 Core owner 在用户确认后按实际集成 HEAD 分配；本稿不抢占 0014 或任何迁移号。

## 1. 问题、来源与基线

P6 阶段任务书 §5 item 6 要求：只有后端认可的真实 human 同任务来源才能接入
`evidence/confirm`；不能伪造 `user.prompt`。Web 原生确认须由 Core owner 单独 ADR/API Gate 决定。
item 5 已交付浏览器提交/readback，但正例的 fingerprint、Evidence 和确认是在浏览器外准备，
因此它不是完整的浏览器确认→验收闭环。本项解决 CR-6，并仅纳入必要的 CR-7（自己的认证身份）。

| 当前只读核对的基线 | SHA / schema | 用途 |
| --- | --- | --- |
| `jasmine-core-v1` | `7016dabb8926d6bd6599f1f56b8f39cbda253ebf` / 10 | Core 当前主实现 |
| `_worktrees/p6-01` | `41a0dd6ae29dc559c9d3817fb6ea34229c68b66b` / 8 | item 4/5 和 Dashboard 消费端 |
| `_worktrees/p6-03-integration` | `039f5a3e7800704e5f0478d01992f00eeb2ca3f1` / 11 | 集成差异参考；不从其 P4 状态继承验收结论 |

可核对的现状：

* `src/jasmine_core/evidence.py:762-849`：旧确认要求 human、同任务 `user.prompt`、当前 State 之后的来源、
  Step 精确匹配、revision、可适用的 Rule 版本；生成 `USER_CONFIRMATION/INFO`。
* `evidence.py:875-881,948-990`：validator 重验来源、fingerprint、精确确认 revision 与 Rule 版本。
* `src/jasmine_core/state.py:22-32`：Step 只能从 VERIFIED 到 ACCEPTED。
* `src/jasmine_core/auth.py:18-23,63-71`：已有 `evidence:confirm`，仅 human 可获此 scope；admin 不是 scope 通配符。
* `src/jasmine_core/models.py:146-166`：公开 Raw 路径现仅保留 adapter 命名空间，不能直接复用于新来源保护。
* `src/jasmine_core/api/dispatch.py:90-108`、`audit.py:1-12`：请求审计另事务 best effort；不保证每次请求都有持久 audit 行。
* P6 `web/P6-02-ITEM5-VERIFIED-ACCEPTED-COMPLIANCE.md` 与 `web/server/core-paths.json`：
  当前确认控件缺席，旧 confirm、Raw 写入、fingerprint scan、auth 管理路由均未开放给浏览器。

这些是现有行为；下文都是待批准的新契约，不能从设计文档推断已经存在。

## 2. 决策与备选

推荐增加 **prepare → submit** 专用 Web 确认 API。Core 准备不可变审阅材料，人显式确认后，
Core 原子追加专用来源 Event、`evidence.confirmed` command Event 和确认 Evidence。
新来源为 `human.confirmation_submitted`，source_system 固定 `core-web-confirmation-v1`。
准备事件 `human.confirmation_prepared` 不表达确认，不可作为 Evidence 来源。

旧 `POST /v1/evidence/confirm` 保留 ADR 0007 的 human prompt 契约和原返回形状，仍不向浏览器代理开放。
新的 validator 仅增加一个严格校验的来源分支，不放宽旧来源分支。

| 备选 | 判断 |
| --- | --- |
| Web 粘贴已存在的 human prompt Event ID，再调用旧 confirm | 可兼容，但依赖另一个采集入口，未完成浏览器原生闭环 |
| 点击时捏造 `user.prompt` | 拒绝；改变来源含义且可以绕过真实采集边界 |
| 一次点击立即确认，客户端提交自己的 fingerprint/actor | 拒绝；无法绑定人审阅的服务端材料与来源 |
| prepare + 专用结构化提交 | 推荐；增加少量持久准备记录，绑定审阅快照、身份和重试结果 |
| 增加 WebAuthn/独立身份服务/第三方签名 | 当前范围外；若要求密码学证明物理人操作，应另行设计，不能由 bearer key 假装达到 |

## 3. 身份与权限承诺

身份只来自 Core `Auth.authenticate` 的 Principal 与 Registry，忽略任何前端角色标签。
新增 `GET /v1/auth/me` 仅返回当前 key 的元数据、actor.kind/home_host_id、scopes 和 Web 确认契约版本；
没有密钥管理能力。prepare/submit/status 均复查 human 与 `evidence:confirm`，不能只在发 key 时检查。
必要只读 scopes 见 API。agent/system 即使伪造字段、带 admin 或错误赋予 confirm scope也被业务层拒绝。

host_id 由 Registry human.home_host_id 派生，必须非 null 且为已登记 host；请求不得自带 host。
它表示登记的操作来源主机，不证明浏览器真实所在设备。home_host 变化使未消费准备记录失效。
不得自动注册/改绑身份或签发 key；配置缺失由操作者使用已有管理流程处理。

既有本机单用户 key 无 project ACL。本项继承此边界：拥有所需 scopes 的 human 可确认当前 Core
可访问的项目，不声称新建了项目隔离权限。所有 Task/Step/Rule/Project 引用仍须结构一致；
若集成基线实际启用了 ACL，必须在三条专用路由和每次读取/重放重新使用同一 ACL 策略，不能跳过。

认证 human bearer key 是被授权操作者的凭据；持有或控制该凭据的程序也能调用 API。
`affirmed=true`、材料摘要和专用来源保护证明“该认证身份提交了结构化确认”，
不证明物理点击、本人在场、逐字阅读或自然人真实意愿。真实浏览器 Gate 证明浏览器调用链，
不会把自动化点击写成实际人手点击。用户确认此方案意味着接受这个 V1 信任边界。

## 4. 审阅材料和确认含义

每份材料绑定 actor/key/host、project/task、可选 step、Task revision、Step revision、目标 `VERIFIED` 或 `ACCEPTED`，
当前对象 criteria、Task 下所有 Step 的 id/status/revision（Task 确认时）、完整可适用 ACTIVE Rule
集合（global/project/task，稳定按 rule_id 排序，包含 version、kind/severity/enforcement/content/matcher、
结构化 verification_requirements 与 activation Event ID）、可选被确认 Rule pair、
服务器完整 fingerprint SHA/algorithm/coverage/完整性，以及渲染契约版本和固定确认声明。
Task/Step 标题也进入材料供人辨认。任何上述字段改变都需要重新 prepare、重新审阅。

确认声明：`我确认已审阅此 Task/Step 的验收条件和适用规则，并对该材料绑定的当前版本作出人工确认。`
声明是显式结构化意图，不能靠 note 里的关键词提取；提交必须 `affirmed` 为 JSON true。
note 可为空，原样 UTF-8 保留，≤4096 bytes，只是说明，不解释成 Rule/State/工具指令。
Rule pair 可省略表示普通对象确认，或仅选择一个当前可适用 ACTIVE Rule 的精确版本；
一次 submit 只生成一条 Evidence。多个 Rule 要分别 prepare/submit，不可用普通确认暗中覆盖所有 Rule。

只接受 ACTIVE Task；Step 目标 VERIFIED 要求当前 EXECUTED，目标 ACCEPTED 要求当前 VERIFIED。
这样显式 USER_CONFIRMATION/INFO criteria 或 Rule 在 EXECUTED→VERIFIED 时也可通过网页确认，
不依赖先经旧 prompt 路径突破 VERIFIED 门槛。Task 只支持目标 ACCEPTED，须至少一个 ACCEPTED Step，
其余全部 ACCEPTED/SKIPPED。criteria 必须非空；规则映射/其他 Evidence 缺失不阻止“人确认”本身，
但材料和 UI 必须明确“未判定 Evidence 充分性，确认不等于验收”。不能据确认保证 accept 成功。
`USER_CONFIRMATION/PASS` 条件仍不能由 INFO 确认满足；不改写 criteria 或把 INFO 升为 PASS。

生成和提交只记录确认，不改变 Task/Step status/revision、不批准 Rule、不运行工具、不触发 Interpreter、
Context build 或 Memory retain。VERIFIED/ACCEPTED 都是人随后通过现有 State API 单独提交的动作。
VERIFIED 转移会增加 Step/Task revision；此前确认不能复用于新的 ACCEPTED revision，须重新prepare。
接受每个 Step 造成 Task revision 增长，必须等所有 Step 处理完，再 prepare Task 确认。

## 5. 新来源不可伪造与重新验证

公开 `POST /v1/events` 与所有外部采集/内部通用 Raw API 均不得提交两个新 event_type、
`core-web-confirmation-v1` source_system，也不得通过 `evidence.confirmed` 的 payload
冒充 Web source。专用 Core store 使用不可由外部 body 提供的内部构造入口创建保留来源。
拒绝保护是 event_type **或** source_system 任一命中即拒绝，不能靠换 namespace 绕过。
旧 Event 数据不重写；保护此后生效，但任何旧伪记录若缺少新准备/提交投影都不可获认可。

`CurrentEvidenceValidator._source_valid` 对 Web 分支重新验证：

1. source 是唯一已消费 preparation 对应的 `human.confirmation_submitted`；actor/key/host/project/task/step、
   固定 source_system、contract_version、target_status、statement、affirmed 与来源/投影一致；
   Event.schema_version 是创建时实际迁移版本，按已支持契约解析，不要求等于后来的当前schema。
2. 来源携带 preparation_id、review_sha256、Task/Step revisions、fingerprint、Rule pair、note；
   canonical 材料摘要匹配 immutable prepare Event/投影。提交 body.note 实际保存为 source.payload.text，
   不是另一个note字段；必须用这些原始字节重建submit semantic_request_sha256并比较command/submission。
3. source Event seq 在准备 Event 之后且在 `evidence.confirmed` 之前；确认 Event ID、source ID、Evidence ID
   和唯一 submission 投影完全交叉一致，来源也在相关当前 State 事件之后。
4. Evidence `kind=USER_CONFIRMATION,status=INFO,result={confirmed:true}`，源和 command 中的指纹、
   task_revision/step_revision/Rule pair 与 Evidence 一致。重验所有新Event body_sha256与首次receipt；
   具体无hash_payload override的算法见API§7。破坏引用、text单字段或摘要须fail closed。

之后继续现有 `_fresh` 和 `_match`：完整 fingerprint SAME、cycle cutoff、精确对象确认 revision、
Rule pair 和其他 Evidence 门槛均保留。已提交确认不因后来 key 撤销而改写历史；新请求/重放不能使用
撤销 key。现有 State 接受时不会重验历史 key 是否仍有效，本项也不赋予追溯撤销语义。
Rule 变化可使下一次 submit 冲突；确认已提交后，适用 Rules 和 State 仍由接受时的既有 validator 再验。

## 6. 原子性、扫描与并发

新路由不得把 `fingerprint.capture()` 放进 writer transaction。准备/提交均使用：
读取一次 DB 审阅投影 → 在事务外有界扫描 → `BEGIN IMMEDIATE` → 重新认证/检查owner →
再次按command ID作精确replay/conflict判断 → 仅新命令继续对象/Rule/配置、消费/时间校验 → 一次提交。
prepare和submit都必须writer内再次replay；否则两个都错过早期lookup的相同请求会被误判为已消费。
writer 内只做 DB/已采样的廉价值校验，不读文件、不调用网络。DB drift 不静默重试，返回 409。
扫描失败/partial/unknown 均不产生确认；不得偷偷改动 VERIFIED 为 STALE。
服务端扫描窗口和上限见 API；材料可包含新 snapshot，但只有成功事务才持久化 snapshot。

文件系统与 SQLite 无跨域原子快照。比较的是提交前最后一次完整采样；采样结束到提交之间仍可有文件变化。
这不等于锁住工作区；接受时再次使用既有 fingerprint validator，变更须拒绝验收。
本项不修改旧 State validator 的扫描位置或旧 confirm 的实现，这是独立债务。

同一 preparation 只消费一次。两个并发同 command ID/同语义提交只有一份来源/Evidence；
同 preparation 不同 command ID 只有一个成功，另一个冲突。准备使用 client event_id；提交使用独立
client event_id。相同 ID 改 body 或换 actor/key 为冲突/拒绝，不能重做副作用。
已成功请求精确重放优先于过期、后续 Truth 漂移、再次扫描；先完成当前认证和 owner 校验。
重放返回首次 receipt，不代表它仍然 fresh。status 是不扫描的 receipt 读取，不代表 freshness。

## 7. Schema 与发布兼容

新增 preparation 和 submission 两张投影表，无新公共 ID 前缀；preparation_id 就是 prepare 的 evt_ ID。
完整 schema/约束见 API §7。Event、准备记录、首次 receipt 均 append-only；提交消费通过独立
submission 表的 UNIQUE preparation_id 表达，不更新 prepare。
空库/旧库前向迁移，不编辑任何已发布迁移。先确定集成基线再分配连续号；不把 schema8 的 web
分支直接写成 schema10/11 接口已可用。旧程序按已有规则拒绝更高 schema；保留原 Event/Evidence bytes。
旧来源无需 backfill，旧确认和验收回归必须通过。

新增三条专用路由及 auth/me；代理只开精确 method/path，并在两个 host 共用默认关闭的
`P6_ENABLE_WEB_CONFIRMATIONS` opt-in。Core 提供版本能力 `web_confirmation_v1`，不支持时 UI
显示依赖缺失、不 fallback 到旧 confirm 或 Raw 写入。详见 API。ADR0007/API 和 item5 文档的
来源枚举、UI候选判断要在实现时显式增补，保留原 item5 证据为其原范围历史记录。

## 8. 审计、故障与交付边界

成功来源/command 中保存首次 request_id、authenticated actor/key id、派生 host、材料摘要和所有
对象/Rule绑定，成为原子持久命令 provenance。禁止保存 bearer token/cookies/auth header。
prepare/submit/status 都按既有 dispatcher 写 metadata audit；拒绝没有半笔来源/Evidence。
失败 audit 仍遵循 ADR0004 best effort，不声称保证持久记录每一次拒绝。正常验收必须核对 audit
readback；独立故障注入必须记录 audit 丢失限制，不能把无 audit 的真实点击链标为完整审计 PASS。
note原文按ADR0004保留，**授权产品API返回原文**，与现有Event GET/list相同；不承诺masked HTTP面。
prepare/status/replay的完整审阅材料也返回原文，review_sha256绑定人可查看的完整字节；
不以脱敏材料替代完整规则让人盲签。UI只作纯文本显示；日志/audit/分享的HAR、截图、bundle是
另行脱敏派生面，不得包含token或私人全文。产品授权Raw读取与可分享证据的权限边界不能混写。

实现顺序：Core迁移/保留来源/API/validator → 独立代码评审与组件/HTTP验证 → P6代理与表单 →
独立真实 Core+浏览器 Gate → 最终集成 revision/readback。测试设计在 validation-plan.md。
Core身份服务/扫描/原子性不归前端实现者自行决定。

当前只交付方案与独立设计评审。不提交生产代码、不启动服务、不改 hooks/trust/用户数据库，
不发布、不宣布 P6 或 V1 完成。独立设计 APPROVE 仅说明方案可进入用户确认，不授权实现。

用户需确认的决策：接受专用 Web source + 两阶段确认、bearer身份信任边界、INFO语义、
新增持久准备记录/路由和按实际集成基线迁移；或明确要求继续只接旧真实 prompt 来源。
