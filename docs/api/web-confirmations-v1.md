# Web confirmation API v1 (ADR 0015)

状态：用户已批准实现的 API 契约；schema 12 候选实现。实现评审与真实浏览器验收单独记录。
基线 SHA 见[批准 ADR §1](../proposals/p6-02-item6/adr.md)。API版本字符串固定 `web_confirmation_v1`；独立于 schema migration 序号。

## 1. 通用契约

沿用 `/v1`、bearer、JSON、Core ID、标准 `{error:{code,message,details,request_id}}` 与 `X-Request-Id`。
新增路由拒绝全部 query 参数、未知字段、重复 JSON key（包括嵌套）、非有限数字、无效 Unicode、
顶层非 object；只接受 `Content-Type: application/json` 的写入。ID 依 ADR0001，SHA为64位小写hex。
正整数必须 type=int 且非 bool。字段的缺失与显式 null 仅在表中允许时等价；其余 null 均400。
所有request及server-derived材料在hash前有界递归严格检查：仅文档规定的JSON类型、string键、
有限数值、合法Unicode和字段/数组结构；不得借canonical_json的default=str把任意对象变成字符串。
材料递归深度≤32、JSON节点≤50,000，超限typed拒绝，不截断。非法输入400 invalid_request；
非法当前server材料或stored材料409 confirmation_integrity_error。不得更改共享canonical工具的旧语义。
新业务请求 cap=16KiB（超过413）；完整 review_document canonical UTF-8 cap=256KiB（超限409，
不得截断任何规则或 criteria）。所有响应 `Cache-Control: no-store`；浏览器不持久保存准备/凭据。

全部保护路由先按现有 route/method→auth→scope→business 权限顺序执行；没有 admin 通配。
对新命令在 writer transaction 内再次复查 key 没撤销、actor仍human/host仍匹配、所需 scopes，
和可用的既有 ACL；已成功重放也要当前认证，owner/key不匹配403，不泄露别人的材料。
要求绑定同一个 key_id，换 human 同一actor的另一个 key也不可消费/重放；可重新prepare。

| Method / path | scopes（全部满足） | actor | 成功 |
| --- | --- | --- | --- |
| GET `/v1/auth/me` | 只需有效认证 key | Registry中当前身份 | 200 |
| POST `/v1/evidence/confirmations/prepare` | `evidence:confirm, objects:read, state:read, authority:read` | human | 首次201 / replay200 |
| POST `/v1/evidence/confirmations/submit` | `evidence:confirm` | human | 首次201 / replay200 |
| GET `/v1/evidence/confirmations/{preparation_id}` | `evidence:confirm, evidence:read, objects:read, state:read, authority:read` | 同human/key owner | 200 |

UI完整来源/readback另需要 `events:read,evidence:read,objects:read,state:read,authority:read`。
确认不要求 `state:accept`，拥有确认权限不意味着拥有验收权限。
旧 `/v1/evidence/confirm` 的human prompt body/scope/返回/错误不变，且保持代理关闭。

## 2. 当前身份和能力

GET `/v1/auth/me` 无 body/query，返回 exact shape：

```json
{
  "actor_id": "act_<26 Crockford characters>",
  "actor_kind": "human",
  "key_id": "key_<26 Crockford characters>",
  "home_host_id": "hst_<26 Crockford characters>",
  "scopes": ["evidence:confirm"],
  "capabilities": {"web_confirmation_v1": true},
  "trust_model": "registered_human_bearer_key"
}
```

以上 IDs 为格式占位符，不是可运行fixture。actor_kind可为human/agent/system；home_host_id可为null；
scopes是实际key权限排序列表；capability true只表示Core实现该API，不保证当前身份有权限或依赖可用。
不返回token/其hash/其他key/所有actor。新Core未支持时旧Core的404为DEPENDENCY_PENDING；
代理开关关闭的404区分 `proxy_path_not_allowed`。UI不能据按钮外观推断identity。

## 3. prepare

请求 exact fields：

