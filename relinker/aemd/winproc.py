# -*- coding: utf-8 -*-
"""aemd.winproc —— Windows 进程与窗口。项目里唯一允许碰进程的地方。

负责：
  · `snapshot_pids(exe_name)` 进程快照（Toolhelp32，不解析 tasklist 文本）
  · `is_alive(pid)` 存活判定（带「僵尸条目」过滤，见下）
  · `visible_pids()` / `has_main_window(pid)` 有没有可见主窗口
  · `ae_procs()` → [(pid, 有可见窗口?)]；`ae_pids()` → [pid]
  · `terminate_tree(pid, force=False)` **唯一的 taskkill 调用点**（低层原语）
  · `open_process_handle` 之类的句柄细节不外泄

不负责：
  · 判断「该不该关这个 AE」（业务裁决在 aehost.close_own_ae，只接受自己拉起的 PID）
  · 写日志、弹窗、改界面
  · 启动任何东西（启动在 aehost）

依赖：`.const`（NO_WINDOW / AE_EXE）。**绝不许** import ui / runner / aehost —— 反过来才对。

═══════════════════════════════════════════════════════════════════
为什么这个模块写满了坑（一代的教训，每条都对应一个真实事故）
═══════════════════════════════════════════════════════════════════
1) 不解析 `tasklist` 文本：它的表头/本地化文字会被当成进程名（v1.9.2 前踩过）。
   用 CreateToolhelp32Snapshot，只比 szExeFile。
2) 「僵尸条目」：进程其实已经结束，但还有别的句柄没释放，快照里仍然列着它。
   这种条目**不占 AE 的单实例锁**，可检测环节会误判成「AE 开着」而卡住流程，
   用户看到的是「明明关掉了还说我没关」。
   → `is_alive()` 用 `OpenProcess(SYNCHRONIZE|QUERY_LIMITED)` + `WaitForSingleObject(h,0)`：
     返回 WAIT_TIMEOUT(0x102) 才是真活着；WAIT_OBJECT_0(0) 就是已退出（HasExited）。
3) 打不开句柄时**不能判死**：对方 AE 可能以管理员身份在跑，而我们是普通权限，
   OpenProcess 返回 0、GetLastError=5 (ERROR_ACCESS_DENIED)。
   判死会导致「把正在跑的 AE 当成没跑」→ 单实例冲突 → 脚本送不进去。
   → err=5 一律按「可能活着」处理（返回 True），并在 `last_error()` 里留痕给诊断包。
   ⚠ 注意：**别用** `err != 0 → alive` 这种一刀切写法，那会把不存在/无效 PID
     （err=87 ERROR_INVALID_PARAMETER）也算活着，流程同样卡住。
4) 最小化窗口的 rect 是 -32000：有的老代码拿 `GetWindowRect` 判断「窗口有效」，
   最小化的 AE 会得到 (-32000,-32000)，被误判成「没有窗口」，进而被当残留处理。
   → 判窗口有效性只看 `IsWindowVisible` + 有 owner/标题（见 `_visible_pids`），
     **不看 rect**。真要 rect，必须先判 `IsIconic()`。
5) taskkill 必须带 `/T`（连子进程一起），并且加 CREATE_NO_WINDOW，
   否则关窗口时会闪一个蓝色控制台。
"""

import ctypes
import time
from ctypes import wintypes as wt

from .const import AE_EXE, NO_WINDOW

_PROCESS_SYNCHRONIZE = 0x00100000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TH32CS_SNAPPROCESS = 0x00000002
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x102
_ERROR_ACCESS_DENIED = 5
_ERROR_INVALID_PARAMETER = 87

_LAST_ERROR = {"pid": None, "err": None}


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD),
                ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wt.DWORD),
                ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


