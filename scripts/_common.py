#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""REA 安装脚本的共用工具层。

解决三个跨脚本的老问题：
1. Windows 上子进程输出可能是 GBK，直接用 text=True 解码会崩，报错被吞成 TypeError；
2. install_* 各自下到用户目录后，新 PATH 只打印不生效，下一步仍看到旧环境（重复下载）；
3. 同一套"Node 版本是否合格"的判定散在四个文件里，容易改漏。

约定：所有脚本顶部先 sys.path 插入本目录，再 `from _common import ...`。
"""
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys

IS_WIN = platform.system() == "Windows"

# 便携版 Python（embeddable）的 stdout 默认跟随控制台旧码页，中文会乱码。
# 统一强制 UTF-8 输出，彻底消除 GBK/UTF-8 混用问题。
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ----------------------------- 进程调用 ------------------------------------ #
def _decode(data):
    """按 utf-8 → 本地编码 的顺序解码；全失败则替换，绝不抛异常。"""
    if not data:
        return ""
    encodings = ["utf-8"]
    if IS_WIN:
        encodings += ["gbk", "cp936"]
    encodings.append(sys.getfilesystemencoding() or "utf-8")
    for enc in encodings:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace")


def run(cmd, timeout=300, shell=True, cwd=None, env=None):
    """执行命令，返回 (returncode, 合并后的输出)。

    始终以二进制读取再自行解码：既避免 GBK/UTF-8 混用崩溃，
    也避免 p.stderr 为 None 时 `p.stdout + p.stderr` 抛 TypeError。
    """
    try:
        p = subprocess.run(
            cmd,
            shell=shell,
            capture_output=True,
            text=False,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
        out = (_decode(p.stdout) + _decode(p.stderr)).strip()
        return p.returncode, out
    except subprocess.TimeoutExpired:
        return 1, "TIMEOUT"
    except Exception as e:  # noqa: BLE001 - 统一转成可读字符串
        return 1, "ERR: %s" % e


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------- 版本判定 ------------------------------------ #
# 与 npm 上 rea-agents 的 engines 字段保持一致：
#   ^22.23.4 || ^24.22.1 || >=26.12.1
NODE_MIN = {22: (23, 4), 24: (22, 1)}
NODE_MIN_ABOVE_26 = (12, 1)   # >=26.12.1


def parse_version(text):
    m = re.search(r"v?(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def node_version_ok(v):
    """唯一版本判定入口，供 doctor / install_node / install_rea 共用。"""
    if not v:
        return False
    major, minor, patch = v
    if major in NODE_MIN:
        return (minor, patch) >= NODE_MIN[major]
    if major > 26:
        return True
    if major == 26:
        return (minor, patch) >= NODE_MIN_ABOVE_26
    return False   # 23 / 25 / 更低一律不支持


def node_requirement_text():
    return "22.x(>=22.23.4) / 24.x(>=24.22.1) / >=26.12.1"


def java_exe_paths():
    """可能可用的 java：先 PATH，再 JAVA_HOME（很多人只设了后者）。"""
    out = []
    w = shutil.which("java")
    if w:
        out.append(w)
    jh = os.environ.get("JAVA_HOME")
    if jh:
        p = os.path.join(jh, "bin", "java.exe" if IS_WIN else "java")
        if os.path.isfile(p):
            out.append(p)
    return out


def java_home_status():
    """JAVA_HOME 状态：missing / stale（指向不存在的目录）/ ok。"""
    jh = os.environ.get("JAVA_HOME")
    if not jh:
        return "missing", ""
    if not os.path.isdir(jh):
        return "stale", jh
    exe = os.path.join(jh, "bin", "java.exe" if IS_WIN else "java")
    return ("ok" if os.path.isfile(exe) else "stale"), jh


def jdk_has_compiler():
    """是否存在含 jdk.compiler 的 java，返回 (ok, note)。"""
    cands = java_exe_paths()
    if not cands:
        note = "java not on PATH"
        if java_home_status()[0] == "stale":
            note += "；JAVA_HOME 指向不存在的目录 %s" % java_home_status()[1]
        return False, note
    for exe in cands:
        rc, out = run('"%s" --list-modules' % exe, timeout=90)
        if "jdk.compiler" in out:
            return True, "jdk.compiler found (%s)" % exe
    return False, "JRE? missing jdk.compiler"


def java_version_text():
    for exe in java_exe_paths():
        rc, out = run('"%s" -version' % exe, timeout=60)
        first = out.splitlines()[0].strip() if out else ""
        if first:
            return first
    return "(unknown)"


# ----------------------------- 工具链状态 ---------------------------------- #
def tools_root():
    """脚本托管工具的根目录。

    默认在用户目录（Windows: %LOCALAPPDATA%\\.rea-tools），可用环境变量
    REA_TOOLS_DIR 覆盖——想把这套 Node/JDK 跟项目一起打包搬迁时很有用。
    """
    override = os.environ.get("REA_TOOLS_DIR")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    if IS_WIN:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.path.expanduser("~")
    return os.path.join(base, ".rea-tools")


def state_path():
    return os.path.join(tools_root(), "env.json")


def load_state():
    try:
        with open(state_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(**kv):
    """合并写入工具链状态，供后续脚本/doctor 复用。"""
    data = load_state()
    for k, v in kv.items():
        if v:
            data[k] = v
        elif k in data:
            data.pop(k)
    os.makedirs(tools_root(), exist_ok=True)
    tmp = state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, state_path())
    return data


def prepend_path(*dirs):
    """把目录插到本进程 PATH 最前，子进程会继承。

    多个目录时**保持传入顺序**：prepend_path(a, b) 之后 PATH 里 a 在 b 前面。
    按真实路径去重。返回实际生效的目录列表。
    """
    applied, targets, seen = [], [], set()
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        key = os.path.normcase(os.path.realpath(d))
        if key in seen:
            continue
        seen.add(key)
        applied.append(d)
        targets.append(key)
    if not applied:
        return []

    cur = os.environ.get("PATH", "")
    tail = [p for p in cur.split(os.pathsep) if p
            and os.path.normcase(os.path.realpath(p)) not in seen]
    os.environ["PATH"] = os.pathsep.join(applied + tail)
    return applied


def node_bin_dir(node_dir):
    """Node 下载包解出的根目录 → 可执行文件所在目录。"""
    return node_dir if IS_WIN else os.path.join(node_dir, "bin")


def jdk_bin_dir(jdk_home):
    return os.path.join(jdk_home, "bin")


def find_installed_node():
    """扫描脚本托管目录，返回最新一个 Node 根目录（含 node.exe / bin/node）。"""
    root = os.path.join(tools_root(), "node")
    if not os.path.isdir(root):
        return None
    cands = []
    for name in os.listdir(root):
        d = os.path.join(root, name)
        if not os.path.isdir(d) or not name.startswith("node-"):
            continue
        exe = os.path.join(node_bin_dir(d), "node.exe" if IS_WIN else "node")
        if os.path.isfile(exe):
            cands.append(d)
    if not cands:
        return None
    # 目录名带版本号，字典序倒排即可取最新
    return sorted(cands, reverse=True)[0]


def find_installed_jdk():
    base = os.path.join(tools_root(), "jdk21")
    if not os.path.isdir(base):
        return None
    for name in sorted(os.listdir(base), reverse=True):
        d = os.path.join(base, name)
        exe = os.path.join(jdk_bin_dir(d), "java.exe" if IS_WIN else "java")
        if os.path.isfile(exe):
            return d
    return None


def apply_toolchain(verbose=False):
    """把脚本装过的 Node/JDK 应用到当前进程，返回应用明细。

    这是"装完能立刻用"的关键：之前每个脚本只打印 PATH 片段，
    下一步脚本读不到，于是 Node 被下两遍、JDK 被下两遍。
    """
    applied = {"node_dir": None, "jdk_home": None, "path_added": []}
    st = load_state()

    node_dir = st.get("node_dir") if st.get("node_dir") and os.path.isdir(st.get("node_dir") or "") else None
    if not node_dir:
        node_dir = find_installed_node()
    applied["node_dir"] = node_dir

    jdk_home = st.get("jdk_home") if st.get("jdk_home") and os.path.isdir(st.get("jdk_home") or "") else None
    if not jdk_home:
        jdk_home = find_installed_jdk()
    applied["jdk_home"] = jdk_home
    if jdk_home:
        os.environ["JAVA_HOME"] = jdk_home

    npm_prefix = st.get("npm_prefix")

    # JAVA_HOME 若指向一个真实存在的 JDK，也把它带进 PATH（不覆盖，只补）
    extra_java = None
    st_jh, st_path = java_home_status()
    if st_jh == "ok":
        extra_java = os.path.join(st_path, "bin")

    # 一次传入、由 prepend_path 保持优先级：node > npm 全局 > jdk
    dirs = []
    if node_dir:
        dirs.append(node_bin_dir(node_dir))
    if npm_prefix and os.path.isdir(npm_prefix):
        dirs.append(npm_prefix)
    if jdk_home:
        dirs.append(jdk_bin_dir(jdk_home))
    elif extra_java:
        dirs.append(extra_java)
    applied["path_added"] = prepend_path(*dirs)

    if verbose:
        for d in applied["path_added"]:
            print("  [env] PATH += %s" % d)
    return applied


def hydrate_rea_env():
    """把脚本记录的路径临时灌进 REA_* 变量，返回被设置的变量名。

    用途：verify.py 要判断"装好的东西能不能用"。若变量只在用户终端里缺失，
    rea doctor 会把已装好的引擎报成 missing，属于假阴性。
    这里只在「变量缺失」或「变量指向不存在的文件」时才补，并让调用方提示用户持久化。
    """
    st = load_state()
    touched = []

    def put(name, value):
        cur = os.environ.get(name)
        if value and (not cur or not os.path.exists(cur)):
            if cur and cur != value:
                print("  [env] %s 原值 %s 不存在，临时改用 %s" % (name, cur, value))
            os.environ[name] = value
            touched.append(name)

    put("REA_JADX_MCP_JAR", st.get("jadx_jar"))
    put("REA_IDA_MCP_CONFIG", st.get("ida_config"))
    if st.get("ida_dir") and not os.environ.get("IDADIR"):
        os.environ["IDADIR"] = st["ida_dir"]
        touched.append("IDADIR")
    return touched


def persist_user_path(dir_to_add):
    """把目录写进**用户级** PATH（不动系统 PATH），幂等。返回 (ok, note)。

    Windows 用 .NET API 读改用户 PATH：比 setx 安全（不会截断、不会把系统段拼进来）。
    Unix 只提示，不擅自改用户 shell 配置。
    """
    if not dir_to_add or not os.path.isdir(dir_to_add):
        return False, "目录不存在：%s" % dir_to_add
    if not IS_WIN:
        return False, "Unix 请自行把 %s 写进 shell 配置" % dir_to_add

    d = os.path.abspath(dir_to_add).rstrip("\\")
    ps = (
        "$d = '%s';"
        "$p = [Environment]::GetEnvironmentVariable('PATH','User');"
        "if ($null -eq $p) { $p = '' };"
        "$parts = $p -split ';' | Where-Object { $_ -ne '' };"
        "if ($parts -notcontains $d) {"
        "  $new = (@($d) + $parts) -join ';';"
        "  [Environment]::SetEnvironmentVariable('PATH',$new,'User');"
        "  Write-Output 'ADDED'"
        "} else { Write-Output 'ALREADY' }"
    ) % d.replace("'", "''")
    rc, out = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                  shell=False, timeout=120)
    if rc != 0:
        return False, (out or "")[:200]
    if "ADDED" in out:
        return True, "已加入用户 PATH：%s" % d
    if "ALREADY" in out:
        return True, "用户 PATH 里已有：%s" % d
    return False, (out or "未知结果")[:200]


def persist_user_env(pairs):
    """把若干变量写进用户级环境（Windows: setx）。返回 (ok, 失败变量列表)。"""
    if not IS_WIN:
        return False, ["Unix 请写 shell 配置（configure_providers.py --persist）"]
    failed = []
    for k, v in pairs:
        rc, out = run('setx %s "%s"' % (k, v), timeout=60)
        if rc == 0:
            print("  [OK] setx %s" % k)
        else:
            failed.append(k)
            print("  [!] setx %s 失败：%s" % (k, (out or "")[:160]))
    return (not failed), failed


def clean_npm_env():
    """返回适合跑 npm 的环境：去掉宿主注入的 NODE_OPTIONS/NODE_PATH 钩子。

    WorkBuddy/IDE 沙箱会往 NODE_OPTIONS 里塞 --require 的 shim，它会拦截 npm 的
    批量删除，导致 `npm install -g` 在回滚阶段报 SAFE_DELETE_BULK_CONFIRM_REQUIRED。
    这不是包的问题，清掉即可。
    """
    env = dict(os.environ)
    removed = []
    for key in ("NODE_OPTIONS", "NODE_PATH"):
        val = env.get(key) or ""
        if val and ("shim" in val.lower() or "sandbox" in val.lower()):
            env.pop(key, None)
            removed.append(key)
    return env, removed


def shell_hint_node(node_dir):
    """给人看的环境设置片段（不自动改用户 shell 配置）。"""
    d = node_bin_dir(node_dir)
    if IS_WIN:
        return [
            '$env:PATH = "%s;$env:PATH"' % d,
            "# 持久化（推荐 GUI 或下面这条二选一，别用 setx PATH，超 1024 字符会被截断）:",
            '#   [Environment]::SetEnvironmentVariable("PATH", "%s;" + [Environment]::GetEnvironmentVariable("PATH","User"), "User")' % d,
        ]
    return ['export PATH="%s:$PATH"' % d]


def shell_hint_jdk(jdk_home):
    if IS_WIN:
        return [
            '$env:JAVA_HOME = "%s"' % jdk_home,
            '$env:PATH = "%s;$env:PATH"' % jdk_bin_dir(jdk_home),
            '# 持久化: setx JAVA_HOME "%s"' % jdk_home,
        ]
    return [
        'export JAVA_HOME="%s"' % jdk_home,
        'export PATH="$JAVA_HOME/bin:$PATH"',
    ]


def ensure_script_dir_import():
    """把脚本目录加入 sys.path，方便 `python /abs/path/script.py` 也能 import dl/_common。"""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    return here