| 字段 | 要求 |
| --- | --- |
| `event_id` | 必填evt_，作为prepare命令幂等ID，也作为preparation_id |
| `task_id` | 必填tsk_；Core派生project |
| `target_status` | 必填VERIFIED或ACCEPTED；无Step只能ACCEPTED |
| `step_id` | 可缺省/null，否则stp_且属于Task；null代表确认整个Task |
| `expected_task_revision` | 必填正整数，始终锁Task的当前revision |
| `expected_step_revision` | 有Step时必填正整数；无Step时必须缺省/null |
| `confirmed_rule_id` / `confirmed_rule_version` | 成对缺省/null，或rul_及正整数；Rule须当前ACTIVE且适用 |

不得提供actor/key/host/project/fingerprint/source/statement/status/Evidence。
按adr§4状态前置条件校验；revision不符409。无Step时至少一个ACCEPTED Step、其余ACCEPTED/SKIPPED。
有Step时ACTIVE Task；目标VERIFIED要求EXECUTED Step，目标ACCEPTED要求VERIFIED Step。
对象自身criteria requirements非空。非法状态/target组合409 confirmation_ineligible。
Rule不存在404；存在但不适用/已非ACTIVE/版本变动返回409 `confirmation_context_conflict`。

服务端稳定 snapshot：DB read transaction内读Task/Step、Task所有Steps（Task确认）、适用ACTIVE
Rules完整集合和完整Rule versions/verification mappings/activation事件；读取配置后在writer外扫描；
获得writer后重新比对DB投影和已采样配置identity。任何变化409，无透明重新prepare。
scan时限15秒，总请求30秒，DB等待沿用5秒；超过返回503，所有时间在处理入口建立单调deadline。
扫描不跟随symlink；使用既有完整coverage/extra paths，且cap不超过20,000成员/256MiB；
若完整覆盖超限，拒绝而非只取cap以内样本。不落库、不触发STALE直到全部检查通过。
配置identity是fingerprint algorithm/coverage/extra paths/root配置和本契约statement/render版本的canonical摘要。
最后一次扫描完成时间作为 `sampled_at`，明确表示文件采样，不宣称DB/FS原子。

成功同一事务：prepare Event + 新完整workspace_fingerprints snapshot（按现有project绑定identity算法）+
preparation投影。Event type=`human.confirmation_prepared`, source_system=`core-web-confirmation-v1`，
`session_id=null, source_event_id=null`，actor/key来自认证、host来自Registry；没有user.prompt，没有Evidence。

返回 exact top-level keys：

```json
{
  "preparation_id": "evt_<prepare event_id>",
  "contract_version": "web_confirmation_v1",
  "review_document": {},
  "review_sha256": "<64 lowercase hex>",
  "created_at": "<RFC3339 UTC>",
  "expires_at": "<created_at plus 600 seconds>",
  "sampled_at": "<RFC3339 UTC>",
  "replayed": false
}
```

`review_document` exact结构（数组不能截断）：

| field | type / 内容 |
| --- | --- |
| `contract_version` | 固定web_confirmation_v1 |
| `actor` | exact `{actor_id,key_id,host_id,trust_model}`，trust_model固定registered_human_bearer_key |
| `project_id` | Task所属prj_ |
| `task` | exact `{task_id,title,status,revision,acceptance_criteria}` |
| `step` | null或exact `{step_id,title,status,revision,acceptance_criteria}` |
| `task_steps` | Step确认=[]；Task确认为所有Step的`{step_id,status,revision}`，按step_id排序 |
| `applicable_rules` | 按rule_id排序数组，成员exact `{rule_id,version,scope_kind,project_id,task_id,kind,severity,enforcement,content,matcher,verification_requirements,activation_event_id}`；映射缺失为null并保留，不解释为充分 |
| `confirmed_rule` | null或exact `{rule_id,version}` |
| `workspace` | exact `{fingerprint_sha256,algorithm,coverage,complete}`，coverage复用fingerprint snapshot的原始JSON结构，不更改含义；complete须true |
| `configuration_sha256` | 上述配置identity的64hex |
| `target_status` | 本次准备目标VERIFIED/ACCEPTED；Task仅ACCEPTED |
| `statement` | adr§4固定中文确认声明，字节精确绑定 |
| `evidence_sufficiency` | 固定NOT_EVALUATED；不是Core验收预判 |

