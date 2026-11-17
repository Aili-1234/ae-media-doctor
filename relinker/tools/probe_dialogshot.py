# -*- coding: utf-8 -*-
"""tools/probe_dialogshot.py —— 把「空标题 #32770」截下来看看它到底是什么（1.1.9）。

为什么要截图：同一个「空标题 #32770」至少有两种尺寸（492x284 的崩溃修复框、
416x139 的小框），键盘/鼠标打法在小框上无效，光看类名分不清。
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


def shot(rect, path):
    l, t, r, b = rect
    w, h = r - l, b - t
    u32 = ctypes.windll.user32
    g32 = ctypes.windll.gdi32
    hdc = u32.GetDC(0)
    mdc = g32.CreateCompatibleDC(hdc)
    bmp = g32.CreateCompatibleBitmap(hdc, w, h)
    g32.SelectObject(mdc, bmp)
    g32.BitBlt(mdc, 0, 0, w, h, hdc, l, t, 0x00CC0020)
    # 存成 24 位 BMP，再让 PIL / PowerShell 转 PNG 太绕 —— 直接写 BMP，我看图工具认
    class BMIH(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                    ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                    ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                    ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]
    bi = BMIH()
    bi.biSize = ctypes.sizeof(BMIH)
    bi.biWidth, bi.biHeight = w, -h
    bi.biPlanes, bi.biBitCount = 1, 24
    bi.biCompression = 0
    stride = (w * 3 + 3) & ~3
    buf = ctypes.create_string_buffer(stride * h)
    g32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    hdr = ctypes.create_string_buffer(54)
    ctypes.memmove(hdr, b"BM", 2)
    total = 54 + stride * h
    ctypes.memmove(ctypes.byref(hdr, 2), ctypes.byref(ctypes.c_int(total)), 4)
    ctypes.memmove(ctypes.byref(hdr, 10), ctypes.byref(ctypes.c_int(54)), 4)
    ctypes.memmove(ctypes.byref(hdr, 14), ctypes.byref(bi), 40)
    with open(path, "wb") as f:
        f.write(hdr.raw)
        f.write(buf.raw)
    g32.DeleteObject(bmp)
    g32.DeleteDC(mdc)
    u32.ReleaseDC(0, hdc)


def main():
    exe = aehost.find_installs()[0][1]
    p = subprocess.Popen([exe])
    pid = p.pid
    print("PID %d" % pid, flush=True)
    try:
        seen = {}
        t0 = time.time()
        while time.time() - t0 < 45:
            time.sleep(1.0)
            for w in winproc.windows_of(pid):
                if w.get("cls") != "#32770" or (w.get("title") or "").strip():
                    continue
                r = wt.RECT()
                u.GetWindowRect(wt.HWND(w["hwnd"]), ctypes.byref(r))
                key = (r.right - r.left, r.bottom - r.top)
                if key in seen:
                    continue
                seen[key] = True
                path = os.path.join(ROOT, "tools", "_dlg_%dx%d.bmp" % key)
                shot((r.left, r.top, r.right, r.bottom), path)
                texts = winproc.dialog_child_texts(w["hwnd"], limit=20)
                print("空标题 #32770 %dx%d @ (%d,%d) -> %s" % (
                    key[0], key[1], r.left, r.top, path), flush=True)
                print("   子控件文字：%r" % (texts,), flush=True)
                print("   子窗口：%r" % ([(c, t) for _h, c, t in
                                       [(h, "", "") for h in []]],), flush=True)
            if len(seen) >= 2:
                break
    finally:
        try:
            winproc.terminate_tree(pid, wait=6)
            print("已关掉 AE", flush=True)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
