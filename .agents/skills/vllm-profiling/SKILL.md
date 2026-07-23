---
name: vllm-profiling
description: 对 vLLM 或 vLLM-Ascend workload 进行 profile 采集、解析和性能优化。用于 torch_npu profile、start_profile/stop_profile 采样、profile 归档解析、热点证据提取、baseline 与 optimized 性能对比、吞吐或延迟优化，以及更新 PROFILE_HISTORY。
---

# vLLM Profiling 性能分析

建立可复现的性能证据闭环：确定 workload 和采样方案，采集、归档并解析
profile，定位热点，实施范围明确的修改，然后在相同条件下复测。

服务生命周期、健康检查、请求执行和输出正确性使用
`vllm-serving-validation`；活动源码、Python、import、Git 或 NPU 状态不明确时
使用 `vllm-runtime-diagnosis`；需要详细源码调用链文档时使用
`code-analysis`。

## 边界

- 先读取工作区 `README.md` 和 `AGENTS.md`；修改源码前再读取目标仓库的
  `AGENTS.md`。
- 采集前确认优化范围、endpoint 或输入数据、输入/输出规模、并发、warmup、
  请求次数、请求间隔和采样轮次。
- 保留无关改动、服务日志、原始 profile、分析归档和 `PROFILE_HISTORY`。
- 不停止非本任务启动的服务。`stop_profile` 只表示停止采集，不代表服务退出。
- 没有可比的 baseline 和 candidate 测量时，不得声称优化有效。
- 不自动提交。只有用户明确要求 commit 或 PR 时，才使用 `vllm-ascend-pr`。

## 资源

- 工作区 `scripts/profile-analyse.sh` 是供开发者使用的已文档化解析工具。
  从工作区动态发现其路径，不要把它复制到本 Skill。
- `scripts/profile_compare.py` 用于对比两个 analysed 归档。需要按 shape
  聚合时可重复传入 `--shape-op OP_TYPE`；未指定时不输出 shape 专项段。

```bash
python <skill-dir>/scripts/profile_compare.py \
  <baseline-analysed-dir> <candidate-analysed-dir> \
  --shape-op DispatchFFNCombine --shape-op MatMul
```

## 执行流程

### 1. 发现并保留上下文

动态发现工作区、服务/profile 配置、最新日志、profiler 输出根目录、解析脚本
以及：

```bash
PROFILE_HISTORY="<profile-root>/PROFILE_HISTORY.md"
```

采集前先读取已有历史。记录仓库 branch 和 HEAD、模型/workload 标识、并行配置、
profiler 参数和归档名称。源码或环境存在歧义时，运行
`vllm-runtime-diagnosis`。

### 2. 固定 workload 和采样方案

记录精确 workload 和成功指标。使用 `vllm-serving-validation` 在 profiler
之外执行健康检查和确定性预检。记录 warmup 与 measured latency，避免把
profiler 启动开销或错误请求误判为热点。

### 3. 采集并归档 baseline

预检通过后：

1. 记录 baseline 元数据；
2. 调用动态发现的 `start_profile` endpoint；
3. 使用 `vllm-serving-validation` 或已确认的 benchmark 工具执行固定
   workload；
4. 调用 `stop_profile`；
5. 确认数据刷盘和相关日志；
6. 运行 `scripts/profile-analyse.sh -p <profile-root> -n <case-name>`。

case 名称保持稳定且可读。保留 warning 和失败信息。如果离线解析器成功生成
完整 CSV 和 trace，不要仅凭 profiler warning 判定采集失败。

### 4. 建立热点证据

将 analysed profile 与同一轮服务日志对齐，按影响排序：

- NPU kernel 和算子耗时；
- host 调度、Python 开销、同步或空洞；
- HCCL 和未重叠通信；
- 重复初始化、fallback、内存压力或异常；
- 与当前 workload 相关的 shape、batch 或 sequence 影响。

每个候选都要保留 profile row 或 trace 证据、日志证据、源码位置、作用机制、
预期收益和风险。需要跨模块调用链时使用 `code-analysis`。

### 5. 在相同条件下复测

只实施当前证据支持的修改。使用相同 workload 和服务配置重复预检、采集、
归档和解析。通过 `profile_compare.py` 对比 step trace、top operators、
显式选择的 shape 聚合以及 top kernels。结果波动较大时增加采样轮次。

通过 `vllm-serving-validation` 独立验证服务正确性，不要把动态 ID、usage
元数据或 latency 误设为正确性比较字段。

### 6. 更新历史

向 `PROFILE_HISTORY` 追加：

- workload 与服务/profile 配置；
- 仓库 branch 和 HEAD；
- 预检 latency 与采样次数；
- baseline/candidate 归档和日志路径；
- 热点证据与源码位置；
- 修改内容、风险、正确性结果和性能变化；
- 无结论或失败的尝试，以及下一个有效实验。

## 最终汇报

汇报固定场景、归档路径、按证据排序的热点、已实施修改、baseline/candidate
统计、正确性状态、历史更新和剩余不确定性。明确标注未运行的检查。
