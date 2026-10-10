# Checkpoint 验收工具 R01–R03 修复与作者复验

**REPAIRED / AUTHOR VERIFICATION PASS / NEW INDEPENDENT CONCLUSIONS PENDING**。
限定验收未签署，P6/V1 未完成，item 6=0，日常使用未放行。

本报告接受 `evidence/p6-checkpoint-review-f3f560d-20261009/REVIEW.md` 的
REVISE_REQUIRED。R01 是上一轮 F03 的 DB→浏览器读回交叉绑定未完全闭合；R02/R03
是原有显示判据遗漏。非作者旧版目标判据对照也接受这些反例，未证实是 f3f560d 修复
引入的新回归。历史作者／非作者正向 PASS 均保留，不能追认旧候选限定验收。

## 候选与冻结边界

| 绑定项 | 值 |
| --- | --- |
| 代码候选 | `cf3c655fee6facecae83f1f3a67b069fa5d9e9fa` |
| tree | `cc5d0941dd1926f9c956f6ae6f6294873ad7f23b` |
| clean archive SHA-256 | `7a0b6780c4cf2291baa0a426ac71ee8f55e1ae43b5bc5756d5d93b404ff5c5e6` |
| 冻结规格 | `5079948808d4c8aa9cb5763c747def422c12c055` |
| matrix SHA-256 | `5e3fcdd9808437dbad7908b52255c52a73b94ee4bf4496b6da9f29b83c954514` |
| 新作者运行 manifest SHA-256 | `632244bd07a3af1b140b2802860765fa91d3568ff1be4872fa5b7a0cd61be875` |
| 新组件反证 manifest SHA-256 | `a432083dc0efdbfbbf0e9607c085cf7b04e459c874e08a37301936e88374b42f` |

报告另作文档提交，后续复评／复验绑定代码候选，不绑定报告 HEAD。
冻结包和其所绑定的 remaining task 文档逐字节未改；仍为 26 场景、43 变体、101
判据定义、155 求值、每变体 G1–G7 共 301 求值。本次仅改 Gate、组件反证及聚焦测试。
Core、schema、产品 UI、代理白名单与部署没有修复改动。

## 三项修复

| 复核项 | 必需的新核对 |
| --- | --- |
| R01/P1：expected／wire／DOM 同错，DB 正确 | 每个成功历史 GET 的 prepared expected 与实际 runtime 的 SQLite 记录规范序列化相等；每个完成的浏览器成功历史 GET body 也独立与相同 DB 重算值相等。variant wire 还必须在实际全局 trace 中存在。迟到 A 响应与 B 页面沿用完整动态核对，不能借两个同错派生值通过。 |
| R02/P1：404 混入旧成功内容 | error/signin-error/invalid/pending/empty 分别要求 kv、digest、raw note、历史 JSON、source/command/对象链接、建议行／面板与成功边界为空；错误页仅允许实际 error.details，保留错误详情与输入。原始 HTML 和成功标记也作缺席检查。 |
| R03/P2：时间及错误链接 | created_at 按 UI 的 UTC ISO 毫秒格式精确比较，截断而非四舍五入。source/command Event 要求准确的行内容、链接文本和 `#/event/{真实 ID}` 目标；正确 ID 塞进错误路径的 query 不再通过。scope 中 Project/Task 链接、Resume 的 checkpoint ID/链接及 Checkpoint trigger level 同型元数据也准确核对。 |

latest 从实际 DB 的 checkpoints JOIN command Event 按 `(seq DESC, checkpoint_id DESC)`
选择；有 session 时核对实际 session 所属 Task 并准确过滤，空态也从 DB 选择结果推导。
不按 created_at 或某个准备对象的声称选择。损坏库读取解码其实际 fork 行，保留损坏事实，
不拿原有效备份替代；拒绝、权限、代理和本地输入边界继续区分。DB 只读 immutable，
原字节不改；SQLite 约束校验继续在实际内存 backup 上进行。

G7 的成功显示必须与 DB 已绑定的 definition/响应一致；R01 的物理绑定是额外必需门。
数字层场景 PASS 即使仍有 43 个，physical binding BLOCKED 也不能放行。

## 新候选作者验证

`evidence/p6-checkpoint-author-run-07/` 从候选 clean git archive 执行独立 npm ci/build，
使用私有 Core/DB/workspace/端口/token/Chrome profile；schema 12，生产 scanner 未替换。

