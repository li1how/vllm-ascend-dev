# vLLM Ascend 开发工作区

vLLM Ascend 多仓库开发工作区。

[README.md](README.md) 包含工作区目录结构、快速开始、脚本速查、环境变量与 Python 环境策略说明；所有操作前先读取 README，确认当前工作区布局和环境约束。

## 开发目录选择

**默认以 `vllm-ascend/` 为目标目录**，`vllm/` 偶尔开发。

- 任务涉及 Ascend 插件 → `vllm-ascend/`，开发前先导入 [AGENTS.md](vllm-ascend/AGENTS.md)
- 任务明确涉及 vLLM 上游源码 → `vllm/`，开发前先导入 [AGENTS.md](vllm/AGENTS.md)
- 不确定属于哪个目录 → 主动询问用户

## 环境

环境变量、Python 环境策略详见已导入的 README。以下为补充说明：

- 网络 / SSL：公司代理有自签证书，正常情况系统已配置；部分 Python 包（如 certifi）自带 CA bundle，需设置 `SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt`

## 约束

- **不确定目录归属先询问**。跨仓库改动时，先确认目标目录再动手。
- **开发前先导入对应子项目的 AGENTS.md**，按「开发目录选择」中的指引操作。
- **多仓库独立 Git**。项目根目录、`vllm/`、`vllm-ascend/`、`benchmark/` 各自是独立的 Git 仓库，Git 操作需进入对应目录执行。
- **Python 环境动态选择**。需要 Python 环境的工作区脚本优先使用目标 conda 环境；目标 conda 环境不可用时，使用当前可用的系统 Python。
- **脚本公共函数复用**。维护工作区 Bash 脚本时，优先复用 `scripts/lib/common.sh` 中的函数；模板脚本和本机私有 `scripts/server.sh` 脚本按需保持独立。
- **Python 环境结论记忆**。Agent 在本工作区完成 Python 环境判断后，应将稳定结论记录到可用记忆中，避免后续重复判断；记录内容包括工作区路径、判断日期、目标 conda 环境是否可用、实际 Python 路径、关键依赖检查结果和 fallback 原因。不得记录 token、API key、代理凭据等敏感信息；复用记忆前需做低成本校验，防止环境变化导致结论过期。
- **共享 Skill 单源在 `.agents/skills/`**。`.claude/skills` 仅作为指向 `../.agents/skills` 的兼容入口；新增或修改工作区共享 Skill 时只改 `.agents/skills/`，不要复制到 `.claude/skills`。
