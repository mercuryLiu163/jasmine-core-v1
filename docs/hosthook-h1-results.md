# Host Hook Protocol H1 结果（2026-10-12）

范围：任务书 H1（ADR 0017、公开规范、Core 端点、hook 运行时、Codex 方言、测试）。**不含** Claude Code 方言、安装命令、真实对话 Gate（H2/H3）。
本文件只陈述 H1 做了什么、在哪一层测过什么。**没有任何 Agent 原生效果被验证**：没有运行过 `codex`、`claude` 或任何真实模型对话。

## 1. 已实现

| 项 | 位置 |
| --- | --- |
| ADR 0017（先于代码写成） | `docs/adr/0017-host-hook-protocol.md` |
| 公开协议规范 + 3 个 JSON Schema（请求、响应、能力声明） | `spec/host-hook-protocol-v1.md`、`spec/schemas/` |
| Core 端点 `POST /v1/host-hooks/events`，scope `hosthook:report` | `src/jasmine_core/hosthook/{api,service,protocol}.py`、`api/handlers.py`（仅追加路由）、`auth.py`（新 scope） |
| forward-only migration（`host_hook_deliveries`，追加只写，schema 12 -> 13） | `src/jasmine_core/migrations/m0013_host_hooks.py` |
| 新错误码 `host_hook_protocol_unsupported`(400)、`host_hook_delivery_conflict`(409)；新 Raw Event 类型 `host.turn_stopped`、`host.compaction_reported` | `errors.py`、`models.py` |
| `evidence.record_tool_result` 的可选参数（`source_system`、`payload_extra`、`native_session_id` 别名）；默认行为不变 | `evidence.py` |
| 审计：`tool.before` 响应按映射后的决策写审计（不含工具参数） | `api/dispatch.py` |
| 适配器侧解析（shell 命令 -> action/路径；含 symlink 的 realpath） | `hosthook/resolve.py` |
| 绑定文件加载、私有密钥文件检查 | `hosthook/binding.py` |
| 私有 spool（0700/0600，原子写）、重放、gap 记录 | `hosthook/spool.py`、`hosthook/runtime.py` |
| CLI：`jasmine-core hook --agent <名字>`、`jasmine-core hook replay` | `cli.py`、`hosthook/runtime.py` |
| 方言注册表（按模块发现，不含 Agent 名字）与 Codex 方言、能力声明文件 | `hosthook/dialects/` |
| Codex 方言文档（参考的文档版本/日期、每事件输入字段、拒绝/注入输出格式） | `docs/hosthook-codex-dialect.md` |
| `docs/api/core-api-v1.md` 新增一节 | 同文件 |

## 2. 测试：层级与结果

测试命令（用 3.13 venv 解释器，系统 `python3` 为 3.9，低于仓库要求的 3.11）：

```
PYTHON=<venv python3.13> ./scripts/run-tests.sh
```

结果：`Ran 1658 tests ... FAILED (failures=1, skipped=13)`。唯一失败是 `p6.test_p4_contract_snapshot...test_every_cited_web_case_exists_exactly`，
**在本分支改动之前的基线上同样失败**（基线 1575 个测试、同一个失败、13 个跳过）；与本任务无关，未处理。H1 新增 83 个测试，均通过。
所以“`run-tests.sh` 全量通过”这一条**没有**达到：存在 1 个既有失败，原因未在本任务中调查。

| 层 | 文件 | 覆盖 |
| --- | --- | --- |
| 组件（Core，真实 HTTP + scratch DB） | `tests/test_hosthook_core.py` | 七个中性事件；原话逐字（含首尾空白与 Unicode）；幂等重放与 409；无绑定降级；引用不存在/不一致；Guard 映射（deny/ask/allow/verify、未解析动作、多路径取最严）；`tool.after` 退出码规则 PASS/FAIL/INFO 与 trust 落在 Evidence 的来源 Event；human 不能写工具结果；工作区未配置时降级；Checkpoint（PRE_COMPACT、SESSION_STOP）；回执表追加只写；Core 代码无 Agent 名字；agent 密钥拿不到该 scope |
| 组件（运行时） | `tests/test_hosthook_runtime.py` | 绑定校验；无绑定空输出；Core 不可达时 spool（权限位）、`tool.before` 默认放行并留 gap、`on_unavailable: deny`；重放顺序、幂等、Core 侧去重；成功投递后顺带清空 spool；4xx 不进 spool；密钥文件权限过松被拒 |
| 方言 | `tests/test_hosthook_dialect_codex.py` + `fixtures/hosthook/codex/` | 方言双向翻译；delivery_id 确定性；工具类别；退出码映射；ask 降级为 deny；注入与 Stop 输出形状；解析器；运行时构造的请求可通过协议校验 |
| 规范一致性 | `tests/test_hosthook_schemas.py` | JSON Schema 与实现的枚举/字段一致；运行时请求与 Core 响应符合 Schema（含用 stdlib 写的小型校验器，负例） |
| 本地集成 | `tests/test_hosthook_integration.py` | 真实 `python -m jasmine_core.cli hook` 子进程 + 真实 HTTP Core（线程内，可注入假分词器）覆盖五项目标的 Core 侧记录与 stdout 形状；`hook replay`；另有一个**独立进程**的 `jasmine-core serve` 在临时目录 scratch DB 上的端到端用例（无分词器，Context 按预期降级） |

