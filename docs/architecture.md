# 架构和数据流

## 工作流程

```
从数据湖读取热点数据:
  同花顺热榜（ths_hot）
  东财概念成分（dc_concept_cons）
  开盘啦概念成分（kpl_concept_cons）
  派生热点特征（hotspot_features）
  ETF 轮动行业信号（来自 rotation-v3）

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
  ├── signals.parquet
  ├── signals.meta.json
  ├── lineage.json
  └── run_config.json
```

## 设计原则

- **LLM 只做信息压缩和主题归类，不做选股。** 主题到具体股票的映射是确定性规则，不依赖模型判断。即使 LLM 调用失败或结果不可用，系统也有基于概念名称频率的兜底提取逻辑。
- **数据湖的单向读取。** 从 `DATA_PLATFORM_ROOT` 读数据，不写回数据湖。
- **所有输出都在本地。** 运行结果写在本仓库的 `outputs/` 目录下，不影响其他系统。
- **研究交接走文件契约。** `signals.parquet` 是给 research-workspace 的交接层，不从本项目直接 import 研究或回测模块。

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

- **上游：** rotation-v3 项目产出 ETF 行业轮动信号，作为 LLM 分析时的行业权重参考。
- **下游：** 本项目的候选池输出给 cross-sectional-trees 等排序或选股工具进一步处理。

候选池内的股票不构成交易建议，只表示当天在热点数据上值得关注。
