# Host Hook H3 结果：真实对话 Gate（Claude Code / Codex）

日期：2026-10-13。分支：`hosthook-h2`（未合并、未推送）。
范围：用真实 Agent CLI、真实模型对话，验证 Host Hook Protocol 的 Claude Code 与 Codex 适配器。
脚本：`scripts/hosthook-real-conversation.py`（`--prepare` / `--run`）。所有运行都在隔离的临时目录，
使用临时 Core 与临时 `core.db`，未触碰任何用户全局配置或真实 `JASMINE_CORE_DB`。

> 本文只记录这两次具体运行观察到的事实。夹具文件仍是“按文档构造”，不是原生抓取。

## 环境

| 项 | Claude Code | Codex |
|---|---|---|
| 版本 | 2.1.287 | 0.162.1（准备阶段后由 0.160.1 升级，已重新 prepare） |
| 校验码 | `GATE-5A9DF82C123D249C` | `GATE-4ACBF0F6C35BC43B` |
| 证据级别 | NATIVE_HOOK | NATIVE_HOOK |

## Gate 结果

| Gate | Claude Code | Codex | 说明 |
|---|---|---|---|
| G-PROMPT 用户原话被记录 | PASS | PASS | 原话进入 Core 事件 |
| G-DENY 被拒命令确实没执行 | PASS | PASS | 临时 DENY/HARD 规则；哨兵文件未创建；模型自述被 PreToolUse hook 拦截 |
| G-INJECT 注入到达模型 | PASS | PASS | 模型答出校验码 |
| G-EVIDENCE 工具结果证据 | PASS（INFO） | PASS（INFO） | NATIVE_HOOK；两者都没有给出 Bash 退出码，故只记 INFO |
| G-CHECKPOINT 一轮结束 | PASS | PASS | `turn.stop` 生成 Checkpoint |
| 压缩前 Checkpoint（实时） | PASS | PASS | Core 在线，暂存目录为空 |
| 压缩后注入 | PASS | PASS | 见下 |

### 压缩
- Claude Code：第一次尝试时 Core 已意外退出，hook 事件进入暂存目录；`hook replay` 重放 7 条（sent 7, rejected 0）。
  第二次 Core 在线：`PRE_COMPACT` Checkpoint 实时生成，压缩后 `SessionStart(source=compact)` 返回上下文包，用户答案匹配。
- Codex：同一原生会话内依次为 `session.start(startup)` → 用户输入 → `turn.stop` → `context.compact(before)`（实时 `PRE_COMPACT` Checkpoint）→
  `context.compact(after)`（只记录，按设计不注入）→ `session.start(source=compact)`（返回上下文包）→ 用户输入 → `turn.stop`。所有响应 `degraded=[]`。

## 过程中发现并修复的问题

1. 安装器测试在 Python 路径含空格时失败（a06d087，引号修复）。
2. G-EVIDENCE 需从关联的 `tool.call` 事件读取 `tool_input`（f672a56，`call_event_id`）。
3. 驱动 `codex exec` 时必须 `stdin=DEVNULL`，否则等待 EOF 挂起（12c212e）。
4. Codex 的 G-DENY 最初 BLOCKED：模型读到了注入的规则，主动不尝试命令，所以根本没触发拦截。
   处理：新增可选绑定字段 `inject_context`（默认 true，ADR 0017 已更新），仅在拒绝测试那一轮关闭注入；
   Core 仍然构建上下文包，`tool.before` 照常执行。这一轮因此测试的是“强制执行层”，不是“模型自觉遵守”。
5. Codex 对 `SessionEnd` hook 的超时上限为 3 秒；安装器现在为 Codex 的 `SessionEnd` 写 3 秒。

## 限制与未验证

- **注入通道不可区分**：校验码在每次注入中相同，无法证明答案来自“压缩后 SessionStart”还是“后续 prompt.submit”。两条路径各自都被证明有效。
- **Bash 证据只有 INFO**：两个 Agent 的 Bash `tool_response` 没有文档化的退出码字段；没有把它升级为 PASS 级证据。
- **G-DENY 以 `inject_context=false` 完成**：Claude Code 的同一轮没有这个问题；Codex 需要此选项。
- 每个 Agent 只跑了各自的一个会话；没有多会话、并发、长时间运行、Windows/Linux 的数据。
- 验证脚本的检查在共享的临时数据库上查询，尚未按运行批次过滤（后续改进）。
- CLI 路径在 `--prepare` 时解析，Agent 升级后需重新 prepare（Codex 升级时已遇到）。
- 规则匹配目前按原生工具名；接入第三个 Agent 前需要中立类别匹配。
- 原生会话尚未映射到 Core 的 `ses_`（P5 之前）。
- 基线失败测试 `p6.test_p4_contract_snapshot…test_every_cited_web_case_exists_exactly` 在 `main` 上同样失败，未调查，与本工作无关。
- Core schema 升级到 13（迁移 `m0013 host_hook_deliveries`）；合并前需评估对已部署服务的影响。

## 结论

Claude Code 与 Codex 两个适配器在真实对话中通过了原话记录、强制拦截、上下文注入、证据采集、Checkpoint 与压缩前后行为，
各项均由原生 hook 触发。其余 Agent（Cursor、OpenCode、Pi、Grok Build、DSH）尚未开始。
