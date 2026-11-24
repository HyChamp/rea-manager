# 安装与运行故障排查

## 安装阶段

| 现象 | 根因 | 处理 |
|---|---|---|
| `Node.js is unsupported` / 脚本直接退出 | Node 版本不在支持区间 | 需 22.x(≥22.23.4)/24.x(≥24.22.1)/≥26.12.1；**23、25 不支持**。`node -v` 核对，升 24 LTS |
| Windows 报 `无法加载 npm.ps1，因为禁止运行脚本` | PowerShell 默认 Restricted 策略 | 改用 CMD 或 Git Bash；或管理员 PowerShell 执行 `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser -Force` |
| `npx rea-agents setup` 报"找不到命令" | 老版 npm 6.x 的 npx 不兼容 | 升级 npm；或本地安装 + node 直调 `node <prefix>/node_modules/rea-agents/scripts/rea.mjs` |
| 装到临时目录后 MCP 失效 | MCP 注册指向安装路径，`%TEMP%` 被清理 | 装到固定目录（npm 全局默认位置即可） |
| `rea: command not found` | PATH 未刷新 | 关掉终端重开；仍不行检查 npm 全局 prefix 是否在 PATH |
| 国内下载卡住/超时 | 连 npm 官方源慢 | `npm config set registry https://registry.npmmirror.com` 后重装 |
| 装完新版本后 IDA/Android 引擎仍不识别 | 注册锁在执行 setup 那一刻的版本 | `rea update` 后**重跑一次 setup** 刷新注册，再重开 Agent |
| 提示冒牌包/仓库 | 非官方来源 | 只认 `morluto/rea`、`rea.tools`、npm `rea-agents` |
| 下载报"校验失败"，重跑仍然失败 | 上一次留下的文件长度对、内容坏 | 本版会**自动删除**坏文件后重下；若仍反复失败，删掉 `~/.rea-tools` 下对应文件再跑 |
| 上一步说装好了，下一步又问"要不要装" | 旧版脚本之间不共享 PATH | 本版用 `~/.rea-tools/env.json` 共享；异常时可删该文件让脚本重新探测 |
| 报错只剩 `TypeError` 或一堆乱码 | 旧版 `text=True` 解不了中文 Windows 的 GBK 输出 | 本版已统一二进制读取 + 多编码回退；仍有乱码请把命令原文贴出来 |

## IDA 相关

| 现象 | 根因 | 处理 |
|---|---|---|
| `rea doctor` 看不到 ida 引擎 | REA 版本不含适配器，或没设环境变量 | 更新 REA；设 `REA_IDA_MCP_CONFIG` 与 `REA_ANALYSIS_PROVIDER=ida`；或直接跑 `install_ida.py` 一键配置 |
| `install_ida.py` 报"未探测到 IDA 目录" | IDA 装在非标准位置 | 用 `--ida-dir "你的IDA安装目录"` 手动指定一次（含 `ida.exe` 那层） |
| 装了桥接但分析时找不到工具 | 装错成 PyPI 的 `ida-mcp`（另一个项目） | 必须从 GitHub 装：`https://github.com/mrexodia/ida-pro-mcp/archive/refs/heads/main.zip`，脚本已自动处理 |
| 注册 JSON 写了 `uv run idalib-mcp`，启动失败 | 该形式要求包在 uv 项目内 | 重跑 `install_ida.py`，本版会写 venv 内 `idalib-mcp` 的绝对路径 |
| idalib 激活失败 | IDA 版本过低（<9.x 无 idalib）或路径不对 | 确认 IDA Pro 9.x；检查 `<IDA>/idalib/python/py-activate-idalib.py` 存在 |
| `import idapro` 报 DLL/ABI 错误、`Module use of python3xx.dll conflicts` | 用了系统 Python，而 idalib 是按 IDA 内嵌 Python 编的 | 改用 `<IDADIR>\python3xx\python.exe`；本版脚本会自动探测并优先用它 |
| 绿色版 IDA 不在 Program Files，脚本探测不到 | 只扫了常规安装位置 | 本版会扫各盘符根目录**及一层子目录**的 `*IDA*`；仍找不到就 `--ida-dir` 一次性指定 |
| 搬了 IDA 目录后 `idalib-mcp.exe` 直接退出码 1、无输出 | pip 的 `.exe` 启动器把 python 绝对路径写死在文件里 | 跑一次 `install_ida.py`：会改用 `python -m` 注册并重建启动器 |
| 搬目录后 `doctor.py` 显示 IDA=否 | 注册 JSON / ida-config 里仍是旧路径 | 同上，`install_ida.py --persist` 一次改完 |
| 分析时报 `headless compatibility profile is missing tools: idb_close` | 桥接是旧快照（version 号可能仍写 2.0.0，但不含 database-supervisor 全套工具） | 重跑 `install_ida.py`：它会探测工具清单并从上游 main 升级（先备份旧包）。手工等价命令：`"<IDADIR>\python3xx\python.exe" -m pip install --force-reinstall --no-deps https://github.com/mrexodia/ida-pro-mcp/archive/refs/heads/main.zip` |
| 身份校验失败 / hash 不匹配 | 传了 `.idb`/`.i64`，或目标被切换 | 传**原始二进制**；换目标前先 `close_binary` |
| `provider_timeout` | 大目标分析超时 | 调高注册 JSON 的 `timeoutMs`（默认 300000）和 Agent 侧请求超时 |
| 刚打开 headless worker 报 busy | 自动分析尚未完成 | 正常现象，REA 每 100ms 重读健康状态等待 |
| `incomplete cleanup` | 超时/中断后无法证明 worker 已停 | REA 保留私有工作区待重试，属安全行为，检查上游会话清单后再手动清理 |

