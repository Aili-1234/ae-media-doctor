# -*- coding: utf-8 -*-
"""aemd.everything —— Everything 引擎：服务 / 客户端 / 索引三态（R6 / R7 / §8）。

负责：
  · 找到引擎（冻结版在 `%APPDATA%\\AE素材医生\\engine\\Everything\\everything.exe`）
  · `service_pids()` 与 `client_pids()` **两个独立函数**（R7：永不混用）
  · `index_state()` 返回**三态枚举** `IndexState.OK / EMPTY / UNKNOWN`（R6）
  · `ensure_ready()` 只按三态决定要不要拉起客户端；只有 EMPTY 才谈提权
  · `lookup(names)` 把缺失素材名交给 es.exe 全盘找
  · `stop_bundled()` 退出时只停本程序拉起的客户端

不负责：
  · 扫描目录建索引（scanner.py，那是我们自己的树扫描）
  · 决定「要不要全盘补搜」这个业务开关（runner 决定，本模块只执行一次查找）
  · 任何界面/日志文案（log 回调由调用方给）

依赖：`.const`、`.paths`、`.winproc`（进程）、标准库 subprocess。

═══════════════════ 一代的两个 bug，v2 的结构性防线 ═══════════════════
A) `_index_count()` 把「探测失败」当成「索引条数 0」（`except: return 0`）：
   es.exe 连不上 IPC 时返回 `Error 8: Everything IPC not found`、退出码 8、stdout 不是数字，
   int() 抛异常被吞成 0 → 代码判成「索引是空的」→ 申请管理员权限重建索引 → 每次跑都弹 UAC。
   → v2 用 `IndexState` 三态：**只有确知 EMPTY 才走提权分支**，UNKNOWN 一律不提权、不弹框。
B) 按 exe 路径判「客户端在不在」会把**服务**算成客户端（服务与客户端是同一个 exe）：
   服务在 Session 0、LocalSystem、命令行带 `-svc`；它不提供索引给 es.exe 用。
   → v2 的 `client_pids()` 排除 `-svc` 与 SessionId==0，`service_pids()` 反过来只认它俩；
     两函数各自独立，任何调用点必须明确写自己问的是哪一个。

⚠ 还有一条只有实践能教的事实（2026-11-15 实测）：**非管理员运行时，CIM 对
  Session 0 / LocalSystem 进程返回的 ExecutablePath 与 CommandLine 都是空的**。
  也就是说 `client_pids()` 的路径匹配只在提权后才看得见服务 —— 这恰好是一代"提权后
  才误杀服务"的成因。所以这里**同时**用 SessionId 与命令行两道判据，谁先到都能拦住。
"""

import enum
import os
import shutil
import subprocess
import sys
import time

from . import const, winproc

_NOWINDOW = getattr(subprocess, "CREATE_NO_WINDOW", const.NO_WINDOW)
_BUNDLED_STARTED = False


class IndexState(enum.Enum):
    """索引三态 —— R6 的唯一表达方式（别再用整数/None 混着传）。"""

    OK = "ok"            # 确知有条目（>0）
    EMPTY = "empty"      # 确知一条都没有（==0）→ 唯一允许申请提权的路径
    UNKNOWN = "unknown"  # 探不到（进程没起/超时/输出不是数字/退出码非 0）→ 什么都不做

    def is_ok(self):
        return self is IndexState.OK


# ------------------------------------------------------------------ 引擎定位

