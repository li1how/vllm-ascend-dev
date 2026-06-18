# vLLM Ascend 开发工作区

vLLM Ascend 多仓库开发工作区。

[README.md](README.md) 包含工作区目录结构、快速开始、脚本速查、环境变量与 Conda 环境说明；所有操作前先读取 README，确认当前工作区布局和环境约束。

## 开发目录选择

**默认以 `vllm-ascend/` 为目标目录**，`vllm/` 偶尔开发。

- 任务涉及 Ascend 插件 → `vllm-ascend/`，开发前先导入 [AGENTS.md](vllm-ascend/AGENTS.md)
- 任务明确涉及 vLLM 上游源码 → `vllm/`，开发前先导入 [AGENTS.md](vllm/AGENTS.md)
- 不确定属于哪个目录 → 主动询问用户

## 环境

环境变量、Conda 环境详见已导入的 README。以下为补充说明：

- 网络 / SSL：公司代理有自签证书，正常情况系统已配置；部分 Python 包（如 certifi）自带 CA bundle，需设置 `SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt`

## 约束

- **不确定目录归属先询问**。跨仓库改动时，先确认目标目录再动手。
- **开发前先导入对应子项目的 AGENTS.md**，按「开发目录选择」中的指引操作。
- **多仓库独立 Git**。项目根目录、`vllm/`、`vllm-ascend/`、`benchmark/` 各自是独立的 Git 仓库，Git 操作需进入对应目录执行。
- **统一使用 conda 管理 Python 环境**，不可用系统 pip。子仓库（如 `vllm/`）的 `AGENTS.md` 中可能有自己的环境管理说明（如 `uv`），以本工作区约束为准，全部走 conda。
- **共享 Skill 单源在 `.agents/skills/`**。`.claude/skills` 仅作为指向 `../.agents/skills` 的兼容入口；新增或修改工作区共享 Skill 时只改 `.agents/skills/`，不要复制到 `.claude/skills`。
