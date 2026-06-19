# AGENTS.md

## 项目概览

A-share 热点题材候选池筛选器。每天开盘前构建 50-100 只股票盯盘池。

## 代码结构

- `src/hot_sector_screener/cli.py` — 唯一命令行入口
- `src/hot_sector_screener/config.py` — 配置加载
- `src/hot_sector_screener/data_sources/platform.py` — 从 DATA_PLATFORM_ROOT 读取数据
- `src/hot_sector_screener/data_sources/rotation_signal.py` — 读取 rotation-v3 行业信号
- `src/hot_sector_screener/topic_classifier.py` — LLM 主题分类
- `src/hot_sector_screener/stock_mapper.py` — 确定性 topic→stock 映射
- `src/hot_sector_screener/universe_builder.py` — 组合+过滤+输出
- `src/hot_sector_screener/paths.py` — 路径管理

## 目录约束

- 临时回测输出放 `outputs/`
- 配置放 `configs/`
- 测试放 `tests/`
- 不要往 DATA_PLATFORM_ROOT 写数据

## 常用命令

- 安装：`uv sync --extra dev`
- 查看热点数据概况：`uv run hotsector info`
- 收集数据（不含 LLM）：`uv run hotsector scan`
- 完整运行（含 LLM）：`uv run hotsector run --date <YYYY-MM-DD>`
- 查看候选池：`uv run hotsector universe --date <YYYY-MM-DD>`
- 运行测试：`uv run pytest`

## 关键约束

1. **LLM 不做选股决策** — 只做信息压缩和主题分类
2. **topic→stock 映射是确定性的** — 基于概念成分股 + ETF 成分股
3. **不写回 DATA_PLATFORM_ROOT** — 输出只放本地 `outputs/`
4. **50-100 只是候选池，不是持仓** — 最终持仓由 cross-sectional ranking 决定
