# vllm-ascend-dev

vLLM Ascend 开发工作区

## 目录结构

```text
.
├── .claude/                          # Claude Code 项目配置
├── .vscode/                          # VSCode 项目配置
├── docs/                             # 开发文档与笔记
├── scripts/                          # 辅助脚本
│   ├── bootstrap.sh                  # 一键初始化脚本
│   ├── preview-vllm-ascend-docs.sh   # 文档构建 & 预览
│   └── run-benchmark.sh              # 基准测试运行脚本
└── vllm-ascend-dev.code-workspace    # VSCode 多根工作区文件
```

代码仓库（vllm、vllm-ascend）由 `bootstrap.sh` 克隆到同级目录，不纳入本仓库。benchmark 仓库需显式指定 `-b | --with-benchmark` 才会克隆。

## 快速开始

```bash
git clone git@github.com:li1how/vllm-ascend-dev.git
cd vllm-ascend-dev
./scripts/bootstrap.sh                        # 默认：仅克隆 vllm + vllm-ascend
./scripts/bootstrap.sh -b | --with-benchmark   # 同时克隆 benchmark 仓库
```
