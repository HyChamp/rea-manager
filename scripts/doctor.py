#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""REA 环境体检：输出平台、Node、npm、rea、JDK、JADX、IDA 状态，附 JSON。

体检会先把脚本托管的工具链（~/.rea-tools）注入本次运行，因此反映的是
"脚本装完之后能不能用"，而不是"你的 shell 有没有配 PATH"。
两者不一致时会额外提示需要持久化哪些变量。
"""
import glob
import json
import os
import platform
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (  # noqa: E402
    apply_toolchain, jdk_has_compiler, node_requirement_text,
    node_version_ok, parse_version, run, sha256_file, state_path, load_state,
)

JADX_SHA = "6e5eacf500b64292bfb73c49797c1958f6ee44646e43e868039ae7feb573ff75"


def find_jar():
    env = os.environ.get("REA_JADX_MCP_JAR")
    if env and os.path.isfile(env):
        return env
    st = load_state()
    if st.get("jadx_jar") and os.path.isfile(st["jadx_jar"]):
        return st["jadx_jar"]
    roots = [os.path.expanduser("~/.local/share/rea/android"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "rea", "android")]
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            if name.startswith("jadx-headless-mcp") and name.endswith(".jar"):
                return os.path.join(root, name)
    return None


def find_ida():
    st = load_state()
    cands = [os.environ.get("IDADIR"), os.environ.get("IDA_HOME"), st.get("ida_dir")]
    if platform.system() == "Windows":
        for base in [os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]:
            cands += sorted(glob.glob(os.path.join(base, "IDA Professional *")), reverse=True)
            cands += sorted(glob.glob(os.path.join(base, "IDA Pro *")), reverse=True)
    elif platform.system() == "Darwin":
        cands += sorted(glob.glob("/Applications/IDA Professional *.app/Contents/MacOS"), reverse=True)
    else:
        cands += sorted(glob.glob("/opt/idapro-*"), reverse=True)
    for p in cands:
        if p and os.path.isdir(p):
            return p
    return None


def ida_bridge_status():
    """IDA 桥接是否真的可执行（不只是配置文件在）。"""
    cands = [os.environ.get("REA_IDA_MCP_CONFIG"), load_state().get("ida_config")]
    cfg = next((c for c in cands if c and os.path.isfile(os.path.expanduser(c))), None)
    if not cfg:
        stale = os.environ.get("REA_IDA_MCP_CONFIG")
        if stale:
            return False, "REA_IDA_MCP_CONFIG 指向不存在的文件：%s（模板占位符？）" % stale
        return False, "未配置注册 JSON"
    cfg = os.path.expanduser(cfg)
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            reg = json.load(f)
    except Exception as e:  # noqa: BLE001
        return False, "注册 JSON 无法解析：%s" % e
    cmd = reg.get("command") or ""
    args = reg.get("args") or []
    # 允许绝对路径或 PATH 上的命令名
    exe = cmd if os.path.isabs(cmd) else shutil.which(cmd)
    if not exe or not os.path.isfile(exe):
        return False, "注册的命令找不到：%s" % (cmd or "(空)")
    rc, out = run([exe] + [a for a in args if a] + ["--help"], shell=False, timeout=45)
    if rc != 0 and "usage" not in (out or "").lower():
        return False, "桥接无法启动：%s" % (out or "(无输出)")[:200]
    return True, "桥接可执行：%s" % exe


def main():
    import argparse
    ap = argparse.ArgumentParser(description="REA 环境体检")
    ap.add_argument("--quiet", action="store_true", help="不打印 JSON 与尾部建议（给 install_all 调用）")
    args = ap.parse_args()

    applied = apply_toolchain()
    system = platform.system()

    node_raw = run("node -v", timeout=60)[1] if shutil.which("node") else ""
    npm_raw = run("npm -v", timeout=90)[1] if shutil.which("npm") else ""
    rea_raw = run("rea --version", timeout=60)[1] if shutil.which("rea") else ""
    nv = parse_version(node_raw)
    node_ok = node_version_ok(nv)
    jd_ok, jd_note = jdk_has_compiler()
    jar = find_jar()
    jar_ok = bool(jar) and sha256_file(jar).lower() == JADX_SHA if jar else False
    # 用 install_ida 的探测器（含盘符根目录 + 一层子目录扫描），比 doctor 自己的短名单更准
    try:
        from install_ida import detect_ida_dir
        ida = detect_ida_dir()
    except Exception:
        ida = None
    if not ida:
        ida = find_ida()
    bridge_ok, bridge_note = ida_bridge_status()

    moved_note = ""
    prev_ida = (load_state().get("ida_dir") or "").strip()
    if prev_ida and os.path.normcase(os.path.abspath(prev_ida)) != os.path.normcase(ida or ""):
        moved_note = prev_ida
    data = {
        "platform": system,
        "toolchain_applied": applied,
        "state_file": state_path(),
        "node": {"raw": node_raw, "parsed": list(nv) if nv else None, "ok": node_ok},
        "npm": {"raw": npm_raw, "ok": bool(npm_raw)},
        "rea": {"raw": rea_raw, "installed": bool(rea_raw) and "ERR" not in rea_raw[:12]},
        "jdk": {"compiler_module": jd_ok, "note": jd_note},
        "jadx_jar": {"path": jar, "present": bool(jar), "checksum_ok": jar_ok},
        "ida": {"path": ida, "present": bool(ida)},
        "ida_moved_from": moved_note or None,
        "ida_bridge": {"ok": bridge_ok, "note": bridge_note},
    }
    data["ready"] = {
        "cli": node_ok and data["rea"]["installed"],
        "android": jd_ok and jar_ok,
        "ida": bool(ida) and bridge_ok,
    }

    def mark(ok):
        return "OK" if ok else "不符合"

    print("=== REA 环境体检 ===")
    print("平台: %s" % system)
    if applied["path_added"]:
        print("工具链: 已注入脚本托管的目录（%d 个）" % len(applied["path_added"]))
        for d in applied["path_added"]:
            print("        %s" % d)
    print("Node: %s  [%s]" % (node_raw.strip() or "未安装", mark(node_ok)))
    print("npm : %s" % (npm_raw.strip() or "未安装"))
    print("REA : %s" % (rea_raw.strip() or "未安装"))
    print("JDK : %s" % jd_note)
    print("JADX JAR: %s%s" % (jar or "未找到", "" if not jar else ("（摘要匹配）" if jar_ok else "（摘要不匹配！）")))
    print("IDA : %s" % (ida or "未找到（可用 install_ida.py --ida-dir 指定）"))
    print("IDA 桥接: %s" % bridge_note)
    print()
    print("就绪状态: CLI=%s  Android=%s  IDA=%s" % (
        "是" if data["ready"]["cli"] else "否",
        "是" if data["ready"]["android"] else "否",
        "是" if data["ready"]["ida"] else "否"))

    if not node_ok:
        if node_raw.strip():
            print("\n[!] Node 已安装但版本不符合：%s（需 %s）。" % (node_raw.strip(), node_requirement_text()))
            print("    跑 python scripts/install_node.py 会自动装 24 LTS 到用户目录。")
        else:
            print("\n[!] 未找到 Node。跑 python scripts/install_node.py 自动安装。")
    if moved_note:
        print("[!] IDA 目录变过：配置里还是 %s，实际是 %s。" % (moved_note, ida or "(未找到)"))
        print("    跑 python scripts/install_ida.py 会自动重配（激活/注册/启动器一次改完）。")
    if not jd_ok:
        print("[!] JDK 不完整或没有 java：跑 python scripts/install_jdk.py")
    if jar and not jar_ok:
        print("[!] JADX JAR 摘要不匹配，请删除后重跑 install_android.py 重新下载。")

    missing_env = [k for k in ("REA_JADX_MCP_JAR", "REA_IDA_MCP_CONFIG", "REA_ANALYSIS_PROVIDER")
                   if not os.environ.get(k)]
    if missing_env:
        print("\n提示：这些变量只在脚本进程内生效，你自己的终端里还需要持久化：%s"
              % ", ".join(missing_env))
        print("      跑 python scripts/configure_providers.py --persist ... 或参考 install_*.py 输出。")

    if args.quiet:
        return 0

    print("\n--- JSON ---")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
