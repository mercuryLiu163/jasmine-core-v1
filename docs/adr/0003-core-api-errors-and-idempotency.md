# ADR 0003 — Core API、错误语义与幂等

- 状态：Accepted（P0-01 冻结）
- 日期：2026-09-29
- 影响范围：HTTP 表面、客户端（Dashboard、Codex 采集入口、Validation Recorder）

## 1 决定

### 1.1 传输与版本

- 基址 `http://127.0.0.1:8787`，路径带版本段 `/v1/...`。
- P0 使用标准库 `http.server.ThreadingHTTPServer`，**零第三方运行时依赖**。路由层与 handler 逻辑与传输解耦（`jasmine_core.api.handlers` 为纯函数式：`(Principal, Request) -> Response`），后续可在不改动契约的前提下换成别的服务器实现。
- 请求与响应均为 `application/json; charset=utf-8`。原文按 UTF-8 原样存取，不做大小写折叠或 Unicode 规范化改写。
- 每个响应带 `X-Request-Id`（客户端可传 `X-Request-Id`，否则服务端生成 ULID），审计与日志以此关联。

### 1.2 端点（P0 范围）

| 方法 | 路径 | 需要的 scope | 说明 |
| --- | --- | --- | --- |
| GET | `/v1/health` | 无 | 进程存活、schema 版本、commit/版本 |
| GET | `/v1/meta/schema` | 无 | 当前 `schema_version` 与已应用迁移列表 |
| GET | `/v1/hosts` | `objects:read` | 已注册的采集设备 |
| GET | `/v1/actors` | `objects:read` | 已注册身份 |
| POST | `/v1/auth/keys` | `admin` | 签发 API key（明文只返回一次） |
| GET | `/v1/auth/keys` | `admin` | 仅元数据，永不返回 token |
| POST | `/v1/auth/keys/{key_id}/revoke` | `admin` | 吊销，幂等 |
| GET | `/v1/audit` | `admin` | 仅元数据，无 prompt 原文、无 token |
| POST | `/v1/projects` | `objects:write` | 创建 project，同事务写源 Event |
| GET | `/v1/projects` `/v1/projects/{id}` | `objects:read` | 列表 / 单个 |
| POST | `/v1/tasks` | `objects:write` | 创建 task（需已存在的 `project_id`） |
| GET | `/v1/tasks` `/v1/tasks/{id}` | `objects:read` | 列表（可按 `project_id` 过滤）/ 单个 |
| POST | `/v1/sessions` | `objects:write` | 创建 session（`task_id` 可选） |
| GET | `/v1/sessions` `/v1/sessions/{id}` | `objects:read` | 列表 / 单个 |
| POST | `/v1/events` | `events:write` | 追加 Raw Event（不创建对象） |
| GET | `/v1/events` `/v1/events/{id}` | `events:read` | 按 `session_id` / `task_id` / `project_id` / `event_type` 过滤，按 `seq` 升序 |
| POST | `/v1/auth/keys` | `admin` | 签发 API key（明文只返回一次） |

**P0 不提供**任何 `PUT/PATCH/DELETE`。没有状态机、没有删除端点；`Event` 不可改这一点同时由 API 表面（无写方法）和数据库触发器（见 §1.5）保证。

### 1.3 幂等与冲突（冻结语义）

**幂等键 = `event_id`。** 客户端需要幂等重试时自带 `event_id`；不需要时省略，由服务端生成。
客户端可用来源身份确定性生成合法 `evt_` ID；`evt_` 正文不透明，不承诺编码时间或字典序。查询 Event 的持久化顺序由 Core 的 `seq` 定义（见 ADR 0001）。

1. `event_id` 省略 → 正常创建，`201`。
2. `event_id` 已存在且**语义内容相同** → `200`，返回**首次写入时持久化的那个对象**，`replayed: true`。不新增任何行。
3. `event_id` 已存在但**语义内容不同** → `409 event_id_conflict`，返回 `409` 与 `{existing_event_id, existing_body_sha256, request_body_sha256}`。**不修改任何已有行。**

"语义内容相同" = 对下列字段做规范化 JSON 序列化（键排序、无多余空白、UTF-8）后取 SHA-256 相同。参与哈希的字段：

- Event：`event_type`、`payload`、`actor_id`、`host_id`、`source_system`、`source_event_id`、`session_id`、`project_id`、`task_id`、`occurred_at`
- 对象创建：以上 Event 字段 + `name`/`title`/`description` 等对象字段

**不参与哈希：** `event_id` 自身、`recorded_at`（服务端生成）、`request_id`，以及**创建类请求中由服务端分配的 `object_id`**（重放时以已存 Event 中的 `object_id` 为准）。

**`actor_id` / `host_id` 参与哈希。** 这与鉴权身份是两件事：token 的持有者可以从不同的 key 重放同一个 `event_id`（key 本身不在哈希里），但如果请求正文声明的 `actor_id` 或 `host_id` 与已存 Event 不同，那是**不同的写入内容**，返回 `409 event_id_conflict`，而不是重放。

