# Context budget：完整字段关系修复契约

本轮修复是 R02/F03、R03/F04 的原有遗漏及同类边界，不能归因为新产品问题。原9场景、36必需门、预算和声明边界不变；本轮不修改 Core、main、部署、旧证据或 item6=0。

## A. 审计响应 → 查询窗口 → 被消费动作

| 层 | 必须核对的完整关系 |
| --- | --- |
| 读取请求 | HTTP收据属于 calls；固定 phase；GET、200、空请求体；/v1/audit；limit=1000；动作独立读取仅含原动作 actor_id 筛选；管理员真实 key/actor/scopes、物理自审计、observer窗口 |
| 实际响应 | 从 after-audit.db 重建读请求自身 seq 之前的审计行，应用同一 actor 筛选、seq升序、1000上限；detail_json解码；完整 entries 与 count 必须一致，不仅校验 detached matching |
| 消费动作 | matching 必须恰为上述实际 entries 中所有目标 request_id 的选择；无重复、无遗漏；每条与物理审计一致，核对 actor/method/path/status/error；observer动作结束不得晚于读取开始 |
| 浏览器全范围 | 同场景 key 绑定全部实际 Core 请求，包括 bootstrap、refresh、跨路由旧请求；仅 /v1/health 与 /v1/meta/schema 为生产 public 路由，actor为null；代理拒绝无Core窗口、不得补造Core审计 |
| 历史正控制 | 不要求读回包含其自身审计，也不把正确早期历史读取改为最新读取；真实 query/window 重建后支持历史查询 |

## B. 请求 → 来源 → 结果行/响应 → 命令 → 请求键

两种创建都用一个完整创建关系校验器；Checkpoint/Context分别由生产函数恢复请求键摘要和 NewEvent 摘要。

| 层 | 字段/关系 |
| --- | --- |
| 注册身份与来源 | key→actor(kind/home_host)；source_for_task 校验请求 task/host/session/current_step/source、原始Task与来源；Checkpoint note source独立检查；trigger provenance 由生产函数重新推导 |
| 结果 | response与真实表行/解码JSON一致；actor/host/project/task/session来自请求/注册/前态Truth；source/reason、Checkpoint note/projection/revision/workspace digest；Context selector/comparison/render/config摘要等保留既有生产重算 |
| 命令全部16列 | event_id合法且与结果command_event_id相连；seq为前态max+1；schema_version为生产版本；event_type与source_system为相应生产创建值；source_event_id为null；occurred_at/recorded_at在HTTP窗口内且created≤occurred≤recorded；actor_id/actor_kind/host_id/session_id/project_id/task_id与原身份一致；payload_json等于生产预期；body_sha256等于生产NewEvent.digest |
| 键全部4列 | 原键不可变；恰增1行；actor_id等于注册actor；idempotency_key等于真实请求；request_hash为Checkpoint sha256({body,task_id})或Context sha256(body)；checkpoint_id/context_pack_id等于真实结果 |
| 对照 | Context、Checkpoint两路均检验身份/来源/时间/摘要/序列/键所有字段，不允许仅修一个字段；正确历史读取、回放、真实直接否定和历史原文继续通过 |

## 门禁与产物

新增关系级单元测试与封存副本反证覆盖以上两组。正式CLI必须拒绝坏关系，不以 seal 不匹配作为关系校验成功。冻结新候选后重新归档运行9场景并执行旧反证、新关系反证和正控制；非作者统一源码复评与全新隔离复验。所有失败尝试保留。

限定结论仅覆盖Context超预算切片；失败构建看板回执仍UNAVAILABLE_EXISTING_CONTRACT；不声明实际Agent注入、P6/V1整体通过或日常使用放行。
