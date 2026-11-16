# -*- coding: utf-8 -*-
"""aemd.ui.widgets —— 自绘小控件（不引第三方库）。

负责：
  · `Tip`        鼠标悬停提示
  · `Toggle`     自绘开关（tk.Checkbutton 在高 DPI 上勾选框太小，用户看不清）
  · `card()`     卡片容器（CARD 底 + CARD_LINE 描边 + 内部留白）
  · `caption()`  小灰字说明
  · `flat_button()` 扁平按钮（primary=True 用 ACCENT 强调）
  · `logo()`     窗口图标/标题区小图
  · `grid2()`    2 列网格助手（§5：复选框必须两列对齐，不许换行错位）

不负责：
  · 业务逻辑、任何 I/O、任何 subprocess
  · 主窗口布局（window.py）

依赖：`tkinter`（只在构造控件时用）、`.theme`（只 import 常量）
"""

import tkinter as tk

from . import theme as T


class Tip(object):
    """鼠标悬停提示（无边框小窗，鼠标离开就销毁）。"""

    def __init__(self, widget, text):
        self.w = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _=None):
        if self.tip:
            return
        try:
            x = self.w.winfo_rootx() + 8
            y = self.w.winfo_rooty() + self.w.winfo_height() + 6
            self.tip = tk.Toplevel(self.w)
            self.tip.wm_overrideredirect(True)
            self.tip.wm_geometry("+%d+%d" % (x, y))
            tk.Label(self.tip, text=self.text, bg=T.PANEL, fg=T.FG, bd=0,
                     padx=10, pady=6, justify="left", font=T.FONT).pack()
        except tk.TclError:
            self.tip = None

    def _hide(self, _=None):
        if self.tip:
            try:
                self.tip.destroy()
            except tk.TclError:
                pass
            self.tip = None


class Toggle(tk.Frame):
    """方框勾选：选中是强调色方块 + 白色对勾。保留 `.get()` / `.set()` 接口。"""

    def __init__(self, master, text, var, bg=None, **kw):
        bg = bg or T.CARD
        tk.Frame.__init__(self, master, bg=bg, **kw)
        self.var = var
        self.cv = tk.Canvas(self, width=18, height=18, bg=bg, highlightthickness=0, bd=0)
        self.cv.pack(side="left")
        self.lb = tk.Label(self, text=text, bg=bg, fg=T.FG, font=T.FONT, cursor="hand2")
        self.lb.pack(side="left", padx=(7, 0))
        for w in (self.cv, self.lb):
            w.bind("<Button-1>", self._click)
            w.bind("<Enter>", lambda e: self.lb.configure(fg="#ffffff"))
            w.bind("<Leave>", lambda e: self.lb.configure(fg=T.FG))
        self.var.trace_add("write", lambda *a: self._draw())
        self._draw()

    def _click(self, _=None):
        self.var.set(not self.var.get())

    def set(self, v):
        self.var.set(bool(v))

    def get(self):
        return bool(self.var.get())

    def _draw(self):
        c = self.cv
        c.delete("all")
        if self.var.get():
            c.create_rectangle(1, 1, 17, 17, fill=T.ACCENT, outline=T.ACCENT_HOVER)
            c.create_line(4.6, 9.2, 7.6, 12.2, 13.4, 5.8, fill="#ffffff",
                          width=2, capstyle="round", joinstyle="round")
        else:
            c.create_rectangle(1, 1, 17, 17, fill=T.FIELD, outline=T.FIELD_LINE)


def card(master, **kw):
    """卡片容器：统一底色 + 描边，返回 Frame（由调用方 pack/grid）。"""
    return tk.Frame(master, bg=T.CARD, highlightbackground=T.CARD_LINE,
                    highlightthickness=1, bd=0, **kw)


def caption(master, text, bg=None):
    """一行小灰字。"""
    return tk.Label(master, text=text, bg=bg or T.CARD, fg=T.DIM,
                    font=T.FONT_SMALL, anchor="w")


def flat_button(master, text, command, primary=False, width=None):
    """扁平按钮。primary=True 用强调色（全界面至多一处）。"""
    bg = T.ACCENT if primary else T.BTN
    fg = "#ffffff" if primary else T.FG
    hover = T.ACCENT_HOVER if primary else T.BTN_HOVER
    b = tk.Button(master, text=text, command=command, bg=bg, fg=fg,
                  activebackground=hover, activeforeground=fg,
                  disabledforeground=T.BTN_DEAD, relief="flat", bd=0, cursor="hand2",
                  font=T.FONT_BOLD if primary else T.FONT,
                  padx=16, pady=8 if primary else 6, highlightthickness=0, width=width)
    b.bind("<Enter>", lambda e: b.configure(bg=hover) if str(b["state"]) != "disabled" else None)
    b.bind("<Leave>", lambda e: b.configure(bg=bg) if str(b["state"]) != "disabled" else None)
    b._bg, b._hover = bg, hover
    return b


