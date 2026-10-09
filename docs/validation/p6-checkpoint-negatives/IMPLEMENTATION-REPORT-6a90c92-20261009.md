# P6 旧／异常 Checkpoint：实现与作者验证

状态：**IMPLEMENTED / AUTHOR GATE PASS / INDEPENDENT CONCLUSIONS PENDING**。
限定验收未签署；P6/V1 整体未完成，原 11 项跳过保留，日常使用未放行。

本报告承接用户审查通过的 `5079948` SPEC ONLY。冻结规格、矩阵、来源清单及冻结收据
均保留原字节；冻结收据中的 NOT_STARTED/NOT_RUN 是冻结时的历史状态。本报告记录后续
实现与作者运行，不回写历史结果，不把作者测试或当前协调对话当成非作者独立结论。

## 固定对象

| 对象 | 绑定 |
| --- | --- |
| 冻结规格 | `5079948808d4c8aa9cb5763c747def422c12c055` |
| 基线 | `f8de226ad51e9b7608b4b87dc9bcd60a86db407f` |
| 实现候选 | `6a90c92c07e4c2fe7e85dcbdba4921759b78f32b` |
| 候选 tree | `090e2211716ec5386ef08a5ec0572033eafbc963` |
| clean archive SHA-256 | `1f00f2bb9edf0f1ed21a82980d614782a04e44e332ef3c4686ddd9e920092d89` |
| 矩阵 SHA-256 | `5e3fcdd9808437dbad7908b52255c52a73b94ee4bf4496b6da9f29b83c954514` |
| 作者运行 manifest SHA-256 | `2138d0d57d9c0d739cfc0ee2e4ac8a8e0a10ed1d19fe1a18835cec763f242ea2` |

工作树：`/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p6-checkpoint-negatives`，
分支 `p6-checkpoint-negatives`。报告文档提交与上述代码候选分开；后续评审、复验统一绑定代码候选。

## 实现变化

- `App.tsx` 按完整 Checkpoint 路由／Resume ID 重建视图，防止切换对象后保留旧输入和旧记录。
- `ContinuityViews.tsx` 显示本条历史 Resume 的 freshness/workspace/reconcile，说明重新读取不会
  重新比较；STATE_EQUAL + UNKNOWN/MISMATCH 仍按实际收据显示需要 reconcile。
  Checkpoint 历史 GET 明示不执行完整性比较；代理拒绝不再推断旧版本/schema 或记录不存在。
- 新增 Python 隔离准备／清理、真实 Chrome DOM 采集、原始收据判定三份工具及聚焦测试。
  Core/API/schema/代理白名单没有产品代码改动。比较和 build 只由专用验证操作者在私有 Core 调用。

正常业务对象通过实际 Core HTTP 创建。Registry/身份/key 是私有测试初始化；名称和 source
明确标为 SYNTHETIC fixture，不冒充真实日常任务或收益证据。CP13–22 的 11 个变体使用停止
基础 Core 后生成的 SQLite 备份分叉；离线注入有原记录、SQL、参数、完整 schema、digest、
foreign-key/integrity 检查与 REAL_CORE_CORRUPT_STORE 标签，原库及生产 scanner 不被替换。

## 作者运行结果与证据

作者 bundle：`/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/evidence/p6-checkpoint-author-run-03/`。
`result.json` 为 PASS：**43/43 变体、155/155 场景判据、301/301 公共不变量求值通过**。
101 个判据定义和 26 个场景不变。raw-result、matrix、browser、操作收据、DB、audit、
observer、真实 click trace、109 份 DOM、109 张截图及构建日志共 536 个文件由 manifest 绑定。

| 冻结范围 | 变体 | 作者结果 |
| --- | ---: | --- |
| CP01–12 正常／旧收据／partial／选择与切换 | 12 | PASS |
| CP13–22 离线损坏分叉／synthetic removed | 11 | PASS |
| CP23 合法但不存在的 ID | 2 | PASS |
| CP24 非法 ID 本地拒绝 | 2 | PASS |
| CP25 逐个缺少读 scope | 13 | PASS，入口边界见下 |
| CP26 自有 P3 off／代理 POST 拒绝 | 3 | PASS |

缺席证据按实际对象／请求映射：CP09 没有 Checkpoint；CP13–21 没有成功 Resume；CP23 没有
目标对象；CP24/26 没有对应 Core 请求。已有对象通过实际 source/command Event GET 核对，
不存在的成功对象不补造来源链。没有目标链时只读合法前置 source 并记录缺席理由和计数。

“重放不扫描”用实际 `scan_workspace` 函数入口／返回的 CPython profiler，加实际 scanner
`subprocess.Popen` audit hook 观测。CP10 的重放 HTTP 200，在完整 request_start/end 窗口内
函数入口、返回、Popen attempt 均为 0，业务／command／receipt／key 无追加；新 build 有 1 次
入口／返回／Popen 正向控制。GET/拒绝同样核对窗口，不能用“没有成功日志”代替零调用。

CP12 只延迟一个真实 GET Response 并移除该请求的 AbortSignal，以强制旧响应晚到。
body/header/Core 状态不被伪造；这个传输干预单独标注。切换前后实际 DOM、字段值及截图
证明 B 记录／输入没有被 A 的晚到响应覆写；不扩展为所有并发或网络故障覆盖。

CP25 中缺 `objects:read` 的两个 token 被实际 `GET /v1/projects?limit=1&offset=0` 登录校验
拒绝，浏览器显示 403 forbidden_scope，展开 details 指向 objects:read。登录尚未成立，因此
目标页 GET 的零请求证据明确保存；同 key 的实际目标 Core GET 另行测得 403。
其余 **11** 个变体完成登录，在目标 Checkpoint/Resume 页测得实际 403 与精确 details。
不把两个登录拒绝宣称为已发生的目标页浏览器读取，不绕过登录、不提升浏览器 key 权限。

