# 命令详解

命令行入口统一为 `hotsector`，子命令如下。

## 全局说明

- 所有日期参数支持 `YYYY-MM-DD` 和 `YYYYMMDD` 两种格式。
- `scan`、`run` 和 `build-prompt` 未指定日期时，解析为关键数据源共同可用的最近已完成观测日；不会使用 `date.today()`。上海时间 16:00 前不能使用当日，显式未来日或无效日会失败。
- `universe`、`export-signals` 和 `validate-output` 未指定日期时读取最近已有输出目录。
- `--date` 表示已完成交易日的观测日/EOD 数据截止日，不是执行日；候选最早供下一交易时段使用。
- 配置参数 `--config` 指向 YAML 配置文件，不传则使用 `configs/default.yml`（如果存在），否则使用内建默认值。

## info — 查看数据概况

查看数据湖中各热点源有哪些交易日数据可用。

```bash
uv run hotsector info
uv run hotsector info --source ths_hot    # 只看同花顺热榜
```

输出包含每个数据源的总交易日数、最早和最晚日期、最近 5 个样本日期。

## scan — 数据收集

收集指定观测日的热点数据，不做 LLM 分类。历史日期的行业轮动信号严格按 as-of 读取，不会回退到最新 run。

```bash
uv run hotsector scan                        # 最近共同可用的已完成观测日
uv run hotsector scan --date 2026-06-19      # 指定日期
uv run hotsector scan --date 2026-06-19 --config configs/experiments/daily_eod.yml
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
| `--no-llm` | 显式跳过 LLM 调用，使用数据驱动的确定性主题提取 |
| `--load-topics path.json` | 加载外部主题文件；仍执行与实时 LLM 相同的严格 schema、观测词表和来源校验 |
| `--output-dir path` | 自定义输出目录，默认 `outputs/<YYYYMMDD>` |
| `--max-candidates N` | 覆盖配置文件中的最大候选股数 |
| `--stocks-per-topic N` | 覆盖配置文件中每个主题最多选取的股票数 |
| `--holdings path.json` | 读取严格版本化的当前持仓快照，额外写出每日资格/重评分 companion artifact |

### 进阶用法示例

手工写好主题文件后跳过 LLM：

```bash
uv run hotsector run --date 2026-06-19 --load-topics topics.json
```

外部主题只能包含 `topic`、`weight`、`reasoning`、`related_concepts`、`source_signals` 五个字段。关联概念必须来自该观测日输入词表，来源必须在该次观测中真实可用；股票代码、公司名、伪概念或 `model_pick` 一类来源会使命令以非 0 退出。

显式选择数据驱动的确定性主题提取：

```bash
uv run hotsector run --date 2026-06-19 --no-llm
```

当 `llm.enabled: true` 且未传 `--no-llm` 时，远端模式要求显式设置 `LLM_API_URL`、`LLM_API_KEY`、
`LLM_MODEL` 和 `LLM_PROVIDER_ID`。任何配置、网络、响应 schema 或主题验证错误都会让
命令以非 0 退出，不会自动降级到确定性结果。

调整候选池规模：

```bash
uv run hotsector run --date 2026-06-19 --max-candidates 80 --stocks-per-topic 20
```

为滞回研究生成旧持仓的当日资格与重评分特征：

```bash
uv run hotsector run \
  --date 2026-06-19 \
  --holdings examples/holdings_snapshot.v1.json \
  --no-llm
```

持仓文件必须精确包含 `schema_version`、`artifact_type`、`market`、`as_of_date`、`symbols`；
`as_of_date` 不得晚于观测日，代码必须是规范化的 `000001.SZ` / `600000.SH` / `430047.BJ`
格式。该选项只增加 `holdings_eligibility_overlay.json`，不会修改 candidate v1/v2，也不会
替下游决定保留、卖出或买入。

## validate-holdings-overlay — 校验持仓资格产物

消费者不需要复制 schema 或 validator。使用 producer 提供的只读命令校验 artifact，并取得
稳定的 canonical 摘要：

```bash
uv run hotsector validate-holdings-overlay \
  --input outputs/20260619/holdings_eligibility_overlay.json
```

成功时输出单行 JSON，包括 policy ID/version/SHA、观测日、总行数、旧持仓数、主题匹配数、
entry/hold eligible 数和整个 artifact 的 canonical SHA-256；合同漂移、非法 JSON 或文件缺失
均以非 0 退出。

## universe — 查看候选池

查看某次运行生成的候选池结果。

```bash
uv run hotsector universe                        # 最近一次
uv run hotsector universe --date 2026-06-19      # 指定日期
uv run hotsector universe --date 2026-06-19 --csv # CSV 表格输出
uv run hotsector universe --date 2026-06-19 --limit 50  # 显示 50 只
```

输出内容包括候选股票列表（代码、名称、相关性得分、来源主题）以及当天的主题空间。

## export-signals — 导出标准信号

把 `candidate_universe.json` 转成 research-workspace 可消费的 `signals.parquet`、
`signals.csv` 和 `signals.meta.json`。`hotsector run` 默认已经自动导出；这个命令用于
从历史候选池重新生成信号产物。这里导出的始终是候选池研究信号，固定
`eligible_for_live=false`，不能直接交给执行系统。

```bash
uv run hotsector export-signals --date 2026-06-19
uv run hotsector export-signals --input outputs/20260619/candidate_universe.json
```

常用参数：

| 参数 | 说明 |
|------|------|
| `--date` | 指定 `outputs/<YYYYMMDD>/candidate_universe.json` |
| `--input` | 直接指定候选池 JSON |
| `--output-dir` | 自定义信号输出目录 |
| `--model-version` | 写入 `model_version` 字段 |
| `--feature-set-id` | 写入 `feature_set_id` 字段 |

## build-prompt — 生成 LLM 提示词

收集热点数据并写出 LLM 提示词到文件，不执行分类。

```bash
uv run hotsector build-prompt --date 2026-06-19
uv run hotsector build-prompt --date 2026-06-19 --out-prompt my_prompt.txt
uv run hotsector build-prompt --date 2026-06-19 --stock-limit 50 --concept-limit 30
```

输出的提示词文件可以直接发给外部 LLM 处理，得到主题 JSON 后再通过 `hotsector run --load-topics topics.json` 回接。

## backtest — 热点策略回测

当前回测已经接入 CLI：

```bash
uv run hotsector backtest stock
uv run hotsector backtest etf
uv run hotsector backtest etf-ml
```

`stock` 用热点概念映射个股并做 1 日持有回测；`etf` 用热点概念映射 ETF 曝光；
`etf-ml` 在 ETF 曝光基础上加入技术特征和 walk-forward 训练。
