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

各仓库分别使用原生 Git 命令，只同步本次需要的仓库。先核对双方 remote、
分支、SHA 和 `git status`；按用户授权提交并推送到个人 fork，再在接收端
fetch 该提交，用 `git merge --ff-only <SHA>` 更新并核对实际 HEAD。
首次切换任务分支需明确目标；未修改的依赖仓库可保持固定 SHA 和 detached HEAD。

远端直接修改后，同样提交、推送，再在本地获取。接收端有未提交修改、历史分叉
或仓库来源不符时停止，不自动 stash、reset 或强推。运行中的测试使用哪个
checkout，就保持该 checkout 稳定。私有配置、权重和产物不参与源码同步。

## 日志与结果

默认前台执行，使用原脚本的日志和输出目录；需要额外留存时重定向日志并保留
实际退出码。长任务沿用原脚本的进程管理方式，SSH 断线后先检查远端日志和
进程，不自动重跑；只停止本任务启动的进程，不把断开 SSH 当作远端已清理。

文件按需通过原生文件传输或 SSH 读取取回，明确两端路径，保留已有产物。
汇报执行目标、涉及仓库的 SHA/dirty、退出码和日志位置，不声称未执行的验证通过。
