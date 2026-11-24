#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全自动安装完整 JDK 21：从 Adoptium API 拉取官方绿色包，校验 SHA-256，解压即用。

装完会把 JAVA_HOME/bin 注入当前进程并把位置记到 ~/.rea-tools/env.json，
这样随后的 install_android.py 不会再判断"没有 JDK"而重复下载 200MB。
"""
import json
import os
import platform
import re
import sys
import tarfile
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (  # noqa: E402
    IS_WIN, java_exe_paths, java_home_status, jdk_bin_dir,
    prepend_path, run, save_state, shell_hint_jdk, tools_root,
)

API = ("https://api.adoptium.net/v3/assets/latest/21/hotspot"
       "?architecture={arch}&os={os}&image_type=jdk&vendor=eclipse")


def api_params():
    s = platform.system()
    os_name = {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}.get(s, "linux")
    arch = platform.machine().lower()
    if "arm" in arch or "aarch64" in arch:
        a = "aarch64"
    else:
        a = "x64"
    return os_name, a


def install_dir():
    return os.path.join(tools_root(), "jdk21")


def extracted_jdks(base):
    java_rel = os.path.join("bin", "java.exe" if IS_WIN else "java")
    if not os.path.isdir(base):
        return
    for name in sorted(os.listdir(base), reverse=True):
        cand = os.path.join(base, name)
        if os.path.isfile(os.path.join(cand, java_rel)):
            yield cand


def apply_java(jdk_home):
    """把 JDK 注入当前进程，并做一次真实校验。"""
    java_exe = os.path.join(jdk_bin_dir(jdk_home), "java.exe" if IS_WIN else "java")
    rc, out = run('"%s" -version' % java_exe, timeout=60)
    first = (out.splitlines()[0].strip() if out else "")
    if rc != 0:
        print("[!] JDK 解压后无法运行：%s" % (out or "(无输出)"))
        return False
    if not re.search(r'\bversion "21(?:[.+-]|\")', first):
        print("[!] 跳过非 JDK 21：%s" % first)
        return False
    rc2, mods = run('"%s" --list-modules' % java_exe, timeout=90)
    if rc2 != 0 or not any(line.split("@", 1)[0].strip() == "jdk.compiler" for line in mods.splitlines()):
        print("[!] 这不是完整 JDK（缺 jdk.compiler）：%s" % first)
        return False
    print("[OK] JDK 就绪：%s（含 jdk.compiler）" % first)
    # 注入 PATH 后，后续脚本里的裸 `java` 也能命中
    os.environ["JAVA_HOME"] = jdk_home
    prepend_path(jdk_bin_dir(jdk_home))
    save_state(jdk_home=jdk_home, java_version=first)
    return True


def main():
    print("=== JDK 准备 ===\n")

    st, st_path = java_home_status()
    if st == "stale":
        print("[!] JAVA_HOME 指向不存在的目录：%s" % st_path)
        print("    REA 会优先用 JAVA_HOME\\bin\\java.exe 找 java，这个值不改，JADX 起来会失败。")

    homes = [os.path.dirname(os.path.dirname(os.path.realpath(exe))) for exe in java_exe_paths()]
    homes.extend(extracted_jdks(install_dir()))
    for home in dict.fromkeys(homes):
        if apply_java(home):
            print("[OK] 复用已有 JDK 21，无需下载。")
            return 0

    os_name, arch = api_params()
    if os_name == "windows" and arch == "aarch64":
        print("[!] 注意：Windows arm64 不在 REA 原生支持范围内，仅安装 JDK 仍可能无法分析 APK。")

    url = API.format(os=os_name, arch=arch)
    print("查询 Temurin 21 下载信息：%s" % url)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "rea-setup/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            assets = json.load(r)
    except Exception as e:  # noqa: BLE001
        print("[!] 查询 API 失败：%s" % e)
        return 1
    if not assets:
        print("[!] 未找到匹配的 JDK 21 构建（os=%s arch=%s）。" % (os_name, arch))
        return 1

    pkg = assets[0]["binary"]["package"]
    dl = pkg["link"]
    checksum = pkg.get("checksum", "")
    ext = ".zip" if dl.endswith(".zip") else ".tar.gz"
    dest_base = install_dir()
    os.makedirs(dest_base, exist_ok=True)
    archive = os.path.join(dest_base, "jdk21" + ext)
    expected = int(pkg.get("size", 0))
    print("  下载 %s" % dl)
    print("  约 %d MB，可用断点续传；中断后重跑本脚本会接着下。" % (expected >> 20))

    from dl import download
    if not download(dl, archive, expected_size=expected, checksum=checksum, tries=8):
        print("[!] 多次续传仍未完成。网络恢复后重跑本脚本即可（已下部分会保留）。")
        return 1

    print("  解压到 %s" % dest_base)
    try:
        if ext == ".zip":
            with zipfile.ZipFile(archive) as z:
                z.extractall(dest_base)
        else:
            with tarfile.open(archive) as t:
                t.extractall(dest_base)
    except Exception as e:  # noqa: BLE001
        print("[!] 解压失败：%s" % e)
        return 1
    try:
        os.remove(archive)
    except OSError:
        pass

    root = next((home for home in extracted_jdks(dest_base) if apply_java(home)), None)
    if not root:
        print("[!] 解压后未找到可用的完整 JDK 21。")
        return 1

    print("\n=== 已自动应用到本次运行的环境（后续脚本直接可用）===")
    for line in shell_hint_jdk(root):
        print(line)
    if st == "stale":
        print("\n[!] 你原来的 JAVA_HOME 是坏的，请务必执行上面那条 setx JAVA_HOME，否则 REA 仍会找错 java。")
    print("\n之后可跑：python \"%s\"" % os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "install_android.py"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
