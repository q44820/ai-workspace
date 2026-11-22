# 受污染状态的可审计恢复方案

本文件只是人工步骤。不要在服务器上执行，不要杀进程，不要清空 `state.json`，不要删除旧 intent，不要把 `filled` 写成一个没有官方成交支持的数字，不要把模式改回 live，也不要改 3x / 290U / 40bp。

## 已核对的事实

用户提供的脱敏证据：2026-11-21 03:34:15（上海）备份里同一批 16 条 intent 全部是 `terminal`。03:34:57 用当时已部署的程序重启，03:36:13 `venuesReady`，03:37:08 出现开仓信号。这个窗口没有新的 intent、cloid 或 `[order]` 日志。本地却出现 `opening_pending`、`entryAccountingUnknown`，以及 `exit_pending.submitted=true`、原因是开仓。

对照备份，同一批旧单被当前持仓复核改写：

- `#2` / `#3`（2026-11-17 Lighter，official id `113917744074459` 与 `204835031781534`）从 `terminal` 变成 `unknown`，终态证据被清空。
- `#13` / `#14`（2026-11-20 开仓，official id `0xd3f20e389bb4ce210e72c85907601ece` 与 `13096582632427`）从 `terminal` 变成 `terminal_wait`，`filled` 0.126 和官方订单证据还在。
- 随后的平仓 `#15` / `#16` 仍是 `terminal`，`filled` 0.126。

代码在发单前写 intent 和 `[order]` 日志。这两样都没有，所以这次开仓没有走到发单。两所当时仓位和挂单为 0。后续已签名的 Lighter 只读查询确认 `#2` / `#3` 都是 filled 0.174，成交明细合计与订单 `filled_base_amount` 一致；03:30–04:05 的 Lighter 非活跃订单、活跃订单和认证成交都是 0。Hyperliquid 当前仓位和挂单也是 0。这些是主助手授权的只读采集，不是本仓库重新连上的账户。

这不是一笔新的已提交未知订单。它是旧的已结算订单被按当前净持仓重新判定，污染了新会话。真正缺官方终态的新订单仍然要停在未知，不能用这份方案去填成交。

## 原因

`reconcile_orders` 用「当前净持仓是否等于该笔下单前持仓加本笔成交」决定旧单是不是做完。后面还有同所的开平时，这个等式对更早的单不再成立。查询窗口里找不到的旧 Lighter 单会被降成 `unknown` 并擦掉证据。发单前的未知门禁接着把这次还没发出的开仓写成已提交退出。

## 04:57 已部署的 98701 做了什么

运维在 2026-11-21 04:57:23（上海）把 `98701f1ac0104dbe2167029650612b5519aebda305b7d4d33216179727752d12` 换上并保持 stopped。`#2` / `#3` 已按授权只读审计恢复为 terminal，16 条 intent 都还在，03:37 的本地标记已解除。`#13` / `#14` 仍是 terminal_wait，因为停止空仓的引擎提前返回，没有跑新对账。04:57:24 写入的亏损基线是 209.884928，限额 5，triggered 为 false；随后流水读不到，`ready` 为 false。不要重跑恢复脚本去改这份已经审过的状态，不要重设基线或金额，不要为了让流水初始化而去切 live。

## 下一份候选仍不能靠切 live 验收

下一份补丁的基线是这份已部署的 `98701…`。它让停止态用只读客户端读流水，并在无仓时做只读对账。即使以后有人换上它，模式也必须停在 stopped。`ready` 变 true 不会自动 live。未 ready 时切 live 应被拒绝。`#13` / `#14` 只有在官方证据仍在、同所后续平仓仍在时，才由只读对账回到 terminal。真正最新的 unknown 不能解锁。其他告警不能一起清掉。

## 人工恢复顺序

