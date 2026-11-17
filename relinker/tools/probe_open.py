# -*- coding: utf-8 -*-
"""tools/probe_open.py —— 基线探针：直接开工程，看 AE 自己会不会卡（1.1.8 排查卡死用）。

不启动我们的脚本、不写 Startup、不改工程（只读打开）。每 1.5 秒采一次：
  · AE 是否 IsHungAppWindow（连续 N 秒才算真卡）
  · 当时所有可见窗口（类名 + 标题）
  · CPU 时间增量（判断"在算"还是"真死了"）
  崩溃修复框（空标题 #32770）默认按一次回车 —— 那只是在等人按，不算卡死。

整轮跑完自己关掉这次启动的 AE（不留进程给用户）。用法：
  python tools/probe_open.py "D:\\x\\y.aep" [--limit 150] [--tag baseline] [--no-enter]
"""
import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from aemd import aehost, winproc  # noqa: E402


def cpu_of(pid):
    try:
        import ctypes
        from ctypes import wintypes as wt
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x0400 | 0x0010, False, pid)
        if not h:
            return None
        ft, ct, kt, ut = (wt.FILETIME(), wt.FILETIME(), wt.FILETIME(), wt.FILETIME())
        ok = k.GetProcessTimes(wt.HANDLE(h), ctypes.byref(ft), ctypes.byref(ct),
                               ctypes.byref(kt), ctypes.byref(ut))
        k.CloseHandle(wt.HANDLE(h))
        if not ok:
            return None

        def secs(f):
            return (f.dwHighDateTime << 32 | f.dwLowDateTime) / 1e7
        return secs(kt) + secs(ut)
    except Exception:
        return None


def wins_of(pid):
    out = []
    try:
        for w in winproc.windows_of(pid):
            if w.get("visible"):
                out.append("%s|%s" % (w.get("cls"), (w.get("title") or "").strip()))
    except Exception as e:
        out.append("ERR %s" % e)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--limit", type=int, default=150)
    ap.add_argument("--tag", default="baseline")
    ap.add_argument("--ae", default="")
    ap.add_argument("--no-enter", action="store_true", help="不自动按崩溃修复框（默认自动按一次）")
    a = ap.parse_args()

    installs = aehost.find_installs()
    exe = a.ae or (installs[0][1] if installs else "")
    if not exe or not os.path.exists(exe):
        print("没找到 AE：%r" % (installs,))
        return 2
    out = os.path.join(ROOT, "tools", "_probe_%s.txt" % a.tag)
    fh = open(out, "w", encoding="utf-8")

    def w(s):
        print(s, flush=True)
        fh.write(s + "\n")
        fh.flush()

    w("AE %s" % exe)
    w("工程 %s" % a.project)
    t0 = time.time()
    p = subprocess.Popen([exe, a.project])
    w("启动 PID %d" % p.pid)
    last_cpu, last_t = None, t0
    hung_since, pressed = None, False
    opened_at = None
    try:
        while time.time() - t0 < a.limit:
            time.sleep(1.5)
            el = time.time() - t0
            if p.poll() is not None:
                w("%6.1fs  进程已退出 code=%r" % (el, p.returncode))
                return 0
            hung = winproc.is_hung(p.pid)
            cpu = cpu_of(p.pid)
            d = "" if (cpu is None or last_cpu is None) else "  cpu+%.2f" % (cpu - last_cpu)
            last_cpu, last_t = cpu, el
            wins = wins_of(p.pid)
            # 崩溃修复框：空标题 #32770，按一次回车（模拟用户点「继续」）
            if (not a.no_enter) and not pressed:
                try:
                    dl = [x for x in winproc.windows_of(p.pid)
                          if x.get("cls") == "#32770" and not (x.get("title") or "").strip()]
                except Exception:
                    dl = []
                if dl:
                    ok = winproc.press_enter(dl[0]["hwnd"])
                    pressed = True
                    w("%6.1fs  「崩溃修复选项」出现，按回车 -> %s" % (el, ok))
            if any("After Effects" == (x.split("|", 1)[1] if "|" in x else "") for x in wins):
                if opened_at is None:
                    opened_at = el
                    w("%6.1fs  ★ 主窗口出现（AE 起来了，用时 %.1fs）" % (el, el))
            if hung and hung_since is None:
                hung_since = el
            if not hung:
                hung_since = None
            w("%6.1fs  hung=%-5s%s  wins=%s" % (el, hung, d, " ; ".join(wins)[:300]))
            # 试验：Video Copilot 的 VCWINDOW（Optical Flares）冒出来之后 AE 就不动了 ——
            # 试着按 Esc / 发 WM_CLOSE，看工程能不能继续打开
            if os.environ.get("PROBE_CLOSE_VC"):
                try:
                    vc = [x for x in winproc.windows_of(p.pid) if x.get("cls") == "VCWINDOW"]
                except Exception:
                    vc = []
                if vc:
                    hw = vc[0]["hwnd"]
                    if not getattr(p, "_vc_esc", False):
                        print("   -> 对 VCWINDOW 先按 Esc", flush=True)
                        winproc.press_escape(hw)
                        p._vc_esc = True
                    elif not getattr(p, "_vc_close", False):
                        import ctypes
                        print("   -> Esc 没用，发 WM_CLOSE", flush=True)
                        ctypes.windll.user32.PostMessageW(hw, 0x0010, 0, 0)
                        p._vc_close = True

            if hung_since is not None and el - hung_since > float(os.environ.get("PROBE_HUNG_S", 20)):
                w("!! 连续 %s 秒无响应 —— 判定卡死，停止采样" % os.environ.get("PROBE_HUNG_S", "20"))
                return 0
    finally:
        fh.close()
        print("时间线写入 %s" % out, flush=True)
        # 关掉这次自己拉起来的 AE（只关这一个 pid）
        try:
            winproc.terminate_tree(p.pid, wait=8)
            print("已关掉本次启动的 AE PID %d" % p.pid, flush=True)
        except Exception as e:
            print("关 AE 失败：%s" % e, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
