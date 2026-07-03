# vllm-ascend-dev

vLLM Ascend 开发工作区

## 目录结构

```text
.
├── .agents/
│   └── skills/                       #   Codex / Claude Code 共享 Skill 单源
├── .claude/                          # Claude Code 项目配置
│   ├── CLAUDE.md                     #   Claude Code 项目入口说明
│   └── skills -> ../.agents/skills   #   Claude Code 兼容入口
├── .vscode/                          # VSCode 项目配置
├── docs/                             # 开发文档与笔记
│   ├── analysis/                     #   代码分析产出
│   └── feature/                      #   特性开发笔记（不入仓库）
├── scripts/                          # 辅助脚本
│   ├── bootstrap.sh                  #   一键初始化脚本
│   ├── install-corp-ca.sh            #   安装公司代理 CA 到系统信任库
│   ├── profile-analyse.sh            #   vLLM profile 分析与归档
│   ├── preview-vllm-ascend-docs.sh   #   文档构建 & 预览
│   ├── run-benchmark.sh              #   基准测试运行脚本
│   └── setup-ssh-key.sh              #   SSH 密钥初始化与公钥安装
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
| `install-corp-ca.sh` | 安装公司代理 MITM 根 CA 到系统信任库 | `-p <host:port>` 指定代理；`-f` 强制重装 |
| `profile-analyse.sh` | 分析 vLLM profile，并将本次 profile 压缩归档到独立目录 | `-p <dir>` profile 根目录；`-g <pattern>` 匹配模式；`-o <dir>` 归档目录 |
| `preview-vllm-ascend-docs.sh` | 构建 vllm-ascend 文档并预览 | `-t` AI 翻译；`-s` 仅构建不启动服务；`PORT=9000` 自定义端口 |
| `run-benchmark.sh` | 运行 ais_bench 基准测试 | `-m <name>` 模型配置；`-d <name>` 数据集（可多次指定） |
| `setup-ssh-key.sh` | 生成或复用本机 SSH 密钥，并安装公钥到远端服务器 | `-i <addr>` 远端 IP；`-u <name>` 远端用户（默认 root） |

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
