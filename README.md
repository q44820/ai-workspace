# AI 协作工作台

公开的脱敏多项目代码与工作流程仓库。用于持续维护、可验证成果和跨AI交接，不是聊天备份、凭据仓库或自动部署系统。

## 开始阅读

1. [项目索引与优先级](PROJECTS.md)
2. [AI协作规则](AGENTS.md)
3. [分支、PR与交接流程](docs/COLLABORATION.md)
4. [实际验收与限制](docs/VERIFICATION.md)
5. [安全规则](SECURITY.md)、[变更记录](CHANGELOG.md)

已迁入01价差网页、03 LP监控、07 Polymarket研究、09天气研究、10跨平台监控、19机票工具的代码副本与工作流程。Polymarket相关统一P3最低优先级；不含实盘下单执行器。

## 其他AI接入

其他AI可直接读取公开代码和文档，从README和目标项目STATUS开始，先读后改。修改通过独立分支和PR；直接推送主分支仍需仓库权限，公开不代表任意写入。不要把GitHub Token贴进聊天。

## 本地验证

```bash
python3 scripts/verify_workspace.py
python3 scripts/check_publication.py --all
```

脚本验收不启动旧服务、不操作账户，不自动部署。部分历史模块只有语法验收，不能视为业务已经跑通。详见项目STATUS及验收记录。

凭据、业务数据、通知配置和服务管理状态均不在此仓库。扫描是辅助机制，不能证明绝无秘密；仍需审核diff。