# ---------------------------------------------------------------- 进程快照
def snapshot_pids(exe_name):
    """列出某个 exe 名的全部 PID（**含僵尸条目**，要过滤请用 is_alive）。

    通用实现：Everything 的探测也走这里（一代是两套代码，口径漂过）。
    """
    want = (exe_name or "").lower()
    if not want:
        return []
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    snap = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return []
    out = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if (entry.szExeFile or "").lower() == want:
                out.append(int(entry.th32ProcessID))
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return out


# ---------------------------------------------------------------- 存活判定
def last_error():
    """最近一次 is_alive() 的 (pid, GetLastError)，给诊断包看（没记录过是 (None, None)）。"""
    return _LAST_ERROR["pid"], _LAST_ERROR["err"]


def is_alive(pid):
    """进程是否真的还在跑（僵尸条目 → False；打不开句柄 → True 并留痕）。

    判据见模块 docstring 的第 2/3 条：这是 §8「僵尸 AE 被当成在跑」的结构性防线。
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    h = k32.OpenProcess(_PROCESS_SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        err = ctypes.get_last_error()
        _LAST_ERROR["pid"], _LAST_ERROR["err"] = pid, err
        if err == _ERROR_INVALID_PARAMETER:
            return False          # 这个 PID 根本不存在
        # err=5（权限不够，多半是管理员身份的 AE）或其它异常：按「可能活着」处理
        return True
    try:
        return k32.WaitForSingleObject(h, 0) == _WAIT_TIMEOUT
    finally:
        k32.CloseHandle(h)


# ---------------------------------------------------------------- 窗口
def _visible_pids():
    """有「可见主窗口」的进程 PID 集合。

    判据：IsWindowVisible + 没有 owner（GW_OWNER==0 才算主窗口）+ 标题非空。
    **不看 GetWindowRect** —— 最小化窗口 rect 是 -32000（模块 docstring 第 4 条）。
    """
    u = ctypes.windll.user32
    hits = set()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if not u.IsWindowVisible(hwnd):
            return True
        if u.GetWindow(hwnd, 4):          # GW_OWNER：有 owner 的是附属窗口，不算主窗口
            return True
        if u.GetWindowTextLengthW(hwnd) <= 0:
            return True
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        hits.add(int(pid.value))
        return True

    u.EnumWindows(cb, 0)
    return hits


def visible_pids():
    return _visible_pids()


def has_main_window(pid):
    try:
        return int(pid) in _visible_pids()
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------- 窗口详情（模态框）
def windows_of(pid):
    """某个进程的全部顶层窗口 → [{hwnd, cls, title, visible, enabled}]。

    为什么需要它（A2 真机实验 N-3）：AE 的 Startup 脚本**解析失败或未捕获异常**时
    会弹一个模态框。模态框开着的时候主窗口 `enabled=False`，但 `Responding` 仍然是
    True、AE 也**永远不会超时退出** —— 用户看到的就是「点了开始，AE 卡着不动」。
    所以判据不能只问「进程还活着吗」，得能看到「它是不是被自己的对话框挡着」。
    """
    u = ctypes.windll.user32
    u.GetClassNameW.restype = ctypes.c_int
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        owner = wt.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if int(owner.value) != int(pid):
            return True
        buf = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, buf, 256)
        n = u.GetWindowTextLengthW(hwnd)
        tbuf = ctypes.create_unicode_buffer(n + 2)
        u.GetWindowTextW(hwnd, tbuf, n + 2)
        out.append({"hwnd": int(hwnd), "cls": buf.value, "title": tbuf.value,
                    "visible": bool(u.IsWindowVisible(hwnd)),
                    "enabled": bool(u.IsWindowEnabled(hwnd))})
        return True

    u.EnumWindows(cb, 0)
    return out


def modal_dialogs(pid):
    """该进程的**可见但被禁用**的顶层窗口 —— 就是挡在前面的模态框。

    `#32770` 是标准的 Windows 对话框类名；另外把所有「可见 + enabled=False」的窗口都算上，
    因为 AE 有些自绘对话框不用 #32770。
    """
    return [w for w in windows_of(pid) if w["visible"] and not w["enabled"]]


def main_window(pid):
    """AE 的主窗口（类名前缀 `AE_CApplication`）—— 判「卡死」要冲着它问。"""
    for w in windows_of(pid):
        if w.get("visible") and (w.get("cls") or "").startswith("AE_CApplication"):
            return w
    return None


def is_hung(hwnd, timeout_ms=1200):
    """这个窗口的 UI 线程是不是卡住了：1.2 秒内连 `WM_NULL` 都不回，就判卡死。

    为什么不用 `IsHungAppWindow` 单独判：它对「被模态框挡住的父窗口」也可能返回 1，
    而那种是**正常等待**。`WM_NULL` 超时是更硬的证据。
    2026-11-16 真机：拾链拉起 AE 打开工程时死锁 —— 主窗口与「打开项目」框两个都超时，
    `IsHungAppWindow` 也同为 1，但表面看只是「一个进度框挂在前面」，极易被当成慢。
    """
    try:
        u = ctypes.windll.user32
        r = ctypes.c_size_t(0)
        # SMTO_ABORTIFHUNG = 0x0002；WM_NULL = 0
        ok = u.SendMessageTimeoutW(wt.HWND(hwnd), 0x0000, 0, 0, 0x0002,
                                  int(timeout_ms), ctypes.byref(r))
        return not bool(ok)
    except Exception:
        return False


# D9：能判成「错误框」的文字（宁可漏关一次，也不能误关 AE 自己的进度窗）。
ERROR_DIALOG_KEYWORDS = (
    "无法执行", "语法错误", "错误", "无法导入", "无法打开", "失败",
    "Error", "error", "Unable to", "unable to", "Syntax",
)


def is_error_dialog(win):
    """这个窗口像不像**脚本出错**弹的框（D9 的判据）。

    真机验收发现 30/60 秒的兜底会把 AE 自己的「打开项目 / 解析字体」进度窗也判成模态框 ——
    那是 disabled + 可见，形状与错误框一模一样，只能靠类名 + 文字再收一道：
    `#32770` 且文字里出现错误关键词。其余一律只记日志、不动手（还有 90 秒超时兜底）。
    """
    if (win.get("cls") or "") != "#32770":
        return False
    title = win.get("title") or ""
    return any(k in title for k in ERROR_DIALOG_KEYWORDS)


def error_dialogs(pid):
    """该进程里「像脚本错误框」的那些窗口。"""
    return [w for w in modal_dialogs(pid) if is_error_dialog(w)]


# 真机回归抓到的「启动期挡路框」：AE 上次没正常退出（崩溃、被关掉、被强杀）后，
# 下次启动会先弹一个**标题为空**的 `#32770`：
#   「崩溃修复选项 —— 我们检测到您的上次会话发生崩溃……」
#   按钮：以安全模式启动 / 重置首选项 / 管理增效工具 / **继续**（默认焦点在「继续」）
# 这个框不点，AE 永远出不了主窗口，Startup 脚本也永远不执行 —— 表现就是
# 「等 90 秒没 marker → 降级 -r → 再等 240 秒没窗口 → 失败」，日志里一行 A2 都没有。
# 判据：`#32770` + **标题为空** + 可见。有标题的 `#32770`（打开项目 / 解析字体）不是它。
def startup_dialogs(pid):
    """AE 启动期「标题为空的 #32770」—— 崩溃修复提示。"""
    return [w for w in windows_of(pid)
            if w["visible"] and (w.get("cls") or "") == "#32770"
            and not (w.get("title") or "").strip()]


