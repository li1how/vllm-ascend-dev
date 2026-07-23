# 服务对比 case 格式

`serving_diff.py` 接受一个 UTF-8 JSON object。相对路径 `cwd` 以 case 文件
所在目录为基准解析。

```json
{
  "name": "greedy-smoke",
  "request": {
    "endpoint": "/v1/completions",
    "payload": {
      "model": "model",
      "prompt": "Hello",
      "temperature": 0,
      "max_tokens": 8,
      "stream": false
    },
    "warmup_requests": 1,
    "requests": 2,
    "timeout_sec": 300
  },
  "baseline": {
    "base_url": "http://127.0.0.1:8000"
  },
  "candidate": {
    "base_url": "http://127.0.0.1:8001",
    "command": ["bash", "server.sh", "--port", "8001"],
    "cwd": "../vllm-ascend",
    "env": {
      "ASCEND_RT_VISIBLE_DEVICES": "0"
    },
    "health_path": "/v1/models",
    "startup_timeout_sec": 1200,
    "shutdown_grace_sec": 30
  },
  "compare": {
    "fields": ["output_text", "finish_reason", "output_tokens"],
    "exact_token_ids": false,
    "prompt_tokens_min": 1
  }
}
```

## 字段说明

- `name` 可选，仅用于报告标识。
- `request.endpoint` 和 `request.payload` 必填。不支持 streaming 响应。
  `warmup_requests` 默认为 `0`，measured `requests` 默认为 `1`，
  `timeout_sec` 默认为 `1800`。
- `baseline` 和 `candidate` 都必须提供 `base_url`。省略 `command` 时连接
  已有服务；managed `command` 必须是非空 JSON string array，不接受 shell
  command string。
- managed 模式默认健康接口为 `/v1/models`，启动超时为 `1200` 秒，停止
  宽限为 `30` 秒。baseline 完全停止后才启动 candidate。脚本只停止自己
  启动的进程。
- `env` 扩展当前环境。环境变量名包含 `KEY`、`TOKEN`、`SECRET` 或
  `PASSWORD` 时，其值在保存的配置和报告中替换为 `<redacted>`。
- 默认比较 `output_text`、`finish_reason` 和 `output_tokens`。动态响应 ID、
  创建时间、usage 细节和 latency 只记录，默认不参与比较。
- `exact_token_ids: true` 用于比较 token ID/token 序列。如果服务没有在
  响应或 logprobs 中暴露这些信息，本次运行判定为无效。
- `prompt_tokens_min` 用于断言每个 measured 响应报告的 prompt token 数量。

默认输出目录为：

```text
<workspace>/tmp/vllm-serving-validation/<UTC timestamp>/
```

使用 `--output-dir` 可以覆盖该路径。工具会保留 `report.json`、脱敏后的配置、
原始响应文件和 managed 服务日志，供后续诊断。