review_sha256=SHA256(`canonical_json(review_document)`的UTF-8 bytes)，
canonical_json复用Core排序键/紧凑分隔符/ensure_ascii=False约定，无末尾newline；现util实际有
default=str且未设allow_nan=False。新store必须先完成§1的严格JSON验证，才能复用其bytes算法，
不能把旧util当strict parser。合法Unicode材料的hash保持与现util相同。
prepare payload.text=""，payload含上述原始response全部字段（去replayed）、首次request_id/key_id和semantic_request_sha256。
semantic request hash包含契约版本、actor/key、method/path以及规范化字段全部值，排除request_id，
缺省可null字段先统一null；不得排除target_status、revision、Rule pair或task/step。其他未知字段直接拒绝。

created_at为实际提交时间，expires_at固定600s后；重放返回原创建/期限/材料，不延长期限或重扫。
原ID改body=409 event_id_conflict；该ID被其他事件占用同样409；同ID换actor/key=403。
prepare早期lookup和writer锁内lookup使用同一顺序：当前auth/scopes/owner → 已有prepare投影与Event完整性 →
同event_id精确semantic hash200 / 改body409。全局ID占用但无该操作合法投影409 event_id_conflict；
属于他人则403。两处lookup都先于new-request的revision、eligibility、scan、时间/DB drift判断。
两个请求都早期miss时，第二个拿到writer后必须先返回首次receipt，不能用自己的新时间/指纹重新append。

### 3.1 授权Raw与派生脱敏政策（所有新路由共用）

prepare、status、prepare replay返回完整原始review_document，review_sha256始终覆盖其全部原字节。
submit、submit replay、status消费receipt及既有Event GET/list返回原始授权Event内容，note不在HTTP
响应里掩码；不改现有Event GET/list序列化行为。review Rule含secret-pattern时也不偷偷掩码或删规则，
拥有相应读scope的人能查看完整材料，UI纯文本显示同一内容，不提供“masked view签raw digest”。
每条新路由按自己的scope表复查当前授权；status/prepare replay须有prepare所有read scopes。
submit receipt不含review_document或Rule正文，故submit/replay只要求evidence:confirm与same-key owner；
receipt含该key本人提交的note及确认字段，这是本endpoint显式授予的自有提交回执读取范围。
既有Event GET/list仍要求events:read，不使用这些新scope代替它。

Raw store和授权产品readback允许本人填写的secret-pattern note；真实凭据不应用于测试。
audit/logs/validation以及分享截图/HAR/bundle须在派生副本脱敏并标注位置，不能改源Event或
用masked内容重新计算原文digest。key_id/actor_id等正常元数据按类型保留，不使用泛化redact
把包含key字样的整个对象消掉。产品API授权原文与“证据包没有秘密”是两个独立验证断言。

## 4. submit

请求 exact fields：

| 字段 | 要求 |
| --- | --- |
| `event_id` | 必填新evt_；作为evidence.confirmed command ID，不能等于prepare ID |
| `preparation_id` | 必填evt_，存在且属于当前actor/key |
| `review_sha256` | 必填64hex，等于服务器材料digest |
| `affirmed` | 必填JSON boolean true；false/missing/数字/字符串400 |
| `note` | 必填string，可空；UTF-8≤4096bytes；不trim，不读取其中指令 |

不接受重复target/revision/Rule/host字段；绑定只从immutable preparation取得。
semantic_request_sha256包含这些规范字段、contract版本、认证actor/key、method/path；note bytes进入摘要。
在做expiry/State/fingerprint检查之前，按authenticated owner+command ID查询原submission：
精确匹配200返回首次receipt，仅replayed改true；改内容409；非owner403。重放不重扫、不消费第二次。
认证/role/scopes/ACL检查永远先于重放； revoked key 401。失联不是失败证明，按原ID/原body重试。

新提交依次检查owner、材料完整性/digest、尚未消费、服务器时间在 `[created_at,expires_at)`，
再在writer外完整scan，并与prepare fingerprint SAME；获取writer后重新认证/检查owner，
**再次执行精确command replay/conflict lookup**，然后仅对新command重查消费/时间/DB投影/规则。
第二次lookup发现相同ID同语义200返回首次receipt，改body409，其他Event无相应投影409 event_id_conflict；
只有不同command ID面对已消费prepare才409 confirmation_consumed。此顺序同时用于prepare。
server now<created_at视为时钟回退并503；过期409。DB与review_document不同409；partial/fail scan503；
完整scan不同409 `confirmation_context_conflict`，details.changed按有序标签返回（revision/rules/fingerprint/configuration/identity），
不提供服务器绝对路径或文件内容。不自动更新material，不自动写STALE，不清空人note。

