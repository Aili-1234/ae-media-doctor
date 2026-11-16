# -*- coding: utf-8 -*-
"""中文 Windows 控制台兜底（**唯一实现**，别在别处复制粘贴）。

这个模块为什么存在：2026-11-15 一天里，同一个坑被踩了三次 ——
v1 的 `✓`、v2 `main.py --ver`（抽检抓到 position 13）、v2 `test_p1_parity`
（`↔`，抽检又抓到一次）。每次表现都一样：**默认 GBK 控制台下打印非 GBK 字符
→ UnicodeEncodeError → 退出码变 2，看起来像逻辑失败**，而 UTF-8 终端里一切正常。

于是规矩定死两条：
  1. 所有可执行脚本（main.py / tools\\*.py / tests\\*.py）**第一件事**调 `harden()`；
  2. 控制台输出里的符号必须是 cp936 能编出来的 —— `↔` 写 `vs`、`→` 写 `->`、
     `·` 写 `,`、`…` 写 `...`。中文照旧，中文是 GBK 的本行。

依赖：无（只用 sys）。被谁调用：main.py、tools/check_imports.py、
tools/shortkey_parity.py、tools/selftest.py、tests/*.py。
不负责：业务输出内容本身的选择（那是各脚本自己的事）。
"""

import sys

_STD_HANDLES = {"stdout": -11, "stderr": -12}


def _reopen_std(which):
    """从**继承来的标准句柄**重建流（父进程做了重定向时走的这条）。

    窗口程序（`--windowed`）里 Python 的 `sys.stdout` 是 None，但句柄本身是被
    继承的：`GetStdHandle` 拿回来再包成文件对象，输出就能被父进程的管道/文件
    接住 —— 这是 `--ver` 在 `$out = & exe --ver` 里能被捕获的唯一办法。
    句柄无效（双击启动、没有重定向）会返回 None，不做任何事。
    """
    try:
        import ctypes
        import io
        import msvcrt
        import os

        kernel32 = ctypes.windll.kernel32
        kernel32.GetStdHandle.restype = ctypes.c_void_p
        h = kernel32.GetStdHandle(which)
        if not h or h == 0xFFFFFFFFFFFFFFFF or h == ctypes.c_void_p(-1).value:
            return None
        fd = msvcrt.open_osfhandle(h, os.O_WRONLY)
        return io.open(fd, "w", encoding="utf-8", errors="replace",
                       buffering=1, closefd=False)
    except Exception:
        return None


def attach_parent_console():
    """`--windowed` 打包后没有控制台：先接回**继承来的句柄**，再退回贴父控制台。

    为什么需要：`main.py --ver` 这类「只打印一行就退出」的模式，在 windowed exe
    里 `sys.stdout is None`，打印等于没打 —— 用户/脚本在控制台里看到的是空白，
    会以为 exe 坏了。

    顺序很重要：
      1. 先 `GetStdHandle` 重建（父进程重定向/接管道时，输出能被捕获）；
      2. 拿不到句柄再 `AttachConsole(-1)` 贴父控制台（控制台上能看见，但不进管道）；
      3. 双击启动时两样都没有，什么也不做 —— 不会闪黑框（那是 F-04 那个坑）。

    返回 True 表示至少重建成功了一个流。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return False
    fixed = False
    for name, which in _STD_HANDLES.items():
        if getattr(sys, name) is not None:
            continue
        stream = _reopen_std(which)
        if stream is not None:
            setattr(sys, name, stream)
            fixed = True
    if fixed:
        return True
    try:
        import ctypes

        if not ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
            return False
    except Exception:
        return False
    for name in ("stdout", "stderr"):
        try:
            setattr(sys, name, open("CONOUT$", "w", encoding="utf-8",
                                    errors="replace", buffering=1))
        except Exception:
            pass
    return True


def harden(streams=None):
    """把 stdout/stderr 变成「怎么打印都不会崩」。

    两条分开处理，因为两种下游的行为不一样：

    * **真控制台（isatty）**：保留本地编码（GBK），只加 `errors="replace"`。
      这里不能强转 UTF-8 —— 否则中文在 GBK 控制台上全是乱码/问号。
    * **管道 / 重定向（非 isatty）**：统一改成 UTF-8。父进程（selftest、别的
      脚本）拿到的是字节，必须有一个**确定**的编码才能正确解码；子进程按 GBK
      吐字节、父进程按 UTF-8 解的话，中文会变成 U+FFFD，父进程再打回 GBK
      控制台就崩 —— 抽检抓到的 position 13 就是这么来的。
    * **`stream is None`**（`--windowed` 打包后没有控制台）：先试着
      `attach_parent_console()` 贴回父控制台（见上面的说明），贴不上就跳过。
    """
    if streams is None and attach_parent_console():
        pass
    for stream in (sys.stdout, sys.stderr) if streams is None else streams:
        if stream is None:
            continue
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass