#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安装两个 skill，**不动客户端 MCP 注册**：

1. REA 官方说明书 skill（reverse-engineer-anything）：
   静态文档（node_modules/rea-agents/skills/reverse-engineer-anything/），
   由 `rea setup --skill-client X` 写进客户端 skills 目录。
2. REA 管理器 skill（rea，即本包自带的 SKILL.md）：
   提供 /rea status|on|off|verify|install 等命令。源目录就是本包的 rea/，
   复制官方 skill 落地时推导出的 skills 根目录，**覆盖旧版前自动备份**。

为什么不用 `rea setup --client X` 一把梭：
- 它会把**当时探测到的** IDADIR / REA_JADX_MCP_JAR 等快照进客户端 MCP 注册，
  换机器或换 JDK 就过期 —— 所以这里只用 `--skill-client`，一行 MCP 配置都不写。
  要常驻工具列表，用 /rea on（toggle.py）按需开关。

顺序要求：先装完环境（Node/JDK/CLI/IDA/JADX）并 verify 通过，再跑本脚本。
"""
import argparse
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import apply_toolchain, run, save_state  # noqa: E402

# 只放我们验证过、且只需要"写一个 skill 目录"的客户端；
# 每个名字都必须是 `rea setup --skill-client` 认的 client id。
MARKERS = {
    "workbuddy": [".workbuddy"],
    "codebuddy": [".codebuddy"],
    "codex": [".codex"],
    "claude_code": [".claude", ".claude.json"],
    "cursor": [".cursor"],
    "opencode": [os.path.join(".config", "opencode")],
    "kilo": [os.path.join(".config", "kilo"), ".kilocode"],
}

# ------------------------- 管理器 skill（本包自带） ------------------------- #
MANAGER_NAME = "rea"
MANAGER_EXCLUDE = shutil.ignore_patterns("install-windows.cmd", "__pycache__", "*.pyc")


def manager_source_dir():
    """管理器 skill 源目录 = 本脚本所在 rea/ 目录（scripts 的上一级）。"""
    here = os.path.dirname(os.path.abspath(__file__))       # .../rea/scripts
    return os.path.dirname(here)                            # .../rea


def known_skills_roots(client=None):
    """已知客户端的 skills 根目录候选（存在才返回）。client 缺省返回全部。"""
    home = os.path.expanduser("~")
    roots = {
        "workbuddy": os.path.join(home, ".workbuddy", "skills"),
        "codebuddy": os.path.join(home, ".codebuddy", "skills"),
        "codex": os.path.join(home, ".agents", "skills"),
        "opencode": os.path.join(home, ".agents", "skills"),
        "claude_code": os.path.join(home, ".claude", "skills"),
        "cursor": os.path.join(home, ".cursor", "skills"),
        "kilo": os.path.join(home, ".config", "kilo", "skills"),
    }
    if client:
        p = roots.get(client)
        return [p] if p and os.path.isdir(p) else []
    return [p for p in roots.values() if os.path.isdir(p)]


def backup_root():
    """备份必须落在 skills 根目录**之外**。

    客户端会把 skills 下每个含 SKILL.md 的目录都索引成一个技能；把
    rea.bak-<时间戳> 留在原地，等于塞进第二个叫 rea 的旧技能，实测
    `/rea` 会命中备份而不是新版。
    """
    d = os.path.join(os.path.expanduser("~"), ".rea", "skill-backups")
    os.makedirs(d, exist_ok=True)
    return d


def sweep_stray_backups(skills_root):
    """把历史版本遗留在 skills 根里的 rea.bak-* 迁到备份区，返回迁走的名字。"""
    moved = []
    try:
        names = os.listdir(skills_root)
    except OSError:
        return moved
    for n in names:
        if not (n.startswith(MANAGER_NAME + ".bak-") and os.path.isdir(os.path.join(skills_root, n))):
            continue
        src = os.path.join(skills_root, n)
        target = os.path.join(backup_root(), n)
        try:
            if os.path.exists(target):
                shutil.rmtree(target)
            shutil.move(src, target)
            moved.append(n)
        except Exception:
            pass
    return moved


def install_manager_into(skills_root, dry_run=False):
    """把管理器 skill 复制到 <skills_root>/rea/。返回 (ok, 说明, 落点)。

    - 覆盖旧版前整体备份到 ~/.rea/skill-backups（不放 skills 根内，避免同名冲突）；
    - 顺手迁走历史遗留的 rea.bak-*，让老安装自愈；
    - 内容完全一致时跳过（幂等）；
    - 源==目标（在 skills 目录副本里自跑）时直接跳过。
    """
    src = manager_source_dir()
    if not os.path.isfile(os.path.join(src, "SKILL.md")):
        return False, "找不到管理器源 SKILL.md：%s" % src, None
    dest = os.path.join(skills_root, MANAGER_NAME)
    # 在 skills 目录副本内自跑时，dest 的父目录就是源目录本身（rea 拷进 rea 自己），跳过
    if os.path.abspath(os.path.dirname(dest)) == os.path.abspath(src):
        return True, "源即目标（正在 skills 目录内运行），跳过", dest
    if dry_run:
        return True, "将安装/更新 %s" % dest, dest
    os.makedirs(skills_root, exist_ok=True)
    for stray in sweep_stray_backups(skills_root):
        print("  [迁移] 旧备份移出 skills 根（避免同名冲突）→ %s" % os.path.join(backup_root(), stray))

    if os.path.isdir(dest):
        old_md = os.path.join(dest, "SKILL.md")
        try:
            with open(old_md, "r", encoding="utf-8", errors="replace") as f:
                old = f.read()
            with open(os.path.join(src, "SKILL.md"), "r", encoding="utf-8", errors="replace") as f:
                new = f.read()
        except Exception:
            old, new = None, None
        if old is not None and old == new:
            return True, "已是最新（%s）" % dest, dest
        backup = os.path.join(backup_root(), "%s-%s" % (MANAGER_NAME, time.strftime("%Y%m%d%H%M%S")))
        shutil.copytree(dest, backup)
        shutil.rmtree(dest)
        print("  [备份] 旧版 rea skill → %s" % backup)
    shutil.copytree(src, dest, ignore=MANAGER_EXCLUDE)
    return True, "已安装 %s" % dest, dest


def detect_clients():
    home = os.path.expanduser("~")
    found = []
    for client, markers in MARKERS.items():
        if any(os.path.exists(os.path.join(home, m)) for m in markers):
            found.append(client)
    return found


def parse_json(text):
    """rea 的输出前面可能有人类可读行，取最后一个能解析的 JSON 对象。"""
    text = (text or "").strip()
    if not text:
        return None
    start = text.find("{")
    while start >= 0:
        try:
            return json.loads(text[start:])
        except Exception:
            start = text.find("{", start + 1)
    return None


def _norm_actions(items):
    """appliedActions 里是字符串 id，plannedActions 里是对象；统一成对象。"""
    out = []
    for a in items or []:
        if isinstance(a, dict):
            out.append(a)
        else:
            sid = str(a)
            out.append({"id": sid,
                        "kind": "install_skill" if sid.startswith("install_skill") else ""})
    return out


def install_one(client, dry_run=False):
    """返回 (ok, 说明, 目标路径或 None)。"""
    inner = "rea setup --skill-client %s --format json" % client
    cmd = (inner + " --dry-run") if dry_run else (inner + " --yes")
    rc, out = run(cmd, timeout=900)
    data = parse_json(out)
    if data is None:
        return False, "输出无法解析（rc=%d）：%s" % (rc, out[-300:]), None

    planned = _norm_actions(data.get("plannedActions"))
    applied = _norm_actions(data.get("appliedActions"))
    status = data.get("status") or "?"
    skills = [a for a in (applied or planned) if a.get("kind") == "install_skill"]
    target = next((a.get("target") for a in skills if a.get("target")), None)

    if rc != 0:
        return False, "rea setup 返回 %d（status=%s）" % (rc, status), target
    if not skills:
        return True, "无需变更（status=%s）" % status, target
    verb = "将安装" if dry_run else "已安装"
    if target and os.path.isfile(os.path.join(target, "SKILL.md")):
        with open(os.path.join(target, "SKILL.md"), "r", encoding="utf-8", errors="replace") as f:
            lines = sum(1 for _ in f)
        return True, "%s %s（SKILL.md %d 行）" % (verb, target, lines), target
    return True, "%s %s" % (verb, target or ",".join(a["id"] for a in skills)), target


def main():
    ap = argparse.ArgumentParser(description="安装两个 REA skill（官方说明书 + /rea 管理器），不含 MCP 注册")
    ap.add_argument("--client", action="append", metavar="NAME",
                    help="指定客户端，可重复；缺省按目录标记自动探测")
    ap.add_argument("--detect", action="store_true", help="显式要求自动探测（与缺省行为相同）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不写文件")
    ap.add_argument("--list", action="store_true", help="只列出探测结果，不安装")
    ap.add_argument("--no-manager-skill", action="store_true", help="跳过管理器 skill（rea），只装官方说明书")
    args = ap.parse_args()

    apply_toolchain()
    print("=== REA skills（官方说明书 + /rea 管理器）===")
    print("（只写各客户端的 skills 目录，不写任何客户端 MCP 注册）\n")

    if not run("rea --version", timeout=120)[1].strip():
        print("[!] 找不到 rea CLI。先跑 install_rea.py 把环境装好，再来装 skill。")
        return 2

    clients = args.client or detect_clients()
    if args.list:
        print("探测到：%s" % (", ".join(clients) or "(无)"))
        print("支持的：%s" % ", ".join(MARKERS))
        return 0
    if not clients:
        print("[!] 没探测到任何已知客户端。用 --client workbuddy 之类显式指定。")
        return 2

    print("目标客户端：%s\n" % ", ".join(clients))
    done, failed, seen_targets, client_targets = [], [], {}, {}
    for c in clients:
        ok, note, target = install_one(c, dry_run=args.dry_run)
        # 有的客户端共用同一个 skills 目录（如 codex 与 opencode → ~/.agents/skills）
        if ok and target and target in seen_targets:
            print("[OK] %-12s → 与 %s 同一个目录，已处理，跳过" % (c, seen_targets[target]))
            done.append(c)
            client_targets[c] = target
            continue
        print("[%s] %-12s %s" % ("OK" if ok else "!", c, note))
        if ok:
            done.append(c)
            if target:
                seen_targets[target] = c
                client_targets[c] = target
        else:
            failed.append(c)

    if done and not args.dry_run:
        save_state(skill_clients=done)

    # ---------------- 管理器 skill（/rea 命令的来源） ---------------- #
    manager_results = []
    if args.no_manager_skill:
        print("\n[skip] 管理器 skill（由 --no-manager-skill 指定）")
    else:
        print("\n=== REA 管理器 skill（/rea status/on/off/verify/install）===")
        for c in clients:
            if c not in done:
                continue
            target = client_targets.get(c)
            if target and os.path.isfile(os.path.join(target, "SKILL.md")):
                skills_root = os.path.dirname(os.path.abspath(target))
            else:
                roots = known_skills_roots(c)
                if not roots:
                    print("[skip] %-12s 未找到已知 skills 目录" % c)
                    continue
                skills_root = roots[0]
            ok, note, dest = install_manager_into(skills_root, dry_run=args.dry_run)
            print("[%s] %-12s %s" % ("OK" if ok else "!", c, note))
            manager_results.append((c, ok, dest))

    print()
    if failed:
        print("失败 %d 个：%s" % (len(failed), ", ".join(failed)))
        print("常见原因：该客户端已有归属不明的 rea 注册（REA 拒绝动它的配置），")
        print("或该客户端不支持 skill 安装。可以只对单个客户端重试：")
        print("  python scripts/install_skill.py --client workbuddy")
        return 1
    if args.dry_run:
        print("干跑完成，没有写任何文件。")
        return 0
    print("完成。两个 skill 已落到各客户端的 skills 目录，重启客户端后生效。")

    # 真装时 appliedActions 只有 id、没有路径，这里统一扫一遍落地位置做确认
    found = []
    home = os.path.expanduser("~")
    for root in (".workbuddy", ".codebuddy", ".agents", ".cursor",
                 os.path.join(".config", "kilo")):
        for name in ("reverse-engineer-anything", MANAGER_NAME):
            p = os.path.join(home, root, "skills", name, "SKILL.md")
            if os.path.isfile(p):
                found.append(p)
    if found:
        print("\n落地确认：")
        for p in found:
            print("  ✓ %s" % p)
    print("\n注意：两个 skill 都只是文档与命令入口，不会让客户端多出 MCP 工具；")
    print("      要常驻工具列表请用 /rea on（会写 mcp 配置，需重启客户端）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