单一事务生成Core分配source evt_，追加：

1. `human.confirmation_submitted` source Event，exact payload字段：`text`（note原字节）、
   `contract_version,preparation_id,review_sha256,statement,affirmed=true,key_id,step_id,task_revision,step_revision,
   fingerprint_sha256,confirmed_rule_id,confirmed_rule_version,request_id,command_event_id,target_status`。
   事件顶层task/project/actor/host来自prepare和认证，session_id=null，source_system保留值，
   顶层source_event_id=preparation_id。
2. `evidence.confirmed` command，payload含`text="",confirmation_source_kind="WEB_STRUCTURED_V1",source_event_id,
   preparation_id,review_sha256,evidence_id,key_id,step_id,task_revision,step_revision,fingerprint_sha256,
   confirmed_rule_id,confirmed_rule_version,request_id,semantic_request_sha256,target_status`，source_system同保留值，
   顶层source_event_id=source Event ID、session_id=null。
3. 一条Evidence：沿用现表字段，kind=USER_CONFIRMATION、status=INFO、result=`{confirmed:true}`，
   tool/command/artifact/producer字段按旧human确认使用null，source_event_id为1，change_event_id为2，
   精确Task/Step revision/fingerprint/Rule/actor/host都来自材料；不需要新增Evidence列。
4. 唯一submission投影与首次response receipt。

以上一步失败全部回滚，既有Truth status/revision/criteria/Rule仍不变；已成功prepare记录保留。
成功返回 exact keys：`{preparation_id,contract_version,review_sha256,source_event,event,evidence,replayed}`。
Event/Evidence形状复用既有授权Raw GET序列化；source_event payload.text返回本人提交的原note，
不会把Authorization/header里的bearer复制到Event或响应；note中人主动填写的原文按§3.1读取。
只在派生证据面脱敏，内部digest验证始终用原文，不用masked text。
command/event/evidence均为首次快照；后续GET Evidence可以重算reference_status，但不得修改首次receipt。

同preparation同ID同body并发201+200；不同ID并发201+409 `confirmation_consumed`，
details含原command/source/evidence ID供owner定位。不能换ID重新执行已消费准备。
每次提交只确认材料，不自动POST VERIFIED/ACCEPTED； UI随后单独发State命令，使用最新Core readback revision。
目标VERIFIED的确认对当次EXECUTED revision有效；转为VERIFIED后目标ACCEPTED须新prepare/submit。

## 5. 恢复与只读status

GET `/v1/evidence/confirmations/{prepare evt_}` 返回 exact keys：
`{preparation,consumption,availability,server_time}`。
preparation为原prepare response去replayed；consumption=null或exact
`{command_event_id,source_event_id,evidence_id,receipt}`（receipt为首次submit response，replayed=false）。
availability为CONSUMED/EXPIRED/OPEN/CLOCK_INVALID；先CONSUMED，再依据server_time推导。
OPEN仅表示尚未消费且未过期，**不代表仍匹配当前State/工作区/可提交**。status不scan、不更新状态。
status在同一DB read transaction读取prepare/submission/相关records并做§7完整性核对，防止混合读回。
不存在prepare404；非owner403。不存在消费只证明该次读时尚未提交，不证明仍在途请求会失败。

UI pending时固定command ID/body，不开放另一个submit。timeout/network/5xx=UNKNOWN_OUTCOME，
先读status或精确重放；读取原command ID可通过原Event GET（events:read）交叉核对。
发现CONSUMED但command ID与草稿不同，显示已由另一命令提交及来源，不宣称自己的命令成功。
刷新后无内存草稿可以read status查看历史，不能自动把历史receipt当fresh，也不能后台重新确认。
任何冲突保留note，要求重新prepare、审阅新材料；不自动用新revision/digest重试。

## 6. 错误与优先级

继承既有401 unauthenticated、403 forbidden_scope/forbidden_actor_kind、404 not_found、
400 invalid_request、409 event_id_conflict/revision_conflict、413 payload_too_large、415 unsupported_media_type、
503 database_busy与500 internal_error。现有错误形状不改，新增code均为拟议值。

