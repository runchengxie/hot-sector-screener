# hot-sector-screener

A 股热点题材候选池筛选器。每天开盘前，根据同花顺热榜、东财概念板块、开盘啦概念成分、ETF 轮动信号等多来源数据，自动生成 50-100 只股票的盯盘候选池。这个池子交给后续的选股流程进一步筛选使用。

## 快速开始

```bash
# 克隆
git clone <repo-url>

# 安装依赖
uv sync --extra dev
```

设置数据湖路径（指向本地的 market-data-platform parquet 数据目录，建议加到 `.bashrc`）：

```bash
export DATA_PLATFORM_ROOT=/home/yourname/data/market-data-platform
```

查看数据湖中有哪些热点数据可用：

```bash
uv run hotsector info
```

跑一次完整流水线（收集数据 → LLM 分析 → 生成候选池）：

```bash
uv run hotsector run --date 2026-06-19
```

查看某天的候选池结果：

```bash
uv run hotsector universe --date 2026-06-19
```

本机日更入口为顶层 `scripts/hotsector_research_handoff.sh`。默认只生成候选池和
`signals.parquet`，不运行 research-workspace，也不导出执行目标；质量门失败时返回非 0。

## 输出文件

每次运行结果写到 `outputs/<YYYYMMDD>/`：

- `candidate_universe.csv` — 候选股票表格，可用 Excel 打开
- `candidate_universe.json` — 完整结果（含主题空间、数据源状态等）
- `candidate_quality.json` — T+1/T+3/T+5 候选池表现回看（后续行情可用时）
- `signals.parquet` / `signals.csv` — research-workspace 可消费的标准信号产物
- `signals.meta.json` — 信号契约和来源元数据
- `lineage.json` — 数据溯源记录
- `run_config.json` — 本次运行的配置快照

详细字段说明见 [docs/output-contract.md](docs/output-contract.md)。

## 目录结构

```
hot-sector-screener/
├── docs/                       # 文档
│   ├── architecture.md         #   架构、数据流、设计原则
│   ├── commands.md             #   命令详细用法
│   ├── configuration.md        #   配置项参考
│   └── output-contract.md      #   输出格式说明
├── configs/
│   ├── default.yml             # 默认配置
│   └── experiments/            # 实验配置
├── src/hot_sector_screener/    # 源码
├── tests/                      # 测试
├── outputs/                    # 运行输出
└── examples/                   # 示例（提示词、主题）
```

## 了解更多

- [架构和数据流](docs/architecture.md) — 工作流程、设计原则、与其他项目的关系
- [命令详解](docs/commands.md) — 全部 5 个子命令和参数说明
- [配置项参考](docs/configuration.md) — 配置文件各字段含义
- [输出格式说明](docs/output-contract.md) — 输出文件的 JSON 结构和字段
- [回测脚本](docs/backtests.md) — 两个独立的回测脚本说明
- [Research Workspace 交接](docs/research-workspace-handoff.md) — 标准信号产物和跨项目调度
