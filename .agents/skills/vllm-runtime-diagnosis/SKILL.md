---
name: vllm-runtime-diagnosis
description: 诊断 vLLM 与 vLLM-Ascend 工作区的运行环境状态。用于排查 editable 源码错误、import 未生效、Python 或 CLI 不匹配、Git branch 和 dirty 状态、开发工具缺失、torch/torch_npu import 失败、NPU 可见性以及环境预检问题。
---

# vLLM 运行环境诊断

调试应用行为前，先用本 Skill 确认实际生效的源码和运行环境。模型架构、并行
策略、KV cache、attention、服务正确性和性能分析分别交给对应专项 Skill。

## 边界

- 先读取工作区 `README.md` 和 `AGENTS.md`。
- 只读执行诊断。不安装依赖、不激活环境、不修改 Git 状态；只有用户要求可选
  NPU 检查时才允许初始化 NPU。
- 不输出代理 URL、凭据、token 或完整环境变量。
- `vllm` 或 `vllm_ascend` 解析到工作区对应仓库之外时记为 error；缺少可选
  开发命令时记为 warning。
- 可能初始化设备的 import 必须放在带超时的隔离子进程中。

## 脚本

运行：

```bash
python <skill-dir>/scripts/runtime_doctor.py
python <skill-dir>/scripts/runtime_doctor.py --json-output report.json
python <skill-dir>/scripts/runtime_doctor.py --npu-mode auto
```

默认报告包含：

- 工作区以及 `vllm`/`vllm-ascend` 仓库是否存在；
- branch、HEAD、upstream 和 dirty 状态；
- 当前 Python，以及 `vllm`、`vllm_ascend`、`torch`、`torch_npu` 的版本、
  import 路径和 editable 安装来源；
- `vllm`、`pytest`、`ruff`、`pre-commit`、`gh` 和 `conda` 命令可用性。

`--npu-mode auto` 额外检查 `npu-smi`，并在子进程中执行 `torch_npu` 设备
探测；`required` 会把 NPU 不可用记为 error。不传 `--json-output` 时输出
简洁的人类可读报告。返回码 `0` 表示没有 error，`1` 表示发现一个或多个
error，`2` 表示参数错误或工作区路径不可用。

## 结果解释

1. 优先解决 error，尤其是源码路径不匹配和 import 失败。
2. 使用 warning 说明验证覆盖不足；没有其他证据时，不要把可选工具缺失直接
   当作根因。
3. 修正环境后使用相同命令复查。
4. 最终汇报生效模块路径、Python executable、仓库 HEAD 和剩余 error；
   不要粘贴完整环境变量。
