---
name: remote-init
description: 初始化或复用 vllm-ascend-dev 的 NPU Dev Container，准备节点配置、对齐源码并调用已有安装和诊断工具。支持通过 SSH 或在目标机器就地执行。
---

# 远端环境初始化

先读根 README、AGENTS，确定目标宿主机和工作区。连接与命令执行使用
[remote-execution](../remote-execution/SKILL.md)，只操作指定节点。
已在目标容器中时，从当前源码和安装阶段开始，不创建嵌套容器。
多节点先并行收集目录、镜像、权重和工具缺口，集中确认需要用户选择的事项；
已获授权的步骤连续推进，不因另一节点等待而停下。

## 流程

1. **确定目录。** 优先使用用户指定路径，否则读取 VS Code 用户设置中的
   `npuMonitor.containers.workspacePaths`（JSONC），在目标机器检查候选。
   设置不可读、配置为空或位置不明确时询问；唯一已有工作区则复用，多个则让
   用户选择。新建优先已挂载且可写的共享盘，其次 home，不固定具体路径。
   多台机器使用同一共享盘时，统一复用同一个工作区目录，不按节点另建目录。
   复用的是工作区路径、节点私有配置和可兼容的持久构建缓存，远端旧源码可被
   指定提交覆盖。共用 checkout 的源码更新、安装和编译串行执行；节点私有配置
   不互相覆盖。共享编译产物先核对架构、Python、torch/CANN 的兼容性；只有具体
   证据表明其他任务正在使用将被覆盖的产物时才协调，不因其他容器有进程就停下。
   实际目录不匹配现有工作区筛选时告知需补充的路径，不自行修改 VS Code 设置。
2. **验证代理。** 从参考工作区私有 `.devcontainer/.env` 读取所有
   `devcontainer_proxy` 行作为候选，不 source 后取最后一条。按目标宿主机
   IPv4 首段匹配，在该节点通过所选代理限时检查 GitHub 访问，避免被
   `NO_PROXY` 绕过。宿主机因公司代理自签证书失败时，按用户约定仅对本次准备
   命令临时跳过校验（curl `-k`、Git `http.sslVerify=false`）；连接失败仍中断，
   不换代理或直连。参考配置不可获取或匹配有歧义时询问。
   后续 clone、fetch、bootstrap 和 CLI 下载沿用同一代理：同时设置大小写
   HTTP/HTTPS 代理变量，并通过命令级 Git 配置覆盖旧 `http.proxy`；bootstrap
   的子进程通过 `GIT_CONFIG_COUNT/KEY_n/VALUE_n` 继承，保留其他已有配置项。
   不写全局代理或关闭全局 TLS 校验，临时绕过仅限宿主机准备阶段。
3. **获取或更新工作区。** 不存在时通过 HTTPS 克隆个人仓库
   `https://github.com/li1how/vllm-ascend-dev.git`。已有目录先核对 Git 根目录
   和来源，不符则停止。若它是本次远端执行副本，记录原分支、SHA 和 dirty
   状态后 fetch，并在原路径对齐本次开发端指定的根仓库提交；远端未提交源码
   修改、分叉或无跟踪分支不阻止对齐，不创建新副本。若该目录实际就是开发
   checkout，不执行覆盖。共享盘触发 Git 所有权检查时，先核对路径、来源和
   所有者，再按需添加精确 `safe.directory`；不使用 `*`。更新完成后在真实宿主机运行
   `scripts/bootstrap.sh`；需要 benchmark 时使用 `-b`。保留原有 A3/A5
   自动选择和私有配置，不批量清理未跟踪文件或缓存。
