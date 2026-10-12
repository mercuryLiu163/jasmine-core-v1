# 本机可用版已部署（2026-10-12）

- 状态：DAILY_USE_ENABLED_LOCAL；完整 P6/V1 验收仍未完成。
- 首轮已验证产品提交：`5585849ee79037e2bbd45ffa51ed768e78d0f0c2`。后续只同步 Overview 的过期功能说明与交付文档，Core 未变；正式部署的精确提交与最终入口复验在私有 RELEASE-RESULT.md / runtime.json 中记录。
- Dashboard：http://127.0.0.1:15180/ 。Core：http://127.0.0.1:18787/v1/health 。仅 loopback。
- 运行：用户 launchd `com.jasminecore.v1.local.core` / `com.jasminecore.v1.local.dashboard`，登录自启、异常重启。
- 人工确认：1；P3/P7 读取：开启；Memory/外部 worker：关闭。P4 实验未重启。

## 验证

- 核心使用测试 163/163；前端 540/540；tsc 与构建通过。
- 隔离恢复库：35 项检查；真实 Chrome 确认、Step VERIFIED/ACCEPTED、Task ACCEPTED 流程 69/69。
- schema 0→12、main 8→12；已发布的 1–8 迁移字节未改，旧 checksum 保留。
- 当前库 schema 12 无需迁移；49 张表一致备份、恢复全行摘要相同；integrity/FK/checksum 检查通过。
- 旧发布在隔离恢复库实际启动并 HTTP health 通过（回滚兼容验证）。
- 实际部署 Chrome 检查 10/10；新建 Task 可读回、Validation 主导航、Checkpoint 入口、未完成说明及 token 不持久化通过。
- 两个实际服务重启后 PID 均变化，Task 内容逐字段相同。

前三次隔离尝试保留：QA 缺测试 Rule、QA 漏 source_system、复用旧 harness 固定采集 ID。第四次完整通过；均未由此修改产品 Core。实际浏览器首轮创建 Task 成功，但脚本误读 GET tasks 字段，后续读取脚本改为真实 `tasks` 字段后通过，原失败报告保留。

## 登录与使用

在终端执行：

```sh
/opt/homebrew/bin/python3.13 "$HOME/Library/Application Support/JasmineCoreV1/local-item6/bin/copy-login-token.py"
```

打开 Dashboard，将剪贴板 token 粘贴到登录框。“我的工作”项目已建立，入门 Task `tsk_01M4QKTAE708Y6VQ3HB5K823K3` 由真实浏览器创建，状态 ACTIVE，尚未执行或验收。日常 key 为 human，未授予 admin；管理员接口不经过代理。

## 备份与回滚

更新前一致备份：`/Users/mercuryliu/Library/Application Support/JasmineCoreV1/local-item6/backups/release-20261012`；恢复演练：`/Users/mercuryliu/Library/Application Support/JasmineCoreV1/local-item6/restore-rehearsals/release-20261012-attempt4`。另保留上线后的日常备份。

```sh
/opt/homebrew/bin/python3.13 "$HOME/Library/Application Support/JasmineCoreV1/local-item6/bin/manage.py" status
/opt/homebrew/bin/python3.13 "$HOME/Library/Application Support/JasmineCoreV1/local-item6/bin/manage.py" backup
/opt/homebrew/bin/python3.13 "$HOME/Library/Application Support/JasmineCoreV1/local-item6/bin/manage.py" restart
/opt/homebrew/bin/python3.13 "$HOME/Library/Application Support/JasmineCoreV1/local-item6/bin/manage.py" rollback-config
```

rollback-config 会先备份当前数据，再恢复先前版本的运行配置与启动器，保留当前 schema 12 数据；旧配置的人工确认为 0。不会自动用历史库覆盖后来写入。历史数据恢复须先停服务并另存当前库，再由操作者选择备份；不执行 schema 降级。

## 未完成

Memory 收益与自动召回、原生 Agent 自动采集/注入、多设备在线、完整 P7 Scenario Runner、manual-reapply、完整 P6/V1 发布验收未完成。Validation 仅读已有记录；Context built 不宣称 injected。当前可信 TEST producer 与工作区绑定仍使用原已登记的 unittest 命令及声明路径，未自动扩展到任意仓库。

## 证据与保留

本次检查：`/Users/mercuryliu/Library/Application Support/JasmineCoreV1/local-item6/receipts/release-20261012`；原阶段证据未改。主工作树原有 7 个未跟踪 proposal 文件与 main 对应文件逐字节一致，已在更新前另存 `/Users/mercuryliu/Library/Application Support/JasmineCoreV1/local-item6/backups/release-20261012/untracked-source-proposals/`；CLAUDE.md 未改。之前 main 由 tag `release-before-local-20261012` 保留。没有继续 94/119 反证修复，没有重启 P4。
