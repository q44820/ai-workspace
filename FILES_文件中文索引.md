# 文件中文对照索引

说明文档已采用“英文_中文.md”命名。代码及工具约定入口保留原名，避免破坏导入、测试和自动识别。这里为每个文件提供中文用途。

## 常用入口

- [README.md（使用说明）](README.md)
- [AGENTS.md（AI协作规则）](AGENTS.md)
- [PROJECTS_项目索引.md](PROJECTS_项目索引.md)

## 网页部署

- [DEPLOYMENT_部署说明.md](projects/basis-radar/DEPLOYMENT_部署说明.md) — 价差网页运行方法、脱敏服务模板与线上版本核对

## 新增：币安Alpha与Booster监控

- [binance_alpha_monitor.py](projects/binance-monitors/binance_alpha_monitor.py) — Alpha空投变化与稳定度监控
- [binance_booster_monitor.py](projects/binance-monitors/binance_booster_monitor.py) — Booster与币安公告监控
- [telegram_runtime.py](projects/binance-monitors/telegram_runtime.py) — 环境变量通知与日志脱敏
- [test_monitors.py](projects/binance-monitors/test_monitors.py) — 15项离线测试
- [README.md（使用说明）](projects/binance-monitors/README.md)
- [WORKFLOW_工作流程.md](projects/binance-monitors/WORKFLOW_工作流程.md)
- [STATUS_当前状态.md](projects/binance-monitors/STATUS_当前状态.md)
- [SCHEDULE_定时运行示例.md](projects/binance-monitors/SCHEDULE_定时运行示例.md)
- [MIGRATION_迁移来源.json](projects/binance-monitors/MIGRATION_迁移来源.json)

## 文件与用途

