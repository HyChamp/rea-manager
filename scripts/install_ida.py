#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键安装并配置 IDA 桥接（ida-pro-mcp / idalib）供 REA 使用。

可自动：探测 IDA 路径、激活 idalib、定位/安装桥接、生成 headless 注册 JSON、真实验证。
不可自动：IDA Pro 本体（商业软件，用户自备）。

关键事实（实测得来，不是照抄上游文档）：
1. **ida-pro-mcp 必须跑在 IDA 自带的那套 Python 上**。idalib.dll 与 `ida_*.pyd` 是按
   IDA 内嵌 Python 的 ABI 编的（这台 9.4 是 3.11），用 3.12/3.13 建 venv 会在加载时报
   DLL/ABI 不匹配。所以这里优先用 `<IDADIR>/python3xx/python.exe`。
2. **激活脚本不需要 uv**。py-activate-idalib.py 只是把 ida-install-dir 写进
   `%APPDATA%\\Hex-Rays\\IDA Pro\\ida-config.json`；而且只要 IDADIR 指向安装目录，
   idalib 不激活也能 open_database（已验证）。uv 只在拿不到 IDA Python 时兜底。
3. IDA 是绿色安装（不在 Program Files）时，要能在盘符根目录扫到，否则必须 --ida-dir。
"""
import argparse
import glob
import json
import os
import platform
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (  # noqa: E402
    IS_WIN, apply_toolchain, load_state, run, save_state, tools_root,
)

# 固定到具体 commit（不可变）；只有内置包缺失时才走网络。
MCP_COMMIT = "13e480cb1198f923154b63669e8cb2faee5deaea"
MCP_REPO_ZIP = ("https://github.com/mrexodia/ida-pro-mcp/archive/%s.zip" % MCP_COMMIT)
VENV_DIR_NAME = "ida-mcp-venv"
MCP_MODULE = "ida_pro_mcp.idalib_supervisor"
# REA 的 headless 走 database-supervisor profile，缺这些工具会在分析时直接报
# "headless compatibility profile is missing tools: idb_close"。
REQUIRED_TOOLS = {"idb_open", "idb_list", "idb_close"}

PROBE_CODE = (
    "import os,sys;"
    "os.environ.setdefault('IDADIR', sys.argv[1]);"
    "import idapro;"
    "v=idapro.get_library_version();"
    "print('IDALIB_OK', v if v else '')"
)

TOOLS_PROBE = (
    "import os,sys;"
    "os.environ.setdefault('IDADIR', sys.argv[1]);"
    "import ida_pro_mcp.idalib_supervisor as s;"
    "names=sorted(n for n in dir(s) if n.islower() and callable(getattr(s,n)) and not n.startswith('_'));"
    "print('TOOLS', ','.join(names))"
)


# ----------------------------- IDA 探测 ------------------------------------ #
def _user_config_ida_dir():
    """读 IDA 自己的 ida-config.json（py-activate-idalib.py 写的那份）。"""
    base = os.environ.get("IDAUSR")
    if not base:
        if IS_WIN:
            appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
            base = os.path.join(appdata, "Hex-Rays", "IDA Pro")
        else:
            base = os.path.join(os.path.expanduser("~"), ".idapro")
    cfg = os.path.join(base, "ida-config.json")
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            p = (json.load(f).get("Paths") or {}).get("ida-install-dir") or ""
        return p if p else None
    except Exception:
        return None


SKIP_DIRS = {
    "windows", "program files", "program files (x86)", "programdata", "users",
    "$recycle.bin", "system volume information", "recovery", "perflogs",
    "node_modules", ".git", ".svn", "appdata", "temp", "tmp",
    "$windows.~ws", "$windows.~bt", "windows.old", "documents and settings",
}


def _looks_like_ida(path):
    return (os.path.isdir(os.path.join(path, "idalib", "python"))
            or any(os.path.isfile(os.path.join(path, n)) for n in ("ida.exe", "ida64.exe", "ida")))


def _drive_root_scan(max_dirs=4000):
    """扫盘符根目录及其一层子目录，找绿色安装的 IDA（如 G:\\WOBANGNI\\IDA94ProBable_CN）。

    只 listdir、不递归遍历文件，并设上限，避免在 C: 上卡住。
    """
    if not IS_WIN:
        return []
    hits = []
    checked = 0
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        root = letter + ":\\"
        if not os.path.isdir(root):
            continue
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        for name in entries:
            p = os.path.join(root, name)
            if not os.path.isdir(p) or name.lower() in SKIP_DIRS:
                continue
            checked += 1
            if _looks_like_ida(p):
                hits.append(p)
                continue
            # 第二层：常见于“工具总目录/软件目录”下面再放 IDA
            try:
                subs = os.listdir(p)
            except OSError:
                continue
            for sub in subs:
                q = os.path.join(p, sub)
                if not os.path.isdir(q):
                    continue
                checked += 1
                if _looks_like_ida(q):
                    hits.append(q)
                if checked >= max_dirs:
                    return hits
    return hits


def detect_ida_dir(explicit=None):
    """探测 IDA 安装目录（含 ida.exe / idalib/python 的那一层）。"""
    candidates = []
    if explicit:
        candidates.append(explicit)
    for env_name in ("IDADIR", "IDA_HOME"):
        env = os.environ.get(env_name)
        if env:
            candidates.append(env)
    cfg = _user_config_ida_dir()
    if cfg:
        candidates.append(cfg)

    system = platform.system()
    if system == "Windows":
        bases = [os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
        for base in bases:
            for pat in ("IDA Professional *", "IDA Pro *", "IDA Freeware *", "IDA Free *", "IDA *"):
                candidates += sorted(glob.glob(os.path.join(base, pat)), reverse=True)
    elif system == "Darwin":
        for pat in ("/Applications/IDA Professional *.app/Contents/MacOS",
                    "/Applications/IDA Pro *.app/Contents/MacOS",
                    "/Applications/IDA Freeware *.app/Contents/MacOS"):
            candidates += sorted(glob.glob(pat), reverse=True)
    else:
        candidates += sorted(glob.glob("/opt/idapro-*"), reverse=True)
        candidates += sorted(glob.glob(os.path.expanduser("~/idapro-*")), reverse=True)

    candidates += _drive_root_scan()

    seen = set()
    for c in candidates:
        if not c:
            continue
        c = os.path.abspath(os.path.expanduser(c))
        if c in seen:
            continue
        seen.add(c)
        if not os.path.isdir(c):
            continue
        has_py = os.path.isdir(os.path.join(c, "idalib", "python"))
        has_exe = any(os.path.isfile(os.path.join(c, n)) for n in ("ida.exe", "ida64.exe", "ida"))
        if has_py or has_exe:
            return c
    return None


# --------------------------- IDA 自带 Python ------------------------------- #
def ida_python_candidates(ida_dir):
    pats = ["python*/python.exe", "python*.exe", "python3*/python.exe"]
    out = []
    for pat in pats:
        out += sorted(glob.glob(os.path.join(ida_dir, pat)), reverse=True)
    # 去重保序
    seen, uniq = set(), []
    for p in out:
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def probe_python(exe, ida_dir, timeout=180):
    """这个解释器能否加载 idalib（ABI 对不对）。返回版本元组或 None。"""
    env = dict(os.environ)
    env["IDADIR"] = ida_dir
    try:
        p = subprocess.run([exe, "-c", PROBE_CODE, ida_dir], capture_output=True,
                           timeout=timeout, env=env)
        from _common import _decode  # noqa: PLC0415
        out = (_decode(p.stdout) + _decode(p.stderr))
    except Exception:
        return None
    if "IDALIB_OK" not in out:
        return None
    line = [l for l in out.splitlines() if "IDALIB_OK" in l][0]
    nums = "".join(ch if ch.isdigit() or ch == "," else " " for ch in line.split("IDALIB_OK")[1])
    parts = [int(x) for x in nums.replace(" ", "").split(",") if x.strip().isdigit()]
    return tuple(parts) if parts else (0, 0, 0)


def pick_ida_python(ida_dir):
    """优先 IDA 自带且能加载 idalib 的 Python。"""
    for exe in ida_python_candidates(ida_dir):
        ver = probe_python(exe, ida_dir)
        if ver:
            print("[OK] 可用 IDA 自带 Python：%s（idalib %s）" % (exe, ".".join(map(str, ver))))
            return exe
    return None


def probe_tools(py, ida_dir):
    """列出桥接模块注册的工具名。拿不到返回 None。"""
    env = dict(os.environ)
    env["IDADIR"] = ida_dir
    try:
        p = subprocess.run([py, "-c", TOOLS_PROBE, ida_dir], capture_output=True,
                           timeout=180, env=env)
        from _common import _decode  # noqa: PLC0415
        out = _decode(p.stdout) + _decode(p.stderr)
    except Exception:
        return None
    for line in out.splitlines():
        if line.startswith("TOOLS "):
            return {n for n in line[6:].strip().split(",") if n}
    return None


def ensure_supervisor_profile(py, ida_dir):
    """确保桥接带 database-supervisor 工具（idb_open/idb_list/idb_close）。

    上游 main 分支有 idb_close，但发行/随 IDA 打包的 2.0.0 快照可能没有；
    缺了它 REA 会在真正分析时拒绝，所以必须在安装阶段就探到并升级。
    """
    tools = probe_tools(py, ida_dir)
    if tools is None:
        print("[!] 无法探测桥接工具清单（模块导入失败），跳过 profile 检查。")
        return True
    missing = sorted(REQUIRED_TOOLS - tools)
    if not missing:
        print("[OK] 桥接工具完整（含 %s）" % "/".join(sorted(REQUIRED_TOOLS)))
        return True

    print("[!] 桥接缺少 %s —— REA 的 headless 会在分析阶段直接失败。" % ", ".join(missing))
    print("    从上游 main 升级（只动 ida_pro_mcp，不动 idapro；先备份旧包）…")
    site = _site_packages_of(py)
    backup = None
    if site:
        src = os.path.join(site, "ida_pro_mcp")
        if os.path.isdir(src):
            backup = src + ".bak-" + time.strftime("%Y%m%d%H%M%S")
            try:
                shutil.copytree(src, backup)
                print("    已备份到 %s" % backup)
            except Exception as e:  # noqa: BLE001
                print("    [!] 备份失败（继续安装）：%s" % e)
                backup = None

    ok = _pip_force_reinstall(py, ida_dir)
    tools2 = probe_tools(py, ida_dir) if ok else None
    if tools2 is not None and not (REQUIRED_TOOLS - tools2):
        print("[OK] 升级完成，工具已齐全。")
        return True

    print("[!] 升级后仍缺 %s。" % ", ".join(sorted(REQUIRED_TOOLS - (tools2 or set()))))
    if backup and site:
        try:
            shutil.rmtree(os.path.join(site, "ida_pro_mcp"), ignore_errors=True)
            shutil.copytree(backup, os.path.join(site, "ida_pro_mcp"))
            print("    已回滚到备份版本：%s" % backup)
        except Exception as e:  # noqa: BLE001
            print("    [!] 回滚失败：%s" % e)
    print("    手工处理：\"%s\" -m pip install --force-reinstall --no-deps \"%s\"" % (py, MCP_REPO_ZIP))
    return False


def _site_packages_of(py):
    """问目标解释器要 site-packages 路径（IDA 内嵌 Python 有自己的布局）。"""
    code = "import sysconfig;print('SITE',sysconfig.get_path('purelib'))"
    try:
        p = subprocess.run([py, "-c", code], capture_output=True, timeout=60)
        from _common import _decode  # noqa: PLC0415
        out = _decode(p.stdout)
    except Exception:
        return None
    for line in out.splitlines():
        if line.startswith("SITE "):
            path = line[5:].strip()
            return path if os.path.isdir(path) else None
    return None


def _pip_force_reinstall(py, ida_dir):
    env = dict(os.environ)
    env["IDADIR"] = ida_dir
    try:
        p = subprocess.run(
            [py, "-m", "pip", "install", "--force-reinstall", "--no-deps", "--no-cache-dir",
             "--disable-pip-version-check", MCP_REPO_ZIP],
            capture_output=True, timeout=1800, env=env)
    except Exception as e:  # noqa: BLE001
        print("[!] pip 执行失败：%s" % e)
        return False
    if p.returncode != 0:
        from _common import _decode  # noqa: PLC0415
        print("[!] pip 失败：%s" % ((_decode(p.stderr) or _decode(p.stdout))[-800:]))
        return False
    return True


# ----------------------------- 桥接定位 ------------------------------------ #
def find_bridge(ida_dir):
    """返回 (command, args_prefix, 说明)。优先 IDA 环境里已装好的。"""
    # 1) IDA python 的 Scripts 里已有 idalib-mcp.exe
    for pat in ("python*/Scripts/idalib-mcp.exe", "Scripts/idalib-mcp.exe",
                "python*/bin/idalib-mcp", "bin/idalib-mcp"):
        for exe in sorted(glob.glob(os.path.join(ida_dir, pat)), reverse=True):
            if os.path.isfile(exe):
                return exe, [], "IDA 自带环境里已装的 idalib-mcp"
    # 2) venv 里装过的
    vdir = os.path.join(tools_root(), VENV_DIR_NAME)
    exe = (os.path.join(vdir, "Scripts", "idalib-mcp.exe") if IS_WIN
           else os.path.join(vdir, "bin", "idalib-mcp"))
    if os.path.isfile(exe):
        return exe, [], "脚本专用 venv"
    # 3) PATH 上
    w = shutil.which("idalib-mcp")
    if w:
        return w, [], "PATH"
    return None, None, None


def module_available(py, ida_dir):
    env = dict(os.environ)
    env["IDADIR"] = ida_dir
    try:
        p = subprocess.run([py, "-c", "import %s" % MCP_MODULE], capture_output=True,
                           timeout=120, env=env)
        return p.returncode == 0
    except Exception:
        return False


VENDOR_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor")


def site_packages_dir(py):
    """问目标 Python 自己的 site-packages 在哪（避免猜路径）。"""
    code = "import sysconfig;print(sysconfig.get_paths()['purelib'])"
    rc, out = run('"%s" -c "%s"' % (py, code), timeout=120)
    if rc == 0 and out.strip() and os.path.isdir(out.strip()):
        return out.strip()
    return None


def install_bridge_from_vendor(py, ida_dir):
    """离线安装：把内置的 ida_pro_mcp 拷进 IDA 自带 Python 的 site-packages。

    优点：不联网、版本确定（2.0.0，已验证含 idb_open/idb_list/idb_close）。
    纯 Python 包，拷目录即可；用的是 `python -m`，不需要生成 .exe 启动器。
    """
    src = os.path.join(VENDOR_DIR, "ida_pro_mcp")
    if not os.path.isdir(src):
        return False
    dst_root = site_packages_dir(py)
    if not dst_root:
        print("[!] 读不到目标 Python 的 site-packages，跳过离线安装。")
        return False
    dst = os.path.join(dst_root, "ida_pro_mcp")
    if os.path.isdir(dst):
        shutil.rmtree(dst, ignore_errors=True)
    try:
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    except Exception as e:  # noqa: BLE001
        print("[!] 拷贝内置包失败：%s" % e)
        return False
    print("[OK] 已从内置包安装 ida_pro_mcp（离线，版本固定）→ %s" % dst)
    return True


def install_bridge(py, ida_dir):
    """装桥接：优先内置包（离线），其次固定 commit 的网络包。"""
    if install_bridge_from_vendor(py, ida_dir):
        return True
    print("内置包不可用，回退到网络下载（固定 commit %s）…" % MCP_COMMIT[:10])
    env = dict(os.environ)
    env["IDADIR"] = ida_dir
    local_wheel = glob.glob(os.path.join(ida_dir, "idalib", "python", "idapro-*.whl"))
    cmds = []
    if local_wheel:
        cmds.append([py, "-m", "pip", "install", "--disable-pip-version-check", local_wheel[0]])
    cmds.append([py, "-m", "pip", "install", "--disable-pip-version-check", MCP_REPO_ZIP])
    for cmd in cmds:
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=1800, env=env)
        except Exception as e:  # noqa: BLE001
            print("[!] 安装失败：%s" % e)
            return False
        if p.returncode != 0:
            from _common import _decode  # noqa: PLC0415
            print("[!] pip 失败：%s" % (_decode(p.stderr) or _decode(p.stdout))[-800:])
            return False
    print("[OK] ida-pro-mcp 安装完成。")
    return True


def activate_idalib(ida_dir, py=None):
    """写 ida-config.json。没有 uv 也能做：这就是个纯 Python 脚本。"""
    script = None
    for pat in ("idalib/python/py-activate-idalib.py", "python/py-activate-idalib.py"):
        cand = os.path.join(ida_dir, pat)
        if os.path.isfile(cand):
            script = cand
            break
    if not script:
        print("[!] 未找到 py-activate-idalib.py（IDA 版本可能低于 9.x，不支持 idalib）。")
        return False
    runner = py or sys.executable
    try:
        p = subprocess.run([runner, script, "--ida-install-dir", ida_dir],
                           capture_output=True, timeout=300)
        from _common import _decode  # noqa: PLC0415
        out = (_decode(p.stdout) + _decode(p.stderr)).strip()
        if p.returncode == 0 and "invalid" not in out.lower():
            print("[OK] idalib 已激活（ida-config.json 已指向安装目录）")
            return True
        print("[!] 激活脚本返回异常：%s" % out[-400:])
    except Exception as e:  # noqa: BLE001
        print("[!] 激活失败：%s" % e)
    return False


# ----------------------------- 注册与验证 ---------------------------------- #
def write_register(path, command, args_prefix, ida_dir):
    reg = {
        "command": command,
        "args": list(args_prefix) + ["--stdio", "--max-workers", "1"],
        "mode": "headless",
        "timeoutMs": 300000,
        "env": {"IDADIR": ida_dir},
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)
    print("[OK] 已写入 IDA 注册文件：%s" % path)
    print("     command = %s" % command)
    print("     args    = %s" % " ".join(reg["args"]))
    return reg


def _probe_help(command, args_prefix, ida_dir, timeout=90):
    """静默探测 `<cmd> [args] --help` 是否可运行，返回 (ok, 输出)。"""
    env = dict(os.environ)
    env["IDADIR"] = ida_dir
    try:
        p = subprocess.run([command] + list(args_prefix) + ["--help"],
                           capture_output=True, timeout=timeout, env=env)
        from _common import _decode  # noqa: PLC0415
        out = (_decode(p.stdout) + _decode(p.stderr)).strip()
    except Exception as e:  # noqa: BLE001
        return False, "ERR: %s" % e
    return (p.returncode == 0 or "usage" in out.lower()), out


def ida_script_exe(ida_dir):
    for pat in ("python*/Scripts/idalib-mcp.exe", "python*/bin/idalib-mcp"):
        for exe in sorted(glob.glob(os.path.join(ida_dir, pat)), reverse=True):
            if os.path.isfile(exe):
                return exe
    return None


def ensure_launchers(py, ida_dir, allow_install=True):
    """pip 的 .exe 启动器写死了 python 路径，IDA 目录一搬就失效。

    REA 这边用 python -m 已经不受影响，但用户其它客户端（Claude Code 等）
    仍会用这些 .exe，所以顺手重建。失败不阻塞流程。
    """
    exe = ida_script_exe(ida_dir)
    if not exe:
        return True
    ok, _ = _probe_help(exe, [], ida_dir, timeout=60)
    if ok:
        return True
    print("[!] 检测到 %s 已失效（多半是 IDA 目录搬过家）。" % os.path.basename(exe))
    if not allow_install:
        print("    --no-install 模式不修，跑一次完整安装即可重建。")
        return False
    print("    重建启动器（pip --force-reinstall，约几十秒）…")
    if not _pip_force_reinstall(py, ida_dir):
        print("[!] 重建失败；不影响 REA（它走 python -m），但其它客户端可能仍不可用。")
        return False
    ok2, _ = _probe_help(exe, [], ida_dir, timeout=60)
    print("[%s] 启动器已重建" % ("OK" if ok2 else "!"))
    return ok2


def verify_bridge(command, args_prefix, ida_dir):
    """跑一次 --help：能起来（且不报 ABI 错）才算装好。"""
    env = dict(os.environ)
    env["IDADIR"] = ida_dir
    try:
        p = subprocess.run([command] + list(args_prefix) + ["--help"],
                           capture_output=True, timeout=120, env=env)
        from _common import _decode  # noqa: PLC0415
        out = (_decode(p.stdout) + _decode(p.stderr)).strip()
    except Exception as e:  # noqa: BLE001
        print("[!] 桥接启动失败：%s" % e)
        return False
    ok = p.returncode == 0 or "usage" in out.lower()
    if ok:
        print("[OK] 桥接可执行验证通过：%s" % " ".join(["<bridge>"] + list(args_prefix) + ["--help"]))
    else:
        print("[!] 桥接无法启动：%s" % out[-1000:])
    return ok


def env_lines(register_path, ida_dir):
    return [
        ("REA_IDA_MCP_CONFIG", os.path.abspath(register_path)),
        ("REA_ANALYSIS_PROVIDER", "ida"),
        ("IDADIR", ida_dir),
    ]


def print_env(lines, show_persist=True):
    print("\n=== 在运行 REA 的同一个 shell 里设置 ===")
    if IS_WIN:
        for k, v in lines:
            print('$env:%s = "%s"' % (k, v))
        if show_persist:
            print("\n持久化（新终端生效）：")
            for k, v in lines:
                print('setx %s "%s"' % (k, v))
    else:
        for k, v in lines:
            print('export %s="%s"' % (k, v))
    if show_persist:
        print("\n也可用 configure_providers.py --persist 让脚本写入配置。")


def persist_env(lines):
    """把变量写进用户级环境（Windows: setx；Unix: 交给 configure_providers 的写法）。"""
    if not IS_WIN:
        print("\n[!] Unix 请用：python scripts/configure_providers.py --persist ...（或手动 source）")
        return False
    failed = []
    for k, v in lines:
        rc, out = run('setx %s "%s"' % (k, v), timeout=60)
        if rc == 0:
            print("  [OK] setx %s" % k)
        else:
            failed.append(k)
            print("  [!] setx %s 失败：%s" % (k, (out or "")[:160]))
    if failed:
        print("  [!] 失败项：%s" % ", ".join(failed))
    else:
        print("  [OK] 已写入用户环境变量（新开的终端生效，当前窗口仍是旧值）")
    return not failed


def main():
    ap = argparse.ArgumentParser(description="一键配置 IDA 桥接供 REA 使用")
    ap.add_argument("--ida-dir", help="IDA 安装目录（自动探测失败时手动指定，含 ida.exe 那层）")
    ap.add_argument("--register", default=os.path.join("~", ".rea", "ida-mcp.json"),
                    help="REA 的 IDA 注册 JSON 落点")
    ap.add_argument("--no-install", action="store_true", help="只生成配置，不装/不改任何东西")
    ap.add_argument("--persist", action="store_true", help="同时把三个环境变量写入用户环境（Windows 用 setx）")
    args = ap.parse_args()

    print("=== IDA 桥接一键配置 ===\n")
    apply_toolchain()

    register_path = os.path.expanduser(args.register)
    ida_dir = detect_ida_dir(args.ida_dir)
    if not ida_dir:
        print("[!] 未探测到 IDA 安装目录。已尝试：环境变量 IDADIR/IDA_HOME、")
        print("    程序目录、IDA 自己的 ida-config.json、各盘符根目录。")
        print("    用 --ida-dir 指定，例如：")
        print(r'    python install_ida.py --ida-dir "G:\IDA94ProBable_CN"')
        return 2
    print("[OK] IDA 目录：%s" % ida_dir)

    # 搬家检测：配置里记着旧路径，就说明拷/移过目录，全套路径都需要重写
    prev_dir = (load_state().get("ida_dir") or "").strip()
    if prev_dir and os.path.normcase(os.path.abspath(prev_dir)) != os.path.normcase(ida_dir):
        print("[!] 检测到 IDA 目录已变更：")
        print("      旧：%s%s" % (prev_dir, "" if os.path.isdir(prev_dir) else "（已不存在）"))
        print("      新：%s" % ida_dir)
        print("    自动重写 ida-config / 注册 JSON / 环境变量，并重建失效的启动器…")
    save_state(ida_dir=ida_dir)

    if args.no_install:
        command, prefix, why = find_bridge(ida_dir)
        if not command:
            print("[!] --no-install 模式下没找到可用的 idalib-mcp，先完整跑一次。")
            return 1
        print("[OK] 复用已有桥接（%s）" % why)
        ensure_launchers(None, ida_dir, allow_install=False)
    else:
        py = pick_ida_python(ida_dir)
        if not py:
            print("[!] 找不到能加载 idalib 的 Python。")
            print("    说明：idalib 只能跑在 IDA 内嵌的 Python 上（本机 IDA 用的是它自带的 python3xx）。")
            print("    若 IDA 目录下没有 python3xx\\python.exe，请用官方安装包重装 IDA。")
            return 1

        # 激活是"锦上添花"：IDADIR 设对了，idalib 不激活也能 open_database（已实测）
        if not activate_idalib(ida_dir, py):
            print("    （不影响 headless：REA 会通过注册 JSON 里的 IDADIR 直接加载 idalib）")

        # 优先用 `python -m 模块`：不依赖 Scripts 里的 .exe 启动器——
        # pip 的启动器把 python 绝对路径写死在文件里，IDA 目录一搬就失效。
        if module_available(py, ida_dir):
            command, prefix = py, ["-m", MCP_MODULE]
            print("[OK] 使用 IDA 自带 Python 跑桥接模块（不依赖 Scripts 启动器，搬目录也不失效）")
            if not ensure_supervisor_profile(py, ida_dir):
                return 1
        else:
            command, prefix, why = find_bridge(ida_dir)
            if command:
                print("[OK] 复用已有桥接（%s）：%s" % (why, command))
                if not ensure_supervisor_profile(py, ida_dir):
                    return 1
            else:
                if not install_bridge(py, ida_dir):
                    print("\n[!] 桥接安装失败。手动重试：")
                    print('    "%s" -m pip install "%s"' % (py, MCP_REPO_ZIP))
                    return 1
                if not ensure_supervisor_profile(py, ida_dir):
                    return 1
                command, prefix = py, ["-m", MCP_MODULE]
                print("[OK] 已安装桥接，使用 python -m 方式")

        if not verify_bridge(command, prefix, ida_dir):
            print("\n[!] 装是装上了，但启动不了，注册不会生效。")
            print("    常见原因：IDA 未激活 / IDA <9.x 无 idalib / 缺少 VC 运行库。")
            print('    手工验证：$env:IDADIR="%s"; "%s" %s --help' % (
                ida_dir, command, " ".join(prefix)))
            return 1

        ensure_launchers(py, ida_dir)

    write_register(register_path, command, prefix or [], ida_dir)
    save_state(ida_config=register_path)

    env_cfg = os.environ.get("REA_IDA_MCP_CONFIG")
    if env_cfg and os.path.abspath(os.path.expanduser(env_cfg)) != os.path.abspath(register_path):
        print("[!] 注意：当前环境 REA_IDA_MCP_CONFIG 指向 %s" % env_cfg)
        print("    与刚写入的 %s 不是同一个文件，REA 会读前者。" % register_path)
        if not os.path.isfile(os.path.expanduser(env_cfg)):
            print("    该路径不存在（多半是模板占位符），请改指向或删掉：")
            print('    setx REA_IDA_MCP_CONFIG "%s"' % register_path)

    lines = env_lines(register_path, ida_dir)
    print_env(lines, show_persist=not args.persist)
    if args.persist:
        print("\n=== 写入用户环境变量 ===")
        persist_env(lines)

    print("\n=== 验证 ===")
    print("  python scripts/verify.py --require ida")
    print("  rea function /abs/path/program.exe main --json")
    print("\n提示：传给 REA 的必须是原始二进制，不能是 .idb / .i64（REA 会核对 SHA-256）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
