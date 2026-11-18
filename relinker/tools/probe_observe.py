# -*- coding: utf-8 -*-
"""tools/probe_observe.py —— 纯观察：录下「先开 AE → 双击工程」全过程（1.1.9 定位用）。

不注入任何按键、不点任何窗口、不关 AE。只做两件事：
  1) 把 AE 冷启动起来（不带工程），**不做任何事**等主窗口就绪
  2) 主窗口一就绪，立刻 startfile 打开工程（= 用户双击）
  3) 之后**完全不碰键盘鼠标**，每 0.8 秒采一次：所有可见窗口、主窗口是否 hung、CPU

为什么这次「完全不按键」：用户说"我手动打开每次都 100% 好、OF 窗口自己消失"，
而我这边的自动化每一步都在发回车/点鼠标。要判断 OF 窗口到底是"自己会走"还是
"被我的按键搅乱了"，就得有一轮**零干预**的对照。

用法：python tools/probe_observe.py "D:\\x\\y.aep" [秒] [tag]
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from aemd import aehost, winproc  # noqa: E402
from tools.probe_open import cpu_of, wins_of  # noqa: E402


def main():
    project = sys.argv[1]
    limit = float(sys.argv[2]) if len(sys.argv) > 2 else 160.0
    tag = sys.argv[3] if len(sys.argv) > 3 else "observe"
    exe = aehost.find_installs()[0][1]

    out = os.path.join(ROOT, "tools", "_probe_%s.txt" % tag)
    fh = open(out, "w", encoding="utf-8")

    def w(s):
        print(s, flush=True)
        fh.write(s + "\n")
        fh.flush()

    w("零干预观察：先起 AE → 等就绪 → 双击工程，之后完全不按键")
    w("AE %s" % exe)
    p = subprocess.Popen([exe])
    pid = p.pid
    w("PID %d" % pid)
    try:
        # ① 冷启动，等主窗口出现（不做任何事）
        t0 = time.time()
        ready = False
        while time.time() - t0 < 120:
            time.sleep(1.0)
            mw = winproc.main_window(pid)
            if mw and "After Effects" in (mw.get("title") or ""):
                ready = True
                w("%6.1fs  主窗口出现：%r（就绪，准备双击工程）" % (time.time() - t0, mw.get("title")))
                break
        if not ready:
            w("!! 120 秒等不到主窗口")
            return 1

        # ② 给 2 秒稳定，然后双击工程
        time.sleep(2.0)
        w("%6.1fs  startfile 打开工程（等同双击，之后零干预）" % (time.time() - t0))
        os.startfile(project)

        # ③ 零干预采样
        last_cpu, opened = None, None
        while time.time() - t0 < limit:
            time.sleep(0.8)
            el = time.time() - t0
            cpu = cpu_of(pid)
            d = "" if (cpu is None or last_cpu is None) else " +%.2f" % (cpu - last_cpu)
            last_cpu = cpu
            wins = wins_of(pid)
            m = winproc.main_window(pid)
            hung = winproc.is_hung(m["hwnd"]) if m else False
            w("%6.1fs hung=%-5s cpu%s  %s" % (el, hung, d, " ; ".join(wins)[:280]))
            base = os.path.basename(project)
            if opened is None and any(base in x for x in wins) and not any("打开项目" in x for x in wins):
                opened = el
                w("        ★ 工程打开完成（%.1fs）" % el)
            if opened is not None and el - opened > 8:
                w("        打开后 8 秒仍稳定，结束观察")
                break
        w("结论：%s" % ("打开了，OF 自己消失了" if opened else "到时间仍未打开"))
    finally:
        fh.close()
        print("时间线写入 %s" % out, flush=True)
        # 这次不关 AE，留给你看现场（如果卡着，你也好手动点一下对照）


if __name__ == "__main__":
    sys.exit(main())
