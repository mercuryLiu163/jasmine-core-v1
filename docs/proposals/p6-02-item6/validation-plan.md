# P6-02 item 6 — 实施拆分与预注册验收计划

状态：PROPOSED，设计审查不等于下列任何验证已运行。仅用户确认后执行。
冻结输入为 adr.md/api.md 本次批准的SHA-256；候选实现需另绑定真实commit/环境，不能沿用设计PASS。

## 1. 实施职责和边界

| 切片 | owner / 文件 | 交付与门禁 |
| --- | --- | --- |
| I6-C Core contract | Core owner：新迁移、models/event保留namespace、auth/me、store、API handlers、validator、ADR0007/API追加 | migration/组件/真实HTTP；独立代码review、独立validator |
| I6-W P6消费 | P6：web/server、shared allowlist、API client/表单/source展示、item5 matrix、tests/p6 | 严格代理、UI错误/未知恢复、真实Core读回；不在前端解释确认来源合法性 |
| I6-E 整合与验收 | 集成owner冻结所有依赖commits；独立验证者使用新的安全项目/数据库/浏览器 | 真实浏览器→Core来源→Evidence→独立State请求→readback/audit，冻结receipt包 |

先选唯一集成基线并解决schema8/10/11差异，再分配迁移号。不能把main缺web/P4的文件直接
假定为消费端已升级；不能改写原P6 item4/5冻结版本来继承既有PASS。
实现者、代码reviewer、验收者分开；设计reviewer只审本方案。未获确认前无任何实现切片启动。

## 2. Core必测矩阵（每一行独立保留PASS/FAIL/BLOCKED）

