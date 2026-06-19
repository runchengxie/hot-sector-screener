# hotspot-universe

A-share 热点驱动选股池构建器。每天开盘前，根据数据湖中的同花顺热榜、东财概念、开盘啦概念、ETF 轮动信号，生成 50-100 只股票盯盘池，供 cross-sectional ranking 进一步排序。

## 设计原则

- **LLM 不做选股决策** — LLM 只做信息压缩和主题分类，topic→stock 映射是确定性的
- **数据湖单向依赖** — 读 DATA_PLATFORM_ROOT，不写回正式资产
- **独立迭代** — 不进入 research-workspace submodule，但 I/O contract 按 workspace 标准设计

## 架构

```
Layer B: Hotspot + Universe (本 repo)

Data Collection:
  ths_hot (同花顺热榜)
  dc_concept_cons (东财概念成分)
  kpl_concept_cons (开盘啦概念成分)
  hotspot_features (派生热点特征)
  rotation-v3 industry-signal (ETF轮动行业信号)

    ↓

LLM Topic Classification:
  输入: 今日热股 + 热点概念 + 行业信号
  输出: 主题空间 {topic: weight}

    ↓

Deterministic Mapping:
  topic → concept/theme → constituent stocks
  + liquidity/volatility/市值 filter

    ↓

Universe Output:
  candidate_universe (50-100 stocks + topic metadata)
```

## 快速开始

```bash
uv sync --extra dev
```

确保 `DATA_PLATFORM_ROOT` 环境变量指向 market-data-platform 数据湖：

```bash
export DATA_PLATFORM_ROOT=/home/richard/data/market-data-platform
```

查看可用的热点数据范围：

```bash
uv run hotspot info
```

运行一次预市热点检查（不调用 LLM，只收集数据）：

```bash
uv run hotspot scan
```

完整跑一次（收集数据 → LLM 分类 → 生成候选池）：

```bash
uv run hotspot run --date 2026-06-19
```

只看候选池输出：

```bash
uv run hotspot universe --date 2026-06-19
```

## 输出契约

候选池输出到 `outputs/<date>/`：

- `candidate_universe.csv` — 候选股票列表
- `candidate_universe.json` — 完整输出（含 topic graph）
- `lineage.json` — 数据溯源
- `run_config.json` — 运行配置快照

输出格式详见 `docs/output-contract.md`。

## 与 rotation-v3 的关系

```
rotation-v3                           hotspot-universe
  ┌──────────────┐                      ┌──────────────────┐
  │ ETF rotation  │ ──industry-signal──▶ │ 行业信号作为      │
  │ signal       │                      │ regime indicator  │
  └──────────────┘                      └──────────────────┘
                                                   │
                                        DATA_PLATFORM_ROOT
                                                   │
                                            ┌──────┴──────┐
                                            │ 候选池(50-100)│
                                            └──────┬──────┘
                                                   │
                                              cross-sectional-trees
                                              (ranking → top 15)
```
