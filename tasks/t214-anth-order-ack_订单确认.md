# 任务：t214-anth-order-ack

- 项目：projects/anth-bot
- 负责人 / 使用的 AI：Grok 4.7（Cursor Cloud Agent）
- 更新时间（含时区）：2026-11-21 03:10 HKT
- 状态：待核验

## 用户目标与授权边界

修复 ANTH 在 Lighter `submission outcome unconfirmed` 后反复保护停止。允许改本仓库分支并开 PR。不部署、不合并、不重启、不恢复 live、不访问服务器或真实交易接口，不改其他项目，不提交凭据或访问码。

## 当前事实与证据

已核验交接包 `baseline_prod/bot.py` SHA256 为 `23126d91860f7220a031233737bb47a6531c705e7aebe06b43c8058ca42de711`。现场见脱敏证据：2026-11-20 22:11 HKT io 首腿成交后约 20ms 出现 unconfirmed，随后应急平仓再次 unconfirmed 并保护停止；稍后 intent 为 filled、双边为 0、收益 unreconciled。进程未崩溃。`confirmSec=0` 是信号参数。

官方语义（lighter-sdk 1.1.8 与公开文档）：`sendTx` code 200 只表示接受；确认要查订单或交易。执行成功的交易仍可能是 IOC 零成交，不能凭 tx 状态写成 filled。

## 本次修改

见本分支 `projects/anth-bot/bot.py`、`test_order_ack.py` 与说明。中断候选 `src/bot.py` 未整份采用。

## 验证

2026-11-21：归档副本与生产适配候选都跑过同一套 mock。`test_execution_safety.py` 33 项通过，`test_order_ack.py` 46 项通过，`test_offline.py` 与 `simtest.py` 全部通过。无网络、无真实客户端。

归档 `projects/anth-bot/bot.py` SHA256 `c3a6b211928389baf02233d8f6ba7cf3693ba28ffdd72121fa97361a4bac2c90`。
生产适配候选 SHA256 `8ac03769b90b1316fe7c44df6ff20e9b042bf002cafacfeef917407c8f4d25da`。
相对基线补丁 SHA256 `005b2dac632a65eb4ac5f1b83fd16a2e974bca743f5730b296ad793e2b85961c`，已用 `patch -p1` 复核，结果与候选一致。
测试文件 SHA256：`test_execution_safety.py` `c80fd1089606c3d5548d9f057992cc578fce90601d657cf1f7045739a82b9f9e`，`test_order_ack.py` `d948e37b515046e8040eae26bae3802d65a33d72e164b8c6cc142df1daee81e2`。

未对生产进程或交易所做核验。生产适配文件不进入 Git。硬编码访问码和钱包私钥扫描未发现。

## 决策与原因

未知与明确拒单分开。首腿已确认 IOC 零成交仍跳过。部分成交、残腿和永久未知仍保护。未对账收益不计盈利。不自动把 stopped 改回 live。

## 下一步与阻塞

部署前必须把订单逻辑移植到生产基线，而不是用归档副本覆盖 `/opt/anth-bot/bot.py`。需要用户另行授权。

## 交接锁

当前写入负责人是本代理的分支 PR。允许后续在该 PR 上复查。不允许部署、合并或实盘操作。