| 编号 | 情境 | 必须观察到的结果 |
| --- | --- | --- |
| C01 | Step EXECUTED/目标VERIFIED、Step VERIFIED/目标ACCEPTED、Task final Steps/目标ACCEPTED prepare→submit | 精确身份/版本/Rule/fingerprint；一份prepare、source、command、INFO Evidence；status/revision不变 |
| C02 | prepare不submit、affirmed=false/missing/string/1 | 无确认来源/Evidence；prepare绝不可被validator当确认 |
| C03 | human key无confirm；agent/system；admin-only；错误强塞confirm scope | 401/403适用；业务层复查human，不能因admin或UI标签通过 |
| C04 | me返回实际actor/key/scopes/host，host null/unregistered | 无凭据/其他key；未绑定409；不自动注册 |
| C05 | submit另actor、同actor另key、伪host/actor/source字段；source保护 | 403或strict400；所有通用Raw写路径无法创建新可信namespace/type；改namespace或type也拒绝 |
| C06 | 跨Task Step/跨project Rule；不存在引用；Rule pair只有一半 | 400/404/409明确；无半笔Event/Evidence；Rule applicability不靠UI标题 |
| C07 | stale Task/Step revision；writer等待期间改State/criteria、同scope新增/退役/supersede Rule | 409，无透明更新；不得接受遗漏新Rule的准备材料 |
| C08 | file改变/coverage-extra-path改变/HEAD改变/partial/symlink/scan cap/超时/配置漂移 | SAME仅完整相同；不确认、不静默STALE、不扫描归档/其他项目 |
| C09 | 600秒expiry边界、时钟回退、请求预算/DB busy | 409/503；不延长期限；writer内最终expiry拒绝 |
| C10 | prepare精确重放/改body/ID撞其他Event；成功submit后State变/过期/scan坏再重放 | 原receipt200/no副作用；改body409；早期及writer内replay先于current判断；已撤销key仍401 |
| C11 | 相同ID相同body、同prepare不同ID并发；网络失联前/后提交 | barrier强制双方早期lookup都miss、扫描结束后才允许writer；submit与prepare分别201+200；不同ID submit201+409；一份记录；UNKNOWN恢复不换ID |
| C12 | 事务中每步注入异常、实际进程commit前崩溃/commit后响应前崩溃、重启 | 前者全部回滚，后者原receipt恢复；prepare历史保留；不凭重试成功抹去失败 |
| C13 | source/prepare/command/Evidence/receipt引用或digest被测试树破坏；只改source.text保留ID/材料hash/receipt，含两种相同mask view的原note | 原文Event digest+submit semantic重建均检查；read/replay409 integrity_error；accept missing_evidence；不重建伪成功，不只检查source_system字符串 |
| C14 | Step确认→Step状态变；Task确认→一个Step动；Rule确认→version变；restart周期 | fresh的精确revision、Rule pair/cycle cutoff仍有效；旧确认不满足新接受 |
| C15 | confirmed_rule=null vs单Rule vs多个Rule；INFO与PASS要求 | 普通确认不满足Rule pair；每Rule各自一条；INFO不升PASS；criteria不被偷偷改写 |
| C16 | 来源和command seq排序、same-task/actor/host；Web与旧prompt两分支 | 只有完整新链/合法旧链可用；旧confirm/API/已发布迁移原bytes回归 |
| C17 | Empty/final Task Steps、非法Step target/current组合；criteria为空 | 409 ineligible；可以显示缺Evidence但确认本身不制造VERIFIED/ACCEPTED |
| C18 | 超长完整材料、合法Unicode/HTML/note指令/合成secret-pattern note和Rule；duplicate keys/null/unknown query；非法server/stored材料类型或非有限数 | 不截断规则；400/413/409区分；合法digest与现canonical bytes一致；所有授权产品prepare/status/replay/submit/Event GET/list原文一致；派生bundle/audit/log独立脱敏；无权限副作用 |
| C19 | 空库/各实际支持旧库/既有历史Events重复迁移/旧二进制打开新库 | 前向checksum稳定，旧数据byte不变，旧程序拒新schema，append-only/REPLACE反证 |
| C20 | audit正常/失败、auth.touch更新、拒绝操作 | 正常metadata可定位；故障审计明确best-effort限制；事件/Evidence真源无半笔；不把允许audit决策当成功 |
| C21 | 新扫描期间另一个合法Core writer与HTTP reader | 能完成，证明新路径不跨扫描持有writer；采样后filesystem变化在accept再拒绝 |
| C22 | 已提交后撤销key；历史readback/使用其他授权key接受 | 新submit/replay401；旧来源不自动被抹去；实际执行既有source freshness/State权限 |
| C23 | prepare后在独立测试DB副本降低bound key的对象/状态/规则读权限，然后status/prepare replay；不同key尝试读取 | 缺当前读scope403，不泄露完整材料；submit replay仅可读取原自身提交receipt，不含Rule原文；换key仍owner拒绝 |
| C24 | EXECUTED Step的criteria或适用Rule显式要求USER_CONFIRMATION/INFO | 目标VERIFIED Web确认→单独State VERIFIED成功；revision增长后旧确认不够ACCEPTED，须目标ACCEPTED新prepare/submit；USER_CONFIRMATION/PASS仍缺失 |

组件测试可用synthetic actors/Events，但必须标component_simulation；真实HTTP用隔离Core与实际文件扫描。
C13的损坏只允许临时测试数据库副本，绝不改现有证据或操作者记录。
C12必须保留实际crash输出/重启结果，不用“模拟Exception”冒充进程崩溃。

## 3. Web / proxy门禁