- [CHANGELOG_变更记录.md](CHANGELOG_变更记录.md) — 变更记录
- [PROJECTS_项目索引.md](PROJECTS_项目索引.md) — 项目索引
- [SECURITY_安全规则.md](SECURITY_安全规则.md) — 安全规则
- [docs/COLLABORATION_AI协作流程.md](docs/COLLABORATION_AI协作流程.md) — AI协作流程
- [docs/PUBLICATION_公开发布说明.md](docs/PUBLICATION_公开发布说明.md) — 公开发布说明
- [docs/VERIFICATION_验收记录.md](docs/VERIFICATION_验收记录.md) — 验收记录
- [projects/basis-radar/STATUS_当前状态.md](projects/basis-radar/STATUS_当前状态.md) — 当前状态
- [projects/basis-radar/WORKFLOW_工作流程.md](projects/basis-radar/WORKFLOW_工作流程.md) — 工作流程
- [projects/flights/STATUS_当前状态.md](projects/flights/STATUS_当前状态.md) — 当前状态
- [projects/flights/WORKFLOW_工作流程.md](projects/flights/WORKFLOW_工作流程.md) — 工作流程
- [projects/lp-monitor/STATUS_当前状态.md](projects/lp-monitor/STATUS_当前状态.md) — 当前状态
- [projects/lp-monitor/WORKFLOW_工作流程.md](projects/lp-monitor/WORKFLOW_工作流程.md) — 工作流程
- [projects/polymarket-research/STATUS_当前状态.md](projects/polymarket-research/STATUS_当前状态.md) — 当前状态
- [projects/polymarket-research/WORKFLOW_工作流程.md](projects/polymarket-research/WORKFLOW_工作流程.md) — 工作流程
- [projects/prediction-cross/STATUS_当前状态.md](projects/prediction-cross/STATUS_当前状态.md) — 当前状态
- [projects/prediction-cross/WORKFLOW_工作流程.md](projects/prediction-cross/WORKFLOW_工作流程.md) — 工作流程
- [projects/weather-research/STATUS_当前状态.md](projects/weather-research/STATUS_当前状态.md) — 当前状态
- [projects/weather-research/WORKFLOW_工作流程.md](projects/weather-research/WORKFLOW_工作流程.md) — 工作流程
- [tasks/migration-selected-projects_项目迁移记录.md](tasks/migration-selected-projects_项目迁移记录.md) — 项目迁移记录
- [templates/task_任务交接模板.md](templates/task_任务交接模板.md) — 任务交接模板
- [.github/pull_request_template.md](.github/pull_request_template.md) — 合并请求模板
- [.github/workflows/verify.yml](.github/workflows/verify.yml) — GitHub自动检查流程
- [.gitignore](.gitignore) — 禁止上传的文件规则
- [AGENTS.md](AGENTS.md) — AI协作规则，工具约定入口
- [README.md](README.md) — 使用说明，GitHub自动展示入口
- [docs/MIGRATION.json](docs/MIGRATION.json) — 代码迁移来源与摘要
- [projects/basis-radar/README.md](projects/basis-radar/README.md) — 价差中位数网页：使用说明，GitHub自动展示入口
- [projects/basis-radar/core.py](projects/basis-radar/core.py) — 价差中位数网页：价差与盘口计算核心
- [projects/basis-radar/exchanges.py](projects/basis-radar/exchanges.py) — 价差中位数网页：交易所公开行情适配
- [projects/basis-radar/server.py](projects/basis-radar/server.py) — 价差中位数网页：价差网页后端服务
- [projects/basis-radar/static/app.js](projects/basis-radar/static/app.js) — 价差中位数网页：网页交互逻辑
- [projects/basis-radar/static/index.html](projects/basis-radar/static/index.html) — 价差中位数网页：网页主页面
- [projects/basis-radar/static/style.css](projects/basis-radar/static/style.css) — 价差中位数网页：网页样式
- [projects/basis-radar/test_core.py](projects/basis-radar/test_core.py) — 价差中位数网页：核心计算测试
- [projects/basis-radar/test_cross.py](projects/basis-radar/test_cross.py) — 价差中位数网页：跨市场比较测试
- [projects/flights/README.md](projects/flights/README.md) — 机票研究：使用说明，GitHub自动展示入口
- [projects/flights/legacy/flight_search.py](projects/flights/legacy/flight_search.py) — 机票研究：携程历史适配参考
- [projects/flights/legacy/flight_search2.py](projects/flights/legacy/flight_search2.py) — 机票研究：去哪儿历史适配参考
- [projects/flights/legacy/flight_search_v2.py](projects/flights/legacy/flight_search_v2.py) — 机票研究：多平台机票历史适配
- [projects/flights/legacy/query_flight_prices.py](projects/flights/legacy/query_flight_prices.py) — 机票研究：浏览器机票提价历史参考
- [projects/flights/query.py](projects/flights/query.py) — 机票研究：机票显式参数查询入口
- [projects/flights/requirements.txt](projects/flights/requirements.txt) — 机票研究：Python依赖清单
- [projects/lp-monitor/README.md](projects/lp-monitor/README.md) — LP监控：使用说明，GitHub自动展示入口
- [projects/lp-monitor/config.example.json](projects/lp-monitor/config.example.json) — LP监控：脱敏配置示例
- [projects/lp-monitor/package.json](projects/lp-monitor/package.json) — LP监控：Node依赖与项目说明
- [projects/lp-monitor/requirements.txt](projects/lp-monitor/requirements.txt) — LP监控：Python依赖清单
- [projects/lp-monitor/scripts/bncb_lp_monitor.py](projects/lp-monitor/scripts/bncb_lp_monitor.py) — LP监控：LP区间提醒
- [projects/lp-monitor/scripts/bncb_lp_snapshot.cjs](projects/lp-monitor/scripts/bncb_lp_snapshot.cjs) — LP监控：BSC仓位只读快照
- [projects/lp-monitor/scripts/lp_address_audit.py](projects/lp-monitor/scripts/lp_address_audit.py) — LP监控：链上LP地址审计
- [projects/lp-monitor/scripts/lp_monitor_sync.py](projects/lp-monitor/scripts/lp_monitor_sync.py) — LP监控：LP监控清单同步
- [projects/lp-monitor/scripts/lp_wallet_watchdog.py](projects/lp-monitor/scripts/lp_wallet_watchdog.py) — LP监控：低频钱包仓位核对
- [projects/lp-monitor/scripts/meme_lp_price_monitor.py](projects/lp-monitor/scripts/meme_lp_price_monitor.py) — LP监控：代币价格与区间监控
- [projects/lp-monitor/scripts/test_bncb_lp_monitor.py](projects/lp-monitor/scripts/test_bncb_lp_monitor.py) — LP监控：LP边界测试
- [projects/lp-monitor/scripts/test_lp_wallet_watchdog.py](projects/lp-monitor/scripts/test_lp_wallet_watchdog.py) — LP监控：钱包监控测试
- [projects/polymarket-research/README.md](projects/polymarket-research/README.md) — Polymarket研究：使用说明，GitHub自动展示入口
- [projects/polymarket-research/hunter.py](projects/polymarket-research/hunter.py) — Polymarket研究：预测市场研究扫描
- [projects/polymarket-research/requirements.txt](projects/polymarket-research/requirements.txt) — Polymarket研究：Python依赖清单
- [projects/polymarket-research/scanner.py](projects/polymarket-research/scanner.py) — Polymarket研究：预测市场候选评分
- [projects/prediction-cross/README.md](projects/prediction-cross/README.md) — 预测市场跨平台监控：使用说明，GitHub自动展示入口
- [projects/prediction-cross/arbitrage_calculator.py](projects/prediction-cross/arbitrage_calculator.py) — 预测市场跨平台监控：跨平台套利费用计算
- [projects/prediction-cross/arbitrage_monitor.py](projects/prediction-cross/arbitrage_monitor.py) — 预测市场跨平台监控：跨平台监控主程序
- [projects/prediction-cross/config.py](projects/prediction-cross/config.py) — 预测市场跨平台监控：非敏感运行参数
- [projects/prediction-cross/market_config.py](projects/prediction-cross/market_config.py) — 预测市场跨平台监控：事件匹配配置
- [projects/prediction-cross/requirements.txt](projects/prediction-cross/requirements.txt) — 预测市场跨平台监控：Python依赖清单
- [projects/prediction-cross/scraper.py](projects/prediction-cross/scraper.py) — 预测市场跨平台监控：公开网页报价抓取
- [projects/prediction-cross/telegram_bot.py](projects/prediction-cross/telegram_bot.py) — 预测市场跨平台监控：通知适配器，凭据运行时读取
- [projects/weather-research/README.md](projects/weather-research/README.md) — 天气研究：使用说明，GitHub自动展示入口
- [projects/weather-research/requirements.txt](projects/weather-research/requirements.txt) — 天气研究：Python依赖清单
- [projects/weather-research/settle_and_report.py](projects/weather-research/settle_and_report.py) — 天气研究：旧天气结算参考，直接入口已禁用
- [projects/weather-research/weather_arbitrage_scanner.py](projects/weather-research/weather_arbitrage_scanner.py) — 天气研究：旧天气研究样本，直接入口已禁用
- [scripts/check_publication.py](scripts/check_publication.py) — 提交内容安全检查
- [scripts/verify_workspace.py](scripts/verify_workspace.py) — 仓库离线验收
- [tests/test_publication.py](tests/test_publication.py) — 安全检查单元测试