def progress_dialogs(pid, titles):
    """AE 启动期**有标题**的 #32770 进度框（「解析字体」「打开项目」…）。

    2026-11-15 真机实证：AE 无参启动时先弹这两个框，而它们**卡在事件循环外面** ——
    我们排进 `app.scheduleTask` 的活要等它们过去才会被调起（实测等了 616 秒仍不动，
    对框按一次回车后 4 秒内脚本就跑完、日志一次写出 20 KB）。所以 Python 侧要在
    「marker 有了、脚本迟迟没输出」时把这几个框按掉。
    """
    want = set(t for t in (titles or ()) if t)
    out = []
    for w in windows_of(pid):
        if not w.get("visible"):
            continue
        if (w.get("cls") or "") != "#32770":
            continue
        t = (w.get("title") or "").strip()
        if t and t in want:
            out.append(w)
    return out


# 「工程缺素材」提示框（打开工程时 AE 会列出找不到的文件）。
# 为什么靠关键词而不靠固定标题：这框的标题在不同版本里是 "After Effects"、空标题、
# 甚至 "错误"，而「解析字体 / 打开项目」那两个进度框标题是稳定的 —— 用文字反而更稳。
MISSING_DIALOG_KEYWORDS = (
    "缺少", "缺失", "找不到", "无法找到", "丢失", "离线", "脱机",
    "missing", "can't find", "cannot find", "could not find",
    "unable to locate", "offline",
)

