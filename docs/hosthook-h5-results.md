# Host Hook H5 结果：Grok Build 真实对话 Gate

日期：2026-10-14。分支：`hosthook-grok`（未合并、未推送）。
范围：用真实 `grok` CLI 与真实模型对话，验证 Grok Build 方言（项目级 hook）。脚本：`scripts/hosthook-real-conversation.py --agent grok-build`。
运行在隔离目录（临时 Core 与临时 `core.db`）。没有改动 `~/.grok` 的任何配置；唯一写入是用户在 Grok 里执行 `/hooks-trust` 对临时项目目录的信任记录。
设备级（`--scope user`）注册的真实验证见文末“设备级注册”一节。

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
* 全套测试 1765 个，仅剩 `main` 上也失败的 `p6.test_p4_contract_snapshot…test_every_cited_web_case_exists_exactly` 基线失败。

## 结论

Grok Build 方言在真实对话中通过了原话记录、强制拦截、延迟注入、带退出码的证据、Checkpoint 与压缩前后行为，均由原生 hook 触发。
同一套中立协议和同一条中立规则现在已在三个 Agent（Claude Code、Codex、Grok Build）上各自跑通。

## 设备级注册的真实验证（2026-10-14，用户同意后）

方法：对每个 Agent 用 `--prepare --hook-scope user --confirm-user-scope` 把 hook 装进**真实**用户级配置，测试项目里不放任何 hook 文件，只放 `.jasmine/host-binding.json`；
`--run` 跑同一组真实对话；再在一个没有项目文件的空目录里跑一轮；最后 `hooks uninstall --scope user`。Core、数据库和密钥都在隔离目录。

| Agent | 版本 | G-PROMPT | G-DENY | G-INJECT | G-EVIDENCE | turn.stop | 无项目文件的目录 | 卸载还原 |
|---|---|---|---|---|---|---|---|---|
| Grok Build | 1.0.52 | PASS | PASS | PASS | PASS | PASS | 事件数 33→33，无暂存文件 | 只删自己的条目；原本不存在的文件留下空壳 `{"hooks": {}}` 和一个备份 |
| Codex | 本机当时版本 | PASS | PASS | PASS | PASS | PASS | 事件数 31→31 | 与安装前的文件完全一致（`description` 等原有内容保留） |
| Claude Code | 本机当时版本 | PASS | PASS | PASS | PASS | PASS | 事件数 30→30 | 语义一致；安装器留下空的 `"hooks": {}`（原文件没有该键），已用卸载前保存的副本精确还原 |

* 压缩（需交互 `/compact`）在设备级形态下**未重测**；它与注册位置无关，已在项目级形态下验证过。
* Codex 需要在 `/hooks` 里审核一次新 hook（用户完成）；Grok 的全局 hook 不需要信任步骤，实测如此。
* 没有测：用户自己已有的 hook 与我们并存（这三个真实配置里都没有用户自己的 hook，该情形只有组件测试）；Grok 与 Claude Code 同时注册。
* 卸载留下的东西：每个被改动的文件旁边有 `<file>.jasmine-backup-<UTC>`（Grok 一个、Codex 两个、Claude Code 两个）；Grok 的 `~/.grok/hooks/jasmine-core.json` 留下一个空壳。
* 上述小缺陷（卸载留下空 `hooks` 键或空文件）随后已修，仅用户级卸载生效：只剩我们创建的骨架时删除整个文件（Grok）；Claude Code 的 `settings.json` 去掉被清空的 `hooks` 键；Codex 的 `hooks.json` 原样保留用户自己的键。有测试覆盖；修复后没有再对真实配置重跑。
* 验证后的清理：用户确认后已删除所有 `*.jasmine-backup-*`（经核对：每个 Agent 一份等于当前原文件、一份是装着我们 hook 的安装态）和 Grok 的空壳文件及原本不存在的 `~/.grok/hooks/` 目录；三个 Agent 的真实配置回到验证前状态。
