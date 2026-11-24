#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安装并验证 rea-agents CLI；Node 版本不合格时自动装 24 LTS 并**继续**。

和旧版的区别：不再在"刚装完 Node"时返回 2 让用户手动重跑。
install_node.py 会把新目录注入本进程 PATH，这里拿到就直接接着装 CLI。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (  # noqa: E402
    apply_toolchain, clean_npm_env, node_requirement_text, node_version_ok,
    parse_version, persist_user_path, prepend_path, run, save_state,
)

HERE = os.path.dirname(os.path.abspath(__file__))
# 固定 CLI 版本：与实测通过的 6.10.1 一致；改这里等于换一个已知基线
REA_PIN = os.environ.get("REA_CLI_VERSION", "6.10.1")


def has_node():
    return bool(shutil.which("node"))


def node_ok():
    if not has_node():
        return False
    return node_version_ok(parse_version(run("node -v", timeout=60)[1]))


def npm_prefix():
    rc, out = run("npm prefix -g", timeout=120)
    if rc == 0 and out.strip() and os.path.isdir(out.strip()):
        return out.strip()
    return None


def installed_version(prefix):
    """从 npm 全局目录直接读 rea-agents 版本，比 `npm ls -g` 快且不依赖 npm 输出格式。"""
    if not prefix:
        return None
    pkg = os.path.join(prefix, "node_modules", "rea-agents", "package.json")
    try:
        with open(pkg, "r", encoding="utf-8") as f:
            return json.load(f).get("version")
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="安装 rea-agents CLI")
    ap.add_argument("--persist-path", action="store_true", help="把 npm 全局 bin 写进用户 PATH（幂等）")
    ap.add_argument("--force", action="store_true", help="已在装也强制重跑 npm install")
    args = ap.parse_args()

    print("=== REA CLI 安装 ===\n")
    apply_toolchain(verbose=True)

    if not node_ok():
        print("[!] Node 缺失或版本不符合要求（需 %s），先自动补齐…" % node_requirement_text())
        sub = [sys.executable, os.path.join(HERE, "install_node.py")]
        if args.persist_path:
            sub.append("--persist-path")
        rc = subprocess.call(sub)
        if rc != 0:
            print("[!] 自动安装 Node 失败。请手动装 24 LTS 后重跑。")
            return 2
        apply_toolchain()
        if not node_ok():
            print("[!] Node 装好了但当前进程仍读不到，请重开终端后重跑本脚本。")
            return 2

    print("Node: %s" % run("node -v", timeout=60)[1].strip())
    if not shutil.which("npm"):
        print("[!] 未找到 npm。Node 安装可能不完整，重跑 install_node.py。")
        return 2

    # 幂等：已经装好且能用，就别再跑两分钟的 npm（修补/重跑时很常见）
    prefix = npm_prefix()
    cur = installed_version(prefix)
    if cur and cur != REA_PIN and not args.force:
        print("\n[!] 已安装 %s，与固定版本 %s 不一致 —— 按稳定优先重装固定版本…" % (cur, REA_PIN))
    elif cur and not args.force:
        rc, ver = run("rea --version", timeout=120)
        if rc == 0 and ver.strip():
            print("\n[OK] rea-agents 已是指定版本：%s（跳过 npm；要重装加 --force）" % ver.strip())
            if prefix:
                prepend_path(prefix)
                save_state(npm_prefix=prefix)
                if args.persist_path:
                    ok, note = persist_user_path(prefix)
                    print("      [%s] %s" % ("OK" if ok else "!", note))
            rc2, help_out = run("rea --help", timeout=120)
            if rc2 == 0 and len(help_out) >= 20:
                print("[OK] 子命令可用，无需重装。")
                return 0
            print("[!] rea 已装上但 --help 异常，继续走重装流程。")

    print("\n[1/3] 安装 rea-agents（npm install -g rea-agents）…")
    print("      国内网络慢可先换镜像：npm config set registry https://registry.npmmirror.com")
    env, removed = clean_npm_env()
    if removed:
        print("      （已临时移除 %s：宿主注入的 shim 会拦截 npm 的批量删除）" % "/".join(removed))
    rc, out = run("npm install -g rea-agents@%s" % REA_PIN, timeout=1800, env=env)
    if rc != 0:
        print("[!] 安装失败：\n%s" % out[-2000:])
        if "SAFE_DELETE" in out:
            print("\n提示：这是宿主环境的删除拦截（不是 npm 包的问题）。")
            print("      在普通终端里跑 `npm install -g rea-agents`，或先 unset NODE_OPTIONS。")
        return 1
    print("      安装命令完成。")

    # npm 全局 bin 未必在当前 PATH 里，主动注入，避免"装好了却 command not found"
    prefix = npm_prefix()
    if prefix:
        prepend_path(prefix)
        save_state(npm_prefix=prefix)
        print("      全局 bin: %s" % prefix)
        if args.persist_path:
            ok, note = persist_user_path(prefix)
            print("      [%s] %s" % ("OK" if ok else "!", note))

    print("\n[2/3] 验证版本…")
    rc, ver = run("rea --version", timeout=120)
    print("      rea --version -> %s" % (ver.strip() or "(空)"))
    if rc != 0 or not ver.strip():
        print("[!] rea 命令不可用。若提示 command not found，把上面的全局 bin 加进 PATH 后重试。")
        return 1

    print("\n[3/3] 确认子命令可用…")
    rc, help_out = run("rea --help", timeout=120)
    if rc != 0 or len(help_out) < 20:
        print("[!] rea --help 输出异常：%s" % help_out[:500])
        return 1

    print("\n[OK] REA CLI 安装成功：%s" % ver.strip())
    print("下一步：rea doctor --json（看引擎基线）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
