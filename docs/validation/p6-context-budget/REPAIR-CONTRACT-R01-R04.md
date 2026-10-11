# R01–R04 集中修复冻结约束

前候选3747ce1，复评 REVISE_REQUIRED。R01/F02、R02/F03、R03/F04 为原项未完全闭合；R04明确否定误报是3747ce1修复引入。旧规格与旧收据保留。

- R01：预算页面合法KV字段集固定、逐次捕获验证；历史rawtext/jsonblock叶节点可引用成功原文，产品KV状态不得隐身。首读、刷新、两者同时新增状态各拒绝。
- R02：从operation/checkpoint/replay/admission.audit_read和browser_audit.http消费角色反向强制phase、GET/200/admin注册权限、物理审计与observer。未知、缺失、另一个合法phase各拒绝；不能由phase决定是否检查角色。
- R03：生产source_for_task校验原source与Task/宿主/调用者关系；Checkpoint行/response、原请求、注册actor、不可变command五项身份actor/host/project/task/session交叉绑定。response及command五字段逐项反证。
- R04：有限直接否定允许，按每一个声明匹配限制；否定句后另附肯定句仍拒绝。诚实否定与历史原文通过正式CLI，不关闭产品文本扫描。

9场景/36门不扩大，新增31聚焦测试、原19反证、原23反证、新20反证、2正式CLI正控制。clean archive重新运行，统一作者验证；非作者复评/复验NOT_RUN、限定验收不签署。main/部署/旧证据不变，item6=0、P4停止、日常使用未放行。
