# ADR 0008 — P2-01 Interpretation 候选链

- 状态：Accepted；provider no-execution.v2 已冻结并按真实/组件边界验证。完整 P2 的 scoped admission-only 独立验收见 [P2结果摘要](../p2-results.md)，不宣称实际工具执行或 P3。
- 日期：2026-09-29
- 基线：`9de00fe`，Schema 6；本 PR 独占新增 `m0007_interpretations` / Schema 7。

## 1 数据和来源

Interpreter 只读已提交 Event，不接受覆盖原话的请求文字；只产候选，不调用 Authority、State、Evidence 写入口。支持 `user.prompt` 和 `assistant.message`，其余事件 400 `invalid_interpretation_source`。Registry.kind必须与Event.actor_kind一致，否则拒绝。session非空host必须与Event.host一致。从 Event envelope 的 Registry actor 派生 provenance：human/user.prompt 为 `USER_EXPLICIT`（这只描述来源，不确认每个语义）；agent 为 `AGENT_PROPOSED`；system 为 `SYSTEM_CONFIG`。模型不能输出 actor/source_type。system 自称 user.prompt 不变 human。`USER_CONFIRMED` 留给后续有权限的明确人工确认。

P1 bound hook 实际用 human_token_file 保存 user.prompt，因此直接兼容。`payload.source_session_id/turn_id/step_id` 是数据，不能绑定 Core identity。Core Event 的 project/task/session/host 引用均重新核实，task 必须属于同 project，session 的非空上下文须兼容 Event（Event 可无 Core session）。model scope 内非空 project/task ID 必须等于源 Event；task scope 无 task ID 仅表示待创建/待绑定候选。GLOBAL 候选没有project/task引用，仍无执行权。PATH 必须是相对路径且不含 `..`；TOOL 字符串仅是候选限定，不能自动成为 P1 matcher。

输出schema version为服务端out-of-band固定，不接受模型伪版本。候选 Schema `jasmine.interpretation.v1`：顶层 event_id、confidence、candidates；1–16 项；每项 kind（TASK_CREATE_OR_ATTACH/REQUIRED_CAPABILITY/RULE/CORRECTION/DECISION/ACCEPTANCE/NO_STRUCTURE）、content、scope、impact（LOW/MEDIUM/HIGH）、certainty（EXPLICIT/TENTATIVE/QUOTED/NEGATED/AMBIGUOUS）、confidence、rationale、source_span。source_span 为零基 Unicode code point 半开 start/end 和逐字 quote，必须匹配原话。每层拒绝未知字段、duplicate JSON keys、NaN/Infinity/bool-as-number、超长字符串、非法枚举及错误引用。DECISION 仍是候选，无 confirmed Truth 字段。ACCEPTANCE 只保存自然语言要求，不能生成 PASS 或验证结论。NO_STRUCTURE 不能与其他候选共存。

## 2 持久化、请求身份和失败恢复

注册 `int_` interpretation ID，形状遵循 ADR 0001。Migration 0007 建三张 append-only 表（interpretation_request_keys 为不可变别名键映射），其中：interpretations（reservation/配置/源/context/input hash/处理 actor/created_at/deadline_at/父解释引用）与 interpretation_results（每个解释最多一条不可变终态，raw_result_json/candidates_json/confidence/status/error_code/completed_at）。原 Event 独立先 commit；reservation 再短事务 commit；provider入口显式断言conn.in_transaction=False；provider 调用无 DB transaction；终态再短事务 commit。成功 `EXTRACTED` 不等于 applied。失败 `FAILED`、中断恢复 `INTERRUPTED` 可查，Raw 不受影响。无终态且deadline未到是 `PROCESSING`。

POST 必须 idempotency_key（1–128 ASCII safe characters）和 event_id，可选 extractor_id（默认 codex-local-v1）。scope `interpretations:process` 必须同时具备 `interpretations:read` 和 `events:read`，因返回含原话-derived内容。唯一 `(processor_actor_id,idempotency_key)`；body先将省略extractor_id归一化为默认计算request hash；相同 key/body 精确重放已有配置provenance，即使运行配置已更新，也不重处理。新的key则按当前冻结config digest去重，异 body 409 `interpretation_idempotency_conflict`。相同 actor/event/input/config 且无显式重跑，换 key 也重放已有解释；别名请求记录 append-only key映射。不同 processing actor 分开。初次所有终态/失败记录返回201；重放/处理中返回200。失败是处理资源状态，非成功语义，HTTP201不代表提取PASS。

