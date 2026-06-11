# vllm-ascend-dev

vLLM Ascend 开发工作区

## 目录结构

```
.
├── .claude/                          # Claude Code 项目配置
├── .vscode/                          # VSCode 项目配置
├── docs/                             # 开发文档与笔记
├── scripts/                          # 辅助脚本
│   ├── bootstrap.sh                  # 一键初始化脚本
│   └── build_docs.sh                 # 文档构建 & 预览
└── vllm-ascend-dev.code-workspace    # VSCode 多根工作区文件
```

代码仓库（vllm、vllm-ascend、benchmark）由 `bootstrap.sh` 克隆到同级目录，不纳入本仓库。

## 快速开始

```bash
git clone git@github.com:li1how/vllm-ascend-dev.git
cd vllm-ascend-dev
./scripts/bootstrap.sh
```
