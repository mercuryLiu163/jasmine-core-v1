# Context budget 七项集中修复 — AUTHOR_VERIFIED / NOT_SIGNED

候选 **`3747ce1d4d5506e4b041c60b9d8025de5c85f9eb`**，从 clean archive 全新运行。作者 **9/9 场景、36/36 必需门**；
正式 CLI PASS、exit 0、stderr 空。原19反证全部拒绝；复核与扩展23反证全部拒绝，
合计 **42/42**，其中原8种误报修改协议 **8/8** 闭合。24/24完整性聚焦测试通过。
**非作者代码复评／独立隔离复验尚未运行，不能签署限定验收。**

前轮复核原件：[REVIEW](<../../../../../evidence/p6-context-budget-review-2a37a1a-20261010/REVIEW.md>)。`2a37a1a` 的结论保留 REVISE_REQUIRED；
原8份误报、旧正常运行、旧测试、旧freeze均未改写。本轮沿其统一清单一次处理F01—F07。
用户已将这7项归为首次发现的工具原有遗漏；本轮没有把它们说成产品线上故障或修复引入回归。
新旧对照：[OLD-NEW-EIGHT](<../../../../../evidence/p6-context-budget-repair-3747ce1-20261010/OLD-NEW-EIGHT.json>)，旧原件CLI exit0/PASS，新候选同样
修改协议重基到新正例副本CLI exit1/FAIL或BLOCKED；不是通过缺少新见证、旧摘要或缓存拒绝。

## 七项修复与闭合

| 项 | 修复 | 具名拒绝对照 |
| --- | --- | --- |
| F01 P1 | 候选 read_task_snapshot 在具名 build-before DB 重衍 Authority/Truth/selector；生产render；Context全部标量/JSON/full_result；不可变命令digest/count及身份。CB05保留旧build-before | mandatory_rule_dropped_consistent_pack；command_digest_wrong；command_count_wrong；Context_column_JSON_diverge |
| F02 P2 | 产品拥有状态/摘要/控件HTML与额外alert检查，历史rawtext/jsonblock/kv结构排除；保留诚实否定句 | positive_page_extra_injection_claim；extra_claim_refresh_only；extra_success_summary；单元历史引用正例 |
| F03 P2 | 实际key/actor/scopes/home_host注册表，与operator、browser首读/刷新、独立admin读取及物理审计method/path/status/error/request_id交叉绑定 | operation_actor_does_not_match_audit；browser_audit_wrong_method；browser_refresh_audit_wrong_method；key_scope_forgery |
| F04 P2 | Checkpoint按生产读逻辑解码存储，与完整note/source/request/原Truth/selector/命令/增量绑定；用实际note和formatter独立重算omission | checkpoint_note_response_does_not_match_DB；wrong_selected_checkpoint；wrong_note_source；wrong_omission_token_count |
| F05 P2 | 启动/结束、唯一owned PID、候选scanner/continuity hash、方法、连续seq和完整request窗口；非replay扫描正例先成立再认读取/replay零调用 | observer_attestation_removed；observer_sequence_gap；observer_code_hash_wrong；observer_stop_absent |
| F06 P2 | 代理pending逐项断言冻结404、错误code/目标和Core零调用 | proxy_pending_HTTP_200 |
| F07 P2 | PNG结构/CRC/IHDR/连续IDAT/IEND，原生Chrome RGB/RGBA8完整inflate/行过滤格式，clip尺寸和捕获角色 | PNG_signature_without_image；PNG_truncated；PNG_wrong_dimensions；单元CRC/尾随字节等 |

未改Core/schema/产品代码/白名单。冻结v2仍是9场景、7浏览器+2HTTP-only、36门；修复约束和
测试定义单独冻结于 REPAIR-FROZEN.json。DB新增 after-audit 一致备份，用于授权读回自身的审计，
原18个业务/操作快照角色不删，本轮共 **19 DB**；10次捕获、20 DOM/PNG。

## 本轮实际验证

