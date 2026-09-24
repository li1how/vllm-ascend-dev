# vllm-ascend-dev

vLLM Ascend 开发工作区，支持本地或远端开发，以及就地或跨机测试。

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
├── .github/
│   └── workflows/
│       └── sync-forks.yml            #   定时同步 vLLM 与 vLLM Ascend fork
├── .vscode/                          # VSCode 项目配置
│   ├── launch.json                    #   VSCode 本机调试配置（由模板生成，不入仓库）
│   └── settings.json                  #   VSCode 工作区设置
├── docs/                             # 开发文档与笔记
│   ├── analysis/                     #   代码分析产出
│   └── feature/                      #   特性开发笔记（不入仓库）
├── templates/                        # 本机配置模板（入仓库）
│   ├── devcontainer.a3.json.template #   A3 Dev Container 配置模板
│   ├── devcontainer.a5.json.template #   A5 Dev Container 配置模板
│   ├── devcontainer.env.template     #   Dev Container 共享代理变量模板
│   ├── env.template                  #   统一环境变量模板
│   ├── launch.json.template           #   VSCode 调试配置模板
│   ├── server.sh.template             #   vLLM 单体服务启动脚本模板
│   ├── p_server.sh.template           #   vLLM Prefill 服务启动脚本模板
│   ├── d_server.sh.template           #   vLLM Decode 服务启动脚本模板
│   └── proxy_server.sh.template       #   vLLM PD Proxy 启动脚本模板
├── scripts/                          # 辅助脚本
│   ├── tests/                        #   脚本回归测试
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
├── log/                              # 服务日志（不入仓库）
├── .cache/                           # 节点本地持久构建缓存（不入仓库）
├── task-cards/                       # 任务卡（不入仓库）
│   ├── <任务ID>/                     # 当前任务卡和实际脚本
│   └── archive/                      # 已结束任务卡与产物
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
./scripts/bootstrap.sh -b                     # 同时克隆 benchmark 仓库
```

首次运行 `bootstrap.sh` 时，会在目标文件缺失时从 `templates/` 复制部分本机配置文件；已经存在的本机配置不会被覆盖。

### 开发与测试

代码可在本地或远端容器中开发，检查和测试可在当前环境执行，也可通过 SSH 调用远端容器。
跨机执行前通过 Git 同步代码；需要 NPU 的测试在具备 NPU 的容器中运行。

[remote-init](.agents/skills/remote-init/SKILL.md) 定义容器初始化和 editable 源码安装流程；
[remote-execution](.agents/skills/remote-execution/SKILL.md) 说明如何通过 Git 同步代码、在指定环境执行命令。serving、benchmark、profiling 等 Skill 沿用原有工具。
初始化会准备缺失的 CLI、处理代理证书并检查安装来源与依赖。默认使用各节点本地盘
工作区，源码通过 Git 同步、缓存通过快照复制；同一物理 checkout 的源码更新与构建
串行执行。安装完成不代表模型测试或全部依赖检查通过。

### 本地开发与远端测试

1. 用 NPU Monitor MCP 的 `list_hosts`、`rank_idle_hosts` 和 `get_host_state` 选候选，
   分别检查 NPU 与容器采集时间；信息过期时分别刷新。新建容器先用
   `list_host_images` 展示候选并确定镜像；复用容器核对实际镜像 ID。MCP 不可用时
   使用只读 SSH 查询并记录来源。执行前复核占用，空闲结果不是资源预留。
2. 以本地开发 checkout 的提交为准同步各仓库，在选定远端的原 checkout 对齐指定
   SHA。记录远端原分支、SHA 和 dirty 状态，核对路径与来源；不建 worktree，保留
   本地路径与缓存。先用宿主机 `findmnt -T` 核实底层文件系统，容器内路径统一为
   `/workspaces/vllm-ascend-dev`。源码同步、安装、普通命令及 Git 操作直接按
   [remote-execution](.agents/skills/remote-execution/SKILL.md) 执行，不建任务卡。
3. 用远端入口 `inspect` 核验容器、editable 来源、缓存和权重；从已核验节点按
   [缓存复制流程](.agents/skills/remote-init/references/cache-transfer.md) 将有效条目
   恢复到节点本地 `.cache/vllm-ascend/csrc-build-cache`，共享盘仅中转快照。
   构建现场仅在原生输入、环境和容器内路径匹配时恢复，目标已有构建目录保留。
   需要安装时用 `prepare --build-cache-dir <容器内目录>` 按需调用现有
   `install-vllm-source.sh`，先 vLLM 后 vLLM-Ascend，继续正常构建并复用缓存。
   仅 Python 代码变化且 editable 来源正确时无需重装，但要重启服务。
4. **启动 vLLM 服务或执行模型测试时才建任务卡。** 当前卡片放在
   `task-cards/<任务ID>/`，完成后归档到 `task-cards/archive/<任务ID>/`。服务卡
   从 `templates/server.sh.template`（或 P/D、Proxy 对应的现有模板）生成实际
   `run.sh`；测试卡直接在 `run.sh` 写短请求、AISBench 等实际命令。先登记目标
   SHA、节点容器和阶段，再编辑脚本、`seal`、`run`。脚本快照、日志和退出码保留
   在卡内 `runs/`；断线后查 `status`，不自动重跑。任务卡不提交。

示例（源码已同步且容器已确定）：

```bash
remote=(python3 .agents/skills/remote-execution/scripts/remote-task.py --ssh-config "$SSH_CONFIG" --host "$SSH_HOST" \
  --container-id "$CONTAINER_ID" --workspace-folder "$HOST_WORKSPACE" --config "$HOST_DEVCONTAINER_CONFIG" --discovery-source mcp)
