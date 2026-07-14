# 输出契约

每次执行 `hotsector run` 后，候选池输出到 `outputs/<YYYYMMDD>/` 目录下，包含以下文件：

- `candidate_universe.json`
- `candidate_universe.csv`
- `candidate_quality.json`
- `signals.parquet`
- `signals.csv`
- `signals.meta.json`
- `lineage.json`
- `run_config.json`

## candidate_universe.json

完整输出结果，包含主题空间、候选股票列表以及运行元信息。

```json
{
  "date": "2026-06-19",
  "date_int": "20260619",
  "generated_at": "2026-06-19T08:30:00",
  "topics": [
    {
      "topic": "AI医疗",
      "weight": 0.32,
      "reasoning": "同花顺热榜 5 只 AI 医疗相关股票上榜",
      "related_concepts": ["AI医疗", "互联网医疗"],
      "source_signals": ["ths_hot"]
    }
  ],
  "candidate_universe": [
    {
      "ts_code": "300308.SZ",
      "name": "中际旭创",
      "relevance": 0.95,
      "score": 1.42,
      "hotspot_feature_score": 0.88,
      "hotspot_score_multiplier": 1.08,
      "liquidity_score": 0.93,
      "amount_rank_pct": 93.2,
      "close": 120.5,
      "source_topics": ["CPO光通信"],
      "source_concepts": ["CPO概念"]
    }
  ],
  "universe_size": 85,
  "config_snapshot": {
    "max_candidates": 100,
    "min_candidates": 30,
    "llm_enabled": true
  },
  "data_sources": {
    "ths_hot_available": true,
    "dc_concept_available": true,
    "dc_concept_cons_available": true,
    "kpl_concept_cons_available": true,
    "daily_available": true,
    "industry_signal_available": false
  },
  "quality_report": {
    "available": true,
    "horizons": {
      "t_plus_1": {
        "available": true,
        "count": 85,
        "mean_return_pct": 0.82,
        "median_return_pct": 0.31,
        "hit_rate_pct": 57.6
      }
    }
  },
  "output_dir": "/home/richard/code/hot-sector-screener/outputs/20260619"
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `date` | string | 交易日（yyyy-MM-dd 格式） |
| `date_int` | string | 交易日（yyyyMMdd 格式，用于目录路径） |
| `generated_at` | string | 生成时间戳 |
| `topics` | array | LLM 识别的当日主题空间，每个主题包含名称、权重、来源信号等 |
| `candidate_universe` | array | 候选股票列表 |
| `universe_size` | int | 候选池股票数量，通常在 50-100 只之间 |
| `config_snapshot` | object | 运行时的配置快照 |
| `data_sources` | object | 各数据源可用性标记 |
| `quality_report` | object | 候选池后续表现回看；后续行情尚不可用时 `available=false` |

### 候选股票字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `ts_code` | string | 股票代码，例如 `300308.SZ` |
| `name` | string | 股票名称 |
| `relevance` | float | 与主题的相关性得分，0-1 之间 |
| `score` | float | 主题权重、概念强度、成分热度等聚合后的原始分 |
| `hotspot_feature_score` | float | 派生热点特征得分，0-1；有对应特征时输出 |
| `hotspot_score_multiplier` | float | 派生热点特征叠加到原始分上的有界乘数 |
| `liquidity_score` | float | 流动性分，来自成交额分位，0-1 之间 |
| `amount_rank_pct` | float | 当日成交额在全市场中的百分位 |
| `close` | float | 当日收盘价，用于价格过滤 |
| `source_topics` | array | 该股票匹配到的主题列表 |
| `source_concepts` | array | 该股票命中的标准化概念 |

## candidate_universe.csv

候选股票的表格化输出，仅包含 `candidate_universe` 数组中的字段。适合直接导入其他工具或 Excel 查看。

## candidate_quality.json

候选池质量回看。若数据湖中已有后续交易日的日线数据，会输出 T+1/T+3/T+5 的平均收益、中位数收益、胜率和样本数；否则 `available=false` 并给出 `reason`。

## signals.parquet / signals.csv

research-workspace 可消费的标准信号产物，契约名为 `alpha_research.signals`：

| 字段 | 类型 | 说明 |
|------|------|------|
| `signal_date` | string | `YYYYMMDD` |
| `symbol` | string | 股票代码 |
| `raw_pred` | float | 候选池原始分 |
| `signal_eval` | float | 评估分 |
| `signal_backtest` | float | 回测分 |
| `signal_direction` | float | 多头方向，固定为 1 |
| `rank` | int | 当日排名 |
| `model_version` | string | 信号模型版本 |
| `feature_set_id` | string | 特征/信号口径 |
| `eligible_for_backtest` | bool | 是否可用于回测 |
| `eligible_for_live` | bool | 是否可用于 live 候选 |

会保留部分候选池辅助字段，例如 `name`、`source_topics`、`source_concepts`、
`liquidity_score`、`amount_rank_pct`、`hotspot_feature_score`。

## signals.meta.json

记录信号契约、schema 版本、行数、来源候选池、数据源可用性和运行配置快照。

## lineage.json

数据溯源文件，记录每次运行的输入来源和产出结果之间的对应关系。

```json
{
  "date": "2026-06-19",
  "generated_at": "2026-06-19T08:30:00",
  "run_config": "run_config.json",
  "data_sources": {
    "ths_hot_available": true,
    ...
  },
  "topics_count": 4,
  "universe_size": 85,
  "output_files": {
    "json": "outputs/20260619/candidate_universe.json",
    "csv": "outputs/20260619/candidate_universe.csv",
    "quality": "outputs/20260619/candidate_quality.json",
    "signals": {
      "parquet": "outputs/20260619/signals.parquet",
      "csv": "outputs/20260619/signals.csv",
      "metadata": "outputs/20260619/signals.meta.json"
    }
  }
}
```

## run_config.json

运行时使用的完整配置快照，内容来自 `configs/default.yml` 或 `--config` 指定的配置文件。用于复现或回溯分析。
