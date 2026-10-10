# Checkpoint 验收工具：三项遗漏修复与作者复验

**REPAIRED / AUTHOR VERIFICATION PASS / INDEPENDENT CONCLUSIONS PENDING**。
限定验收仍未签署，P6/V1 未完成。item 6 为 0，日常使用未放行。

本报告接受非作者复核 `evidence/p6-checkpoint-review-6a90c92-20261009/REVIEW.md` 的
F01/P1、F02/P2、F03/P1。它们是首次发现的工具遗漏，不称旧问题重报或有证据的回归。
旧候选虽可复现工具 43/43、155/155、301/301，仍是 REVISE_REQUIRED，不能据此签署。
旧代码候选、报告和封存证据保留；本报告只记录新候选的作者修复与验证。

## 同一候选与冻结范围

| 绑定项 | 值 |
| --- | --- |
| 冻结规格 | `5079948808d4c8aa9cb5763c747def422c12c055` |
| 新代码候选 | `f3f560d1f28a38474397c698a3c4c39c324ea5a6` |
| candidate tree | `501669a0139c52d60ebffc6cf1b620d8ee2bdc01` |
| clean archive SHA-256 | `fad3f76a993e434d9fbb7c47bb40bc91853e2b55270f9b327bb11bad6121f2d5` |
| 作者新运行 manifest SHA-256 | `a43f0654a8fe8b946993e5291929e63e69f93e988f355b86de8c034cd2a622a5` |
| 新坏输入回归 manifest SHA-256 | `3c772ccb38c6bd05e4bffa9ef5dfa30008c6746f90cdd8bb261c1ac1e1f947f4` |

报告文档另作提交；复评／复验绑定上述代码候选，不绑定报告 HEAD。
冻结 26 场景、43 变体、101 判据定义、155 求值及各变体 G1–G7 均不改变。
35 个坏输入是组件反证，不新增验收变体，也不扩展其他损坏结构或算法／coverage／extra_paths
不兼容分支的运行覆盖。Core、schema、产品 UI、代理白名单没有本次修复改动。

## 三项修复

| 复核项 | 新的必需核对 |
| --- | --- |
| F01：释放后只看 ID | G7 同时核验切换 B 的释放前／后完整动态数据：实际 Core/DB 目标、note、三类 JSON、digest、freshness/reconcile、来源与命令链接、建议；命名输入字段恰为 B。held/released 绑定真实 A 请求 ID、path、response 和采集时间顺序。 |
| F02：空态／额外执行声称 | 按实际收据准确比对动作行数量、顺序、kind、全部 subject 字段及文本、code/span 结构；空列表必须为空 JSON且无动作行／动作面板；非空面板标题和完整内容精确匹配建议合同。额外、重复、缺失、错 Step/status 和执行声称都拒绝。 |
| F03：DB／注入绑定不完整 | 正式 Gate 必须读取封存文件，不能仅凭内存 raw PASS。从全部 154 份 SQLite 备份重算完整逻辑快照，与 53 份操作文件、实际 HTTP、audit、原始 observer 和 source/command/receipt/key 交叉核验。注入原件／分叉路径、字节 hash、完整原记录、唯一允许字段变化、SQL/参数、schema 与 PRAGMA 均核对。缺必需文件／引用／源码键即 BLOCKED/FAIL。 |

浏览器采集新增实际 DOM 的 key/value、digest、原文和动作行结构，而非填入收据的预期值。
展示核对仍以实际 HTTP/DB 记录为目标。B 的旧页面 ID正确不能替代动态内容正确；输入空值
或第三 ID 不能以“不等于 A”通过。释放时间和 request ID 从真实 held Response 读取。

F03 有两层强制绑定：原始 collection index 绑定文件字节；正式 manifest 必须包含 Gate
推导出的必需文件集合。53 份唯一操作与 154 份 DB 拓扑由实际对象引用验证，不依赖摘要
文件“自行列出了哪些文件”。浏览器目标定义与准备对象相同，key 元数据取实际 DB；审计子集来自完整响应，
每个已完成 GET 和单列验证者 audit GET 必须有实际 Core 入站／出站窗口。
源码映射固定四份 Core 与三份 launcher 必需键，再与候选
git archive 字节核验；不存在 None==None 的成功路径。

注入检查从只读原 DB 重建冻结的唯一变化，并要求实际分叉所有表只有该变化。CP15 只能
改变 Rule version，不能同时加入 Task-ahead 来遮蔽 guard；实际 409 message 还核对对应
guard。SQL只与独立重建的 UPDATE／参数比对，不执行来自收据的 SQL。服务启动仅允许
迁移代码取得精确的 migration_lock 元数据行，其他原件／分叉／操作状态必须完全一致。

封存 DB 以 mode=ro&immutable=1 打开，不写 WAL／SHM。SQLite 对这种连接会跳过 CHECK
验证，所以 integrity/foreign-key 检查在其实际 in-memory backup 上进行，封存原字节不变；
CP20 column_version 必须实际报告对应 CHECK 失败，不能读到一个模式相关的 ok 就放行。

同时更正旧报告关于公共探测审计的说明：实际 Core 会为 health/schema 探测写
actor_id=null 的 audit，旧 actor 过滤遗漏了它。新验证者取完整审计，再按请求和实际 DB
核对匿名记录、scope/status/error。浏览器仍不访问 admin audit/auth 路由。