## Android 相关

| 现象 | 根因 | 处理 |
|---|---|---|
| 报缺 `jdk.compiler` | 装的是纯 JRE | 装完整 JDK 17+（推荐 21），`java --list-modules` 确认含 `jdk.compiler` |
| `config_drift` 说 JAR 取不到 | 上游 v1.x 改名，或指错路径 | 用本文钉死的 0.7.1 地址，校验 SHA-256；设 `REA_JADX_MCP_JAR` 绝对路径 |
| Windows 报 package prerequisite | REA 包不含 Windows 原生 controls，或 arm64 | 更新到较新 REA 包；Windows arm64 不支持 |
| `command not found` on Linux 的 JADX 跟踪 | 缺 procps 的 `ps` | 装发行版 `procps` / `procps-ng` 包 |
| 设了变量但引擎仍不可用 | 在别的终端 export，MCP server 是已运行进程 | 在启动 MCP server 的同一个 shell 设置；持久化后重开 Agent |
| 大 APK 内存不足 | JVM 堆不够 | `REA_JADX_HEAP_MIB=8192`（Unix）/ `$env:REA_JADX_HEAP_MIB='8192'`（Windows） |

## 上下文与输出膨胀

| 现象 | 处理 |
|---|---|
| Agent 一上来就卡、系统提示被截断 | MCP 全量加载 200+ 工具定义。改用本 skill 的 CLI 路线；或在客户端开 Tool Search / 延迟加载 |
| 分析一次把上下文吃满 | 不要整坨 `cat --json` 结果。落盘到文件后用 `jq`/`rg`/`head` 取样；先概览后深挖；一次只问一个功能点 |
| 开了 `hot-cold` 却几乎什么工具都调不到 | **实测**：`hot-cold` 启动时只加载一小批"热"工具（本机只有 4 个：`binary_session`/`open_binary`/`close_binary`/`get_evidence_bundle`）。`analyze_function` 等在服务端是 `available`，但客户端目录不刷新就调不到。要用分析工具：`toggle.py on --auto --profile full` + 重启 |

## MCP 开关与证据导出

| 现象 | 根因 | 处理 |
|---|---|---|
| `mcp.json` 里明明有 `rea`，客户端就是没工具 | 客户端不自动信任新写入的服务 | 「连接器管理 → 自定义连接器」找到 `rea` 点**信任**，再重启客户端 |
| 改了 `mcp.json` 但当前会话毫无变化 | 注册只在客户端**启动时**读取 | 必须重启；`on/off` 不可能"即时生效" |
| 导出的证据包只有 125 字节、`records: 0` | 先 `close_binary` 再导出：会话记录已释放，只剩下空骨架 | 顺序固定为 **open → analyze → export → close** |
| `analyze_function` 返回的是视图不是完整 dossier | 默认 `detail: summary` 返回 projection，完整记录在 `parent_evidence_id` 下 | 要全量就 `detail: complete`；要留证就连父记录一起导出 |
| 注册里残留失效路径（如 `GHIDRA_INSTALL_DIR` 指向已删目录） | `rea setup` 只合并不删除旧键 | 重跑 `toggle.py on`：会先净化环境变量，再剔除注册里的死路径 |

## 通用排查顺序

1. `python scripts/doctor.py` —— 看平台/Node/JDK/JAR/IDA 状态。
2. `python scripts/verify.py` —— 看**可用性**：桥接能否启动、JAR 摘要是否匹配、可选冒烟是否通过。
3. `rea --version` 与 `rea --help` —— 确认 CLI 本身可用。
4. `rea doctor --json` —— 官方引擎状态基线。
5. 对照上表定位，改完配置后**重开终端/Agent**再验证。
6. 仍失败时，把报错原文（不要转述）交给 Agent，并附上 `doctor.py` 的 JSON 输出。
