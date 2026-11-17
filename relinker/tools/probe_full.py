# -*- coding: utf-8 -*-
"""tools/probe_full.py —— 一条命令跑完整个对照（1.1.9 定根因用）。

流程（全程不关 AE，最后自己收尾）：
  1) 冷启动 AE（不带工程）
  2) 崩溃修复框出现 → 鼠标点标题栏激活 + 点「继续」
  3) 等主窗口出现且能响应
  4) 在资源管理器意义上"双击"工程（os.startfile）
  5) 采样 120 秒：看工程能不能打开、Optical Flares 的窗口有没有把它卡住

和 `probe_open.py` 的唯一区别是第 3 步：工程在 AE **已经活起来之后**才打开。
用法：python tools/probe_full.py "D:\\x\\y.aep" [秒]
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
from tools.probe_open import cpu_of, wins_of  # noqa: E402

u = ctypes.windll.user32


def click_at(x, y, label=""):
    u.SetCursorPos(int(x), int(y))
    time.sleep(0.15)
    u.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.08)
    u.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.2)
    print("   点了 (%.0f,%.0f) %s" % (x, y, label), flush=True)


def kill_crash_dialog(pid, wait_s=40):
    """等崩溃修复框并点掉它；返回是否点掉。"""
    t0 = time.time()
    hwnd = None
    while time.time() - t0 < wait_s:
        time.sleep(0.8)
        for w in winproc.windows_of(pid):
            if w.get("cls") == "#32770" and not (w.get("title") or "").strip():
                hwnd = w["hwnd"]
                break
        if hwnd:
            break
    if not hwnd:
        print("   （没弹崩溃修复框）", flush=True)
        return True
    r = wt.RECT()
    u.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))
    l, t, r2, b = r.left, r.top, r.right, r.bottom
    w_, h_ = r2 - l, b - t
    print("   崩溃修复框 (%d,%d) %dx%d" % (l, t, w_, h_), flush=True)
    click_at(l + w_ / 2, t + 9, "标题栏")
    click_at(l + w_ * 0.83, t + h_ * 0.89, "「继续」")
    time.sleep(1.5)
    if winproc.still_open(hwnd):
        click_at(l + w_ * 0.83, t + h_ * 0.89, "「继续」（补一次）")
        time.sleep(1.5)
    gone = not winproc.still_open(hwnd)
    print("   崩溃修复框：%s" % ("点掉了 ✓" if gone else "还在 ✗"), flush=True)
    return gone


def wait_main(pid, limit=90):
    t0 = time.time()
    while time.time() - t0 < limit:
        time.sleep(1.0)
        mw = winproc.main_window(pid)
        if mw and "After Effects" in (mw.get("title") or "") and not winproc.is_hung(mw["hwnd"]):
            print("   主窗口就绪（%.1fs）：%r" % (time.time() - t0, mw.get("title")), flush=True)
            return True
    print("   !! 等不到可响应的主窗口", flush=True)
    return False


def main():
    project = sys.argv[1]
    limit = float(sys.argv[2]) if len(sys.argv) > 2 else 120.0
    exe = aehost.find_installs()[0][1]
    print("AE %s" % exe, flush=True)
    p = subprocess.Popen([exe])
    pid = p.pid
    print("① 冷启动 AE PID %d" % pid, flush=True)
    try:
        print("② 处理崩溃修复框", flush=True)
        kill_crash_dialog(pid)
        print("③ 等主窗口可响应", flush=True)
        if not wait_main(pid):
            return 1
        time.sleep(3.0)
        print("④ 现在打开工程（模拟用户双击）：%s" % project, flush=True)
        os.startfile(project)
        t0 = time.time()
        last_cpu, ok = None, False
        while time.time() - t0 < limit:
            time.sleep(1.5)
            el = time.time() - t0
            cpu = cpu_of(pid)
            d = "" if (cpu is None or last_cpu is None) else " +%.2fs" % (cpu - last_cpu)
            last_cpu = cpu
            wins = wins_of(pid)
            m = winproc.main_window(pid)
            hung = winproc.is_hung(m["hwnd"]) if m else False
            print("   %6.1fs hung=%-5s cpu%s  %s" % (el, hung, d, " ; ".join(wins)[:240]), flush=True)
            if any(os.path.basename(project) in x for x in wins) and not any("打开项目" in x for x in wins):
                print("★ 工程打开完成（%.1fs）—— 热状态下没卡" % el, flush=True)
                ok = True
                break
        if not ok:
            print("!! 热状态下也卡住了", flush=True)
        return 0 if ok else 1
    finally:
        try:
            winproc.terminate_tree(pid, wait=8)
            print("已关掉本次 AE PID %d" % pid, flush=True)
        except Exception as e:
            print("关 AE 失败：%s" % e, flush=True)


if __name__ == "__main__":
    sys.exit(main())
