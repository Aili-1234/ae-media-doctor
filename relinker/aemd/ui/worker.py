# -*- coding: utf-8 -*-
"""aemd.ui.worker —— 后台任务与「回调搬回主线程」。

负责：
  · `post(widget, fn, *a)`：在**主线程**执行 fn —— 后台线程只能通过它碰控件
  · `poller(widget, interval_ms, fn)`：`tk.after` 驱动的轮询（chain 自己续期）
  · `spawn(fn)`：起一个 daemon 线程跑阻塞工作（如 runner.start）

不负责：
  · 任何业务逻辑
  · 线程池/队列框架（标准库 threading + tk.after 足够，别引依赖）

依赖：标准库 threading。**不许** import tkinter（widget 由调用方传进来，测试可传假对象）。

⚠ 一代的坑（真机上踩过）：后台线程里直接 `self.after(0, apply)`，
  在「非 mainloop 驱动」（例如测试用 update() 泵）时会抛
  `RuntimeError: main thread is not in main loop`。v2 的约定：
  **只有主线程能调 after**；后台线程一律走 `post()`，由主线程的轮询把队列抽干。
"""

import threading


class Inbox:
    """线程安全的小邮箱：后台线程 push，主线程 drain。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._items = []

    def push(self, item):
        with self._lock:
            self._items.append(item)

    def drain(self):
        with self._lock:
            items, self._items = self._items, []
        return items


def post(widget, fn, *a, **kw):
    """把回调排进主线程（本函数**只能在主线程调用**，它内部用 widget.after）。"""
    return widget.after(0, lambda: fn(*a, **kw))


def poller(widget, interval_ms, fn):
    """每 interval_ms 调一次 fn，自动续期；返回第一次的 after id（可 after_cancel）。"""

    def tick():
        try:
            fn()
        finally:
            widget.after(interval_ms, tick)

    return widget.after(interval_ms, tick)


def spawn(fn, name=None, daemon=True):
    """起后台线程（阻塞工作放这里），返回 Thread。"""
    t = threading.Thread(target=fn, name=name or "aemd-worker", daemon=daemon)
    t.start()
    return t