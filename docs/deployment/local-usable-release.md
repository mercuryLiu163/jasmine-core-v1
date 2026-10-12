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

Memory 引擎、worker、自动召回与管理入口关闭；P4 收益实验保持停止。原生 Agent 自动采集/注入、多设备在线、完整 Scenario Runner、manual-reapply 尚未开放。自动备份调度尚未提供，维护脚本支持一致备份与恢复检查。

当前工作区绑定 `~/Documents/JasmineCoreWork/local-item6` 的声明文件。更换采集工作区/producer 需明确配置，项目记录本身不会自动把任意本地目录接入采集。

## 运维

两个用户 LaunchAgent 负责 Core/Dashboard 登录自启与异常重启，配置与日志位于上述私有 local-item6 目录。`scripts/deployment/local-service.py` 仅启动配置指定的本机服务，无外部 worker。
更新前必须用 SQLite backup API 保存数据库，并备份 config/bin；先在副本做升级、恢复与旧版启动验证。历史 schema 不降级。恢复旧运行配置可保留同 schema 的当前库；如需恢复历史数据，必须停服务、另存当前库后再替换，避免丢掉已接受的写入。

准确提交、备份路径、运行验证与回滚入口由私有部署 `RELEASE-RESULT.md` 记录。未运行远端 CI；是否推送与本机部署状态分开记录。
