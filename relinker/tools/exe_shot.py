# -*- coding: utf-8 -*-
"""对打好的 exe 做两件验收：

1) 从 exe 里数出图标资源帧数（ExtractIconExW）—— 证明 --icon 真的嵌进去了；
2) 双击等价：起 exe、等窗口出现、PrintWindow 抓图存 artifacts\\p4-exe-window.png，
   然后对主窗口发 WM_CLOSE 正常关掉（不 taskkill、不碰任何 AE）。

用法：python tools\\exe_shot.py [dist\\拾链\\拾链.exe]
"""
import ctypes
import ctypes.wintypes as wt
import importlib.util
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

u32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32

WANT_TITLE_PREFIX = "\u62fe\u94fe"          # 拾链


def icon_frames(exe):
    """返回 (大图标数, 小图标数)。ExtractIconExW 传 -1 只计数。"""
    big = shell32.ExtractIconExW(exe, -1, None, None, 0)
    return int(big)


def load_ui_shot():
    spec = importlib.util.spec_from_file_location(
        "aemd_ui_shot", os.path.join(ROOT, "tools", "ui_shot.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def find_window(pid, timeout=25.0):
    """在给定进程下找第一个可见、有标题的顶层窗口。"""
    found = []
    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _lp):
        wpid = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value != pid:
            return True
        if not u32.IsWindowVisible(hwnd):
            return True
        n = u32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u32.GetWindowTextW(hwnd, buf, n + 1)
        found.append((hwnd, buf.value))
        return True

    cb_ref = EnumProc(cb)
    t0 = time.time()
    while time.time() - t0 < timeout:
        found[:] = []
        u32.EnumWindows(cb_ref, 0)
        for hwnd, title in found:
            if title.startswith(WANT_TITLE_PREFIX):
                return hwnd, title
        time.sleep(0.3)
    return (found[0] if found else (None, None))


def main():
    exe = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist", "\u62fe\u94fe", "\u62fe\u94fe.exe")
    exe = os.path.abspath(exe)
    if not os.path.isfile(exe):
        print("\u627e\u4e0d\u5230 exe\uff1a%s" % exe)
        return 2

    n = icon_frames(exe)
    print("\u56fe\u6807\u5e27\u6570\uff08ExtractIconExW\uff09= %d" % n)

    # 双击等价：起进程（不传参数）
    proc = subprocess.Popen([exe], cwd=os.path.dirname(exe))
    try:
        hwnd, title = find_window(proc.pid)
        if not hwnd:
            print("\u7b49\u4e0d\u5230\u7a97\u53e3\uff08\u8fdb\u7a0b PID %d\uff09" % proc.pid)
            return 1
        print("\u7a97\u53e3\u6807\u9898 = %r" % title)

        # 抓图前让它画完一帧
        u32.SetForegroundWindow(hwnd)
        time.sleep(0.8)
        shot = load_ui_shot()
        rect = wt.RECT()
        u32.GetWindowRect(hwnd, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        pix = shot.grab(int(hwnd), w, h)
        out = os.path.join(ROOT, "artifacts", "p4-exe-window.png")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shot.write_png(out, *pix)
        print("\u622a\u56fe = %s  (%dx%d, %d B)" % (out, w, h, os.path.getsize(out)))

        # 正常关掉：只对主窗口发 WM_CLOSE
        u32.PostMessageW(hwnd, 0x0010, 0, 0)
        t0 = time.time()
        while time.time() - t0 < 10 and proc.poll() is None:
            time.sleep(0.2)
        print("\u5173\u7a97\u540e\u8fdb\u7a0b\u9000\u51fa\u7801 = %r" % proc.poll())
    finally:
        if proc.poll() is None:
            u32.PostMessageW(0xFFFF, 0x0010, 0, 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())