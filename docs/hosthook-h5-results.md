# Host Hook H5 结果：Grok Build 真实对话 Gate

日期：2026-10-14。分支：`hosthook-grok`（未合并、未推送）。
范围：用真实 `grok` CLI 与真实模型对话，验证 Grok Build 方言（项目级 hook）。脚本：`scripts/hosthook-real-conversation.py --agent grok-build`。
运行在隔离目录（临时 Core 与临时 `core.db`）。没有改动 `~/.grok` 的任何配置；唯一写入是用户在 Grok 里执行 `/hooks-trust` 对临时项目目录的信任记录。
设备级（`--scope user`）注册**没有**在真实 Agent 上验证，只有组件测试。

> 本文只记录这次具体运行观察到的事实。

## 环境

| 项 | 值 |
|---|---|
| Grok Build | 1.0.52（准备阶段前由 1.0.41 升级） |
| 注册方式 | 项目级 `.grok/hooks/jasmine-core.json`，hook 命令指向项目绑定 |
| 证据级别 | NATIVE_HOOK |
| 校验码 | `GATE-DB6F1025F4A56FB6` |

## Gate 结果

| Gate | 结果 | 说明 |
|---|---|---|
| G-PROMPT 用户原话被记录 | PASS | 三轮原话逐字保存 |
| G-DENY 被拒命令确实没执行 | PASS | 规则是中立的 `tool_category: shell`；哨兵文件未创建；放行对照文件存在；模型自述“Hook denied: matched rul_…” |
| G-INJECT 注入到达模型 | PASS | 第一轮先让模型跑 `echo gate-probe`，之后它答出校验码——即延迟注入（PostToolUse `additionalContext`）确实到达模型 |
| G-EVIDENCE 工具结果证据 | PASS | NATIVE_HOOK；**结果里有 `exit_code`（0）**，故能记退出码，比 Claude Code / Codex 的 INFO 级更强 |
| G-CHECKPOINT 每轮结束 | PASS | `turn.stop` 生成 `SESSION_STOP` Checkpoint |
| 压缩前 Checkpoint（交互式 `/compact`） | PASS | `PreCompact` 触发，实时生成 `PRE_COMPACT` Checkpoint |
| 压缩后注入 | PASS（弱证据） | `PostCompact` 触发并生成上下文包；压缩后模型先用 grep 找校验码（说明它当时没有），之后答对。但答案可能来自延迟注入也可能来自别处，通道不可严格区分（与 H3 同样的限制） |

## 首次真实运行确认的事实（此前为未确认）

* hook 在 `grok -p` 无头模式下会触发。
* Shell 工具名：`run_terminal_command`，输入 `{command, description}`。
* `toolResult`：顶层有 `exit_code`、`output_for_prompt`、`type: "Bash"` 等；另有 `output`（字节值数组，与文本重复）。方言现在丢弃该数组并记录 `output_bytes_omitted`。
* `grep` 工具名为 `grep`，输入 `{pattern, glob, head_limit}`，**没有路径**。
* `PostCompact` 事件存在；`PreCompact`/`PostCompact` 的 `trigger` 未给出（记录为 null）。
* 文件类工具（`read_file`、`search_replace` 等）的操作数键名**仍未观察到**；`write`/`edit` 尚未被真实调用。

## 过程中发现并修复的问题

1. **暂存目录并发重放**：Core 未开时 hook 把事件存入暂存目录；`--run` 开始时两个 hook 进程同时重放，一个读到已被另一个删除的文件，被误记为“malformed”进 `spool/rejected`。事件没有丢（按 `delivery_id` 幂等），但会产生假的拒绝记录。现在该情形直接跳过，并有回归测试。
2. **无路径的搜索工具被判 `ask`**：`grep` 无 `path` 时 Guard 回答“insufficient context to evaluate all applicable rules”，交互使用中每次全项目搜索都会弹确认。方言现把无路径的 `grep`/`glob`/`list_dir`（Claude Code 的 `Grep`/`Glob` 同理）按当前工作目录处理。修复后用真实 Grok 重跑一次 grep，决策为 `allow`。

## 限制与未验证

* 一个交互会话（压缩那次）没有记录 `session.end`；原因未查明（可能是退出方式，也可能是 `SessionEnd` 1 秒超时）。无头会话都记录了。
* 只有一个设备、一个模型、几个会话；没有并发、长时间运行、Windows/Linux。
* 校验码在每次注入中相同，无法区分注入通道。
* `ask` 决策在交互模式下的呈现未观察（压缩那次的两次 `ask` 发生在修复前，用户未报告异常）。
* 项目级 hook 需要对目录做 `/hooks-trust`；全局 hook 不需要，但未实测。
* 设备级注册（`hooks install --scope user`）对 Claude Code、Codex、Grok 均未用真实 Agent 验证。
* 全套测试 1765 个，仅剩 `main` 上也失败的 `p6.test_p4_contract_snapshot…test_every_cited_web_case_exists_exactly` 基线失败。

## 结论

Grok Build 方言在真实对话中通过了原话记录、强制拦截、延迟注入、带退出码的证据、Checkpoint 与压缩前后行为，均由原生 hook 触发。
同一套中立协议和同一条中立规则现在已在三个 Agent（Claude Code、Codex、Grok Build）上各自跑通。
