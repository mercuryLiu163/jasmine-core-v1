# Jasmine Core V1 P2/P3 主线与 P4/P6 并行协作

本文件是分工和集成计划，不表示这些阶段已实现。P1 已在 `main@905962564d07e4b5d7c08802231611c21f0b4ad5` 完成；当前 Schema v6，仓库已公开。实施启动时再次核对实际 HEAD 和指令。

## 1. 委派入口

| 工作线 | 任务书 | 顺序与可提前部分 |
| --- | --- | --- |
| 当前调度线程 / 核心主线 | [P2](Jasmine-Core-V1-P2-阶段任务书.md) → [P3](Jasmine-Core-V1-P3-阶段任务书.md) | P2-01→02→03 Gate；通过后 P3-01→02→03 Gate。P3 契约设计可提前，不越过 P2 真实 Gate 宣称阶段完成 |
| 外部 Memory 工作线 | [P4](Jasmine-Core-V1-P4-阶段任务书.md) | 现在可做 transport/adapter、bank/provenance、安全过滤、job/worker组件；完整 Recall 收益 Gate 需 P3 实际 Context 注入 |
| 外部 Dashboard 工作线 | [P6](Jasmine-Core-V1-P6-阶段任务书.md) | 现在可做框架和 P1 只读视图；Extraction 接 P2，Checkpoint/Context 接 P3，Memory 接 P4；P5/P7 未有能力不可伪造为在线/备份/完整Validation |

给外部执行 Agent：将对应任务书与本协作说明一起交接，优先使用 `gpt-6.1-sol` 实现、独立 Review 和验证。任务书末尾有可复制启动 prompts。原始三个 DOCX 位于 `/Users/mercuryliu/Public/AgentApp/Jasmine Mesh/jsamine core/Jasmine-Core-V1-设计实施验证文档包/`；在另一台机器执行时一并提供该设计包。ignored `artifacts/phase-planning-20260929/` 只是可选本地文本抽取，不是其他 checkout 必然存在的资料。

## 2. 分支、文件所有权和提交规则

每条实现线从核对过的 `main` 建立独立分支/工作树，不在其他 Agent 的工作树改文件。阶段负责人登记基线 SHA、文件 owner、候选 PR 和依赖；外部 Agent 不直接向 `main` 提交，也不自动合并其他线的 PR。

| owner | 拥有的数据与实现 | 提供给其他线 |
| --- | --- | --- |
| P2 | Interpreter、Interpretation、Resolution、Policy、review/correct/rerun | 候选/派生来源图、已决议 Truth 和 review 查询 |
| P3 | Checkpoint、Resume、Context Pack、实际生命周期注入、预算/audit | exact pack 和实际注入回执；Memory provider 接口 |
| P4 | Hindsight transport、bank mapping、Memory provenance、jobs/worker/recall | 非权威 recall 结果、过滤/故障状态、jobs/health 查询 |
| P6 | Dashboard、浏览器会话层、页面交互、契约消费测试 | Core API 缺口清单、真实 trace 页面和人工操作请求 |
| 集成 owner（当前调度线程统筹） | 公共 IDs、Schema/version、migration 序列、auth scopes、API route/dispatch、公共错误和阶段 ADR 注册 | 已审查、版本化、可合并的公共契约 |

领域 owner 可以准备公共文件 patch，但由明确指定的集成 PR 串行应用。禁止多个 Agent 同时写共享文件，禁止 P4 自建 P3 的 Context 表，禁止 UI 直接 SQL 或自判批准策略。

## 3. 迁移和契约协调

1. 当前最后已发布迁移是 `m0006`。下一个公共迁移及 ADR/ID 由协调线程登记分配；不要在三条分支都独立发布 `m0007`。
2. 外部线先交字段、索引、外键、幂等/事务和升级提案；独立模块/协议/组件测试可先推进。尚未分配迁移号时记录 `PENDING_INTEGRATION`，不以一个私有平行 Schema 冒充生产兼容。
3. 在合并到主线前，基于最新 `main` rebase；未发布的候选迁移可以经协调重新编号，并用全新测试库重验。已经进入 `main` 的迁移正文/checksum不修改。
4. 冻结接口要有字段级 JSON、权限/actor/scope、分页、revision、source refs、幂等、错误/降级语义和版本。P2/P3 的内部不稳定对象不直接成为 P4/P6 真源。
5. mock/fake 可以用于并行 UI/组件开发，但响应明确 `mock/unverified`；真实后端尚无能力时显示未实现/待接入，不填虚假 online、PASS 或“实际注入”记录。

## 4. 集成和验收顺序

- 当前主线先 P2：冻结候选/Policy → 实现 Interpreter → Resolver/review → 真实对话 Gate → 合并后回归。
- 然后 P3：确定性 Checkpoint → Context/Resume 与真实注入审计 → 真实 compact/续接 Gate → 合并后回归。
- P4 的独立 adapter 可提前评审/合并（其范围符合实际依赖时）；依赖已决议来源及 P3 pack 的完整长期记忆 Gate 延后到真实链路就绪。合规 job 最终入库与公共迁移按依赖集成。
- P6-01 可基于当前 P1 提前集成；P6-02 等 P2 frozen API；P6-03 各 inspector 对应依赖逐批真实接入。完整 trace Gate 需要原话→解释→resolution→Rule→实际 Context→Tool/Evidence。
- P6 Validation 下钻只等待 P7-01 的最小读取/安全 artifact 契约，可由其 owner 提前交付；不等待 P7 全阶段或最终发布 Gate，避免 P6/P7 循环依赖。
- 每个实现 PR 需非作者 Review、独立验证和对应候选 CI；阶段末尾有真实 Gate，合并后复跑受影响部分。单次环境或依赖缺失为具体项 BLOCKED，实际断言失败为 FAIL；阶段完成以证据判断。

## 5. 每次交接报告

报告写明 `stage/PR/base SHA/candidate SHA/dirty/schema/changed files`、已冻结 API/依赖、实际跑过的命令及退出码、审查阻断项、正负/故障测试、真实 Gate 证据与未验证项、是否请求迁移/公共文件集成。

不要把数据库、tokens、nonce、原始私密对话、Hindsight 数据、生产 binding、验收 artifacts 送入公开 Git。PR 保留安全摘要及 receipts/hash；原始资料在授权本地 ignored runtime。V0 `_archive/` 只作历史参考，不能恢复旧 hook/MCP/自启动作为新阶段捷径。

被用户委派实施后，可完成必要的隔离、可逆本地开发/测试准备；生产部署、付费外部账户、停止用户既有服务或改变全局安全/信任设置另需适用授权。不能仅凭设计文档中的环境操作文字推断授权，也不应为已授权的普通实现和验证重复请求确认。