# 框里出现这些字眼就**绝不动手**：宁可让用户自己点，也不能替他点「搜索 / 定位 / 取消」。
# （取消会让工程半开；搜索会再弹一个文件浏览器模态框，把整条启动链堵死。）
MISSING_DIALOG_FORBIDDEN = (
    "取消", "搜索", "查找", "定位", "浏览", "替换", "重置", "安全模式",
    "cancel", "search", "find", "locate", "browse", "replace", "reset",
)


def missing_dialogs(pid):
    """打开工程时「这些文件找不到」那一类提示框。

    判据三道，缺一不可：
      · 可见的 `#32770`（标准对话框类）
      · **有标题**（标题为空的归「崩溃修复选项」那条线，不在这里抢）
      · 标题或子控件文字里出现 `MISSING_DIALOG_KEYWORDS`
    """
    out = []
    for w in windows_of(pid):
        if not w.get("visible") or (w.get("cls") or "") != "#32770":
            continue
        if not (w.get("title") or "").strip():
            continue
        blob = (w.get("title") or "") + " " + " ".join(dialog_child_texts(w["hwnd"], limit=12))
        low = blob.lower()
        if any(k.lower() in low for k in MISSING_DIALOG_KEYWORDS):
            out.append(w)
    return out


def dialog_has_forbidden(win):
    """框里有没有「替用户点就出事」的按钮文字。命中就别动手。"""
    blob = ((win.get("title") or "") + " "
            + " ".join(dialog_child_texts(win.get("hwnd"), limit=12))).lower()
    return any(k.lower() in blob for k in MISSING_DIALOG_FORBIDDEN)


def dialog_child_texts(hwnd, limit=8):
    """枚举一个对话框的子控件文字（只读，给日志留证据用）。"""
    out = []
    try:
        u = ctypes.windll.user32
        buf = ctypes.create_unicode_buffer(256)

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def _cb(child, _lp):
            try:
                u.GetWindowTextW(child, buf, 256)
                s = (buf.value or "").strip()
                if s:
                    out.append(s)
            except Exception:
                pass
            return True

        u.EnumChildWindows(wt.HWND(hwnd), _cb, 0)
    except Exception:
        return out
    return out[:limit]


def press_enter(hwnd):
    """给窗口发一次回车（VK_RETURN 的 WM_KEYDOWN + WM_KEYUP）。

    崩溃修复框的默认焦点就在「继续」上，回车等于点它 —— **不点**「以安全模式启动」
    「重置首选项」这两个会改用户环境的按钮。实测：真机上一次回车 AE 就继续启动了。
    """
    try:
        u = ctypes.windll.user32
        try:
            u.SetForegroundWindow(wt.HWND(hwnd))
        except Exception:
            pass
        u.PostMessageW(wt.HWND(hwnd), 0x0100, 0x0D, 0)      # WM_KEYDOWN VK_RETURN
        u.PostMessageW(wt.HWND(hwnd), 0x0101, 0x0D, 0)      # WM_KEYUP
        return True
    except Exception:
        return False


