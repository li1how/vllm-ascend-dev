# vLLM Ascend 开发工作区

vLLM Ascend 多仓库开发工作区。

[README.md](README.md) 包含工作区目录结构、快速开始、脚本速查、环境变量与 Python 环境策略说明；所有操作前先读取 README，确认当前工作区布局和环境约束。

## 开发目录选择

**默认以 `vllm-ascend/` 为目标目录**，`vllm/` 偶尔开发。

- 任务涉及 Ascend 插件 → `vllm-ascend/`，开发前先导入 [AGENTS.md](vllm-ascend/AGENTS.md)
- 任务明确涉及 vLLM 上游源码 → `vllm/`，开发前先导入 [AGENTS.md](vllm/AGENTS.md)
- 任务涉及工作区脚本、模板、文档或共享 Skill → 工作区根目录，使用 [workspace-maintenance](.agents/skills/workspace-maintenance/SKILL.md)
- 不确定属于哪个目录 → 主动询问用户

## 环境

环境变量、Python 环境策略详见已导入的 README。以下为补充说明：

- 网络 / SSL：公司代理有自签证书，正常情况系统已配置；部分 Python 包（如 certifi）自带 CA bundle，需设置 `SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt`
- vllm-ascend CI：若 `format.sh ci` 因工具缺失、hook 下载失败或可执行文件架构错误等环境问题失败，先在工作区根目录运行 `./scripts/install-pre-commit.sh`，再原样重跑检查；代码检查失败仍按 hook 输出修复。

## 执行位置与代码同步

- 开发位置与执行位置分别确定：代码可在本地或远端修改，检查和测试可就地执行或通过 SSH 调用远端容器。操作前明确开发 checkout、执行端及容器（如适用）；未指定远端目标时默认就地执行。
- 需要 NPU 的测试在具备 NPU 的环境执行；[vllm-runtime-diagnosis](.agents/skills/vllm-runtime-diagnosis/SKILL.md) 只诊断目标 NPU 运行环境，可就地执行或通过 SSH 调用。当前环境缺少 NPU 不代表远端故障，不自动选择其他节点。
- 初始化使用 [remote-init](.agents/skills/remote-init/SKILL.md)，包含容器准备和 editable 源码安装；初始化完成不代表模型或 NPU 测试通过。
- 仅在启动 vLLM 服务或执行模型、AISBench 等运行测试前，于 `task-cards/<任务ID>/` 创建任务卡，写明目标 SHA、节点容器、服务/测试阶段与实际 `run.sh`；计划或命令变化先 `update`，再编辑脚本、`seal` 并由卡片执行。完成后归档，卡片不提交。源码修改、同步、安装、普通命令、静态检查、UT 和 Git 操作按相应流程直接执行，不要求任务卡。
- 跨机开发以本次选定的开发 checkout 为源码权威；各独立仓库分别 commit/push，再将指定提交同步到远端执行 checkout。开发 checkout 的未提交修改要保留，不自动暂存无关修改；根仓库提交不能代替子仓库提交。
- 远端执行 checkout 是可覆盖的源码副本：先确认仓库路径、来源和目标 SHA，记录其原分支、SHA、dirty 状态；即使有未提交源码修改或历史分叉，也直接在原路径对齐指定提交，不为此建独立副本、worktree 或反复询问。用户明确指定远端代码为开发成果时，先把它当作开发 checkout 处理。若两端实际是同一个物理 checkout，不执行覆盖操作。
- 默认使用各节点本地盘工作区，通过宿主机 `findmnt -T` 核实底层文件系统；源码通过 Git 同步，持久构建缓存从已核验来源复制，共享盘只中转缓存快照。初始化私有配置统一容器内工作区路径为 `/workspaces/vllm-ascend-dev`，A3/A5 公共模板保持现状。各节点独立 checkout；同一物理 checkout 的更新和构建串行执行。仅在有具体证据表明某任务正在读取同一源码路径或使用将被覆盖的编译产物时，协调该冲突。其他容器有进程、占用 NPU 或使用同名仓库，本身不构成阻塞。
- 测试记录各仓库实际 SHA、dirty 状态、目标容器和日志位置。对已证实使用同一 checkout 的运行任务，不在其运行中换码；只停止自己启动的服务，SSH 断线后先检查远端日志和进程，不自动重跑。
- 私有配置、Agent 凭据、权重和产物不作为源码提交。跨机同步与命令执行方式见 [remote-execution](.agents/skills/remote-execution/SKILL.md)，验证 Skill 沿用原有脚本。

## 约束

- **不确定目录归属先询问**。跨仓库改动时，先确认目标目录再动手。
- **开发前先导入对应子项目的 AGENTS.md**，按「开发目录选择」中的指引操作。
- **多仓库独立 Git**。项目根目录、`vllm/`、`vllm-ascend/`、`benchmark/` 各自是独立的 Git 仓库，Git 操作需进入对应目录执行。
- **Python 环境动态选择**。需要 Python 环境的工作区脚本优先使用目标 conda 环境；只有系统完全没有 conda 时才使用当前可用的系统 Python。conda 已安装但初始化失败、目标环境不存在或激活失败时必须报错，禁止回退系统 Python。
- **脚本公共函数复用**。维护工作区 Bash 脚本时，优先复用 `scripts/lib/common.sh` 中的函数；模板脚本和本机私有 `scripts/server.sh` 脚本按需保持独立。
- **Python 环境结论记忆**。Agent 在本工作区完成 Python 环境判断后，应将稳定结论记录到可用记忆中，避免后续重复判断；记录内容包括工作区路径、判断日期、目标 conda 环境是否可用、实际 Python 路径、关键依赖检查结果和 fallback 原因。不得记录 token、API key、代理凭据等敏感信息；复用记忆前需做低成本校验，防止环境变化导致结论过期。
- **共享 Skill 单源在 `.agents/skills/`**。`.claude/skills` 仅作为指向 `../.agents/skills` 的兼容入口；新增或修改工作区共享 Skill 时只改 `.agents/skills/`，不要复制到 `.claude/skills`。
