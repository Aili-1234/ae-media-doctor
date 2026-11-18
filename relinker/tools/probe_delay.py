# -*- coding: utf-8 -*-
"""tools/probe_delay.py —— 找「AE 就绪后再等几秒」才不触发 Optical Flares 死锁（1.1.9）。

背景（2026-11-17 用户指正 + 实录）：
  · 用户手动「先开 AE → 等一下 → 双击工程」，每次都能过，OF 窗口自己消失。
  · 我自动「AE 一起就 app.open()」，OF 抢在 app.open 返回前冒出来，主线程互锁卡死。
  · 用户明确说「会等一下再双击」——所以关键变量是"等多久"。

做法：冷启动 AE（不带工程）→ 等主窗口出现 → 额外再等 `delay` 秒 → startfile 打开工程
→ 采样 60 秒，看有没有 VCWINDOW/Optical Flares 冒出来、会不会卡。

用法：python tools/probe_delay.py <工程> <delay秒> [tag]
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
    delay = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    tag = sys.argv[3] if len(sys.argv) > 3 else ("delay%.0f" % delay)
    exe = aehost.find_installs()[0][1]

    out = os.path.join(ROOT, "tools", "_probe_%s.txt" % tag)
    fh = open(out, "w", encoding="utf-8")

    def w(s):
        print(s, flush=True)
        fh.write(s + "\n")
        fh.flush()

    w("延迟实验：AE 就绪后再等 %.0f 秒才打开工程" % delay)
    p = subprocess.Popen([exe])
    pid = p.pid
    w("PID %d" % pid)
    try:
        t0 = time.time()
        ready = False
        while time.time() - t0 < 120:
            time.sleep(1.0)
            mw = winproc.main_window(pid)
            if mw and "After Effects" in (mw.get("title") or "") and not winproc.is_hung(mw["hwnd"]):
                ready = True
                w("%6.1fs  主窗口就绪，再等 %.0f 秒" % (time.time() - t0, delay))
                break
        if not ready:
            w("!! 等不到主窗口")
            return 1
        time.sleep(delay)
        w("%6.1fs  现在打开工程（startfile）" % (time.time() - t0))
        os.startfile(project)

        last_cpu, opened, blocked = None, None, None
        while time.time() - t0 < 90:
            time.sleep(0.8)
            el = time.time() - t0
            cpu = cpu_of(pid)
            d = "" if (cpu is None or last_cpu is None) else " +%.2f" % (cpu - last_cpu)
            last_cpu = cpu
            wins = wins_of(pid)
            m = winproc.main_window(pid)
            hung = winproc.is_hung(m["hwnd"]) if m else False
            w("%6.1fs hung=%-5s cpu%s  %s" % (el, hung, d, " ; ".join(wins)[:280]))
            if any("Optical Flares" in x or "VCWINDOW" in x for x in wins) and blocked is None:
                blocked = el
                w("        !! Optical Flares 窗口出现（%.1fs）" % el)
            if any(os.path.basename(project) in x for x in wins) and not any("打开项目" in x for x in wins):
                if opened is None:
                    opened = el
                    w("        ★ 工程打开完成（%.1fs）" % el)
                if el - opened > 8:
                    break
        verdict = "成功" if opened and (blocked is None or blocked > opened) else (
            "卡死" if blocked is not None else "未完成")
        w("结论（延迟 %.0f 秒）：%s" % (delay, verdict))
    finally:
        fh.close()
        print("时间线写入 %s" % out, flush=True)
        try:
            winproc.terminate_tree(pid, wait=6)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
