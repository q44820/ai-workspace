# 币安Alpha与Booster监控

新增用户选中的两项本地脚本代码副本；不读取OpenClaw配置，不使用大模型，不交易或自动领取空投。默认推送关闭。

- `binance_alpha_monitor.py`（Alpha空投变化与稳定度监控）：第三方alpha123.uk空投/稳定度数据，辅以公开市场信息。
- `binance_booster_monitor.py`（Booster与币安公告监控）：币安公告API及Booster页面可访问性探测。
- `telegram_runtime.py`（环境变量通知适配与日志脱敏）：不复制原私有认证适配器。
- [WORKFLOW_工作流程.md](WORKFLOW_工作流程.md)
- [STATUS_当前状态.md](STATUS_当前状态.md)
- [SCHEDULE_定时运行示例.md](SCHEDULE_定时运行示例.md)
- [MIGRATION_迁移来源.json](MIGRATION_迁移来源.json)

## 运行

Python 3.10+，仅标准库。一次运行后退出；没有安装定时器。

```bash
python3 binance_alpha_monitor.py
python3 binance_booster_monitor.py
python3 -m unittest test_monitors -v
```

运行脚本会访问公开网络并写入`runtime/`，测试则完全离线、只写临时目录。可用`BINANCE_MONITOR_RUNTIME`指定独立私有数据目录；运行数据、日志及通知配置不进Git。

## 通知配置

默认`ALPHA_MONITOR_PUSH=0`、`BOOSTER_MONITOR_PUSH=0`。明确需要发送提醒时才在运行环境中启用，并提供对应渠道配置。

- `ALPHA_MONITOR_CHANNEL`、`BOOSTER_MONITOR_CHANNEL`：`wxpusher`、`telegram`或`both`。
- `WXPUSHER_APP_TOKEN`、`WXPUSHER_UIDS`：运行时环境变量，不写入仓库。
- `TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`：运行时环境变量，不自动读取任何其他AI的认证。
- 可选私有配置文件`runtime/wxpusher_config.json`；不要把这个真实文件提交或发给AI。
- `BOOSTER_MONITOR_ALL`默认1，监控所列分类全部公告；设0才按Booster/钱包等关键词过滤。
- `BOOSTER_MONITOR_NOTIFY_INITIAL`默认0，首轮建立历史基线，不推送历史公告。

## 限制

- Alpha源是第三方，不是币安官方承诺。接口未实时验收，字段变化或区域阻断都可能失败。
- 流通判断中“没查到”不等于新币或安全：接口故障可能被当作无市场；仅为历史启发式标签。
- Alpha首次运行可能把已有空投视为新增；默认关闭推送防止克隆后误发。一般提醒上限默认3次，临近提醒独立计数。
- 持续稳定时，冷却期过后可能再次提醒；稳定度绿灯不保证收益，不构成交易指令。
- Booster探测成功不代表已获取活动详情；抓取失败时空列表也不代表没有新活动。默认all=1会扩大提醒范围。
- 通知HTML转义、并发锁、Alpha推送失败的可靠重试仍需完善；两个定时实例不得重叠运行。
- Booster可调用Google公开翻译接口，只处理公开公告；这不是大模型调用，也没有翻译可用性保证。
- 本次仅代码整理与离线验收，没有验证真实通知送达、实时API或生产服务健康。