def logo(parent, bg):
    """标题区那个小图标：**用真图标**（深蓝底双环 + 橙色闪电），取不到才退回手绘。

    1.1.3 起不再画那个「紫圆 + 白十字」—— 那是占位符，跟着 exe 图标对不上，
    用户一眼就能看出「窗口里这个和桌面上那个不是一个软件」。
    真图标以 base64 内嵌在 `appicon.py`（见那里的注释：为什么不走打包带资源）。
    """
    try:
        from . import appicon
        img = appicon.photo(parent, scale=2)      # 68 -> 34
        lb = tk.Label(parent, image=img, bg=bg, bd=0, highlightthickness=0)
        lb._img = img                             # 关键：不留住引用会被回收成空白
        return lb
    except Exception:
        cv = tk.Canvas(parent, width=34, height=34, bg=bg, highlightthickness=0, bd=0)
        cv.create_oval(1, 1, 33, 33, fill=T.ACCENT, outline="")
        cv.create_rectangle(14, 8, 20, 26, fill="#ffffff", outline="")
        cv.create_rectangle(8, 14, 26, 20, fill="#ffffff", outline="")
        return cv


def grid2(master, bg=None):
    """两列等宽网格：左列放「只体检 / 先复制」，右列放「收编 / 关 AE」。

    §5 那条「复选框一排 5 个换行错位」就是靠这个函数在结构上保证的：
    两列各自 `grid` 到同一行，列宽由 `columnconfigure(weight=1)` 均分，
    子控件永远落在自己那一列里，窗口缩放也不会串行。
    """
    bg = bg or T.CARD
    f = tk.Frame(master, bg=bg)
    f.columnconfigure(0, weight=1, uniform="u")
    f.columnconfigure(1, weight=1, uniform="u")
    left = tk.Frame(f, bg=bg)
    right = tk.Frame(f, bg=bg)
    left.grid(row=0, column=0, sticky="nw")
    right.grid(row=0, column=1, sticky="nw")
    return f, left, right


class SlimScroll(tk.Canvas):
    """自绘细滚动条（10 px 宽）。

    为什么不用系统滚动条：这台机器上实测 tk.Scrollbar **无视配色**——
    `tk.Scrollbar(bg="#ff0000", troughcolor="#00ff00")` 画出来仍是系统灰
    240,240,240（relief/borderwidth 都不管用）；ttk 在 clam 下箭头也仍会亮。
    考屏截图上表现为目录列表右侧一条 10 px 宽的浅灰竖条，肉眼可见。
    滚动条是纯装饰 + 交互，自绘一个 Canvas 最省事，颜色 100% 可控。

    用法：`sw = SlimScroll(parent, target)`，pack 到目标右侧；
    构造时它会接管 `target.configure(yscrollcommand=self.set)`。
    """

    def __init__(self, master, target, track=None, thumb=None, hover=None,
                 width=10, bg=None):
        self.target = target
        self.c_track = track or T.FIELD
        self.c_thumb = thumb or T.BTN
        self.c_hover = hover or T.BTN_HOVER
        tk.Canvas.__init__(self, master, width=width, height=1, bg=bg or master.cget("bg"),
                           highlightthickness=0, borderwidth=0, takefocus=0)
        self._first, self._last = 0.0, 1.0
        self._drag = None
        self._track_item = None
        self._thumb_item = None
        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_drag", None))
        self.bind("<Enter>", lambda _e: self._tint(self.c_hover))
        self.bind("<Leave>", lambda _e: self._tint(self.c_thumb))
        target.configure(yscrollcommand=self.set)

    # ---------------------------------------------------- 目标回调进来的口子
    def set(self, first, last):
        try:
            self._first, self._last = float(first), float(last)
        except (TypeError, ValueError):
            return
        self._redraw()

    # ---------------------------------------------------- 绘制
    def _redraw(self):
        h = max(1, self.winfo_height())
        w = max(6, self.winfo_width())
        span = h - 2
        y0 = 1 + int(span * self._first)
        y1 = 1 + int(span * self._last)
        if y1 - y0 < 18:                      # 太短的滑块不好抓，给个最小高度
            mid = (y0 + y1) // 2
            y0, y1 = max(1, mid - 9), min(h - 1, mid + 9)
        if self._track_item is None:
            self._track_item = self.create_rectangle(0, 0, 1, 1, fill=self.c_track, width=0)
        self.coords(self._track_item, 0, 0, w, h)
        if self._thumb_item is None:
            self._thumb_item = self.create_rectangle(2, 1, w - 2, 2, fill=self.c_thumb, width=0)
        self.coords(self._thumb_item, 2, y0, w - 2, y1)
        # 内容一眼看得完时把滑块藏掉：别在深色底上留一坨色块
        self.itemconfigure(self._thumb_item,
                           state="hidden" if self._last - self._first >= 0.999 else "normal")

    def _tint(self, color):
        if self._thumb_item is not None:
            self.itemconfigure(self._thumb_item, fill=color)

    # ---------------------------------------------------- 交互
    def _on_press(self, e):
        self._drag = e.y
        frac = max(0.0, min(1.0, float(e.y) / max(1, self.winfo_height())))
        if frac < self._first or frac > self._last:   # 点到轨道：按页跳
            self.target.yview_moveto(max(0.0, frac - (self._last - self._first) / 2))

    def _on_drag(self, e):
        if self._drag is None:
            return
        dy = float(e.y - self._drag) / max(1, self.winfo_height())
        self._drag = e.y
        self.target.yview_moveto(max(0.0, min(1.0, self._first + dy)))


__all__ = ["Tip", "Toggle", "card", "caption", "flat_button", "logo", "grid2", "SlimScroll"]