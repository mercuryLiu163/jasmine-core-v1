# Context budget 七项工具修复 — 统一候选约束

上轮 2a37a1a 非作者复核 REVISE_REQUIRED，原8份误报、原报告及原规格不回写。
这七项归为首次发现的原有遗漏，非该候选的修复引入回归。本文不是签署文件。

- F01：具名 build-before DB +候选 read_task_snapshot 重衍原时点 Authority/Truth/selector；
  scalar/JSON/full_result、配置摘要、生产 render、原 context.built 摘要/count/身份交叉核对。
  CB05 固定旧 before，不拿最后新增规则否定合法旧包。
- F02：产品拥有的 HTML 状态/摘要/控件检查与额外 alert 精确检查；历史 rawtext/jsonblock/kv
  容器明确排除，保留诚实否定句与历史引用；不做全页词语黑名单，不宣称覆盖任何措辞。
- F03：HTTP/operator/browser/首读/刷新/admin readback 的 actor/method/path/status/error/request_id
  绑定；私有 registered key/actor/scopes/home host 与物理审计交叉核对。admin身份保持独立。
  新增 after-audit 一致备份作为授权读回自身的审计见证：原18份备份不删，共19份DB。
- F04：Checkpoint真实存储解码、完整note/source/request、原Truth/selector/命令及增量；省略
  standalone_tokens/原因通过原note与生产formatter/tokenizer重算，不臆断分区或整包。
- F05：物理 observer_started/stopped、唯一owned PID、候选源码hash、方法、单调连续seq及
  完整request window。非replay真实build/checkpoint的scanner enter/exit/Popen正例可见后，
  才允许读取/replay零调用结论。保留派发前点击与CDP回调异步边界。
- F06：proxy pending 核对冻结404、proxy_path_not_allowed、目标、零Core调用。
- F07：PNG chunk边界/CRC/IHDR/连续IDAT/IEND、原生Chrome RGB/RGBA8解码及每行filter、
  完整payload与clip尺寸/角色；截断、签名伪图、错尺寸拒绝。可解码不证明像素与DOM不可伪造。

统一门槛保持冻结 v2 的9场景、7浏览器+2HTTP-only、36门。先24个聚焦完整性测试，再从同一
新候选clean archive全新运行，原19个反证重放，新23个反证（原8个同样修改协议重基+15扩展）
全部用正式CLI重算/重封存后判拒绝。原副本和冻结证据保持原hash，不用缺少新见证冒充闭合。

所有结果仍是作者验证；待非作者统一复评/全新隔离复验，再决定限定签署。不得改Core/schema/
产品写入/proxy范围；不得修改正式库、main、实际部署/服务/旧证据，不开启item6。P4路线停止。
失败build看板回执、旧Memory/Authority冲突、真实注入/对话、Validation真实FAIL及最终集成仍缺。
