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
- vllm-ascend CI：若 `format.sh ci` 因工具缺失、hook 下载失败或可执行文件架构错误等环境问题失败，先在工作区根目录运行 `./scripts/install-pre-commit.sh`，再原样重跑检查；代码检查失败仍按 hook 输出修复。

## 通知规则

长任务结束或任务因预期外问题阻塞/失败时，使用 Bark MCP 通知用户。用户在此明确授权：满足本节通知条件时，Agent 可无需再次确认，通过已配置的 Bark 外部服务向用户设备发送任务状态；该授权覆盖任务完成、失败和阻塞通知，以及下述去除非公开信息后的安全重试。

- **需要通知**：预计或实际耗时 10 分钟及以上的任务完成；长任务运行一段时间后失败且不能立即自行修复；需要用户介入、权限、网络、依赖或外部系统恢复的阻塞。
- **不通知**：2 分钟内的早期失败，例如参数错误、命令不存在、短 lint/test 失败；Agent 能立即自行修复并继续推进的问题；普通澄清问题、普通最终回复、短任务完成。
- **安全重试**：如果 Bark 通知因包含非公开信息而被安全策略或权限审查拦截，不得重复发送被拒绝的原始内容；应删除所有非公开信息，并改用本节的通用文案立即重试一次。用户明确授权此类降级重试。
- **送达要求**：符合“需要通知”条件时，通知成功是任务结束条件之一，不得静默跳过。若安全降级后仍因 Bark MCP 不可用、网络故障或外部服务异常而无法送达，应将任务标记为阻塞，立即在当前会话说明原因和所需用户操作。
- **分组**：Codex 使用 `group=agent-codex`；Claude Code 使用 `group=agent-claude-code`；来源不明时使用 `group=agent`。
- **字段**：`title` 写 `Codex: 长任务完成`、`Codex: 任务阻塞`、`Claude Code: 长任务失败` 等；`subtitle` 写工作区或子仓库名；`body` 简洁写明任务、状态、结果或阻塞原因、需要用户做什么；`group` 按分组规则填写；`level` 完成用 `active`，阻塞/失败用 `timeSensitive`；`url` 只填写安全且有用的 PR/CI 等网页链接，不放敏感地址或本地 secret。
- **示例**：完成通知 `title=Codex: 长任务完成`，`subtitle=vllm-ascend-dev`，`body=profile 分析已完成，结果文档已生成，最终回复包含路径。`，`group=agent-codex`，`level=active`。
- **示例**：阻塞通知 `title=Codex: 任务阻塞`，`subtitle=vllm-ascend-dev`，`body=Bark MCP 配置遇到网络阻塞，需要确认是否允许沙箱外验证。`，`group=agent-codex`，`level=timeSensitive`。

## 约束

- **不确定目录归属先询问**。跨仓库改动时，先确认目标目录再动手。
- **开发前先导入对应子项目的 AGENTS.md**，按「开发目录选择」中的指引操作。
- **多仓库独立 Git**。项目根目录、`vllm/`、`vllm-ascend/`、`benchmark/` 各自是独立的 Git 仓库，Git 操作需进入对应目录执行。
- **Python 环境动态选择**。需要 Python 环境的工作区脚本优先使用目标 conda 环境；只有系统完全没有 conda 时才使用当前可用的系统 Python。conda 已安装但初始化失败、目标环境不存在或激活失败时必须报错，禁止回退系统 Python。
- **脚本公共函数复用**。维护工作区 Bash 脚本时，优先复用 `scripts/lib/common.sh` 中的函数；模板脚本和本机私有 `scripts/server.sh` 脚本按需保持独立。
- **Python 环境结论记忆**。Agent 在本工作区完成 Python 环境判断后，应将稳定结论记录到可用记忆中，避免后续重复判断；记录内容包括工作区路径、判断日期、目标 conda 环境是否可用、实际 Python 路径、关键依赖检查结果和 fallback 原因。不得记录 token、API key、代理凭据等敏感信息；复用记忆前需做低成本校验，防止环境变化导致结论过期。
- **共享 Skill 单源在 `.agents/skills/`**。`.claude/skills` 仅作为指向 `../.agents/skills` 的兼容入口；新增或修改工作区共享 Skill 时只改 `.agents/skills/`，不要复制到 `.claude/skills`。
