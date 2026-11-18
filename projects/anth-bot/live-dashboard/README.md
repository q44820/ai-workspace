# ANTH 实盘只读看板 · 日本服务器

- 生产入口已迁入日本服务器的整合网站，使用既有鉴权路径。公开仓库不含访问码、完整能力URL、API密钥、账户配置或私密环境文件。
- `program-dashboard/server.py` 在已鉴权的 `anth-live/` 下提供看板、静态资源和 `api/status`，只允许读取，不包含交易控制接口；模拟页仍在同鉴权目录的 `anth-paper/`。
- `publish_status.py --local-file /var/lib/anth-status-publisher/status.json` 每30秒仅读取原机器人loopback公开快照、原子写入本机文件。systemd StateDirectory为唯一写入范围；不导入交易SDK、不签名、不调用启停或控制接口，不重启交易进程、不改机器人代码/环境配置。
- API每10秒读取本机文件，无Vercel Blob依赖。快照超过90秒或读取失败显示未知。旧安全修复提示只在精确匹配且当前状态安全时转历史说明，不改原状态文件。
- `japan.html`、`dashboard.css`、`dashboard.js`由 `build_local.py` 从共享页面生成；图表库固定Chart.js 4.4.4在本机加载，保留MIT许可。日本页面不请求Vercel或外部图表CDN。
- Vercel旧地址仅提供迁移说明和兼容模拟静态页，以免中断进行中的6小时网页测试。`api/status.js` 返回410，`api/ingest.js` 仅对已有鉴权请求回应已退役/未存储，不导入或读写Blob。这样不需重启原交易进程，就能停止旧推送产生存储操作。
- 原Blob设计每30秒写入一次，单日2880次，2000次额度不足支撑持续状态推送。邮件给的是累计额度告警，不能据此推断所有消耗均来自最近一次部署。
- `index.html`保留旧站迁移说明；修改共享页面后重新运行 `python3 build_local.py`，再执行状态与服务回归。部署必须同步GitHub和整合网站，不公开鉴权URL。
- 本仓库无实盘交易授权。模拟的成交推断、假设费用和资金费未知等限制仍适用；公网部署不等于策略盈利或实盘安全验证。
