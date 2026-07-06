---
name: vllm-profiling
description: Profile and optimize vLLM/vLLM-Ascend workloads in the vllm-ascend-dev workspace. Use for profiling, profile 分析, 性能优化, 吞吐或延迟优化, torch_npu profile, start_profile/stop_profile capture, vllm_profile 分析, 源码热点定位, and baseline-vs-optimized validation.
---

# vLLM Profiling 分析与优化 Skill

## 目标

在 `vllm-ascend-dev` 工作区中完成 vLLM / vLLM-Ascend profiling 闭环：发现运行配置，预检 workload，采集和分析 profile，对照源码定位热点，实施最小源码优化，复测验证，固化记录，并在确认有效时提交代码。

## 必守边界

- 先读取工作区根目录 `README.md` 和 `AGENTS.md`，确认目录布局、脚本发现方式、Python 环境策略和多仓库 Git 约束。
- 默认优化目标是 `vllm-ascend/`。涉及 Ascend 插件源码时，先读取 `vllm-ascend/AGENTS.md`；明确涉及上游 vLLM 时，先读取 `vllm/AGENTS.md`；不确定目录归属时先问用户。
- 启动 profiling 前必须确认优化范围和 workload。优化范围可为仅源码、仅脚本、源码和脚本都包括，或只分析不修改；workload 至少包含 endpoint、payload 或输入数据、输入/输出规模、并发、请求次数、间隔、warmup 和采样轮次。
- 只修改与本轮确认范围和当前热点直接相关的源码或脚本。若需要扩大范围，或需要修改本机服务启动脚本，先说明优化依据、预期收益、风险和回滚方式，获得用户同意后再动手；服务启动脚本是本机私有配置，默认不入仓库。
- 默认源码优化只修改源码，不同步修改 UT、单测、集成测试或测试基线文件；运行已有测试验证即可。只有用户明确要求、测试文件本身是优化目标，或不改测试无法验证关键行为时，先说明原因再修改测试项。
- 不写死绝对脚本路径、服务 IP、端口、profile 目录或日志路径。所有运行信息都从工作区说明、脚本、进程、日志或 profiler 输出动态发现。
- 不停止非本次任务启动的服务进程，除非已说明影响范围并获得用户同意；`stop_profile` 只表示停止 profiler 采集，不要假设 vLLM 服务会随之退出。
- 不删除日志目录、profile 目录或归档目录中的采集结果。需要清理时先征得用户同意。
- 不伪造性能结论。没有成功采集、没有运行分析脚本、或优化后没有复测时，必须明确说明缺口。

## 可复用脚本

脚本路径相对 `SKILL.md` 所在目录解析：

- `scripts/completion_probe.py`：发送 OpenAI 兼容 `/v1/completions` 探测请求，支持 warmup、多次 measured 请求、间隔、长 prompt repeat 构造，输出每次请求 latency、usage 和汇总统计。默认绕过环境代理，避免本地 `127.0.0.1` 被代理干扰；需要代理时显式加 `--use-env-proxy`。
- `scripts/profile_compare.py`：对比两个 `profile-analyse.sh` 生成的 analysed 目录，汇总 step trace、top ops、指定 kernel shape 聚合和 top kernels。默认 shape 聚合目标是 `DispatchFFNCombine`。

常用示例：

```bash
python <skill-dir>/scripts/completion_probe.py \
  --base-url "$BASE_URL" --model "$SERVED_MODEL" \
  --repeat-text "hello " --repeat-count 120000 \
  --max-tokens 1 --temperature 0 \
  --warmup-requests 1 --requests 3 --interval-sec 1

python <skill-dir>/scripts/profile_compare.py \
  "$PROFILE_DIR/analysed/baseline-case" \
  "$PROFILE_DIR/analysed/optimized-case"
```

## 闭环流程

### 1. 发现配置并读取历史

定位工作区根目录后，从 `README.md`、`scripts/`、启动脚本、运行中进程、最新日志和 profiler 配置中发现并记录：

```bash
WORKSPACE_ROOT="<discovered-workspace-root>"
SERVER_SCRIPT="<discovered-server-script>"
ANALYSE_SCRIPT="<discovered-profile-analysis-script>"
BASE_URL="<discovered-service-base-url>"
PROFILE_DIR="<discovered-profiler-output-dir>"
PROFILE_HISTORY="$PROFILE_DIR/PROFILE_HISTORY.md"
LATEST_LOG="<discovered-latest-vllm-log>"
```

- 同时记录 served model name、模型路径、并行参数、设备范围、关键环境变量、vLLM 日志位置和 torch profiler 输出位置。
- 如果 `PROFILE_HISTORY` 存在，启动服务前先阅读，复用已有结论，避免重复分析已验证过的热点、无效方案、服务配置和 workload 结果；如果不存在，在首轮归档后创建。
- 如果服务 bind host 是通配地址，根据当前机器可访问方式选择客户端 host，并用轻量 API 请求验证 `BASE_URL` 可用；不要假设 loopback 地址或固定端口。
- 检查启动脚本是否启用了 torch profiler 配置。若未启用，先提示缺口；只有用户确认后才修改本机启动脚本或建议用户手动调整。

### 2. 确认 workload 和采样计划

启动服务前，向用户确认优化范围与 workload，并把确认结果写入本次分析元数据。用户目标不够具体时，基于已发现的模型名、OpenAI 兼容 API 和关注场景提出一个可执行方案，等用户确认后再继续。

后续若需要调整优化范围、endpoint、payload、输入/输出规模、并发、次数或间隔，先说明原因并再次确认。

