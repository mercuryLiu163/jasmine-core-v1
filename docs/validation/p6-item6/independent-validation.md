# P6-02 item 6 独立验收

结论：**PASS_I6_SCOPED**。验收者 `/root/item6_independent_validation` 未参与产品实现、设计评审或实现代码评审。本报告仅确认本次隔离项目中的 item 6 确认契约与浏览器 → Core → Evidence → 独立 State 请求闭环，不代表 P6 整体、P3/P4/P7 或 V1 整体通过。

候选 A：`75eea9601bf5f788a11ecedb5077460ef43006bf`，schema 12；正式 Gate 开始及结束时均核实 HEAD=A、tracked/untracked Git status 完全 clean。验收过程中未编辑产品、测试或冻结 harness。后续仅文档 commit 的产品等价性应另核对，不能把报告自动移绑到修改过的产品。

## 绑定与证据

隔离证据目录：`validation-runs/p6/20261009-independent-item6/`。正式 Gate 子目录 `realcore/`。报告与完整 ignored 证据均在本 checkout；数据库、凭据与 Chrome profile 在独立私有 temp 中，已由各 runner 清理。没有访问用户运行数据库、部署服务、hooks、全局配置或其他 worktrees。

证据总 manifest SHA-256：`3aa9c5caeab7f3c0ab809d5525392119d26ffa02dd4a997b96596c5c603103c8`（`independent-bundle-manifest.json`，146 文件；manifest 自身及本报告明确排除以避免循环摘要，本报告 SHA 另行交付）。正式 Gate manifest SHA-256：`8ef53e4dc9c64494355715804342bbb8d21b19df4886c21f6a3e2522fdb49ae3`。逐条独立重算正式 manifest 全部文件摘要，一致。

冻结设计输入现场重算一致：

| 输入 | SHA-256 |
| --- | --- |
| adr.md | 540568b257c9419dd6c4f749f7ef40a8b94994f6750892fcbb609397e4e91cef |
| api.md | aefccb63087ad3d4c7fc5ff2dc87cbbca29e10df260988384ab5d2178f426da5 |
| validation-plan.md | 125c2e2ee8f34bef72da301332d31b192412e19534e9a7a246614b26b40f59e2 |

依赖基线：Core/P3 `7016dabb8926d6bd6599f1f56b8f39cbda253ebf`；P6 `41a0dd6ae29dc559c9d3817fb6ea34229c68b66b`；P3/P4 集成 `039f5a3e7800704e5f0478d01992f00eeb2ca3f1`。环境为本 checkout `.venv/bin/python` Python 3.13.15、Node v26.10.0、真实 Chrome CDP；具体环境与 UTC 时间见正式 `gate.json`。

## 实际结果与失败保留

- 正式命令：`.venv/bin/python scripts/p6/item6_realcore_gate.py --out validation-runs/p6/20261009-independent-item6/realcore`，未传 `--development`，exit **0**，**125 个检查全部通过**。开始 `2026-10-09T07:33:33.490321+00:00`；真实准备期限 `2026-10-09T07:43:38.184364Z`，等待实际 600 秒后 submit **409 confirmation_expired**，拒绝前后 Truth 不变。未 patch 时钟或 SQL 到期值。
- 独立完整 Python suite：**1424 tests，11 skips，0 failures，0 errors，exit 0**；37 项 item 6 Core tests 均 PASS。Web：**34 files / 515 tests，exit 0**。正式 Gate 从候选重新 build Dashboard，exit 0。
- 私有 suite runner 首跑因自身 `sys.path` 漏 repo root，两个 `tests.*` 模块 import ERROR；原始 `full-suite-first-attempt-import-errors.txt/json` 保留。仅修私有 runner 后同 suite 重跑取得以上最终结果；首跑没有算产品 PASS。
- 独立 Chrome negative 私有副本首跑在第二项文件修改前缺 `readFile` import；该 FAIL、脚本与截图保留在 `actual_browser_negatives-first-attempt-missing-import-*`。仅补私有 import 后同命令复核通过；产品 A 未变。
- `actual-crash-subprocess-output.json` 保留真实 `os._exit(75/76/73/74)` 的 stdout、exit code；完整 suite 同时保留 fresh connection 重启结果与 receipt SHA。没有把 Python 异常注入称为进程崩溃。

