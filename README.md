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
├── .devcontainer/                    # Dev Container 多版本配置
│   ├── .env                          #   各版本共用的本机代理配置（由模板生成，不入仓库）
│   ├── post-create.sh                #   各版本共用的容器创建后初始化脚本
│   └── <version>/                    #
│       └── devcontainer.json         #   各版本 Dev Container 配置（由模板生成，不入仓库）
├── .vscode/                          # VSCode 项目配置
│   ├── launch.json                    #   VSCode 本机调试配置（由模板生成，不入仓库）
│   └── settings.json                  #   VSCode 工作区设置
├── docs/                             # 开发文档与笔记
│   ├── analysis/                     #   代码分析产出
│   └── feature/                      #   特性开发笔记（不入仓库）
├── templates/                        # 本机配置模板（入仓库）
│   ├── devcontainer.json.template    #   dev 版本 Dev Container 配置模板
│   ├── devcontainer.env.template     #   Dev Container 共享代理变量模板
│   ├── env.template                  #   统一环境变量模板
│   ├── launch.json.template           #   VSCode 调试配置模板
│   ├── server.sh.template             #   vLLM 单体服务启动脚本模板
│   ├── p_server.sh.template           #   vLLM Prefill 服务启动脚本模板
│   ├── d_server.sh.template           #   vLLM Decode 服务启动脚本模板
│   └── proxy_server.sh.template       #   vLLM PD Proxy 启动脚本模板
├── scripts/                          # 辅助脚本
│   ├── lib/
│   │   ├── bark_mcp_config_helper.py  #   Bark MCP TOML / JSON 配置修改 helper
│   │   └── common.sh                  #   Bash 脚本公共函数库
│   ├── bootstrap.sh                  #   一键初始化脚本
│   ├── configure-bark-mcp.sh         #   配置/卸载 Codex / Claude Code 全局 Bark MCP
│   ├── install-ascend-stack.sh       #   从 pkg/ 按项安装 CANN / torch_npu / triton_ascend
│   ├── install-corp-ca.sh            #   安装公司代理 CA 到系统信任库
│   ├── install-pre-commit.sh         #   安装并预热 vllm-ascend pre-commit 环境
│   ├── install-vllm-source.sh        #   安装 vLLM 与 vLLM Ascend 源码
│   ├── process-trace.sh              #   根据进程 PID 查询运行目录与容器归属
│   ├── profile-analyse.sh            #   vLLM profile 分析与归档
│   ├── preview-vllm-ascend-docs.sh   #   文档构建 & 预览
│   ├── run-benchmark.sh              #   基准测试运行脚本
│   ├── server.sh                     #   本机 vLLM 单体服务启动脚本（由模板生成，不入仓库）
│   ├── p_server.sh                   #   本机 vLLM Prefill 启动脚本（由模板生成，不入仓库）
│   ├── d_server.sh                   #   本机 vLLM Decode 启动脚本（由模板生成，不入仓库）
│   ├── proxy_server.sh               #   本机 vLLM PD Proxy 启动脚本（由模板生成，不入仓库）
│   └── setup-ssh-key.sh              #   SSH 密钥初始化与公钥安装
├── benchmark-outputs/                # 基准测试产物（不入仓库）
├── log/                              # vLLM 服务日志（不入仓库）
├── weekly-report/                    # 周报产出（不入仓库）
├── tmp/                              # 临时文件（不入仓库）
├── pkg/                              # 大二进制包（不入仓库）
├── .env                              # 统一环境变量（由模板生成，不入仓库）
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

首次运行 `bootstrap.sh` 时，会在目标文件缺失时从 `templates/` 复制部分本机配置文件；已经存在的本机配置不会被覆盖。

## 脚本速查

