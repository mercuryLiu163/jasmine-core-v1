# P6 五类浏览器写入 — 实现者运行报告

2026-10-09。**实现者运行通过；独立 review / validation 待执行。**
本报告不签署最终验收，不放行日常使用，也不升级 P6/V1 整体结论。

## 固定源与范围

* 分支：`p6-five-browser-writes`，隔离工作树 `_worktrees/p6-five-browser-writes/`。
* 运行候选：`5d30e819320ee278f080a6301e74ffefe136ffa3`，从 `git archive` 提取并重新构建 Dashboard；tree、archive 与实际 assets 摘要见运行 `acceptance.json`。
* 相对 item-6 实际 release `79448dff91dd7a702f91157766e0ff8ad9fb7352`，Core、Dashboard 产品源与 Web 依赖没有变化。本轮新增判据、历史普查、隔离浏览器工具和退出门测试。
* 只补 interpretation.correct、interpretation.rerun、review.approve、rule.supersede、rule.retire 的浏览器提交证据。此前原件不改写、不继承为本候选的独立结论。

先冻结的路由/判据见 [plan.md](plan.md)。[历史普查](historical-gap-census.json)绑定 INDEX 和 146 份请求日志，五条目标路由均为零 POST；498 条历史 POST 均有可辨识 path，原件摘要没有漂移。普查只回答覆盖缺口。

## 实际运行结果

正式实现者运行：[five-writes-attempt-06](../../../validation-runs/p6/five-writes-attempt-06/result.md)。
结论 `PASS_FIVE_BROWSER_WRITES_SCOPED`，**20/20 场景、137/137 断言**。
真实 Chrome 进行了 10 次 DOM 提交；另外 10 次 replay/改内容拒绝为单独标识的 operator API 检查。

| 路径 | DOM 成功 | DOM 旧依据冲突 | 同体同键 replay | 同键改内容 |
| --- | --- | --- | --- | --- |
| Correct | 201；同来源 EXTRACTED 子解释，head rev 2；不自动写 Truth | 409 interpretation_not_current | 200；原 receipt，无第二次写入 | 409 idempotency_conflict |
| Re-run | 201；同来源子解释，head rev 3；有 completion/config；不自动写 Truth | 409 interpretation_not_current | 200；原 receipt，无第二次写入 | 409 idempotency_conflict |
| Review approve | 201；APPROVED rev 2；实际 CREATE_RULE/APPLIED，有 Resolution 来源链 | 409 revision_conflict | 200；原 receipt，无第二次应用 | 409 idempotency_conflict |
| Rule supersede | 200；ACTIVE v2/rev3；原 scope、旧版本保留，新 origin/history | 409 revision_conflict | 200；原 Event，无第二个版本 | 409 event_id_conflict |
| Rule retire | 200；RETIRED v1/rev3；原版本与历史保留 | 409 revision_conflict | 200；原 Event，无第二次停用 | 409 event_id_conflict |

每个成功请求都核对浏览器 POST/body/request ID、独立 GET 对象与 command、command body SHA、原始来源、另一个受控 admin 身份的 audit，以及强制页面 reload 后的精确对象 GET。共有 26 次显式独立 GET 读回；网络响应完整捕获，没有 response-capture error。
每个冲突对象先在浏览器中准备草稿，再由独立 API 竞争写入制造真实旧依据；409 后草稿/重放键保持，未自动重试，所有 business/source/operation 表与提交前一致（审计及 key usage 元数据不纳入该相等比较）。

## 证据与负例

运行 manifest SHA-256：`ce8956b4d3547d03d14113bef1211dbe9f04dacf3a8ce291c21c51c2a6cee7ac`。
29 个绑定文件重算一致。bundle 包含 scenario、click trace、请求/响应、truth-before-after、source/command/audit mappings、独立 GET 原文、10 张截图、provider trace、构建/运行日志、acceptance、result 和 manifest。

