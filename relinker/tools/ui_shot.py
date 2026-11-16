# -*- coding: utf-8 -*-
"""tools/ui_shot.py —— 给主界面拍一张 900×820 的截图（证据用，不参与打包）。

跑法（在 media-doctor-v2 目录下）：

    python tools/ui_shot.py            # 拍两张：空状态 / 跑完的样子
    python tools/ui_shot.py out.png

为什么不用第三方截图库：这台机器上不一定装了 PIL，而 Windows 自带
`PrintWindow` + GDI 就够了；PNG 编码用标准库 zlib 手写（约 20 行）。
注意 `PrintWindow(..., 2)` 的 2 是 PW_RENDERFULLCONTENT —— 不传它，
DWM 合成的深色窗口会拍出一片黑。
"""

import ctypes
import os
import struct
import sys
import zlib
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from aemd import console

console.harden()

import tkinter as tk

from aemd import const, runner
from aemd.ui import theme as T
from aemd.ui import window as U

W, H = T.WIN_SIZE


def grab(hwnd, w, h):
    """把窗口画到位图里，返回 (宽, 高, RGB 字节)。"""
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    hdc = user32.GetWindowDC(hwnd)
    if not hdc:
        raise RuntimeError("GetWindowDC 失败")
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    try:
        ok = user32.PrintWindow(hwnd, mem, 2)      # 2 = PW_RENDERFULLCONTENT
        if not ok:
            raise RuntimeError("PrintWindow 返回 0（窗口可能被最小化）")

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                        ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]

        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.biWidth, bi.biHeight = w, -h            # 负高度 = 自上而下
        bi.biPlanes, bi.biBitCount = 1, 24
        buf = ctypes.create_string_buffer(w * h * 3)
        got = gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
        if not got:
            raise RuntimeError("GetDIBits 失败")
        return w, h, buf.raw
    finally:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(hwnd, hdc)


def write_png(path, w, h, rgb):
    """把自上而下的 24 位 BGR 数据写成 PNG（标准库 zlib，无依赖）。"""
    stride = w * 3
    raw = bytearray()
    for y in range(h):
        raw.append(0)                              # filter type 0
        row = bytearray(rgb[y * stride:(y + 1) * stride])
        row[0::3], row[2::3] = row[2::3], row[0::3]   # BGR -> RGB（切片比逐像素快几十倍）
        raw.extend(row)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", ihdr))
        f.write(chunk(b"IDAT", zlib.compress(bytes(raw), 6)))
        f.write(chunk(b"IEND", b""))


def shoot(win, path):
    win.root.update_idletasks()
    win.root.update()
    hwnd = int(win.root.winfo_id())
    w, h, rgb = grab(hwnd, W, H)
    write_png(path, w, h, rgb)
    return path


class _Res(object):
    def __init__(self, stats):
        self.stats = stats
        self.summary = ""
        self.warnings = []
        self.legacy_files = ["_relink_log.txt", "_media_index.json"]


class _Poll(object):
    def __init__(self, state, message=""):
        self.state = state
        self.message = message
        self.report = None
        self.lines = []
        self.status_text = "修复完成，工程已保存"
        self.finished = True


PROJ = r"D:\ae\别人工程\again (1)\again\again.aep"


def _sandbox():
    """把「%APPDATA%\\AE素材医生」指到一个临时目录，并让写配置变成空操作。

    为什么必须这样（2026-11-15 18:21 的教训）：拍截图要真的构造 MainWindow，
    而 MainWindow 会读真实 settings.json、并按界面值做一次 _persist()。
    结果是我拍一张图就把用户的配置重写了一次（436 B -> 422 B）。
    截图工具不许有这种副作用 —— 它只是「看」，不是「用」。
    """
    import tempfile

    tmp = tempfile.mkdtemp(prefix="aemd_shot_")
    const.APP_HOME = tmp
    const.LOG_DIR = os.path.join(tmp, "logs")
    const.WORK_DIR = os.path.join(tmp, "work")
    os.makedirs(const.LOG_DIR, exist_ok=True)
    os.makedirs(const.WORK_DIR, exist_ok=True)

    from aemd import cfg as _cfg
    _cfg.merge_cfg = lambda updates=None, base=None: dict(updates or {})
    _cfg.save = _cfg.merge_cfg
    return tmp


def build(installs, project=None):
    """project=None 就是「刚装好、还没选工程」的空状态（首屏第一眼看到的样子）。"""
    root = tk.Tk()
    cfg = {"project": project} if project else {}
    win = U.MainWindow(root=root, cfg_data=cfg, installs=installs)
    for d in (r"D:\ae\别人工程\again (1)\again", r"D:\ae\素材库", r"D:\ae\footage"):
        win.lb_dirs.insert("end", d)
    if project:
        win._set_project_quiet(project)   # 内部赋值：不写盘（t-18）
    root.geometry("%dx%d" % (W, H))
    root.update()
    return root, win


def fill_run(win):
    stats = {"total": 32, "ok": 0, "fixed": 21, "missing": 11, "ambiguous": 1, "zeroByte": 0}
    win.run_obj = type("R", (), {})()
    win.run_obj.result = _Res(stats)
    win.run_obj.missing_path = lambda: ""
    win._clear_log()
    for line, tag in [
        ("OPEN    again.aep（32 条素材引用）", None),
        ("LAUNCH  已启动 AE，脚本已放进 Startup（跑完自删）", "dim"),
        ("A2      脚本开始执行（启动后 14 秒）", "dim"),
        ("RELINK  D:\\ae\\素材库\\01.mp4 -> 已接上", "ok"),
        ("RELINK  D:\\ae\\footage\\bg.mov -> 已接上", "ok"),
        ("MISSING 06.mp4（搜索目录里找不到）", "warn"),
        ("SUMMARY 总数 32 / 接上 21 / 还缺 11 / 按文件名猜的 1", "head"),
        ("SAVED   工程已保存", "ok"),
        ("NOTE    工程目录还有 2 个旧版文件：_relink_log.txt、_media_index.json（没删，可手动清）", "dim"),
    ]:
        win.log(line, tag)
    win._finish(_Poll(runner.State.DONE))


def main():
    _sandbox()
    out_dir = os.path.join(ROOT, "artifacts")
    os.makedirs(out_dir, exist_ok=True)
    installs = [("Adobe After Effects 2025", r"C:\Program Files\Adobe\Adobe After Effects 2025\Support Files\AfterFX.exe")]
    made = []

    root, win = build(installs, project=None)
    root.attributes("-topmost", True)
    root.update()
    made.append(shoot(win, os.path.join(out_dir, "p3-ui-empty.png")))
    root.destroy()

    root, win = build(installs, project=PROJ)
    root.attributes("-topmost", True)
    root.update()
    fill_run(win)
    root.update()
    made.append(shoot(win, os.path.join(out_dir, "p3-ui-done.png")))
    root.destroy()

    for p in made:
        print("%s  %d B" % (p, os.path.getsize(p)))
    return 0


if __name__ == "__main__":
    sys.exit(main())