"${remote[@]}" inspect --weight "$MODEL_PATH"
"${remote[@]}" prepare --jobs 32 --build-cache-dir /workspaces/vllm-ascend-dev/.cache/vllm-ascend/csrc-build-cache

"${remote[@]}" task create --title "PCP 服务" --repository vllm-ascend \
  --branch "$ASCEND_BRANCH" --candidate-sha "$ASCEND_SHA" --vllm-sha "$VLLM_SHA" \
  --allow serve --change "启动 PCP 服务" --stage "serve=$ASCEND_SHA" --template server
# 在返回的 task-cards/<TASK_ID>/run.sh 中修改模型、网络和启动参数
"${remote[@]}" task seal "$TASK_ID"
"${remote[@]}" run --case serve --task-id "$TASK_ID" --stage serve --operation serve
"${remote[@]}" status "$RUN_ID"

"${remote[@]}" task create --title "PCP 冒烟" --repository vllm-ascend \
  --branch "$ASCEND_BRANCH" --candidate-sha "$ASCEND_SHA" --vllm-sha "$VLLM_SHA" \
  --allow test --change "运行短请求和小样本测试" --stage "smoke=$ASCEND_SHA"
# 在返回的另一张任务卡 run.sh 中写明实际测试命令，再 seal 和 run
"${remote[@]}" task seal "$SMOKE_TASK_ID"
"${remote[@]}" run --case smoke --task-id "$SMOKE_TASK_ID" --stage smoke --operation test
"${remote[@]}" status "$SMOKE_RUN_ID"
"${remote[@]}" task archive "$SMOKE_TASK_ID" --outcome complete --summary "冒烟完成"
```

Agent 内部入口位于 `.agents/skills/remote-execution/scripts/`。服务和测试的计划或
命令变化时先 `task update`，再修改卡内脚本并重新 `seal`。运行中的服务脚本应保持
前台，以便任务卡状态反映真实进程。密钥只通过 Dev Container 环境传入。

## 脚本速查

| 脚本 | 用途 | 常用参数 |
| ------ | ------ | --------- |
| `bootstrap.sh` | 初始化本机配置、克隆代码仓库、配置 remote | `-b` 同时克隆 benchmark |
| `configure-npu-monitor-mcp.sh` | 从 `.env` 配置 Codex / Claude Code 的 NPU Monitor MCP | `-t codex/claude/all`；`-f` 替换；`-u` 卸载 |
| `configure-bark-mcp.sh` | 为 Codex / Claude Code 配置或卸载全局 Bark HTTP MCP；优先使用对应 CLI，未安装时回退 Python helper | `-t codex/claude/all` 指定目标；`-k <key>` 直接传入 Bark key；`-f` 覆盖已有 `bark`；`-u` 卸载 |
| `install-ascend-stack.sh` | 从指定包目录按项安装 CANN / torch_npu / triton_ascend | `-p <dir>` 或 `-p <version>` 指定包目录或 `pkg/` 下版本名；`-i cann,torch_npu,triton_ascend,all` 指定安装项；`-y` 确认执行；`--dry-run` 仅预览 |
| `install-corp-ca.sh` | 安装公司代理 MITM 根 CA 到系统信任库 | `-p <host:port>` 指定代理；`-f` 强制重装 |
| `install-pre-commit.sh` | 使用 APT/YUM 与 pip 安装 vllm-ascend lint 依赖，预热并启用 Git hooks | 无参数；`-h` 查看帮助 |
| `install-vllm-source.sh` | 卸载并从源码安装 vllm / vllm-ascend | 优先使用节点本地 `/var/tmp/`，回退工作区 `tmp/`；`-s` 跳过卸载；`-v` 仅 vllm；`-a` 仅 vllm-ascend；`-c` 显式清理构建现场；`-t <dir>` 临时目录；`-j <数>` 并发；`--build-cache-dir <dir>` 持久增量缓存（默认 `.cache/vllm-ascend/csrc-build-cache`） |
| `process-trace.sh` | 根据宿主机进程 PID 查询运行目录、容器运行时、容器 ID、名称和状态 | 直接传 `<pid>`（推荐），或使用 `-p | --pid <pid>` |
| `profile-analyse.sh` | 分析 vLLM profile，并将本次 profile 压缩归档到独立目录 | `-p <dir>` profile 根目录；`-g <pattern>` 匹配模式；`-n <name>` 归档名称 |
| `preview-vllm-ascend-docs.sh` | 构建 vllm-ascend 文档并预览 | `-t` AI 翻译；`-s` 仅构建不启动服务；`PORT=9000` 自定义端口 |
| `run-benchmark.sh` | 运行 ais_bench 精度或性能测试 | `-m <name>`、`-d <name>` 可重复；`--mode all/perf`；`-w <dir>` 输出根目录；`--debug` 显式调试；`--max-runtime-seconds` 可选总时限；`--` 透传额外参数 |
| `server.sh` | 本机 vLLM 单体服务启动脚本（由模板生成，不入仓库） | 首次生成后按机器修改配置 |
| `p_server.sh` | 本机 vLLM Prefill 启动脚本（由模板生成，不入仓库） | 配置本机网络并直接修改 `vllm_cmd` |
| `d_server.sh` | 本机 vLLM Decode 启动脚本（由模板生成，不入仓库） | 配置本机网络并直接修改 `vllm_cmd` |
| `proxy_server.sh` | 本机 vLLM PD Proxy 启动脚本（由模板生成，不入仓库） | 直接在 `proxy_cmd` 中配置 P/D 后端 |
| `setup-ssh-key.sh` | 生成或复用本机 SSH 密钥，并安装公钥到远端服务器 | `-i <addr>` 远端 IP；`-u <name>` 远端用户（默认 root） |
| `.devcontainer/post-create.sh` | 各个 Dev Container 创建后初始化 | 由 devcontainer 自动调用 |

带命令行参数的工作区辅助脚本支持 `-h | --help` 查看完整用法；本机服务启动脚本通过文件中的命令数组直接修改部署参数。

安装统计位于 `log/install-source.*/summary.json`，记录退出码、组件耗时、并发和
HIT/MISS/BYPASS；`ascend-build.log` 保留原生构建输出。远端 `prepare` 关联这些
统计记录。未产生缓存事件时明确标记统计不可用，不把零事件视为全部命中。

## 环境

### 环境变量（`.env`）

`.env` 文件存放统一环境变量，脚本启动时自动加载。主要变量：

- `NPU_MONITOR_MCP_URL` / `NPU_MONITOR_MCP_TOKEN` — 从 NPU Monitor 面板复制到 `.env`，运行 `./scripts/configure-npu-monitor-mcp.sh` 后重启客户端；插件须在同一环境运行
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