## Core 矩阵

以下 `COMPONENT_PASS` 表示本次独立执行的隔离 component_simulation/真实本地 HTTP/SQLite/文件或子进程证据；时钟、scan budget、数据破坏等故障注入仍明确属于组件方式。`REAL_GATE_PASS` 表示正式非 development Gate 中的对应实际环境链。每行仅覆盖注明的执行情境，不声称所有组合都经过真实浏览器。

| 行 | 本次结果 / 主要证据 |
| --- | --- |
| C01 | COMPONENT_PASS + REAL_GATE_PASS：Step VERIFIED、Step ACCEPTED、Task ACCEPTED 三组 prepare/source/command/INFO；确认与 State 独立请求。 |
| C02 | COMPONENT_PASS + REAL_GATE_PASS：false/1/string/null；另补实际 HTTP 缺 affirmed 字段，400 且 Task/Step/Event/Evidence 不变、preparation OPEN。未提交 prepare 不产生 Evidence。 |
| C03 | COMPONENT_PASS + REAL_GATE_PASS：无 scope、admin-only/system 无 confirm、agent 与 out-of-band agent 坏 grant 拒绝。 |
| C04 | COMPONENT_PASS + REAL_GATE_PASS：真实 me 身份；null host、身份 host drift 拒绝；没有自动登记。 |
| C05 | COMPONENT_PASS + REAL_GATE_PASS：其他 actor/key、额外身份字段、保留 type/namespace/direct EventStore 写拒绝；实际 Raw source 伪造失败且 Truth 不变。 |
| C06 | COMPONENT_PASS：不存在引用、半 Rule pair、跨 Task Step；另独立 HTTP 跨 project ACTIVE Rule pair 409 rules conflict、没有半笔 projection。 |
| C07 | COMPONENT_PASS + REAL_GATE_PASS：版本冲突、State/criteria writer-window drift、Rule 新增/退役/supersede/版本漂移拒绝。 |
| C08 | COMPONENT_PASS + REAL_GATE_PASS：实际文件/HEAD/symlink/20,001 文件上限、extra-path 配置 drift；组件 reduced byte budget/非法 partial snapshot 拒绝。未实际生成 256 MiB 文件集。 |
| C09 | COMPONENT_PASS + REAL_GATE_PASS：真实 600 秒到期；组件 expiry exact boundary/clock rollback/request/scan budget、writer 最终到期及实际 SQLite competing lock 拒绝。 |
| C10 | COMPONENT_PASS + REAL_GATE_PASS：精确重放原 receipt、changed body 冲突、成功后文件/到期不再 scan 重放、host drift 拒绝、revoked key 401。 |
| C11 | COMPONENT_PASS + REAL_GATE_PASS：实际 HTTP 并发；组件强制两方 early miss/scan barrier；相同 ID 201+200、不同 submit ID 201+409、一份消费。 |
| C12 | COMPONENT_PASS：prepare/submit 各写点 rollback；真实子进程 commit 前后分别崩溃和 fresh connection 恢复，四 exit code 与原始 stdout 均保留。 |
| C13 | COMPONENT_PASS：仅一次性 DB backup 的 source text/hash、重 hash 原文、prepare、command、Evidence、cached receipt 与 refs 破坏；status/replay integrity_error、State missing_evidence。 |
| C14 | COMPONENT_PASS：Step restart/revision cycle 与 Rule 新版本使旧确认失效，旧 receipt 仍可历史读；不将历史读判 fresh。 |
| C15 | COMPONENT_PASS + REAL_GATE_PASS：普通确认不满足 Rule pair；两个 exact Rule pair 分别确认；INFO 不满足 USER_CONFIRMATION/PASS。 |
| C16 | COMPONENT_PASS + REAL_GATE_PASS：严格 prepare/source/command seq 和 source 链；旧 user.prompt/confirm 实际 HTTP 回归；published migration bytes 独立不变。 |
| C17 | COMPONENT_PASS：target/current mismatch、empty criteria、skipped-only Task；另补 empty Task Steps 409 task_steps_ineligible。 |
| C18 | COMPONENT_PASS + REAL_GATE_PASS：strict body/duplicate keys/finite Unicode/types/node cap/oversize review/非法 snapshot；完整原文 Rule/note/SHA/授权 readback/replay/list 比较后才派生脱敏；HTML 不执行。 |
| C19 | COMPONENT_PASS + 独立实际旧代码拒绝：schema 1–11 逐前向迁移保历史 rows/checksum；append-only/REPLACE；另在私有 temp git archive 039f5a 实际旧源码 SCHEMA=11 读取新 schema12 明确拒绝，非 patch 替代。11 份 old migration bytes 与 Git 历史一致。 |
| C20 | COMPONENT_PASS + REAL_GATE_PASS：audit 故障 best-effort 不破坏事务；正式三个浏览器首次 submit request IDs/command IDs 与独立 admin audit 精确匹配，audit 不含 synthetic secret-pattern note/Rule。 |
| C21 | COMPONENT_PASS：新 scan 期间另一个真实 HTTP writer/reader 完成；scan 时 conn 无事务；采样期间 State/Rule 改变拒绝。 |
| C22 | COMPONENT_PASS + REAL_GATE_PASS：撤销 key 401；组件其他授权 key 可使用仍 fresh 历史 Evidence，不删除旧来源。 |
| C23 | COMPONENT_PASS：隔离测试 key 当前读 scopes 降低后 status/prepare replay 403；自身 submit replay 只返回原 receipt、不泄露 Rule review；换 key owner mismatch。 |
| C24 | COMPONENT_PASS + REAL_GATE_PASS：EXECUTED UC/INFO → 浏览器目标 VERIFIED 确认 → 单独 VERIFIED → 新 revision 目标 ACCEPTED 确认 → 单独 ACCEPTED；UC/PASS negative 仍缺 Evidence。 |

