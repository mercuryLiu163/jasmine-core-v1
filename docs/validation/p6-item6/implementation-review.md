# P6-02 item 6 独立实现评审

结论：**APPROVE**。冻结候选中没有尚未解决的阻断性代码 finding。
这是实现代码与验收 runner 的评审结论；实际 Gate verdict 由独立验收者另行出具。
不沿用设计评审 APPROVE，也不把组件测试计作真实 Chrome 或自然人操作证明。

## 身份与冻结输入

- Reviewer：`/root/item6_code_review`，独立于 Core/Web 实现者、设计 reviewer 和 Gate validator。
- 评审工作区：`_worktrees/p6-item6`，分支 `p6-item6-web-confirmation`。
- Candidate A：`75eea9601bf5f788a11ecedb5077460ef43006bf`。
- 对照基线：`4d7eda79327c6f563f86ee2f1eaa5d8f7cd3f05f`；集成基线 schema 11，候选 schema 12。
- 最终检查确认 HEAD 为 Candidate A，`git status --porcelain --untracked-files=all` 为空。
- 本报告先保存于 ignored validation-runs；随后可以复制到正式验证文档目录，不能使运行中的 Gate 偷偷改用另一个代码候选。

本次重新读取并核对批准方案的原始 SHA-256，三个文件未被实现过程改写：

| Frozen document | SHA-256 |
| --- | --- |
| `docs/proposals/p6-02-item6/adr.md` | `540568b257c9419dd6c4f749f7ef40a8b94994f6750892fcbb609397e4e91cef` |
| `docs/proposals/p6-02-item6/api.md` | `aefccb63087ad3d4c7fc5ff2dc87cbbca29e10df260988384ab5d2178f426da5` |
| `docs/proposals/p6-02-item6/validation-plan.md` | `125c2e2ee8f34bef72da301332d31b192412e19534e9a7a246614b26b40f59e2` |

## 审查结果

Core 两次 replay lookup 的最终顺序符合契约：当前授权和 owner/完整性核对后，先处理已成功 command 的精确重放/冲突，才判断新命令的预算、消费、期限和当前 context。writer 内重查 key/scopes/human/host，并在同一事务提交受保护来源、command、INFO Evidence、唯一 consumption 和首次 receipt。扫描处于所有事务之外；当前 context、配置 identity、完整 fingerprint 和最终 expiry 仍有提交前复查。status 在单个读事务中核对原始链且不扫描。

共用 `verify` 复算完整原文 Event body hash，另从 source 原 note 重建 submit semantic hash，逐字段核对材料、source、command、Evidence、投影及首次 receipt。新保留 type/namespace/discriminator 被通用 Raw ingress 拒绝；State Web 来源分支复用完整性验证，并保留既有精确 revision、Rule pair、fingerprint 与周期判断。确认保持 USER_CONFIRMATION/INFO；独立 State 命令和 INFO/PASS 门槛没有被前端绕过。m0012 新增表具有 append-only/REPLACE 防护，旧迁移文件没有改写。

Web 两个 host 共用默认关闭的显式开关和精确 method/path；旧 confirm、Raw Events、scan 和管理入口继续被代理拒绝。表单显示完整原文材料，affirmation 默认未勾选。UNKNOWN 保留同一个 command ID/body，跨 Task/key 的旧回调有 generation 防护，旧 pending 不能由不同 key 消费。成功 receipt 后执行 source、command、Evidence 三个 GET；页面不自动发送 VERIFIED/ACCEPTED。

runner 的正例通过真实 Chrome 点击，TEST Evidence 来自实际 subprocess 输出与固定 producer 配置。丢响应发生在实际 Core 201 commit 后，以 UNKNOWN/status 恢复检查固定 command，三个对象/目标链及必要检查精确次数都在最终聚合条件中。HTTP 负例核对具体 error code/changed 标签和 Truth 前后，而非只看 409。缺场景、失败断言、未知 phase 或空检查不能进入 PASS 分支。实际 600 秒到期不能由 development 模式冒充。产品原文/DOM 与服务端 digest 在派生证据脱敏之前比对；截图临时替换在 affirmation 前恢复，share report 明确标注派生脱敏。私有 DB/凭据/profile 不进入证据目录，Core 子进程去除了继承的 JASMINE 配置。

## 已解决的评审发现

| Finding | Candidate A 中的处理 |
| --- | --- |
| 同一个成功 submit ID 改成不存在的 preparation，先查新引用造成 404 而非内容冲突 | early 与 writer lookup 先按原 command 定位原 owner/记录；新增改 preparation 的 409 测试。 |
| 同组件切 Task 时旧 Step/Rule 选择残留 | Task/token 切换清 Step、target、Rule pair 与旧材料，note 保留明确原对象；新增切 Task 请求体单测。 |
| 初版 Gate 缺 G-I6-02 实际负例、仅校验 HTTP status，clean 检查遗漏 untracked | 增加真实 HTTP 反例、精确拒绝原因、Truth 对比和 required 集合；clean 包含所有非 ignored 的 untracked。 |
| 初版浏览器没有合成 secret-pattern Rule/note 的原文链与派生脱敏证明 | 新增真实原文/HTML/DOM hash、status/replay/list 比对；仅派生报告及截图脱敏并标注。 |
| C13 只改变 source.text 时可能只验证了第一个 body hash 检查 | 增加重算 source hash、同步 receipt 原文镜像但保留旧 semantic 的反证，独立验证 note semantic 检查。 |
| C12 子进程实际 crash 输出被 capture 后丢弃，Rule supersede 只有名称覆盖 | 四种 os._exit 输出和重启 receipt 已结构化保留；增加实际 v1→v2 supersede、旧 pair 失效及 Task/Step 周期组合。 |
| 子进程可能继承用户 Memory 部署配置 | 清除全部继承 JASMINE 环境，仅设置隔离 fixture 必需项。 |

## Reviewer 执行的核对与界限

冻结前，reviewer 用临时组件 fixture 复现了 changed-preparation 404，随后确认修复；损坏 snapshot/深 payload 的 API/status/State 反证均被拒绝。冻结前的 20 项 targeted spot-check 通过，不计正式验收。

在 Candidate A clean tree 上，reviewer 独立执行：

```sh
.venv/bin/python -W ignore::ResourceWarning -m unittest discover -s tests -p 'test_web_confirmations*.py' -q
```

结果：**37 tests，5.224s，OK，exit 0**。实际 prepare/submit commit 前后四种 child crash 的 exit 75/76/73/74，以及新连接恢复状态/首次或 replay receipt，均输出为结构化无凭据记录。这是 component_simulation 与真实 SQLite/进程/socket 的组件验证，不是浏览器 Gate。

该评审没有宣称所有预注册行的每一种排列都已执行。具体组合覆盖和未执行边界见 `docs/validation/p6-item6/core-component-matrix.md`；旧程序实际打开新 schema、缺字段 HTTP 反证和冻结实际 Gate 由独立 validator 补核。旧设计 PASS、实现者 development PASS 和未冻结 suite 输出不能代替最终 Gate。

信任边界保持注册 human bearer 凭据；自动化点击不证明自然人本人在场。文件采样不锁住工作区，State 接受仍需重新判断当前 fingerprint。audit 仍是另事务 best-effort；实际正常 audit/source/readback 链缺失不能签完整 scoped Gate PASS。P4 Memory、P7、用户部署/全局 hooks 与整体 P6/V1 验收均不在本次代码批准范围内。