| 新code | HTTP | details最低字段 | 意义 |
| --- | --- | --- | --- |
| confirmation_identity_unbound | 409 | reason=home_host_missing | Registry未配置host |
| confirmation_owner_mismatch | 403 | reason=owner_mismatch | 不返回他人的actor/材料 |
| confirmation_context_conflict | 409 | changed:标签数组 | 完整fingerprint/DB/Rule/config/identity与材料不同 |
| confirmation_expired | 409 | expires_at | 未消费已过期 |
| confirmation_consumed | 409 | command_event_id/source_event_id/evidence_id | owner可定位首次结果 |
| confirmation_digest_mismatch | 409 | reason=digest_mismatch | 请求digest不等于存储材料 |
| confirmation_integrity_error | 409 | reason=stored_integrity_invalid | Event/投影/receipt破坏；拒绝而非重建成功记录 |
| confirmation_ineligible | 409 | reason=task_not_active/step_target_state_mismatch/task_steps_ineligible/empty_criteria | 对象当前不适合确认 |
| confirmation_review_too_large | 409 | max_bytes=262144 | 完整材料超cap |
| confirmation_scan_unavailable | 503 | reason=timeout/failed/partial/too_large | 当前scan不可用 |
| confirmation_clock_invalid | 503 | reason=clock_before_creation | 持久时钟回退 |

新命令的确定检查序：route/method→auth→scopes→role→严格body→owner/有效stored records→
exact command replay/conflict→digest→consumption→clock/expiry→new-request对象revision/eligibility→
扫描→writer内复查（key/scopes/owner，完整记录，**再次command replay/conflict**，新command消费/time/DB context）。
prepare同样两次replay，新command才判断revision/eligibility/context；并发冲突以writer内结果为准。
缺引用404；跨Task Step400 invalid_request；校验时fresh revision错误409 revision_conflict，
prepare期望值details={current_task_revision,current_step_revision}（null合法）。
新API本身不返回missing_evidence来表示“确认等于验收”；真正接受仍使用旧State错误。
标准details不得包含note、token、服务器绝对路径。refusal audit decision保留dispatch现规则（409为allow），
UI用HTTP/error理解拒绝，不能把audit decision=allow画成命令成功。

## 7. 拟议持久化与完整性

正式迁移名为 `m0012_web_confirmations`，集成基线 schema 11，候选实现 schema 12。
新增保留Event类型需同步内部/HTTP类型校验，旧m0001…已发布mNNNN全部保持原bytes/checksum。

`web_confirmation_preparations` 列：

* preparation_id TEXT PK FK events(event_id)，actor_id FK actors、key_id FK api_keys、host_id FK hosts，
  project_id FK projects、task_id FK tasks、step_id nullable FK steps。
* expected_task_revision INTEGER CHECK>0，expected_step_revision nullable CHECK>0；成对Step/revision CHECK。
* target_status TEXT CHECK IN ('VERIFIED','ACCEPTED')；task-only仅ACCEPTED的CHECK；
  confirmed_rule_id nullable FK rules、confirmed_rule_version nullable INTEGER CHECK>0；Rule pair CHECK；
  当前版本存在性由store transaction校验。
* fingerprint_sha256 TEXT FK workspace_fingerprints；review_json TEXT、review_sha256 TEXT，
  semantic_request_sha256 TEXT、created_at/expires_at/sampled_at TEXT，response_json TEXT。

`web_confirmation_submissions` 列：

* command_event_id TEXT PK FK events，preparation_id UNIQUE NOT NULL FK preparations，
  source_event_id UNIQUE NOT NULL FK events，evidence_id UNIQUE NOT NULL FK evidence。
* actor_id/key_id/host_id FK、semantic_request_sha256 TEXT、request_id TEXT、response_json TEXT、created_at TEXT。

表所有列除明确nullable外NOT NULL；Digest形状/JSON结构/type由strict store校验；
UPDATE/DELETE触发器拒绝append-only表变更，INSERT OR REPLACE不能绕过（沿用recursive_triggers）。
插入使用非REPLACE语句。全球Event ID唯一；preparation/command/source三者不同。
response_json保存首次完整授权Raw receipt；原note在原source Event和receipt中都是原字节（见§3.1）。
自引用receipt不含投影本身，避免递归。每次read/replay和validator均重算材料digest并核对Event→投影→
Evidence关联，缺失/不一致返回integrity_error或在State validator中missing_evidence，不依据投影裸bool信任。
历史合法prompt确认无本表要求，仅Web来源进入新分支。

