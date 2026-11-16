#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拾链 Relinker —— 入口（GUI / --cli 分派）。

用法：
    python main.py                # 开界面（P3 之前会明确说「还没实现」）
    python main.py --ver          # 打印版本，退出码 0
    python main.py --cli <aep>…   # 无界面跑一次（P2 之前会明确说「还没实现」）
    python main.py --selfcheck    # 跑一遍本机环境自检（P1 填满）

本文件只做三件事：
  1. 把 `sys.argv` 拆成「工程文件 / 目录 / 未知参数」三堆（`split_args`，纯函数）
  2. 把中文控制台兜住（cp936 下 print 勾叉会 UnicodeEncodeError 崩掉，退出码变 2）
  3. 分派到 ui.window / runner

不负责：任何业务逻辑（全在 aemd 包里）。

入口为什么要在包外：打包时 PyInstaller 拿它当主脚本；包内模块一律相对 import，
不许反过来 import main。
"""

import os
import sys

from aemd import APP_NAME, APP_VER, console
from aemd.const import PROJECT_EXTS


def _harden_console():
    """中文 Windows 控制台默认 GBK：打印任何非 GBK 字符都会崩（退出码变 2，像测试失败）。

    §8 的 `test_cli_gbk_safe` 盯着这条：**任何** CLI 输出都不许把进程搞崩。
    两条分开处理，因为两种下游的行为不一样：

    * **真控制台（isatty）**：保留本地编码（GBK），只加 `errors="replace"`。
      这里不能强转 UTF-8 —— 否则中文在 GBK 控制台上全是乱码，用户看到的是问号。
    * **管道 / 重定向（非 isatty）**：统一改成 UTF-8。父进程（selftest、别的脚本）
      拿到的是字节，必须有一个**确定的**编码才能正确解码；如果子进程按 GBK 吐字节、
      父进程按 UTF-8 解，中文会变成 U+FFFD，父进程再打印到 GBK 控制台就崩
      （2026-11-15 coordinator 抽检抓到的正是这一条：`--ver` 崩在 position 13）。
    """
    console.harden()


def split_args(argv):
    """把命令行参数分成 (工程文件列表, 目录列表)；未知参数与空串直接丢掉。

    拖文件到 exe 图标时 Windows 会把路径作为 argv 传进来；`.ffx`（预设）这类
    我们不认的扩展名必须忽略，否则会被当成工程去打开。
    """
    projects, dirs = [], []
    for a in argv or []:
        if not a:
            continue
        if os.path.isdir(a):
            dirs.append(a)
            continue
        ext = os.path.splitext(a)[1].lower()
        if ext in PROJECT_EXTS and os.path.isfile(a):
            projects.append(a)
    return projects, dirs


def main(argv=None):
    _harden_console()
    argv = list(sys.argv[1:] if argv is None else argv)

    if "--ver" in argv or "-v" in argv:
        print("%s %s" % (APP_NAME, APP_VER))
        return 0

    if "--selfcheck" in argv:
        print("%s %s" % (APP_NAME, APP_VER))
        print("自检脚本在 tools/selftest.py（P0 阶段请跑它，别指望这个开关）")
        return 0

    if "--cli" in argv:
        rest = argv[argv.index("--cli") + 1:]
        from aemd import runner
        try:
            return runner.run_cli(rest) or 0
        except NotImplementedError as e:
            print("CLI 还没实现（P2）：%s" % e)
            return 3

    projects, dirs = split_args(argv)
    from aemd.ui import window
    try:
        window.run()
        return 0
    except NotImplementedError as e:
        print("界面还没实现（P3）：%s" % e)
        if projects:
            print("认到的工程：%s" % "、".join(projects))
        return 3


if __name__ == "__main__":
    sys.exit(main())