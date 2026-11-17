# -*- coding: utf-8 -*-
"""tools/probe_click.py —— 用鼠标真正点「继续」（1.1.9）。

2026-11-17：那个「崩溃修复选项」是 DroverLord 自绘的，键盘两条路都不稳（有时管用有时不管）。
用户截图给了按钮位置，于是按窗口矩形比例算坐标直接点「继续」：
    · 先点标题栏激活（正文一个像素都不碰）
    · 再按比例点「继续」按钮（截图量得：x≈0.83w, y≈0.89h）
点完留在原地（不关 AE），方便接着看工程能不能打开。
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


def click_at(x, y, label=""):
    u.SetCursorPos(int(x), int(y))
    time.sleep(0.15)
    u.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.08)
    u.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.2)
    print("  点了 (%.0f,%.0f) %s" % (x, y, label), flush=True)


def main():
    exe = aehost.find_installs()[0][1]
    print("AE %s" % exe, flush=True)
    p = subprocess.Popen([exe])
    pid = p.pid
    print("启动 PID %d（这次不关它）" % pid, flush=True)

    hwnd = None
    t0 = time.time()
    while time.time() - t0 < 90:
        time.sleep(1.0)
        for w in winproc.windows_of(pid):
            if w.get("cls") == "#32770" and not (w.get("title") or "").strip():
                hwnd = w["hwnd"]
                break
        if hwnd:
            break
    if not hwnd:
        print("没等到崩溃修复框 —— 这次 AE 是正常退出的，直接看主窗口")
    else:
        r = wt.RECT()
        u.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))
        l, t, r2, b = r.left, r.top, r.right, r.bottom
        w_, h_ = r2 - l, b - t
        print("%.1fs 崩溃修复框 rect=(%d,%d,%d,%d) %dx%d"
              % (time.time() - t0, l, t, r2, b, w_, h_), flush=True)
        # ① 点标题栏（激活窗口，不动正文）
        click_at(l + w_ / 2, t + 9, "标题栏（只激活）")
        # ② 点「继续」（截图量出来的比例）
        click_at(l + w_ * 0.83, t + h_ * 0.89, "「继续」按钮")
        time.sleep(2.0)
        if winproc.still_open(hwnd):
            print("   还开着？再补一次回车 + 再点一次", flush=True)
            u.keybd_event(0x0D, 0, 0, 0)
            u.keybd_event(0x0D, 0, 2, 0)
            time.sleep(0.5)
            click_at(l + w_ * 0.83, t + h_ * 0.89, "「继续」按钮（第二次）")
            time.sleep(2.0)
        print("   框还在吗：%s" % ("在" if winproc.still_open(hwnd) else "没了 ✓"), flush=True)

    # 观察 25 秒：主窗口应出现
    t1 = time.time()
    while time.time() - t1 < 25:
        time.sleep(2.0)
        mw = winproc.main_window(pid)
        print("%.0fs 主窗口=%r hung=%s wins=%s" % (
            time.time() - t1,
            (mw or {}).get("title"),
            winproc.is_hung(mw["hwnd"]) if mw else "-",
            " ; ".join((w.get("title") or w.get("cls") or "") for w in winproc.windows_of(pid) if w.get("visible"))[:200]),
            flush=True)
        if mw and "After Effects" in (mw.get("title") or ""):
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
