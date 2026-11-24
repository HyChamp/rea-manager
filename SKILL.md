---
name: rea
description: REA（Reverse Engineer Anything）的管理器：装环境、切换"在不在场"、按需分析二进制/APK。默认零常驻（走 CLI 全套命令），只有 /rea on 才把工具常驻进客户端。仅当用户显式输入 /rea 或点名 rea 技能时执行，模型不得自动触发。
agent_created: true
disable-model-invocation: true
user-invocable: true
---

# REA 管理器

装环境 → 查状态 → 按需开关 → 分析 → 留证。**默认什么都不常驻**，需要时才开。

```
REA · 不带参数 = 看状态

  status [--deep] [--verbose]   现在开没开 / 引擎扫到哪些 / 建议开哪些族
  on                           打开（= on --auto --profile full）
  on --profile hot-cold        显式选择轻量档
  off                          关掉（移除注册，自动备份 mcp.json）
  verify [--binary X]          真跑一次，验证能不能分析
  doctor                       官方引擎清单（rea doctor）
  skill                        装两个 skill（官方说明书 + 管理器自身，只写 skills 目录）
  install                      装/修环境（Node、JDK、CLI、IDA、JADX）
  help                         这张表
```

## 两种模式

| | 默认：**零常驻** | `on`：**常驻加速** |
|---|---|---|
| 工具目录 | 不注册 REA MCP | `full` 暴露自动选中的工具族，数量随本机环境变化 |
| 分析入口 | 显式指定任务后走 CLI | 重启后用已加载的 MCP 工具；CLI 仍可用 |
| 同一大文件连问 | 每次重开数据库，慢 | 复用引擎，快 |
| 切换代价 | 无需重启 | **必须重启客户端** |

**默认走 CLI**。`on` 只在"同一文件连续深挖"时值得。

## 三条硬约束（说错就是误导用户）

1. **注册由客户端启动时读取**：`on`/`off` 改完必须重启，当前会话不变。
2. **新服务要在「连接器管理 → 自定义连接器」点一次信任**，否则重启也不加载。
3. **`hot-cold` 只加载 4 个热工具**（`binary_session`/`open_binary`/`close_binary`/`get_evidence_bundle`）；要真正分析必须 `full`。

## 固定版本（稳定优先，不追最新）

| 组件 | 固定值 | 说明 |
|---|---|---|
| Node | `v24.22.3` | 其他兼容版本也不代替它；复用目标版本，否则下载到用户目录，不卸载系统 Node。`REA_NODE_VERSION=latest` 才改用最新 24.x |
| Python（安装器自身运行时） | `3.12.10` embeddable | 无系统 Python 时 bootstrap-python.ps1 自动下载（MD5 校验）；只用于跑安装脚本，不进 PATH |
| rea-agents CLI | `6.10.1` | `install_rea.py` 的 `REA_PIN`；不一致自动重装 |
| ida-pro-mcp | 2.0.0（内置 `vendor/ida_pro_mcp`） | 离线拷贝，不走网络 |
| ida-pro-mcp 兜底 | commit `13e480cb…`（不可变） | `install_ida.py` 的 `MCP_COMMIT` |
| JADX 桥接 | 0.7.1 + SHA-256 | `install_android.py` |
| JDK | 复用完整 **21**.x；没有才下载 Temurin 21 | 主版本固定，补丁不锁 |

脚本会自己处理：任意盘/子目录的绿色版 IDA、IDA 搬家与 ABI 不匹配、桥接缺 `idb_close`（离线覆盖）、Node/CLI 版本漂移、注册里的失效路径。

## 什么时候提醒 `/rea on`

开始新目标前跑 `python scripts/advise.py <路径> [--calls N] [--last-seconds S]`，退出码 1 = 建议常驻。

| 触发 | 阈值 | 依据 |
|---|---|---|
| 文件大 | ≥ 20 MB | kernel32 0.7 MB 单次已 15–20 秒 |
| 单次慢 | ≥ 60 秒 | 已在为"不复用引擎"付钱 |
| 反复问 | 同一目标第 3 次 | 累计时间超过切换成本 |

