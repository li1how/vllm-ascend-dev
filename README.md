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
├── .devcontainer/                    # Dev Container 本机配置（不入仓库）
│   └── devcontainer.json             #   由 templates/devcontainer.json.template 生成
├── .vscode/                          # VSCode 项目配置
├── docs/                             # 开发文档与笔记
│   ├── analysis/                     #   代码分析产出
│   └── feature/                      #   特性开发笔记（不入仓库）
├── templates/                        # 本机配置模板（入仓库）
│   ├── devcontainer.json.template    #   devcontainer 模板
│   └── server.sh.template            #   vLLM 服务启动脚本模板
├── scripts/                          # 辅助脚本
│   ├── bootstrap.sh                  #   一键初始化脚本
│   ├── devcontainer-post-create.sh   #   Dev Container 创建后初始化
│   ├── install-corp-ca.sh            #   安装公司代理 CA 到系统信任库
│   ├── profile-analyse.sh            #   vLLM profile 分析与归档
│   ├── preview-vllm-ascend-docs.sh   #   文档构建 & 预览
│   ├── run-benchmark.sh              #   基准测试运行脚本
│   ├── server.sh                     #   本机 vLLM 服务启动脚本（由模板生成，不入仓库）
│   └── setup-ssh-key.sh              #   SSH 密钥初始化与公钥安装
├── benchmark-outputs/                # 基准测试产物（不入仓库）
├── log/                              # vLLM 服务日志（不入仓库）
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

首次运行 `bootstrap.sh` 时，会在目标文件缺失时从 `templates/` 复制本机配置：

- `templates/devcontainer.json.template` → `.devcontainer/devcontainer.json`
- `templates/server.sh.template` → `scripts/server.sh`

生成后的 `.devcontainer/devcontainer.json` 和 `scripts/server.sh` 不入仓库。请按机器实际情况修改 devcontainer 镜像、权重挂载、代理地址，以及 server 本机 IP、设备号和模型路径。
`server.sh` 会通过 `ifconfig` 根据本机 IP 自动获取网卡名；如需手动指定，可修改脚本中的 `nic_name`。
`server.sh` 默认将 vLLM 输出保存到 `log/vllm_<timestamp>.log`；如需调整路径，可修改脚本中的 `LOG_DIR` 或 `LOGFILE`。

## 脚本速查

| 脚本 | 用途 | 常用参数 |
| ------ | ------ | --------- |
| `bootstrap.sh` | 初始化本机配置、克隆代码仓库、配置 remote | `-b` 同时克隆 benchmark |
| `devcontainer-post-create.sh` | Dev Container 创建后初始化通用环境 | 由 devcontainer 自动调用 |
| `install-corp-ca.sh` | 安装公司代理 MITM 根 CA 到系统信任库 | `-p <host:port>` 指定代理；`-f` 强制重装 |
| `profile-analyse.sh` | 分析 vLLM profile，并将本次 profile 压缩归档到独立目录 | `-p <dir>` profile 根目录；`-g <pattern>` 匹配模式；`-n <name>` 归档名称 |
| `preview-vllm-ascend-docs.sh` | 构建 vllm-ascend 文档并预览 | `-t` AI 翻译；`-s` 仅构建不启动服务；`PORT=9000` 自定义端口 |
| `run-benchmark.sh` | 运行 ais_bench 基准测试 | `-m <name>` 模型配置；`-d <name>` 数据集（可多次指定） |
| `server.sh` | 启动本机 vLLM 服务（由模板生成，不入仓库） | 首次生成后按机器修改配置 |
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
