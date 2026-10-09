# P6 阶段交接 — 2026-10-09（America/Los_Angeles）

## 当前结论

**item 6 的实现、隔离范围验收和本机部署入口验收已完成。最终开关为 0，日常使用未放行；P6/V1 整体未完成。**

这是进度整理与证据索引，不是新 Gate 或新独立产品评审。历史验收仍绑定各自提交。
本次仅更新文档，不合并主工作树、不部署新代码、不改变运行配置，不重启服务。

## 代码、部署与范围

| 项目 | 固定绑定与状态 |
| --- | --- |
| item 6 产品/测试/隔离 Gate | `75eea9601bf5f788a11ecedb5077460ef43006bf`，schema 12 |
| item 6 交付及实际 release | `79448dff91dd7a702f91157766e0ff8ad9fb7352`；相对前者仅增加三份验收文档 |
| 分支 | `p6-item6-web-confirmation`；本交接的后续文档提交不替换实际 release 或验收绑定 |
| 产品评审与隔离验收 | APPROVE / PASS_I6_SCOPED；125 checks；见 `../docs/validation/p6-item6/README.md` |
| 独立全套记录 | Python 1424 tests，0 failures/errors，11 skips；Web 515 tests PASS；非本轮重新运行 |
| 第一阶段部署 | BASE_READY_CONFIRMATIONS_DISABLED；schema 12，49 表一致备份恢复演练；HTTP 27 项、Chrome 登录 8 项 |
| 第二阶段部署 | PASS_I6_DEPLOYED_ENTRY；D01–D10 适用矩阵全部 PASS；实际 600 秒过期、重启、读回与审计 |
| 当前使用政策 | Web 确认开关 **0**，四条确认代理路径拒绝；日常使用 **未放行** |
| 整体结论 | **P6/V1 未完成**；没有 primary/main 合并或全阶段发布验收 |

确认生成 `USER_CONFIRMATION/INFO`。Step VERIFIED、Step ACCEPTED 和 Task ACCEPTED 是
独立 State 请求，并继续由 Core 判断 Evidence、版本与 freshness；确认不自动转换状态。
旧 `/evidence/confirm` 仍不经 Dashboard 代理；新来源由独立 ADR/API 明确定义。

浏览器为真实 Chrome 自动化，登记 human bearer 证明凭据控制权，不证明物理人操作。
TEST 为新实例配置的实际本地子进程。没有 native capture 或实际 P3/P4/P7 接入结论。
11 项 skip 是 10 项离线 tokenizer/encoding/cache 缺项和 1 项历史 evidence index 缺项；
未改为 PASS，无新增确认测试跳过。不同运行时的旧 9 项 symlink 失败不混入这次结果。

## 当前入口与运行方式

