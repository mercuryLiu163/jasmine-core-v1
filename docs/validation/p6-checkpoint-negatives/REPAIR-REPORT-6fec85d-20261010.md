# 首读与刷新独立收据的必需绑定修复

**REPAIRED / AUTHOR VERIFICATION PASS / NEW INDEPENDENT CONCLUSIONS PENDING**。
限定验收未签署，P6/V1 未完成，item 6=0，日常使用未放行。

接受 `evidence/p6-checkpoint-review-96613e0-20261009/REVIEW.md` 的 REVISE_REQUIRED。
删除全部 41 份刷新观察及 82 份 DOM/PNG、或复用首读文件仍被旧 CLI 接受，属于
首次发现的原有遗漏。复核中的 cf3c655 对照也接受，非 96613e0 引入回归。
旧正向 43/155/301 与旧反证 74/74 结果保留，不能据此签署限定验收。

## 候选绑定与范围

| 绑定项 | 值 |
| --- | --- |
| 代码候选 | `6fec85d7a396896da428a2a5ff59e683c3300cfd` |
| tree | `4c070385db3fce2279d2f9186142963b6bf46958` |
| clean archive SHA-256 | `dc2b1f702f1167ab222b458aa15e85a2bcbe49a06bb303ab80747ce30c5fa96c` |
| 冻结规格 | `5079948808d4c8aa9cb5763c747def422c12c055` |
| matrix SHA-256 | `5e3fcdd9808437dbad7908b52255c52a73b94ee4bf4496b6da9f29b83c954514` |
| 作者 run-12 manifest SHA-256 | `aacd556bbdead06df211ee6807e07a634aaa1a387711e21955b00010d38136a4` |
| 反证 manifest SHA-256 | `52fffe628c3a5aba64458248b8d9923aa4f58df45b7db3f9db09f4b9cb8523cf` |
| 补充收据 manifest SHA-256 | `69d67d9e37a50ca451132f01c853b7eba7d3af01c99172bb9dd8373d93ff2228` |

报告另作文档提交。后续复评／复验必须绑定此代码候选，不能把报告 HEAD 当作候选。
只改验收 gate、组件反证及聚焦测试；Core、schema、产品 UI、代理白名单未改。
冻结四文件和其绑定的 remaining task 文件逐字节保持不变；仍为 26 场景、43 变体、
101 判据定义、155 求值及 G1–G7 共 301 求值。组件反证不扩充矩阵覆盖声明。

## 必需收据与独立性

每页从已经绑定 actual operations/selectors 的场景定义推导捕获角色。普通页面必须
恰有首读；声明刷新的页面必须恰有首读及刷新。CP12 切换初始 A 页同样必须保有自己的
首读、刷新；切换 B 的释放前后分别绑定自己的两个角色。缺失、追加及倒置均拒绝。

物理必需路径按 scene/page/role 推导，与采集器实际命名逐项匹配，不从提交者剩余的
observations 或 index 数量推导。DOM 和 PNG 分别强制匹配本角色的路径；刷新不能引用
首读文件、借普通页或另一场景文件。捕获阶段不得重叠或倒置，原有导航、实际刷新点击、
完成响应、request_id 及 Core observer 窗口继续强制绑定。显示门也检查角色数量。

不可变历史记录在重新读取后可以完全相同，因此不要求两份 DOM 或 PNG 的 hash 不同。
这项修复要求独立捕获收据、正确角色及实际请求窗口，不以固定“500 个文件”替代绑定。
缺席／拒绝保留原有缺席证据；真实 scanner 的重放零调用观测及原来源链门未放宽。

## 新候选作者验证

`evidence/p6-checkpoint-author-run-12/` 从固定 clean git archive 做 npm ci/build，使用
私有 Core、schema 12、真实生产 scanner、隔离 DB/workspace/端口/token/Chrome profile。

- 作者总体及正式 CLI 重算 PASS：43/43、155/155、301/301。
- 必需物理门 PASS：43 个场景角色、154 份 DB、53 份操作、500 个文件引用；总 manifest
  538 份。41 个应刷新页面角色各自具备两套 DOM/PNG 与实际请求关联。
- 聚焦 unittest 31/31 方法通过：原 23 项及 8 项收据数量、文件别名／借用、切换初始页、
  释放前后角色、时间顺序、相同内容合法对照和拒绝判定 JSON 往返稳定性测试。
- 27 个自有进程组全部退出；私有 runtime/profile 删除。未启动或重启既有服务。

`evidence/p6-checkpoint-gate-regressions-6fec85d-01/`：**86/86 全部拒绝**。
原有 74 项继续拒绝；新增四份原反例及八份同型反例共 12 项均由必需 capture 门判
BLOCKED。每项重新封存 JSON、wire、index/raw/result/selfchecks/manifest，再执行正式
CLI：退出码 1、输出 BLOCKED、stderr 为空。不能靠陈旧摘要或异常冒充闭合。
删除全部刷新反例实际移除 41 份观察和 82 份 DOM/PNG，仍由角色完整性门拒绝。

额外反例覆盖普通 Resume 和 switch initial Resume 的刷新缺席、单独复用 DOM 或 PNG、
switch initial 复用首读、借用别页、追加捕获及角色倒置。原正向 bound bytes 全部复核
一致，NEGATIVE 私有副本删除。这些是工具组件输入，不声称产品真实展示了错误页面。

## 首版失败与保留边界

本轮首版 `f2dcb07` 的 run-11 正向 PASS，但 86 项反证在已完成 74 项后、首次新增缺席
反例处失败：缺席判定的 required_roles 使用 tuple，保存为 JSON 后成为 list，正式 CLI
报 `recomputed verdict differs`。这是本次实现产生的工具缺陷，不能算拒绝成功。
首版日志、74 份已完成收据、失败状态及重现收据均保留；没有改写成 PASS。

`6fec85d` 改为 JSON 原生列表并加入往返测试；新候选从新的干净 archive 完成 run-12
及全部 86 项反证。旧 bundle 上的新 verifier 单例检查只是组件预检，不替代 run-12。
中间证据位于 run-11、gate-regressions-f2dcb07-01 和 repair-receipts-f2dcb07。

补充收据 `evidence/p6-checkpoint-repair-receipts-6fec85d/` 含聚焦测试日志、正式 CLI、
逐页捕获关联摘要、12 项正式拒绝摘要、冻结包及五组旧证据字节核对、配置边界。
主仓 main、主工作树 HEAD/status 与部署 runtime 配置 hash 保持一致，item 6=0；
未访问实际入口或正式数据库，原复核报告及负例原件未改。

**新候选尚待非作者代码复评与全新隔离复验，两项结论齐备后才能考虑限定签署。**
作者角色参数不证明独立性；本报告不签署，不扩大冻结矩阵之外的覆盖，不放行日常使用。
