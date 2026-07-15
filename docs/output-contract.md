# 输出契约

每次执行 `hotsector run` 后，候选池输出到 `outputs/<YYYYMMDD>/` 目录下，包含以下文件：

- `candidate_universe.json`
- `candidate_universe.csv`
- `candidate_quality.json`
- `candidate_outcomes.json`
- `signals.parquet`
- `signals.csv`
- `signals.meta.json`
- `lineage.json`
- `run_config.json`

## candidate_universe.json

完整输出结果，包含主题空间、候选股票列表以及运行元信息。v1 字段已冻结；可执行的 canonical payload 见 [`examples/candidate_universe.v1.json`](../examples/candidate_universe.v1.json)，并由仓内测试直接调用 producer validator 校验。

```json
{
  "schema_version": "1.0.0",
  "artifact_type": "hot_sector_candidate_universe",
  "market": "CN",
  "date": "2026-06-19",
  "date_int": "20260619",
  "observation_date": "20260619",
  "data_cutoff": "20260619",
  "data_cutoff_semantics": "end_of_day",
  "execution_not_before": "next_trading_session",
  "future_data_included": false,
  "generated_at": "2026-06-19T16:30:00+08:00",
  "provenance": {
    "timezone": "Asia/Shanghai",
    "observation_date": "20260619",
    "data_cutoff": "20260619",
    "future_data_included": false,
    "artifact_role": "candidate_universe",
    "strict_point_in_time": false,
    "rotation": {
      "as_of_date": "20260619",
      "signal_date": null,
      "provenance_level": "unavailable",
      "strict_point_in_time": false,
      "publisher_receipt_verified": false
    }
  },
  "evidence": {
    "strict_point_in_time": false,
    "out_of_sample_claim": false,
    "temporal_context": "same_day_eod_generation",
    "limitations": [
      "rotation_publisher_receipt_unavailable",
      "candidate_artifact_does_not_establish_out_of_sample_validity"
    ]
  },
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
  "universe_size": 1,
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
    "available": false,
    "reason": "future_data_excluded_from_generation",
    "horizons": {}
  },
  "outcome_report": {
    "available": false,
    "reason": "future_data_excluded_from_generation",
    "horizons": {}
  }
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `schema_version` | string | 固定 `1.0.0`；旧版或缺失版本的产物会被 fail closed 拒绝 |
| `artifact_type` | string | 固定 `hot_sector_candidate_universe` |
| `market` | string | 固定 `CN` |
| `date` | string | 交易日（yyyy-MM-dd 格式） |
| `date_int` | string | 交易日（yyyyMMdd 格式，用于目录路径） |
| `observation_date` | string | 观测日，表示本次候选使用的已完成交易日 |
| `data_cutoff` | string | 所有生成输入不得晚于该日期 |
| `execution_not_before` | string | 固定 `next_trading_session`，观测日不是执行日 |
| `future_data_included` | bool | 固定 `false` |
| `generated_at` | string | 带 UTC offset 的实际生成时间；同日 EOD 产物应在收盘后生成 |
| `provenance` | object | 观测日、时区、rotation 的 signal-date-only 证据与 `strict_point_in_time=false` 声明 |
| `evidence` | object | 生成时序、缺失 receipt 和不构成 OOS 有效性的限制说明 |
| `topics` | array | LLM 识别的当日主题空间，每个主题包含名称、权重、来源信号等 |
| `candidate_universe` | array | 候选股票列表 |
| `universe_size` | int | 候选池股票数量，通常在 50-100 只之间 |
| `config_snapshot` | object | 运行时的配置快照 |
| `data_sources` | object | 各数据源可用性标记 |
| `quality_report` | object | 生成阶段固定 deferred，不读取未来行情 |

### 候选股票字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `ts_code` | string | 股票代码，例如 `300308.SZ` |
| `name` | string | 股票名称 |
| `relevance` | float | 必填、有限；与主题的相关性得分，0-1 之间 |
| `score` | float | 必填、有限；主题权重、概念强度、成分热度等聚合后的原始分 |
| `hotspot_feature_score` | float | 派生热点特征得分，0-1；有对应特征时输出 |
| `hotspot_score_multiplier` | float | 派生热点特征叠加到原始分上的有界乘数 |
| `liquidity_score` | float | 流动性分，来自成交额分位，0-1 之间 |
| `amount_rank_pct` | float | 当日成交额在全市场中的百分位 |
| `close` | float | 当日收盘价，用于价格过滤 |
| `source_topics` | array | 必填字符串数组；该股票匹配到的主题列表 |
| `source_concepts` | array | 必填字符串数组；该股票命中的标准化概念 |

## candidate_universe.csv

候选股票的表格化输出，仅包含 `candidate_universe` 数组中的字段。适合直接导入其他工具或 Excel 查看。

## candidate_quality.json

候选生成阶段只写以下 deferred stub：

```json
{
  "available": false,
  "reason": "future_data_excluded_from_generation",
  "horizons": {}
}
```

`hotsector run` 不读取 T+1/T+3/T+5 行情。候选池后续表现必须由独立的事后评价 owner/流程在未来数据实际可用后计算，不能回填并影响原始候选或 live eligibility。

## candidate_outcomes.json

与 `candidate_quality.json` 相同，生成阶段只记录 deferred stub。保留文件名用于兼容既有产物消费者，不代表生成流程已经观察到未来结果。

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
| `eligible_for_backtest` | bool | 当前 v1 候选契约通过后可进入独立回测；不表示已有 OOS 证据 |
| `eligible_for_live` | bool | 固定为 `false`；下游发布门禁负责晋升 |

会保留部分候选池辅助字段，例如 `name`、`source_topics`、`source_concepts`、
`liquidity_score`、`amount_rank_pct`、`hotspot_feature_score`。

## signals.meta.json

记录信号契约、schema 版本、行数、来源候选池、数据源可用性和运行配置快照。关键边界字段包括：

- `artifact_role: candidate_universe`
- `execution_eligible: false`
- `data_cutoff: <observation_date>`
- `data_cutoff_semantics: end_of_day`
- `execution_not_before: next_trading_session`
- `future_data_included: false`
- `strict_point_in_time: false`
- `evidence.out_of_sample_claim: false`
- `evidence.temporal_context: same_day_eod_generation | post_observation_generation`

## lineage.json

数据溯源文件，记录每次运行的输入来源和产出结果之间的对应关系。

```json
{
  "schema_version": "1.0.0",
  "artifact_type": "hot_sector_candidate_universe",
  "market": "CN",
  "date": "2026-06-19",
  "observation_date": "20260619",
  "data_cutoff": "20260619",
  "data_cutoff_semantics": "end_of_day",
  "execution_not_before": "next_trading_session",
  "future_data_included": false,
  "generated_at": "2026-06-19T16:30:00+08:00",
  "run_config": "run_config.json",
  "data_sources": {
    "ths_hot_available": true,
    ...
  },
  "topic_classification": {
    "mode": "remote",
    "provider_receipt": {
      "protocol": "chat_completions.v1",
      "provider_id": "<private-runtime-value>",
      "model": "<private-runtime-value>",
      "api_host": "<private-runtime-value>",
      "prompt_sha256": "<sha256>",
      "response_sha256": "<sha256>"
    }
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

`topic_classification` 是本地内部审计字段，不进入 `candidate_universe.json`、信号文件或
客户展示。远端模式记录部署侧供应商标识、模型、API host、完整无凭据请求语义的
`prompt_sha256`（system/user messages、temperature、max tokens 和 model）以及响应 hash，
但绝不记录 API key；显式 `--no-llm` 或 `llm.enabled: false` 时记录
`mode: deterministic`，`--load-topics` 时记录 `mode: external_topics`。

## run_config.json

运行时使用的完整配置快照，内容来自 `configs/default.yml` 或 `--config` 指定的配置文件。用于复现或回溯分析。
