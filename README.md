# vllm-ascend-dev

vLLM Ascend 开发工作区

## 目录结构

```text
.
├── .claude/                          # Claude Code 项目配置
│   └── skills/                       #   自定义 Skill（code-analysis, manage-workspace, weekly-report）
├── .vscode/                          # VSCode 项目配置
├── docs/                             # 开发文档与笔记
│   ├── analysis/                     #   代码分析产出
│   └── feature/                      #   特性开发笔记（不入仓库）
├── scripts/                          # 辅助脚本
│   ├── bootstrap.sh                  #   一键初始化脚本
│   ├── preview-vllm-ascend-docs.sh   #   文档构建 & 预览
│   └── run-benchmark.sh              #   基准测试运行脚本
├── benchmark-outputs/                # 基准测试产物（不入仓库）
├── weekly-report/                    # 周报产出（不入仓库）
├── tmp/                              # 临时文件（不入仓库）
├── pkg/                              # 大二进制包（不入仓库）
├── .env                              # 统一环境变量（不入仓库）
├── .gitignore
├── vllm/                             # [克隆] vLLM 上游仓库
├── vllm-ascend/                      # [克隆] vLLM Ascend 插件仓库
├── benchmark/                        # [克隆/可选] ais_bench 基准测试仓库，不在 workspace folders
└── vllm-ascend-dev.code-workspace    # VSCode 多根工作区文件
```

`vllm/`、`vllm-ascend/` 由 `bootstrap.sh` 克隆到同级目录；`benchmark/` 需显式指定 `-b | --with-benchmark` 才会克隆。以上代码仓库均不入本仓库。

## 快速开始

```bash
git clone git@github.com:li1how/vllm-ascend-dev.git
cd vllm-ascend-dev
./scripts/bootstrap.sh                        # 默认：仅克隆 vllm + vllm-ascend
./scripts/bootstrap.sh -b | --with-benchmark   # 同时克隆 benchmark 仓库
```

## 脚本速查

| 脚本 | 用途 | 常用参数 |
| ------ | ------ | --------- |
| `bootstrap.sh` | 克隆代码仓库、配置 remote | `-b` 同时克隆 benchmark |
| `preview-vllm-ascend-docs.sh` | 构建 vllm-ascend 文档并预览 | `-t` AI 翻译；`-s` 仅构建不启动服务；`PORT=9000` 自定义端口 |
| `run-benchmark.sh` | 运行 ais_bench 基准测试 | `-m <name>` 模型配置；`-d <name>` 数据集（可多次指定） |

所有脚本均支持 `-h | --help` 查看完整用法。

## 环境

### 环境变量（`.env`）

`.env` 文件存放统一环境变量，脚本启动时自动加载。主要变量：

- `DEEPSEEK_API_KEY` — AI 翻译用 API Key，由 `preview-vllm-ascend-docs.sh` 读取

### Conda 环境

| 环境名 | 用途 |
| ------ | ------ |
| `vllm-ascend-dev` | 通用开发环境 |
| `ais_bench` | 基准测试（ais_bench） |
