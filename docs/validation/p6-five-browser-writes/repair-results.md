# 五类浏览器写入：R1/R2 修复结果

2026-10-09。**CLOSED_LOCALLY / PENDING_NEW_INDEPENDENT_REVIEW_VALIDATION。限定验收未签署。**
修复者为本对话 root；本报告、聚焦测试和新版隔离运行均是修复者证据，不冒充非作者结论。
原候选 `5d30e81` 的 REVISE_REQUIRED 与原独立复验 PASS 均保持原绑定。

## 新候选

* 修复代码候选：`25cfc38a6fc8ce40729ccb9d6551e38331c6fd89`，clean commit/archive。
* 范围：验收工具、准入谓词、聚焦测试和 plan 修复增量；Core、Dashboard 产品源码与 schema 未改。
* 正式修复者新运行：`validation-runs/p6/five-writes-repair-root/run-25cfc38/`。
* 实际结果：20/20 场景、**139/139 断言**；10 次浏览器提交，API replay/改内容仍是单列证据。
* 新运行 manifest：31 个文件摘要一致；SHA-256 `612ccd0f3a7e3954f7503f28430a7b251664dab1eb7186f202dc3e3d95f85f31`。
* 后续报告提交不改变这个候选，也不为其添加独立签署。

## R1：顶层持有浏览器生命周期

Python 在启动前创建自己的私有 profile，用 start_new_session 启动 Node，Chrome 继承
本轮进程组。正常退出与超时都由顶层按记录的数字 PGID 清理、等待并核对成员消失，
然后删除本轮创建的 profile。没有按名称搜索或全局 pkill。
Node 保留每个断言后的 trace-progress；顶层保留 stdout/stderr 和 browser-lifecycle，
超时/清理失败不能通过门。已存在或其他运行的 profile 不作为输入。

验证：正常父进程退出遗留子进程、真实 timeout 两种进程负例均通过，旁观进程未被停止。
另一个**真实 Chrome** 超时探针观察到 DevToolsActivePort 就绪、随后超时，精确 Chrome
PID 和组成员消失、profile 删除、stdout/失败生命周期收据保留。
探针是具名负例，不是 20 场景验收 run。
正式新版运行的 browser-lifecycle 也显示 trace_exit=0、group_gone/profile_removed/cleanup_passed=true。

## R2：成功门强制来源链

新增两个必需检查：

1. `source_application_canonical_resolution`：原 source 的唯一 application → 本次 receipt
   和独立 GET 的同一 Resolution，Resolution 的 source 必须一致。
2. `applied_rule_origin_review_command_source`：新 Rule origin → 本次 review.approved command
   → payload 中的原 source/review，并核对相同 project/task scope。

冻结 Core 的 Review command 顶层 source_event_id 为 null，source 指针在 payload 中；
按真实合同核对，没有修改 Core 来适应工具。
Python CHECKS 必需表与实际 JS positive 路径同时加入这两项，少任何一个不能 PASS。
新增纯谓词直接用于浏览器执行路径，不是与执行代码分离的测试副本。

反证：在新运行的内存副本上清空 application、改 canonical Resolution、改 Rule origin、
改 command payload source，实际谓词均拒绝；分别移除两个必需检查，退出门均拒绝。
原正式运行和新正式运行原件未修改。

## 检查与保留的修复失败

* 22 项聚焦测试通过（原门 15 项 + 1 项新必需门测试 + 6 项生命周期/来源谓词测试），无 skip。
* Node 语法、Python 编译、git diff --check 通过；新 archive 的 Web typecheck/build 通过。
* 没有重跑整个 P6/Core 回归套件；不继承旧全套计数或升级 11 skips。
* 首次聚焦测试的正常退出子进程错误继承 stdout，导致 communicate 等待；失败在本对话
  工具输出中，未伪造原始日志。测试修为与真实 Chrome launcher 一样的 ignored stdio。
* 第二次测试和首个真实 Chrome 探针捕获本轮清理实现的空组 signal 误判：进程已经退出，
  Darwin 对空组报 EPERM，清理未写完收据。这是**本轮修复中出现的新错误**，已按数字
  PGID 实际成员查询处理；有成员却不能 signal 时仍失败，绝不直接忽略权限错误。
  失败测试日志与首个探针均保留，不算成功证据。

原件索引（相对仓库根）：

* `validation-runs/p6/five-writes-repair-root/unit-tests-attempt-02.log`（失败）
* `validation-runs/p6/five-writes-repair-root/unit-tests-attempt-03.log`（22 OK）
* `validation-runs/p6/five-writes-repair-root/actual-chrome-timeout/`（失败，具名说明）
* `validation-runs/p6/five-writes-repair-root/actual-chrome-timeout-02/`（真实超时清理负例）
* `validation-runs/p6/five-writes-repair-root/new-run-lineage-falsifications.json`
* `validation-runs/p6/five-writes-repair-root/repair-verification.json`

## 原证据与部署边界

旧实现者 run、独立评审/复验的四个 manifest 摘要无变化；120 个绑定文件条目全部核对一致
（可能含不同 manifest 间重复引用，不冒充 120 个唯一文件）。实际部署 stage2 的53个
绑定文件无变化，runtime flag=0，原 Core/Dashboard PID48822/49023仍存在。
正式库与凭据未打开，main未合并，实际部署未重启或换 release，日常使用未放行。
provider仍为 component_simulation，非原生 P2 capture/真实模型质量，全 P6/V1 未完成。

下一步仅为针对 `25cfc38` 的非作者独立代码复评与新隔离复验：重点反证 timeout 清理、
空组错误处理、application/Rule origin 必需门与新正式原始收据。两项结论齐备前保持 HELD。
本轮没有启动子 agent，也没有将 root 的修复者运行改称独立验证。
