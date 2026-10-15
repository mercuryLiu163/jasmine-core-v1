# Host Hook Protocol H2 结果（2026-10-12）

范围：任务书 H2（Claude Code 方言、两个 Agent 的项目级安装命令、真实对话 Gate 脚本的 `--prepare`），外加用户在 H1 之后对开放问题的决定（第 0 节）。
基于分支 `hosthook-h1`，工作分支 `hosthook-h2`，未 push、未合并。
本文件只陈述 H2 做了什么、在哪一层测过什么。**没有任何 Agent 原生效果被验证**：没有运行过 `claude`、`codex` 或任何真实模型对话；没有执行 `--run`。

## 0. 先落实的用户决定（独立提交 c7e5943）

| 决定（用户，2026-10-12） | 落实 |
| --- | --- |
| Core 不可达时 `tool.before` 默认 **deny**；保留 `on_unavailable: allow` 作为显式选择；spool 仍记录缺口 | `binding.py`、`dialects/__init__.py`（`Outcome` 默认）、`runtime.py`；ADR 0017 第 10、13、14(D1) 节、spec、测试 |
| 仅持有 `events:write` 的客户端写 `host.*` 事件类型或 `host-hook-v1` 来源 → 拒绝 | 新错误 `403 host_hook_namespace_reserved`（`errors.py`），在 `EventStore.append` 中统一执行（`events.py`，端点处理期间用进程内 contextvar 放行）；ADR 第 11.1 节、API 文档、测试（`/v1/events` 与带 `source_system` 的对象路由都被拒） |
| Rule 暂时继续匹配原生工具名；第三个 Agent 加入前必须改为中性类别匹配 | 已写入 ADR 0017 第 15 节第 2 项（含理由） |
| 不映射 Core `ses_`（P5 再议）；不做注入前 `check-current`；trust 门槛另立 ADR | 已写入 ADR 0017 第 15 节，标注“用户 2026-10-12 决定” |

顺带发现并修复一个 H1 测试缺陷：`test_deny_ask_and_allow_reach_the_native_output` 在 fail-open 默认下“通过”，实际是第二次调用复用了同一个 `tool_use_id` 而得到 `409 host_hook_delivery_conflict`（被放行掩盖）。改默认后暴露，已在测试中改用不同的 `tool_use_id`；代码无改动。

## 1. 已实现

| 项 | 位置 |
| --- | --- |
| Claude Code 方言（七个事件；`ask` 原生映射；`allow` 不输出；`prompt_id` 作 turn id，缺失时给出替代；Claude 文件工具的 action/path 解析） | `src/jasmine_core/hosthook/dialects/claude_code.py` |
| 方言文档（官方页面 URL、读取日期、**无法确认的字段清单**、turn id 稳定性、输出格式） | `docs/hosthook-claude-code-dialect.md` |
| 两个方言的能力声明 JSON | `dialects/codex.capabilities.json`（H1）、`dialects/claude_code.capabilities.json` |
| 方言注册表支持带连字符的 Agent 名（模块名 `claude_code` ↔ 名称 `claude-code`） | `dialects/__init__.py` |
| 项目级安装器：`jasmine-core hooks install\|uninstall\|status --agent codex\|claude-code --project-dir D --binding F [--command PREFIX]` | `hosthook/install.py`、`cli.py`；每个方言提供 `INSTALL`（配置文件、事件、超时、全局目录名） |
| 真实对话 Gate 脚本（`--prepare` 与 `--run`；`--run` 未执行） | `scripts/hosthook-real-conversation.py` |
| ADR 0017 第 16 节（安装器、第二方言、H2 偏差 D7–D9）；Codex 方言文档更新“Config”节；README 状态表加一行 | `docs/adr/0017-host-hook-protocol.md`、`docs/hosthook-codex-dialect.md`、`README.md` |

安装器规则：只写 `<project>/.codex/hooks.json` 或 `<project>/.claude/settings.json`；合并而不覆盖（其余键与其他 hook 原样保留）；`install` 幂等；`uninstall` 只删自己的 handler（按 `... hook --agent X --binding P` 命令形态识别，外观相似的不删）；拒绝项目目录等于 HOME、目标落入任何方言声明的全局目录、符号链接、已有文件非合法 JSON 对象、绑定文件不可用、未知 Agent。核心模块 `install.py` 不含 Agent 名（现有测试检查）。

README 选择：加了一行状态表（分支上已实现、组件测试通过、原生未验证）。README 本身已陈旧（仍写 schema v8），没有做其他修改。

## 2. 测试：层级与结果

测试命令（需要 venv 解释器：`p4-codex-hook-install` 的既有测试要求 canonical venv launcher，直接用 `/opt/homebrew/bin/python3.13` 会让 10 个既有 p4 测试失败，与本任务无关）：