| 脚本 | 用途 | 常用参数 |
| ------ | ------ | --------- |
| `bootstrap.sh` | 初始化本机配置、克隆代码仓库、配置 remote | `-b` 同时克隆 benchmark |
| `configure-bark-mcp.sh` | 为 Codex / Claude Code 配置或卸载全局 Bark HTTP MCP；优先使用对应 CLI，未安装时回退 Python helper | `-t codex/claude/all` 指定目标；`-k <key>` 直接传入 Bark key；`-f` 覆盖已有 `bark`；`-u` 卸载 |
| `install-ascend-stack.sh` | 从指定包目录按项安装 CANN / torch_npu / triton_ascend | `-p <dir>` 或 `-p <version>` 指定包目录或 `pkg/` 下版本名；`-i cann,torch_npu,triton_ascend,all` 指定安装项；`-y` 确认执行；`--dry-run` 仅预览 |
| `install-corp-ca.sh` | 安装公司代理 MITM 根 CA 到系统信任库 | `-p <host:port>` 指定代理；`-f` 强制重装 |
| `install-pre-commit.sh` | 使用 APT/YUM 与 pip 安装 vllm-ascend lint 依赖，预热并启用 Git hooks | 无参数；`-h` 查看帮助 |
| `install-vllm-source.sh` | 卸载并从源码安装 vllm / vllm-ascend | 默认使用工作区 `tmp/`；`-s` 跳过卸载；`-v` 仅 vllm；`-a` 仅 vllm-ascend；`-c` 清理 Ascend 构建缓存；`-t <dir>` 覆盖构建临时目录 |
| `process-trace.sh` | 根据宿主机进程 PID 查询运行目录、容器运行时、容器 ID、名称和状态 | 直接传 `<pid>`（推荐），或使用 `-p | --pid <pid>` |
| `profile-analyse.sh` | 分析 vLLM profile，并将本次 profile 压缩归档到独立目录 | `-p <dir>` profile 根目录；`-g <pattern>` 匹配模式；`-n <name>` 归档名称 |
| `preview-vllm-ascend-docs.sh` | 构建 vllm-ascend 文档并预览 | `-t` AI 翻译；`-s` 仅构建不启动服务；`PORT=9000` 自定义端口 |
| `run-benchmark.sh` | 运行 ais_bench 精度或性能测试 | `-m <name>`、`-d <name>` 可重复；`--mode all/perf`；`-w <dir>` 输出根目录；`--debug` 显式调试；`--` 透传额外参数 |
| `server.sh` | 本机 vLLM 单体服务启动脚本（由模板生成，不入仓库） | 首次生成后按机器修改配置 |
| `p_server.sh` | 本机 vLLM Prefill 启动脚本（由模板生成，不入仓库） | 配置本机网络并直接修改 `vllm_cmd` |
| `d_server.sh` | 本机 vLLM Decode 启动脚本（由模板生成，不入仓库） | 配置本机网络并直接修改 `vllm_cmd` |
| `proxy_server.sh` | 本机 vLLM PD Proxy 启动脚本（由模板生成，不入仓库） | 直接在 `proxy_cmd` 中配置 P/D 后端 |
| `setup-ssh-key.sh` | 生成或复用本机 SSH 密钥，并安装公钥到远端服务器 | `-i <addr>` 远端 IP；`-u <name>` 远端用户（默认 root） |
| `.devcontainer/post-create.sh` | 各个 Dev Container 创建后初始化 | 由 devcontainer 自动调用 |

带命令行参数的工作区辅助脚本支持 `-h | --help` 查看完整用法；本机服务启动脚本通过文件中的命令数组直接修改部署参数。

## 环境

### 环境变量（`.env`）

`.env` 文件存放统一环境变量，脚本启动时自动加载。主要变量：

- `BARK_KEY` — Bark 通知用 Key，由 `configure-bark-mcp.sh` 默认读取；也可通过脚本 `-k | --key` 参数传入
- `DEEPSEEK_API_KEY` — AI 翻译用 API Key，由 `preview-vllm-ascend-docs.sh` 读取

### Dev Container 环境变量

Dev Container 主要环境变量：

- `devcontainer_proxy` — HTTP/HTTPS 代理地址，各个 Dev Container 启动时会读取并设置 `http_proxy` / `https_proxy` / `HTTP_PROXY` / `HTTPS_PROXY`
- `devcontainer_git_user_name` — Dev Container 内的 Git `user.name`，兼容大写变量 `DEVCONTAINER_GIT_USER_NAME`
- `devcontainer_git_user_email` — Dev Container 内的 Git `user.email`，兼容大写变量 `DEVCONTAINER_GIT_USER_EMAIL`

### Python 环境策略

需要 Python 环境的工作区脚本会在运行时动态选择解释器：

1. 优先尝试激活脚本对应的目标 conda 环境。
2. 如果系统完全没有 `conda`，则使用当前可用的系统 Python。
3. 如果 `conda` 已安装但初始化失败、目标环境不存在或激活失败，脚本会立即报错，不会把依赖误装到系统 Python。

推荐的目标 conda 环境如下：

| 环境名 | 用途 |
| ------ | ------ |
| `vllm-ascend-dev` | 通用开发环境 |
| `ais_bench` | 基准测试（ais_bench） |
