#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判断"这次该继续走 CLI，还是该提醒用户 /rea on"。

为什么需要它：常驻 MCP 的唯一好处是**复用已打开的引擎**。只有"同一个大目标上
反复调用"或"单个大文件"才值回票价；一次性小文件走 CLI 更快也更省上下文。

用法：
    python scripts/advise.py <文件路径> [--calls N] [--last-seconds S] [--json]

退出码 0 = 继续 CLI；1 = 建议 /rea on。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import run  # noqa: E402

MB = 1024 * 1024

# 阈值（都基于实测：kernel32.dll 约 700KB，单次 CLI 分析 15-20 秒）
BIG_FILE_MB = {
    "apk": 20,
    "pe": 20,
    "elf": 20,
    "macho": 20,
    "zip": 20,
    "default": 20,
}
SLOW_SECONDS = 60      # 单次 CLI 超过这个时间，说明每次重开数据库代价明显
ROUNDS = 3             # 同一目标第 3 次 CLI 调用起，建议切常驻

EXT_KIND = {
    ".apk": "apk", ".aab": "apk", ".dex": "apk",
    ".exe": "pe", ".dll": "pe", ".sys": "pe", ".ocx": "pe",
    ".so": "elf", ".dylib": "macho", ".elf": "elf",
    ".i64": "idb", ".idb": "idb",
}


def kind_of(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in EXT_KIND:
        return EXT_KIND[ext]
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except OSError:
        return "unknown"
    if head[:2] == b"MZ":
        return "pe"
    if head == b"\x7fELF":
        return "elf"
    if head[:2] == b"PK":
        return "apk"   # 也可能是 zip；apk 判定由调用方补充
    return "unknown"


def advise(path, calls=0, last_seconds=None):
    """返回 (suggest_on: bool, reason: str, facts: dict)。"""
    facts = {"path": path, "exists": os.path.isfile(path)}
    if not facts["exists"]:
        return False, "路径不存在，无法判断", facts

    size = os.path.getsize(path)
    kind = kind_of(path)
    facts.update({"size_bytes": size, "size_mb": round(size / MB, 2), "kind": kind})

    limit_mb = BIG_FILE_MB.get(kind, BIG_FILE_MB["default"])
    facts["threshold_mb"] = limit_mb
    facts["calls"] = calls
    facts["last_seconds"] = last_seconds

    if kind in ("idb",):
        return False, "这是 .idb/.i64 数据库，REA 只接受原始二进制，先换回原文件", facts

    reasons = []
    if size / MB >= limit_mb:
        reasons.append("文件 %.1f MB ≥ %d MB，每次重开数据库代价高" % (size / MB, limit_mb))
    if last_seconds and last_seconds >= SLOW_SECONDS:
        reasons.append("上次 CLI 调用耗时 %ds ≥ %ds" % (int(last_seconds), SLOW_SECONDS))
    if calls + 1 >= ROUNDS:
        reasons.append("同一目标已调用 %d 次，第 %d 次起建议复用引擎" % (calls, calls + 1))

    if reasons:
        return True, "；".join(reasons), facts
    return False, ("目标 %.1f MB、第 %d 次调用，CLI 够用" % (size / MB, calls + 1)), facts


def main():
    ap = argparse.ArgumentParser(description="建议继续 CLI 还是 /rea on")
    ap.add_argument("path")
    ap.add_argument("--calls", type=int, default=0, help="这个目标上已完成的 CLI 调用次数")
    ap.add_argument("--last-seconds", type=float, default=None, help="上一次 CLI 调用耗时（秒）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    suggest, reason, facts = advise(args.path, args.calls, args.last_seconds)
    if args.json:
        print(json.dumps({"suggest_on": suggest, "reason": reason, "facts": facts},
                         ensure_ascii=False))
    else:
        if facts["exists"]:
            print("文件：%s  %.1f MB  %s" % (os.path.basename(args.path),
                                            facts["size_mb"], facts["kind"]))
        print("结论：%s" % ("建议 /rea on" if suggest else "继续 CLI"))
        print("原因：%s" % reason)
        if suggest:
            print("提示语（可直接发给用户）：这个目标建议常驻加速，要我 /rea on 吗？"
                  "（会占 99 个工具的位置，改完需重启）")
    return 1 if suggest else 0


if __name__ == "__main__":
    sys.exit(main())
