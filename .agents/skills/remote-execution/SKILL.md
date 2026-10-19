---
name: remote-execution
description: 使用 Git、SSH 和 Dev Container CLI 在当前环境或指定远端执行命令、同步源码和取回文件。用于跨机开发与验证；环境初始化使用 remote-init。
---

# 远端同步与执行

先读根 README、AGENTS，明确开发 checkout、执行位置和本次命令。
环境尚未准备好时使用 [remote-init](../remote-init/SKILL.md)。

## 执行命令

- **当前环境：** 进入目标目录直接运行原有命令。
- **SSH 宿主机：** 使用下面的 SSH 小工具转发一条命令。
- **指定容器：** 在其宿主机调用 `devcontainer exec`，指定完整
  `--container-id`、宿主机 `--workspace-folder` 和实际 `--config`，执行原有命令。
  已在该容器中则直接运行。

小工具 [scripts/ssh.py](scripts/ssh.py) 使用 Python 3.10+，只负责 SSH 连接，
无需在目标端安装。以下命令从控制端工作区根目录调用：

```bash
python3 .agents/skills/remote-execution/scripts/ssh.py \
  --config "$SSH_CONFIG" "$SSH_HOST" -- uname -m
```

使用用户指定的 SSH 配置和主机别名，区分宿主机与容器内的工作区目录。
容器执行前用只读查询核对完整 ID、实际配置路径和工作区挂载，
不把路径混用，不自动换节点或容器。

命令按参数传递；需要管道、重定向、切换目录或环境激活时显式调用 `bash -c`，
将动态值作为位置参数传入。Python 环境按目标工作区 README 的策略选择。
SSH 工具保留括号别名、Windows/WSL 路径、密钥和跳板兼容，不修改 SSH 配置或
主机密钥记录；括号别名遇到无法兼容的 `Match` 配置时报告，不忽略条件连接。

## Git 同步

先分清开发 checkout（本次源码权威）和选定的远端执行 checkout（可覆盖副本）。
各独立仓库只同步本次需要的提交：在开发 checkout 核对 remote、分支、SHA 和
`git status`，按本次授权提交并推送；开发 checkout 的未提交修改不丢弃。若用户
明确要保留远端直接修改的代码，则先将该远端 checkout 视为开发端并提交、推送。

在远端执行 checkout 核对实际物理路径、Git 根目录和仓库来源，记录覆盖前的
分支、SHA、dirty 状态；来源或路径不符时停止，避免覆盖错仓库。确认目标提交
可从指定 remote 获取后，在**原 checkout** fetch 并强制对齐该准确 SHA（例如
`git -C "$REMOTE_REPO" switch --detach --force "$TARGET_SHA"`），再核对 HEAD。
远端执行副本的未提交源码修改或分叉无需保留，不自动 stash，也不为此创建
worktree、独立副本或请求许可。不使用 `git clean -fdx`，只在未跟踪文件确实
阻碍切换时处理具体冲突文件；私有配置、权重、日志和构建缓存不参与源码覆盖。
不对任何 remote 强推。两端实际指向同一物理 checkout 时，直接使用该源码，
不执行覆盖。

默认在各节点本地盘工作区执行，通过宿主机 `findmnt -T` 核实底层文件系统。
不同节点即使路径相同也使用各自 checkout；共享盘只中转缓存快照。容器内路径
按初始化私有配置统一为 `/workspaces/vllm-ascend-dev`。同一物理 checkout 的
更新与构建串行进行。检查任务记录、进程工作目录、启动参数、
挂载与 editable 来源等能指向同一物理源码路径的证据；仅在换码或编译会影响
已证实使用该路径的运行任务时协调。其他容器有进程、占用 NPU 或含同名仓库，
都不能单独证明源码冲突，不据此停下询问。只停止本任务启动的进程。

## 日志与结果

默认前台执行，使用原脚本的日志和输出目录；需要额外留存时重定向日志并保留
实际退出码。长任务沿用原脚本的进程管理方式，SSH 断线后先检查远端日志和
进程，不自动重跑；只停止本任务启动的进程，不把断开 SSH 当作远端已清理。

文件按需通过原生文件传输或 SSH 读取取回，明确两端路径，保留已有产物。
汇报执行目标、涉及仓库的 SHA/dirty、退出码和日志位置，不声称未执行的验证通过。

## 统一远端入口

优先用 NPU Monitor MCP `list_hosts`、`rank_idle_hosts`、`get_host_state` 选机；
分别查看 NPU 与容器的采集时间。NPU 状态过期调用 `refresh_hosts`，仅容器信息过旧
调用 `refresh_containers`，新建容器前用 `list_host_images` 展示镜像候选并由用户
选择。复用容器读取其实际 image ID。MCP 不可用时用只读 SSH 查询并在运行记录中
注明来源。空闲状态不保证资源预留，执行前复核占用。

源码修改、Git 同步、安装、普通命令、静态检查和 UT 沿用对应工具直接执行，
无需任务卡。统一远端入口 `scripts/remote-task.py` 显式接收 `--ssh-config`、
`--host`、完整 `--container-id`、宿主机 `--workspace-folder` 和 `--config`。
它先以只读 Docker inspect 核对容器身份、配置和 bind mount，再用
`devcontainer exec` 执行，使代理环境生效。`inspect` 核验 editable 来源、
缓存和指定权重；`weight record/list/verify` 管理节点私有权重清单。
`prepare --jobs <数量> --build-cache-dir <容器内目录>` 根据来源、原生输入和安装记录按需调用
`install-vllm-source.sh`，先 vLLM 后 vLLM-Ascend；只改 Python 代码时复用
editable 安装并提示重启服务。缓存默认位于本地工作区
`.cache/vllm-ascend/csrc-build-cache`；复制使用 remote-init 的
[缓存工具](../remote-init/references/cache-transfer.md)。构建现场失配时报告具体项，
不自动删除。安装退出码、组件耗时与缓存统计保存在 `log/install-source.*/summary.json`，
`prepare` 关联这些记录；缓存统计不可用时明确报告，不将零次事件视为全部命中。

仅在**启动 vLLM 服务或执行模型、AISBench 等运行测试**时用本 Skill 的
`scripts/task-card.py` 创建 `task-cards/<任务ID>/`。登记仓库版本、节点、
容器、允许操作 `serve` 或 `test` 和阶段。单体服务使用
`templates/server.sh.template` 生成卡内 `run.sh`；P/D 和 Proxy 使用各自现有
服务模板。测试命令直接写入卡内 `run.sh`，不新增通用模板。计划或命令变化时先
`task update`，再修改脚本、`task seal`。`run` 核对脚本、源码摘要、阶段与执行
目标，执行快照保存在卡内 `runs/`。完成后用 `task archive` 移至
`task-cards/archive/<任务ID>/`。

远端按 `task create/update/seal` → `run --case <用例> --task-id <任务ID>
--stage <阶段> --operation serve/test` → `status <运行ID>` → `task archive` 执行。
`log/remote-runs/<用例>-<运行ID>/` 保存容器、镜像、源码、日志、耗时和退出码。
服务脚本保持前台，状态才反映服务进程；SSH 断线后先查状态与日志，不自动重跑。
密钥只通过容器环境传入。先做短请求与小样本冒烟，再运行正式测试；benchmark 的
`--max-runtime-seconds` 可选，触及时逐题产物保留，结果标记不完整。
未指定测试节点时不启动远端模型。