1. 再复制一份当前 `state.json`。不要覆盖 03:34:15 备份。本工具不代替这一步，也不改服务器文件。
2. 不要靠再跑一遍 `8ac037…` 自己修好。旧程序会继续按当前持仓改写。
3. `#13` / `#14`：证据仍是官方订单、`filled` 仍是 0.126，且同所已有更晚的平仓终态。新对账会把它们从 `terminal_wait` 调回 `terminal`，不改数量。这是恢复被错误降级的官方终态，不是新造一笔成交。不需要 `audit_restore_intent`。
4. `#2` / `#3` 只能用授权只读采集里的订单和成交明细逐条提案。手填的 0.174，或只有 `source` 字符串而没有成交明细的文件，都会被拒绝。`source` 不是密码学签名。
5. `#2` / `#3` 仍是 unknown 时，保留 03:37 的本地开仓标记和退出标记。没有新 intent 不代表可以删标记。
6. 窗口文件必须同时给出两边的 `orders=0`、`fills=0`、`code=200`，以及官方仓位 0/0，并且覆盖标记时间。缺任何一边，脚本保留标记。
7. 脚本在这些条件都成立时，提案里才会把没有对应 intent 的 03:37 标记清掉。它不删除 16 条 intent，不把模式改成 live，不改 3x / 290U / 40bp。`#13` / `#14` 保持原样，等新对账处理。
8. 若窗口文件里任一侧不是 0，停在这里。那是真实未知：不重发、不平未知腿、不把空仓当成已核实。

## audit_restore_intent 的离线用法

函数只返回一条提案，不写 `state.json`，没有 HTTP 路由，重启也不会自动调用。离线命令：

```bash
python3 audit_restore_offline.py current.json backup.json official.json
```

在不含 `.env` 的空目录里运行，旁边只放 `bot.py` 和这三个 JSON。命令拒绝联网。退出码 0 只在标准输出打印提案，并且 `wrote` 为 false。退出码 2 是拒绝。它不修改输入文件。

`official.json` 必须是授权只读采集留下的对象。单独的 `source` 字符串不够。至少包含：

- `collection.chain`：`ops-readonly-authorized`
- `collection.reader`：非空的采集名称
- `collection.queries`：非空的接口名列表
- `order.status`：`filled`
- `order.client_order_index`：与当前记录、备份记录相同
- `order.filled_base_amount`：有限正数，并且等于备份 `terminal_evidence.filled`
- `trades`：至少一条，`size` 合计等于 `filled_base_amount`
- `venue`：`io` 或 `lt`，三边相同

下面任一情况都拒绝，当前记录保持原样：

- `official` 是数字、字符串，或只有 `filled`
- `source` 是 `manual`，或采集链不是上面的值
- 成交明细合计与订单数量或备份数量不一致
- 当前状态不是 `unknown`，或当前已经有官方终态证据
- 备份不是 `terminal`
- `official_id` 或 `venue` 任何一边不一致

一条提案只覆盖一个 official id。恢复 `#2` 不会让 `#3` 变成已知，也不会移除本地开仓标记。提案上的 `audit_note` 是 `proposal-only-from-authorized-readonly-fixture`。

整份状态的审查命令默认不写文件：

```bash
python3 restore_state_offline.py current.json backup.json official.json window.json
python3 restore_state_offline.py current.json backup.json official.json window.json --output proposal.json
```

`official.json` 的 `records` 里只放要恢复的 `#2` / `#3`。`window.json` 带同样的 `collection`，以及 `start_ms`、`end_ms`、`positions` 和两边的 `venues`。`--output` 不能是已有文件，不能叫 `state.json`，也不能等于任一输入。写出的文件以 `ANTH_RESTORE_PROPOSAL` 开头，不是可加载的 `state.json`。标准输出里的 diff 给运维看。命令不改输入，不联网。

## 和亏损上限的关系

两所合计亏损上限是另一项功能，默认 5U，只停止新开仓。它不能拿来掩盖这次历史单污染，也还没有部署。服务器仍是原 3x / 290U / 40bp，本任务没有重启。
