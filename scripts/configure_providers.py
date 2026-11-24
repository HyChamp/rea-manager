#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 IDA 注册模板、持久化 REA 相关环境变量。

旧版 Windows 上 `--persist` 只打印 setx 片段，名字叫 persist 却不落地。
现在真的写：Windows 用 setx（用户级，不需要管理员）并额外生成
~/rea-env.ps1 方便当前会话 dot-source；Unix 写 shell 配置，且块级替换，
反复执行不会堆出重复段落。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import IS_WIN, run, save_state  # noqa: E402

BEGIN = "# >>> REA setup >>>"
END = "# <<< REA setup <<<"

IDA_TEMPLATE = """{
  "command": "python",
  "args": [
    "/absolute/path/to/ida_pro_mcp/server.py",
    "--ida-rpc",
    "http://127.0.0.1:13337"
  ]
}
"""

IDA_HEADLESS_TEMPLATE = """{
  "command": "/absolute/path/to/idalib-mcp",
  "args": ["--stdio", "--max-workers", "1"],
  "mode": "headless",
  "timeoutMs": 300000
}
"""


def shell_profile():
    home = os.path.expanduser("~")
    shell = os.environ.get("SHELL", "")
    if "zsh" in shell:
        return os.path.join(home, ".zshrc")
    if "bash" in shell:
        return os.path.join(home, ".bashrc")
    for name in (".bashrc", ".zshrc", ".profile"):
        p = os.path.join(home, name)
        if os.path.exists(p):
            return p
    return os.path.join(home, ".profile")


def write_block(path, block):
    """块级替换：已有 REA 段落就替换，没有才追加（避免重复堆积）。"""
    old = ""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            old = f.read()
    start = old.find(BEGIN)
    if start >= 0:
        end = old.find(END, start)
        if end >= 0:
            old = old[:start] + old[end + len(END):]
    new = old.rstrip("\n") + "\n\n" + block + "\n"
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
    os.replace(tmp, path)
    return path


def main():
    ap = argparse.ArgumentParser(description="REA provider 配置助手")
    ap.add_argument("--emit-ida-config", action="store_true", help="输出 GUI 附加模式的 IDA 注册模板")
    ap.add_argument("--emit-ida-headless", action="store_true", help="输出 headless 模式的 IDA 注册模板")
    ap.add_argument("--persist", action="store_true", help="把环境变量真正写入本机环境")
    ap.add_argument("--jadx-jar", help="jadx-headless-mcp JAR 绝对路径")
    ap.add_argument("--java-home", help="完整 JDK 绝对路径")
    ap.add_argument("--ida-config", help="ida-mcp.json 绝对路径")
    ap.add_argument("--ida-provider", action="store_true", help="同时设置 REA_ANALYSIS_PROVIDER=ida")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要执行的操作")
    args = ap.parse_args()

    if args.emit_ida_config:
        print(IDA_TEMPLATE.strip())
        return 0
    if args.emit_ida_headless:
        print(IDA_HEADLESS_TEMPLATE.strip())
        return 0
    if not args.persist:
        ap.print_help()
        return 0

    exports = []
    if args.jadx_jar:
        exports.append(("REA_JADX_MCP_JAR", os.path.abspath(args.jadx_jar)))
    if args.java_home:
        exports.append(("JAVA_HOME", os.path.abspath(args.java_home)))
    if args.ida_config:
        exports.append(("REA_IDA_MCP_CONFIG", os.path.abspath(args.ida_config)))
    if args.ida_provider:
        exports.append(("REA_ANALYSIS_PROVIDER", "ida"))

    if not exports:
        print("[!] --persist 需要至少一个变量参数（--jadx-jar / --java-home / --ida-config / --ida-provider）。")
        return 2

    if IS_WIN:
        # 1) 当前会话可 dot-source 的片段
        ps1 = os.path.join(os.path.expanduser("~"), "rea-env.ps1")
        body = "\n".join(['$env:%s = "%s"' % (k, v) for k, v in exports]) + "\n"
        if args.dry_run:
            print("会写入 %s：\n%s" % (ps1, body))
        else:
            with open(ps1, "w", encoding="utf-8") as f:
                f.write(body)
            print("[OK] 已写入 %s（当前会话：. \"%s\"）" % (ps1, ps1))

        # 2) 用户级持久化（新终端生效）
        failed = []
        for k, v in exports:
            cmd = 'setx %s "%s"' % (k, v)
            if args.dry_run:
                print("会执行：%s" % cmd)
                continue
            rc, out = run(cmd, timeout=60)
            if rc == 0:
                print("[OK] setx %s" % k)
            else:
                failed.append(k)
                print("[!] setx %s 失败：%s" % (k, (out or "")[:200]))
        if not args.dry_run:
            save_state(**{k.lower(): v for k, v in exports})
            print("\n新开的终端会自动带上这些变量；当前窗口用上面的 . \"rea-env.ps1\"。")
        return 1 if failed else 0

    # Unix：写 shell 配置
    lines = "\n".join(['export %s="%s"' % (k, v) for k, v in exports])
    block = "%s\n%s\n%s" % (BEGIN, lines, END)
    profile = shell_profile()
    if args.dry_run:
        print("会写入 %s：\n%s" % (profile, block))
        return 0
    write_block(profile, block)
    save_state(**{k.lower(): v for k, v in exports})
    print("[OK] 已写入 %s（重复执行会替换而非追加）" % profile)
    print(block)
    print("\n执行 source %s 或重开终端使其生效。" % profile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
