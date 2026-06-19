# 配置项参考

默认配置在 `configs/default.yml`，也可通过 `--config` 指定其他 YAML 文件。

## 配置示例

```yaml
market: a_share

hotspot_sources:
  - ths_hot
  - dc_concept
  - kpl_concept

llm:
  enabled: true
  model: deepseek-reasoner
  provider: deepseek
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

output:
  format: csv
  publish: false

# rotation_signal_dir: /path/to/rotation-v3/latest
```

## 字段说明

### 顶层

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `market` | `a_share` | 市场标识，当前仅支持 A 股 |
| `rotation_signal_dir` | null | 可选，覆盖 rotation-v3 行业信号的读取路径 |

### hotspot_sources

| 值 | 说明 |
|----|------|
| `ths_hot` | 同花顺热榜 |
| `dc_concept` | 东方财富概念板块 |
| `kpl_concept` | 开盘啦概念 |

当前固定支持这 3 个来源，配置项作为可扩展预留。

### llm

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `true` | 是否调用 LLM。关闭时使用兜底主题提取 |
| `model` | `deepseek-reasoner` | LLM 模型名称 |
| `provider` | `deepseek` | LLM 服务商 |
| `prompt_template` | `default` | 提示词模板，当前仅支持 default |

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

### output

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `format` | `csv` | 输出格式，当前仅支持 csv |
| `publish` | false | 是否发布到外部系统（预留，当前无效） |
