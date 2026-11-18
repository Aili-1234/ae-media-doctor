# -*- coding: utf-8 -*-
"""tools/probe_winstruct.py —— 把 Optical Flares 窗口的**完整结构**扒出来（1.1.9 定指纹用）。

目标：不再"靠关键词猜"这是什么插件，而是拿到它的**类型指纹** ——
   · 顶层窗口类名、标题、样式（WS_*）、扩展样式（WS_EX_*）
   · 整棵子窗口树（类名 + 文本 + 每个的样式），递归到最深
   · 每个子窗口是不是可见/启用、有没有按钮（BS_* 之类）

有了这套结构，就能反推出"这是一类什么样的窗口"（自绘 + 无原生按钮 + 某个独特的类名组合），
以后别的插件弹同型窗口也能自动对上。

用法：python tools/probe_winstruct.py  （开着 AE、打开那个会卡 OF 的工程，或直接启动 AE）
     --wait 90  等这个窗口出现的秒数；抓到就扒，扒完不关 AE。
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes as wt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from aemd import aehost, winproc  # noqa: E402

u = ctypes.windll.user32

GWL_STYLE = -16
GWL_EXSTYLE = -20


def style_bits(style):
    """把 WS_* 位解成可读名字。"""
    names = []
    for bit, nm in [(0x80000000, "WS_POPUP"), (0x40000000, "WS_CHILD"),
                    (0x20000000, "WS_MINIMIZE"), (0x10000000, "WS_VISIBLE"),
                    (0x08000000, "WS_DISABLED"), (0x04000000, "WS_CLIPSIBLINGS"),
                    (0x02000000, "WS_CLIPCHILDREN"), (0x01000000, "WS_MAXIMIZE"),
                    (0x00C00000, "WS_CAPTION"), (0x00800000, "WS_BORDER"),
                    (0x00400000, "WS_DLGFRAME"), (0x00200000, "WS_VSCROLL"),
                    (0x00100000, "WS_HSCROLL"), (0x00080000, "WS_SYSMENU"),
                    (0x00040000, "WS_THICKFRAME"), (0x00020000, "WS_MINIMIZEBOX"),
                    (0x00010000, "WS_MAXIMIZEBOX"), (0x00008000, "WS_GROUP"),
                    (0x00004000, "WS_TABSTOP"), (0x00001000, "BS_DEFPUSHBUTTON"),
                    (0x00000001, "BS_PUSHBUTTON")]:
        if style & bit:
            names.append(nm)
    return names


def exstyle_bits(ex):
    names = []
    for bit, nm in [(0x00080000, "WS_EX_LAYERED"), (0x00000200, "WS_EX_CONTEXTHELP"),
                    (0x00000080, "WS_EX_TOOLWINDOW"), (0x00000100, "WS_EX_WINDOWEDGE"),
                    (0x00000008, "WS_EX_TOPMOST"), (0x00000020, "WS_EX_TRANSPARENT"),
                    (0x00000001, "WS_EX_DLGMODALFRAME"), (0x00000010, "WS_EX_ACCEPTFILES"),
                    (0x00000040, "WS_EX_CLIENTEDGE")]:
        if ex & bit:
            names.append(nm)
    return names


def info(hwnd):
    cls = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(hwnd, cls, 256)
    txt = ctypes.create_unicode_buffer(512)
    u.GetWindowTextW(hwnd, txt, 512)
    st = u.GetWindowLongW(wt.HWND(hwnd), GWL_STYLE)
    ex = u.GetWindowLongW(wt.HWND(hwnd), GWL_EXSTYLE)
    return cls.value, txt.value, st, ex


def walk(hwnd, depth=0, out=None):
    if out is None:
        out = []
    c, t, st, ex = info(hwnd)
    ind = "  " * depth
    out.append("%s0x%X  cls=%r  txt=%r  style=%s  ex=%s" % (
        ind, hwnd, c, t, "+".join(style_bits(st)), "+".join(exstyle_bits(ex))))
    # 递归子窗口
    kids = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _l):
        kids.append(h)
        return True
    u.EnumChildWindows(wt.HWND(hwnd), cb, 0)
    for k in kids:
        walk(k, depth + 1, out)
    return out


def find_of(pid, wait_s):
    """等 VCWINDOW / Optical Flares 那个窗口出现，返回 hwnd 或 0。"""
    t0 = time.time()
    while time.time() - t0 < wait_s:
        time.sleep(1.0)
        for w in winproc.windows_of(pid):
            if w.get("visible") and ((w.get("cls") or "") == "VCWINDOW"
                                     or "Optical Flares" in (w.get("title") or "")):
                return w["hwnd"]
    return 0


def main():
    wait_s = float(sys.argv[1]) if len(sys.argv) > 1 else 90.0
    ps = aehost.procs()
    if not ps:
        print("没有正在运行的 AE，先把它启动并打开那个会卡 OF 的工程")
        return 2
    pid = ps[0][0]
    print("AE PID %d，等 Optical Flares 窗口出现（最多 %.0f 秒）" % (pid, wait_s), flush=True)
    hwnd = find_of(pid, wait_s)
    if not hwnd:
        print("没等到。要不要我帮你启动 AE + 打开工程？")
        return 1
    print("\n======== Optical Flares 窗口结构（含全部子窗口） ========\n", flush=True)
    for line in walk(hwnd):
        print(line, flush=True)
    print("\n======== 同进程其它可见窗口（对照） ========\n", flush=True)
    for w in winproc.windows_of(pid):
        if not w.get("visible"):
            continue
        c, t, st, ex = info(w["hwnd"])
        print("0x%X  cls=%r  txt=%r  style=%s" % (w["hwnd"], c, t, "+".join(style_bits(st))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
