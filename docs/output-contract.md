# 输出契约

每次执行 `hotsector run` 后，候选池输出到 `outputs/<YYYYMMDD>/` 目录下，包含以下文件：

- `candidate_universe.json`
- `candidate_universe.csv`
- `holdings_eligibility_overlay.json`（仅传入 `--holdings` 时）
- `candidate_quality.json`
- `candidate_outcomes.json`
- `signals.parquet`
- `signals.csv`
- `signals.meta.json`
- `lineage.json`
- `run_config.json`

## candidate_universe.json

完整输出结果，包含主题空间、候选股票列表以及运行元信息。新产物使用 v2。v1
字段保持冻结且仍可读取/校验，绝不原地升级或改写历史文件。两个可执行 canonical payload
分别见 [`candidate_universe.v1.json`](../examples/candidate_universe.v1.json) 和
[`candidate_universe.v2.json`](../examples/candidate_universe.v2.json)，仓内测试会直接调用
producer validator 校验。

```json
{
  "schema_version": "2.0.0",
  "artifact_type": "hot_sector_candidate_universe",
  "model_identity": {
    "model_id": "hotsector-theme-v3",
    "model_version": "3.0.0",
    "feature_set_id": "topic-concept-hotspot-overlay-theme-only-v1"
  },
  "source_concepts_policy": {
    "policy_id": "hotsector.source_concepts.theme_only",
    "version": "1.0.0",
    "allowed": ["theme", "concept", "related_concepts"],
    "excluded": ["tag", "lu_desc", "status", "rank_reason", "limit_type"],
    "normalizer_id": "hotsector.concept_token.v1",
    "canonical_sha256": "d14282e8047367ba61ea762cd3c3de56162329c12f1778c9681246ec7f0f0b40"
  },
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
  "source_mode": "normal",
  "fallback_reason": null,
  "source_gate": {
    "schema_version": "hotsector_source_gate.v1",
    "observation_date": "20260619",
    "source_mode": "normal",
    "fallback_reason": null,
    "mapping": {"kpl_complete": true, "dc_complete": false},
    "event_confirmation": {
      "minimum_required": 2,
      "available_count": 2,
      "sources": ["limit_list_ths", "limit_step"]
    },
    "sources": {
      "kpl_concept_cons": {
        "available": true, "exact_date": true, "complete": true,
        "row_count": 500, "observed_trade_dates": ["20260619"]
      },
      "limit_list_ths": {
        "available": true, "exact_date": true, "row_count": 60,
        "observed_trade_dates": ["20260619"]
      },
      "limit_step": {
        "available": true, "exact_date": true, "row_count": 20,
        "observed_trade_dates": ["20260619"]
      }
    }
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
      "source_concepts": ["CPO概念"],
      "source_event_tags": ["涨停"],
      "source_event_statuses": [],
      "source_event_reasons": ["CPO 板块事件确认"]
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
| `schema_version` | string | 新产物固定 `2.0.0`。validator 仍可显式读取冻结的 `1.0.0`，其他版本 fail closed |
| `artifact_type` | string | 固定 `hot_sector_candidate_universe` |
| `model_identity` | object | v2 固定模型、模型版本和 feature-set identity。消费者不得手抄另一套常量 |
| `source_concepts_policy` | object | v2 固定概念来源白名单、事件字段排除表、normalizer 和 canonical hash |
| `market` | string | 固定 `CN` |
| `date` | string | 交易日（yyyy-MM-dd 格式） |
| `date_int` | string | 交易日（yyyyMMdd 格式，用于目录路径） |
| `observation_date` | string | 观测日，表示本次候选使用的已完成交易日 |
| `data_cutoff` | string | 所有生成输入不得晚于该日期 |
| `execution_not_before` | string | 固定 `next_trading_session`，观测日不是执行日 |
| `future_data_included` | bool | 固定 `false` |
| `generated_at` | string | 带 UTC offset 的实际生成时间。同日 EOD 产物应在收盘后生成 |
| `provenance` | object | 观测日、时区、rotation 的 signal-date-only 证据与 `strict_point_in_time=false` 声明 |
| `evidence` | object | 生成时序、缺失 receipt 和不构成 OOS 有效性的限制说明 |
| `source_mode` | string | `normal`、`dc_fallback`、`event_fallback` 或 `blocked` |
| `fallback_reason` | string/null | normal 为 `null`，其余模式记录稳定原因码 |
| `source_gate` | object | 精确观测日映射完整性、事件确认源及逐源审计元数据 |
| `topics` | array | LLM 识别的当日主题空间，每个主题包含名称、权重、来源信号等 |
| `candidate_universe` | array | 候选股票列表 |
| `universe_size` | int | 候选池股票数量，通常在 50-100 只之间 |
| `config_snapshot` | object | 运行时的配置快照 |
| `data_sources` | object | 各数据源可用性标记 |
| `quality_report` | object | 生成阶段固定 deferred，不读取未来行情 |

### 生产来源四态门禁

- `normal`：目标日 KPL 成分未触及接口行数上限（或有显式完整性 receipt），且每行
  `name/con_code/con_name` 映射键完整可用，并有至少两个目标日事件确认源。
- `dc_fallback`：KPL 不完整或缺失，目标日 DC 题材成分的逐日 manifest 明确
  `complete=true`，并有至少两个目标日事件确认源。
- `event_fallback`：完整成员映射不可用，但事件确认源不少于两个。投递端必须明确展示
  事件型降级版，映射阶段不得消费截断的 DC/KPL 成分。
- `blocked`：事件确认源少于两个，不得进入生产投递。

事件确认源为 `limit_list_ths`、`limit_step`、`limit_cpt_list`、`ths_hot`。
每个来源的每一行 `trade_date` 必须合法且严格等于 `observation_date`，空值、非法日期或
D-2 数据均不得冒充 D-1。
`dc_concept_cons` 仅非空不构成完整性证据。必须读取
`manifest.completeness.trade_dates[observation_date]` 的逐日 receipt，并校验行数、页数、
终止页、题材数以及 `coverage.row_coverage_ratio=1.0` 与分区一致。

### 候选股票字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `ts_code` | string | 股票代码，例如 `300308.SZ` |
| `name` | string | 股票名称 |
| `relevance` | float | 必填、有限。与主题的相关性得分，0-1 之间 |
| `score` | float | 必填、有限。主题权重、概念强度、成分热度等聚合后的原始分 |
| `hotspot_feature_score` | float | 派生热点特征得分，0-1。有对应特征时输出 |
| `hotspot_score_multiplier` | float | 派生热点特征叠加到原始分上的有界乘数 |
| `liquidity_score` | float | 流动性分，来自成交额分位，0-1 之间 |
| `amount_rank_pct` | float | 当日成交额在全市场中的百分位 |
| `close` | float | 当日收盘价，用于价格过滤 |
| `source_topics` | array | 必填字符串数组。该股票匹配到的主题列表 |
| `source_concepts` | array | 必填字符串数组。仅来自 `theme/concept/related_concepts` 的标准化概念 |
| `source_event_tags` | array | v2 必填、可为空。事件标签，只作解释，不进入概念匹配或 breadth |
| `source_event_statuses` | array | v2 必填、可为空。`status/limit_type` 事件状态，只作解释 |
| `source_event_reasons` | array | v2 必填、可为空。`lu_desc/rank_reason` 事件说明，只作解释 |

`source_concepts_policy.canonical_sha256` 对不含 hash 本身的 policy 对象使用 UTF-8
canonical JSON（key 排序、无多余空格、`ensure_ascii=false`）计算。v2 validator 要求整个
policy 和 `model_identity` 与 owner 常量精确一致，因此任何字段漂移都会 fail closed。

## holdings_eligibility_overlay.json

传入版本化持仓快照时，producer 额外写出独立的 companion artifact。它不会改变冻结的
candidate v1/v2 schema，也不会把继续持有/替换谁的组合决策放进候选生产者。
输入示例见 [`holdings_snapshot.v1.json`](../examples/holdings_snapshot.v1.json)：

```json
{
  "schema_version": "1.0.0",
  "artifact_type": "hot_sector_holdings_snapshot",
  "market": "CN",
  "as_of_date": "20260618",
  "symbols": ["000001.SZ", "600000.SH"]
}
```

输出固定为 `hot_sector_holdings_eligibility_overlay/1.0.0`，使用
`hotsector.holdings_overlay.daily_rescore/1.0.0` feature policy，并包含基础候选池与声明持仓
的并集。关键字段语义如下：

| 字段 | 说明 |
|------|------|
| `entry_eligible` | 当日仍匹配主题，且通过当日价格、可交易性和入池流动性门槛。不是买入指令 |
| `hold_eligible` | 当日通过硬性市场门禁，且存在严格截至观测日的技术特征。不是继续持有指令 |
| `current_theme_match` | 是否仍在本次观测日的主题映射池中 |
| `theme_score` / `theme_relevance` | 当日主题分。无当日主题匹配时严格为 `0` |
| `last_theme_seen` / `theme_age` | 当日匹配时分别为观测日和 `0`。无可信主题历史时均为 `null`，不猜测 |
| `technical_as_of_date` | 技术特征最后日期。历史未精确结束于观测日时为 `null`，所有技术字段同时为 `null` |
| `amount_rank_pct` / `liquidity_score` | 从观测日全市场 daily cross-section 重新计算，不使用旧候选值 |
| `*_ineligible_reasons` | 稳定原因码。资格布尔值与原因数组必须一致 |

根级 `eligibility_parameters` 固化本次实际使用的入池成交额分位、价格上下限和 ST 开关。
feature policy 固定算法语义，参数快照固定某次运行的具体阈值，两者都不能由消费者猜测。

`hold_eligible` 故意不复用新股票的成交额分位准入阈值，从而允许下游研究买入严格、卖出
稍宽的滞回规则。价格越界、ST、一字板、缺少当日行情或缺少当日技术特征仍会 fail closed。
producer 不输出 Top-N、保留期、最多替换数或权重，这些继续由组合/回测 owner 决定。

主题历史目前没有可校验的逐股 append-only 来源。因此，对于当天已经不匹配主题的旧持仓，
`last_theme_seen` 和 `theme_age` 明确为 nullable。不能从旧冻结 manifest 猜测或前填。与主候选
产物一致，overlay 仍标记 `strict_point_in_time=false`，只能用于 research-only 实验。
跨仓消费者应调用 `hotsector validate-holdings-overlay --input <path>` 复用 owner validator，
并读取其 canonical 摘要，不应重写 schema 校验或手抄 policy 常量。

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
| `eligible_for_backtest` | bool | 候选契约通过后可进入独立回测。不表示已有 OOS 证据 |
| `eligible_for_live` | bool | 固定为 `false`。下游发布门禁负责晋升 |

会保留部分候选池辅助字段，例如 `name`、`source_topics`、`source_concepts`、
`source_event_tags`、`source_event_statuses`、`source_event_reasons`、
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
  "schema_version": "2.0.0",
  "artifact_type": "hot_sector_candidate_universe",
  "model_identity": {"model_id": "hotsector-theme-v3", "model_version": "3.0.0", "feature_set_id": "topic-concept-hotspot-overlay-theme-only-v1"},
  "source_concepts_policy": {"policy_id": "hotsector.source_concepts.theme_only", "version": "1.0.0", "canonical_sha256": "d14282e8047367ba61ea762cd3c3de56162329c12f1778c9681246ec7f0f0b40"},
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
但绝不记录 API key。显式 `--no-llm` 或 `llm.enabled: false` 时记录
`mode: deterministic`，`--load-topics` 时记录 `mode: external_topics`。

## run_config.json

运行时使用的完整配置快照，内容来自 `configs/default.yml` 或 `--config` 指定的配置文件。用于复现或回溯分析。
