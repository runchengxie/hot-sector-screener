# 架构和数据流

## 工作流程

```
从数据湖读取热点数据:
  同花顺热榜（ths_hot）
  东财概念成分（dc_concept_cons）
  开盘啦概念成分（kpl_concept_cons）
  派生热点特征（hotspot_features）
  ETF 轮动行业信号（来自 rotation-v3）
  所有输入的数据日期 <= observation_date；rotation 只取 signal_date <= observation_date

    ↓

LLM 分析当日热点 → 输出主题空间:
  输入: 今日热股 + 热点概念 + 行业信号
  输出: 3-5 个主题，每个主题带权重
  例: {"topic": "AI 医疗", "weight": 0.32, ...}

    ↓

确定性规则映射主题 → 候选股票:
  topic → 关联概念板块 → 概念成分股
  + 派生热点特征有界叠加
  + 价格 / 流动性过滤

    ↓

生成候选池:
  outputs/<YYYYMMDD>/
  ├── candidate_universe.csv
  ├── candidate_universe.json
  ├── holdings_eligibility_overlay.json  # 仅显式传入版本化持仓快照时
  ├── signals.parquet
  ├── signals.meta.json
  ├── lineage.json
  └── run_config.json
```

## 设计原则

- LLM 只做信息压缩和主题归类，不做选股。 LLM 输出契约只包含主题、权重、理由、关联概念和来源信号，不包含模型生成的股票代码、股票排名或AI 精选（AI 选股器产出的股票列表）文案。`related_concepts` 会与观测日输入概念词表求交。自由主题名和公司名不能触发股票映射。主题到具体股票的映射是确定性规则，不依赖模型判断。远端模式要求显式配置通用 provider adapter。配置、网络、响应、主题验证或客户文案泄漏校验失败都会终止运行。只有显式 `--no-llm` 或 `llm.enabled: false` 才使用基于概念名称频率的确定性提取。
- 生成路径按观测日封顶，但不冒充严格 PIT。 `observation_date` 表示已完成交易日的 EOD 数据截止点，rotation 信号不允许向未来回退，候选生成不读取 T+1/T+3/T+5 行情。由于 rotation 发布者尚未提供带 `published_at`、`data_cutoff` 和 hash 的 receipt，产物明确记录 `provenance_level=signal_date_only`、`strict_point_in_time=false` 和 OOS 限制。历史日事后重建不能解释为当时已可见。候选最早供下一交易时段使用。
- 候选不是 live 信号。 本仓生成的 `signals.*` 角色固定为 `candidate_universe`，`eligible_for_live=false`。只有下游研究和发布门禁可以晋升。
- 数据湖的单向读取。 从 `DATA_PLATFORM_ROOT` 读数据，不写回数据湖。
- 所有输出都在本地。 运行结果写在本仓库的 `outputs/` 目录下，不影响其他系统。
- 研究交接走文件契约。 `signals.parquet` 是给 research-workspace 的交接层，不从本项目直接 import 研究或回测模块。
- 持仓 overlay 只生产当日事实。 显式传入版本化持仓快照后，本仓把旧持仓加入更宽的
  每日资格池，严格按观测日重算主题、技术与流动性特征。无当日主题匹配时主题分为 0，
  无可信主题历史时年龄字段为 null，技术/流动性不前填。下游仍独立决定 rank buffer、
  margin、退出宽限和替换数量。

## 数据源

目前从数据湖中读取 5 类数据：

| 数据源 | 内部标识 | 说明 |
|--------|---------|------|
| 同花顺热榜 | `ths_hot` | 当日热门股票排名，包含热度、所属概念等 |
| 东财概念板块 | `dc_concept` | 东方财富概念板块涨跌幅和强度 |
| 东财概念成分 | `dc_concept_cons` | 东财各概念对应的成分股 |
| 开盘啦概念成分 | `kpl_concept_cons` | 开盘啦各概念对应的成分股 |
| 派生热点特征 | `hotspot_features` | 基于热榜派生的 19 维特征 |
| ETF 轮动行业信号 | `rotation-v3` | 来自 rotation-v3 项目的行业轮动信号 |

## 和其他项目的关系

这个项目在整个选股链路中处于中间层。

- 上游： rotation-v3 项目产出 ETF 行业轮动信号，作为 LLM 分析时的行业权重参考。
- 下游： 本项目的候选池输出给 `strategy-pipeline` 等排序或选股工具进一步处理。

候选池内的股票不构成交易建议，只表示当天在热点数据上值得关注。
