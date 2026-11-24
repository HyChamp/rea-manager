# IDA Pro 接入 REA 完整指南

## 架构关系（先理解再动手）

REA 不直接驱动 IDA。它复用上游 `mrexodia/ida-pro-mcp` 桥接服务，把该服务暴露的能力包装成自己的原生分析工具。

- REA 负责：选择引擎、调度、回收带证据的结论、身份校验、生命周期管理。
- 上游 ida-pro-mcp 负责：与 IDA 通信、执行反编译/反汇编。
- IDA + Hex-Rays 负责：真正的反编译能力（用户已自备）。

REA 不安装、不升级、不激活、不授权 IDA/Hex-Rays/Python/上游 MCP，也不捆绑另一套 IDA server。这些必须事先按上游仓库说明装好。

版本要求：IDA 适配器需要包含该能力的 REA 版本；仓库 main 分支可能领先于 npm 发布版。若 `rea doctor` 不识别 ida 引擎，先 `npm update -g rea-agents` 再 `rea update`。

## 两种生命周期模式（二选一）

### 模式 A：GUI 附加（附加到已打开的 IDA 数据库）

适合交互式分析。支持上游 1.4.0 的 legacy 工具（`get_metadata`、`list_functions`、`decompile_function` 等）。

1. 在 IDA 里打开**原始输入二进制**（不是 `.idb` / `.i64`）。
2. 按上游说明启动 ida-pro-mcp 服务（GUI 侧通过 stdio 代理桥接，`/mcp` 是内部 RPC 端点，不要当 Streamable HTTP 直接连）。
3. 把上游注册里的 `command` / `args` / 可选 `env` 原样写成 JSON 文件（可用 `configure_providers.py --emit-ida-config` 取模板）：

```json
{
  "command": "python",
  "args": [
    "/absolute/path/to/ida_pro_mcp/server.py",
    "--ida-rpc",
    "http://127.0.0.1:13337"
  ]
}
```

  注意：`server.py` 路径和参数必须与你正在用的上游注册**完全一致**，不要手工改写。已有含 `mcpServers.ida-pro-mcp` 条目的配置文件也可直接被接受。

4. 设置环境变量（CLI 用 `--provider ida` 或 MCP 用 `open_binary.provider_id` 也可选引擎）：

```bash
export REA_IDA_MCP_CONFIG=/absolute/path/to/ida-mcp.json
export REA_ANALYSIS_PROVIDER=ida
```

Windows PowerShell：
```powershell
$env:REA_IDA_MCP_CONFIG = 'D:\analysis\ida-mcp.json'
$env:REA_ANALYSIS_PROVIDER = 'ida'
```

5. 跑第一条分析命令：

```bash
rea function /absolute/path/to/program.exe main --json
rea decompile /absolute/path/to/program.exe main --json
```

### 模式 B：headless（无人值守，推荐批量/服务端）

需要上游的 database supervisor API（`idb_open` / `idb_list` / `idb_close`、显式数据库参数、现代分析工具）。旧版 1.4.0 的 `idalib-mcp`（只接受一个初始文件名、跑 SSE）**不支持**此模式。

注册 JSON（可用 `configure_providers.py --emit-ida-headless` 取模板）：

```json
{
  "command": "G:/IDA94ProBable_CN/python311/Scripts/idalib-mcp.exe",
  "args": ["--stdio", "--max-workers", "1"],
  "mode": "headless",
  "timeoutMs": 300000,
  "env": {"IDADIR": "G:/IDA94ProBable_CN"}
}
```

- **`command` 写绝对路径**，指向脚本挑好的桥接。三种可用形态（`install_ida.py` 按顺序挑第一个能用的）：
  1. IDA 自带环境里已装的 `idalib-mcp.exe`：`<IDADIR>\python3xx\Scripts\idalib-mcp.exe`
  2. 用 IDA 自带 Python 跑模块：`command = <IDADIR>\python3xx\python.exe`，`args = ["-m", "ida_pro_mcp.idalib_supervisor", "--stdio", "--max-workers", "1"]`
  3. 脚本 venv / PATH 上的 `idalib-mcp`