重放不会启动新provider。过期 PROCESSING 在短事务内追加 `INTERRUPTED/provider_interrupted` 终态；不重复外发。真正 rerun/correct/reject 留 P2-02 API 和新父解释引用。无DB锁跨provider。迟到结果发现已有终态必须返回已保存结果，不能覆盖 INTERRUPTED。断电导致reservation存在、终态缺失时，重启GET/POST能恢复并读到INTERRUPTED。

保存字段：interpretation_id/event_id/project_id/task_id/session_id/host_id/source_actor_id/source_actor_kind/source_type/processor_actor_id/extractor_id/extractor_model/extractor_version/provider/prompt_version/output_schema_version/schema_digest/config_digest/input_hash/config_json/parent_interpretation_id/created_at/deadline_at；result含bounded原始JSON文本、候选和总置信度。配置固定digest含provider runtime版本/executable hash、模型、prompt/schema、限制及隔离设置。程序版本与模型名称区分；CLI没有服务端模型build attestation时不可声称知道服务端权重版本。

## 3 Provider

优先复用已登录 Codex，固定 gpt-6.1-sol；不接V0、不新建账户或付费凭据。operator配置选择绝对CLI路径，不允许HTTP body挑可执行文件/model/prompt。以新临时private目录运行 `codex exec`，无repo配置，无resume；`--ignore-user-config --ephemeral --skip-git-repo-check --sandbox read-only --output-schema --json`；清除继承Jasmine Gate nonce/服务token环境、禁hooks/memories/plugins/apps/agents/browser/web/shell/image等能力，固定prompt仅把source JSON当数据。不能仅凭prompt说禁tools。provider上线前须捕获实际完整请求并验证model为指定ID、无执行工具面及固定schema/prompt；平台仅允许一项默认模式不可执行的Plan-only request_user_input元声明，须独立forced-call证明不可执行；不满足此合同则 BLOCKED/provider_unavailable，不降级执行原话命令。独立验证agent负责最小preflight；具体可运行flags在合同评审附录冻结。

限制：UTF-8源原话<=32KiB、完整输入<=48KiB、结果<=64KiB、CLIstdout/stderr各<=256KiB；timeout默认120s（含spawn/输出），进程组终止、短grace后kill，有限缓冲防大输出。原始畸形JSON截断上限保留，error_code固定不返回stderr/token/堆栈。输出CLI item允许text-only reasoning、唯一agent_message及完整顺序生命周期/usage；出现任何工具执行/调用项fail closed。这是额外断言，不能替代完整请求工具面的独立检查。fake provider明确 `component_simulation`，不被真实Gate认作LLM。

真实saved-auth smoke已验证CLI0.159.0 / gpt-6.1-sol约11秒可结构提取；该结果只证明provider能力，不证明硬禁tools。provider不可用/timeout/输出异常留下FAILED。P2-01不接同步hook；P2-03 deadline必须单独冻结：当前P1 hook12秒，超时必须显式pending/block，Guard仍生效，不能输出空context暗示本轮已处理。

