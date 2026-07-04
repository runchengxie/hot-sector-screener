# Research Workspace Handoff

`hot-sector-screener` 负责热点主题候选池，不负责最终组合、回测、目标持仓或执行。
和 `research-workspace` 的交接通过文件契约完成，避免两个仓库互相 import。

## 分层

```text
market-intel / hot-sector-screener
  热点数据 -> 主题空间 -> 候选股票 -> signals.parquet

research-workspace / strategy-pipeline
  signals.parquet -> StrategySpec -> positions_by_rebalance.csv -> targets.json

quant-execution-engine
  targets.json -> preflight / dry-run / execution evidence
```

## 产物

每次 `hotsector run` 默认会在 `outputs/<YYYYMMDD>/` 写出：

- `candidate_universe.json`
- `candidate_universe.csv`
- `candidate_quality.json`
- `lineage.json`
- `run_config.json`
- `signals.parquet`
- `signals.csv`
- `signals.meta.json`

`signals.parquet` 使用 `cstree.signals` 兼容字段：

| 字段 | 说明 |
|------|------|
| `signal_date` | 信号日期，`YYYYMMDD` |
| `symbol` | A 股 `ts_code` |
| `raw_pred` | 候选池原始分 |
| `signal_eval` | 评估分，当前使用过滤后的相关性 |
| `signal_backtest` | 回测分，当前使用过滤后的相关性 |
| `signal_direction` | 多头方向，固定为 `1.0` |
| `rank` | 当日按 `signal_backtest` 降序排名 |
| `model_version` | 默认 `hotsector-theme-v2` |
| `feature_set_id` | 默认 `topic-concept-hotspot-overlay` |
| `eligible_for_backtest` | 是否可回测 |
| `eligible_for_live` | 是否可进入 live 候选 |

## 手动运行

```bash
cd ~/code/market-intel/hot-sector-screener
DATA_PLATFORM_ROOT=$HOME/data/market-data-platform \
  uv run hotsector run --date 2026-06-29 --no-llm

# 如需从已有 candidate_universe.json 重新导出
uv run hotsector export-signals --date 2026-06-29
```

## 跨项目调度

顶层脚本：

```bash
cd ~/code/market-intel
TRADE_DATE=20260629 scripts/hotsector_research_handoff.sh
```

默认只生成候选池和标准信号。要继续触发 `strategy-pipeline`，显式打开：

```bash
RUN_RESEARCH=1 \
STRATEGY_CONFIG=hotsector_overlay \
TRADE_DATE=20260629 \
scripts/hotsector_research_handoff.sh
```

脚本会把 `HOTSECTOR_SIGNAL_FILE` 指向本次 `signals.parquet`，供
`strategy-pipeline` 的配置或桥接命令读取。

## systemd 示例

示例 unit 位于：

- `scripts/systemd/hotsector-research-handoff.service`
- `scripts/systemd/hotsector-research-handoff.timer`

默认 `RUN_RESEARCH=0`，也就是只生成信号，不自动跑研究或导出执行目标。正式启用研究链路前，
应先在 `strategy-pipeline` 增加并验证 `hotsector_overlay` 配置。

## 策略口径

`hotsector-theme-v2` 保持确定性主题映射为主，新增派生热点特征叠加：

1. 主题权重、概念强度、成分热度生成基础分。
2. `hotspot_features` 里的热榜分位、主题强度、近期涨停/连板、调研和券商推荐等字段生成
   `hotspot_feature_score`。
3. 派生特征只做有界乘数，默认权重 `0.25`，不会替代主题映射。
4. 最后再执行成交额分位、价格、ST 和一字板过滤。

调参入口在 `configs/default.yml`：

```yaml
universe:
  hotspot_feature_overlay: true
  hotspot_feature_weight: 0.25

output:
  export_signals: true
```
