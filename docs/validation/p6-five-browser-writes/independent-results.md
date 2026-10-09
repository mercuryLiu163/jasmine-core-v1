# 五类浏览器写入：独立评审与隔离复验汇总

2026-10-09。**两项独立结论齐备；综合结论 REVISE_REQUIRED，限定验收未签署。**
签署状态：`HELD_REVIEW_FINDINGS_NOT_SIGNED`。按用户“发现问题先汇总”的要求，本轮没有修复候选、修改正式证据、部署或开启 item 6。

## 统一源绑定

| 绑定 | 值 |
| --- | --- |
| 候选 | `5d30e819320ee278f080a6301e74ffefe136ffa3` |
| tree | `0e781ea49ce28ac0f5caded4d08864fe5bdd7dd8` |
| clean archive SHA-256 | `ae64fbbf697cd9e9b07335a2cbed12c26c7793ea2e7d8351a06c86d73776e701` |
| 非实现者 code reviewer | `/root/five_writes_independent_code_review` |
| 非实现者 validator | `/root/five_writes_independent_isolated_validation` |

两名本轮新 agent 分别执行代码评审与隔离复验，结论均绑定上述候选。
后续文档提交只交付本汇总，不替换候选，也不从实现者或历史运行继承 PASS。
根记录见 [joint-conclusion.json](../../../validation-runs/p6/five-writes-independent-round-01/joint-conclusion.json)。

## 两项独立结论

| 工作 | 结论 | 实际证据 |
| --- | --- | --- |
| 独立代码评审 | **REVISE_REQUIRED，两个 blocking P2** | 私有 archive 静态审查、真实 Core/Dashboard 合同核对、15 项门测试、两个隔离反例；不充当浏览器复验 |
| 独立隔离复验 | **PASS_FIVE_BROWSER_WRITES_SCOPED（运行事实）** | 干净 detached clone → archive → 新构建，首次真实 Core/Chrome；20/20 场景、137/137 候选断言、119 项独立 raw 核对、177 个退出门内存负例全部拒绝、15 项单元测试 |
| 综合限定签署 | **HELD / 未签署** | 运行成功不能消除候选工具的两项评审缺陷 |

原件：[独立代码评审](../../../validation-runs/p6/five-writes-independent-round-01/review/REVIEW.md)；[独立复验报告](../../../validation-runs/p6/five-writes-independent-round-01/validation/VALIDATION-REPORT.md)。

## 问题汇总与修复边界

### R1 / P2：超时分支可能遗留本轮 Chrome/profile

位置：`scripts/p6/five_browser_writes.py:145` 与 `.mjs:65,71`。
Python `subprocess.run(timeout=240)` 超时杀 Node，越过负责 Chrome/profile 清理的 JavaScript finally；Python 只登记 Core/Dashboard。候选没有顶层浏览器生命周期回收及超时 trace/清理收据。

[生命周期反例](../../../validation-runs/p6/five-writes-independent-round-01/review/timeout-lifecycle-counterexample.json)使用等价 Node→自建 sleeper child 模型，证实 Node finally 未执行、子进程仍活。模型 child 已按确切 PID 清理；没有启动真实 Chrome/Core，不把这个模型称为真实 Chrome 超时复现。
后续修复应由顶层 runner 回收自己拥有的浏览器进程/profile，在超时路径保留失败与清理收据，并补必要的失败分支负例；禁止全局 pkill。

### R2 / P2：Review 的 application 与 Rule origin 未纳入成功门

位置：`scripts/p6/five_browser_writes.mjs:57`（相关 required checks 在 Python 中）。
现有成功谓词没有检查 `source_applications` 的 source→canonical Resolution，以及新 Rule origin→本次 review command、command→原 source 的完整关系。
[内存副本反例](../../../validation-runs/p6/five-writes-independent-round-01/review/lineage-gate-counterexample.json)把 Rule origin 改错、application 表清空，现有相关谓词仍通过。原始 bundle 没有修改，这不是新的 Core 接受运行。

独立复验已补证**本次实际 Review 链正确**：新 Rule origin 指向批准 command，command 指向原 source，唯一 application 指向同一 receipt Resolution；见 [独立链核对](../../../validation-runs/p6/five-writes-independent-round-01/validation/review-source-chain-independent.json)。
后续修复应把这些关系加入实际 positive 断言、required check 名单及拒绝错误链的负例。补证本次运行不修复候选退出门。

两项发现均在验证工具，本轮没有产品代码缺陷或修改产品的结论。后续如决定修复，必须使用新候选并按影响范围重新独立评审/复验；当前 `5d30e81` 的 REVISE_REQUIRED 保持原样。

## 原件封存与边界

| 原件 manifest | 逐项重算 | SHA-256 |
| --- | --- | --- |
| `review/manifest-final.json` | 18 文件一致 | `b868b8a769984625b510ffd421a41447d6af0ed195c0c73866195d187d2b6e8b` |
| `validation/validation-manifest.json` | 44 文件一致 | `7da5e2449a9da30c1f283e342993aa3f8749bbd8e85a5fd583a5491ec7e04eaa` |
| `validation/run-01/manifest.json` | 29 运行文件一致 | `f9801b4586d42b0b2caf6f3eda22f7a48d81e411809d9aefda7a0e349a544b63` |

本轮新证据保存在 `validation-runs/p6/five-writes-independent-round-01/`。validator 自己 raw 核对器的两个调试错误版本也保留；修正只作用于它自己的收据核对器，不修改候选、标准或正式浏览器运行。

独立 raw 复核覆盖实际 POST/GET、command SHA、原 source/parent、object/version/history、operator audit，以及拒绝/replay 的全业务/source/operation 快照相等。草稿/key 精确比较与 reload 时刻部分仍依冻结执行工具的实际比较结果；完整 draft 数组/reload 时刻没有独立落盘，保留 validator 明示的证据边界，不把截图或单一 GET 升格为全部行为证明。

provider 为明确 `component_simulation`，输入为 synthetic assistant.message；没有 native capture、真实模型质量、全 T03 或整个 P6/V1 验收结论。

[边界收据](../../../validation-runs/p6/five-writes-independent-round-01/boundary/postflight.json)核对旧实现者 133 个文件、实际部署 53 个绑定文件及旧 INDEX/146 个日志全部无漂移。部署 config SHA 不变；原 Core PID 48822 / Dashboard PID 49023 不变，进程及配置 item 6 开关均 **0**。primary、item-6 工作树的 HEAD/status 与原有未跟踪文件保持原状；没有合并或部署操作，日常使用仍未放行，11 项历史 skip 不变。

复验私有 Core PID 55598 / Dashboard PID 55600 与端口 64421/64424 已退出/关闭，正常路径私有 runtime/profile 清理。实际部署凭据/数据库未读取。原实现者报告与所有正式旧证据保持原件，本汇总新增独立结论，不改写其历史状态。
