# t245：ANTH 顶部持仓误导显示修复与过严自动停机优化（生产同步记录）

- **时间（UTC+8）**：2026-11-22 18:23 – 22:00
- **状态**：已部署并受控重载，正式策略保持 `stopped`，`lt-read-pause.json` 保持持久锁存
- **安全边界**：未下单、未撤单、未改杠杆、未解除 Lighter 405 暂停、未重置 5U 基线、未清理 24 条历史 intent、未触碰 `.env`

## 1. 变更摘要

### (1) 顶部“无持仓”误导显示修复
- `live-dashboard/japan.html` 与 `dashboard.js`：线上核对发现已于 `2026-11-22 07:41` 更新（顶部改为“等待核验仓位”），保持未覆盖。
- `control.html`：内联只读判定模块 `AnthPositionStatus`，替换原 `:"无持仓"` 硬编码渲染；当 `d.pos` 为空但存在 LT 暂停、权益过期/未就绪、行情中断或缺少新鲜官方 0 仓证明时，统一显示“未核实”及原因，不再误报“无持仓”。

### (2) `bot.py` 3 处过严自动停机优化
在不放宽任何真实风控底线（残腿未平、未知终态订单、开仓前已有持仓、5U 亏损限额、LT 暂停锁存、一键全平）的前提下：
1. **开仓前预检异常（未发单前）**：改为进入冷却（≥30s）继续监控，连续失败达到 3 次才切 `stopped`；手动/测试模式或 LT 暂停态仍立即停机。
2. **杠杆预检临时失败（未发单前）**：同上，改为冷却后继续监控，连续失败 3 次才切 `stopped`。
3. **开仓两腿不齐但已官方确认安全全平归零（`flat=True`）**：维持失败先落盘 `stopped`（防崩溃敞口）；仅当同时满足「`flat=True`、无未知终态订单、非手动/测试模式、LT 未暂停、未触及 5U 亏损限额、连续不足 3 次」6 项条件时，才恢复原监控模式并进入 ≥60s 冷却；否则保持 `stopped`。

## 2. 校验与运行证据
- **备份目录（受限 0700）**：`/opt/anth-bot/secure-backups/t245-ui-autostop-20261122-182306`
- **更新后文件 SHA256**：
  - `bot.py`：`45733fa55c0d3854d9719c1984386a23e6536c7591361626758ae0c356469699`
  - `control.html`：`c4ef8c56480f586904d9719c198431e8324bff3cc9ee0b3e14b7f37dd8634e808`
  - `lt-read-pause.json`：`6fdc2a25bd5fe75c180f59ffe7d6f224ca55ac9f0cddb74dfa9cd31b88f3572e`（未变更）
- **验证**：
  - `venv/bin/python -m py_compile bot.py`：通过
  - 13 项离线分支单元测试（预检连续失败、残腿全平恢复与 6 项风控门禁）：`13/13` 通过
  - 受控重启 `anth-bot.service`：新 PID `2213971`，`ActiveState=active`，`NRestarts=0`，日志确认 `LT_PAUSED startup persistent=true`、`mode=stopped`，24 条 intent 与 `lt_paused=True` 完好。

## 3. 恢复实盘前待办（须用户另行确认）
1. 确认 Lighter 官方 CloudFront 405 人机验证已恢复（禁止绕过或高频探测）。
2. 独立核实 Lighter 当前真实仓位、挂单、杠杆与历史成交费用。
3. 经用户明确确认后，再解除 `lt-read-pause.json` 并将 `bot.py` 第 127 行 `TEST_CONTROL = True` 调整为 `False`。
