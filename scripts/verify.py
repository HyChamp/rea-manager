#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""装完之后的端到端验证：回答"到底能不能用"，而不只是"文件在不在"。

doctor.py 查存在性，本脚本查可用性：
  CLI      → rea --version / rea doctor --json 是否正常返回
  IDA      → 注册 JSON 里的命令能否真的启动
  Android  → JDK 含 jdk.compiler、JAR 摘要匹配
  冒烟     → 传入 --apk / --binary 时真的跑一次分析命令

用法：
  python scripts/verify.py
  python scripts/verify.py --apk "/abs/Example.apk"
  python scripts/verify.py --binary "/abs/program.exe" --symbol main
"""
import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (  # noqa: E402
    apply_toolchain, hydrate_rea_env, jdk_has_compiler, load_state, run, sha256_file,
)

JADX_SHA = "6e5eacf500b64292bfb73c49797c1958f6ee44646e43e868039ae7feb573ff75"

OK, BAD, SKIP = "✅", "❌", "—"


def check_cli():
    """rea --version 能跑、rea doctor --json 能产出可解析 JSON 即算通过。

    注意：rea doctor 在"有引擎不健康"时退出码是 1，但 JSON 是完整的。
    把退出码当失败会误判，所以只看 JSON 能不能解析出来。
    """
    if not shutil.which("rea"):
        return BAD, "rea 不在 PATH", {}
    rc, out = run("rea --version", timeout=120)
    if rc != 0:
        return BAD, "rea --version 失败：%s" % (out or "")[:160], {}
    version = out.strip().splitlines()[0] if out.strip() else ""

    rc2, doc = run("rea doctor --json", timeout=600)
    data = None
    start = doc.find("{") if doc else -1
    if start >= 0:
        try:
            data = json.loads(doc[start:])
        except Exception:
            data = None
    if data is None:
        return BAD, "rea 可用（%s），但 doctor 没有返回可解析的 JSON" % version, {}

    checks = data.get("checks") or []
    bad_checks = [c for c in checks if not c.get("ok")]
    detail = "rea %s；doctor 检查 %d 项，%d 项不合格" % (version, len(checks), len(bad_checks))
    if bad_checks:
        # 有引擎没配好不算 CLI 坏了，只提示
        return OK, detail + "（属引擎配置，见下方清单）", {"doctor": data, "warn": True}
    return OK, detail, {"doctor": data}


def print_doctor_summary(data):
    """把 doctor 的 JSON 压成几行：版本、引擎可用性、待修项。"""
    ident = data.get("identity") or {}
    if ident.get("cli_package_version"):
        print("  CLI 版本: %s" % ident["cli_package_version"])
    cat = (ident.get("catalog") or {}).get("counts") or {}
    if cat:
        print("  目录: %s 条 CLI 命令 / %s 个 MCP 工具" % (cat.get("cli_commands", "?"), cat.get("mcp_tools", "?")))

    checks = data.get("checks") or []
    engines = {}
    for c in checks:
        name = c.get("name", "")
        if name in ("node", "host") or name.startswith("registration:") or name.startswith("skill:"):
            continue
        if name.startswith("ghidra") or name.startswith("ida") or name.startswith("jadx") \
                or name.startswith("hopper") or name.startswith("kuna"):
            root = name.split("-")[0]
            engines.setdefault(root, []).append(c)
    if engines:
        print("\n  引擎:")
        for name, items in engines.items():
            ok = all(i.get("ok") for i in items)
            detail = ""
            for i in items:
                if i.get("ok") and i.get("detail"):
                    detail = i["detail"]
                    break
            print("    %-8s %s  %s" % (name, OK if ok else BAD, detail[:90]))

    fix = [c for c in checks if not c.get("ok") and c.get("remediation")]
    if fix:
        print("\n  待修项（按 remediation 处理）:")
        for c in fix[:8]:
            print("    · %-22s %s" % (c.get("name"), (c.get("detail") or c.get("remediation") or "")[:110]))


def check_ida():
    cands = [os.environ.get("REA_IDA_MCP_CONFIG"), load_state().get("ida_config")]
    cfg = next((c for c in cands if c and os.path.isfile(os.path.expanduser(c))), None)
    if not cfg:
        return SKIP, "未配置（没装 IDA 或注册 JSON 不存在）", {}
    cfg = os.path.expanduser(cfg)
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            reg = json.load(f)
    except Exception as e:  # noqa: BLE001
        return BAD, "注册 JSON 解析失败：%s" % e, {}
    cmd = reg.get("command") or ""
    exe = cmd if os.path.isabs(cmd) else (shutil.which(cmd) or "")
    if not exe or not os.path.isfile(exe):
        return BAD, "注册的命令不存在：%s" % (cmd or "(空)"), {}
    rc, out = run([exe, "--help"], shell=False, timeout=90)
    if rc == 0 or "usage" in (out or "").lower():
        return OK, "桥接可启动：%s" % exe, {"register": cfg}
    return BAD, "桥接启动失败：%s" % (out or "(无输出)")[:200], {"register": cfg}


def check_android():
    ok, note = jdk_has_compiler()
    cands = [os.environ.get("REA_JADX_MCP_JAR"), load_state().get("jadx_jar")]
    jar = next((c for c in cands if c and os.path.isfile(c)), None)
    if not jar:
        return SKIP, "未找到 JADX JAR（未装 Android 依赖）", {}
    digest = sha256_file(jar)
    jar_ok = digest.lower() == JADX_SHA
    if ok and jar_ok:
        return OK, "JDK 含 jdk.compiler，JAR 摘要匹配", {"jar": jar}
    detail = []
    if not ok:
        detail.append("JDK 问题：%s" % note)
    if not jar_ok:
        detail.append("JAR 摘要不匹配（%s…）" % digest[:12])
    return BAD, "；".join(detail), {"jar": jar}


def smoke_android(apk):
    rc, out = run('rea inspect-android-package "%s" --json' % apk, timeout=1200)
    if rc == 0 and out.strip():
        return OK, "smoke 通过：%s" % apk
    return BAD, "smoke 失败：%s" % (out or "(无输出)")[-400:]


def smoke_binary(binary, symbol):
    rc, out = run('rea function "%s" %s --json' % (binary, symbol), timeout=1800)
    if rc == 0 and out.strip():
        return OK, "smoke 通过：%s @ %s" % (binary, symbol)
    return BAD, "smoke 失败：%s" % (out or "(无输出)")[-400:]


def main():
    ap = argparse.ArgumentParser(description="REA 安装后可用性验证")
    ap.add_argument("--apk", help="用于冒烟测试的 APK 路径（可选）")
    ap.add_argument("--binary", help="用于冒烟测试的原生二进制路径（可选）")
    ap.add_argument("--symbol", default="main", help="--binary 时要分析的函数名，默认 main")
    ap.add_argument("--require", default="", help="要求必须可用，逗号分隔：cli,ida,android。缺省时未配置的引擎记为跳过")
    args = ap.parse_args()

    required = {s.strip().lower() for s in args.require.split(",") if s.strip()}

    apply_toolchain(verbose=True)
    hydrated = hydrate_rea_env()
    if hydrated:
        print("  [env] 已按 ~/.rea-tools/env.json 临时注入：%s" % ", ".join(hydrated))
    print("\n=== REA 可用性验证 ===\n")

    rows = []
    cli_state, cli_note, cli_data = check_cli()
    rows.append(("CLI", cli_state, cli_note))

    ida_state, ida_note, _ = check_ida()
    rows.append(("IDA", ida_state, ida_note))

    and_state, and_note, _ = check_android()
    rows.append(("Android", and_state, and_note))

    # --require：被点名却处于"跳过"状态的，直接算失败
    def enforce(rows_, key, name):
        if key not in required:
            return
        for i, (n, state, note) in enumerate(rows_):
            if n == name and state != OK:
                rows_[i] = (n, BAD, note + "（--require %s 要求必须可用）" % key)

    enforce(rows, "cli", "CLI")
    enforce(rows, "ida", "IDA")
    enforce(rows, "android", "Android")

    if args.apk:
        if not os.path.isfile(args.apk):
            rows.append(("APK smoke", BAD, "文件不存在：%s" % args.apk))
        else:
            rows.append(("APK smoke",) + smoke_android(args.apk))

    if args.binary:
        if not os.path.isfile(args.binary):
            rows.append(("Binary smoke", BAD, "文件不存在：%s" % args.binary))
        else:
            rows.append(("Binary smoke",) + smoke_binary(args.binary, args.symbol))

    width = max(len(r[0]) for r in rows)
    for name, state, note in rows:
        print("  %-*s  %s  %s" % (width, name, state, note))

    bad = [r for r in rows if r[1] == BAD]
    if cli_data.get("doctor"):
        print("\n--- rea doctor 摘要 ---")
        print_doctor_summary(cli_data["doctor"])

    print()
    if bad:
        print("结论：有 %d 项不可用 → %s" % (len(bad), ", ".join(r[0] for r in bad)))
        return 1
    print("结论：已验证项全部可用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
