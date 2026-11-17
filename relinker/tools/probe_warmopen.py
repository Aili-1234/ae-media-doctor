# -*- coding: utf-8 -*-
"""tools/probe_warmopen.py —— 在**已经起来的 AE** 上打开工程（1.1.9 对照实验）。

对照的是 `probe_open.py`（冷启动时命令行带工程）：那个必卡在 Optical Flares。
这里 AE 已经响应了，再 startfile 打开 —— 就是用户手动操作的真实路径。

用法：python tools/probe_warmopen.py "D:\\x\\y.aep" [秒]
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from aemd import aehost, winproc  # noqa: E402
from tools.probe_open import cpu_of, wins_of  # noqa: E402


def main():
    project = sys.argv[1]
    limit = float(sys.argv[2]) if len(sys.argv) > 2 else 120.0
    ps = aehost.procs()
    if not ps:
        print("没有正在运行的 AE —— 先跑 probe_click.py")
        return 2
    pid = ps[0][0]
    mw = winproc.main_window(pid)
    print("AE PID %d 主窗口=%r" % (pid, (mw or {}).get("title")), flush=True)
    print("打开 %s" % project, flush=True)
    t0 = time.time()
    os.startfile(project)
    opened = False
    last_cpu = None
    while time.time() - t0 < limit:
        time.sleep(1.5)
        el = time.time() - t0
        cpu = cpu_of(pid)
        d = "" if (last_cpu is None or cpu is None) else " +%.2fs" % (cpu - last_cpu)
        last_cpu = cpu
        wins = wins_of(pid)
        m = winproc.main_window(pid)
        hung = winproc.is_hung(m["hwnd"]) if m else False
        print("%6.1fs hung=%-5s cpu%s  %s" % (el, hung, d, " ; ".join(wins)[:260]), flush=True)
        base = os.path.basename(project)
        if any(base in x for x in wins) and not any("打开项目" in x for x in wins):
            print("★ 工程打开完成（%.1fs），没卡" % el, flush=True)
            opened = True
            break
        if any("Optical Flares" in x for x in wins):
            print("   （Optical Flares 的窗口出现了）", flush=True)
    if not opened:
        print("!! 没等到打开完成", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
