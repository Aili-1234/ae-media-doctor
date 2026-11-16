# -*- coding: utf-8 -*-
"""aemd.ui.theme —— 颜色 / 字体 / 间距（界面外观的唯一出处）。

负责：
  · 色板（深色，一套）、字体元组、间距刻度 `PAD = 8` 的倍数
  · 语义色：OK / WARN / BAD / DIM（数字「--」用 DIM，不用 FG）

不负责：
  · 建控件（widgets.py）
  · 布局（window.py）

依赖：无（纯常量；不 import tkinter，方便测试时无显示环境也能 import）。
"""

# ---- 色板（沿用 v1.9.8 的观感，只统一命名）----
BG = "#16181d"          # 窗口底
PANEL = "#1c1f26"       # 面板
CARD = "#1a1d24"        # 卡片
CARD_LINE = "#272b35"   # 卡片描边
LINE = "#2a2e38"        # 分隔线
FG = "#d7dce5"          # 主文字
DIM = "#8b93a4"         # 次要文字 / 空值占位
ACCENT = "#7c5cff"      # 强调（全界面至多两处）
ACCENT_HOVER = "#8b6bff"
OK_DIM = "#5d8a6a"   # 灰绿：只表示"没有坏链接"，不表示"成功"，别抢强调色的戏
OK_C = "#7ed3a0"
WARN_C = "#e0a96c"
BAD_C = "#e07a6c"

# ---- 控件底色（按钮 / 输入框 / 日志区）----
BTN = "#232833"          # 次级按钮底
BTN_HOVER = "#2e3542"
BTN_DEAD = "#5d6472"     # 禁用文字
FIELD = "#20242c"        # 输入框 / 勾选框底
FIELD_LINE = "#3a4150"
LOG_BG = "#12141a"
LOG_FG = "#c8d0dc"
TRACK = "#2a2e38"        # 进度条槽

# ---- 字体 ----
FONT = ("Microsoft YaHei UI", 9)
FONT_BOLD = ("Microsoft YaHei UI", 9, "bold")
FONT_SMALL = ("Microsoft YaHei UI", 8)
FONT_TITLE = ("Microsoft YaHei UI", 15, "bold")
FONT_SUBTITLE = ("Microsoft YaHei UI", 10, "bold")
FONT_NUM = ("Microsoft YaHei UI", 20, "bold")   # 三宫格数字
FONT_NUM_LABEL = ("Microsoft YaHei UI", 8)
MONO = ("Consolas", 9)

# ---- 间距（8 的倍数，别写 5/7/13 这种）----
PAD = 8
PAD_S = 4
PAD_L = 16
GAP_MUL = 8

# ---- 窗口 ----
WIN_SIZE = (900, 820)   # 单屏密度验收尺寸（§5：900×820 不许出现横向滚动）