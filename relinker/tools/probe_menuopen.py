# -*- coding: utf-8 -*-
"""tools/probe_menuopen.py —— 验证「先等 AE 起来，再用菜单开工程」会不会卡。

假设（2026-11-17，与 Optical Flares 卡死有关）：
  · 冷启动时开工程（命令行带路径 / 启动脚本里 app.open）→ AE 在自己的初始化调用栈里，
    主线程不回消息，OF 的 VCWINDOW 一冒出来就互等 → 死锁（实测 CPU 归零、61 秒不动）。
  · 手动「文件 > 打开」时 AE 已经完全起来、消息循环在转 → OF 的窗口能被服务，
    自己就过去了（用户观察到的"顺利跳过"）。

做法：无参数启动 AE → 等主窗口能响应 → Ctrl+O → 粘贴路径 → 回车 → 采样 120 秒。
跑完自己关掉这次启动的 AE。
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


def key(vk, up=False):
    ctypes.windll.user32.keybd_event(vk, 0, 0x0002 if up else 0, 0)
    time.sleep(0.05)


def ctrl_hot(vk):
    key(0x11)          # Ctrl down
    key(vk)
    key(vk, up=True)
    key(0x11, up=True)  # Ctrl up


def main():
    project = sys.argv[1] if len(sys.argv) > 1 else ""
    limit = float(sys.argv[2]) if len(sys.argv) > 2 else 140.0
    tag = sys.argv[3] if len(sys.argv) > 3 else "menuopen"
    if not project or not os.path.exists(project):
        print("用法：python tools/probe_menuopen.py <工程.aep> [秒] [tag]")
        return 2

    exe = aehost.find_installs()[0][1]
    out = os.path.join(ROOT, "tools", "_probe_%s.txt" % tag)
    fh = open(out, "w", encoding="utf-8")

    def w(s):
        print(s, flush=True)
        fh.write(s + "\n")
        fh.flush()

    w("AE %s（无参数启动，之后用菜单开工程）" % exe)
    t0 = time.time()
    p = subprocess.Popen([exe])
    pid = p.pid
    w("启动 PID %d" % pid)
    try:
        # ① 等主窗口出现并能响应（不要求 title 里有工程名）
        ready = False
        while time.time() - t0 < 90:
            time.sleep(1.5)
            mw = winproc.main_window(pid)
            if mw and not winproc.is_hung(mw["hwnd"]):
                ready = True
                w("%6.1fs  主窗口就绪：%r" % (time.time() - t0, (mw.get("title") or "")))
                break
            w("%6.1fs  等主窗口… wins=%s" % (time.time() - t0, " ; ".join(wins_of(pid))[:200]))
        if not ready:
            w("!! 90 秒等不到可响应的主窗口")
            return 1

        time.sleep(3.0)
        if os.environ.get("PROBE_USEROPEN"):
            # 完全复刻用户动作：AE 已经开着，在资源管理器里双击工程（ShellExecute）
            w("%6.1fs  startfile 双击工程（模拟用户在资源管理器里打开）" % (time.time() - t0))
            os.startfile(project)
            time.sleep(2.0)
        else:
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Set-Clipboard -Value '%s'" % project.replace("'", "''")],
                           check=False)
            time.sleep(0.6)
            w("%6.1fs  Ctrl+O 打开工程对话框" % (time.time() - t0))
            ctrl_hot(0x4F)
            time.sleep(2.0)
            w("        对话框后窗口：%s" % " ; ".join(wins_of(pid))[:300])
            w("%6.1fs  Ctrl+V 粘贴路径 + 回车" % (time.time() - t0))
            ctrl_hot(0x56)
            time.sleep(1.0)
            key(0x0D)
            key(0x0D, up=True)

        # ② 采样
        deadline = time.time() - t0 + limit
        opened_at = None
        while time.time() - t0 < deadline:
            time.sleep(1.5)
            el = time.time() - t0
            cpu = cpu_of(pid)
            wins = wins_of(pid)
            hung = winproc.is_hung(winproc.main_window(pid)["hwnd"]) if winproc.main_window(pid) else False
            w("%6.1fs  hung=%-5s cpu=%s  wins=%s" % (el, hung, cpu, " ; ".join(wins)[:280]))
            base = os.path.basename(project)
            if any(base in x for x in wins) and opened_at is None:
                opened_at = el
                w("        ★ 工程标题已出现在主窗口（%.1fs）" % el)
            if opened_at and not any("打开项目" in x for x in wins):
                w("        ★ 「打开项目」进度框消失，工程打开完成（%.1fs）" % el)
                break
        if opened_at is None:
            w("!! 到时间仍未看到工程标题 —— 仍然卡着")
    finally:
        fh.close()
        print("时间线写入 %s" % out, flush=True)
        try:
            winproc.terminate_tree(pid, wait=8)
            print("已关掉本次启动的 AE PID %d" % pid, flush=True)
        except Exception as e:
            print("关 AE 失败：%s" % e, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
