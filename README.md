# AI 协作工作台

公开的脱敏多项目代码与工作流程仓库。用于持续维护、可验证成果和跨AI交接，不是聊天备份、凭据仓库或自动部署系统。

## 开始阅读

**[FILES_文件中文索引.md（所有文件的中文用途对照）](FILES_文件中文索引.md)**

说明文档采用“英文_中文.md”命名。`README.md`、`AGENTS.md`及代码、依赖、自动化入口保留原名，避免影响工具识别和运行；对应中文用途均可在上方索引查看。

1. [项目索引与优先级](PROJECTS_项目索引.md)
2. [AI协作规则](AGENTS.md)
3. [分支、PR与交接流程](docs/COLLABORATION_AI协作流程.md)
4. [实际验收与限制](docs/VERIFICATION_验收记录.md)
5. [安全规则](SECURITY_安全规则.md)、[变更记录](CHANGELOG_变更记录.md)

已迁入01价差网页、03 LP监控、07 Polymarket研究、09天气研究、10跨平台监控、19机票工具的代码副本与工作流程。Polymarket相关统一P3最低优先级；这些项目不含实盘下单执行器。另有ANTH历史策略代码归档，已禁用真实交易和服务启动。

另新增[币安Alpha与Booster监控](projects/binance-monitors/README.md)，包含两项定时脚本副本、通知适配与离线测试；默认不推送，不修改现有运行任务。

## 新增：服务器项目

- [Arb Hub套利聚合网页](projects/arb-hub/README.md)：公开行情与中文网页，独立runtime，不含旧快照和导入数据。
- [ANTH机器人离线归档](projects/anth-bot/README.md)：策略/界面/合成测试，真实交易与服务启动已阻断；不含账户和历史成交。

## 程序总览

[Program Dashboard程序总览看板](projects/program-dashboard/README.md)：统一查看已登记服务器服务、本机调度与代码项目；实时快照及带访问码的私密入口不放Git。

## 其他AI接入

其他AI可直接读取公开代码和文档，从README和目标项目STATUS开始，先读后改。修改通过独立分支和PR；直接推送主分支仍需仓库权限，公开不代表任意写入。不要把GitHub Token贴进聊天。

## 本地验证

```bash
python3 scripts/verify_workspace.py
python3 scripts/check_publication.py --all
```

脚本验收不启动旧服务、不操作账户，不自动部署。部分历史模块只有语法验收，不能视为业务已经跑通。详见项目STATUS及验收记录。

凭据、业务数据、通知配置和服务管理状态均不在此仓库。扫描是辅助机制，不能证明绝无秘密；仍需审核diff。
