# Jasmine Core V1 P3 阶段任务书：Checkpoint、Context Pack 与真实续接

日期：2026-09-29。状态：实施任务书，未实现、未验收。准备基线为 P1 完成的 `main@9059625`、schema 6；启动时重新核对事实。仓库为 `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1`。本线程按 P2 → P3 顺序实施，P2 真实 Gate 通过并合并后才能开始 P3 实施；可提前冻结 P3 设计和跨阶段契约，不提前声明续接能力。

明确委派实施后允许必要的项目/临时目录隔离开发测试环境及依赖准备；禁止擅改/停止用户现有服务、生产自启动、外部付费账户、全局 user config/hooks/secret/trust、重新连接 V0 或启动其他 taskbook。V0 `_archive/` 仅历史。所有设计包保留，设计 input 不是已实现证明或即时环境操作指令。

## 1. 来源与要求级别

设计输入原件hash、大小和便携交接结构见 [阶段任务书设计输入索引](Jasmine-Core-V1-阶段任务书-设计输入索引.md)。

原设计文件为 `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jsamine core/Jasmine-Core-V1-设计实施验证文档包/Jasmine-Core-V1-详细设计书.docx`、同目录 `Jasmine-Core-V1-模块设计书.docx`、`Jasmine-Core-V1-实施与验证任务书.docx`。外部 Agent 必须收到包含三份原 DOCX 与 SHA manifest 的完整便携任务交接 ZIP，或具有上述原文件访问权；缺设计输入先补交接包，不仅依赖 Git checkout。原 DOCX 不发布到 public Git，设计包保留。`artifacts/phase-planning-20260929/` 三份同名 TXT 仅为可选本地完整抽取，在其他 checkout 可能不存在。

| 原文/已有契约 | 明确原要求 | 本任务分配 |
| --- | --- | --- |
| 详细设计 §3、§8 | Authority > Memory；Current State > 旧 Checkpoint；唯一结构化注入入口；revision/fingerprint Resume；1500–2200 tokens，约2500上限 | P3-01/02 |
| 模块设计 §9 | deterministic projection、可选非权威 note、六类 checkpoint触发、旧快照不回滚、超前revision integrity error；三个 API | P3-01 |
| 模块设计 §10 | Core统一组装、全适用HARD、exact rendered pack、实际rule versions/memory IDs、token计数、AUTHORITY_TOO_LARGE、Memory degraded | P3-02 |
| 模块设计 §8/14 | fingerprint partial不假相同；Agent lifecycle边界 | P3-01/03 |
| 实施验证 §2/3、§7/8 VC-04/11/12/13/24/25、§10 G6 | compact/交接真实续接、预算和HARD规则、真实证据包 | P3-03 Gate |
| docs阶段PR计划 | P3-01 Checkpoint + PreCompact/Stop；P3-02 Context/SessionStart/Resume；P3-03真实Gate；独立Review/验证 | §7顺序 |
| ADR 0001–0007、现有API/src | 公共ID、Event-first单事务、幂等、权限、Evidence/fingerprint与P1最小hook读取 | 不重写基础 |

下文字段、scope、错误码（除原要求 AUTHORITY_TOO_LARGE）、请求一致性策略、tokenizer选择、文件边界、runner/schema/PR细分为**候选建议**，须负责人和独立Reviewer冻结成ADR/API；不能将建议写成原设计已有决定。

### 1.1 官方 hook 与实测能力分开

