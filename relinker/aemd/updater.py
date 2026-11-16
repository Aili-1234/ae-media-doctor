# -*- coding: utf-8 -*-
"""aemd.updater —— 程序自身指纹 + 「已更新，点这里重启」（R12 / §8）。

负责：
  · `exe_path()`：当前跑的是哪个文件（源码模式 = main.py 所在树的入口；冻结模式 = 自己的 exe）
  · `stamp()`：`(路径, 大小, mtime)` 三元组 —— 磁盘上的程序被换过，它就变
  · `stamp_changed(seen)`：比一次，变了返回新 stamp，没变返回 None
  · `restart_args()`：一键重启要起的命令行（`self.quit()` 之前调用）

不负责：
  · 画提示条、按钮置灰（ui 层订阅 stamp_changed 的结果）
  · 下载/安装更新（**不做自动更新**：只是让已经开着旧窗口的人能一键换成新版）
  · 判断版本号高低（运行中的进程读不到新文件的版本，不许编，§8 的 test_restart_banner）

依赖：无（标准库 os/sys）。**不许** import ui / runner / winproc。

⚠ 一代的坑（用户 m00962）：部署了新 exe 之后，用户开着的旧窗口还在跑旧代码，
  他以为是「升级没生效」。Python 进程没法热替换模块，所以只能：
  发现指纹变了 → 顶部提示条 → 用户点一下 → 起新进程 + 旧进程干净退出。
"""

import os
import sys


def exe_path():
    """当前跑着的这个程序的文件。冻结模式是 exe 自己，源码模式是入口脚本。"""
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    main = sys.modules.get("__main__")
    f = getattr(main, "__file__", None)
    if f:
        return os.path.abspath(f)
    return os.path.abspath(sys.argv[0] or ".")


def stamp(path=None):
    """`(路径, 大小, mtime)`；拿不到 stat 时返回 `(路径, -1, -1)`（别抛，界面每 20 秒调一次）。"""
    p = path or exe_path()
    try:
        st = os.stat(p)
        return (p, int(st.st_size), int(st.st_mtime))
    except OSError:
        return (p, -1, -1)


def stamp_changed(seen, path=None):
    """§8 的核心判据：磁盘与 `seen` 不同 → 返回新 stamp，否则 None。

    `path` 必须和当初算 `seen` 时用的**同一个**文件，否则比的是两个东西
    （自检踩过：`stamp(p)` 之后调 `stamp_changed(st)` 不带 path，它去 stamp
    当前入口脚本，永远"变了"）。
    """
    cur = stamp(path)
    return cur if cur != seen else None


def restart_args(path=None):
    """一键重启的命令行：源码模式要把入口脚本带上，冻结模式直接起 exe。"""
    p = path or exe_path()
    if getattr(sys, "frozen", False):
        return [p]
    return [sys.executable, p]


def restart(path=None):
    """起一个新进程（**不**负责退出当前进程；调用方随后 quit/destroy）。"""
    import subprocess
    args = restart_args(path)
    return subprocess.Popen(args, cwd=os.path.dirname(args[0]) or None)


__all__ = ["exe_path", "stamp", "stamp_changed", "restart_args", "restart"]