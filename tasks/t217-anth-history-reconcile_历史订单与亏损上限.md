# 任务：t217-anth-history-reconcile

- 项目：projects/anth-bot
- 负责人 / 使用的 AI：Grok 4.7（Cursor Cloud Agent）
- 更新时间（含时区）：2026-11-21 05:15 HKT
- 状态：待核验

## 用户目标与授权边界

同一分支继续两件事：先修历史已结算订单被当前持仓复核降级，再保留两所合计亏损上限。允许改归档副本、测试和说明，并给出不进 Git 的生产适配候选。禁止访问服务器、交易、部署、合并、重启、清状态、删旧 intent 或伪造 filled。生产参数保持 3x / 290U / 40bp。5U 不是本缺陷的掩盖手段。

## 当前事实与证据

用户证据：已部署程序 SHA256 `8ac03769b90b1316fe7c44df6ff20e9b042bf002cafacfeef917407c8f4d25da`。2026-11-21 03:34:15 备份中 16 条 intent 全是 terminal。03:37 开仓信号之后没有新 intent、cloid 或订单日志，本地却有 opening_pending 和已提交的 exit_pending。`#2`/`#3` 被降成 unknown 且证据被清空；`#13`/`#14` 被降成 terminal_wait，filled 0.126 仍在。两所仓位和挂单为 0。Hyperliquid 该窗口订单和 fills 为 0。Lighter 最近 50 笔成交窗口为 0，私有历史未签名 400，不能证明绝对未发单。

本代理没有连接该服务器，也没有改它的进程或文件。

2026-11-21 04:57:23（上海）运维已部署 `98701f1a…` 并保持 stopped。`#2`/`#3` 已恢复 terminal，16 条 intent 仍在，03:37 标记已解除。`#13`/`#14` 仍是 terminal_wait。04:57:24 基线 209.884928、限额 5 已落盘，随后 `ready` 为 false。下面的旧哈希是当时的候选；现网就是那一版。新的未部署候选见本节末尾。

## 本次修改

归档 `bot.py` 的对账不再用当前净持仓推翻已有更晚同所订单的历史终态，也不擦掉已有官方证据。没有更晚订单、且没有官方来源的最新本地 terminal，在官方查询为空时仍降为 unknown。已完整平仓、账户该侧为 0、并且有更早同数量 terminal 的平仓，查询窗口过期时保持 terminal。发单前因旧单退出不再写成已提交退出；没有新 intent 时撤掉本次本地开仓标记。仍有未知订单则停止新开仓，不重发、不平未知腿。

`audit_restore_intent` 只在授权只读采集的订单 `filled_base_amount` 与成交明细合计、备份 terminal 的 official id 和 venue 一致时给出一条提案。手填数字或单独的 source 字符串会被拒绝。函数不写状态。`restore_state_offline.py` 默认不写文件，不删 intent，也不把提案当成可加载的 state。

亏损上限默认 5U，可保存 0 到 100000 之间的有限正数。净亏损从首次就绪的两所官方权益合计起算，含已实现、未实现、费用和资金费。能分类的充值、提现和内部划转按资金进出调整；分类不了就数据异常并停止新开仓，不改基线。重启和改金额不重置基线、不解除触发、不改变模式。触发先落盘再挡开仓。已有持仓仍可止损、回归和到时退出。显式确认才能重置基线。公开 DRY_RUN 模拟没有官方权益，不启用这道门；生产候选默认 DRY_RUN 关闭。

控制台增加金额、保存和带确认的重置。归档页保留“仅供阅读”说明。集成站未改。

## 验证

无网络、无真实客户端。

- 归档与生产候选：`test_execution_safety.py` 33 项 OK，`test_order_ack.py` 46 项 OK，`test_history_reconcile.py` 48 项 OK。
- `test_offline.py` / `simtest.py` 全部通过。
- 历史 fixture：完整开平后空仓，重启和全量对账不把旧单改回 unknown/terminal_wait，也不清空证据。发单前门禁不制造 submitted 退出、不追加 intent。污染样本里 terminal_wait 且有官方证据的开仓回到 terminal 且数量不变；证据已空的 unknown 保持 unknown。伪造的最新 terminal 仍被降级。
- 亏损：默认 5、非法金额拒绝、重启保留触发和基线、调低立即触发、调高不解锁也不改模式、费用和未实现计入合计、一所盈利一所亏损按合计、现金流调整、无法分类或时间错开则失败关闭、触发后减仓仍可发、两个开仓门都挡、确认重置不把模式改成 live。
- 控制页脚本通过 `node --check`。没有在浏览器里点击控制台：归档 `main()` 拒绝启动，本环境也不启动生产进程。
- 生产候选由 `8ac037…` 打补丁得到，`patch -p1` 后与候选字节一致。保留 `.env`、杠杆默认 3、名义默认 290。密钥扫描未发现硬编码私钥或访问码。

48 项是历史对账测试文件的方法数，含继承用例。它不表示 5U 已在现网生效，也不表示真实账户的提现第二来源、explorer 日志或两条 unknown 已经验收。