### 3. 启动服务并预检请求

从工作区根目录启动发现到的服务脚本：

```bash
"$SERVER_SCRIPT"
```

- 启动后用最新日志确认模型加载、端口监听和异常信息。
- 从启动脚本、进程或日志中再次确认 `BASE_URL`，必要时用 `"$BASE_URL/v1/models"` 做轻量探活。
- 服务刚可用时不要立刻 `start_profile`。先在 profiler 外对同一 payload 跑预检请求，确认状态码、usage、输出、日志和正常请求时间，保证正式 profile 只覆盖干净 workload。
- 将预检的 `measured_min_sec`、`measured_avg_sec`、`measured_max_sec` 记录到最终报告和 `PROFILE_HISTORY`。

### 4. 采集 baseline profile

预检请求全部成功且延迟没有明显异常后，记录 baseline 元数据：相关仓库分支与 commit、模型和并行配置、关键环境变量、workload 参数、日志路径和 profile 归档名。

调用 `start_profile`：

```bash
curl --location --request POST "$BASE_URL/start_profile"
```

按确认的 workload 构造请求。请求构造不限定为 `curl`，可使用 `completion_probe.py`、Python `requests` / `httpx`、OpenAI client、现有 benchmark 脚本或临时脚本；优先选择方便复现、方便记录参数、且能稳定控制并发和间隔的方式。

请求完成后调用 `stop_profile`：

```bash
curl --location --request POST "$BASE_URL/stop_profile"
```

`stop_profile` 返回成功后，确认日志尾部和本次 profile 输出目录；不要把它当作服务退出信号。随后运行发现到的分析脚本：

```bash
"$ANALYSE_SCRIPT" -p "$PROFILE_DIR" -n <case-name>
```

- `<case-name>` 使用可读、稳定、无斜杠的名称，如 `baseline-qwen35b-tp2-cp2`。
- 记录脚本实际输出位置、归档位置、异常、traceback、OOM、超时或未刷盘迹象。
- 如果日志出现 `Incorrect schedule: Stop profiler while current state is RECORD`、`profiling data cannot be parsed during the daemon process` 或建议使用 offline parsing 的 warning，先继续运行分析脚本；只要 analysed CSV/trace 成功生成，就不要把这类 warning 当作 blocker。
- 如果分析脚本失败，保留错误信息，先判断是 profile 目录、Python 环境、`torch_npu` 模块还是采集不完整导致。

### 5. 定位热点并最小优化

把 profile 输出与同一轮 `LATEST_LOG` 对齐，围绕以下信号提炼热点：

- NPU kernel 或算子耗时占比
- CPU 调度、Python 调用、同步等待或空洞
- HCCL / 通信耗时
- shape、batch、sequence length、并行配置导致的异常开销
- fallback、warning、重复初始化、内存压力或异常日志

在 `vllm-ascend/` 或 `vllm/` 中用 `rg` 搜索 profile 中的函数名、算子名、配置名和日志关键字。优先沿真实调用链定位：API 请求 -> engine/scheduler -> model runner -> vllm-ascend platform/worker/attention/ops -> torch_npu/CANN。

对每个优化候选给出证据链：profile 条目、日志片段、源码位置、推断原因、预期收益和风险。实施优化前检查对应仓库或文件状态，保护用户已有改动，并确认改动仍在本轮允许范围内。

### 6. 复测和对比

实施优化后，用同一服务配置和同一 workload 重复“预检 -> start_profile -> 请求 -> stop_profile -> analyse”闭环，使用新的 `<case-name>` 归档。

优先用 `scripts/profile_compare.py` 对比 baseline 与 optimized，并记录：

- E2E latency、TTFT、throughput 或 tokens/s
- 关键算子/函数耗时
- CPU/NPU 空洞、同步等待、通信耗时
- 正确性、日志异常和资源占用

若复测结果不稳定，增加采样轮次，并报告均值、范围或可观察趋势。没有复测时不要声称优化有效。

### 7. 固化记录、提交和清理

每轮复测结束后，更新 `PROFILE_HISTORY`，至少记录 workload、服务配置、预检正常延迟、baseline/optimized 时间、主要热点、分析依据、具体改法、日志路径、profile 归档路径、验证结果和后续风险。

如果优化已被复测确认有效，且用户没有明确禁止提交，则在目标代码仓库中把本轮有效代码改动提交为一个 commit：

- 提交前检查目标仓库 `git status --short` 和 `git diff`，只纳入本轮相关源码改动；默认不纳入 profile 原始数据、日志、`vllm_profile/` 本机历史文档或无关用户改动。
- commit message 简要包含 workload、关键收益和热点名。
- 提交后把 commit hash 写入 `PROFILE_HISTORY` 和最终汇报；如果未提交，记录原因。

如果服务是本次任务启动的，在 profile 归档、复测和必要提交完成后显式停止服务，并确认端口释放。不要删除日志、profile 或历史文档。

## 输出要求

最终汇报包含：

- 场景与配置：模型、并行策略、请求工具、请求参数、预检请求次数、采样次数、日志路径、profile 归档路径。
- 主要热点：按影响排序列出 profile 证据和源码定位。
- 已实施改动或建议：说明改了什么、为什么改、风险是什么。
- 验证结果：预检正常延迟、baseline 与 optimized 对比；未复测时明确说明原因。
- 固化记录：`PROFILE_HISTORY` 更新情况；若已提交，给出 commit hash。
- 后续建议：只列与当前热点直接相关的下一步。
