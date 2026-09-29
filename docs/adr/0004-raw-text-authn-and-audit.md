# ADR 0004 — 原文保留、敏感信息与鉴权审计边界

- 状态：Accepted（P0-01 冻结）
- 日期：2026-09-29
- 影响范围：Event Store、审计、Codex 采集入口、Validation 证据包

## 1 决定

### 1.1 原文保留（Event 是原文真源）

- `events.payload.text` 保存来源原文，**逐字节原样保存**：不 trim、不改大小写、不做 Unicode 规范化、不做敏感词掩码。理由：Event 是真源，任何改写都会让后续解释、审计、争议复核失真。
- 脱敏发生在**派生面**（日志、审计、Validation 证据包），不发生在 Event 本身。
- 空的 `text`（`""`）允许存在，事件类型与 `payload` 决定其含义；`text` 字段缺失则 `400`。
- Event 上保存 `body_sha256`，覆盖 ADR 0003 §1.3 的语义字段，用于幂等重放判定与事后篡改比对。

### 1.2 审计里绝不出现的字段

`audit_log` 只存**元数据 + 长度/哈希**，不存原文：

| 存 | 不存 |
| --- | --- |
| `audit_id`、`at`、`request_id`、`actor_id`、`method`、`path`、`decision`、`status_code`、`scope`、`target_id` | 请求体原文、prompt 文本、文件内容 |
| `text_sha256`、`text_len`（可选，由调用方显式提供时） | 任何 `Authorization` 头的值 |
| `error_code` | 异常堆栈 |

- 审计写入**前**对所有字符串字段做一次凭据掩码兜底（`Bearer <...>`、`sk-...`、`api_key=...` 等模式），即使调用方误传也不落盘。
- 服务端日志（`stderr`）用同样规则；异常只记类型与 `str(exc)[:200]`，不记 traceback 到文件（traceback 只在 `--dev-trace` 显式开启时写本地不入库的调试文件）。
- 数据库文件、API key 明文、日志、Validation 证据包都受 `.gitignore` 保护，**不得提交**（README 已声明）。

### 1.3 身份与鉴权

- 身份实体：`actors`（人或 Agent）、`hosts`（设备）、`api_keys`（凭证绑定 actor + scope 集合）。
- P0 认证方式：`Authorization: Bearer <token>`。token 为 `secrets.token_urlsafe(32)`（256 位），库里**只存 SHA-256**，明文仅在 `POST /v1/auth/keys` 的响应里返回一次。
- 每次请求的 `actor_id` / `host_id` 来自 token 绑定的 actor 与请求的 `X-Jasmine-Host`（P0 采集入口在同机，通过 `host_id` 请求体字段显式声明并记入 Event）。请求体里的 `actor_id` **不作为身份来源**，只作为 Event 的记录字段；若与 token 绑定的 actor 不一致 → `403 actor_mismatch`。
- scope 粒度：`admin`、`objects:read`、`objects:write`、`events:read`、`events:write`。缺 scope → `403 forbidden_scope`，且不写任何 Truth。
- 吊销：`api_keys.revoked_at` 非空即拒绝；P0 不提供删除端点。
- **P0 无网络边界防护**：服务默认只绑 `127.0.0.1`，不对外暴露。对外暴露、加 TLS、多用户配额属 P5。

### 1.4 Evidence 与 Validation 证据包（不得含敏感数据）

- `evidence` 的结构（`evidence_id`、`event_id`、`workspace_fingerprint`、`kind`、`payload`）在 P0 只冻结 ID 与外键约定，**不建表、不实现**：P1 的 Evidence Agent 才落地。P0 不允许把"证据"写成测试日志或 README 里的 PASS 声明。
- P0 的 Validation Run 是**验收记录**，不是产品 Evidence。其内容遵循任务书：保留必要原文、请求/响应、命令与输出、版本、失败分类。
- 硬性禁止写进任何 Validation 记录或证据包：API key / token / 私钥、`auth.json` 内容、用户私人对话全文、V0 state 目录文件、数据库文件本身。P0-T07 的测试消息必须是**固定的无敏感短句**，执行前由执行者确认。

## 2 后果

- "原文完整保存"与"日志脱敏"是两条独立的规则，实现上以不同函数实现（`payload` 落库原样 vs `redact()` 用于日志/审计），避免互相污染。
- 客户端不传 `host_id` 会被拒（`400 missing_host_id`）而不是被 Core 猜测，防止把不同设备的对话混为一谈。
