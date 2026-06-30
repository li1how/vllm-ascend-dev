---
name: vllm-ascend-pr
description: 在 vllm-ascend-dev 工作区中按当前 vllm-ascend 仓库规则完成开发者式 PR 流程。用于用户要求创建/重命名分支、准备提交、生成或修正 commit message、执行提交前检查、push 到 fork、创建 draft PR、填写 PR title/body、处理本机 git/gh 凭据失败或 GitHub connector fallback 的场景。
---

# vLLM Ascend PR 流程 Skill

## 目标

在 `vllm-ascend-dev` 工作区内，像正常 vLLM Ascend 开发者一样准备和发布 PR：动态读取当前仓库规则、检查当前环境、选择合适本地验证、提交带 sign-off 的 commit、推送 fork，并创建 draft PR。

## 基本约束

- 执行时必须动态发现规则和环境：先读取当前仓库规则文件，再检查当前机器上的命令、hooks、remote、凭据和环境等；只基于本次检查结果行动。
- 只在 `vllm-ascend/` 子仓执行 vLLM Ascend 代码仓 Git 操作。
- 不使用 `--no-verify` 绕过 hook。
- 不默认 `git add -A`。工作区有混杂改动时，先确认本次 PR 文件范围。
- 不伪造检查结果。未运行的检查必须写明原因，不能写成已通过。
- 不在 PR body 写 `CI passed`，除非用户明确确认或已查询到 CI 通过。
- 用户明确指定 branch、commit message、PR title 或 PR body 时优先采用；仍需检查是否满足当前仓库规则。

## 每次执行前读取当前规则

先读取工作区入口和仓库规则，再做 Git 操作：

1. 在工作区根目录读取 `README.md` 和 `AGENTS.md`，确认多仓库布局、目标目录和环境说明。
2. 进入 `vllm-ascend/` 后读取 `AGENTS.md`。
3. 读取当前贡献和 PR 规则源文件，至少包括：
   - `docs/source/developer_guide/contribution/index.md`
   - `docs/source/developer_guide/contribution/testing.md`
   - `docs/source/developer_guide/contribution/doc_writing.md`
   - `.github/PULL_REQUEST_TEMPLATE.md`
   - `.github/workflows/pr_test.yaml`
   - `.github/TEST_README.md`
   - `.pre-commit-config.yaml`
   - `format.sh`
   - `DCO`
4. 读取当前环境中的 `.git/hooks/pre-commit` 和 `.git/hooks/commit-msg`，如果文件存在且可读。
5. 把 `docs/source/developer_guide/**/*.md` 视为开发者文档源。用 `rg --files docs/source/developer_guide` 建立清单；根据改动类型继续读取相关文档。涉及 docs、测试、CI、模型教程、性能、调试、设计文档时，读取对应文档后再决定检查项。

如果上述文件不存在或路径变化，不要沿用旧记忆；用 `rg` / `find` 重新发现当前等价文件，并说明发现结果。

## 动态环境检查

提交前检查当前环境，不假设固定路径：

- 命令可用性：`git`、`python3` 或 `python`、`conda`、`pre-commit`、`gh`。
- Git 状态：仓库根、当前分支、upstream、remote、`user.name`、`user.email`、工作区状态、staged/unstaged/untracked 文件。
- Hook 状态：`.git/hooks/pre-commit`、`.git/hooks/commit-msg` 是否存在、是否可执行、实际调用哪个配置和解释器；如果 hook 写了绝对解释器路径，先检查该路径是否可执行，不要假设一定存在。
- 环境说明：从当前 `README.md` / `AGENTS.md` / 子仓文档读取 conda 或依赖说明；需要激活环境时按当前说明执行。找不到环境或依赖时，选择仍可运行的检查并记录缺口。
- 网络和凭据：需要 `fetch`、`push`、`gh` 或 connector 时，先预期可能需要网络/认证权限；失败时保留原始错误要点并说明影响。

## 分支与提交流程