当时归档 `bot.py` SHA256 `f28d9ee12f51007594ae9b1969fcc28c0971e5e539712ed4ea886851d85f6141`。
2026-11-21 04:57 已部署并保持 stopped 的程序 SHA256 `98701f1ac0104dbe2167029650612b5519aebda305b7d4d33216179727752d12`。
当时相对 `8ac037…` 的补丁 SHA256 `24ce74de5c6aec80d18a17b5c42d5cf24eb47c7afd5b8f19d6365a47e9fabc48`。不要再把它打到已经是 `98701…` 的文件上。
`fbe0ed0d99eb38b7f4b98a0b0279e8e2103c9b8537bbd711aeffca12be30cfcb` 没有部署。
已部署控制页 SHA256 `4b4270385d54d9f2e644a6f499d9e6990c1cbe4160b86f4f4f24e39eef67c2b0`，本轮未改。

## 停止态循环依赖（新候选，未部署）

实测：`_lighter_history` 要 `V.lt.tx_api`，停止空仓启动跳过 `V.init`，基线后现金流为未核实，`ready` 为 false，提现回退没运行。`#13`/`#14` 因引擎提前返回而留在 terminal_wait。

新候选把只读初始化与交易初始化分开。停止态用 `ApiClient` 加 `TransactionApi` 读存款、提现、转账。没有 `ro:` 只读令牌时，签名客户端只用来签发 10 分钟令牌并立刻关闭。不设杠杆、不发单、不撤单，也不把交易客户端留在 `V.lt`。断流仍是未核实，不是 0。未 ready、仍有非终态 intent 或已提交退出时拒绝切 live。停止且无本地持仓时，独立循环用公开仓位做只读对账，不占用 0.25 秒主循环。只清除那句未知订单告警已经解除的情况。

回归在 `test_history_reconcile.py`：`V.init` 未被调用；实测形状的存款、转账和提现粘滞 cursor 让基线后连续 ready，基线不变；断流后 ready 为 false，恢复后可以再 ready 但模式仍是 stopped；`#13`/`#14` 在官方 filled 和同所后续平仓下回到 terminal；最新 unknown 不解锁；没有 set_leverage / send / cancel。测试用假的 lighter 模块和已测响应页，不是预先放一个 `V.lt`。

归档 `bot.py` SHA256 `28eb033a3c549533f60837875c0ecfd2cf856e9358cccb58cba5d764edcd4443`。
新生产候选 SHA256 `c4c64291c9df24f1da518b52b39a8789008a9de7522e6930bcde0a95db4cc95a`。
相对 `98701…` 的补丁 SHA256 `08971438ea9ee7037b308ed93cce12e0427ef561056402eb402b42f37eec58bc`。
历史对账测试现为 49 项 OK。这不表示新候选已部署，也不表示现网 `ready` 已经变 true，更不表示 5U 已生效。explorer 短页仍没有对真实账户复读。

## 决策与原因

已可信的官方终态不降级、不擦除。没有官方来源的最新本地 terminal 仍不盲信。历史已平且该侧已空的平仓，不因查询窗口过期被翻活。真正未知继续保护。5U 只挡新开仓，不保证最多只亏 5U。

## 下一步与阻塞

恢复方案见 `projects/anth-bot/RECOVERY_历史订单恢复方案.md`。`#2`/`#3` 和 03:37 标记已经在 04:57 的停止部署里按审计结果处理完，不要再重写。`#13`/`#14` 留给停止态只读对账，不能靠切 live 或清 intent。下一份补丁是否替换正在跑的 `98701…` 仍要人决定。换上之后也必须保持 stopped，并按下面的步骤验收。本记录不授权部署、切 live 或重设基线。

停止且不交易的验收：

1. 进程保持 stopped，不发送 `POST /api/mode` 的 live。若在 `ready` 仍为 false 时发送，应返回失败，模式仍是 stopped，并且没有为了读流水去 `set_leverage`。
2. 换上新文件并保持 stopped、空仓后，连续几次余额轮询里 `loss.ready` 为 true，基线时间仍是 04:57:24，合计仍是 209.884928。人为或真实断流时 `ready` 回到 false，恢复后可以再变 true，模式仍是 stopped。
3. 不调用重置基线，也不用改限额掩盖之前的 flow fail。限额保持 5 只是原值，不表示 5U 已经挡住过一次真实亏损。
4. `#13`/`#14` 仅当官方 filled 0.126 和同所后续平仓还在时变为 terminal，16 条 intent 都还在。另造或真实存在的最新 unknown 保持非终态，开仓仍被拒绝。
5. 只自动清除 “Unknown account/order outcome; manual review required” 这句已经没有对应未知订单的告警。残腿、敞口和亏损异常保留。
6. 日志里这条路径没有 `set_leverage`、`send`、`cancel`。只读令牌过期前关闭旧客户端。模式没有自己变成 live。

## 交接锁

写入在分支 `cursor/anth-order-ack-9ab6`。允许后续只读复核。不允许凭本记录部署、重启、清状态或交易。
