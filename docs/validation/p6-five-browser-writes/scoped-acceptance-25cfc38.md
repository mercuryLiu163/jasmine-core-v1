# 五类浏览器写入：限定验收签署

2026-10-09，签署时间 `2026-10-09T14:34:25Z`。
**PASS_FIVE_BROWSER_WRITES_SCOPED / SIGNED_SCOPED_ACCEPTANCE**。
两名非作者的新版独立结论齐备且无阻断发现，由 root 汇总签署本文件所列范围。

## 固定绑定与范围

| 绑定 | 值 |
| --- | --- |
| 代码候选 | `25cfc38a6fc8ce40729ccb9d6551e38331c6fd89` |
| tree | `a6ee34f49eb184163b9d904fc98c0ffc75849202` |
| clean archive SHA-256 | `ff1974ce8e60797a2010f6361bdcf3c74920d9e07db72ce23c61f38e32409873` |
| 独立代码 reviewer | `/root/five_writes_independent_code_review`：**APPROVE**，原 R1/R2 闭合，0 blocking |
| 独立隔离 validator | `/root/five_writes_independent_isolated_validation`：**PASS_FIVE_BROWSER_WRITES_SCOPED** |
| 综合收据 | [joint-conclusion.json](../../../validation-runs/p6/five-writes-independent-round-02/joint-conclusion.json) |

签署仅覆盖 interpretation.correct、interpretation.rerun、review.approve、rule.supersede、rule.retire 的真实浏览器提交及冻结判据。保留 synthetic assistant.message / component_simulation provider 边界，不证明 native P2 capture、真实模型抽取/质量或全 T03。Core、Dashboard 产品源、Web 依赖与 schema 相对冻结 item6 release 没有变化。
本次报告文档提交不替换候选。`5d30e81` 的 REVISE_REQUIRED、作者失败尝试及其原证据继续保持原绑定。

## 独立执行与门核对

* 非作者从干净 detached clone / archive 重新构建；真实 Chrome DOM → Dashboard proxy → 真实冻结 Core，新私有 DB/随机身份/对象/端口，所有可选开关 0。
* 一次正式运行 **20/20 场景、139/139 必需断言**：五个 DOM 正向、五个 DOM stale conflict、五个原体原键 API replay、五个同键改体 API 拒绝。replay 不冒充浏览器提交。
* **122 项独立 raw 核对**：实际 POST/独立 GET、command SHA、原始来源、child/head/completion、Rule history/version/scope、另一 admin actor 的 audit 与 request ID/actor/status/scope/decision/error 关联；15 个 conflict/replay/changed-body 的全业务/source/operation 等表相等，仅排除 audit_log / api_keys。
* 新增 application→同 receipt/GET Resolution，Rule origin→本次批准 command→原 source/review/scope 两项必需门已在实际执行路径核实。**179 个退出门反例、20 个新增来源链反例全部拒绝**；22 项聚焦测试通过，无 skip。
* 顶层 ownership 独立实证：validator 的**真实 Chrome** timeout 和 normal Node exit 探针 **28/28 检查通过**，本轮 Node/Chrome/组/profile/CDP port 清理、partial stdout/stderr/progress/lifecycle 保留、独立旁观进程存活。探针使用候选实际 helper，属于具名负例，不追加到 20 场景或冒充完整 240 秒 driver 超时。
* reviewer 独立 Node 生命周期与 EPERM 分支探针、13 项真实 helper/门正反例通过；reviewer 没有执行真实 Chrome/Core，不继承 validator 的执行身份。

原件：[独立复评](../../../validation-runs/p6/five-writes-independent-round-02/review/REVIEW.md)；[独立隔离复验](../../../validation-runs/p6/five-writes-independent-round-02/validation/VALIDATION-REPORT.md)。

## 保留的非阻断 P3 与证据边界

**R3 / P3 未修复**：在 communicate 先 timeout、随后 owned group 仍有成员而 signal 被拒绝的组合故障中，TimeoutExpired 已捕获的 partial stdout/stderr 会丢失。reviewer 的独立 signal-EPERM 注入探针可复核此现象；不是实际 Chrome/Darwin 权限失败复现。
该分支仍保留 trace-progress 与 timed_out/cleanup_error/cleanup_passed=false 的生命周期失败收据，正确拒绝 PASS；原 R1/R2 的安全门与实际来源关系不受此诊断缺口影响。reviewer 明确认定为非阻断，本限定签署接受并保留这一限制，**不声称所有失败组合均保留完整日志**。未为本签署改候选或补做伪造成功证据。

草稿/key 精确比较和 reload 由冻结 CDP 执行工具实际比较；独立 wire/screenshot 核对支持其运行事实，但完整 draft 数组/reload 时刻未另外落盘，保留 validator 的原始限定。Chrome 为自动化操作，登记 human bearer 证明凭据控制权，不证明物理人点击。

## 封存与部署状态

| manifest | 逐项重算 | SHA-256 |
| --- | --- | --- |
| `review/manifest.json` | 39 文件一致 | `ce647170230fdd8d316c7f770bdc8e9cafc20b7dcd07033a02bb7dbbb74c2d5a` |
| `validation/validation-manifest.json` | 61 文件一致 | `012a92901ddbec2a1e6ee440e44657755bebff4d7848b0b6a141d87c82977193` |
| `validation/run-01/manifest.json` | 31 运行文件一致 | `26f99cbda886e48ab6fdf2afe11cc111fc0ea3452ae495cf9553fd556a50f308` |

[本轮边界](../../../validation-runs/p6/five-writes-independent-round-02/boundary/postflight.json)核对旧证据 **251 个唯一文件**与实际部署 **53 个绑定文件**均无漂移；primary/item6 HEAD/status 及原未跟踪文件保持。实际部署 runtime config 摘要不变，原 Core PID **48822** / Dashboard PID **49023**，配置及进程 item6 开关均 **0**。root 只读核对 config/PID/flag/hash；没有读取实际 token/DB、合并 main、重启/替换实际服务或操作其他服务。

本轮私有 Core PID 60620 / Dashboard PID 60621 与端口 56493/56494 已结束；profile/runtime 清理。新运行入口为临时入口，不是新增常驻部署。

**五类浏览器写入覆盖切片已限定验收完成。日常使用仍未放行，11 项历史 skip 不变，整个 P6/V1 未完成。**
native T03、T04 注入 receipt、T05 剩余负例、T06 真实 FAIL、manual-reapply 等任务继续按原清单处理；没有因本签署自动升级或启动它们。
