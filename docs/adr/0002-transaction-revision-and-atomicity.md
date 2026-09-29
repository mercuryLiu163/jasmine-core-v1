# ADR 0002 — 事务、revision 与 Event / projection 原子性

- 状态：Accepted（P0-01 冻结）
- 日期：2026-09-29
- 影响范围：`jasmine_core.db`、`jasmine_core.events`、`jasmine_core.objects`

## 1 背景

设计书要求 Raw Event 为真源，且任何派生对象都必须能追溯到产生它的原始事件。任务书同时要求：注入中途失败时不得留下"半笔" Event 或 projection。二者共同要求 Event 与 projection 在**同一事务**内提交。

## 2 决定

### 2.1 连接与并发

- `core.db` 为 SQLite，连接初始化固定执行：
  - `PRAGMA journal_mode=WAL` —— 读写不互相阻塞，满足"多设备/多客户端"方向的并发读需求。
  - `PRAGMA foreign_keys=ON` —— 引用完整性由数据库强制。
  - `PRAGMA synchronous=FULL` —— P0 每次提交都 fsync。WAL + NORMAL 更快但崩溃时可能丢最近提交；P0 优先可恢复性。性能调优属 P7。
  - `PRAGMA busy_timeout=5000` —— 写锁竞争时等待而非立刻报错。
- 连接使用 `isolation_level=None`（autocommit），事务由 `jasmine_core.db.transaction()` 显式控制：

```python
with transaction(conn):          # BEGIN IMMEDIATE
    store.append_event(...)      # 1. 先写 Event
    objects.insert_projection()  # 2. 后写 projection
# 任一步异常 -> ROLLBACK，Event 与 projection 一起消失
```

- `BEGIN IMMEDIATE` 在事务开始即取写锁，避免"读完再升级写锁"造成的 `SQLITE_BUSY` 中途失败。
- **顺序固定为 Event 先、projection 后。** projection 的 `source_event_id` 外键指向 Event，因此顺序是 schema 强制的，不是约定。

### 2.2 Event 与 projection 的原子性

一次对象创建 = 一个事务 = 两条 INSERT（`events` + `projects|tasks|sessions`）。
- 事务失败 → 两者都不存在，调用方看到 5xx/错误码，**不产生"虚假成功"**。
- 事务成功后 → 对象 `revision = 1`，`source_event_id` 指向唯一 Event，Event 的 `body_sha256` 覆盖请求语义内容。

P0 注入失败点（用于 P0-T05）：Event 写入后、projection 写入前抛错。测试必须证明 `events` 与 `objects` 两张表计数都不变。

### 2.3 `revision` 与 `expected_revision`

- `tasks.revision`（以及 `projects.revision`、`sessions.revision`）是从 `1` 开始的整数，**每次成功的真源变更 +1**。
- 变更请求必须带 `expected_revision`，表示客户端读到的版本。Core 在事务内 `UPDATE ... WHERE revision = :expected`。
  - 命中 0 行 → `409 revision_conflict`，返回当前 `revision` 与当前对象快照。
  - 命中 1 行 → 新 `revision = expected + 1`。
- `expected_revision` 缺失 → `400 missing_expected_revision`（对已存在对象的更新）。创建请求不接受 `expected_revision`；若创建时传了 → `400 unexpected_expected_revision`。
- **P0 只创建与查询，不实现状态迁移**，因此 P0 不开放任何更新端点。`revision` 列与上述规则在此 ADR 冻结并由 P0 的测试覆盖（直接调用存储层做一次乐观锁更新，断言 409 语义），P1 的 State Agent 依此实现而不重新设计。

### 2.4 没有 Task 的 Raw Event 如何追加

- Raw Event 的 `task_id` / `project_id` / `session_id` **允许为 NULL**。
- `POST /v1/events` 不要求存在 task；调用方原样提供它已知的 `source_system` / `source_event_id` / `host_id` / `actor_id`，Core 不猜测归属，也不按 cwd 或目录名自动绑定 project。
- 若请求显式给出 `task_id` 而该 task 不存在 → `404 task_not_found`（显式错误优于静默丢弃）。
- 这保证"任何真实输入先落 Raw Event"不因缺少 Truth 上下文而丢失；后置归属（P2 Interpreter/Resolver）可依据 `session_id` 回填，`events` 表本身不被改写。

### 2.5 迁移中的 Event

迁移永不写 `events`。`schema_migrations` 与 `core_meta` 也不是 append-only 真源，它们是可重放的部署元数据，备份/保留策略见 P7。

## 3 后果

- "事件先落、对象后落、同一事务"成为不可绕过的实现约束。
- `synchronous=FULL` 让每次写入付出 fsync 成本；P0 接受该成本以获得可验证的重启恢复。
- Raw Event 可以先于 Truth 存在，这是设计意图而非漏洞；它保证了输入链完整。