命中就**一句话**提醒；用户不回应继续 CLI，同目标只提醒一次。

## 装环境（换设备 / 环境坏了才走）

Windows：完整解压 ZIP，双击 `install-windows.cmd`。入口先探测系统 Python（含 REA_PYTHON / py.exe / 常见安装路径），找不到时**自动下载官方便携版 Python 3.12.10**（用户目录、免管理员、MD5 校验、断点续传）再继续。全流程唯一需要用户自备的是 IDA Pro 本体。

汇总区分完成/跳过/失败：退出码 `0` 无失败，`1` 必需步骤或验证失败，`2` 可选组件部分失败。没装 IDA 本体记"跳过"，不影响 CLI。

```bash
python scripts/install_all.py            # Node→JDK→CLI→IDA→JADX→verify→两个 skill
python scripts/install_all.py --no-skill # 不要任何 skill
python scripts/install_all.py --no-manager-skill # 官方说明书照装，跳过管理器 skill
```

**IDA 本体必须自备**（商业软件）；Ghidra 可作免费替代（`GHIDRA_INSTALL_DIR` 指到含 `Ghidra/application.properties` 的那层）。

### Agent 代装（用户把 ZIP 交给 AI 助手执行时，严格按此顺序）

1. **完整解压 ZIP** 到任意目录（不要在压缩包内直接执行）。
2. **探测系统 Python**：`py -3 --version` 或 `python --version`，且满足 3.10+。
3. **探测不到就先跑引导**（PowerShell，Windows 自带，无需管理员）：
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File <解压目录>\rea\scripts\bootstrap-python.ps1
   ```
   成功时脚本**最后一行输出**便携版 python.exe 的绝对路径（记为 $PY）；失败时输出 BOOTSTRAP_FAILED，按 references/troubleshooting.md 处理。
4. **用 $PY 跑总安装**（不要依赖 PATH 里的 python）：
   ```powershell
   & "$PY" <解压目录>\rea\scripts\install_all.py
   ```
   install_all 内部会自动 apply 工具链 PATH 并依次装 Node→JDK→CLI→IDA→JADX→verify→两个 skill。
5. **汇报汇总**：Node/JDK/REA CLI 必须为"完成"；IDA 为"跳过"属正常（本体需自备）。失败项按 troubleshooting.md 重试；下载类支持断点续传，重跑同一脚本即可接着下。
6. **最后提醒用户重启客户端**——两个 skill 与 /rea on 的 MCP 注册都要重启才生效。

注意：Agent 在 skills 目录副本内运行本 skill 时，install_skill.py 会自动识别"源即目标"并跳过自拷贝；vendor 随技能目录携带，IDA 桥接离线可用。

## 分析时的省上下文纪律

1. 先概览再深挖，别一上来就 `decompile_all`
2. 一律 `--json`，用 `jq`/`rg`/`head` 只取字段，不 `cat` 大文件
3. 大结果落盘，按需取样
4. 一次只问一个功能点

## 三个高频坑

| 现象 | 处理 |
|---|---|
| `mcp.json` 有 rea 但客户端没工具 | 去连接器管理点**信任**，再重启 |
| 导出证据只有 125 字节 / `records:0` | 先 `close_binary` 了；顺序必须是 **open → analyze → export → close** |
| CLI 有 `decompile`，MCP 无同名工具 | 名字不对等：MCP 走 `analyze_function` 的 pseudocode facet |

## 文件地图

- `scripts/toggle.py` — 开关：`status`/`on`/`off`，含引擎扫描与"未接入"检测
- `scripts/advise.py` — 判断该继续 CLI 还是该 `on`
- `scripts/install_*.py` — 各组件安装（幂等、版本固定）
- `scripts/install_skill.py` — 两个 skill 一起装：官方说明书（reverse-engineer-anything）+ 管理器自身（rea）；覆盖旧版前自动备份；只写 skills 目录，不动 MCP 注册
- `scripts/doctor.py` / `verify.py` — 体检 / 端到端验证
- `vendor/ida_pro_mcp/` — 内置桥接包（离线、版本确定）
- `references/` — IDA 对接、Android 分析、故障速查
