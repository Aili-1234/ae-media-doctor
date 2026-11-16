# -*- coding: utf-8 -*-
"""aemd.ui —— 界面层（tkinter）。依赖方向的最上层，只向下调用。

分层：
  theme.py    颜色 / 字体 / 间距（间距一律 8 的倍数）
  widgets.py  自绘小控件：Tip / Toggle / card / flat_button / logo
  window.py   主窗口（单屏密度布局，六块）
  worker.py   `tk.after` 驱动的轮询与「把后台线程的回调搬回主线程」

铁律（§8）：
  · ui 包里**不许出现** subprocess / taskkill / winproc —— 关 AE 的唯一入口是
    `aehost.close_own_ae`，界面层连调用它的权利都没有（由 runner 按 R1 决定）。
  · ui 不许 import 具体库模块做业务判断：它只认 runner 给的 `Run` 对象与回调。
  · 所有跨线程回调必须经 `worker.post()` 搬到主线程再碰控件。
"""