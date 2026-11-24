#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Install the REA toolchain and report actual component outcomes."""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _common import apply_toolchain, node_requirement_text, run  # noqa: E402
OK, SKIP, FAIL = "完成", "跳过", "失败"


def step(name, script, extra=()):
    print("\n=== %s ===" % name)
    rc = subprocess.call([sys.executable, "-u", os.path.join(HERE, script), *extra])
    apply_toolchain()
    return rc


def finish(results, required_failed=False):
    print("\n=== 安装汇总 ===")
    for name, (state, note) in results.items():
        print("  %s：%s%s" % (name, state, " · " + note if note else ""))
    if required_failed:
        print("必需步骤未完成，请先处理上方失败项。")
        return 1
    if any(state == FAIL for state, _ in results.values()):
        print("部分组件未就绪；请按上方日志重试对应步骤。")
        return 2
    print("安装流程结束。仅安装工具链与说明，不开启客户端 MCP。")
    return 0


def main():
    ap = argparse.ArgumentParser(description="REA 全自动安装")
    ap.add_argument("--ida-dir", help="IDA Pro 安装目录（自动探测失败时用）")
    ap.add_argument("--skip-ida", action="store_true", help="跳过 IDA 接入")
    ap.add_argument("--skip-android", action="store_true", help="跳过 Android 依赖")
    ap.add_argument("--no-verify", action="store_true", help="跳过安装后的可用性验证")
    ap.add_argument("--no-skill", action="store_true", help="不安装任何 skill")
    ap.add_argument("--no-manager-skill", action="store_true", help="不安装 /rea 管理器 skill（官方说明书仍装）")
    args = ap.parse_args()

    print("REA 全自动安装开始（IDA Pro 本体需自备）")
    print("Node 兼容范围：%s（安装器另有固定目标版本）" % node_requirement_text())
    results = dict.fromkeys(("Node", "JDK", "REA CLI", "IDA 桥接", "Android(JADX)", "可用性验证", "skills（官方+管理器）"), (SKIP, "前置步骤未完成"))
    apply_toolchain()
    for name, script, extra, version_cmd in (
        ("Node", "install_node.py", ["--persist-path"], "node -v"),
        ("JDK", "install_jdk.py", [], "java -version"),
        ("REA CLI", "install_rea.py", ["--persist-path"], "rea --version"),
    ):
        rc = step(name, script, extra)
        if rc:
            results[name] = (FAIL, "退出码 %d" % rc)
            return finish(results, required_failed=True)
        rc, version = run(version_cmd, timeout=90)
        results[name] = (OK, version.strip().splitlines()[0] if rc == 0 and version.strip() else "")

    if args.skip_ida:
        results["IDA 桥接"] = (SKIP, "由参数指定")
    else:
        extra = ["--persist"] + (["--ida-dir", args.ida_dir] if args.ida_dir else [])
        rc = step("IDA 桥接", "install_ida.py", extra)
        if rc == 2 and not args.ida_dir:
            results["IDA 桥接"] = (SKIP, "未找到 IDA Pro 本体")
        else:
            results["IDA 桥接"] = (FAIL, "退出码 %d" % rc) if rc else (OK, "桥接检查通过")

    if args.skip_android:
        results["Android(JADX)"] = (SKIP, "由参数指定")
    else:
        rc = step("Android(JADX)", "install_android.py", ["--persist"])
        results["Android(JADX)"] = (FAIL, "退出码 %d" % rc) if rc else (OK, "依赖检查通过")

    if args.no_verify:
        results["可用性验证"] = (SKIP, "由参数指定")
    else:
        engines = [engine for name, engine in (("IDA 桥接", "ida"), ("Android(JADX)", "android")) if results[name][0] == OK]
        rc = step("可用性验证", "verify.py", ["--require", ",".join(engines)] if engines else [])
        results["可用性验证"] = (FAIL, "退出码 %d" % rc) if rc else (OK, "CLI 及已接入引擎")

    if args.no_skill:
        results["skills（官方+管理器）"] = (SKIP, "由参数指定")
    elif results["可用性验证"][0] == FAIL:
        results["skills（官方+管理器）"] = (SKIP, "可用性验证未通过")
    else:
        extra = ["--no-manager-skill"] if args.no_manager_skill else []
        rc = step("skills（官方说明书 + /rea 管理器，不写 MCP 注册）", "install_skill.py", extra)
        results["skills（官方+管理器）"] = (FAIL, "退出码 %d" % rc) if rc else (OK, "两个均已写入客户端 skills 目录")

    return finish(results, required_failed=results["可用性验证"][0] == FAIL)


if __name__ == "__main__":
    sys.exit(main())