公开 `GET /v1/health` 与 `GET /v1/meta/schema` 在此候选不写 Core audit。它们保存实际
Core 入站／出站与零扫描窗口，并核实 audit 缺席；其他浏览器请求按 request_id、actor、
method/path、status/error 与真实审计关联。代理／本地拒绝以实际零入站为证，不能伪造 audit。
管理员审计读经独立直连 Core 通道，浏览器没有 `/v1/audit` 或 `/v1/auth/*` 权限。

补充作者核对：`evidence/p6-checkpoint-author-receipt-audit-03/` 下保存可重跑的
`verify_receipts.py` 与 `receipt.json`。从实际 SQLite 备份重算 **153** 次逻辑摘要，核对
**53** 份唯一操作文件与 raw 引用；重新构建候选 git archive、核对全部 manifest、DOM、
来源字节与冻结四份文件，结果 PASS，核对前后作者 bundle 摘要不变。此核对仍由作者完成。

结果生成器在本轮真实正向 bundle 的内存副本上执行 8 项篡改自检：漏变体、UNKNOWN→SAME、
漏拒绝后的计数、换对象 ID、漏 fault 标签、漏 DOM、错 candidate、错 archive。
全部拒绝为 FAIL/BLOCKED，详见 `harness-selfchecks.json`；不计入 43 个验收变体。

## 验证与运行环境

- 前端完整测试 520/520，34 个测试文件；包含本切片增加的 5 个显示／切换测试。
- Python harness 聚焦测试 11/11；现有 Core continuity 测试 8/8。
- clean archive 内独立 `npm ci --no-audit --no-fund`、TypeScript 检查及 Vite build 通过；
  原始输出为作者 bundle 的 `npm-ci.log` / `build.log`。本机完整测试日志保存在补充核对目录，
  不冒充 archive 内独立测试或非作者证据。
- Python 3.13.15，Node v26.10.0，npm 11.19.1，Chrome 154.0.8037.98；lockfile SHA
  `e930eac9de8d1a8ff4e094d1ac917c0433302dc2c02e2928a7426fe6220a9e1c`。
- 私有 DB、workspace、端口、npm cache、token、Chrome profile；真实 Core health 为 schema 12。
  27 个自有进程组均证明已退出，profile/runtime 删除，原始失败和成功收据保留。
- 自有 Dashboard 仅 P3 read 为 1（专门 off 实例为 0）；item 6/P4/P7 开关全为 0。
  仅只读核对既有部署 runtime 配置，其 web_confirmation_flag 仍为 0、冻结 release 为 79448df。
  未修改配置、数据库、正式证据或既有服务，未重启／访问实际部署入口，未合并 main。

可复现命令（非作者复验须用全新 out；运行身份由实际执行者证明，role 参数不能证明独立性）：

```sh
cd '/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p6-checkpoint-negatives'
/opt/homebrew/bin/python3.13 scripts/p6/checkpoint_negatives.py run \
  --ref 6a90c92c07e4c2fe7e85dcbdba4921759b78f32b \
  --out '<全新隔离证据目录>' --role independent-validation
/opt/homebrew/bin/python3.13 scripts/p6/checkpoint_negative_gate.py \
  --bundle '<本次证据目录>' \
  --expected-candidate 6a90c92c07e4c2fe7e85dcbdba4921759b78f32b
```

## 失败记录与修复

1. `evidence/p6-checkpoint-author-run-01/`，候选 af71d7c，43 个槽均 BLOCKED：首个扫描
   子进程没有继承归档 `src` 导入根，checkpoint_scan_failed/worker_exit 1，尚未进入矩阵
   浏览器执行。早期失败的 scanner 原始 observer 未被旧清理路径保存，此证据缺口明确保留。
   前后 DB／实际 HTTP 失败与审计仍保留；不声称此轮零调用或完整 Gate 运行。
2. `evidence/p6-checkpoint-worker-import-diagnostic-01/` 以同一 af71 archive 的实际 worker
   命令对照：没有 PYTHONPATH 失败，有 archive/src 成功。是诊断组件证据，不算矩阵通过。
   f450466 显式绑定子进程 PYTHONPATH，并在任何早期退出保存 HTTP/observer/final DB。
3. `evidence/p6-checkpoint-author-run-02/`，候选 f450466，14 PASS / 15 FAIL / 14 BLOCKED：
   工具误要求公开探测的 audit；scope 登录拒绝未按真实入口采集；失败后的密码输入追加了
   旧输入；同一路由重复访问没有新的首次 GET。6a90c92 修复原始采集及判定，保存 failure
   DOM 与失败收尾、实际选择替换输入、先卸载旧路由再重新进入，仍要求两次真实 GET。
   既有登录防线保持原状，拒绝的 browser target GET 缺席单列，403 details 实际展开核对。
4. 修复后按完全相同冻结矩阵从全新 archive 重跑全部 43 个变体，没有选取成功子集或改阈值。
   前两轮和诊断目录不重写；npm 初次本机安装遇到旧 cache 权限错误时改用私有 cache，
   未修改用户 cache 权限或全局配置。

## 未完成的验收门

同一候选仍需要非作者代码评审和非作者 clean archive 隔离复验。两项结论齐备、阻断项关闭
后才可提出限定签署；本切片尚无这两项独立结论。本报告不签署、不合并、不部署。
覆盖仅为冻结 26 场景／43 变体，算法、coverage、extra_paths 不兼容分支及其他损坏结构
没有运行覆盖声明。实际部署入口验收和日常放行仍需后续单独安排。