- 总体和正式 CLI 重算 PASS：43/43 变体、155/155 判据、301/301 公共不变量。
- physical binding PASS：154 份 DB、53 份唯一操作、492 个必需路径；manifest 538 份文件。
- 20/20 聚焦 unittest 方法通过，覆盖序列选择与 session/空态、实际 fork 序列化、UTC
  毫秒时间、source/command/scope/checkpoint 链接及各非记录状态逐组件残留负例。
- 原生成器 8 项自检仍拒绝坏输入；重放保留真实 scanner 零调用观测，不补造缺席来源链。
- 27 个自有进程组全部消失，私有 runtime/profile 删除；未重启既有服务。

单测使用的旧 CP12 DOM fixture 明标 HISTORICAL COMPONENT，仍来自 BLOCKED run-04；
只能作组件输入。当前候选真实浏览器证明仅来自新 run-07。产品代码未变，不把旧 Web
520 测试结果称为本轮新测试。新 build／HTTP／audit／observer／DB／DOM／截图均在 bundle。

## 重封存反证

`evidence/p6-checkpoint-gate-regressions-cf3c655-01/` 保留原 35 项及 24 项本轮反例／同型
组件输入，共 59 项。这些不是额外验收变体，也不是产品实际出现错误渲染的会话证据。
每项从本次正向 bundle 建立具名 NEGATIVE 私有副本，JSON 原件替换后重新绑定
receipt-index、raw、wire 和 manifest；不靠陈旧 hash 拒绝。DB 与截图不改。
原始 bound bytes 在结束后全部重读核对不变，私有副本清理。

**59/59 拒绝，原 8 类和本轮 4 份原反例均拒绝。** 本轮 24 项均经正式 verifier
重算拒绝且无异常捷径；其中 5 项由 DB→历史 GET 必需门 BLOCKED，19 项保持 physical
binding PASS、由目标 G7 判失败。原 35 项继续全部拒绝，源 bundle 每一份摘要仍一致。

四份原反例分别是：DB 正确但 CP12 B 备注的 prepared expected／wire／DOM 同错；CP23
404 塞入旧成功 kv/digest/note/projection/workspace；CP02 created_at 错；source href
导航到错误 Event 并把正确 ID 放 query。另覆盖 Resume 同错备注、仅 wire 错误、latest
选择错误、伪空态、Resume 404／无效输入／signin／scope／pending／空态残留、Resume 时间、
command/scope/checkpoint 链接和 trigger/checkpoint 元数据。

24 项本轮输入均对正式 verifier 做落盘重算。DB 读回负例必须明确由
`actual historical DB GET` 对照拒绝；DOM／状态负例必须 physical binding PASS 且目标
G7 失败，不能用另一个不相关物理缺陷冒充修复证明。原缺全部 154 份 DB 的正式 CLI
拒绝继续保留。counterexample 收据同时保存 input_patch、语义／物理门结果与 CLI 判定。

## 重跑与未完成事项

```sh
cd '/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p6-checkpoint-negatives'
/opt/homebrew/bin/python3.13 scripts/p6/checkpoint_negatives.py run \
  --ref cf3c655fee6facecae83f1f3a67b069fa5d9e9fa \
  --out '<全新隔离运行目录>' --role independent-validation
/opt/homebrew/bin/python3.13 scripts/p6/checkpoint_negative_gate.py \
  --bundle '<本次运行目录>' --expected-candidate cf3c655fee6facecae83f1f3a67b069fa5d9e9fa
/opt/homebrew/bin/python3.13 scripts/p6/checkpoint_gate_regressions.py \
  --bundle '<本次运行目录>' --out '<全新组件反证目录>'
```

独立性需要实际非作者执行者与上下文，role 参数本身不证明独立性。本次没有启动独立
agent，也不把当前作者验证称为非作者结论；新候选仍待非作者复评、全新 archive 隔离
复验，两项结论齐备后再决定是否签署限定验收。

旧作者／非作者证据与 REVISE_REQUIRED 报告保留，未改失败归因或追认旧验收。
补充收据 `evidence/p6-checkpoint-repair-receipts-cf3c655/` 保存 20 单测日志、正式 CLI
命令/退出状态及只读配置边界。实际 runtime `web_confirmation_flag=0`，main 仍
`b9985e21f7971079e1a0b3d4efa87ee9b1aede3d`，primary HEAD 仍 7016dab，预存
`docs/proposals/` 保留。正式 DB／实际入口未访问；未 push/PR/CI/合并/部署或更改既有服务。
本轮覆盖仅冻结矩阵与明确组件反证，不扩大其他损坏结构、算法／coverage／extra_paths
不兼容分支的宣称。限定验收未签署，P6/V1 未完成，日常使用未放行。