```
python3.13 -m venv <scratch>/venv
env PYTHON=<scratch>/venv/bin/python ./scripts/run-tests.sh
```

结果：`Ran 1712 tests ... FAILED (failures=1, skipped=13)`。唯一失败仍是基线就失败的 `p6.test_p4_contract_snapshot...test_every_cited_web_case_exists_exactly`（未处理）。H1 末为 1658 个测试；本阶段新增 54 个（第 0 节 +2，H2 +52），均通过。

| 层 | 文件 | 覆盖 |
| --- | --- | --- |
| 组件（Core） | `tests/test_hosthook_core.py`（增 1） | `host.*`/`host-hook-v1` 保留命名空间：`/v1/events` 与对象路由被拒，端点仍可写 |
| 组件（运行时） | `tests/test_hosthook_runtime.py`（改/增） | 默认 deny 且留 gap；`allow` 显式选择；绑定损坏默认 deny；4xx 拒绝的策略 |
| 方言（fixture “依据文档构造”，`fixtures/hosthook/claude-code/`，20 个文件含 README） | `tests/test_hosthook_dialect_claude_code.py`（21） | 双向翻译；`prompt_id` 与替代 turn id 的稳定性；delivery_id 确定性；工具类别与文件操作数；`exit_code` 不臆造；`ask` 原生、`allow` 无输出、原因截断、不可达策略、注入形状；每个 fixture 构造的请求通过协议解析；Schema 校验（`test_hosthook_schemas.py` 增 1） |
| 组件（运行时 + Core） | `tests/test_hosthook_claude_code_flow.py`（`RuntimeComponent`，8） | 进程内 `run_hook("claude-code")`：原话逐字、注入形状、Compact 后 SessionStart 的 Context、deny/ask/allow、按原生工具名的 Guard、Evidence 与 trust、Checkpoint、重放不重复、Core 不可达时 fail-closed/spool |
| 本地集成 | 同文件 `CliProcess`（2） | 真实 `python -m jasmine_core.cli hook --agent claude-code` 子进程 + 真实 HTTP Core（scratch DB，线程内，假分词器）覆盖五项目标的 Core 侧记录；无绑定时空输出 |
| 跨方言 | `tests/test_hosthook_cross_dialect.py`（2） | 同一组中性事件（session/prompt/deny/allow/tool.after/compact/stop/end）经 Codex 与 Claude Code 方言，Core 的 Raw Event（去除来源字段与逐条 ID）、Evidence、Context/Checkpoint 原因序列、receipt 决策结构一致；来源字段（agent、adapter、能力摘要、delivery_id）确实不同 |
| 安装器 | `tests/test_hosthook_install.py`（15） | 合并保留、幂等、换绑定、两 Agent 互不影响、仅新增一个文件且 HOME 无变化、生成的命令可被 `/bin/sh` 执行、卸载只删自己的、`status`、各类拒绝、CLI 子进程 |
| Gate 脚本 `--prepare` | `tests/test_hosthook_gate_prepare.py`（3） | 两个 Agent 的 scratch 夹具（0600/0700、项目级配置已安装、绑定无 token、规则 ACTIVE、DB 无 hook 流量、Core 已停止）；用“被执行就留标记”的替身 CLI 证明 `--prepare` 不启动 Agent；nonce 不在项目文件中；各种拒绝 |

我还对两个 Agent 实际运行了 `--prepare`（scratch 目录，`--cli-bin` 为本机真实 `claude`/`codex` 路径，只做定位与 SHA-256，**未执行**）：均退出 0，输出手动步骤。

## 3. 未验证

- 所有原生效果（沿用 H1 清单），对 Claude Code 同样适用：hook 是否触发、字段是否如方言文档、`deny`/`ask` 是否生效、`additionalContext` 是否到达模型、`-p` 模式下 hook 与项目信任行为。
- **Claude Code 文档读取受限**：抓取工具只返回小模型生成的摘要，未得到原文。无法确认的字段见 `docs/hosthook-claude-code-dialect.md`：`UserPromptSubmit` 的 `prompt` 字段名、`SessionEnd.reason`、`PreCompact.trigger`、`PostToolUse.tool_response` 的任何键（尤其是否有整数退出码）、`UserPromptSubmit` 上 `additionalContext` 与纯 stdout 的差异、`ask` 在无头模式的行为、`PostToolUseFailure` 等事件。
- Claude Code 的 `prompt_id`（摘要引用）：方言在存在时用作 turn id；其真实行为未观察。
- 若 Claude Code 的 Bash `tool_response` 没有整数退出码，所有 Claude Code 的 Bash Evidence 将是 `INFO`（NATIVE_HOOK 等级仍记录，但无法满足需要 `PASS` 的验收要求）。已知限制。
- 安装器写出的条目是否被 Codex/Claude Code 加载、是否需要信任确认（Codex 需要；Claude Code 在 `-p` 下不确定）；省略 matcher 是否表示“全部”。
- Gate 脚本 `--run` 整体未运行，其中的 CLI 参数（`claude -p --output-format json --allowedTools Bash`、`codex exec --json --skip-git-repo-check --sandbox workspace-write`）、答案解析、检查逻辑都**只经阅读检查，未经任何执行**。
- Context Pack 的真实分词器路径（本机没有 tiktoken 0.14.0 与资产；`--prepare` 报告 `context_tokenizer: unavailable`，G-INJECT 在这种情况下 `--run` 会 BLOCKED）。
- 延迟 p95 仍未测。

