#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""REA 的"在场开关"：控制客户端 MCP 注册在不在，从而控制上下文成本。

为什么需要它：
- 我们的 /rea skill 本身几乎不占上下文（disable-model-invocation），
  真正吃上下文的是**客户端里的 MCP 注册**（263 tools + 6 prompts，每个会话常驻）。
- 注册是客户端启动时读取的，所以 on/off 都**要重启客户端才生效**，这里只负责改配置并说清楚。

用法：
  python scripts/toggle.py status
  python scripts/toggle.py on                      # 默认 full，按本机能力选族
  python scripts/toggle.py on --families native,android --profile hot-cold
  python scripts/toggle.py off
  python scripts/toggle.py off --client codebuddy
"""
import argparse
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import apply_toolchain, jdk_has_compiler, run, sha256_file  # noqa: E402

JADX_SHA = "6e5eacf500b64292bfb73c49797c1958f6ee44646e43e868039ae7feb573ff75"
VALID_FAMILIES = [
    "direct", "enhanced", "native", "artifact", "managed", "firmware", "uefi",
    "android", "pcap", "flutter", "frida", "apktool", "jeb", "adb", "browser",
    "electron", "javascript-runtime", "application", "session",
]

# 只支持"一个 JSON 文件、mcpServers 是对象"的客户端；其它（如 codex 的 TOML）
# 交给 rea setup 自己处理，我们不去猜格式。
CLIENTS = {
    "workbuddy": os.path.join("~", ".workbuddy", "mcp.json"),
    "codebuddy": os.path.join("~", ".codebuddy", "mcp.json"),
    "cursor": os.path.join("~", ".cursor", "mcp.json"),
}


# 实测得到的每族工具数（用 REA_MCP_TOOL_FAMILIES=<族> rea mcp doctor 逐个量过，合计正好 263）
FAMILY_TOOLS = {
    "direct": 41, "enhanced": 15, "native": 40, "artifact": 27, "managed": 7,
    "firmware": 2, "uefi": 3, "android": 6, "pcap": 2, "flutter": 2,
    "frida": 9, "apktool": 2, "jeb": 4, "adb": 24, "browser": 12,
    "electron": 6, "javascript-runtime": 2, "application": 35, "session": 24,
}

BROWSER_EXES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Mozilla Firefox\firefox.exe",
]


def detect_capabilities():
    """探测本机到底有哪些引擎/工具，返回 {能力: 依据或 None}。"""
    caps = {}

    cfg = os.environ.get("REA_IDA_MCP_CONFIG") or os.path.expanduser(os.path.join("~", ".rea", "ida-mcp.json"))
    caps["ida"] = cfg if os.path.isfile(cfg) else None

    gh = os.environ.get("GHIDRA_INSTALL_DIR")
    caps["ghidra"] = gh if gh and os.path.isdir(gh) else None

    jar = os.environ.get("REA_JADX_MCP_JAR") or os.path.expanduser(
        os.path.join("~", "AppData", "Local", "rea", "android", "jadx-headless-mcp-0.7.1-all.jar"))
    caps["jadx"] = jar if os.path.isfile(jar) and sha256_file(jar).lower() == JADX_SHA else None
    caps["jdk"] = None
    if jdk_has_compiler()[0]:
        caps["jdk"] = jdk_has_compiler()[1]

    heap = os.environ.get("REA_HEAP_MIB")
    caps["heap"] = heap

    for tool in ("adb", "frida", "flutter", "dotnet", "mono", "tshark", "dumpcap",
                 "apktool", "jeb", "electron", "node", "binwalk", "uefitool"):
        caps[tool] = shutil.which(tool)

    env_apk = os.environ.get("REA_APKTOOL_PATH")
    if not caps["apktool"] and env_apk and os.path.isfile(env_apk):
        caps["apktool"] = env_apk

    caps["browser"] = next((p for p in BROWSER_EXES if os.path.isfile(p)), None)
    return caps


def auto_families():
    """按本机能力选族。返回 (picked, reasons, skipped)。

    原则：只选"能证明用得上"的族。CLI 的 204 个命令不受族限制，
    所以少选只影响 MCP 工具面，不会让 CLI 变瘸 —— 因此宁少勿多。
    """
    caps = detect_capabilities()
    picked, reasons, skipped = [], [], []

    def take(fam, cond, why_ok, why_no):
        if cond:
            picked.append(fam)
            reasons.append((fam, why_ok))
        else:
            skipped.append((fam, why_no))

    engine_native = caps["ida"] or caps["ghidra"]
    take("native", bool(engine_native),
         "IDA 注册可用" if caps["ida"] else "Ghidra 目录可用",
         "没检测到 IDA/Ghidra")
    take("enhanced", bool(engine_native), "有反编译器引擎（IDA/Ghidra）", "无反编译器引擎")
    take("session", bool(engine_native) or bool(caps["jadx"]),
         "有引擎，需要会话/数据库管理", "没有可用引擎")
    take("android", bool(caps["jadx"]) and bool(caps["jdk"]),
         "JADX JAR + 完整 JDK 就绪", "缺 JADX 或完整 JDK")
    take("apktool", bool(caps["apktool"]), "找到 apktool", "没装 apktool")
    take("adb", bool(caps["adb"]), "PATH 上有 adb", "PATH 上没有 adb")
    take("jeb", bool(caps["jeb"]), "找到 jeb 启动器", "没装 JEB")
    take("frida", bool(caps["frida"]), "PATH 上有 frida", "没装 frida")
    take("flutter", bool(caps["flutter"]), "找到 flutter SDK", "没装 flutter")
    take("managed", bool(caps["dotnet"] or caps["mono"]), ".NET/Mono 可用", "无 .NET/Mono")
    take("pcap", bool(caps["tshark"] or caps["dumpcap"]), "Wireshark 命令行可用", "无 tshark/dumpcap")
    take("browser", bool(caps["browser"]), "本机有浏览器可接管", "没找到浏览器")
    take("electron", bool(caps["electron"]), "electron 在 PATH", "无 electron")
    take("javascript-runtime", bool(caps["node"]), "Node 可用", "无 Node")
    take("firmware", bool(caps["binwalk"]), "找到 binwalk", "没装 binwalk")
    take("uefi", bool(caps["uefitool"]), "找到 UEFITool", "没装 UEFITool")

    # 语义不明确、无法从能力推断的族，一律不自动选
    for fam, why in (("direct", "核心直连工具，无法判断你是否需要"),
                     ("artifact", "打包产物类，无法判断你是否需要"),
                     ("application", "应用层（AgentEnv 等），无法判断你是否需要")):
        skipped.append((fam, why))
    return picked, reasons, skipped


def client_path(name):
    return os.path.expanduser(CLIENTS[name])


def load_servers(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None, {}
    except Exception as e:  # noqa: BLE001
        print("[!] %s 解析失败：%s" % (path, e))
        return None, {}
    servers = data.get("mcpServers")
    return data, (servers if isinstance(servers, dict) else {})


def is_rea_entry(name, spec):
    if name == "rea":
        return True
    text = json.dumps(spec, ensure_ascii=False).lower()
    return "rea-agents" in text or "rea.mjs" in text or "ida-pro-mcp" in text and "rea" in name


def find_registrations():
    """返回 {client: [(name, spec)]}，只列存在的客户端文件。"""
    found = {}
    for c in CLIENTS:
        p = client_path(c)
        if not os.path.isfile(p):
            continue
        _, servers = load_servers(p)
        hits = [(n, s) for n, s in servers.items() if is_rea_entry(n, s)]
        found[c] = hits
    return found


# ------------------------------ status ------------------------------------ #
def scan_engines():
    """扫描本机实际装了哪些分析引擎/辅助工具。

    返回 (installed, hidden)：installed 是 [(名字, 细节)]，hidden 是 [(名字, 原因)]。
    设计原则：**没装的直接隐藏**，不占屏、不当错误——用户不该为没装的东西看 ❌。
    """
    installed, hidden = [], []
    caps = detect_capabilities()

    # 分析引擎
    if caps["ida"]:
        installed.append(("IDA", "注册可用：%s" % caps["ida"]))
    if caps["ghidra"]:
        installed.append(("Ghidra", caps["ghidra"]))
    if caps["jadx"] and caps["jdk"]:
        installed.append(("JADX", "JAR 摘要匹配 + JDK 就绪"))
    if caps.get("jeb"):
        installed.append(("JEB", caps["jeb"]))
    if os.environ.get("REA_KUNA_COMMAND") and os.path.isfile(os.environ["REA_KUNA_COMMAND"]):
        installed.append(("Kuna", os.environ["REA_KUNA_COMMAND"]))

    for name, reason in (
        ("IDA", "未配置 REA_IDA_MCP_CONFIG"),
        ("Ghidra", "GHIDRA_INSTALL_DIR 未设置或路径不存在"),
        ("JADX", "JAR 缺失/摘要不符或缺完整 JDK"),
        ("JEB", "PATH 上没有 jeb"),
        ("Kuna", "未设置 REA_KUNA_COMMAND"),
        ("Hopper", "Windows 上不支持"),
    ):
        if not any(n == name for n, _ in installed):
            hidden.append((name, reason))

    # 辅助工具（有就列，没有就隐藏）
    for label, key, why in (
        ("apktool", "apktool", "未安装"),
        ("adb", "adb", "未安装"),
        ("frida", "frida", "未安装"),
        ("flutter", "flutter", "未安装"),
        (".NET/Mono", "managed", "未安装"),
        ("Wireshark CLI", "pcap", "未安装"),
        ("浏览器接管", "browser", "未找到浏览器"),
        ("binwalk", "firmware", "未安装"),
        ("UEFITool", "uefi", "未安装"),
    ):
        val = caps.get(key)
        if val:
            installed.append((label, str(val) if not isinstance(val, bool) else "可用"))
        else:
            hidden.append((label, why))
    return installed, hidden


def _find_candidates(patterns, max_dirs=4000):
    """在盘符根目录及一层子目录里找名字匹配的目录/文件（只看名字，不递归）。

    用来发现"装了但没接入"的工具：Ghidra 解压了却没设 GHIDRA_INSTALL_DIR、
    JEB 解压了却不在 PATH、platform-tools 在但 adb 没进 PATH 等。
    """
    hits = []
    checked = 0
    roots = [c + ":\\" for c in "CDEFGHIJKLMNOPQRSTUVWXYZ"] if os.name == "nt" else ["/opt", "/usr/local"]
    skip = {"windows", "program files", "program files (x86)", "programdata", "users",
            "$recycle.bin", "system volume information", "recovery", "perflogs",
            "node_modules", ".git", "appdata", "temp", "tmp", "windows.old"}
    low_patterns = [p.lower() for p in patterns]
    for root in roots:
        if not os.path.isdir(root):
            continue
        try:
            top = os.listdir(root)
        except OSError:
            continue
        for name in top:
            p = os.path.join(root, name)
            if not os.path.isdir(p) or name.lower() in skip:
                continue
            checked += 1
            for pat in low_patterns:
                if pat in name.lower():
                    hits.append(p)
                    break
            else:
                try:
                    subs = os.listdir(p)
                except OSError:
                    continue
                for sub in subs:
                    q = os.path.join(p, sub)
                    if os.path.isdir(q) and any(pat in sub.lower() for pat in low_patterns):
                        hits.append(q)
                    checked += 1
                    if checked >= max_dirs:
                        return hits
            if checked >= max_dirs:
                return hits
    return hits


def scan_untracked():
    """找"本机有、但 REA 接不上"的工具。返回 [(名字, 位置, 怎么接)]。"""
    out = []
    caps = detect_capabilities()

    # IDA：目录在，但注册缺失/失效
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from install_ida import detect_ida_dir
        ida_dir = detect_ida_dir()
    except Exception:
        ida_dir = None
    if ida_dir and not caps.get("ida"):
        out.append(("IDA", ida_dir, "跑 install_ida.py 生成注册"))

    # Ghidra：解压了但环境变量没指过去
    if not caps.get("ghidra"):
        for d in _find_candidates(["ghidra"]):
            if os.path.isdir(os.path.join(d, "Ghidra")) or os.path.isdir(os.path.join(d, "ghidra")):
                out.append(("Ghidra", d, "setx GHIDRA_INSTALL_DIR \"%s\"" % d))
                break

    # JEB：目录/jar 在但命令不可用
    if not caps.get("jeb"):
        for d in _find_candidates(["jeb"]):
            out.append(("JEB", d, "把它的启动脚本加入 PATH，或设 REA_KUNA_COMMAND 之外的自定义命令"))
            break

    # adb：platform-tools 在但 PATH 没有
    if not caps.get("adb"):
        for d in _find_candidates(["platform-tools"], max_dirs=2500):
            exe = os.path.join(d, "adb.exe" if os.name == "nt" else "adb")
            if os.path.isfile(exe):
                out.append(("adb", exe, "把 %s 加入 PATH" % d))
                break

    # apktool：jar 在但没有包装脚本
    if not caps.get("apktool"):
        for d in _find_candidates(["apktool"], max_dirs=2500):
            jar = os.path.join(d, "apktool.jar")
            if os.path.isfile(jar):
                out.append(("apktool", jar, "设 REA_APKTOOL_PATH 指向 apktool.jar 的包装脚本"))
                break

    # JADX：命令行有 jadx，但 REA 需要的 MCP JAR 缺失
    if not caps.get("jadx") and shutil.which("jadx"):
        out.append(("jadx", shutil.which("jadx"), "设 REA_JADX_MCP_JAR 指向 jadx-headless-mcp JAR（不是 jadx 本体）"))
    return out


def cmd_status(args):
    print("=== REA 在场状态 ===\n")
    regs = find_registrations()
    any_reg = False
    if not regs:
        print("客户端配置：未发现 workbuddy/codebuddy/cursor 的配置文件。")
    for c, hits in regs.items():
        p = client_path(c)
        if hits:
            any_reg = True
            for name, spec in hits:
                env = (spec.get("env") or {}) if isinstance(spec, dict) else {}
                fam = env.get("REA_MCP_TOOL_FAMILIES", "(未限制，全部族)")
                prof = (env.get("REA_MCP_TOOL_CATALOG_PROFILE")
                        or env.get("REA_MCP_CATALOG_PROFILE") or "(默认)")
                print("[开] %-10s %s" % (c, p))
                print("      条目=%s  族=%s  目录=%s" % (name, fam, prof))
                if env:
                    stale = [k for k, v in env.items() if v and os.sep in str(v)
                             and not os.path.exists(str(v).replace("/", os.sep))]
                    if stale:
                        print("      [!] 注册里有不存在的路径：%s" % ", ".join(stale))
                        print("          可能是环境搬过家，也可能该引擎的路径本身没配对；")
                        print("          调整环境后重新 on 一次即可刷新快照。")
        else:
            print("[关] %-10s %s（没有 rea 条目）" % (c, p))

    print()
    if any_reg:
        print("结论：注册存在 → 客户端**下次启动**会加载工具目录（本会话未重启则没变）。")
        print("      已重启却仍看不到工具？多半是该服务还没在连接器管理里被“信任”。")
        print("      想省上下文：python scripts/toggle.py off（然后重启客户端）")
    else:
        print("结论：未注册 → REA 的 263 个工具不在上下文里，只有你能手动跑 CLI。")
        print("      想要工具：python scripts/toggle.py on（然后重启客户端）")

    print("\n引擎扫描（只列已安装的，没装的隐藏）：")
    installed, hidden = scan_engines()
    if installed:
        width = max(len(n) for n, _ in installed)
        for name, detail in installed:
            print("  ✅ %-*s  %s" % (width, name, detail))
    else:
        print("  （一个可用的分析引擎都没扫到）")
    if hidden:
        if args.verbose:
            print("  ⋯ 已隐藏 %d 项（未安装）：" % len(hidden))
            for name, why in hidden:
                print("      %-12s %s" % (name, why))
        else:
            print("  ⋯ 已隐藏 %d 项未安装（--verbose 看是哪些）" % len(hidden))

    # 真检测：本机有、但 REA 接不上的东西
    try:
        loose = scan_untracked()
    except Exception:
        loose = []
    if loose:
        print("\n已发现但未接入（这些不是没装，是没连上）：")
        for name, where, hint in loose:
            print("  ! %-10s %s" % (name, where))
            print("      → %s" % hint)

    # 顺便给个"按本机能力该开哪些族"的建议，不写任何东西
    try:
        picked, _, _ = auto_families()
        if picked:
            total = sum(FAMILY_TOOLS.get(f, 0) for f in picked)
            print("\n按本机能力建议开：%s" % ", ".join(picked))
            print("  → 约 %d tools（全量 %d）；应用方式：toggle.py on --auto" % (total, sum(FAMILY_TOOLS.values())))
    except Exception:
        pass

    if args.deep:
        print("\n--- rea mcp doctor ---")
        rc, out = run("rea mcp doctor --format json", timeout=300)
        data = None
        s = out.find("{")
        if s >= 0:
            try:
                data = json.loads(out[s:])
            except Exception:
                data = None
        if data:
            inv = (data.get("inventory") or {}).get("tools") or {}
            print("工具清单：expected=%s observed=%s" % (inv.get("expected"), inv.get("observed")))
            print("健康：%s" % data.get("healthy"))
        else:
            print("[!] doctor 输出无法解析（rc=%d）" % rc)
    return 0


# -------------------------------- on -------------------------------------- #
def sanitized_env():
    """把"指向已不存在路径"的引擎变量从子进程环境里摘掉。

    rea setup 会把当前进程里探测到的变量快照进注册；如果某个变量指向已删除的目录
    （常见于搬过家/删过引擎），快照进去后 REA 每次都会报 config_drift。
    这里只在"值看起来像路径且不存在"时摘除，并返回被摘掉的清单。
    """
    env = dict(os.environ)
    watch = ("GHIDRA_INSTALL_DIR", "HOPPER_LAUNCHER_PATH", "REA_KUNA_COMMAND",
             "IDADIR", "IDA_HOME", "JAVA_HOME", "REA_JADX_MCP_JAR",
             "REA_IDA_MCP_CONFIG", "REA_APKTOOL_PATH")
    dropped = []
    for k in watch:
        v = env.get(k)
        if not v:
            continue
        text = str(v)
        if (os.sep in text or "/" in text) and not os.path.exists(text.replace("/", os.sep)):
            env.pop(k, None)
            dropped.append((k, text))
    return env, dropped


def prune_registration_env(client):
    """删掉注册 env 里指向不存在路径的键。

    rea setup 的合并策略是"保留已有 override、用探测值替换同名键"——它**不会删除**
    旧键。所以环境变了之后，注册里会留下死路径；这里直接改文件清掉。
    返回被删掉的键名列表。
    """
    p = client_path(client)
    data, servers = load_servers(p)
    if data is None:
        return []
    removed = []
    for name, spec in list(servers.items()):
        if not is_rea_entry(name, spec) or not isinstance(spec, dict):
            continue
        env = spec.get("env")
        if not isinstance(env, dict):
            continue
        for k, v in list(env.items()):
            text = str(v or "")
            if (os.sep in text or "/" in text) and not os.path.exists(text.replace("/", os.sep)):
                env.pop(k, None)
                removed.append(k)
        if removed:
            servers[name] = spec
    if removed:
        backup = "%s.bak-%s" % (p, time.strftime("%Y%m%d%H%M%S"))
        shutil.copy2(p, backup)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    return removed


def cmd_on(args):
    fams = [f.strip() for f in (args.families or "").split(",") if f.strip()]
    if args.auto and fams:
        print("[!] --auto 与 --families 不能同时用：要么让它自己选，要么你指定。")
        return 2
    auto = None
    if args.auto or not fams:
        picked, reasons, skipped = auto_families()
        if not picked:
            print("[!] 未检测到可选工具族，未修改注册。请先运行 status --verbose。")
            return 2
        fams = picked
        auto = (reasons, skipped)
    bad = [f for f in fams if f not in VALID_FAMILIES]
    if bad:
        print("[!] 未知工具族：%s" % ", ".join(bad))
        print("    可用：%s" % ", ".join(VALID_FAMILIES))
        return 2
    if args.profile not in ("hot-cold", "full"):
        print("[!] --profile 只能是 hot-cold 或 full")
        return 2

    clients = args.client or ["workbuddy"]
    print("=== 打开 REA（写客户端 MCP 注册）===\n")
    print("客户端：%s" % ", ".join(clients))
    if auto:
        reasons, skipped = auto
        print("\n按本机能力自动选族：")
        total = 0
        for fam, why in reasons:
            n = FAMILY_TOOLS.get(fam, 0)
            total += n
            print("  ✓ %-18s %3d tools  %s" % (fam, n, why))
        for fam, why in skipped:
            print("  ✗ %-18s     -      %s" % (fam, why))
        print("  → 合计 %d tools（全量 %d）\n" % (total, sum(FAMILY_TOOLS.values())))
    print("工具族：%s" % (", ".join(fams) if fams else "(不限制)"))
    print("目录档位：%s%s\n" % (args.profile,
                              "（按需加载，省上下文）" if args.profile == "hot-cold" else "（全量常驻，最贵）"))

    env, dropped = sanitized_env()
    if dropped:
        print("下列变量指向不存在的路径，不会写进注册：")
        for k, v in dropped:
            print("  - %-22s %s" % (k, v))
        print()

    failed = False
    for c in clients:
        if c not in CLIENTS:
            failed = True
            print("[!] 暂不支持直接改 %s 的配置（只支持 %s）。" % (c, ", ".join(CLIENTS)))
            print("    请用 REA 自己的命令：rea setup --client %s" % c)
            continue
        cmd = "rea setup --client %s --yes --format json --tool-catalog-profile %s" % (c, args.profile)
        if fams:
            cmd += " --tool-families %s" % ",".join(fams)
        if args.dry_run:
            cmd += " --dry-run"
        rc, out = run(cmd, timeout=900, env=env)
        data = None
        s = out.find("{")
        if s >= 0:
            try:
                data = json.loads(out[s:])
            except Exception:
                data = None
        if rc != 0:
            failed = True
            print("[!] %s 失败（rc=%d）" % (c, rc))
            for line in (out or "").splitlines()[:6]:
                print("    %s" % line)
            continue
        applied = data.get("appliedActions") if data else None
        planned = data.get("plannedActions") if data else None

        def brief(items):
            out = []
            for a in items or []:
                if isinstance(a, dict):
                    out.append("%s → %s" % (a.get("id"), a.get("target") or ""))
                else:
                    out.append(str(a))
            return "; ".join(out)

        if args.dry_run:
            print("[OK] %s 干跑：将写入 %s" % (c, brief(planned) or "(无变更)"))
        else:
            print("[OK] %s 已写入注册：%s" % (c, brief(applied) or "(无变更)"))
            pruned = prune_registration_env(c)
            if pruned:
                print("     已从注册里剔除失效环境变量：%s" % ", ".join(sorted(set(pruned))))

    print("\n注意：注册在客户端**启动时**读取 —— 必须重启 WorkBuddy/对应客户端才生效，")
    print("      当前会话的上下文不会立刻变化。")
    print("      想确认写进去了：python scripts/toggle.py status")
    return 1 if failed else 0


# -------------------------------- off ------------------------------------- #
def cmd_off(args):
    clients = args.client or [c for c in CLIENTS if find_registrations().get(c)]
    if not clients:
        print("没有需要关闭的客户端（本来就没注册）。")
        return 0
    print("=== 关闭 REA（移除客户端 MCP 注册）===\n")
    for c in clients:
        p = client_path(c)
        if not os.path.isfile(p):
            print("[跳过] %s 没有配置文件" % c)
            continue
        data, servers = load_servers(p)
        if data is None:
            continue
        hits = [n for n, s in servers.items() if is_rea_entry(n, s)]
        if not hits:
            print("[跳过] %s 里没有 rea 条目" % c)
            continue
        backup = "%s.bak-%s" % (p, time.strftime("%Y%m%d%H%M%S"))
        shutil.copy2(p, backup)
        for n in hits:
            servers.pop(n, None)
        data["mcpServers"] = servers
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        print("[OK] %s 已移除 %s（备份：%s）" % (c, ", ".join(hits), os.path.basename(backup)))

    print("\n重启客户端后生效。当前会话仍持有已加载的工具定义。")
    return 0


def main():
    apply_toolchain()
    ap = argparse.ArgumentParser(description="REA 在场开关")
    ap.add_argument("action", choices=["status", "on", "off"])
    ap.add_argument("--client", action="append", metavar="NAME",
                    help="client id，可重复；on 默认 workbuddy，off 默认所有已注册的")
    ap.add_argument("--families", help="逗号分隔的工具族（on）")
    ap.add_argument("--auto", action="store_true",
                    help="按本机能力自动选族（探测 IDA/Ghidra/JADX/adb/frida/browser 等）")
    ap.add_argument("--profile", default="full", choices=["hot-cold", "full"],
                    help="目录档位（on），默认 full")
    ap.add_argument("--deep", action="store_true", help="status 时额外跑 rea mcp doctor")
    ap.add_argument("--dry-run", action="store_true", help="on 时只打印计划，不写配置")
    ap.add_argument("--verbose", action="store_true", help="status 时显示被隐藏项的隐藏原因")
    args = ap.parse_args()

    if args.action == "on" and run("rea --version", timeout=120)[0] != 0:
        print("[!] 找不到 rea CLI，先把环境装好（install_all.py）。")
        return 2
    return {"status": cmd_status, "on": cmd_on, "off": cmd_off}[args.action](args)


if __name__ == "__main__":
    sys.exit(main())
