# 本机可用版（2026-10-12）

本次整合已交付功能供本机日常使用，不代表完整 P6/V1 验收完成。

## 入口与使用

Dashboard: http://127.0.0.1:15180/ 。Core: http://127.0.0.1:18787/ 。仅监听本机。
凭据在本机私有 `~/Library/Application Support/JasmineCoreV1/local-item6/config/credentials/browser-daily.json`，将其中 token 复制到登录框。退出/刷新后重新登录；浏览器不持久化 token。

1. 在 Overview 选择项目，在项目页创建 Task；Task 的“写入”页创建 Step、编辑 criteria 并转换状态。
2. Rule、Interpretation、Review 按 Core 原有权限和版本检查提交。未配置提取模型时，不会自动产生 Interpretation。
3. TEST/PASS 需实际执行与可信采集；界面不提供伪造测试通过的按钮。人工确认在 Task 专用页准备、查看完整材料、勾选确认并提交。它只产生 INFO，VERIFIED/ACCEPTED 仍需单独提交并满足 Evidence 门槛。
4. Task 页读取最新 Checkpoint；Context/Resume 按实际 ID 读取。没有记录显示缺失。构建成功不代表 Agent 实际注入。
5. Validation 主导航只读展示已有运行及原 verdict，不会生成通过记录。

## 本次未开放

Memory 已接入独立的本机日常服务：后台 worker 从已提交的合格来源写入 Hindsight，Context 构建按需召回；主导航提供健康、队列、条目与 trace，管理操作仍由 Core 权限校验。P4 收益实验保持停止。原生 Agent 自动采集/注入、多设备在线、完整 Scenario Runner、manual-reapply 尚未开放。自动备份调度尚未提供，维护脚本支持一致备份与恢复检查。

当前工作区绑定 `~/Documents/JasmineCoreWork/local-item6` 的声明文件。更换采集工作区/producer 需明确配置，项目记录本身不会自动把任意本地目录接入采集。

## 运维

四个用户 LaunchAgent 负责 Hindsight、Core、Memory worker 与 Dashboard 登录自启与异常重启，配置与日志位于上述私有 local-item6 目录。`scripts/deployment/local-service.py` 启动配置指定的 Core、Dashboard 与独立 Memory worker。Hindsight 使用 `scripts/deployment/local-hindsight.py`，持久 HOME/数据库在私有 `local-item6/engine/`，监听 18084，PostgreSQL 15434；原临时目录 18080 服务未改。推理使用现有 Keychain 中的 DeepSeek 凭据，浏览器不接触它。
更新前必须用 SQLite backup API 保存数据库，并备份 config/bin；先在副本做升级、恢复与旧版启动验证。历史 schema 不降级。恢复旧运行配置可保留同 schema 的当前库；如需恢复历史数据，必须停服务、另存当前库后再替换，避免丢掉已接受的写入。

准确提交、备份路径、运行验证与回滚入口由私有部署 `RELEASE-RESULT.md` 记录。未运行远端 CI；是否推送与本机部署状态分开记录。

## Memory 与备份边界

Memory 从 Core 已提交的 Rule/Evidence/失败状态派生；不存在浏览器任意文本 retain 入口。普通聊天、未采集工具动作不会自动变成记忆。Context 构建只接受有本地 Evidence 支撑的历史结果/教训，Authority Decision 不会混入 Memory。构建不代表已注入真实 Agent。

本机备份页面读取启动器生成的公开运维摘要，标注采样时点、手动命令和最新通过数据库摘要核验的备份。SQLite 一致备份覆盖 Core；不包含 Hindsight PostgreSQL、云端服务或 Keychain。引擎记忆可从 Core 来源重新投影，但恢复引擎后须重置扫描游标并核对已有 job，不能把已有 DONE 当作远端仍存在的证明。自动备份、Storage/Devices 统计和在线心跳尚未实现。
