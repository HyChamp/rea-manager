#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全自动升级 Node.js 到 24 LTS：下载官方绿色版到用户目录，校验 SHA-256，解压即用。

不碰系统安装、不需要管理员权限、不改用户 shell 配置。
装完会把新目录注入**当前进程**的 PATH（后续脚本能立刻用），并把位置记到
~/.rea-tools/env.json，供 doctor / 其他脚本复用。
"""
import argparse
import json
import os
import platform
import shutil
import sys
import tarfile
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (  # noqa: E402
    IS_WIN, node_bin_dir, node_version_ok, parse_version,
    persist_user_path, prepend_path, run, save_state, shell_hint_node, tools_root,
)

NODE_INDEX = "https://nodejs.org/dist/index.json"
# 固定版本：实测通过，装出来每次都一样。要追最新可设 REA_NODE_VERSION=latest
NODE_PIN = "v24.22.3"
DIST_BASE = "https://nodejs.org/dist/"
TARGET_MAJOR = 24
MIN_PATCH = (22, 1)          # 24.x 需 >=24.22.1


def matching_version(rc, output, target):
    return rc == 0 and output.strip() == target and node_version_ok(parse_version(target))


def pick_version():
    """默认用固定版本（确定性优先）；REA_NODE_VERSION=latest 时才去查最新。"""
    want = os.environ.get("REA_NODE_VERSION", NODE_PIN)
    if want and want.lower() != "latest":
        return want if want.startswith("v") else "v" + want
    try:
        with urllib.request.urlopen(NODE_INDEX, timeout=20) as r:
            data = json.load(r)
        for item in data:
            v = parse_version(item.get("version", ""))
            if v and v[0] == TARGET_MAJOR and (v[1], v[2]) >= MIN_PATCH:
                # index 按时间倒序，第一个即最新
                return item["version"]
    except Exception as e:  # noqa: BLE001
        print("[!] 读取 Node 版本列表失败：%s" % e)
    return "v24.22.3"  # 查证时的已知最新 LTS 兜底


def platform_tag():
    """返回 (tag, ext, 不支持时的平台说明)。"""
    s = platform.system()
    arch = platform.machine().lower()
    if s == "Windows":
        if "arm" in arch:
            return None, None, "Windows arm64"
        return "win-x64", ".zip", None
    if s == "Darwin":
        return ("darwin-arm64" if "arm" in arch else "darwin-x64"), ".tar.gz", None
    if "arm" in arch or "aarch64" in arch:
        return "linux-arm64", ".tar.xz", None
    return "linux-x64", ".tar.xz", None


def expected_sha(version, fname):
    url = DIST_BASE + version + "/SHASUMS256.txt"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            text = r.read().decode("utf-8", "replace")
        for line in text.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip("*") == fname:
                return parts[0]
    except Exception as e:  # noqa: BLE001
        print("[!] 取校验文件失败：%s" % e)
    return None


def install_dir():
    return os.path.join(tools_root(), "node")


def report_ready(root, version, bin_dir=None):
    """校验准确版本和 npm，再记录实际使用的目录。"""
    bin_dir = bin_dir or node_bin_dir(root)
    node_exe = os.path.join(bin_dir, "node.exe" if IS_WIN else "node")
    rc, out = run('"%s" -v' % node_exe, timeout=60)
    if not matching_version(rc, out, version):
        print("[!] Node 不可用或版本不匹配（目标 %s）：%s" % (version, out or "(无输出)"))
        return False
    print("[OK] 新 Node 就绪：%s" % out.strip())

    npm = os.path.join(bin_dir, "npm.cmd" if IS_WIN else "npm")
    rc2, out2 = run('"%s" -v' % npm, timeout=120)
    if rc2 != 0:
        print("[!] npm 不可用：%s" % (out2 or "(无输出)")[:300])
        return False
    print("[OK] npm 可用：%s" % out2.strip())

    prepend_path(bin_dir)
    save_state(node_dir=root, node_version=out.strip())
    return True


def main():
    ap = argparse.ArgumentParser(description="准备 Node 24 LTS")
    ap.add_argument("--persist-path", action="store_true", help="把 Node 目录写进用户 PATH（幂等）")
    args = ap.parse_args()

    print("=== Node 准备 ===\n")

    version = pick_version()
    parsed = parse_version(version)
    if not node_version_ok(parsed) or version != "v%d.%d.%d" % parsed:
        print("[!] Node 目标版本无效或不兼容：%s" % version)
        return 2
    print("目标 Node：%s" % version)
    which = shutil.which("node")
    if which:
        bin_dir = os.path.dirname(os.path.abspath(which))
        root = bin_dir if IS_WIN else os.path.dirname(bin_dir)
        if (IS_WIN or os.path.basename(bin_dir) == "bin") and report_ready(root, version, bin_dir):
            if args.persist_path:
                ok, note = persist_user_path(bin_dir)
                print("[%s] %s" % ("OK" if ok else "!", note))
            return 0

    tag, ext, unsupported = platform_tag()
    if unsupported:
        print("[!] %s 不在支持范围（REA 的原生组件只提供 x64）。" % unsupported)
        print("    请手动安装 Node 24 LTS 后重跑本脚本。")
        return 2

    folder = "node-%s-%s" % (version, tag)
    fname = folder + ext
    url = DIST_BASE + version + "/" + fname
    dest_base = install_dir()
    root = os.path.join(dest_base, folder)
    if os.path.isdir(root) and report_ready(root, version):
        if args.persist_path:
            ok, note = persist_user_path(node_bin_dir(root))
            print("[%s] %s" % ("OK" if ok else "!", note))
        return 0

    print("本机未找到可用的目标版本，下载 %s 到用户目录…" % version)
    os.makedirs(dest_base, exist_ok=True)
    archive = os.path.join(dest_base, fname)
    print("  下载 %s" % url)

    from dl import download
    checksum = expected_sha(version, fname)
    if not checksum:
        print("[!] 缺少官方 SHA-256，停止下载。")
        return 1
    if not download(url, archive, checksum=checksum):
        print("[!] 下载或校验失败。网络恢复后重跑本脚本（会从断点继续）。")
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

    if not report_ready(root, version):
        return 1
    try:
        os.remove(archive)
    except OSError:
        pass

    print("\n=== 已自动应用到本次运行的环境（后续脚本直接可用）===")
    for line in shell_hint_node(root):
        print(line)
    if args.persist_path:
        ok, note = persist_user_path(node_bin_dir(root))
        print("[%s] %s" % ("OK" if ok else "!", note))
    else:
        print("\n你自己的终端要生效，另开一个 shell 执行上面的片段，或加 --persist-path 自动写入。")
    print("提示：rea-agents 需在这个 Node 环境下重装一次（npm install -g rea-agents）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
