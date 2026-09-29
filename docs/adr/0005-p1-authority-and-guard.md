# ADR 0005 — P1 Authority 与 Guard 契约

- 状态：Accepted for P1-01 implementation
- 日期：2026-09-29
- 依赖：ADR 0001–0004；P1 阶段任务书

## Rule 身份、版本与生命周期

`rul_` 是 Core 分配的稳定 Rule ID，格式沿用 ADR 0001 的非 Event ID。`rule_key` 是作用域内唯一、可读的业务键，格式为小写字母开头，后接小写字母、数字、下划线、点或短横线，最多 128 字符。相同 `rule_key` 可分别存在于 global、不同 project、不同 task；同一作用域不能并发创建第二个同名 Rule。

`rules` 是当前投影，保存 `current_version/status/revision`；`rule_versions` 是不可改的内容版本；`rule_changes` 是不可改的生命周期索引。版本内容包括 `kind`（RULE/DECISION/ACCEPTANCE）、`severity`（HARD/NORMAL）、`enforcement`（CONTEXT/DENY/CONFIRM/VERIFY）、content、matcher、`origin_event_id`、命令 `change_event_id`。SQL 触发器拒绝更新、删除和替换版本或变更索引。每次新 Rule 以 version=1、status=PROPOSED、revision=1 建立；approve 改状态为 ACTIVE、revision+1；同作用域 supersede 追加 version+1 并激活、revision+1；retire 改状态为 RETIRED、revision+1。每次操作先追加自己的不可变命令 Event，再更新投影，均在单个 `BEGIN IMMEDIATE` 事务内。历史 API 按 revision 返回每个变更时的快照。

proposal 是创建，不接受 `expected_revision`；approve/supersede/retire 是更新，必须有严格正整数 `expected_revision`（bool 无效）。过期值返回 `409 revision_conflict` 和当前 Rule 快照。客户端可给合法 `evt_` `event_id` 幂等重试：相同命令返回首次 Event 与首次快照，`replayed=true`；不同命令复用 ID 返回 `409 event_id_conflict`。P1-01 不支持定时到期；规则持续 ACTIVE，直到明确 supersede/retire。客户端不能靠本地时钟自行使 HARD Rule 失效。

## 来源、权限与作用域

`origin_event_id` 引用已存在 Raw Event，必须与 Rule 的 global/project/task 上下文一致。它是内容来源，不是本次命令的幂等 ID。Agent 来源可形成 PROPOSED；不能因为 agent 文本或 `source_system` 自称 USER 而激活。批准/替代/退休命令 Event 记录经 token 鉴权的 actor、从 Registry 查得的 actor kind 与 host。`authority:manage` 且 actor.kind 为 human/system 才可执行；agent 即使持有 `admin` 或误配管理 scope 也不能执行。普通 API key 签发只允许给当前 actor 签发；agent 不能签 `authority:manage`。`system` 仅代表受信任 Core/operator 身份，不能通过普通采集数据冒充。

scope 只支持 global/project/task；task 必须属于指定 project。Guard 加载当前请求 task 的 global、project、task ACTIVE Rule；缺少 task/project 身份而可能漏掉 scoped HARD Rule 时返回 confirm。HARD 祖先 Rule 不因子 scope 规则而消失。P1-01 拒绝跨 scope supersede；同 scope 版本替换仍须明确管理命令。Rule provenance 分别显示内容来源 Event 的 actor kind、类型和最终状态变更 Event 的 actor kind，不把 agent 内容伪写为用户原话。

## 确定性 Guard

matcher 仅支持非空 exact `tool`、exact `action`、canonical absolute POSIX `path_prefix`，各维度取 AND；路径只在自身或 `/` 边界下的子路径命中。拒绝未知 matcher 字段、相对/非规范路径。DENY/CONFIRM 必须是 kind=RULE，且 matcher 非空。语义文本不会参与程序匹配；不能用语义文本伪装成 DENY。所有适用规则都参与，优先级 DENY > CONFIRM > VERIFY > CONTEXT；缺少判定所需信息时返回 confirm。返回决策 `allow/deny/confirm/verify`、原因、命中 Rule ID/version、来源 Event ID、请求 SHA-256 和 `capability=ADVISORY`。CONFIRM/VERIFY 都不等于已批准或已验证。

`POST /v1/guard/check` 的 HTTP 200 仅表示成功计算。审计使用响应中的实际 Guard 决策：deny/confirm/verify 记为 deny，并保存安全的 Rule ID、version、来源 Event ID、请求摘要；不保存原始工具参数或路径。P1-03 的真实 PreToolUse hook 负责原生阻断验证；本 API 在其通过真实 Gate 前不宣称 ENFORCED。对工具参数含 symlink、编码或复合 shell 命令的情形，调用端必须解析成确定性 tool/action/path；未解析不能把 ALLOW 当成安全批准。

## API 与权限

| 路径 | 权限 |
| --- | --- |
| `GET /v1/rules/active?project_id=&task_id=`、`GET /v1/rules/{id}`、`GET /v1/rules/{id}/history` | `authority:read` |
| `POST /v1/rules/proposals` | `authority:propose` |
| `POST /v1/rules/{id}/approve`、`/supersede`、`/retire` | `authority:manage` 且 human/system actor |
| `POST /v1/guard/check` | `guard:check` |

P1-01 不实现自然语言 Interpreter/Resolver，也不自动从用户 prompt 激活 Rule；结构化 API 预置用于本阶段真实对话 Gate。