- **工具清单必须含 database-supervisor 三件套**：`idb_open` / `idb_list` / `idb_close`。缺 `idb_close` 时 REA 会在真正分析那一刻报 `headless compatibility profile is missing tools: idb_close` —— `--help` 完全看不出来。自检：
  ```bash
  "<IDADIR>/python3xx/python.exe" -c "import ida_pro_mcp.idalib_supervisor as s; print([n for n in dir(s) if n.startswith('idb_')])"
  ```
  缺就从上游 main 重装：`python -m pip install --force-reinstall --no-deps https://github.com/mrexodia/ida-pro-mcp/archive/refs/heads/main.zip`。
  注意：这个包的 version 号长期写 `2.0.0`，**版本号相同不代表内容相同**，只能按工具清单判断新旧。
- **必须跑在 IDA 自带的那套 Python 上**。`idalib.dll` 与 `ida_*.pyd` 按 IDA 内嵌 Python 的 ABI 编译（9.4 = 3.11）；用系统 3.12/3.13 建 venv 会在 `import idapro` 时报 DLL/ABI 不匹配。判断方法：`python -c "import idapro; print(idapro.get_library_version())"`。
- 写 `"command": "idalib-mcp"` 依赖 PATH；写 `["uv", "run", "idalib-mcp"]` 要求包在 uv 项目内，裸跑会 `Failed to spawn` —— 这两种是实际踩过的坑。
- 若你的 idalib 安装需要，加 `"env": {"IDADIR": "/abs/path/to/ida"}`（`install_ida.py` 会自动写入）。
- 也可用本地 Streamable HTTP supervisor：`{"url": "http://127.0.0.1:8745/mcp", "mode": "headless"}`。
- headless 的 REA 进程与 supervisor 必须**共享主机文件系统和原生路径语法**，路径要写绝对路径。
- 打开目标时 REA 会把二进制复制进私有临时目录、核对摘要、请求一个独占 worker（`force_headless`），一个 REA 会话只拥有一个数据库。

## 身份校验（最常见的坑）

- 必须把**原始输入二进制**传给 REA，不能是 `.idb` / `.i64`。其 SHA-256 必须与 IDA 记录的输入哈希一致。
- 换分析目标前先让 REA 关闭当前连接，避免身份校验冲突。
- REA 会在每次分析请求前校验附加身份和 image base；不匹配或切换目标会失败并给出恢复指引。
- 关闭操作只释放 MCP 连接/proxy，**不会保存或关闭**你已有的 GUI 数据库；外部编辑实时可见（结果是活查询，不是快照重放）。

## 超时与稳定性

- 注册 JSON 的 `timeoutMs`（整数 1–2147483647）默认 300000 ms（5 分钟）。大目标要调高，同时把 Agent 侧 MCP 请求超时也调高。
- 该值被 REA 在下次连接时重新读取；已打开的会话保持连接时的旧值。改 `REA_IDA_MCP_CONFIG` 路径本身只在进程启动时读取。
- headless worker 刚打开时可能短暂 `status: busy`；REA 每 100ms 重读健康状态，直到应答/取消/`timeoutMs` 用尽。用尽返回可重试的 `provider_timeout`。
- 超时或中断后，REA 会保留私有工作区（无法证明请求已停止），报告 `incomplete cleanup`——不要误以为它静默删除了文件。
- `binary_session` 命令可查看实际可用工具；`rea doctor` / `rea setup` 在注册已可用时无需反复跑。

## MCP 会话标准四步（Agent 侧）

```json
{"name":"open_binary","arguments":{"path":"/abs/path/program","provider_id":"ida"}}
{"name":"analyze_function","arguments":{"procedure":"main"}}
{"name":"search_strings","arguments":{"pattern":"license"}}
{"name":"close_binary","arguments":{}}
```

`analyze_function` 默认只回摘要（保留 dossier）；需要 dossier 本身内联返回时加 `"detail":"complete"`。