4. **准备 CLI 与配置。** 检查可调用的 Dev Container CLI（含已安装但不在 PATH
   的入口）及其 Node.js；可用则复用，不自动升级。缺少 CLI 时直接安装：有兼容
   Node.js/npm 则将 `@devcontainers/cli` 安装到用户独立目录，否则使用
   [官方安装脚本](https://github.com/devcontainers/cli/blob/main/scripts/install.sh)
   的 `--prefix`、`--version`、`--node-version` 安装 CLI 和独立 Node.js，不替换
   系统 Node.js。先下载并审阅脚本，全部下载沿用所选代理、限时和临时证书策略；
   不仅处理外层下载而遗漏安装器内部请求。记录版本与绝对入口，验证 `--version`
   和 `up --help` 后继续；权限不足或安装失败时保留日志并报告。
   检查节点已有的 vllm-ascend 镜像，按硬件、架构和明确版本
   列出少量较新候选，包含标签、ID、创建时间及兼容信息，未知项明确注明。
   用户选择后才写入配置，只有一个候选也需确认；复用容器且不换镜像时沿用原配置。
   从已有挂载、共享盘和常见权重目录核实实际权重，新挂载默认内外路径一致，
   缺失或不明确时询问。env 只写一条选中代理，Git 身份参考现有配置；
   已有配置仅改必要字段。共用工作区但配置不同，将各自私有配置保存在
   `.devcontainer/<节点>/devcontainer.json` 和同目录 `.env`，相同配置可复用；
   `--env-file` 使用目标宿主机绝对路径。缺少 Docker 或镜像时报告，不自动安装
   Docker、升级基础运行栈或下载权重。
5. **启动容器。** 在宿主机使用 Dev Container CLI 创建或复用容器，确认
   post-create 完成，核对完整容器 ID 和工作区映射。可参考 NPU Monitor
   的精简信息；同一工作区有多个容器时让用户选择，不猜测目标。
   post-create 安装公司 CA 后正常验证 TLS，撤销临时证书绕过；按容器实际系统
   CA 路径设置私有配置的 `SSL_CERT_FILE`、`REQUESTS_CA_BUNDLE` 和 `PIP_CERT`，
   在新的 `devcontainer exec` 会话验证 Python/pip，无需永久关闭校验。
6. **对齐源码。** 在容器内读取子仓库 AGENTS。新建工作区没有指定提交时，
   将 Ascend 切到 `main` 并更新，从该 checkout 的
   `.github/vllm-main-verified.commit` 读取 SHA，将 vLLM 固定到该提交，允许
   detached HEAD。有本次开发端指定提交时，以该提交为准。
   已有远端执行 checkout 先核对路径、来源并记录原分支、SHA、dirty 状态，
   再在原路径覆盖对齐指定提交；远端未提交源码修改和分叉不触发询问。仅当
   证据表明正在运行的任务实际使用同一物理源码路径，或将被覆盖的编译产物时
   协调换码；其他容器有进程不等于使用该源码。两端是同一物理开发 checkout
   时不执行覆盖。
   编译前初始化所需子模块并检查 Git 访问，所有权异常按上述精确路径规则处理，
   包括嵌套子模块，避免安装中途才失败。
7. **安装与诊断。** 直接调用 `scripts/install-vllm-source.sh`，需要两者时
   先 vLLM、后 vLLM-Ascend；可按组件使用 `--vllm-only` 或 `--ascend-only`。
   缺失、非 editable 或源码路径不符时自动安装；正确且仍适用时复用，依赖或
   编译代码变化时重新安装。先用同一 Python 记录镜像原有 `pip check`，结合
   选定源码核对 torch/torch_npu、Triton、NumPy、FastAPI 等关键约束；无交集时
   先报告并确定处理方式，不反复升降单个包或修改源码声明来隐藏冲突。
   verified SHA 不代表镜像所有依赖都兼容。安装脚本从中性目录检查真实包路径；
   仅工作区根目录出现 namespace 遮蔽时提示换到输出目录运行，不因此重装。
   使用安装时相同的 Python 调用
   [运行环境诊断](../vllm-runtime-diagnosis/SKILL.md)，核对 editable 来源和
   两个模块的真实 import 路径、`LLM` 导入及 `vllm --help`，比较安装前后依赖
   冲突。不以安装退出成功代替诊断，仍有冲突时明确标为环境有未解决限制。

## 结果

汇报容器与 post-create 状态、源码 SHA/dirty、实际 Python、安装和诊断结果及
日志位置。沿用原有 Python 环境策略，保留失败日志，不默认清理构建缓存、
升级 CANN 或启动模型测试。私有配置不提交；开发 checkout 和无关仓库的
已有修改、暂存状态保持原样，选定远端执行 checkout 的旧源码允许覆盖。