[实现者证据复核](../../../validation-runs/p6/five-writes-verification/verification.json)：15 项退出门测试通过；逐个删去 required row/assertion、false/unknown、重复、空记录均拒绝。
[负门收据](../../../validation-runs/p6/five-writes-verification/gate-negative-receipt.json)来自原记录的内存副本，明确标记 `ADMISSION_FALSIFICATION_ONLY_NOT_AN_ACCEPTANCE_RUN`，不改写正式 bundle。
这些门测试不算浏览器验收，也不替代独立 review/validation。

失败尝试全部保留，未把任一 FAIL 改写为 PASS：

| 尝试 | 候选 | 实际停止点 |
| --- | --- | --- |
| 01 | cdbeeeb | npm 全局 cache 权限拒绝；Core/浏览器未启动 |
| 02 | d277a2a | 导航等待返回 DOM Node，无法作为 CDP returnByValue 的布尔判据 |
| 03 | 8d597ef | Correct 成功；冲突审计真实 decision=allow，工具误期望 error |
| 04 | 6bbf5bc | Correct/Re-run 通过；Review 草稿内容不精确匹配服务器候选，真实 400 拒绝 |
| 05 | 0e76210 | Correct/Re-run/Review 全部通过；工具构造了不存在的单 Rule 页面路由 |
| 06 | 5d30e81 | 修正为实际 scoped rules 路由；五条路径全部通过 |

修复均限于隔离工具/fixture，不改变目标路由、成功/拒绝标准或产品。各尝试仍绑定自己的候选、原始输出及 manifest，不能引用 06 来覆盖先前 FAIL。

## 模拟边界与身份

Core 路由、权限、revision、事务、Resolver/Review、Authority、审计及 replay 都执行冻结产品代码，经本轮 Dashboard proxy 到实际 Core。只有 Interpreter provider 使用公开构造注入接缝，标为 `component_simulation`；输入是显式 synthetic assistant.message，不伪装 native human prompt。
Provider trace 为 6 次预置 extract + 1 次真正的 rerun store 调用，rerun 原来源 ID 一致；不证明真实模型质量、原生 capture 或完整 T03。
Chrome 是自动化操作；登记 human bearer 证明凭据控制权，不证明物理人点击。human 与另一个 operator-admin 的非敏感身份见 `acceptance.json`，token 不进入共享证据；浏览器 local/session/IndexedDB 均为 0。

运行 Core `127.0.0.1:55283` / Dashboard `127.0.0.1:55284` 为临时入口，已关闭；PID 52910 / 52912 已不存在。私有 runtime DB/keys/profile 已清理，只有合成验证对象的脱敏收据保留。这不是新的常驻部署入口。

## 实际部署与最终状态

[前后边界收据](../../../validation-runs/p6/five-writes-boundary/postflight.json)确认：

* 原部署 Core PID 48822 / Dashboard PID 49023 不变，原 runtime config 摘要与冻结值一致，进程 item-6 flag 仍为 **0**。
* 旧 stage2 manifest 及其 53 个绑定文件逐一核对一致，原 INDEX/146 日志原件一致。
* primary 与 item-6 工作树的 HEAD/status 不变，原 untracked 文件仍在原处；未合并、未重启实际部署、未打开实际 DB、未读取实际凭据。
* 日常使用 **未放行**；11 项历史 skip 不变，本轮未运行原全套 suite 或宣称 skip 通过。

交接要求“该项还需独立 review/validation 及自己绑定的干净候选”。当前执行者记录不足以代替这两项结论。独立子 agent 的授权问题已提出，尚无回复；依当前委派规则不能自行启动。候选与运行证据现已可供独立审查，最终验收状态为 **PENDING_INDEPENDENT_REVIEW_VALIDATION**。


## 后续修复候选（不改写上述历史运行）

独立评审的两项 P2 已由 root 修复在 `25cfc38`，修复者新版运行20/20场景、139/139断言。
其生命周期负例和来源门反证见 [repair-results.md](repair-results.md)。
当前仍 **PENDING_NEW_INDEPENDENT_REVIEW_VALIDATION / 未签署**；原候选、报告和原正式
bundle保持原样，新版 PASS 不替代新独立结论。