## 4. 与任务书/H1 的偏差

1. `tool.before` Core 不可达默认 deny（用户决定，偏离任务书 4.4）。
2. 新错误码 `host_hook_namespace_reserved`（用户决定 2；已入 ADR 与 API 文档）。
3. 任务书称 Claude Code 没有 turn id；文档摘要列出 `prompt_id`，方言存在时使用，缺失时仅对 `prompt.submit` 给替代值（ADR D7）。
4. Claude Code 没有 PostCompact：压缩后的注入通过 `SessionStart(source=compact)`，映射为 `session.start`；`context.compact` 对该 Agent 只有 `before`。
5. `allow` 不输出 `permissionDecision: "allow"`（会绕过 Agent 自身的权限询问）。
6. 方言模块新增 `INSTALL` 数据（含 `global_dir`），安装器据此拒绝全局目录，从而核心 `install.py` 不出现 Agent 名。
7. Gate 脚本在 `--prepare` 自己生成 scratch 的 human/system key 文件（仅 `hosthook:report`，位于 `--out/private/`），因此用户不需要提供任何 key 文件；`--prepare` 成功的 `verdict` 为 `PREPARED`（退出码 0），不是 Gate 结果。
8. 触碰了 `cli.py`、`events.py`、`errors.py`、`dialects/__init__.py`、`codex.py`（H1 文件）——均不在禁区列表；m0013 未改动，没有新增 migration。

## 5. 遗留问题 / 需要用户决定

1. 对照原始页面复核 Claude Code 文档的未确认字段（第 3 节），尤其 `tool_response` 与 `-p` 模式下的 hook/信任行为，再做 H3。
2. 若 Claude Code 无整数退出码：是否接受 Bash Evidence 为 `INFO`，还是由方言根据 `interrupted`/stderr 等推断（不推荐，会臆造）。
3. G-INJECT 需要固定版本 tiktoken 与编码资产；H3 前要在运行解释器中准备好（`--tokenizer-cache`），否则只能得到 BLOCKED。
4. `ask` 在 `claude -p` 下的行为（可能等同拒绝）会影响 G-DENY 对未解析命令的预期；Gate 的被拒命令是可解析的简单 `touch`，走 deny 而非 ask。
5. 多个 hook 进程并发清空 spool（H1 遗留）。
6. 既有失败 `test_every_cited_web_case_exists_exactly` 仍在基线上失败。

## 6. H3 的 `--run` 步骤（每个 Agent）

通用：先 `--prepare` 得到 `--out`（新目录，绝对路径）；用 3.11+ 解释器（带 `tiktoken==0.14.0` 才能让 G-INJECT 有机会 PASS，并加 `--tokenizer-cache <含固定编码资产的目录>`）。不需要提供 key 文件。

```
python scripts/hosthook-real-conversation.py --agent <codex|claude-code> --prepare --out /absolute/new/dir [--tokenizer-cache DIR]
# 人工步骤（见输出）
python scripts/hosthook-real-conversation.py --agent <...> --run --out /absolute/new/dir --user-reviewed-trust
```

- Codex：在 `<out>/project` 中交互运行 `codex`，信任该项目，用 `/hooks` 审查并信任来自 `.codex/hooks.json` 的每个 hook，退出；然后 `--run`。
- Claude Code：在 `<out>/project` 中交互运行一次 `claude` 并接受该目录的工作区信任提示，退出；然后 `--run`。若 hook 完全没触发，所有检查为 BLOCKED（原因写明），不是 FAIL。
- 结果 JSON 位于 `<out>/run-<时间>/report.json`；退出码 0 全 PASS，1 有 FAIL，2 其余（含 G-CHECKPOINT 的压缩部分，无头模式无法触发，恒为 BLOCKED，需交互 `/compact` 另行验证 PRE_COMPACT）。