| 编号 | 场景 | 结果 |
| --- | --- | --- |
| W01 | 两host开关默认false/打开+新Core/打开+旧Core | 精确路由拒绝/可用/DEPENDENCY_PENDING；auth/keys、旧confirm、events和scan仍拒绝 |
| W02 | 看材料→未勾选→显式确认→readback，含合成secret-pattern规则 | 默认无affirmation；完整Rule/criteria/身份/指纹/期限与Core原字节一致，不mask盲签；确认只是INFO |
| W03 | 409 revision/rules/file drift、422 accept missing_evidence | 保留note并重审；无换revision自动提交；status/history等于Core |
| W04 | 断响应、并发双击、切项目/Task/key、页面刷新 | UNKNOWN恢复固定ID；防旧请求回流；pending有单独恢复入口；不后台生成新确认 |
| W05 | malicious note/原文、合成secret-pattern note的submit/status/replay/Event GET/list、token/HAR/storage、非同源Origin | 授权产品Raw一致且纯文本不执行；派生HAR/bundle有标注脱敏，内存bearer不进入日志/bundle；host拒绝非法Origin；Core仍做权限 |
| W06 | 来源深链接、item5候选矩阵、Step/Task顺序、单Rule多确认 | 识别新来源类型而非伪user.prompt；候选非verdict；所有Step接受后重建Task确认 |

浏览器单元/mock结果不能代替真实Chrome+Core。W02自动化点击只能证明浏览器链，
若验收目标还要求人实际操作，另收用户实际点击观察receipt；不能自动化后写human physically proved。

## 4. 真实闭环 Gate

G-I6-01：新非敏感项目，固定登记human及least-required key，由真实producer准备既有TEST/其他所需Evidence；
对于显式USER_CONFIRMATION/INFO要求，先在EXECUTED浏览器prepare/审阅/submit目标VERIFIED，
单独State转为VERIFIED；在新revision重新浏览器prepare/审阅/submit目标ACCEPTED，
GET来源Event/command/Evidence，浏览器另行ACCEPTED Step；等所有Step
结束后重新prepare/submit Task、另行Task accept。Core最终State/history记录evidence_ids与Rule版本。
数据准备可API完成，但确认正例必须本次浏览器专用提交，不能浏览器外先造confirm。
真实工具Evidence准备仍按已有可核验producer契约，不把手填PASS称为实测。

G-I6-02：同样环境至少逐个观察无确认、错误身份、State/revision/Rule/file漂移、过期/并发、
UNKNOWN恢复、proxy拒绝和INFO/PASS负例。拒绝对比Task/Step status/revision/criteria、Rule版本、
State history和新source/command/Evidence数量，精确允许auth.touch/audit以及独立成功prepare记录；
不能要求完全不写任何表来误报auth bookkeeping，也不能因此允许半笔确认。

G-I6-03：重启后receipt/事件深链读回、既有prompt确认回归、final集成commit smoke；
若merge改身份/来源/扫描/revision/trace再执行对应完整Gate。P3/P4/P7未验证范围继续独立标记。

## 5. 证据包和可证伪性

私有ignored `validation-runs/p6/<UTC-run-id>-item6/`：
manifest含最终commit、schema、三个依赖commit、方案hash、actor.kind/scope集合（无token）、
模式/时间/环境/工具版本、所有文件digest；scenario、click-trace、脱敏API request/response/request_id、
truth-before-after、source/prepare/command/Evidence/State映射、authorized独立audit readback、截图、
原始测试/crash输出、逐C/W/G verdict、reviewer与validator身份、result/limitations。
bundle不含token/数据库/private auth或完整私人对话；原始失败receipt保留，不抹除。

独立验证者必须尝试反证三个判定：假来源能否被接受；失败检查是否让runner非零；
未知/丢失来源是否会被UI升为成功。harness空记录/缺必需检查/未声明BLOCKED都非零，
已声明的环境依赖BLOCKED不可计PASS。不调整阈值或拿一次成功遮蔽原失败。

设计评审结论：APPROVE/REQUEST_CHANGES，仅对文档完整性。
实现评审结论：绑定candidate commit，独立代码review。
Gate结论：PASS_I6_SCOPED / FAIL / BLOCKED / PARTIAL；没有完整source/audit/readback不能完整PASS。
即便I6通过也只解除P6-02 item6依赖，P6整体/P4/P7/V1结论各自保留。
