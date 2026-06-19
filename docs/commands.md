# 命令详解

命令行入口统一为 `hotsector`，当前支持 5 个子命令。

## 全局说明

- 所有日期参数支持 `YYYY-MM-DD` 和 `YYYYMMDD` 两种格式。
- 未指定日期时默认使用当天。
- 配置参数 `--config` 指向 YAML 配置文件，不传则使用 `configs/default.yml`（如果存在），否则使用内建默认值。

## info — 查看数据概况

查看数据湖中各热点源有哪些交易日数据可用。

```bash
uv run hotsector info
uv run hotsector info --source ths_hot    # 只看同花顺热榜
```

输出包含每个数据源的总交易日数、最早和最晚日期、最近 5 个样本日期。

## scan — 数据收集

收集指定日期的热点数据，不做 LLM 分类。适合开盘前快速确认数据是否到位。

```bash
uv run hotsector scan                        # 当天
uv run hotsector scan --date 2026-06-19      # 指定日期
uv run hotsector scan --date 2026-06-19 --config configs/experiments/daily_premarket.yml
```

输出内容包括各数据源的行数、列名、同花顺热榜前 10 名样本。

## run — 完整流水线

执行完整的收集 → LLM 分类 → 股票映射 → 输出候选池。

### 基本用法

```bash
uv run hotsector run --date 2026-06-19
```

### 进阶参数

| 参数 | 说明 |
|------|------|
| `--no-llm` | 跳过 LLM 调用，使用数据驱动的兜底主题提取 |
| `--load-topics path.json` | 加载外部生成的主题文件，绕过 LLM 分类步骤 |
| `--output-dir path` | 自定义输出目录，默认 `outputs/<YYYYMMDD>` |
| `--max-candidates N` | 覆盖配置文件中的最大候选股数 |
| `--stocks-per-topic N` | 覆盖配置文件中每个主题最多选取的股票数 |

### 进阶用法示例

手工写好主题文件后跳过 LLM：

```bash
uv run hotsector run --date 2026-06-19 --load-topics topics.json
```

不想等 LLM 调用，直接用数据驱动的方式提取主题：

```bash
uv run hotsector run --date 2026-06-19 --no-llm
```

调整候选池规模：

```bash
uv run hotsector run --date 2026-06-19 --max-candidates 80 --stocks-per-topic 20
```

## universe — 查看候选池

查看某次运行生成的候选池结果。

```bash
uv run hotsector universe                        # 最近一次
uv run hotsector universe --date 2026-06-19      # 指定日期
uv run hotsector universe --date 2026-06-19 --csv # CSV 表格输出
uv run hotsector universe --date 2026-06-19 --limit 50  # 显示 50 只
```

输出内容包括候选股票列表（代码、名称、相关性得分、来源主题）以及当天的主题空间。

## build-prompt — 生成 LLM 提示词

收集热点数据并写出 LLM 提示词到文件，不执行分类。

```bash
uv run hotsector build-prompt --date 2026-06-19
uv run hotsector build-prompt --date 2026-06-19 --out-prompt my_prompt.txt
uv run hotsector build-prompt --date 2026-06-19 --stock-limit 50 --concept-limit 30
```

输出的提示词文件可以直接发给外部 LLM 处理，得到主题 JSON 后再通过 `hotsector run --load-topics topics.json` 回接。
