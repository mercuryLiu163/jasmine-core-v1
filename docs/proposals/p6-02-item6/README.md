# P6-02 item 6：交用户确认的 Core ADR/API 方案

日期：2026-10-08（America/Los_Angeles）。状态：**独立设计复评 APPROVE；等待用户确认，尚未实现。**

推荐采用 Core **prepare → 显式 submit** 两步确认，新增受保护的
`human.confirmation_submitted` 来源，不伪造 `user.prompt`。
确认生成 `USER_CONFIRMATION/INFO`，VERIFIED/ACCEPTED 仍通过现有 State API 独立请求、判定和读回。

## 完整文件

| 文件 | 内容 |
| --- | --- |
| [ADR 方案](adr.md) | 问题/备选、身份边界、来源和材料、原子性、兼容与实施范围 |
| [API 契约](api.md) | 精确字段/响应/scopes、完整性算法、幂等/并发/UNKNOWN恢复、错误、持久表、代理/UI |
| [实施与验收计划](validation-plan.md) | Core/P6/集成拆分；24项Core要求、6项Web要求、3个真实闭环Gate；本轮全部未执行 |
| [首轮独立评审](review/round-1.md) | REQUEST_CHANGES：5项必修及可验证修复，不删除原发现 |
| [最终独立复评](review/round-2.md) | APPROVE：5项全部关闭；VERIFIED/ACCEPTED目标增量完整复审；绑定最终文档SHA |
| [交付清单](manifest.json) | 文档/评审SHA256、三条只读基线、设计/实现/验证状态分别记录 |

## 建议确认的决定

1. 允许专用结构化 Web 确认来源。Core生成完整审阅材料，绑定actor/key/Registry host、
   Task/Step精确revision、target、当前适用Rule集合/版本与完整fingerprint；人主动确认后才submit。
2. Step EXECUTED时可确认目标VERIFIED；到VERIFIED后须在新revision重新确认目标ACCEPTED。
   Task仅支持ACCEPTED，并等所有Step达现有最终状态条件后再确认。一次可确认普通对象或一个Rule pair。
3. 保留现有human bearer key信任模型；它证明该认证身份的请求，不能单独证明自然人实际点击/在场。
   host来自登记身份，也不证明浏览器真实设备。当前单用户Core不新增project ACL。
4. 增加identity/capability读取和三条专用prepare/submit/status路由、两张append-only投影表；
   正式ADR和迁移号在实现时按唯一实际集成基线分配。两个proxy host的新增通道默认关闭。
5. 旧human prompt确认契约不变；浏览器仍不能访问旧confirm、Raw写入、工具Evidence生产、
   fingerprint scan或auth管理。授权产品API原文readback保留，日志和分享证据另行脱敏。

确认材料不提供Evidence充分性预判，不把INFO变为PASS，不自动改State/批准Rule。
CR-1…CR-5和CQ-1不随本项扩张；真实Gate仍需现有合规producer Evidence和实际Core/browser链。
请求审计沿用best effort，成功命令来源另在原子Event中持久化；缺audit的运行不能完整审计PASS。

## 独立评审与当前结果

独立reviewer为另一个agent上下文 `/root/item6_independent_review`；作者修订、reviewer复评，
reviewer未代写主方案。首轮发现原文/脱敏、writer并发replay、note原始字节校验、status读取权限、
canonical严格校验五项问题；复评逐项确认关闭，并重新绑定三个最终文档摘要。
作者已独立复算三个文档和两份review的SHA256，与报告一致。

本轮只有方案文件与review文件写入。生产代码、既有设计包、其他worktree和基线HEAD未改；
未运行组件/HTTP/浏览器验收，未迁移、未启动服务或修改hooks/trust/用户数据库，未提交或发布。
APPROVE仅是设计可交用户确认，不能解释为item6已实现、P6完成或任何现有Gate已升级。

下一步仅在用户确认后进入I6-C Core实现，再按计划做独立代码review与验证。
