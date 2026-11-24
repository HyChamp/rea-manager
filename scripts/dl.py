#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公共下载器：断点续传 + 多次重试 + SHA-256 校验。供各 install 脚本复用。

设计要点（都是踩过的坑）：
- 校验不过必须删除文件，否则下次「已有字节 >= 期望长度」会直接跳过下载，
  拿同一份坏文件反复校验失败，形成永久死循环。
- 416（Range 越界）说明本地文件比远端还大或已损坏，删掉重下。
- 失败之间要有退避，不要 30 次瞬间打满。
"""
import hashlib
import os
import sys
import time
import urllib.error
import urllib.request


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def _discard(path, why):
    try:
        os.remove(path)
        print("  已删除本地文件（%s），下次运行会重新下载。" % why)
    except OSError:
        pass


def download(url, dest, expected_size=0, checksum=None, tries=30):
    """断点续传下载；完成后如给 checksum 则校验。返回 bool。

    True  = 文件完整（且给了 checksum 时校验通过）
    False = 未完成/校验失败；本地文件已清理，重跑等同全新下载
    """
    for attempt in range(1, tries + 1):
        have = os.path.getsize(dest) if os.path.exists(dest) else 0

        # 本地文件比目标还大 → 一定坏了
        if expected_size and have > expected_size:
            _discard(dest, "比目标长度更大，文件已损坏")
            have = 0

        if expected_size and have >= expected_size:
            break

        headers = {"User-Agent": "Mozilla/5.0 rea-setup"}
        if have:
            headers["Range"] = "bytes=%d-" % have
            print("  续传：已有 %d MB（第 %d/%d 次）" % (have >> 20, attempt, tries))
        else:
            print("  下载中（第 %d/%d 次）" % (attempt, tries))

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=120) as r:
                mode = "ab" if have and r.status == 206 else "wb"
                if mode == "wb":
                    have = 0
                length = r.headers.get("Content-Length")
                total = have + (int(length) if length and length.isdigit() else 0)
                with open(dest, mode) as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        have += len(chunk)
                        if total:
                            sys.stdout.write("\r  %d%% (%d/%d MB)" % (
                                min(100, have * 100 // total), have >> 20, total >> 20))
                            sys.stdout.flush()
            print()
        except urllib.error.HTTPError as e:
            print("\n  中断：HTTP %s %s" % (e.code, e.reason))
            if e.code == 416 and os.path.exists(dest):
                _discard(dest, "服务端拒绝续传区间（416）")
                have = 0
            elif e.code in (403, 404):
                print("[!] 链接不可用（%d），重试没有意义。" % e.code)
                return False
        except Exception as e:
            print("\n  中断：%s" % e)

        now = os.path.getsize(dest) if os.path.exists(dest) else 0
        if expected_size and now >= expected_size:
            break
        if not expected_size and now > 0:
            break
        time.sleep(min(5, 1 + attempt))

    if not os.path.exists(dest) or os.path.getsize(dest) == 0:
        print("[!] 下载失败，未产生文件。")
        return False

    if expected_size and os.path.getsize(dest) < expected_size:
        print("[!] 文件不完整（%d/%d MB），网络恢复后重跑可续传。" % (
            os.path.getsize(dest) >> 20, expected_size >> 20))
        return False

    if checksum:
        actual = sha256(dest)
        if actual.lower() != checksum.lower():
            print("[!] 校验失败：期望 %s 实际 %s" % (checksum, actual))
            # 关键：留着这个文件会让下次运行直接跳过下载，永远失败。必须删。
            _discard(dest, "SHA-256 不匹配")
            return False
        print("[OK] SHA-256 校验通过。")
    return True