## 新候选作者验证

`evidence/p6-checkpoint-author-run-06/` 从新 git archive 独立 npm ci/build，使用私有
Core/DB/workspace/端口/token/profile。实际 Core 为 schema 12；生产 scanner 未替换。

- 总体 Gate PASS：43/43 变体、155/155 场景判据、301/301 公共不变量。
- **正式 Gate 的 physical_evidence_binding PASS**：154 份 DB、53 份唯一操作、492 个
  必需文件路径，与总计 538 份 manifest 文件一致。数字层 PASS 不再能单独放行。
- 原八项结果生成器自检仍全部拒绝坏输入；真实 scanner 入口／返回／Popen 正向控制与
  重放零调用观测继续保留。缺席目标不补造来源链。
- 新聚焦单测 16/16，包括历史真实 DOM 的逐字段负例、Step/status/顺序/空态合同，以及
  只读 SQLite 的 CHECK 验证。历史 DOM fixture 明标来自 BLOCKED run-04，只作为组件
  输入，不能当成新候选的当前浏览器证明；当前证明来自 run-06。
- 补充收据 `evidence/p6-checkpoint-repair-receipts-f3f560d/` 保存当前候选的单测日志、
  CLI 命令与退出状态、只读配置边界和字节摘要。
- CLI 从落盘字节和候选 archive 重算后 PASS。完整 Web 520 项不重复宣称为本次新测试；
  产品 UI 没有本次改动，archive TypeScript/Vite build 的实际日志在新 bundle。
- 27 个自有进程组全部退出，私有 runtime/profile 删除；未启动／重启任何既有服务。

## 35 项重新绑定的坏输入

`evidence/p6-checkpoint-gate-regressions-f3f560d-01/` 保存每项 input_patch、判定与 manifest。
35/35 拒绝，包含复核的全部 8 个误报类别及已知 UNKNOWN→SAME 控制。每项先从实际正向
bundle 建立具名 NEGATIVE 私有副本，修改 JSON 原始采集，再重新生成其 index、raw 和自身
manifest；不能靠“旧摘要没改”制造一个无意义拒绝。原件每一份 bound file 均重读核对不变。

26 项在 physical binding 仍 PASS 时，由动态显示／字段判据报 FAIL 或 BLOCKED，包括迟到
后的 note/digest/projection/workspace/comparison、空／第三输入、缺释放或释放关联错误、
空列表插入恢复声称、额外／重复／缺失动作和错 subject/status。迟到动态检查及动作 G7
的拒绝另外明确核对，不把另一条不相关失败当成修复证明。

缺全部 154 份 DB 的副本即使重建自身 manifest，仍被正式 CLI 拒绝，其 physical binding
为 BLOCKED（缺必需备份引用），不把内存场景 43 PASS 当成总体通过。Rule-ahead 加 Task-ahead
的副本明确以“not the frozen single mutation; another guard may shadow intended guard”拒绝。
missing source keys、原件/hash/SQL/参数错误、假 DB 引用、错误 operation 文件、截断 observer
和换 source payload 同样拒绝。所有副本只作为坏输入组件证据，不声称执行了额外 Core 场景。

可重跑（out 必须全新；修复工具也是候选 archive 的一部分）：

```sh
cd '/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/_worktrees/p6-checkpoint-negatives'
/opt/homebrew/bin/python3.13 scripts/p6/checkpoint_negatives.py run \
  --ref f3f560d1f28a38474397c698a3c4c39c324ea5a6 \
  --out '<全新隔离运行目录>' --role independent-validation
/opt/homebrew/bin/python3.13 scripts/p6/checkpoint_negative_gate.py \
  --bundle '<本次运行目录>' \
  --expected-candidate f3f560d1f28a38474397c698a3c4c39c324ea5a6
/opt/homebrew/bin/python3.13 scripts/p6/checkpoint_gate_regressions.py \
  --bundle '<本次运行目录>' --out '<全新组件反证目录>'
```

独立性由实际非作者执行者／上下文证明，role 参数本身不证明独立性。

## 本次保留的失败与验收边界

`evidence/p6-checkpoint-author-run-04/` 绑定中间候选 143845d，内存场景全 PASS，但新
physical Gate 因未登记 runtime_label 判 BLOCKED，原输出保留。随后只读诊断还确认服务
启动的 migration_lock 差异和 SQLite immutable 跳过 CHECK 的行为；在内存副本对照，
未改原证据。新候选修复元数据与约束验证后，从全新 archive 全量重跑 run-06。
61cfb4a 的 run-05 和首次 35 项反证也作为中间作者检查保留；最后补齐浏览器目标／key／
审计窗口绑定后固定 f3f560d，再以新 archive 完整运行 run-06 与其独立命名的反证目录。
没有改规格、选取成功子集、回写 run-04 或追认旧 6a90c92 的限定验收。

旧作者／非作者 bundle、8 个误报和复核报告均保留。既有部署 runtime 配置只读核对
web_confirmation_flag 仍为 0；正式 DB／入口未访问，部署配置、main 和正式证据未修改。
新运行只是作者验证，**新候选仍待非作者代码复评与 clean archive 隔离复验，未签署**。
