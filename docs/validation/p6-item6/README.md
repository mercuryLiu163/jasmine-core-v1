# P6-02 item 6 实施与验收记录

用户已授权“按方案实现”。产品/测试/验收 harness 冻结候选为
`75eea9601bf5f788a11ecedb5077460ef43006bf`，schema 12，分支
`p6-item6-web-confirmation`，独立工作树 `_worktrees/p6-item6`。
最终交付的后续 commit 只添加验收文档；产品代码与此候选一致。

独立实现代码评审：**APPROVE**，无未解决阻断项。
独立真实 Gate：125 checks / PASS，绑定上述候选，真实 Core＋Chrome，
实际等待 600 秒到期，包含正常审计/三个 GET 读回/重启/来源深链。
独立验收结论：**PASS_I6_SCOPED**。完整模式与覆盖边界见独立验收报告；
cross Task/key、旧 Core capability 和多 Rule UI 等组合仍明确标为组件覆盖。

## 已交付

* ADR 0015、完整 Web confirmation API v1；新增 m0012，旧迁移字节未改。
* 当前身份 `GET /v1/auth/me`、prepare、submit、同 key owner status 四个端点。
* 完整原文材料与 SHA、严格 JSON/容量/期限/角色/权限校验；writer 外扫描，
  writer 内重查与精确 replay；source/command/INFO Evidence/receipt 原子提交。
* 原始 Event hash、note semantic、完整引用链共享验证；State 保留 freshness、
  Rule pair、周期与 INFO/PASS 门槛，确认不会自动转换 State。
* Dashboard Task → 人工确认，未预勾 affirmation；显式三项读回与固定 ID/body
  UNKNOWN 恢复。两 host 的 `P6_ENABLE_WEB_CONFIRMATIONS` 默认关闭。

## 独立验证

| 验证 | 结果与范围 |
| --- | --- |
| 冻结 Python/Core 全回归 | 1424 tests，0 failure/error，11 skipped；37 个新增 Core tests 无 skip |
| 冻结 Web 全套 | 34 files / 515 tests PASS；生产 build/typecheck PASS |
| 正式真实 Gate | 125 checks PASS；browser loop 61、restart/history 9；schema 12 |
| 实际 crash / concurrency / tamper | 组件矩阵及独立实际进程输出；不冒充浏览器或 native capture |
| 验收 runner 反证 | false、empty、缺 Gate 必需记录、缺浏览器必需来源检查均 exit 1 |
| 真浏览器拒绝/未知反证 | UI VERIFIED 无确认→422；prepare 后文件变→409；丢来源/读回404不升完整成功 |
| 旧版本与前向迁移 | 实际 schema 11 代码拒绝 schema 12；m0001–m0011 字节与历史基线一致 |

11 项 skip：10 项离线 tokenizer/encoding asset/cache 未配置，1 项历史 P6
evidence index 在此 checkout 缺失。它们不是 PASS；无新增确认测试跳过。
独立 suite runner 的首轮路径错误、私有负例 runner 首轮 import 错误以及所有开发
调试 FAIL 均保留。修复的是验收脚本/环境错误，没有用成功结果抹去原失败。

## 证据与使用

正式证据：`validation-runs/p6/20261009-independent-item6/`，含最终 manifest、
逐测试结果、实际 crash 输出、真实 HTTP/浏览器/审计/Truth 前后/截图与反证。
代码评审原件：`validation-runs/p6/20261009T073349Z-item6-code-review/`。
整合出处与开发失败索引：`validation-runs/p6/20261009-item6-integration/`。
证据包位于 ignored 私有目录，不包含 bearer token、数据库或浏览器 profile。
只有派生报告/截图脱敏；产品原文及原 digest 未改。

| 封存对象 | SHA-256 |
| --- | --- |
| 独立实现报告 | `466e3af53faa87edfa1fd3abfff4d96029804462de2785562a784e728a0ff4f3` |
| 独立验收报告 | `c191614b94e8e34031f5c20811a9a0c836ebb5fdc66d8659621695a7e1aea996` |
| 独立总 manifest（146 文件） | `3aa9c5caeab7f3c0ab809d5525392119d26ffa02dd4a997b96596c5c603103c8` |
| 正式 Gate manifest | `8ef53e4dc9c64494355715804342bbb8d21b19df4886c21f6a3e2522fdb49ae3` |

入口为 `#/task/{task_id}/confirmation`。通过现有部署流程启用
`P6_ENABLE_WEB_CONFIRMATIONS=1` 后，使用已登记 human key，要求 confirmation
及对象/状态/规则/来源读权限；State 写权限仍单独配置。详细说明见 Web 接入文档。
复跑命令、环境、真实到期等待与 development 区别见 `scripts/p6/ITEM6-GATE.md`。

这次只解除 item 6 的 Core/API 与消费端依赖；不签整体 P6、P4/P7 或 V1。
自动化浏览器证明接口链，不证明自然人本人在场。用户现有数据库、服务、hooks、
全局配置和 retired V0 未部署或重接；原 main/P6-01/P6-03 checkout HEAD 保持原值。
