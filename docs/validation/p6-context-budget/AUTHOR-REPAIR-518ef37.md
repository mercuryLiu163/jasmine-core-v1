# Context budget R01–R04 统一修复 — AUTHOR_VERIFIED / NOT_SIGNED

候选 `518ef370575843f964644701172abb4b070d3966`，从全新 git archive 构建 Core、两个私有 Dashboard 与 Chrome。只修改验收工具、聚焦测试及新修复冻结文档；没有 Core/Web 产品、schema 或部署修改。

## 归因与修复

前候选 `3747ce1` 的非作者结论保留 REVISE_REQUIRED。R01/F02、R02/F03、R03/F04 是原项未完全闭合；R04 的“尚未注入成功”误报明确归为3747ce1修复引入的新问题，不混称原遗漏。

| 项 | 实现边界 | 具名验证 |
| --- | --- | --- |
| R01 | KV不再整块排除，预算页面合法字段集合固定；历史rawtext/jsonblock与产品状态分开 | metadata_claim_first / refresh / both；historical_raw_claim |
| R02 | 从实际消费的audit_read和browser_audit.http反向强制phase、GET/200/admin、注册权限、物理审计和observer；phase不能控制是否检查 | admin_phase_operation/browser_unknown / missing / other_valid |
| R03 | production source_for_task核对原source/Task/宿主/调用者；response/物理行/创建请求/注册actor/不可变command逐项绑定actor、host、project、task、session | checkpoint_identity_response/command五字段逐项副本 |
| R04 | 明确有限直接否定允许，按每一个匹配判断；后续肯定声明不能借前面否定绕过 | honest_direct_negative PASS；negative_then_affirmative 拒绝；成对聚焦测试 |

## 本轮实际作者验证

- [全新隔离](../../../../../evidence/p6-context-budget-author-repair-518ef37-20261010/result.json)：9/9场景、36/36必需门；[正式CLI](../../../../../evidence/p6-context-budget-repair-518ef37-20261010/FORMAL-CLI.json) exit0/PASS，stderr空。
- [聚焦测试](../../../../../evidence/p6-context-budget-repair-518ef37-20261010/focused-tests.log)：31/31通过。
- [原反证](../../../../../evidence/p6-context-budget-adversaries-518ef37-20261010/RESULT.json)：19/19拒绝。
- [复核/扩展](../../../../../evidence/p6-context-budget-regressions-518ef37-20261010/RESULT.json)：原23 + 新20 = 43/43拒绝；合计62/62。两项应通过的正式CLI正控制2/2通过：直接否定与历史原文。
- [原四项对照](../../../../../evidence/p6-context-budget-repair-518ef37-20261010/OLD-NEW-FOUR.json)：复用封存复评的旧输出、新候选同协议重基的CLI输出；前三种坏证据由旧PASS变为拒绝，直接否定由旧FAIL恢复PASS。不把旧副本缺新绑定引发的前置失败当闭合。

所有组件仅修改完整副本，重算result、重新封存、执行正式CLI；逐份保存stdout/exit/stderr和实质拒绝理由。正例字节不变。复制DB的trigger解除仅用于损坏证据构造，不涉及实际库。这些不是新的实际产品失败或独立验收样本。

原9场景/36门、7浏览器+2HTTP-only范围保持；CB03/04仅证明真实API超限拒绝，失败构建看板回执仍UNAVAILABLE。CB05合法旧包、历史GET/replay继续通过；replay零扫描仍有观察器见证。19份一致DB、10次DOM/PNG捕获；observer184条连续记录，scanner enter/exit/Popen各6次；自有4进程组退出、私有runtime/profile清理完成。

clean archive npm ci/build含tsc通过。未运行完整Core/P6 suite，不沿用旧52测试作为新执行结果，历史11skip保留。PNG解码不证明像素来源不能伪造，采集器/操作员信任边界保留。文本否定规则限于冻结直接措辞，不宣称理解所有自然语言。

## 冻结、复验与边界

新契约：工作树 docs/validation/p6-context-budget/REPAIR-CONTRACT-R01-R04.md；新冻结收据 REPAIR-R01-R04-FROZEN.json。旧v1/v2及F01–F07冻结文件保留历史字节，不重新解释旧冻结为新测试定义。

从候选clean checkout执行：

```sh
/opt/homebrew/bin/python3.13 scripts/p6/context_budget_run.py run --ref 518ef370575843f964644701172abb4b070d3966 --out '/path/to/new-isolated-bundle'
/opt/homebrew/bin/python3.13 scripts/p6/context_budget_gate.py --bundle '/path/to/new-isolated-bundle' --expected-candidate 518ef370575843f964644701172abb4b070d3966
/opt/homebrew/bin/python3.13 scripts/p6/context_budget_adversaries.py --bundle '/path/to/new-isolated-bundle' --expected-candidate 518ef370575843f964644701172abb4b070d3966 --out '/path/to/new-adversaries'
'<pinned-toolchain-python>' scripts/p6/context_budget_review_regressions.py --bundle '/path/to/new-isolated-bundle' --expected-candidate 518ef370575843f964644701172abb4b070d3966 --out '/path/to/new-regressions'
```

默认pinned Python/cache位置沿既有context_budget_run.py，缺依赖BLOCKED。未访问正式服务15180/18787或正式DB；[边界核对](../../../../../evidence/p6-context-budget-repair-518ef37-20261010/BOUNDARIES.json)证明main/runtime配置及两套旧正例与复评目录字节不变（64/64/537份文件），旧manifest摘要一致，item6=0。

**非作者复评/全新隔离复验 NOT_RUN；限定验收未签署。** 下一步统一绑定518ef37做非作者delta复评与全新复验。P6/V1仍未完成，P4实验路线停止；日常使用未放行。Checkpoint6fec85d限定签署边界不变；旧Memory/Authority冲突、实际注入、真实对话链、Validation真实FAIL及最终集成部署仍未闭合。