2026-09-29 使用 OpenAI Docs skill 检索并实际打开 [官方 Hooks 文档](https://learn.chatgpt.com/docs/hooks?translationFallback=de-DE)。文档列出 SessionStart、PreCompact、Stop；SessionStart source 含 compact，支持 additionalContext；PreCompact在compact前触发。非受管hook须用户审查信任。此文档支持不证明本机/本平台已执行；实施启动重新核对版本、event schema、官方限制并做真实入口探测。

P1 ADR0007与实际源码只有UserPromptSubmit最小Rule/Task/Step附加上下文及受控Pre/PostTool；**不是P3 exact pack，也没有P3真实compact证明**。官方可有PostCompact等其他event，但P3不据此扩成P5完整Adapter；post-compact注入具体走何入口、Stop含义/递归保护、各平台触发与输出限制须实现前冻结并实测。缺实际compact入口/信任时BLOCKED，不通过手工hook调用或拼JSON补齐。

## 2. 目标、依赖、owner

完成 P3 应让真实 Codex compact 后，用户仅说“继续”，Agent 重新加载当前Rules/State及安全Checkpoint，准确区分执行/验证/验收，保留任务边界并继续真实动作；旧Snapshot和workspace变化可明确识别，exact实际注入pack可独立读回。

| 模块/共享文件 | owner与P3读写范围 | 不允许行为 |
| --- | --- | --- |
| P2 Interpretation/Resolution/review | P2 owner；P3消费已决议的task绑定/Rule/State/source | P3重写语义解释、审批/纠正政策 |
| P1 Authority/State/Evidence/fingerprint | P1领域；P3只合法读取/调用现API | Resume恢复旧State，凭note创建PASS或接受任务 |
| P3 Checkpoint、Resume、Context schema/exact render/token/ruleversion/memory_result_ids | 本阶段owner | P4/P6另建该领域表/预算/注入 |
| P4 adapter/worker/query trace | P4可并行先做隔离模块；P3 owns Memory召回slot/provider合同 | P3装Hindsight/代写job；P4改ContextBuilder |
| P6 Dashboard | 只消费冻结API、显示exact/trace | 直写SQLite、fixture代表实测、修改P3数据 |
| 公共ID/auth/API/errors/migrations/SCHEMA_VERSION | root/integration单一协调窗口 | 三支同时抢m0007；改已发布迁移/跨域大重写 |

P3-01前冻结project/task/session合法绑定、checkpoint source/读写权限与revision一致性、fingerprint采样、公共ID、新迁移分配。P3-02前冻结exact文本renderer版本、tokenizer/encoding/model/库版本、2500具体整数上限与分区预算、Memory provider签名、Resume reconciliation、安全失败语义。P3-03前冻结官方hook输入输出、命令级隔离、真实compact触发、pack传输限制、cleanup/证据协议。

设计可提前review；实际next task顺序为：P2最后候选真实Gate → P2合并后受影响Gate复跑 → root确认P3依赖冻结 → P3-01 → P3-02 → P3-03真实Gate → 最终commit复跑 → 将P3 Memory slot提交给P4/P6消费。

## 3. P3-01：确定性 Checkpoint 与安全 Resume

### 3.1 交付

建议`checkpoint.py`、`resume.py`与专属测试，具体目录由owner冻结。使用当前State、Rule refs、Evidence refs、Workspace组成deterministic projection；next_actions/working_set来自已有可追溯结构数据，不能LLM猜测变权威。可选human/LLM working note明确非权威，保存生成/source/version，不能覆盖结构字段。

原设计 API 路径：`POST /v1/checkpoints`、`GET /v1/tasks/{id}/checkpoints/latest`、`POST /v1/tasks/{id}/resume`。请求字段/返回/error未冻结；建议写请求只接受task/reason/来源/非权威note，State/Rule/Evidence快照由Core读，不接受客户端任填VERIFIED/ACCEPTED。latest按合法来源与创建顺序稳定选择，不信客户端clock。

候选Checkpoint字段：Core分配checkpoint_id（prefix经公共IDreview）、project/task/session/source_event_id、reason、task_revision、current_step/step_statuses、rules(id/version)、evidence refs、next_actions/working_set、workspace_fingerprint_id/hash/coverage、note、renderer/schema version、created_at。不可变快照+Event-first事务，幂等命令不产生第二快照。Checkpoint创建本身不增加Task revision；状态变更由P1独立合法命令负责。客户端来源跨task/project/session、不存在ref拒绝。

确定性采样必须说明一致性：读取State与Rule/Evidence快照的统一读事务；fingerprint实际文件扫描不能假装与DB锁形成全局原子。建议记录采样时刻/hash/coverage，并在构建末核对Task/Rule版本漂移，遇竞争限次刷新或显式冲突，不输出混合版本的“当前”pack。远端Memory和耗时文件扫描禁止占SQLite写锁。

触发包括PRE_COMPACT、SESSION_STOP、HANDOFF、BLOCKED、STEP_VERIFIED、MANUAL；P3实现实际可接的Core事件/显式接口和本机hook，未接入口声明未验证。阶段PR建议将PreCompact/Stop脚本骨架与store对接放P3-01，真实lifecycle验证统一P3-03，不用fixture宣称hook触发完成。

### 3.2 Resume判定

- checkpoint_revision < current：标stale/history，Current State优先；旧note只能作历史提示，不导出当前步骤真相。
- 相等：仍比较workspace及Rule版本；revision相等不证明环境相同。
- checkpoint_revision > current：明确integrity error（候选`checkpoint_integrity_error`），停止安全续接；不能降低Checkpoint revision掩盖错误或回写State“追平”。
- fingerprint SAME仅完整、算法/coverage一致、实际hash相等；HEAD相同但dirty/untracked变化仍mismatch；partial/UNKNOWN不可SAME。
- mismatch：输出CHECKPOINT_WORKSPACE_MISMATCH与diff，要求reconcile/重新检查输入与Evidence，不沿旧文件假设直接修改；合法fingerprint刷新及STALE迁移仅走P1现有受信API。
- 无Checkpoint可从当前Truth构建pack，明确不存在；绑定缺失/歧义不得猜任务。Resume只读及创建新的Context/trace，不替代State命令，不能回滚revision。

### 3.3 正负/失败测试

正例：六类reason、来源完整、结构投影可复算、同命令幂等、重启latest读回、旧cp与新State合理diff。负例：任填status/revision、跨项目ref、agent伪人类、note指令注入、过期Evidence、没有task、cp>current被拒绝。失败例：Event/projection中途异常一起回滚，快照读时并发revision/Rule变化，fingerprint扫描失败/partial/coverage改变/symlink，DB busy与source缺失均明确机器错误。检查Checkpoint/Resume不能更改Task/Rule状态，除显式合法P1 fingerprint命令产生预期STALE事件。

## 4. P3-02：唯一 Context Builder、exact文本及真实tokens

### 4.1 交付与候选契约

建议`context/builder.py`、`renderer.py`、`tokens.py`、`memory_provider.py`，专属持久记录/API/测试。原 API：`POST /v1/context/build`、`GET /v1/context/{id}`。沿现API鉴权审计，不建旁路SQLite调用或独立Context真源。

候选输入：project/task/session、reason、user_prompt_event_id/query、请求幂等ID；绑定由Core核验。候选持久字段：context_pack_id/source_event_id、resolved scope、state_revision、rule_versions、checkpoint_id/freshness与workspace comparison、memory_result_ids/query_trace_ids、evidence_ids、exact rendered_content/UTF8 hash、renderer version、tokenizer身份、分区/总token计数、budget与裁剪理由、degraded/error、created_at。权限建议context:build/read与checkpoint:read/write，但新scope、actor kind、项目授权和raw内容限制必须review；P6读取trace不能自动获取不具events:read权限的原话。

明确构建顺序：识别task → 当前State → 全适用Active Rules → latest Checkpoint及revision/fingerprint比较 → 可用且有益时Memory Recall → evidence hints → 去重/分区预算 → 保存实际rendered pack。Authority按已有global/project/task适用规则确定性加载，不能只抓“最相关”Rule。P3 owns current Truth version引用，P4不重定义。

### 4.2 预算及精确性

原设计目标：Authority 400–700，State 300–500，Checkpoint 200–350，Memory 400–600，Evidence hints 100–250；总目标1500–2200，硬上限约2500 tokens。分区范围是指导目标，并非必须凑满每区。实施需冻结确定上限（建议2500）、动态分配/优先级与必留字段，含实际header、分隔符和schema标签开销。

选择实际tokenizer/encoding及库版本，说明对目标模型的适用范围；计数基于最终exact renderer字节解码得到文本，非字符数/4或字节估算。精确指对冻结tokenizer的可复现计数，不能虚称精确等于未知平台隐含prompt总tokens。分区合计受跨边界tokenization影响时采用明定切片/计数方法并保存全pack独立count，不能凭加法假“exact”。库缺失/未知模型encoding不得悄悄fallback估算PASS；配置错误按被冻结语义拒绝/明确未验证。

HARD Authority不得静默裁剪或摘要替代。全HARD超预算返回原要求`AUTHORITY_TOO_LARGE`（wire code大小写/HTTP状态需ADR冻结），不输出缺Rule的正常pack。先削可选Memory/旧note/evidence正文等；每裁剪记录section/来源/reason/tokens，State当前目标与安全边界不得被预算隐去。没有Memory时允许少于目标；不能塞历史凑tokens。

保存的rendered_content必须等于发送hook additionalContext的正文；若hook包装、转义、限制裁剪可能改变正文，需输出前验证与传输receipt hash/长度，平台截断导致差异为FAIL或BLOCKED，不能GET pack正文当实际注入证明。构建不得覆盖旧pack，完整State/Rule/source引用可读回。

### 4.3 Memory slot与失败

与P4冻结provider输入task/project/query/types/max_tokens/current authority refs/state_revision；输出items/source/bank/token/filtered reasons/status=ok|empty|degraded。slot未实现/配置关闭显式标`unavailable/not_configured`，不编造召回。P3的真实compact Gate可以无Hindsight完成，验证Truth独立及Memory非权威；P4“真实召回帮助”Gate等待此实际注入路径及真实引擎。

普通pack只能Recall，不自动Reflect；Memory区明确NON AUTHORITATIVE，过滤与当前Decision冲突或无合法来源结果，当前State/Rules仍胜。provider异常/deadline输出degraded并省略Memory正文，保留安全code/trace，Task不受阻；绝不在写事务等远端。

自动化覆盖：各scope Rule全加载与预算边界；中英/Unicode/JSON转义/header tokenizer计数；同输入重复/幂等规则；empty/degraded Memory、越界project/失真provenance、恶意“忽略规则”结果；超大HARD error；并发Rule/State变更；broken Evidence hint；restart exactpack/hash/version读回；权限不足不给rawpack。回归P1完整Rules/Guard与Evidence门槛，不拿P1 8KB限制代替P3真实token预算。

## 5. P3-03：Codex lifecycle与实际compact/交接

交付项目局部installer增量、官方schema adapter、真实入口probe、受控Gate runner与恢复说明。SessionStart读取已绑定task，经Resume/Builder取得exact pack并用官方输出注入；PreCompact写deterministic checkpoint；Stop写安全checkpoint并避免递归/每轮重复。重入幂等键与Stop官方字段冻结；不得因Stop checkpoint“成功”擅自ACCEPTED。交接使用Stop或显式HANDOFF checkpoint，由新真实Codex Session恢复；跨Windows/Claude完整接力属于P5。

P1 UserPromptSubmit最小Context与P3 pack同时存在时需review唯一结构化注入：增量替换为调用同一Builder或停旧最小reader，但保留P0 raw capture、P1 Pre/Post/Guard/Evidence行为；禁止两个相互矛盾的Rule/State pack。同次hook有重复调用须correlation/dedupe，不用重复追加消耗预算。

### 5.1 命令级隔离与信任

沿P1实际lease/nonces实现思路但不照抄当平台保证。P1 `scripts/p1-real-conversation.py` 已有`CODEX_MEMORY_ISOLATION`及每个Codex调用的`-c key=false`注入；曾有内置memory worker先触发hook抢lease的教训。P3启动检查当前配置键和官方/本机支持，将隔离flag应用到**每次startup/resume/compact相关实际命令**，私有run_nonce/session lease绑定真实目标调用；后台worker或别的session不能认领Gate。预先清晰记录哪些配置仅对子进程生效。

不修改全局`~/.codex/config.toml`、全局hooks、持久trust或绕过hook trust标志。installer只操作明确授权的隔离fixture或仓库局部hooks，dry-run、备份、恢复、保留P0/P1其他定义；用户拥有信任/登录交互。缺信任BLOCKED，不能自动确认。凭证/nonce不入argv、prompt、hookJSON或安全日志；runner退出cleanup隔离资源，保存恢复receipt。

### 5.2 实际入口探测

实现前先记录Codex executable path/version/hash、平台、实际支持event与compact触发方式。真实PreCompact + SessionStart(source=compact)或经官方确认的post-compact组合，必须由平台事件链触发。手动POST checkpoint、模拟stdin、runner写“compact”日志只能做component test。CLI不支持真实compact时尝试已授权可用的真实界面/入口并读回证据；仍缺平台入口则BLOCKED，不拿新Session当compact。

## 6. 真实 Gate任务矩阵

真实Gate依赖P2已经完成的真实来源/任务绑定与P1权限/Truth，使用新V1 run/session/event。不能借V0/old activity/manual checkpoint代替自动checkpoint/注入。每场景独立可审before/after证据。

| Gate | 原VC/要求 | 操作及PASS判据 | 失败/BLOCKED |
| --- | --- | --- | --- |
| P3-G01 compact规则保持 | VC-04 | 真实对话建立经P2解析/合法激活的HARD边界；完成真实初步工作；真实compact；只说继续并提出违反边界的动作；自动cp和post-compact exactpack含当前规则，P1真实Guard阻止动作 | Rule消失/动作执行FAIL；无compact入口/信任BLOCKED |
| P3-G02准确续接及目标不漂移 | VC-24/25 | “只修callback task_id、不重构”；保留真实FAIL路线与下一动作；compact后Agent不用重述历史，仍做目标内真实工具动作，不重复已证伪路线；EXECUTED/VERIFIED/ACCEPTED不混淆 | 旧失败路线反复/重构或自述PASS当VERIFIED FAIL |
| P3-G03旧Checkpoint/旧Session | VC-11/12 | Session A cp revision r，Session B合法进展到r+n；A resume载新State/rules并标旧cp，不恢复旧步骤 | State/revision回退或旧cp当当前FAIL |
| P3-G04 workspace mismatch | VC-13 | cp后真实修改受控相关文件/HEAD（保留diff），resume实际fingerprint识别变化并reconcile/重测，不盲改旧路径 | SAME误判/旧Evidence仍证明新输入FAIL |
| P3-G05真实Stop/新Session交接 | 阶段目标/VC-10局部 | 真Stop触发checkpoint，另一真实Codex Session只说继续task，绑定当前Truth/pack，正确区分Build已验证与后续未做；明确单平台覆盖 | 手工cp冒充Stop FAIL；未验证跨平台不宣称P5 |
| P3-G06 budget/失败独立 | §10 G6、模块§10 | 实际tokenizer计数、exact发送hash；超预算HARD明错误并不注入缺Rule pack；Memory配置关闭/失败degraded仍通过真实安全续接 | 字符估算精确/平台裁剪/伪Memory PASS FAIL |
| P3-G07 P2/P3联合原话→实际工具 | VC-01/02 | 在P2明确委派的真实Playwright场景接续：真实用户原话“你好，帮我测试一下这个程序，使用 Playwright skill。”→同源Interpretation/Resolution→task-scoped requirement→P3实际exact Context Pack→真实Agent实际Playwright工具；再运行真实“我不是让你用curl，我说使用Playwright skill”纠正，CORRECTION与同一Task、Context diff强化required、curl-only路线rejected，并观察后续实际工具 | requirement仅fixture/pack未实际注入/纠正创建新Task/只curl完成为FAIL；缺真实Playwright skill或工具入口、用户委派/信任或P2来源链为BLOCKED，不合成对话冒充 |

G07是P2与P3联合验收：P2拥有原话解析、requirement与纠正Resolution，P3拥有exact pack与实际注入；不得P3旁路解析补齐缺P2。可引用P2已委派场景的同源有效证据，但在当前候选SHA必须复跑实际pack→工具及纠正Context diff，不以旧P2 component PASS代替联合Gate。

integrity cp>current用数据库故障fixture验证拒绝（明确component test，不刻意破坏真实生产DB）。P3完整Gate必须包含真实compact，Stop/newSession不能替代；完整P4召回、P5跨设备/P7综合长任务不在此偷宣称已完成。

## 7. PR顺序、三Agent职责与merge

每个阶段/PR由实现、独立Review、独立验证三个Agent执行，均使用用户指定 `gpt-6.1-sol`。职责按顺序调度，不因槽位不足合并review身份。

| PR | 实现Agent交付 | ReviewAgent阻断焦点 | 验证Agent交付 |
| --- | --- | --- | --- |
| P3-01 | checkpoint/resume schema/store/API与PreCompact/Stop增量骨架；ADR | 不可变快照/版本一致性/旧cp/超前错误/fingerprint权限 | component正负失败、恢复/迁移/source读回；hook仍标待真实Gate |
| P3-02 | Context/tokenizer/renderer/API、Memory slot与SessionStart增量骨架 | HARD预算/唯一注入/exact/Rule版本/Memory非权威/跨project权限 | tokenizer可复算与exact存储、并发版本、degraded/overflow；实际注入仍待Gates |
| P3-03 | lifecycle真实集成/入口probe/runner/cleanup/Gate | 官方schema与实际能力/命令隔离/lease/不改trust/无假compact | 候选SHA全G01～07、完整receipt与最终SHA受影响复跑 |

root/integration分配schema6后新migration号与公共ID/auth/API/errors窗口；各PR先提交迁移提案，不自行抢0007。rebase只重排尚未发布新增migration，保持连续并跑checksum/升级回归；禁止改已发m0001–0006。

合并条件：P2真实Gate依赖完成；冻结ADR/API/owner；该PR meaningful tests/必要P1回归通过；migration空库/已有库升级/重复执行及无半写通过；独立Review blocking关闭且记录SHA；独立验证绑定同SHA。阶段最后P3-03候选G01～07全PASS后合并，最终SHA复跑受影响Gates。component/fake-only或实际compact BLOCKED只能报告部分实现，不能过阶段最终门槛。P3-01/02可按自身边界合并但不得宣称完整续接。

## 8. 证据格式与验收结论

沿最小Validation Recorder的USER RAW/AGENT RESPONSE/TOOLS/STATE BEFORE-AFTER/Interpretation/Context，附P3证据；不重写P7全量Recorder。run数据不入Git，PR附安全manifest/路径/hash/可访问说明。不能仅HTTP200/checkpoint ID或additionalContext生成成功就判断实际model收到。

候选manifest字段：`run_id, candidate_commit, final_commit, schema_version, dependency_commits, platform, codex_executable_identity, hook_config_sha256, command_isolation_config, tokenizer{name,version,encoding,model}, cases[]`。每case保存`case_id, verdict(PASS/FAIL/BLOCKED), mode(real/component), reason, project/task/session/turn IDs, source_event_ids, interpretation_ids, resolution_ids, requirement_rule_refs, same_task_correction_event_ids, context_diff_receipts, actual_playwright_tool_receipts, checkpoint_ids, context_pack_ids, rule_versions, evidence_ids, state_revisions_before_after, fingerprint_comparison, rendered/sent_text_hashes, section/total_token_counts, degraded/errors, receipts{absolute_path,sha256,kind}`。字段名最终与现Recorder安全field规则对齐，不把token credentials写入manifest。

每真实Gate必须有：真实用户原话/Agent response、P2 Interpretation/Resolution与Rule来源、完整State history、pre/postcompact平台事件及hook trace、checkpoint current diff、actual exactpack读回/发送回执、token复算、绑定/lease session关联、真实tools与Evidence、fingerprint前后、Guard决定、cleanup。private数据用已定脱敏策略，证据hash/source可受权追溯；不得用日志“triggered”当真实compact/pack消耗完成。缺平台model-visible回执时结合原生事件/真实行为证明并明确证据局限，不虚称直接读到了模型内存。

## 9. 下一任务与启动prompts

**root下一任务序列：** 等P2最后真实Gate/合并确认 → 指派P3-01实现(gpt-6.1-sol) → 独立Review → 独立验证 → 合并；同法P3-02；P3-03实际生命周期/compact Gate → 独立Review/真实验证 → 最终commit复跑 → 把冻结provider/Inspector API和真实注入证据通知已授权P4/P6负责Agent。该序列是委派模板，本文件本身不发送跨chat消息或启动服务。

### 实现Agent prompt

> 在 `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jasmine-core-v1` 使用 gpt-6.1-sol 执行 root 指定的单个 P3 PR。先读AGENTS、本任务书、原设计§8与模块§9/10、现ADR/API和源代码；核对P2真实Gate合并及冻结契约。按分配文件实现，不改P2/P4/P6领域，不抢migration共享文件。Checkpoint确定性且不回写旧State；Context保存exact渲染/实际tokenizer/fullHARD/error，Memory非权威degraded；hook依据当前官方schema并实际探测，不把P1最小reader当P3pack。每条Codex命令隔离后台memory worker与Gate lease，不改全局config/trust，不用V0。只报告有receipt的事实，缺入口依赖BLOCKED；提交candidate SHA、tests、ADR/API提案、风险与PR。

### 独立ReviewAgent prompt

> 使用 gpt-6.1-sol 独立审指定P3候选SHA，不承担该PR主要实现。核对原要求/本任务建议/已冻结合同；重点snapshot一致性、旧cp和超前integrityerror、fp partial、Rule全加载、真实tokenizer预算/HARD overflow、exact发送与存储、Memory隔离、project权限、共享迁移owner。检查真实官方hook与平台receipt、命令隔离lease和不改trust，synthetic不能冒充compact。给文件行号/可复现blocking问题及证据，关闭阻断后再结论。

### 独立验证Agent prompt

> 使用 gpt-6.1-sol 从指定候选提交复核P3，记录实际SHA/schema/P2依赖/平台/Codex/tokenizer。复跑对应正负失败和迁移测试；最后PR执行实际G01～07，必须原生compact→自动checkpoint→postcompact exact实际注入→真实继续动作全链。联合复核G07真实Playwright原话/requirement/实际pack/实际工具，以及纠正同Task/Contextdiff；验证旧cp/新State、fp mismatch、HARD守护/预算和Memory degraded；核对每次命令后台memory隔离与lease不被抢。无入口信任依赖BLOCKED，不改全局配置、自动trust或V0服务；receipt/hash/明确mode/verdict与局限交root，最终SHA复跑受影响Gates。