组件逐 test ID/verdict/duration 见 `full-suite-result.json`；实际 HTTP 补项见 `missing-affirmed-http.json`、`additional-core-http-negatives.json`。正式全部 HTTP/request_id/拒绝前后 Truth 见 `realcore/gate.json`。

## Web 与真实 Gate 矩阵

| 行 | 本次结果 / 执行模式 |
| --- | --- |
| W01 | COMPONENT_PASS + REAL_SUBSET_PASS：两个 host 共享精确 method/path/flag/Origin policy；启用 standalone host → 实际新 Core 可用；实际旧/broad 写和 cross-Origin 新写被拒绝。旧 Core capability UI negative 只在 mock suite 验证；未声称真实旧 Core 浏览器运行。 |
| W02 | REAL_GATE_PASS：三次真实 Chrome 完整 raw review 与 Core SHA 同字节、默认未勾选、真实 checkbox/button；INFO 三个独立 GET readback；原文 synthetic secret-pattern Rule 可见且 script 不执行。 |
| W03 | REAL_BROWSER_NEGATIVE_PASS + COMPONENT_PASS：独立新增真实 Chrome 无 confirmation 的单独 VERIFIED → 422 missing-evidence panel，Task/Step/双 history 不变；prepare 后实际修改 invoice.py → UI submit 409 fingerprint conflict，原 note 保留、review 清除、无 auto prepare/submit retry、无 State 变化。其他 Rule/revision UI 分支为组件覆盖。 |
| W04 | REAL_GATE_PASS + COMPONENT_PASS：提交已 commit 的丢响应保持 UNKNOWN，固定原 ID/body，经 status 原链恢复、没有新 submit；新 Chrome/reload 只读历史。cross Task/key late callback、双击边界在 mock suite，未外推为每组合真实 Chrome 实测。 |
| W05 | REAL_GATE_PASS + 独立 REAL_BROWSER_COUNTEREXAMPLE_PASS：Raw Unicode/HTML/note 跨 submit/status/replay/Event/list 不 mask，浏览器不执行；bearer 不入 bundle/storage；实际 Origin 拒绝。额外 201 source_event=null → UNKNOWN；恢复时 source GET 404 → 未核实，无完整 readback/State 晋级。 |
| W06 | REAL_GATE_PASS + COMPONENT_PASS：source deep link/重启/reload 保留新专用 source；Step VERIFIED→ACCEPTED→final Steps 后 Task 新确认与 accept；item5 candidate matrix/多 Rule 选择仅组件 UI 覆盖，不把候选当 Core verdict。 |
| G-I6-01 | REAL_GATE_PASS：当前候选 build、隔离新项目、实际 subprocess invoice unittest 与配置 producer、真实 Chrome 三次确认/三个独立 State 请求、原链与最终 Core accepted State、独立 admin audit。 |
| G-I6-02 | REAL_GATE_PASS + 上述独立补充反证：缺确认、wrong identity、State/revision/Rule/file drift、真实到期、真实 socket 并发、UNKNOWN 恢复、proxy 拒绝、INFO/PASS；逐拒绝 Truth 不变。强制 early-miss concurrency 是另行组件证据。 |
| G-I6-03 | REAL_GATE_PASS（候选 A）：仅重启 harness-owned Core，fresh Chrome status/receipt/source deep link/reload 读取；旧 prompt 为独立 suite 中真实 HTTP 回归。后续若产品/harness 更改必须重新验收；仅文档集成需证明代码树等价。 |

