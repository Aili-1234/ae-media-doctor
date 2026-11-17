# -*- coding: utf-8 -*-
"""tools/probe_press.py —— 试「怎么才能真正按动崩溃修复框」（1.1.9 排查用）。

背景（2026-11-17）：AI 侧一直以为回车按掉了，实际是用户手点的 —— 说明
`PostMessage(WM_KEYDOWN)` 与「SetForegroundWindow 后 keybd_event」两条路在那个
自绘对话框上都无效（DroverLord 不吃伪造消息）。本脚本按顺序试 5 种打法，
每试一种看 3 秒，谁把框弄没了就记谁。

用法：python tools/probe_press.py   （会启动 AE、最多 3 分钟、最后自己关掉）
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
k = ctypes.windll.kernel32


def log(s):
    print(s, flush=True)


def click_at(x, y):
    u.SetCursorPos(int(x), int(y))
    time.sleep(0.15)
    u.mouse_event(0x0002, 0, 0, 0, 0)      # LEFTDOWN
    time.sleep(0.08)
    u.mouse_event(0x0004, 0, 0, 0, 0)      # LEFTUP


def rect(hwnd):
    r = wt.RECT()
    u.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def children(hwnd):
    out = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _l):
        cls = ctypes.create_unicode_buffer(128)
        u.GetClassNameW(h, cls, 128)
        txt = ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(h, txt, 256)
        out.append((h, cls.value, txt.value))
        return True
    u.EnumChildWindows(wt.HWND(hwnd), cb, 0)
    return out


def try_strategy(name, fn, hwnd):
    """试一种打法，最多看 4 秒；框没了返回 True。"""
    log("  试：%s" % name)
    try:
        fn(hwnd)
    except Exception as e:
        log("    抛出 %s: %s" % (type(e).__name__, e))
    t0 = time.time()
    while time.time() - t0 < 4:
        time.sleep(0.5)
        if not winproc.still_open(hwnd):
            log("    ✓ 框消失了（用了 %.1fs）" % (time.time() - t0))
            return True
    log("    ✗ 还在")
    return False


def main():
    exe = aehost.find_installs()[0][1]
    log("AE %s" % exe)
    p = subprocess.Popen([exe])
    pid = p.pid
    log("启动 PID %d" % pid)
    try:
        # 等崩溃修复框（空标题 #32770）
        hwnd = None
        t0 = time.time()
        while time.time() - t0 < 120:
            time.sleep(1.0)
            for w in winproc.windows_of(pid):
                if w.get("cls") == "#32770" and not (w.get("title") or "").strip():
                    hwnd = w["hwnd"]
                    break
            if hwnd:
                log("%.1fs 崩溃修复框出现 hwnd=%s rect=%r" % (time.time() - t0, hex(hwnd), rect(hwnd)))
                break
        if not hwnd:
            log("!! 这次没弹崩溃修复框（AE 上次是正常退出？）—— 无法测试")
            return 1

        log("框内子窗口：")
        for h, cls, txt in children(hwnd):
            log("   %s  %-24s %r" % (hex(h), cls, txt))

        l, t, r, b = rect(hwnd)
        cx, cy = (l + r) // 2, (t + b) // 2

        strategies = [
            ("① PostMessage WM_KEYDOWN/UP（现在代码里的兜底）",
             lambda h: (u.PostMessageW(wt.HWND(h), 0x0100, 0x0D, 0),
                        u.PostMessageW(wt.HWND(h), 0x0101, 0x0D, 0))),
            ("② SetForegroundWindow + keybd_event 回车",
             lambda h: (u.SetForegroundWindow(wt.HWND(h)), time.sleep(0.2),
                        u.keybd_event(0x0D, 0, 0, 0), u.keybd_event(0x0D, 0, 2, 0))),
            ("③ 点标题栏（激活，不碰正文）+ 回车",
             lambda h: (click_at((l + r) // 2, t + 10), time.sleep(0.3),
                        u.keybd_event(0x0D, 0, 0, 0), u.keybd_event(0x0D, 0, 2, 0))),
            # 故意不试「点正文正中」：正文里是 安全模式 / 重置首选项 那几个单选项，
            # 点歪了会改用户的 AE 首选项。只点标题栏，正文永远不碰。
            ("④ WM_COMMAND IDOK 发给框",
             lambda h: (u.PostMessageW(wt.HWND(h), 0x0111, 1, 0),)),
            ("⑤ 直接 WM_CLOSE",
             lambda h: (u.PostMessageW(wt.HWND(h), 0x0010, 0, 0),)),
        ]
        for name, fn in strategies:
            if try_strategy(name, fn, hwnd):
                log("\n结论：%s 有效" % name)
                return 0
            if not winproc.still_open(hwnd):
                return 0
        log("\n结论：六种打法全部无效 —— 这个框只能人手点，或者需要在更早的时点（前台/焦点在手时）按")
        return 1
    finally:
        try:
            winproc.terminate_tree(pid, wait=8)
            log("已关掉本次启动的 AE PID %d" % pid)
        except Exception as e:
            log("关 AE 失败：%s" % e)


if __name__ == "__main__":
    sys.exit(main())
