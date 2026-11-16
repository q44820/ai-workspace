# 项目索引与维护顺序

P1为维护主线，P2为次级，**P3为最低档**。优先级仅影响工作排序，不代表自动运行或交易授权。

- **01｜P1｜[价差中位数网页](projects/basis-radar/README.md)**：公开行情、同所/跨所与现货/永续；近期重点补成交活跃度和方向提示。
- **03｜P1｜[LP监控](projects/lp-monitor/README.md)**：钱包低频核对、价格分频、区间提醒；本地零模型监控。
- **19｜P2｜[机票研究](projects/flights/README.md)**：保留历史适配代码，增加显式参数入口；尚不能保证含税/足量座位报价。
- **07｜P3｜[Polymarket研究](projects/polymarket-research/README.md)**：只读候选扫描；最低优先级，无执行器。
- **09｜P3｜[天气研究](projects/weather-research/README.md)**：历史研究与模拟参考；含硬编码样本，旧结算入口禁用，不能当实时策略。
- **10｜P3｜[预测市场跨平台监控](projects/prediction-cross/README.md)**：历史Polymarket—Opinion监控；默认无事件对，选择器/费率未实时验收。

## 新增：币安监控

- **[币安Alpha与Booster监控](projects/binance-monitors/README.md)**：对应OpenClaw清单第1、2项；两份代码与定时示例，默认关闭推送，不改既有任务。新增维护优先级待用户安排，不挤占P1主线。

## 新增：服务器项目（优先级待定）

- **[Arb Hub套利聚合网页](projects/arb-hub/README.md)**：独立公开行情聚合项目，不含生产快照或配置。
- **[ANTH机器人离线归档](projects/anth-bot/README.md)**：历史衍生品策略与合成模拟，仅研究用途，真实交易/服务启动被禁止。

## 所有AI的入口

1. [AGENTS.md](AGENTS.md)：协作与安全边界。
2. [docs/COLLABORATION_AI协作流程.md](docs/COLLABORATION_AI协作流程.md)：分支、PR、任务锁和更新顺序。
3. 每个项目的README、WORKFLOW、STATUS：目的、入口、限制和待办。
4. [docs/VERIFICATION_验收记录.md](docs/VERIFICATION_验收记录.md)：本次实际验收证据，不把迁入等同在线可用。
5. [docs/MIGRATION.json](docs/MIGRATION.json)：原文件相对位置、迁移前后摘要和整理说明。

其他未入选项目不在本次迁移范围，原文件照留；没有删除、重启或恢复任何旧任务。没有导入生产凭据、钱包配置、聊天、交易流水、历史模拟账或部署机器标识。
