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
- `candidate_outcomes.json`
- `lineage.json`
- `run_config.json`
- `signals.parquet`
- `signals.csv`
- `signals.meta.json`

`signals.parquet` 使用 `alpha_research.signals` 契约字段：

| 字段 | 说明 |
|------|------|
| `signal_date` | 信号日期，`YYYYMMDD` |
| `symbol` | A 股 `ts_code` |
| `raw_pred` | 候选池原始分 |
| `signal_eval` | 评估分，当前使用过滤后的相关性 |
| `signal_backtest` | 回测分，当前使用过滤后的相关性 |
| `signal_direction` | 多头方向，固定为 `1.0` |
| `rank` | 当日按 `signal_backtest` 降序排名 |
| `model_version` | 默认 `hotsector-theme-v3` |
| `feature_set_id` | 默认 `topic-concept-hotspot-overlay-theme-only-v1` |
| `eligible_for_backtest` | 通过 owner 候选结构契约，可进入独立回测。不表示已有 OOS 证据 |
| `eligible_for_live` | 固定 `false`。本层产物只能作为研究候选 |

## 手动运行

```bash
cd ~/code/market-intel/hot-sector-screener
DATA_PLATFORM_ROOT=$HOME/data/market-data-platform \
  uv run hotsector run --date 2026-06-29 --no-llm

# 如需从已有 candidate_universe.json 重新导出
uv run hotsector export-signals --date 2026-06-29
```

## 跨项目调度

该交接脚本（`scripts/hotsector_research_handoff.sh`）已移除。当前跨项目调度由 `market-intel`
主仓统一编排，本仓只通过 `hotsector` 命令行入口产出第一级日更产物。

生成候选池与标准信号（第一级日更产物）：

```bash
cd ~/code/market-intel/hot-sector-screener
uv run hotsector run --date 2026-06-29 --no-llm
uv run hotsector export-signals --date 2026-06-29
```

第一级产物为：

```text
candidate_universe.json + candidate_universe.csv + signals.parquet
```

这一级不会运行 `research-workspace`，也不会导出执行目标。

未指定日期时，可用 `hotsector latest-date` 选择关键热点/概念源共同可用的最近交易日。
随后执行 `hotsector validate-output`，要求关键源可用、候选数量达到 `min_candidates`、
并且 `signals.parquet` 非空。`daily` 只读取观测日及此前数据，用于流动性过滤和技术确认。
第一级生成路径不会读取未来行情，`candidate_quality.json` 和 `candidate_outcomes.json`
只写 deferred stub。事后评价由独立研究流程完成。质量门失败时命令返回非 0，避免把空文件当作每日建议。

要继续触发 `strategy-pipeline`，把本次 `signals.parquet` 文件路径指给 `strategy-pipeline`
的 `hotsector_overlay` preset 读取。需要继续导出执行目标时，由下游流程消费该信号产物：

```bash
HOTSECTOR_SIGNAL_FILE=outputs/2026-06-29/signals.parquet \
STRATEGY_CONFIG=hotsector_overlay \
uv run -p research-workspace strategy-pipeline run
```

## 定时调度说明

本仓原先提供的 `scripts/systemd/hotsector-research-handoff.service`、
`scripts/systemd/hotsector-research-handoff.timer`、`scripts/setup_cron.sh`、
`scripts/windows/install_scheduled_tasks.ps1` 均已移除，不再内置定时调度。
当前日更任务由 `market-intel` 主仓统一调度，通过上文的 `hotsector run`、
`hotsector export-signals`、`hotsector validate-output` 三个命令行入口触发，
默认只生成候选池和信号，不自动跑研究或导出执行目标。如需在本机自管定时任务，
可在调度器（systemd timer、cron 或 Windows 任务计划程序）里直接调用上述 `uv run hotsector ...`
命令，无需本仓额外脚本。

## 策略口径

`hotsector-theme-v3` 保持确定性主题映射为主，并将概念来源收窄为
`theme/concept/related_concepts`。事件标签、状态和说明只作为解释元数据。派生热点特征叠加：

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
