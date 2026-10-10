# Context budget — AUTHOR_VERIFIED / NOT_SIGNED

候选 **`2a37a1a43b6952c40bf0f923001da11536788c74`**，分支 `p6-context-budget`。作者 clean-archive 隔离运行
**9/9 场景、36/36 必需门**；正式 CLI **PASS / exit 0 / stderr 空**。
19/19 重封存组件反证由同一正式 CLI 拒绝，均 exit 1；原 bundle 字节保持不变。
**非作者评审、独立复验、限定签署均 NOT_RUN**，不能据此签署或宣布 P6/V1 完成。

## 产品与范围

`ContextBudget.tsx` 在 ContextPackBody 显著显示历史计数、整项预算省略及实际 Event 来源。
缺失／异常计数或上限为未知；损坏历史超限输入单独告警。可选预算原因可来自分区或整包，
不臆定是哪一种。构建记录不证明当前准入或 Agent 已接收；必需内容不截断。
Core、schema、proxy 白名单未扩展，没有新增 Dashboard Context 写入。

v1 原件保留；[v2 规格](v2/ACCEPTANCE-SPEC-v2.md)在新运行前冻结，9 场景、7 浏览器、2 HTTP-only、
36 门。v2 修正 CB09 的错误码简称：既有 Core 返回 `context_not_found`，不是裸 `not_found`。
输入、预算 2500、失败分类及计数未变。不存在的 pack 不硬凑来源链。
**失败 build 没有持久 pack/尝试读取接口：CB03/04 只证明真实 API 拒绝，不证明看板展示真实失败回执。**
该缺口仍需要后续 Core ADR/API 决策；本次未扩大授权范围。

## 真实运行收据

来源 `git archive 2a37a1a43b6952c40bf0f923001da11536788c74`，私有 Core schema 12、私有一致 DB、真实生产 scanner/tokenizer、
两个隔离 Dashboard（P3 on/off，item 6=0）、自有 Chrome/profile。业务输入明确标记 SYNTHETIC；
审批使用私有 human 角色凭据，不宣称真人操作、真实对话、Memory 引擎或 native 注入。

| 场景 | 本轮实际结果 |
| --- | --- |
| CB01 正常 pack | 427 / 2500；首读与实际刷新一致 |
| CB02 note 整项预算省略 | 486 / 2500；note standalone 950 token 省略；强制 Rule 原文保留，来源链接准确 |
| CB03 HARD 超限 | 409 AUTHORITY_TOO_LARGE；mandatory 5976 > 2500；全量业务/history DB 不变，无 pack/command/key 新增 |
| CB04 NORMAL mandatory 超限 | 409 CONTEXT_MANDATORY_TOO_LARGE；mandatory 6129 > 2500；同样零新增 |
| CB05 同 Task 的旧包 | 289 / 2500 的旧记录保持历史值；旧 key replay 200 完全一致且实测 scanner 零调用；check-current 409 context_stale |
| CB06 缺 context:read | 真实 Core 403 forbidden_scope，页面无旧包内容，物理 DB 审计对应 actor/request |
| CB07 proxy off | proxy_path_not_allowed；dependency pending，目标 Core 调用为零 |
| CB08 非法 ID | 本地 invalid，目标 GET 为零，无虚构来源 |
| CB09 合法但不存在 ID | 真实 Core 404 context_not_found；实测对象缺席，无旧成功内容 |

首读与刷新有独立角色和文件名：10 次捕获、20 DOM/PNG；8 次具名写入／重放／拒绝操作
各 before/after 一致备份，加浏览器整体前后，共18份 DB。来源 Event、context.built命令、
Task/project/actor/host、创建时间、链接、所有动态 JSON、完整 rendered 字节/hash/count、
request_id、派发前导航／点击窗口、实际 Core observer、DB审计分别交叉核对。
精确 token 使用原 pinned tiktoken 0.14.0/o200k_base 离线资产重算，限定
RENDERED_PACK_ENCODING_ONLY / EXPLICIT_ENCODING_MODEL_UNVERIFIED。
业务/history全表排除运营审计与私有 api_keys；这不是忽略审计，目标审计另与物理 DB 逐条比对。

原件：[raw](../../../../../evidence/p6-context-budget-author-run-06/raw-result.json)、
[result](../../../../../evidence/p6-context-budget-author-run-06/result.json)、
[manifest](../../../../../evidence/p6-context-budget-author-run-06/manifest.json)。
正式 CLI 收据及实际执行代码与候选 archive 的逐文件 hash 比对：
[FORMAL-CLI](../../../../../evidence/p6-context-budget-author-2a37a1a-20261010/FORMAL-CLI.json)。

## 作者反证与聚焦检查

