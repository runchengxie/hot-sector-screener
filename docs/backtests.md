# 回测脚本

回测已接入 `hotsector backtest` CLI，用于验证热点数据的选股/选基效果。

## 热点追踪策略回测

入口： `uv run hotsector backtest stock`

### 方法论

1. 每天从同花顺热榜提取排名前 N 只热股，统计这些股票上出现的概念标签频率
2. 频率最高的 K 个概念作为 今日热点
3. 通过开盘啦概念成分表把概念映射到具体股票
4. 等权买入这些股票，持有 1 天，次日再平衡
5. 对比沪深 300 基准收益

### 数据依赖

| 数据源 | 说明 |
|--------|------|
| `ths_hot` | 同花顺热榜（约 576 天，2024-01-02 起） |
| `kpl_concept_cons` | 开盘啦概念成分股（约 394 天，2024-10-14 起） |
| daily A 股日线 | 用于计算持仓收益和基准 |

数据均通过 `DATA_PLATFORM_ROOT` 从数据湖读取。

### 运行

```bash
DATA_PLATFORM_ROOT=/home/yourname/data/market-data-platform \
  uv run hotsector backtest stock
```

### 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--start` | `2024-10-14` | 回测开始日期 |
| `--end` | `2026-05-01` | 回测结束日期 |
| `--top-concepts` | 3 | 每天选取的概念数量 |
| `--stocks-per-concept` | 10 | 每个概念选取的股票数 |
| `--sample` | 3 | 每 N 个交易日采样一次（提速用） |

### 输出解读

脚本输出 JSON 格式的回测指标：

- `strategy.total_return_pct` — 策略累计收益率
- `strategy.sharpe` — 夏普比率
- `strategy.hit_rate_pct` — 胜率（正收益交易占比）
- `strategy.max_drawdown_pct` — 最大回撤
- `benchmark.total_return_pct` — 沪深 300 同期收益
- `excess_return_pct` — 超额收益
- `concept_samples` — 前 10 次交易的概念选择记录，用于定性分析

## Hotspot→ETF 轮动回测

入口： `uv run hotsector backtest etf`

### 方法论

1. 每天从同花顺热榜提取热股的概念标签
2. 概念标签映射到预定义的 ETF 曝光维度
3. 各 ETF 根据其曝光维度打分，选出得分最高的 K 只 ETF
4. 等权买入，持有至下一交易日
5. 对比沪深 300

### 数据依赖

| 数据源 | 说明 |
|--------|------|
| `ths_hot` | 同花顺热榜（数据湖） |
| ETF 日线 | rotation-v3 下载的 18 只 ETF，约 378 天 |
| ETF metadata | 预定义的曝光维度映射表（硬编码在脚本中） |

### 运行

```bash
DATA_PLATFORM_ROOT=/home/yourname/data/market-data-platform \
  ETF_ROTATION_ROOT=/home/yourname/code/market-intel/guan-etf-rotation-v3 \
  uv run hotsector backtest etf
```

需要额外设置 `ETF_ROTATION_ROOT` 指向 rotation-v3 项目目录，因为脚本依赖该项目的 ETF 日线数据。

### 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--start` | `2024-10-14` | 回测开始日期 |
| `--end` | `2026-04-30` | 回测结束日期 |
| `--top-k` | 3 | 每天选取的 ETF 数量 |
| `--etf-pool` | 29 | ETF 候选池大小 |
| `--sample` | 1 | 每 N 个交易日采样一次 |

### 输出解读

脚本首先打印摘要表格：

```
                    策略            沪深300         超额
总收益              +18.25%         +25.83%        -7.57%
年化收益             +12.17%         +16.79%
年化波动             33.10%
夏普比率              0.368
胜率                 51.6%
最大回撤             -29.13%
```

关键指标和热点追踪策略回测一致，额外输出：

- 分年度收益（判断策略在不同市场环境下的表现）
- 最常用 ETF 排名（检查是否有单只 ETF 被过度选中，导致集中度过高）

### 已知局限

- 概念到 ETF 曝光维度的映射是硬编码的，当热点概念超出预定义的曝光维度时，映射会失效
- ETF 候选池只有 29 只，覆盖有限。选来选去容易集中在半导体/科技方向

## ML 增强 ETF 回测

入口： `uv run hotsector backtest etf-ml`

在热点概念 → ETF 曝光的基础上，加入 ETF 技术特征和 walk-forward 训练。

```bash
DATA_PLATFORM_ROOT=/home/yourname/data/market-data-platform \
  ETF_ROTATION_ROOT=/home/yourname/code/market-intel/guan-etf-rotation-v3 \
  uv run hotsector backtest etf-ml
```
