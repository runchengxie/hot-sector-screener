# AGENTS.md

## 项目概览

A 股热点题材候选池筛选器。以最近一个已完成交易日的 EOD 数据构建下一交易时段使用的 50-100 只股票盯盘池。

## 代码结构

- `src/hot_sector_screener/cli.py` — 命令行入口，支持 info/scan/run/universe/build-prompt 五个子命令
- `src/hot_sector_screener/config.py` — 配置加载（YAML + 默认值）
- `src/hot_sector_screener/data_sources/platform.py` — 从 DATA_PLATFORM_ROOT 读取数据湖
- `src/hot_sector_screener/data_sources/rotation_signal.py` — 读取 rotation-v3 行业信号
- `src/hot_sector_screener/topic_classifier.py` — LLM 主题分类与显式确定性提取
- `src/hot_sector_screener/topic_provider.py` — 供应商中立的远端协议适配器
- `src/hot_sector_screener/topic_text_safety.py` — 远端客户文案的元数据泄漏校验
- `src/hot_sector_screener/stock_mapper.py` — 确定性 topic→stock 映射
- `src/hot_sector_screener/ranking.py` — 派生热点特征叠加排序
- `src/hot_sector_screener/signal_export.py` — 导出 research-workspace 兼容信号产物
- `src/hot_sector_screener/universe_builder.py` — 组合、过滤、输出
- `src/hot_sector_screener/paths.py` — 路径管理

## 目录约束

- 临时回测输出放 `outputs/`
- 配置放 `configs/`
- 测试放 `tests/`
- 不要往 DATA_PLATFORM_ROOT 写数据

## 常用命令

- 安装：`uv sync --extra dev`
- 查看热点数据概况：`uv run hotsector info`
- 收集数据（不含 LLM）：`uv run hotsector scan --date <YYYY-MM-DD>`
- 完整运行（含 LLM）：`uv run hotsector run --date <YYYY-MM-DD>`
- 导出标准信号：`uv run hotsector export-signals --date <YYYY-MM-DD>`
- 生成 LLM 提示词文件：`uv run hotsector build-prompt --date <YYYY-MM-DD>`
- 查看候选池：`uv run hotsector universe --date <YYYY-MM-DD>`
- 回测：`uv run hotsector backtest stock`
- 运行测试：`uv run pytest`

## 关键约束

1. LLM 只负责信息压缩和主题分类，不做选股决策；远端失败不得静默 fallback；只有显式 `--no-llm` 或 `llm.enabled: false` 才走确定性提取
2. topic 到 stock 的映射是确定性的，基于概念成分股和 ETF 成分股
3. 不写回 DATA_PLATFORM_ROOT，输出只放本地 `outputs/`
4. 50-100 只是候选池，最终持仓由后续的选股排序流程决定
5. rotation 只有 `signal_date` 边界、没有 publisher receipt 时，必须标记 `strict_point_in_time=false`，不得声称严格 PIT 或 OOS 有效