[19份反证及汇总](../../../../../evidence/p6-context-budget-author-adversaries-2a37a1a-20261010/RESULT.json)：
删除6份刷新捕获、删除20份全部捕获、删除18份全部DB、刷新复用首读、压掉刷新角色；
错误省略计数／分区／token／来源；额外注入成功文案；错误总数／完整渲染／来源链接／时间；
404混入旧Task；跨场景借用真实pack；刷新窗口错误；缺物理审计；wire与DOM同步错误但DB正确。
副本修改后重算 result 并重封存 manifest，不能用旧摘要失败冒充内容拒绝；每份保留完整 CLI
输出、exit与拒绝原因。audit损坏场景只在证据副本解除 append-only trigger，正式 Core 完全未改。
本项是作者组件检查，不是新增真实业务场景或独立复验。

52/52 聚焦组件测试（20新预算 +32现有 P3）与 typecheck 在 `52879d1` 执行并有原始日志；
其完整 web tree 与本候选逐字节同一 Git tree，随后仅改反证副本构造。当前候选的 clean-archive
build 也实际通过 tsc。不得把此记录称为当前候选的完整 Core/P6 suite；11项历史 skip 仍保留。
[执行绑定说明](../../../../../evidence/p6-context-budget-author-2a37a1a-20261010/PREFLIGHT-BINDING.json)，
[聚焦日志](../../../../../evidence/p6-context-budget-author-preflight-52879d1-20261010/focused-tests.log)。

## 保留的作者失败

- run-01 (`5fafe2d`) 是旧工具作者 PASS，不能继承到 v2 或称签署。
- run-02 (`0f3aeed`) 5/9：将异步 CDP 回调接收时间误作 Core 派发前时间；误判保留。
- run-03 (`ee5fea5`) 5/9：运行期间本工作树资格代码改变，**固定候选资格不可用**；不采纳为 Gate。
- run-04 (`ae587b9`) 8/9：CB09 裸 not_found 不匹配实际 context_not_found，未放宽为任意404。
- run-05 (`52879d1`) v2作者9/9；首次反证构造在删除复制 audit 时被 append-only guard 中止，
  已完成17份反证，不能宣称19/19。其 COLLECTION-FAILURE 是作者终端观察摘要，非完整机器stderr。
- run-06 为本候选全新9/9；新版19/19反证全部完成。旧收据均未重写。

新 runner 启动时从候选 archive 加载资格函数，运行后工作树变化不能改变该函数。
导航与点击派发前至对应捕获构成请求窗口，保留request_id、审计、零扫描与独立刷新门。

## 运行边界与下一步

自有4组进程（Core、2 Dashboard、Chrome父Node）均 group_gone=true，无残留自有PID；
私有 runtime/profile 已删除。无localStorage/sessionStorage/IndexedDB凭据存储。
正式库未读取，实际入口未访问，现有服务未探测或重启；只读既有runtime配置确认开关0，
其SHA256仍为 `ffac90bfe948e5aa9262130b613094415d7fe95e2f5b150150737dc9735f1905`。primary HEAD与main、旧Checkpoint工作树HEAD/status保持原值，
[边界收据](../../../../../evidence/p6-context-budget-author-2a37a1a-20261010/BOUNDARIES.json)。

下一步绑定同一候选完成非作者代码／规格复评与全新隔离复验，再决定既有合同范围的限定签署。
失败构建看板回执、旧Memory/Authority冲突、真实注入、真实对话链、Validation真实FAIL及最终集成
部署仍未关闭。**P4收益验收未通过、路线停止；P6/V1未完成；item 6=0；日常使用未放行。**

## 独立复验命令

在代码候选的干净 checkout 中执行，给新的证据目录名；不使用正式库、15180或18787服务。
这些命令的 Core/Dashboard/profile 由 runner 私有创建并清理；可读 toolchain/离线资产路径
由 `--python`、`--cache` 控制，缺失必须 BLOCKED。

```sh
/opt/homebrew/bin/python3.13 scripts/p6/context_budget_run.py run \
  --ref 2a37a1a43b6952c40bf0f923001da11536788c74 \
  --out '/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/evidence/p6-context-budget-independent-new'
/opt/homebrew/bin/python3.13 scripts/p6/context_budget_gate.py \
  --bundle '/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/evidence/p6-context-budget-independent-new' \
  --expected-candidate 2a37a1a43b6952c40bf0f923001da11536788c74
/opt/homebrew/bin/python3.13 scripts/p6/context_budget_adversaries.py \
  --bundle '/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/evidence/p6-context-budget-independent-new' \
  --expected-candidate 2a37a1a43b6952c40bf0f923001da11536788c74 \
  --out '/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/evidence/p6-context-budget-independent-adversaries-new'
```

以上是待执行复验指令，**本轮未执行独立复验**。失败或残余问题先汇总，不能自动签署。
