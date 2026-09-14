---
name: remote-init
description: 初始化或复用 vllm-ascend-dev 的 NPU Dev Container，准备节点配置、对齐源码并调用已有安装和诊断工具。支持通过 SSH 或在目标机器就地执行。
---

# 远端环境初始化

先读根 README、AGENTS，确定目标宿主机和工作区。连接与命令执行使用
[remote-execution](../remote-execution/SKILL.md)，只操作指定节点。
已在目标容器中时，从当前源码和安装阶段开始，不创建嵌套容器。

## 流程

1. **确定目录。** 优先使用用户指定路径，否则读取 VS Code 用户设置中的
   `npuMonitor.containers.workspacePaths`（JSONC），在目标机器检查候选。
   设置不可读、配置为空或位置不明确时询问；唯一已有工作区则复用，多个则让
   用户选择。新建优先已挂载且可写的共享盘，其次 home，不固定具体路径。
2. **验证代理。** 从参考工作区私有 `.devcontainer/.env` 读取所有
   `devcontainer_proxy` 行作为候选，不 source 后取最后一条。按目标宿主机
   IPv4 首段匹配，在该节点通过所选代理限时检查 GitHub 访问，避免被
   `NO_PROXY` 绕过。参考配置不可获取或匹配有歧义时询问；无匹配或检查失败
   即中断，不换代理或直连。后续网络命令沿用该代理，不修改宿主机全局配置。
3. **获取或更新工作区。** 不存在时通过 HTTPS 克隆个人仓库
   `https://github.com/li1how/vllm-ascend-dev.git`。已有目录先核对 Git 根目录
   和来源，不符则停止；对根仓库 fetch，再以 `--ff-only` 更新当前分支到
   其跟踪分支的最新提交并核对 SHA。有未提交修改、分叉或无法确定跟踪分支时
   停止并询问，不强制覆盖。更新完成后，在真实宿主机运行
   `scripts/bootstrap.sh`；需要 benchmark 时使用 `-b`。保留原有 A3/A5
   自动选择、私有配置和子仓库开发状态。
4. **填写配置。** 检查节点已有的 vllm-ascend 镜像，按硬件、架构和明确版本
   列出少量较新候选，包含标签、ID、创建时间及兼容信息，未知项明确注明。
   用户选择后才写入配置，只有一个候选也需确认；复用容器且不换镜像时沿用原配置。
   从已有挂载、共享盘和常见权重目录核实实际权重，新挂载默认内外路径一致，
   缺失或不明确时询问。env 只写一条选中代理，Git 身份参考现有配置；
   已有配置仅改必要字段。缺少 Docker、CLI 或镜像时报告，不自动安装或下载权重。
5. **启动容器。** 在宿主机使用 Dev Container CLI 创建或复用容器，确认
   post-create 完成，核对完整容器 ID 和工作区映射。可参考 NPU Monitor
   的精简信息；同一工作区有多个容器时让用户选择，不猜测目标。
6. **对齐源码。** 在容器内读取子仓库 AGENTS。新建工作区将 Ascend 切到
   `main` 并快进更新，从该 checkout 的 `.github/vllm-main-verified.commit`
   读取 SHA，将 vLLM 固定到该提交，允许 detached HEAD。
   已有工作区保留开发分支和依赖提交，需要切换时询问；更新前检查修改、分叉
   和正在使用该 checkout 的进程，不强制覆盖。
7. **安装与诊断。** 直接调用 `scripts/install-vllm-source.sh`，需要两者时
   先 vLLM、后 vLLM-Ascend；可按组件使用 `--vllm-only` 或 `--ascend-only`。
   缺失、非 editable 或源码路径不符时自动安装；正确且仍适用时复用，依赖或
   编译代码变化时重新安装。使用安装时相同的 Python 调用
   [运行环境诊断](../vllm-runtime-diagnosis/SKILL.md)，核对 editable 来源和
   两个模块的 import 路径，不以安装退出成功代替诊断。

## 结果

汇报容器与 post-create 状态、源码 SHA/dirty、实际 Python、安装和诊断结果及
日志位置。沿用原有 Python 环境策略，保留失败日志，不默认清理构建缓存、
升级 CANN 或启动模型测试。私有配置不提交，已有修改和暂存状态保持原样。
