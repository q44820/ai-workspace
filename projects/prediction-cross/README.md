# 10｜Polymarket—Opinion跨平台监控

- 优先级：**P3（最低优先级）**。P1主线，P2次级，P3最低。
- 本次状态：代码已整理；运行服务未迁移，未启停旧任务。
- 依赖：Python 3.10+，aiohttp、playwright；python -m playwright install chromium。
- 流程与交接：[WORKFLOW_工作流程.md](WORKFLOW_工作流程.md)、[STATUS_当前状态.md](STATUS_当前状态.md)。

## 入口

`需先配置已核验同事件MARKET_PAIRS；默认空列表，不自动启用。`

## 验证方式

`仓库级语法/静态核验；历史浏览器选择器未做公网验收。`

## 限制

默认费率属于历史假设需重核；报价不能保证深度；事件名相同不等于可对冲；通知需运行时环境变量，不迁入凭据。

运行时配置、凭据和业务状态不在Git中；不要去原生产目录寻找并复制认证材料。