**`occurred_at` 仅在客户端显式声明时参与哈希。** 对象创建未显式声明时由服务端补录受理时刻，若纳入哈希，任何重试都会被判为"内容不同"而永远无法命中重放。时间戳先归一化为 UTC 再计算哈希，因此 `Z`、`+00:00`、等价偏移量与不同小数精度表示同一时刻时都能识别为重放。

### 1.3.1 错误码补充

- `403 actor_mismatch`：正文声明的 `actor_id` 与 token 绑定的 actor 不一致。
- `404 host_not_found` / `404 actor_not_found`：请求引用的 host 或 actor 尚未注册。Core 不会猜测或自动注册。
- `400 invalid_request`：payload 含 `NaN` / `Infinity`（Python 的 JSON 扩展，`json_valid` 检查会拒绝），以保证错误是 400 而不是 COMMIT 时的原始 `IntegrityError`。

**`source_event_id` 幂等：** `events` 上有唯一索引 `(source_system, source_event_id)`（`source_event_id` 非空时）。同一来源系统重复上报同一 `source_event_id` → `409 source_event_duplicate`，无论 `event_id` 是否相同。这防止采集端重试把一条用户消息写成两条 Event。

### 1.4 错误响应

统一形状：

```json
{"error": {"code": "task_not_found", "message": "task tsk_... not found",
           "request_id": "01K5...", "details": {}}}
```

| HTTP | `code` | 触发条件 |
| --- | --- | --- |
| 400 | `invalid_request` | JSON 解析失败、字段类型/取值非法、未知字段 |
| 400 | `missing_expected_revision` | 更新类请求缺 `expected_revision`（P0 无更新端点，保留契约） |
| 400 | `invalid_request` | 未知字段（`POST /v1/auth/keys` 也拒绝）、非法查询参数（`limit`/`offset`/`after_seq`）|
| 401 | `unauthenticated` | 缺失/非法/吊销的 `Authorization: Bearer` |
| 403 | `forbidden_scope` | scope 不足 |
| 404 | `project_not_found` / `task_not_found` / `session_not_found` / `event_not_found` | 引用或查询目标不存在 |
| 404 | `key_not_found` | 吊销一个不存在的 key |
| 404 | `host_not_found` / `actor_not_found` | 请求引用的 host 或 actor 未注册 |
| 409 | `event_id_conflict` | 同 `event_id` 不同内容 |
| 409 | `source_event_duplicate` | 同 `(source_system, source_event_id)` 重复 |
| 409 | `revision_conflict` | `expected_revision` 不匹配（P1 起生效） |
| 413 | `payload_too_large` | 请求体超过 1 MiB |
| 415 | `unsupported_media_type` | 非 JSON |
| 500 | `internal_error` | 未预期异常；`details` 不含堆栈或原文 |
| 503 | `database_busy` | 写锁竞争超过 `busy_timeout`；未写入任何 Truth |
| 503 | `schema_version_unsupported` | DB 版本高于/低于代码期望且无法迁移，或检测到迁移漂移 |
| 503 | `migration_conflict` | 迁移无法由本进程推进（例如被其他进程占用） |

**鉴权与校验失败绝不写入任何表**（`events`、对象表、`api_keys`）。`last_used_at` 只在 scope 校验通过之后才记录：被拒绝的调用方不得留下任何痕迹以外的东西。失败**一定写入审计**（`audit_log`），在独立事务里落盘——审计失败不能反过来阻断业务事务，两者分开提交。

**审计不保存调用方输入里的凭据。** `path`、`X-Request-Id` 与 `details` 都可能被调用方塞进 token，因此三者都过脱敏（同时匹配 `Bearer%20`、`sk-`、`ghp_`、JWT、PEM 等形态），`X-Request-Id` 若像凭据则换成 Core 生成的值。审计字段另有长度上限，`detail_json` 不随请求体线性增长。

### 1.5 Event 不可变

`events` 与 `audit_log` 上建 `BEFORE UPDATE` / `BEFORE DELETE` 触发器，`RAISE(ABORT, 'events are append-only')`；`events` 另有 `BEFORE INSERT ... WHEN EXISTS` 触发器拒绝复用 `event_id`。只建 UPDATE/DELETE 触发器是不够的：SQLite 在 `recursive_triggers` 关闭时（默认值）不会为 `INSERT OR REPLACE` 触发删除触发器，因此该 INSERT 触发器与 `PRAGMA recursive_triggers=ON` 缺一不可。绕过 API 直接 `UPDATE`/`DELETE`/`REPLACE` 会被数据库拒绝（见 P0-T05）。P0 无删除端点；保留/归档属 P7，届时须先提变更 PR 定义导出流程，不能就地删除真源。

## 2 后果

- 幂等语义完全由 `event_id` + `body_sha256` 决定，客户端无需额外幂等键表。
- 零依赖 HTTP 层在并发/长连接上能力有限（P0 是本机单用户服务）；换框架时契约不变。
