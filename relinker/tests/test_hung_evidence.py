# -*- coding: utf-8 -*-
"""tests/test_hung_evidence.py —— AE 卡死时的取证（1.1.7）。

背景：2026-11-16 真机那次，AE 打开工程时 UI 线程整个无响应。程序当时只会说「卡了」，
事后既没有窗口清单，也没有插件清单，连版本都没记 —— 等于白卡一次。
这一版起，检出卡死的那一刻就把现场抓下来。

三条要证明的事：
  A. 插件清单：只列一层、按修改时间倒序、目录与文件都认
  B. 快照文本：窗口 / 主窗口判定 / 插件 / 「怎么用这份文件」四段都在
  C. 落盘：写得出文件、内容非空；底层炸了也只返回空串，绝不把整次运行带崩

跑法：`python -m tests.test_hung_evidence`（退出码 0 = 全过）。
"""

import io
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from aemd import console  # noqa: E402

console.harden()

from aemd import aehost, diag, winproc  # noqa: E402

OK, FAIL = [], []


def check(name):
    def deco(fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            FAIL.append((name, "%s: %s" % (type(e).__name__, e)))
        else:
            OK.append(name)
        return fn
    return deco


def need(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _fake_ae(root):
    """造一个假的 AE 安装：Support Files/Plug-ins/ 下三个条目，时间可区分。"""
    sup = os.path.join(root, "Support Files")
    plug = os.path.join(sup, "Plug-ins")
    os.makedirs(plug)
    old = os.path.join(plug, "BuiltIn.aex")
    io.open(old, "wb").write(b"x" * 10)
    os.utime(old, (1600000000, 1600000000))                 # 很旧
    sub = os.path.join(plug, "Optical Flares")
    os.makedirs(sub)                                        # 目录也要列
    new = os.path.join(plug, "Suspicious.aex")
    io.open(new, "wb").write(b"x" * 20)
    os.utime(new, (1750000000, 1750000000))                 # 比 BuiltIn 新
    return os.path.join(sup, "AfterFX.exe")


@check("A 插件清单：一层、按修改时间倒序、目录与文件都认")
def _():
    d = tempfile.mkdtemp(prefix="aemd_plug_")
    try:
        exe = _fake_ae(d)
        inv = diag.plugin_inventory(exe)
        need(inv["exists"] is True, "假插件目录没被认出来：%r" % (inv,))
        need(inv["count"] == 3, "应当 3 个条目，实得 %r" % (inv["count"],))
        names = [x["name"] for x in inv["items"]]
        times = [x["mtime"] for x in inv["items"]]
        need(times == sorted(times, reverse=True), "应当按修改时间倒序：%r" % (times,))
        need(names.index("Suspicious.aex") < names.index("BuiltIn.aex"),
             "后装的 Suspicious.aex 应当排在老 BuiltIn.aex 前面：%r" % (names,))
        need("Optical Flares" in names, "目录没被列出来：%r" % (names,))
        need([x for x in inv["items"] if x["name"] == "Optical Flares"][0]["dir"] is True,
             "目录条目的 dir 应当为 True")
        # 不存在时不许抛
        need(diag.plugin_inventory(os.path.join(d, "nope", "AfterFX.exe"))["exists"] is False,
             "目录不存在时 exists 应当 False")
        need(diag.plugin_inventory("")["exists"] is False, "空路径不许抛")
    finally:
        shutil.rmtree(d, ignore_errors=True)


@check("B 快照文本：窗口 / 主窗口判定 / 插件 / 怎么用，四段齐全")
def _():
    d = tempfile.mkdtemp(prefix="aemd_hung_")
    old_wins, old_hung = winproc.windows_of, winproc.is_hung
    try:
        exe = _fake_ae(d)
        winproc.windows_of = lambda pid: [
            {"hwnd": 111, "cls": "AE_CApplication_25.3", "title": "Adobe After Effects",
             "visible": True, "enabled": False},
            {"hwnd": 222, "cls": "#32770", "title": "打开项目", "visible": True, "enabled": True},
            {"hwnd": 333, "cls": "VCWINDOW", "title": "Optical Flares", "visible": True,
             "enabled": True},
        ]
        winproc.is_hung = lambda hwnd, timeout_ms=1200: int(hwnd) == 111
        text = diag.hung_report(4321, ae_exe=exe)
        for must in ("卡死证据快照", "pid=4321", "is_hung=True", "打开项目",
                     "Optical Flares", "插件目录", "怎么用这份文件"):
            need(must in text, "快照里缺 %r：\n%s" % (must, text[:400]))
        need("Suspicious.aex" in text, "插件清单没进快照")
        need("安全模式" in text, "少了「别点安全模式」那句（这句是防把现场弄丢的）")
    finally:
        winproc.windows_of, winproc.is_hung = old_wins, old_hung
        shutil.rmtree(d, ignore_errors=True)


@check("C 落盘：写出 hung-*.txt；底层炸了只返回空串，不抛")
def _():
    d = tempfile.mkdtemp(prefix="aemd_hungout_")
    old_wins, old_hung = winproc.windows_of, winproc.is_hung
    real_report = diag.hung_report
    try:
        winproc.windows_of = lambda pid: []
        winproc.is_hung = lambda hwnd, timeout_ms=1200: False
        p = aehost.capture_hung(1, d, ae_exe=os.path.join(d, "AfterFX.exe"))
        need(p and os.path.isfile(p), "没写出文件：%r" % (p,))
        need(os.path.basename(p).startswith("hung-") and p.endswith(".txt"),
             "文件名不合约定：%r" % (p,))
        need(os.path.getsize(p) > 100, "文件太小，像没内容：%d" % os.path.getsize(p))
        body = io.open(p, encoding="utf-8").read()
        need("没找到 AE 主窗口" in body, "没有窗口时应当如实写「没找到主窗口」：%r" % (body[:200],))

        # 底层炸掉：必须吞掉，返回空串
        diag.hung_report = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        logs = []
        p2 = aehost.capture_hung(1, d, log=lambda s, tag=None: logs.append(s))
        need(p2 == "", "抓证据失败时应当返回空串，实得 %r" % (p2,))
        need(any("没写成" in s for s in logs), "失败要留一句说明：%r" % (logs,))
    finally:
        diag.hung_report = real_report
        winproc.windows_of, winproc.is_hung = old_wins, old_hung
        shutil.rmtree(d, ignore_errors=True)


def main():
    print("拾链 —— 卡死取证（tests/test_hung_evidence.py）")
    print("-" * 66)
    for name in OK:
        print("  ok    %s" % name)
    for name, err in FAIL:
        print("  FAIL  %s" % name)
        print("        %s" % err)
    print("-" * 66)
    if FAIL:
        print("失败 %d 项 / 通过 %d 项" % (len(FAIL), len(OK)))
        return 1
    print("ALL PASS（%d 项全部通过）" % len(OK))
    return 0


if __name__ == "__main__":
    sys.exit(main())