方言 fixture 全部“依据文档构造”，见 `fixtures/hosthook/codex/README.md`，不是从真实 Codex 会话采集。
已有测试因新增 migration 而必须同步的修改（只改版本/迁移名单，不改断言意图）：`test_api.py`、`test_cli.py`、`test_memory_schema.py`、`test_migrations.py`、`test_state.py`、`test_web_confirmations_integrity.py`、`test_web_confirmations_rules_schema.py`。

延迟：单次测量 `tool.before` 的 hook 进程（含 Python 启动）约 64 ms，**只是一次测量，不是 p95**，且是在本机、未负载、loopback 上；300 ms p95 目标未验证。

## 3. 未验证（全部原生效果都属于这一类）

- Codex 是否真的按文档调用这些 hook、是否传文档里的字段、`hooks.json` 如何安装与信任（`/hooks`）。
- `deny` 之后工具是否**确实没有执行**；`additionalContext` 是否**确实到达模型**；`PreCompact`/`Stop` 的实际触发。
- `tool_response` 的真实形状（文档示例用 `returncode`；方言读 `exit_code`/`exitCode`/`returncode` 整数，读不到则 Core 记 `INFO`，不会记 `PASS`）。
- `apply_patch` 的 `tool_input.command` 补丁文本格式（方言按 `*** Add/Update/Delete File:` 解析，依据文档推断）。
- Context Pack 的实际注入内容：本环境没有固定版本的 tiktoken 与分词资产，组件/集成测试用假分词器和假工作区扫描器；真实分词器路径未在这里跑过（独立进程用例确认其不可用时降级而不失败）。
- Checkpoint 的真实工作区扫描（假扫描器）。
- 并发与大负载、非 loopback、TLS。
- 文档读取：`developers.openai.com/codex/hooks` 308 重定向到 `learn.chatgpt.com/docs/hooks`，抓取工具返回的是摘要而非原文，H3 之前应对照原页面复核。

## 4. 与任务书的偏差（现有 ADR 优先）

详见 ADR 0017 第 14 节。要点：

1. `prompt.submit` 的 Context（`USER_PROMPT`）需要人类身份的来源 Event（ADR 0012），且 ADR 0007 不允许 system 密钥冒充人类，所以：`hosthook:report` 允许 human 或 system 持有；绑定文件可选 `human_api_key_file`，仅用于 `prompt.submit`；只有 system 密钥时原话仍记录（actor_kind=system），但不构建提示时的 Context，返回 `prompt_not_human_attributed`。
2. 工具结果沿用 ADR 0007 的冻结 payload 键 `codex_session_id`（`evidence.py` 里的历史命名），仅新增别名 `native_session_id` 与可选 `source_system`；Core 的 hosthook 模块自身不含 Agent 名字。
3. 不创建 Core `ses_` 会话对象；原生会话 ID 在 Event payload 里。
4. 新增两个 Raw Event 类型，作为 Checkpoint 的 Task 级来源；新增 `occurred_at`、`deliver_context`、`paths` 字段。
5. 趁文档支持，Codex 方言额外处理 `SessionEnd`；`PostCompact` 不请求注入（文档没有该输出）。

## 5. 遗留问题 / 需要用户决定（ADR 0017 第 15 节）

1. 是否把原生会话映射为 Core `ses_` 对象？（H1：否。）
2. Rule 的 `matcher.tool` 是否应匹配中性类别而不是原生工具名？（H1：原生名，现有 `Bash` 规则可继续用。）
3. 注入前是否再调 `context/{id}/check-current`？（H1：否。）
4. 按 trust 等级设验收门槛：另立 ADR。
5. 是否禁止 `events:write` 客户端写入 `host.*` 事件类型与 `host-hook-v1` 来源？（H1：不禁止，与其他事件类型一致。）
6. `tool.before` 在 Core 不可达时默认放行（按任务书），与 P1 hook 的“失败即拒绝”不同；是否把默认改为 deny？
7. 既有测试失败 `test_every_cited_web_case_exists_exactly` 在基线上就失败，需要有人处理。
8. 运行时没有自动并发锁：多个 hook 进程同时清空 spool 时依靠 Core 的 `delivery_id` 幂等，可能重复发送但不会重复记录。