def post_close(hwnd):
    """给窗口发 WM_CLOSE（不杀进程、不抢焦点）。风险面比 taskkill 小得多。"""
    try:
        u = ctypes.windll.user32
        u.PostMessageW(hwnd, 0x0010, 0, 0)      # WM_CLOSE
        return True
    except Exception:
        return False


def close_modal_dialogs(pid):
    """只点掉**像错误框**的模态对话框 → 返回关掉的个数（D9）。

    ⚠ 两道自我限制：
      1. 只处理 `#32770` 且文字含错误关键词的（`is_error_dialog`），AE 自己的进度窗不动；
      2. 只发 WM_CLOSE 给**对话框**，绝不对主窗口发 —— 对主窗口发等于让用户丢掉一次运行。
         主窗口被模态框挡着时它是 disabled，「可见 + disabled」正好把两者分开。
    """
    n = 0
    for w in error_dialogs(pid):
        if post_close(w["hwnd"]):
            n += 1
    return n


# ---------------------------------------------------------------- AE 专用视图
def ae_procs():
    """[(pid, 是否有可见窗口)] —— 已过滤僵尸条目。没窗口的多半是上次调试的残留。"""
    pids = [p for p in snapshot_pids(AE_EXE) if is_alive(p)]
    if not pids:
        return []
    vis = _visible_pids()
    return [(p, p in vis) for p in pids]


def ae_pids():
    """当前正在运行的 After Effects 进程 PID。"""
    return [p for p, _ in ae_procs()]


def foreign_ae_pids(own_pid=None):
    """除 `own_pid` 之外的 AE PID —— R1/R3 的判据（别人的 AE 一律不碰）。

    注意：这里只是**列出**，不做任何动作。该不该关是 aehost 的事，
    而 aehost 唯一能关的就是自己拉起的那个。
    """
    own = None
    if own_pid is not None:
        try:
            own = int(own_pid)
        except (TypeError, ValueError):
            own = None
    return [(p, v) for (p, v) in ae_procs() if p != own]


# ---------------------------------------------------------------- 关进程原语
def terminate_tree(pid, force=False, wait=0.0):
    """对单个 PID 执行 taskkill（/T 连子进程）。返回是否已看不到该进程。

    ⚠ 这是**低层原语**：全库只有 aehost.close_own_ae() 可以调用它，
      而 close_own_ae 只接受「本程序自己拉起的 pid」（R1）。
      ui 层不 import 本模块，也拿不到 taskkill（§8 的依赖方向限制）。
    """
    import subprocess
    args = ["taskkill", "/PID", str(int(pid)), "/T"]
    if force:
        args.append("/F")
    try:
        subprocess.run(args, capture_output=True, creationflags=NO_WINDOW)
    except Exception:
        return False
    if not wait:
        return True
    t0 = time.time()
    while time.time() - t0 < wait:
        if not is_alive(pid):
            return True
        time.sleep(0.25)
    return not is_alive(pid)


def kill_tree(pid, wait=4.0):
    """温和关不掉时的兜底：/F 强杀。返回是否已死。"""
    return terminate_tree(pid, force=True, wait=wait)


__all__ = ["snapshot_pids", "is_alive", "last_error", "visible_pids", "has_main_window",
           "windows_of", "modal_dialogs", "error_dialogs", "is_error_dialog",
           "ERROR_DIALOG_KEYWORDS", "post_close", "close_modal_dialogs",
           "startup_dialogs", "progress_dialogs", "dialog_child_texts", "press_enter",
           "ae_procs", "ae_pids", "foreign_ae_pids", "terminate_tree", "kill_tree",
           "PROCESSENTRY32W"]