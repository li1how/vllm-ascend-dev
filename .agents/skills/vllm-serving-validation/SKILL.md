---
name: vllm-serving-validation
description: 验证 OpenAI-compatible vLLM 与 vLLM-Ascend 服务。用于服务健康检查、completion/chat 请求探测、baseline 与 candidate 正确性对比、生成结果一致性、managed 服务生命周期管理和端到端服务验证。
---

# vLLM 服务验证

使用本 Skill 验证服务行为和正确性。遇到 import、editable 安装、Git 状态、
Python 或 NPU 可用性问题时使用 `vllm-runtime-diagnosis`；目标是性能证据时
使用 `vllm-profiling`。

## 边界

- 操作服务前先读取工作区 `README.md` 和 `AGENTS.md`。
- 只有 `serving_diff.py` 启动的服务才能由它停止。managed 进程使用独立进程
  组，并在每个 case 完成后清理。
- 不要把 secret 放入命令参数或 case payload。环境变量名包含 `KEY`、
  `TOKEN`、`SECRET` 或 `PASSWORD` 时，在报告中对其值脱敏。
- 正确性对比优先使用确定性 payload：固定 model、seed、temperature=0 和
  一致的 decoding 参数。
- 将生成的报告保存到工作区已忽略的 `tmp/` 目录。

## 脚本

路径均相对本 `SKILL.md`：

- `scripts/request_probe.py`：探测 completion 或 chat endpoint，支持自定义
  JSON payload、warmup、重复 measured 请求和结构化输出。
- `scripts/serving_diff.py`：对比 baseline 和 candidate 响应。两侧都可以
  连接已有 `base_url`，也可以提供 JSON array 形式的 `command` 启动
  managed 服务。
- `scripts/serving_common.py`：提供共享 HTTP、响应规范化、健康检查和脱敏
  函数；由其他脚本 import，不作为主要 CLI。

创建 managed 对比 case 前，先读取
[references/case-format.md](references/case-format.md)。

## 执行流程

1. 动态发现实际 base URL、endpoint、served model 和 request payload。
2. 在 profiler 外运行 `request_probe.py`，检查规范化输出。
3. 编写可复现 case。两侧服务都已运行时使用 existing 模式；否则为两侧提供
   command array 和不同端口。
4. 运行 `serving_diff.py`。脚本在启动 candidate 前会先完整停止 baseline。
5. 返回码 `0` 表示一致，`1` 表示对比已完成但存在差异，`2` 表示配置错误、
   启动失败或请求失败。
6. 汇报 case 路径、report 目录、比较字段、差异和无法执行的断言。

示例：

```bash
python <skill-dir>/scripts/request_probe.py \
  --base-url http://127.0.0.1:8000 --kind chat \
  --model model --prompt "Say hello" --max-tokens 8 \
  --warmup-requests 1 --requests 2 --summary-json result.json

python <skill-dir>/scripts/serving_diff.py case.json
```

需要精确比较生成 token 时，将 `compare.exact_token_ids` 设为 `true`。任一
响应没有直接返回 token IDs，也没有通过 logprobs 暴露 token 时，验证以返回码
`2` 明确失败。
