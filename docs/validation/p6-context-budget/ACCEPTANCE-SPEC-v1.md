# Context 超预算切片 v1

冻结后作者实现；非作者评审/复验与限定签署尚未运行。先沿既有接口推进，不改 Core/schema/
proxy 白名单，不新增 Dashboard Context 写入，不开启 item 6，不重启正式服务。

## 边界

既有 P3 ContextBuilder 的精确计数为 pinned tiktoken 0.14.0/o200k_base，整包上限 2500。
数字仅限 RENDERED_PACK_ENCODING_ONLY，资格 EXPLICIT_ENCODING_MODEL_UNVERIFIED；不是模型
完整隐藏提示 token，也不是实际注入回执。必须真实离线资产且 hash 符合 Core；缺依赖 BLOCKED。

必需内容不截断：HARD+header 超限为 AUTHORITY_TOO_LARGE；整个 mandatory 超限为
CONTEXT_MANDATORY_TOO_LARGE，真实 POST /v1/context/build 409 且不产生 pack/context.built/key。
成功 pack 的可选内容按整项省略；optional_budget 可能来自分区或总额度，不能臆定是哪一个。

Dashboard 现只有 GET /v1/context/{ctx}，失败 build 不持久化 pack/尝试记录。本轮 CB03/04
只证明真实 API 拒绝与零写入，不能称看板显示了真实失败 build 的 409。失败尝试看板回执
仍需后续 Core ADR/API 决策，不用假的 GET 409 或不存在 pack 的虚构来源替代。

## 固定矩阵

9 场景：CB01 正常 pack；CB02 可选 Checkpoint note 预算省略；CB03 HARD 超限；CB04
NORMAL mandatory 超限；CB05 同 Task 超限后的旧 pack 历史读取/replay 与 fresh admission
拒绝；CB06 context:read 缺失；CB07 proxy off；CB08 本地非法 ID；CB09 不存在的合法 ID。

CB01/02/05 各首读+实际刷新两套独立 DOM/PNG，对应两个完成 GET；其他四个浏览器场景
各一套。共 7 浏览器场景、10 捕获、20 DOM/PNG。CB03/04 不强凑浏览器捕获或 pack 链。
每场景四门：实际目标与结果、显示或拒绝边界、完整 DB Truth/history 不变量、来源/命令/
request_id/授权审计交叉绑定；共 36 门。任何缺席/矛盾不能 PASS，工具必须非零退出。

规则与 note 均为标记 SYNTHETIC 的隔离业务输入，经真实 Core API 创建，不改阈值、不替换
生产 scanner/tokenizer，不访问 Memory 引擎或停止的 P4 实验。不声明真实对话或 native hook。

## 证明方法

候选从 clean git archive 构建 Web，两个隔离 Dashboard(on/off)、私有 Core/DB/workspace、
浏览器 profile、凭据使用完清理；所有子进程为自有 PGID，顶层超时负责 Chrome 清理。
实际 Core observer/scanner 观测、DB一致备份、HTTP原件、DOM/PNG、真实导航/点击分别保存。
成功 pack 绑定 DB full_result、来源 Event、context.built命令、Task/rule/scope、exact
rendered bytes/hash/token/section metrics/omissions/selected IDs；真实 tokenizer 独立重算。
省略展示对应 note+source，必需 Rule 原文保留。刷新不重新 build，不改变 DB；旧 key replay
保持字节不变且有零 scanner 观测，不能证明仍可注入。

看板必须显著显示预算省略及其来源；缺失/异常数值、计数或上限未知不得显示 0 或“预算正常”。
历史损坏 over-limit pack 只做显式组件反例，不称真实 API 可生成超限包。正常范围同样不
称“注入成功”或“当前可用”，清楚标为构建时历史记录。错误/pending/非法页不得残留旧包。

组件坏输入重封存副本，不能用旧摘要失败冒充内容拒绝；至少检查遗漏/复用捕获、错误动态
内容、来源链接、wire、DB备份和整数计数。原件字节保持不变，不扩展 9 场景之外的宣称。

验收仅可签署既有合同这部分；失败尝试的看板回执、实际 Agent 注入、Memory/Authority 冲突、
真实对话链、Validation真实FAIL与整版本部署仍未关闭；P4路线停止、P6/V1未完成。