def _project_root():
    """源码树的项目根（`main.py` 所在那层）。冻结版这段不会被用到。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def engine_dir():
    r"""引擎目录。冻结版是 `%APPDATA%\AE素材医生\engine\Everything` 下的**副本**。

    为什么用副本而不是 `_internal` 里的原件：Everything.ini 与索引文件要写在引擎旁边，
    写在 Program Files 那种只读位置会失败；副本还让索引跨版本保留，下次不用重建。
    """
    if getattr(sys, "frozen", False):
        dst = os.path.join(const.APP_HOME, "engine", "Everything")
        src = os.path.join(getattr(sys, "_MEIPASS", "") or _project_root(), "engine", "Everything")
        if os.path.isdir(src):
            try:
                os.makedirs(dst, exist_ok=True)
                for name in ("everything.exe", "es.exe", "Everything.lng"):
                    a, b = os.path.join(src, name), os.path.join(dst, name)
                    if os.path.isfile(a) and not (os.path.isfile(b) and os.path.getsize(a) == os.path.getsize(b)):
                        shutil.copy2(a, b)
            except OSError:
                pass
        return dst
    return os.path.join(_project_root(), "engine", "Everything")


def bundled_everything():
    """自带 everything.exe 的完整路径；不存在 → ""（空串，别返回 None）。"""
    p = os.path.join(engine_dir(), "everything.exe")
    return p if os.path.isfile(p) else ""


def find_es():
    """找 es.exe：自带引擎 → PATH → 官方安装位置。找不到 → None。"""
    p = os.path.join(engine_dir(), "es.exe")
    if os.path.isfile(p):
        return p
    p = shutil.which("es.exe")
    if p:
        return p
    for p in (r"C:\Program Files\Everything\es.exe",
              r"C:\Program Files (x86)\Everything\es.exe"):
        if os.path.isfile(p):
            return p
    return None


# ------------------------------------------------------------------ 服务 / 客户端

def _cim_everything():
    """一次 CIM 查询拿到全部 everything.exe 的 (pid, session, path, cmdline)。

    比一代分两次 powershell 便宜，也让"服务 vs 客户端"的判据集中在一个地方。
    拿不到（非管理员、PowerShell 被策略禁、超时）→ []。
    """
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='everything.exe'\" | "
          "ForEach-Object { \"{0}`t{1}`t{2}`t{3}\" -f $_.ProcessId, $_.SessionId, "
          "$_.ExecutablePath, $_.CommandLine }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, creationflags=_NOWINDOW, timeout=20)
        text = (r.stdout or b"").decode("mbcs", "ignore")
    except Exception:
        return []
    rows = []
    for line in text.splitlines():
        parts = line.rstrip("\r\n").split("\t")
        if len(parts) < 4:
            continue
        pid, sess, path, cmd = parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip()
        if not pid.isdigit():
            continue
        rows.append({"pid": int(pid),
                     "session": int(sess) if sess.lstrip("-").isdigit() else None,
                     "path": path,
                     "cmdline": cmd})
    return rows


def service_pids():
    """一切 `everything.exe` 里的**服务**进程（命令行含 -svc 或 SessionId==0）。

    不限路径：服务可能是用户自己装的 Everything，也可能是我们的副本 ——
    本函数只回答"哪些是服务"，杀不杀是调用方的事（而调用方只有一个：不杀）。
    """
    out = []
    for r in _cim_everything():
        cmd = (r.get("cmdline") or "").lower()
        if "-svc" in cmd or (r.get("session") == 0):
            if winproc.is_alive(r["pid"]):
                out.append(r["pid"])
    return out


def client_pids():
    """本程序那份 `everything.exe` 的**客户端**进程（排除 -svc / Session 0）。

    路径必须等于 `bundled_everything()`：用户自己装的 Everything 不算"我们的"，
    否则 `stop_bundled()` 会把用户的东西关掉（U-02 的同类风险）。
    """
    exe = bundled_everything()
    if not exe:
        return []
    want = os.path.normcase(os.path.abspath(exe))
    out = []
    for r in _cim_everything():
        p = (r.get("path") or "")
        if not p or os.path.normcase(os.path.abspath(p)) != want:
            continue
        if "-svc" in (r.get("cmdline") or "").lower() or r.get("session") == 0:
            continue
        if winproc.is_alive(r["pid"]):
            out.append(r["pid"])
    return out


def anything_running():
    """有没有**任何** everything.exe 在跑（含服务，含用户自己装的）。

    只用于"要不要报告索引完全不可用"，**不许**用它判断"我们的客户端在不在"
    —— 那就是一代的 bug B。
    """
    try:
        return bool([p for p in winproc.snapshot_pids("everything.exe") if winproc.is_alive(p)])
    except Exception:
        return False


# ------------------------------------------------------------------ 三态

def index_state(es_exe=None):
    """跑一次 `es.exe -get-result-count`，按退出码/输出判定 IndexState（R6）。

    判据顺序（每一步失败都返回 UNKNOWN，**绝不返回 EMPTY**）：
      没有 es.exe → UNKNOWN；起不来 / 超时 → UNKNOWN；
      退出码非 0 → UNKNOWN（IPC not found 就是这条路）；
      输出空或不是数字 → UNKNOWN；
      解析出 0 → EMPTY（这是唯一允许提权的状态）；>0 → OK。
    """
    es = es_exe if es_exe is not None else find_es()
    if not es:
        return IndexState.UNKNOWN
    try:
        r = subprocess.run([es, "-get-result-count"], capture_output=True,
                           creationflags=_NOWINDOW, timeout=15)
    except Exception:
        return IndexState.UNKNOWN
    if r.returncode != 0:
        return IndexState.UNKNOWN
    try:
        txt = (r.stdout or b"").decode("mbcs", "ignore").strip()
    except Exception:
        return IndexState.UNKNOWN
    if not txt:
        return IndexState.UNKNOWN
    try:
        n = int(txt.split()[0])
    except (ValueError, IndexError):
        return IndexState.UNKNOWN
    return IndexState.EMPTY if n == 0 else IndexState.OK


def ensure_ready(log=None, wait_client_s=12, wait_admin_s=20):
    """确保索引可用：OK 直接返回；UNKNOWN 只尝试拉起客户端；EMPTY 才提权重建。

    返回最终的 `IndexState`（调用方据此决定要不要全盘补搜 / 要不要提示用户）。

    三条不变量（都是一代踩过的）：
      · 拉起判据用 `client_pids()`，**不用** `anything_running()` —— 服务在跑 ≠ es 连得上
      · 提权只在 `state is EMPTY` 时发生；UNKNOWN 永远不弹 UAC（R6 的核心）
      · 等待是分段的：先等客户端自建索引（12×1s），提权后再等（20×1s）
    """
    global _BUNDLED_STARTED
    es = find_es()
    if not es:
        return IndexState.UNKNOWN
    state = index_state(es)
    if state.is_ok():
        return state
    exe = bundled_everything()
    if not exe:
        return state        # 没自带引擎：不拉起、不提权，原样返回
    if not client_pids():
        try:
            subprocess.Popen([exe, "-startup", "-minimized"],
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            _BUNDLED_STARTED = True
        except Exception:
            pass
        if log:
            log("INDEX     启动自带 Everything，等待索引…")
        for _ in range(max(0, int(wait_client_s))):
            time.sleep(1)
            state = index_state(es)
            if state.is_ok():
                return state
    if state is IndexState.EMPTY:
        if log:
            log("INDEX     确知索引为空，申请管理员权限重建（会弹一次 UAC）")
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Start-Process -FilePath '%s' -ArgumentList '-startup','-minimized' -Verb RunAs"
                            % exe], capture_output=True, creationflags=_NOWINDOW, timeout=30)
            _BUNDLED_STARTED = True
        except Exception:
            pass
        for _ in range(max(0, int(wait_admin_s))):
            time.sleep(1)
            state = index_state(es)
            if state.is_ok():
                if log:
                    log("INDEX     Everything 索引就绪")
                return state
    return state


# ------------------------------------------------------------------ 查找 / 关闭

def lookup(names, timeout_per=20):
    """用 es.exe 在整机范围找缺失素材名 → {小写名: [路径…]}。

    只返回**真实存在**的文件路径（`os.path.isfile` 复核）：es 的索引可能是旧的，
    索引里有、磁盘上已经删掉的情况很常见。
    没有 es.exe 时返回 {}（P2 补 PowerShell 兜底；一代那条路在本机从未被走到）。
    """
    names = [n for n in dict.fromkeys(names or []) if n and n.strip()]
    hits = {}
    if not names:
        return hits
    es = find_es()
    if not es:
        return hits
    for n in names:
        try:
            r = subprocess.run([es, "-n", "20", "-a", n], capture_output=True,
                               creationflags=_NOWINDOW, timeout=timeout_per)
            txt = (r.stdout or b"").decode("mbcs", "ignore")
        except Exception:
            continue
        for line in txt.splitlines():
            line = line.strip().strip('"')
            if line and os.path.isfile(line):
                hits.setdefault(n.lower(), []).append(line)
    return hits


def stop_bundled(log=None):
    """退出时只停**本程序拉起的客户端**，服务一行不碰（v1.9.6 的教训）。"""
    global _BUNDLED_STARTED
    pids = client_pids()
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True,
                           creationflags=_NOWINDOW, timeout=15)
        except Exception:
            pass
    _BUNDLED_STARTED = False
    if log and pids:
        log("INDEX     已关闭内置 Everything（随开关同步）")
    return len(pids)