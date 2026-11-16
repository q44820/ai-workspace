# 07｜Polymarket只读扫描研究

- 优先级：**P3（最低优先级）**。P1主线，P2次级，P3最低。
- 本次状态：代码已整理；运行服务未迁移，未启停旧任务。
- 依赖：Python 3.10+，requests。
- 流程与交接：[WORKFLOW.md](WORKFLOW.md)、[STATUS.md](STATUS.md)。

## 入口

`python3 scanner.py`

## 验证方式

`仓库级 scripts/verify_workspace.py 仅做语法与结构核验；无实时策略验收。`

## 限制

历史注释中的“确定性edge”和分数不是概率校准或盈利证明；未迁入执行器/钱包查询/AI自动下单；不运行常驻扫描。

运行时配置、凭据和业务状态不在Git中；不要去原生产目录寻找并复制认证材料。
