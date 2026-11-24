#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安装 Android APK 分析依赖：校验完整 JDK，下载并校验固定版本 JADX JAR，输出环境变量。

JDK 缺失时会自动装 Temurin 21，装完**继续**下载 JAR（旧版会提前 return，
导致"JDK 装好了但 JAR 没下、还得再跑一遍"）。
"""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (  # noqa: E402
    IS_WIN, apply_toolchain, jdk_has_compiler, persist_user_env, run,
    save_state, sha256_file,
)

_PERSIST = False

JADX_URL = "https://github.com/1013503897/jadx-headless-mcp/releases/download/v0.7.1/jadx-headless-mcp-0.7.1-all.jar"
JADX_SHA = "6e5eacf500b64292bfb73c49797c1958f6ee44646e43e868039ae7feb573ff75"
JADX_NAME = "jadx-headless-mcp-0.7.1-all.jar"

HERE = os.path.dirname(os.path.abspath(__file__))


def default_dir():
    if IS_WIN:
        return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "rea", "android")
    return os.path.expanduser("~/.local/share/rea/android")


def ensure_jdk():
    """保证当前进程里有完整 JDK。必要时调用 install_jdk.py，并在成功后重新检测。"""
    ok, note = jdk_has_compiler()
    if ok:
        print("[OK] 完整 JDK 已就绪：%s" % run("java -version", timeout=60)[1].splitlines()[0])
        return True
    print("[!] 当前 java 不可用或不完整（%s）。" % note)
    print("自动安装 JDK 21 到用户目录…")
    rc = subprocess.call([sys.executable, os.path.join(HERE, "install_jdk.py")])
    if rc != 0:
        print("[!] 自动安装 JDK 失败，请手动装完整 JDK 17+ 后重跑。")
        return False
    apply_toolchain()   # install_jdk 已把 JAVA_HOME/bin 注入它自己的进程，这里重新读取状态
    ok, note = jdk_has_compiler()
    if not ok:
        print("[!] JDK 已下载但当前进程仍读不到（%s）。重开终端后重跑本脚本。" % note)
        return False
    print("[OK] JDK 已就绪：%s" % run("java -version", timeout=60)[1].splitlines()[0])
    return True


def main():
    global _PERSIST
    ap = argparse.ArgumentParser(description="安装 Android(JADX) 分析依赖")
    ap.add_argument("--persist", action="store_true", help="把 REA_JADX_MCP_JAR（和 JAVA_HOME）写入用户环境")
    args = ap.parse_args()
    _PERSIST = args.persist

    print("=== Android(JADX) 依赖 ===\n")
    apply_toolchain(verbose=True)

    if not ensure_jdk():
        return 2

    env_jar = os.environ.get("REA_JADX_MCP_JAR")
    if env_jar and os.path.isfile(env_jar) and sha256_file(env_jar).lower() == JADX_SHA:
        print("[OK] REA_JADX_MCP_JAR 已设置且校验通过：%s" % env_jar)
        save_state(jadx_jar=env_jar)
        print_env(env_jar)
        return 0

    dest_dir = default_dir()
    os.makedirs(dest_dir, exist_ok=True)
    jar_path = os.path.join(dest_dir, JADX_NAME)

    if os.path.isfile(jar_path):
        actual = sha256_file(jar_path)
        if actual.lower() == JADX_SHA:
            print("[OK] 已存在校验通过的 JAR：%s" % jar_path)
            print_env(jar_path)
            save_state(jadx_jar=jar_path)
            return 0
        print("[!] 已有 JAR 摘要不匹配（期望 %s… 实际 %s…），删除后重下。" % (JADX_SHA[:12], actual[:12]))
        try:
            os.remove(jar_path)
        except OSError:
            pass

    print("下载 JADX 桥接 JAR（约 40 MB，支持断点续传）…\n  %s" % JADX_URL)
    from dl import download
    if not download(JADX_URL, jar_path, checksum=JADX_SHA):
        print("[!] 下载或校验失败。网络恢复后重跑本脚本（会接着下）。")
        return 1
    print("[OK] 下载并校验通过。")

    save_state(jadx_jar=jar_path)
    print_env(jar_path)
    return 0


def print_env(jar_path):
    print("\n=== 需要在运行 REA 的同一个 shell 里设置 ===")
    if IS_WIN:
        print("$env:REA_JADX_MCP_JAR = '%s'" % jar_path)
        print("$env:JAVA_HOME = '你的JDK绝对路径'   # 可选，java 已在 PATH 时可省")
        print("# 大 APK：$env:REA_JADX_HEAP_MIB = '8192'")
        if not _PERSIST:
            print("# 持久化：setx REA_JADX_MCP_JAR \"%s\"" % jar_path)
    else:
        print('export REA_JADX_MCP_JAR="%s"' % jar_path)
        print('export JAVA_HOME=/你的/JDK/路径   # 可选')
        print("# 大 APK：export REA_JADX_HEAP_MIB=8192")
    if not _PERSIST:
        print("\n或用 configure_providers.py --persist 自动写入配置。")
    print("Linux 还需 procps 兼容的 ps（REA 用它跟踪 JADX 进程）。")
    print("\n冒烟测试：rea inspect-android-package /abs/path/Example.apk --json")

    if _PERSIST:
        print("\n=== 写入用户环境变量 ===")
        pairs = [("REA_JADX_MCP_JAR", jar_path)]
        jh = os.environ.get("JAVA_HOME")
        if jh and os.path.isdir(jh):
            pairs.append(("JAVA_HOME", jh))
        persist_user_env(pairs)


if __name__ == "__main__":
    sys.exit(main())