* Dashboard：[http://127.0.0.1:15180/](http://127.0.0.1:15180/)
* Core：[http://127.0.0.1:18787/v1/health](http://127.0.0.1:18787/v1/health)
* 正式库：`/Users/mercuryliu/Library/Application Support/JasmineCoreV1/local-item6/data/core.db`
* 冻结 release：实例目录下 `releases/79448dff91dd7a702f91157766e0ff8ad9fb7352/`
* 独立工作区：`/Users/mercuryliu/Documents/JasmineCoreWork/local-item6/`，plain 三项声明输入覆盖。
* 运行账户 mercuryliu（UID 501），两个前台 PTY，无自启；不承诺应用退出后的保活。

上述 URL 是当前部署地址，不是永久在线保证。停止/重启须重新核对实例与端口，不能全局
pkill。开关 0 关闭 Dashboard 确认代理；Core 直连仍按原有认证/权限合同提供 API。
凭据、数据库、备份、配置均在私有实例目录，本交接不复制 token/数据库或公开私有备份。

## 部署证据索引（原件保留，未复制或改写）

实例根：`/Users/mercuryliu/Library/Application Support/JasmineCoreV1/local-item6/`。
路径和 SHA 是本次只读核对的快照；后续内容变化必须重新说明，不能沿用本次绑定。

| 原件（相对实例根） | SHA-256 |
| --- | --- |
| `STAGE1-RESULT.md` | `34a42d5eea2b56cb76d1a0cf4dfa3c4be9d2b314559c5e863f6aec8aeda664ce` |
| `receipts/stage1-manifest.json` | `8959ba57e175aa5b88cdffff3124d2820c1fc17a0648c181393316f586464193` |
| `STAGE2-RESULT.md` | `23b50596c12d3857ffaa5dbc1e9464d7684bb6944fcddaa843f7947765484538` |
| `receipts/stage2/manifest.json` | `821a097034dd862b636d47f6f4cfdf6a7312c9dabcf8e4141a56cc4bd0cfb877` |
| `receipts/stage2/acceptance.json` | `27b84c00ad21629feef3cbbda35abd5ca7ffc6f19c2e5cc3c3ac489ab3742ed2` |

第二阶段 manifest 的 **53 个绑定文件**已逐一重算 SHA，0 不一致；D01–D10 的 PASS
取自该运行收据，本次没有重新执行完整 Gate 或 600 秒等待。这种摘要核对不单独证明
每个断言的语义正确；独立实现评审/隔离验收与部署运行记录分别负责自己的范围。
第二阶段 `off.json` 是启用前状态；最终关闭以 `final-disabled.json` 和最终运行配置为准。

初始化 B12-init 与写入前 B12-pre-entry 备份均保留，恢复仅在独立副本演练。
正式库包含验证写入；开关关闭不是数据回滚，不删除 Event/Evidence/State。
同盘备份不提供磁盘故障保护；没有自动备份/清理或常驻守护进程。

## 剩余任务与下一项

| 剩余项 | 状态 / 处理方式 |
| --- | --- |
| P6-02 milestone B 五类浏览器提交 | 现有清单仅有 4/9 浏览器提交证据；先核对原 bundle，再补 correct/rerun、review approve、rule supersede/retire |
| T03 原生 P2 对话链 | 仍需要真实 capture → Interpretation/Review → Resolver/写入链；浏览器补测不能替代 |
| T04 Context 注入 | pack 已构建不等于注入，缺真实注入 receipt |
| T05 剩余负例 | stale Memory vs Authority、old/异常 Checkpoint、Context over-budget；现有 outage/recovery 是 wire-double 范围 |
| T06 / P7 criterion 1 | 缺真实 FAIL Run、分类与原始失败输出；synthetic/NEGATIVE 不能顶替 |
| manual-reapply | 未实现；来源/权限契约与明确操作者决定仍需单独冻结 |
| online/backup/storage 视图 | 缺 P5/P7 对应读取合同；本机 SQLite 备份不等于交付这些视图 |
| P4 G01 | 实验路线已停止，P4 不可签署；本交接不重启或追加候选 |
| 部署日常使用/合并 | 未放行、未合并；不从 item 6 PASS 推断整阶段放行 |

**建议下一项：补齐 milestone B 五类浏览器写入证据，先冻结路由/判据表。**
已有 HTTP 证据和产品实现，无需为此触碰实际部署实例或改 Core。
实施前在原 bundle 中复查缺口；存在新证据就引用它。其余使用隔离 Core/DB/端口/对象，
从真实 DOM 点击，经代理提交，分别 GET 读回对象、command Event 和受控 audit。
revision 冲突保留草稿且不自动重试；固定 replay ID 不产生第二次写入。
成功/拒绝必须与实际请求和目标对象相关联，不能只检查常驻页面文字或 HTTP 200。
该项还需独立 review/validation 及自己绑定的干净候选；本轮没有启动它。

详细逐项状态见 [P6 剩余任务清单](P6-REMAINING-TASKS.md)。
