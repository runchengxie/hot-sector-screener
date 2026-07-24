# 配置项参考

默认配置在 `configs/default.yml`，也可通过 `--config` 指定其他 YAML 文件。

## 配置示例

```yaml
market: a_share

hotspot_sources:
  - dc_concept
  - dc_concept_cons
  - kpl_concept_cons
  - kpl_list
  - limit_step
  - limit_cpt_list
  - limit_list_ths

llm:
  enabled: true
  adapter: chat_completions
  prompt_template: default

universe:
  max_candidates: 100
  min_candidates: 30
  min_daily_amount_rank_pct: 80
  max_price: 200.0
  min_price: 2.0
  max_st_allow: false
  topics_per_run: 5
  stocks_per_topic: 25
  hotspot_feature_overlay: true
  hotspot_feature_weight: 0.25

output:
  format: csv
  publish: false
  export_signals: true
  signal_model_version: hotsector-theme-v3
  signal_feature_set_id: topic-concept-hotspot-overlay-theme-only-v1
  eligible_for_live: false

# rotation_signal_dir: /path/to/rotation-v3/run-20260619
```

## 字段说明

### 顶层

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `market` | `a_share` | 市场标识，当前仅支持 A 股 |
| `rotation_signal_dir` | null | 可选，固定 rotation-v3 run。其中 `signal_date` 仍必须不晚于观测日 |

### hotspot_sources

| 值 | 说明 |
|----|------|
| `dc_concept` | 东方财富概念板块 |
| `dc_concept_cons` | 东方财富概念成分 |
| `kpl_concept_cons` | 开盘啦概念成分 |
| `kpl_list` | 开盘啦涨停/炸板榜单 |
| `limit_step` | 连板天梯 |
| `limit_cpt_list` | 涨停概念榜单 |
| `limit_list_ths` | 同花顺涨停榜单 |

生产质量门默认要求以上 7 个关键源在同一观测日可用。`ths_hot`、`hotspot_features` 和 `daily` 是可选增强源，不参与默认共同日期门槛。

### llm

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `true` | 是否调用远端主题分类。关闭时明确使用确定性主题提取 |
| `adapter` | `chat_completions` | 供应商中立的 JSON 线协议适配器 |
| `prompt_template` | `default` | 提示词模板，当前仅支持 default |

远端模式不提供任何内建 endpoint、凭据、模型或供应商默认值，启动前必须显式设置：

| 环境变量 | 说明 |
|------|------|
| `LLM_API_URL` | HTTPS API base URL，不得包含凭据、query 或 fragment |
| `LLM_API_KEY` | 仅用于本次请求的凭据，不写入任何产物 |
| `LLM_MODEL` | 部署选择的模型标识，仅写入本地内部 lineage |
| `LLM_PROVIDER_ID` | 部署侧供应商标识，仅写入本地内部 lineage |

四项缺少任意一项，或远端请求、响应 schema、主题观测边界校验失败，`hotsector run`
都会以非 0 退出，不会静默切换到另一条生成路径。需要确定性主题提取时必须显式传
`--no-llm`，或在私有运行配置中设置 `enabled: false`。旧的 `llm.model` 和
`llm.provider` 配置会被明确拒绝，避免看似生效、实际未接线的配置漂移。

远端返回的自由文本在进入候选池前还会执行客户文案泄漏校验：供应商标识、模型标识、
API host、URL、凭据和系统元数据不得出现在 `topic` 或 `reasoning`。这些运行时信息只
保留在本地内部 `lineage.json`，不会进入候选池或信号产物。

### universe（候选池参数）

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `max_candidates` | 100 | 候选池最大股票数 |
| `min_candidates` | 30 | 候选池最小股票数（不保证达到） |
| `min_daily_amount_rank_pct` | 80 | 日成交额排名百分位下限 |
| `max_price` | 200.0 | 股票最高单价 |
| `min_price` | 2.0 | 股票最低单价 |
| `max_st_allow` | false | 是否允许 ST 股票 |
| `topics_per_run` | 5 | LLM 输出的主题数量 |
| `stocks_per_topic` | 25 | 每个主题最多选取的股票数 |
| `hotspot_feature_overlay` | true | 是否用 `hotspot_features` 对候选池排序做有界叠加 |
| `hotspot_feature_weight` | 0.25 | 派生热点特征叠加强度，0 表示关闭 |

### output

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `format` | `csv` | 输出格式，当前仅支持 csv |
| `publish` | false | 是否发布到外部系统（预留，当前无效） |
| `export_signals` | true | 是否输出 research-workspace 标准信号产物 |
| `signal_model_version` | `hotsector-theme-v3` | 写入信号产物的 `model_version`。v3 对应 candidate v2 的 theme-only 概念边界 |
| `signal_feature_set_id` | `topic-concept-hotspot-overlay-theme-only-v1` | 写入信号产物的 `feature_set_id` |
| `eligible_for_live` | false | 固定为 false。本仓只产候选池，下游发布门禁负责晋升 |