1. 检查 diff。
   - 用 `git status -sb`、`git diff --name-status`、`git diff --cached --name-status` 确认范围。
   - 未跟踪文件只在用户明确纳入 PR 时添加。
2. 更新 base。
   - 默认以当前远端主线为 base。通常从当前 remote/default 或 `origin/main` 获取；若仓库规则或用户要求指定其他 base，以当前规则或用户要求为准。
   - 网络受限时说明无法刷新 base；不要假装已基于最新远端。
3. 创建或整理分支。
   - 用户给定分支名时直接使用并验证格式。
   - 未给定时，从当前 PR 类型、改动范围和近期本仓分支/提交风格推导小写 slash 分支名，例如 `<type>/<slug>`。
   - 避免在主分支上直接提交。
4. 生成 commit message。
   - 从当前贡献文档、PR title 校验规则和近期 `git log` 判断格式。
   - 如果文档和历史风格冲突，优先满足 CI 硬校验和当前维护者实际使用风格，并在需要时说明取舍。
   - message 必须清晰具体，避免 `update code`、`fix bug` 之类泛化描述。
5. Stage 目标文件。
   - 用明确路径 `git add <paths>`。
   - 如果验证工具改了文件，重新查看 diff，再决定是否纳入同一 commit。
6. Commit。
   - 使用 `git commit -s`，或确认最终 commit message 中有当前 `git config user.name` / `user.email` 对应的 `Signed-off-by`。
   - 让 pre-commit 和 commit-msg hook 正常运行。hook 失败时先修复或记录真实阻塞原因，不用 `--no-verify`。

## 提交前检查策略

总是先跑：

```bash
git diff --check
```

然后按当前仓库规则选择检查：

- 先运行 `bash format.sh ci`（普通本地 lint 可用 `bash format.sh`）。它只是 `pre-commit` 包装器，覆盖 ruff、拼写、clang-format、markdownlint（ci/manual）、actionlint、shellcheck、PNG、文件名、Python 包结构和若干本地代码规范检查；不覆盖 commit-msg sign-off。
- 不把 `format.sh` 当完整 CI。`mypy`、test coverage 配置校验、test selection、UT/E2E、docs build、doctest、linkcheck 都需要按改动类型单独判断。
- 代码 / 测试改动：参考 `.github/TEST_README.md`、`testing.md` 和 `select_tests.py`，能本地跑的 CPU UT 或脚本单测优先跑；需要 NPU、模型或 CI runner 的测试写明交给 CI。
- CI / workflow / test selection 改动：补跑相关脚本或单测，例如 `coverage.py`、`select_tests.py`、`test_select_tests.py`。
- 文档改动：`format.sh ci` 只覆盖 markdownlint；涉及结构、示例或链接时，再考虑 `make -C docs html`、doctest 或 linkcheck。

## Push 与 Draft PR

默认本机命令优先：

1. 推送到当前 fork remote，通常是 `self`；不要误推 upstream，除非当前仓库配置显示它就是用户 fork 或用户明确要求。
2. 优先用当前 `gh` 登录态创建 draft PR。
3. PR title 和 body 必须来自当前规则源文件：
   - title 满足当前 workflow 的 title 校验。
   - body 按当前 `.github/PULL_REQUEST_TEMPLATE.md` 填写，不保留模板注释。
   - 文档-only PR 的用户可见变更说明按当前模板解释处理。
4. 如果 HTTPS、SSH、`gh` 或网络失败：
   - 报告具体失败原因和是否已创建本地 commit。
   - 如果 GitHub connector 可用，作为 fallback 创建远端分支和 draft PR。
   - connector fallback 仍要使用同一 commit message、PR title/body 和目标 base。

## 最终汇报

完成后简洁列出：

- 分支名、base、fork remote、PR target。
- commit SHA 和 commit subject。
- PR URL；若未创建，说明阻塞点和可继续执行的命令。
- 已运行检查及结果。
- 未运行检查及原因。
- 是否存在未提交或无关工作区改动。
