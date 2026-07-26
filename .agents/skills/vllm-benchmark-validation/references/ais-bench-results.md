# AISBench 结果布局与指标

AISBench 版本可能改变目录或字段。解析失败时先检查当前 `benchmark/` 仓库的
summarizer 与 calculator 实现，再更新比较器；不要根据旧文档猜测字段。

## 精度结果

时间戳运行目录通常包含：

```text
<run>/
├── configs/
├── logs/
├── predictions/
├── results/
└── summary/
    └── summary_<timestamp>.csv
```

summary CSV 的固定维度列是 `dataset`、`version`、`metric`、`mode`，部分
summarizer 会增加 `total_count`。其余列视为模型名，单元格是该模型在当前
数据集和 metric 上的分数。

比较器使用以下 key 对齐精度指标：

```text
dataset + version + metric + mode + model
```

空字符串、`-`、`N/A` 等非数值单元格只产生 warning，不转换为零。

## 性能结果

性能目录通常按模型和数据集组织：

```text
<run>/performances/<model>/<dataset>.csv
<run>/performances/<model>/<dataset>.json
<run>/performances/<model>/<dataset>_details.jsonl
<run>/performances/<model>/<dataset>_plot.html
```

CSV 提供每请求统计，常见字段包括：

- E2EL：端到端请求时延；
- TTFT：首 token 时延；
- TPOT：除首 token 外，每个输出 token 的平均耗时；
- ITL：相邻输出 token 的间隔；
- InputTokens、OutputTokens；
- OutputTokenThroughput；
- Average、Min、Max、Median、P75、P90、P99 和样本数 N。

JSON 提供运行级汇总，常见字段包括：

- Benchmark Duration；
- Total、Success、Failed Requests；
- Concurrency、Max Concurrency；
- Request Throughput；
- Prefill、Input、Output、Total Token Throughput；
- Total Input Tokens、Total Generated Tokens。

比较器使用以下 key 对齐性能指标：

```text
model + dataset + metric + stage + statistic + unit
```

只有 key 和单位相同才计算差值。baseline 为零时保留绝对差值，将相对变化写为
空值，避免除零或伪造百分比。

## 可比性检查

结果文件本身通常不足以证明 workload 相同。报告前另外核对：

- 合成或真实数据集及其版本；
- 输入/输出 token 分布；
- 请求数量和 warmup；
- batch size、最大并发、request rate、pressure 模式；
- streaming、采样参数、served model；
- vLLM/vLLM-Ascend/benchmark commit；
- 硬件、并行配置和服务启动参数。

生成的 `configs/*.py` 可能包含 endpoint、API key 或环境信息。可以用它核对
workload，但不得整段复制到报告。
