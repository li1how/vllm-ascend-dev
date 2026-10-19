# 缓存复制

使用 `scripts/cache-transfer.py` 在目标容器中执行 `inspect`、`export`、`restore`。
SSH 与容器入口沿用 remote-execution 的工具，使用 `devcontainer exec` 带入代理。
Python 沿用工作区环境策略；先 source `scripts/lib/common.sh` 并调用
`ws_select_python_env vllm-ascend-dev`，后续使用 `$PYTHON_BIN`。不为缓存复制建任务卡。

## 来源与目录

从本次指定或已经核验的节点导出缓存，记录实际 image ID、SOC 和来源主机。
源码从 Git 获取，私有配置、权重、凭据不随缓存复制。共享盘只提供快照中转目录；
源缓存和恢复后的目标缓存位于各自节点本地工作区。先在宿主机用 `findmnt -T`
核实工作区及缓存的文件系统。不要仅按 `/home` 或 `/mnt` 名称判断。

`--workspace`、`--cache-dir` 必填；`export/restore` 另需 `--snapshot-dir`。
路径均是执行此工具的环境中实际可见的路径，不能混用宿主机与容器路径。
导出、恢复输出 JSON，并在 `log/cache-transfer/` 保存数量、大小、来源、校验结果
及耗时、快照路径和退出码；失败也保存记录，`inspect` 只读。快照目录需每次
使用新路径，且不能与缓存或待复制的构建现场重叠；目标已有缓存条目保留。

```bash
cache_tool=.agents/skills/remote-init/scripts/cache-transfer.py
cache_dir=/workspaces/vllm-ascend-dev/.cache/vllm-ascend/csrc-build-cache
snapshot_dir="/mnt/share/l00813495/build-cache/${SNAPSHOT_ID:?先设置本次快照ID}"

# 在来源容器执行；旧缓存位于其他目录时明确替换 cache_dir
"$PYTHON_BIN" "$cache_tool" inspect --workspace /workspaces/vllm-ascend-dev \
  --cache-dir "$cache_dir"
"$PYTHON_BIN" "$cache_tool" export --workspace /workspaces/vllm-ascend-dev \
  --cache-dir "$cache_dir" --snapshot-dir "$snapshot_dir" \
  --host "$SOURCE_HOST" --image-id "$IMAGE_ID" --soc "$SOC_VERSION"

# 在目标容器执行，必须能访问同一中转快照
"$PYTHON_BIN" "$cache_tool" restore --workspace /workspaces/vllm-ascend-dev \
  --cache-dir "$cache_dir" --snapshot-dir "$snapshot_dir" \
  --image-id "$IMAGE_ID" --soc "$SOC_VERSION"
./scripts/install-vllm-source.sh --skip-uninstall --build-cache-dir "$cache_dir" --jobs 32
```

镜像由本次选定容器的 Docker inspect 获取真实 ID。未指定镜像/SOC 时仍可复制
有效增量条目，但不能据此证明构建现场可复用。源码 SHA 用于追踪来源，不要求
整个 SHA 相同；编译输入变化由已有 action-cache 引擎逐项判定。CPU 架构、OS/libc
不一致的快照拒绝恢复；其他编译环境与输入仍由引擎校验，不宣称复制即全部命中。

## 可选构建现场

两端都添加 `--include-build-state` 才尝试复制 `vllm-ascend/csrc/build` 和
`vllm-ascend/build`。来源构建必须已经结束，工具与远端 prepare/run 使用相同
checkout 锁，忙时失败，不中断任务。手工启动且不遵循该锁的进程需先核实结束。

安装脚本成功完成 Ascend 构建后记录 `log/last-native-build.json`；远端 prepare
会传入实际 image ID，直接安装时可设置 `VLLM_BUILD_IMAGE_ID`。导出核对该成功
构建记录与当前输入、环境；缺少记录、镜像/SOC、CANN 元数据或关键依赖时跳过
构建现场，仍导出有效增量条目。旧的手工构建不会被自动标为已验证来源。

恢复检查原生输入（含子模块）、镜像、Python、torch/torch_npu、CANN、编译器、
构建选项与容器内源码/缓存路径。失配时给出具体原因，只恢复兼容增量缓存。
目标已有构建目录保留，不替换或改写旧 CMake 路径。缓存失配不触发清理。

## 安装记录

先 vLLM 后 vLLM-Ascend，继续使用正常源码构建与原有依赖处理。安装脚本无参数
仍执行重装，正确 editable 下的纯 Python 改动通过 prepare 跳过安装并提示重启。

每次安装在 `log/install-source.*/` 保留 `ascend-build.log`、
`build-cache.events.jsonl` 和 `summary.json`，记录退出码、并发、组件耗时与
HIT/MISS/BYPASS。历史源码不支持缓存事件时 `cacheTelemetryAvailable=false`，
不能以零事件推断命中率。统计不包含模型加载和图捕获耗时，初始化成功也不等于
模型验证通过。
