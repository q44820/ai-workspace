# 03｜LP区间与钱包监控

- 优先级：**P1（维护主线）**。P1主线，P2次级，P3最低。
- 本次状态：代码已整理；运行服务未迁移，未启停旧任务。
- 依赖：Python 3.10+；地址审计可选pycryptodome；BSC快照Node.js 18+与ethers v6。
- 流程与交接：[WORKFLOW.md](WORKFLOW.md)、[STATUS.md](STATUS.md)。

## 入口

`python3 scripts/meme_lp_price_monitor.py --help`

## 验证方式

`cd scripts && python3 -m unittest test_lp_wallet_watchdog -v && python3 test_bncb_lp_monitor.py`

## 限制

仅链上读取，无签名交易；--apply/看门狗会改本地监控配置，通知会对外发送；生产钱包、推送参数不在仓库；并非所有链通用。

运行时配置、凭据和业务状态不在Git中；不要去原生产目录寻找并复制认证材料。
