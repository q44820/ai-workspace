# 定时运行示例｜Alpha与Booster

两项均可每10分钟执行一次，脚本单次执行后退出。这是脱敏示例，不是已安装配置，不复制原LaunchAgent。

## cron参考

替换下列通用路径，不包含真实通知凭据；每项使用独立状态文件。默认推送关闭。

```cron
*/10 * * * * cd /path/to/ai-workspace/projects/binance-monitors && /path/to/python3 binance_alpha_monitor.py
*/10 * * * * cd /path/to/ai-workspace/projects/binance-monitors && /path/to/python3 binance_booster_monitor.py
```

macOS也可自行配置launchd的`StartInterval=600`，但不要将样例直接覆盖现有任务。先手动执行隔离副本并验证，再单独批准部署。

## 注意

- 任一任务未退出前不得再次启动同一脚本；生产化应增加互斥锁，示例没有提供完整锁机制。
- 正式凭据用权限受限的运行环境注入，不在crontab命令行、Git或日志中保存。
- 关闭推送运行会更新本地已见状态；之后启用推送不保证补发之前忽略的全部项目。
- CI只做离线验收，不运行这些cron，也不访问账户或发送通知。