真实 browser 主要 loop 61 checks / 24 click-control traces / 77 API requests；history 9 checks。所用 select change 是浏览器 DOM control 的真实 change dispatch，affirmation 和提交是 CDP pointer；没有调用 React handler 或在浏览器外先造确认正例。

## 三项独立反证

1. **假来源**：正式 Raw `/v1/events` 专用 source 伪造 400；Task/Step/Event/Evidence/Rule Truth 不变，缺确认仍 422。组件同时试保留 type、保留 namespace、伪 command marker 与 direct generic EventStore 写。
2. **runner 可证伪**：仅 ignored 私有 harness 副本，原 A 产品未改。forced-false → `candidate_tracked_clean_or_explicit_development` false、exit 1；空 checks / 删除必需 Gate check → `all_required_scenarios_present_and_passed` false、exit 1；删除 browser source check → `all_required_browser_scenarios_and_counts` false（required=3/count=0），browser 与 Gate exit 1。均在目标断言处失败，非 ROOT/import/environment 失败。每个副本 SHA、原 SHA、输出和 false details 均保留。
3. **UNKNOWN/丢来源**：额外真实 Chrome 在 Core 真正 201 commit 后将 response 的 source_event 置 null，UI 保持 UNKNOWN、固定 command ID且无成功 receipt/readback。status 返回真 receipt 后 source GET 被独立拦为 404，UI 明确 GET readback 未完成/未核实，没有完整 readback，Step 仍 EXECUTED rev3。独立 Core source GET 200 证明实际来源仍存在，测试模拟的是交付/读回缺口。13 个检查，exit 0（`PASS_COUNTEREXAMPLE`）。

私有副本的 `--development` 仅表示反证环境允许独立裁剪 harness；其结果从未借作正式 600 秒 Gate 的 PASS。全部注入文件与原 SHA 见 `counterexamples/injection-manifest.json`。

## 明确限制与 skip

11 个 skip 不是 PASS：`p6.test_freeze_bundle...test_the_current_p6_03b1_bundle_matches_its_manifest` 因本 checkout 没有历史 evidence index；九项 `test_context` 与一项 `test_memory_gate_runner...test_two_real_p3_packs_for_different_tasks_differ_only_in_their_task_ids` 因 offline tokenizer cache 未配置，原 reason 为 `pinned tokenizer or encoding asset is unavailable`。逐完整 test ID/reason 在 `independent-command-results.json` 与 `full-suite-result.json`，不概称整个 P3/P4 skipped 或通过。

自动化 Chrome 证明浏览器至 Core 的链，不证明现实中的物理人亲自阅读/点击；human 代表登记的 bearer key actor。TEST producer 是本次隔离配置的实际 subprocess runner，不是 native Codex/P2 capture。完整材料浏览器比较在派生截图/JSON synthetic-pattern 脱敏之前，截图不是独立证明原始 SHA 的替代品。未下载缺失 token assets，未启动/重启用户服务，未将 C/W 组件模拟包装成全部真实部署通过。
