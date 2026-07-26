---
name: vllm-benchmark-validation
description: 使用 AISBench benchmark 对 vLLM、vLLM-Ascend 或其他 AISBench 配置后端执行精度与性能评测，并解析、汇总或比较一个及多个运行结果。用于 benchmark、ais_bench、精度测试、性能测试、吞吐/时延测试、baseline 与 candidate 对比、回归结果整理和评测报告生成。
---

# vLLM AISBench 评测

通过配置发现、运行前检查、固定 workload、执行 AISBench 和结构化结果报告，
建立可复现的精度与性能测试流程。

## 边界

- 先读取工作区 `README.md` 和 `AGENTS.md`。
- 不修改 `benchmark/` 仓库中的上游配置。需要定制时使用用户提供的配置，或在
  工作区忽略目录 `tmp/benchmark-cases/` 中创建 case。
- 不接管、不停止非本任务启动的服务。OpenAI-compatible 服务使用
  `vllm-serving-validation` 做健康检查和小请求预检。
- 活动源码、Python、editable 安装、Git 或 NPU 状态不明确时，先使用
  `vllm-runtime-diagnosis`。
- 需要算子级 profile 和热点定位时使用 `vllm-profiling`；本 Skill 只处理
  端到端 benchmark 指标。
- 不把 API key、token、完整 endpoint 或生成配置中的其他敏感信息写入报告。
- 不内置精度、时延或吞吐门禁。指标变化只报告，不据此返回失败。

解析产物或解释指标前，读取
[references/ais-bench-results.md](references/ais-bench-results.md)。

## 执行流程

### 1. 固定测试上下文

记录并在最终报告中保留：

- 工作区、`vllm`、`vllm-ascend` 和 `benchmark` 的 branch/HEAD；
- 实际 Python、AISBench 版本或 benchmark HEAD；
- 模型任务、数据集任务、summarizer、mode 和输出根目录；
- 服务启动配置的非敏感摘要；
- 请求数量、输入/输出规模、并发、request rate、warmup 和测试轮次。

比较运行结果时，先确认 workload 相同。无法确认时仍可输出指标，但必须标记
“不可直接比较”及缺失的上下文。

### 2. 发现配置并预检服务

使用实际 Python 模块入口发现配置，不假设 shell 中存在同名 CLI：

```bash
python -m ais_bench.benchmark.cli.main \
  --models <model-task> --datasets <dataset-task> --search
```

检查搜索结果指向预期配置。对服务化模型执行健康检查和一个小型确定性请求。
如果用户只要求离线解析已有结果，跳过服务和运行环境检查。

### 3. 运行精度评测

使用工作区 `scripts/run-benchmark.sh`，默认 `--mode all`。正式精度评测保留
完整数据集；`--num-prompts` 仅用于用户明确要求的子集或冒烟测试，并在报告中
标记实际样本数和覆盖不足。

```bash
./scripts/run-benchmark.sh \
  --model <model-task> \
  --dataset <dataset-task> \
  --mode all \
  --work-dir benchmark-outputs/<case-name>
```

确认运行目录包含 `summary/`、`results/` 和 `predictions/`。失败时保留
AISBench 原始错误和日志路径，不用部分 summary 冒充完整结果。

### 4. 运行性能评测

使用流式服务模型和适合性能测试的数据集，固定输入/输出 token 规模、请求数、
并发和发送速率。正式性能测试不要传 `--debug`；debug 会限制并行执行，只用于
排障。

```bash
./scripts/run-benchmark.sh \
  --model <stream-model-task> \
  --dataset <perf-dataset-task> \
  --mode perf \
  --num-warmups 1 \
  --work-dir benchmark-outputs/<case-name>
```

比较多轮结果时保持服务配置和 workload 不变。若只运行一轮，明确说明无法评估
抖动。确认 `performances/` 中存在 CSV 和 JSON，再解释 TTFT、TPOT、ITL、
E2EL、请求吞吐与 token 吞吐。

### 5. 生成报告

显式传入每个 AISBench 时间戳运行目录；不要把包含多个运行的输出根目录当成
单个输入：

```bash
python <skill-dir>/scripts/benchmark_report.py \
  --input baseline=/path/to/baseline-run \
  --input candidate=/path/to/candidate-run \
  --markdown-output tmp/benchmark-report.md \
  --json-output tmp/benchmark-report.json
```

`--input LABEL=RUN_DIR` 可重复传入，第一个输入是差值参考。比较器按相同
workload key 对齐指标，展示原始值、绝对差值、相对变化和缺失项，不判断好坏。

返回码：

- `0`：至少解析到一个指标并成功生成报告，包括指标变差或部分指标缺失；
- `2`：参数无效、目录不可用，或所有输入均没有可解析指标。

## 最终汇报

汇报测试类型、完整命令的非敏感部分、配置与仓库版本、run 目录、样本和 workload
参数、指标表、差值、缺失项、warning 和未运行的检查。精度子集不得称为完整
精度，单轮性能不得称为稳定性能，workload 不一致不得声称存在性能回归。