### 7.1 共用原始字节完整性算法

新prepare/source/command Event构造固定 `hash_payload=None,client_occurred_at=None`；
内部不能借旧store的hash_payload排除text/request_id或材料字段。其body_sha256复算对象为：
`{event_type,source_system,source_event_id,occurred_at:null,actor_id,host_id,session_id,project_id,task_id,payload}`，
其余值为原stored record；payload是全部原JSON。算法复用严格校验后canonical+SHA256，
与NewEvent.hash_fields一致；seq/recorded_at与实际server occurred_at不进入body hash，
顺序/时间需另核对事件链/准备期限，actor_kind也另核对human。不得误用stored occurred_at替换null。

重建submit semantic摘要的exact对象为：
`{contract_version:"web_confirmation_v1",actor_id,key_id,method:"POST",path:"/v1/evidence/confirmations/submit",
body:{event_id:command_event_id,preparation_id,review_sha256,affirmed:true,note:source.payload.text}}`。
该摘要须同时等于command.payload.semantic_request_sha256和submission.semantic_request_sha256。
prepare摘要同样对象，method/path为prepare，body为§3所有规范字段（含event_id/target_status，optional统一null）；
从immutable prepare材料/投影重建，须等于prepare payload和preparation投影的摘要。
statement/target/revisions/host/Rule/fingerprint按材料与source/command/Evidence逐字段核对，
source.request_id等于command/submission原request_id；这是source text→command digest的关联，
不是只验证review_sha256。首次receipt从已验证prepare/source/command/Evidence原始快照重建，
exact shape序列化后与response_json相同；submit的replayed始终false，运行时replay仅改为true。
Evidence首次reference_status固定NONE（无artifact），不能用后来重算引用状态代替首次快照。

同一完整性函数供status、prepare/submit replay和CurrentEvidenceValidator的Web分支使用；
API不一致409 confirmation_integrity_error，State接受不一致422 missing_evidence。
改raw text但不改其他字段必须被event body和submit semantic两个检查发现，
即便两段原note在派生证据里会mask成相同内容也必须识别。合法secret-pattern原note不因mask而失效。

## 8. P6消费与最小代理改动

`P6_ENABLE_WEB_CONFIRMATIONS=1` 为两个host共用显式opt-in，默认false。
允许精确GET auth/me、GET confirmations/evt_、POST confirmations/prepare与submit。
两个POST仅在开关打开时允许；path ID严格evt_，不采用`[a-z]{3}`通配来误匹配prepare/submit。
保持旧confirm、POST events、workspaces/fingerprint、tool-results、auth/keys、audit拒绝。
auth/me是唯一新增可代理auth子路径；不能开`/v1/auth/*`。

UI流程：读me/capability与当前对象→prepare→显示完整对象/criteria/适用规则/指纹coverage/身份/期限与固定声明→
人主动选中未预勾选affirmation并可写note→submit→来源/command/Evidence逐个GET readback→人另行提交目标State。
显示“确认已记录 / INFO；VERIFIED/ACCEPTED另行由Core判定”，材料不是自动Evidence充分性矩阵。
不能只展示digest让人盲签；完整规则允许滚动浏览，长内容不得静默删节。
跨项目/对象/key切换丢弃旧准备和affirmation，保留人note为草稿须明示对象；已有pending outcome单独保留恢复入口。
同源host继续只绑定loopback，凭据只在内存，JSON+bearer，无cookie认证，无跨源CORS授权；
新proxy写入口对非同源Origin明确拒绝，不转发cookies、token查询或任意header；没有浏览器Origin的直接Core
CLI调用仍可使用bearer，不能从此推断物理点击。代理未知body不作身份解释，Core做最终验证。

item5 matrix新增展示可信Web source与精确revision字段；candidate仍不等于Core verdict。
CR-1…CR-5（权威矩阵/指纹读取/producer/来源检查/状态边集）与CQ-1不在本项实现；
如检查结果要求扩展它们，先提出单独契约，不能藉item6默默扩张范围。