官方能力依据：[非交互/结构输出/复用saved auth](https://learn.chatgpt.com/docs/non-interactive-mode)，本机 `codex exec --help` 确认额外ignore-user-config/ephemeral能力。

## 4 API与权限

POST `/v1/interpret`；GET `/v1/interpretations`；GET `/v1/interpretations/{int_id}`。GET scope interpretations:read + events:read（返回原始model输出可能引用源原话）。列表固定limit1–200默认50，after_id按稳定created_at/int_id排序游标；可event_id/project_id/task_id/status过滤，响应items/next_after_id。不接受自由SQL/其他source正文。unknown query/body字段400；ID错误400；不存在404 interpretation_not_found/event_not_found。

P0/P1 tokens是整个Core实例的scope，尚无project ACL；过滤是查询上下文而非授权隔离，本PR不宣称租户级project隔离。项目/任务来源一致性仍是强校验。Agent可read/process（不能改变来源身份/Truth）；human/system可read/process；没有新增review/manage权。key mint不得凭新scope获得旧Authority/Evidence权限。

## 5 P2-02 Policy待实现边界

当前Task、USER_EXPLICIT、LOW、EXPLICIT REQUIRED_CAPABILITY 可由后续policy形成task requirement；Agent/Memory至多PROPOSED；tentative/quoted/negated/ambiguous不confirmed；GLOBAL/PROJECT HIGH/HARD或跨scope放宽进入review。已完成/测试通过不产生PASS。CORRECTION有现Task须绑定其task，不另造Task。此PR无Resolver实现，后续独立ADR冻结具体P1 scope/matcher映射、全部对象revision集合、命令Event+派生Truth同事务、人工correct/reject/rerun权限与review CAS。

## 6 验证

空库/含P1数据v6升级及重复迁移、旧程序拒绝v7；strict schema负例/source引用/身份伪造；并发重复请求只一个provider调用；坏输出及timeout留失败Raw；provider期间独立Core writer成功；中断reservation恢复、迟到结果不覆盖；scope/audit脱敏；真实provider输出至少一个Playwright候选且Truth前后无变化。仅这些通过才能称P2-01完成，不称完整P2 PASS。

## 7 Provider合同评审附录

2026-10-01 CLI0.159.3兼容复验：独立实际binary/owned local mock Responses probe验证Interpreter实际_argv 8/8、P2 admission 8/8、P3 app-server 11/11；包含精确工具边界、Default模式Plan-only request_user_input不可用及未知工具拒绝。证据为artifacts/codex-01593-component-probe/summary.json。据此允许SHA256 4d210f7c5a18fd0386434df23b5bdbb8c0e7257d3e8a2b30b0769c8bbe99a878，保留旧0.159.0 fingerprint；配置版本按精确binary SHA选择，执行前仍复核。此为无付费model的component边界证据，七个真实Gate均NOT_RUN，不证明新版真实模型提取或Gate通过。未修改全局config/hooks/trust。

初始独立component preflight捕获CLI0.159.0实际请求，顶层tools字段缺省、model精确gpt-6.1-sol、strict json_schema；同配置真实saved-auth调用13.8167秒产出结构候选。但后续完整检查发现input.additional_tools仍含code-mode functions.exec，因此初始记录不证明工具面为空。固定actual catalog条目清除apply_patch_tool_type/experimental_supported_tools/supports_search_tool及tool_mode（null），模型slug保持原值。禁用features与进程env whitelist以src/jasmine_core/interpreter_provider.py为准；CLI二进制SHA固定，其他版本拒绝直到重新评审。operator用JASMINE_CORE_INTERPRETER_CODEX和JASMINE_CORE_INTERPRETER_CATALOG显式启用；HTTP不可设置。catalog内容/hash与绝对executable路径/hash记录config digest；每调用重新核二进制hash，实际临时catalog由冻结字节生成。继承环境仅HOME/PATH/TMPDIR/LANG/LC_ALL/SYSTEMROOT和固定saved-auth CODEX_HOME，清除API key/BASE_URL/nonce/proxy/provider覆盖。没有修改全局config/hooks/trust。

不同processor actor同源Event各有解释；后续Resolver必须按源Event及已应用历史去重，不能仅按interpretation_id。human assistant.message映射AGENT_PROPOSED（非用户明确），仅human user.prompt可标USER_EXPLICIT。schema版本不由model输出。

P2-02/03续接验收须包含project已知但task为空的真实原话，之后由Resolver创建Task及requirement；P2-01允许TASK scope task_id=null。P1旧hook的task/step固定绑定保持兼容，project-only输入到后续Task绑定过渡留给P2-03，不由本PR预造Task替代自然语言创建Gate。

Provider额外设置project_doc_max_bytes=0并写入config digest，阻止全局/目录AGENTS正文进入实际解释prompt；独立preflight必须按候选相同flags验证无额外用户文档和完整工具面（含input.additional_tools）。timeout取消包括已退出父进程但仍持pipe的所有本调用processgroup子进程（TERM后KILL）；不触已有服务。

真实HTTP首轮保留FAILED/provider_tool_use：当时曾假设失败由非工具reasoning完成项引起；后续下段诊断推翻了这一失败归因。协议reasoning-and-final.v1严格允许thread.started→turn.started→零或多条指定reasoning文本→唯一agent_message→turn.completed；reasoning仅接受id/type/text且text是字符串。任何其他item或晚到reasoning仍fail closed，完整请求的工具面必须通过preflight确认满足no-execution.v2边界；该合同不声称无工具声明。reasoning协议版本进入config digest，首轮FAILED不覆写，最终独立重试用新配置provenance。

真实失败进一步定位为Code Mode不可用startup error项，并非已证实reasoning项；保留拒绝error项，不把启动降级视为无工具成功。provider强制actual catalog tool_mode=null以避免code_mode_only在input.additional_tools注入functions.exec，模型slug保持gpt-6.1-sol。最终必须检查顶层tools、input.additional_tools和所有namespace：只允许默认模式不可执行的Plan-only request_user_input声明，其他执行/操作面必须为空，且真实调用无降级error；尚未成立时仍BLOCKED，不将之前结构结果标为安全providerGate PASS。

固定skills.include_instructions=false和skills.bundled.enabled=false并记录digest，避免自动技能block污染解释prompt（[官方配置Schema](https://learn.chatgpt.com/docs/config-schema.json)）。tools契约仍待完整工具面结果，不能从工具禁用flags直接推断无可调用面。

## 8 no-execution.v2 合同调整（root审查后决定）

用户要求Interpreter只抽取数据、不执行工具/命令。CLI当前固定catalog tool_mode=null后仍声明一个Plan-only request_user_input元工具，因此撤回任何literal tools=[]/绝对无声明的旧结论，保留初始preflight与真实HTTP FAILED证据。本版本名称为no-execution.v2，不能写成no-tools。完整实际请求仅可有该唯一元声明，不可有exec/shell/code-mode/MCP/browser/file/网络动作工具。CLI固定`codex exec`默认非Plan模式、ignore-user-config、private无配置目录，禁default_mode_request_user_input；HTTP及model数据不能选择/切换mode。该执行模式、meta声明白名单和default-mode禁调用flag均入config digest。

独立验收必须让component模型响应实际调用request_user_input，检查完整后续请求/CLI日志：默认模式明确拒绝、不会弹用户问询、不会切Plan、不会执行命令/其他操作。只有这一拒绝证据和完整工具面检查成立，才能冻结此provider；描述中的Plan-only本身不是证明。任何额外native工具/错误项仍fail closed；reasoning仅数据whitelist。随后在相同candidateSHA跑真实LLM/API并确认Truth不变。若元工具可以实际询问用户、改变模式或执行操作，此路线BLOCKED。

## 9 固定官方 saved-auth HTTP/SSE transport

候选467e736真实HTTP120秒达到截止，保留FAILED/provider_timeout，不延长deadline。实际CLI此前WebSocket多次reset后才HTTPS降级；root允许固定官方支持的HTTP/SSE传输。CLI拒绝override保留的built-in openai标识，独立probe用配置名interpreter-openai，固定{name="OpenAI",wire_api="responses",requires_openai_auth=true,supports_websockets=false}，不设base_url/API key，仍复用相同官方saved-auth路由；probe真实调用成功且无降级error项。该标识及四项配置与transport进入digest。新配置的最终同SHA真实API仍须独立验收，初始FAIL不覆写。支持参数依据[官方config Schema](https://learn.chatgpt.com/docs/config-schema.json) ModelProviderInfo。

最终HTTP/SSE首次实际模型调用已无网络/协议异常，但严格schema拒绝TASK候选的scope.tool="Playwright skill"（应null）。保存FAILED/invalid_output:scope_shape；不放宽schema。固定prompt升级jasmine.interpreter.v2，逐项明确GLOBAL/PROJECT/TASK/PATH/TOOL字段shape，能力名只写content，TASK的path/tool必须null。新的prompt version/digest区分该失败配置；最终同SHA重验仍是门槛。

Provider cleanup uses owned-group-bounded.v2 in configuration digest. Group TERM/KILL denial does not prove descendants terminated. Only a still-live child represented by this invocation Popen may receive terminate/kill fallback, with bounded waits. Primary timeout/output-cap error is retained; error_code adds fixed cleanup_permission_denied/cleanup_incomplete qualifiers when cleanup cannot be confirmed. No PID, stderr or user process is exposed or targeted. An exited parent receives no PID fallback.