[全新raw](<../../../../../evidence/p6-context-budget-author-repair-run-01-20261010/raw-result.json>)、[result](<../../../../../evidence/p6-context-budget-author-repair-run-01-20261010/result.json>)、
[manifest](<../../../../../evidence/p6-context-budget-author-repair-run-01-20261010/manifest.json>)；[正式CLI](<../../../../../evidence/p6-context-budget-repair-3747ce1-20261010/FORMAL-CLI.json>)。
合法CB05旧包、历史GET/replay均通过，check-current实际409 context_stale，replay scanner零次。
真实scanner正例为6次enter、6次exit、6次Popen；observer从started到stopped共184条连续见证。
HARD／NORMAL mandatory超限均真实409且没有新增pack/command/key；不把HTTP-only称浏览器失败回执。

[原19反证](<../../../../../evidence/p6-context-budget-repair-existing-3747ce1-20261010/RESULT.json>)；
[复核+扩展23反证](<../../../../../evidence/p6-context-budget-repair-review-regressions-3747ce1-20261010/RESULT.json>)。
全部副本修改后重算result、重新封存、调用同候选正式CLI，完整stdout/exit/理由逐份保存；
正例原字节不变。复制DB的trigger解除仅用于显式坏证据构造，实际Core与旧证据保持原状。

[24聚焦测试](<../../../../../evidence/p6-context-budget-repair-preflight-3747ce1-20261010/integrity-tests.log>)；
py_compile与diff-check通过，clean archive npm ci/build实际包含tsc并通过。
产品Web/Core均未改，本轮不重报旧52为新执行，也未运行完整Core/P6 suite，历史11skip仍保留。

## 可复验入口与限制

从候选clean checkout执行：

```sh
/opt/homebrew/bin/python3.13 scripts/p6/context_budget_run.py run \
  --ref 3747ce1d4d5506e4b041c60b9d8025de5c85f9eb --out '/path/to/new-private-evidence'
/opt/homebrew/bin/python3.13 scripts/p6/context_budget_gate.py \
  --bundle '/path/to/new-private-evidence' --expected-candidate 3747ce1d4d5506e4b041c60b9d8025de5c85f9eb
/opt/homebrew/bin/python3.13 scripts/p6/context_budget_adversaries.py \
  --bundle '/path/to/new-private-evidence' --expected-candidate 3747ce1d4d5506e4b041c60b9d8025de5c85f9eb --out '/path/to/new-existing-adversaries'
'/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p6-03-integration/validation-runs/p6/realcore-toolchain/venv/bin/python3.13' \
  scripts/p6/context_budget_review_regressions.py \
  --bundle '/path/to/new-private-evidence' --expected-candidate 3747ce1d4d5506e4b041c60b9d8025de5c85f9eb --out '/path/to/new-review-regressions'
```

可读toolchain/离线资产沿既有 --python/--cache；依赖缺失必须BLOCKED。脚本只创建/关闭自身
Core、2Dashboard、Chrome/profile，item6=0，不访问正式15180/18787服务或正式库。
本轮4组owned进程均group_gone，自有runtime/profile已清理；这些不是独立复验或实际部署收据。

PNG解码不证明像素与DOM不可能伪造，原生采集器/操作员信任边界保留。产品声明判据覆盖
冻结结构与具名断言，历史数据与否定句不按全页词语黑名单处理，不宣称理解所有措辞。
旧8反例是同修改协议重基，不把原旧副本因缺after-audit.db直接BLOCKED冒充闭合。

下一步绑定本候选统一做非作者复评、全新隔离复验，发现残留先汇总；两项结论齐备再决定限定签署。
失败build看板回执、旧Memory/规则冲突、实际注入、真实对话链、Validation真实FAIL与最终集成仍缺。
**P4路线停止，P6/V1未完成，main/部署未改，item6=0，日常使用未放行。**
[边界收据](<../../../../../evidence/p6-context-budget-repair-3747ce1-20261010/BOUNDARIES.json>)。
