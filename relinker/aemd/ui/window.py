# -*- coding: utf-8 -*-
"""aemd.ui.window —— 主窗口（单屏密度，六块）。

布局（§5：900×820 不出横向滚动）：
  ① 工程文件（只读路径框 + 「选工程」）+ AE 版本下拉
  ② 素材搜索目录（列表 + 添加/移除/自动推断 + 「缺失后全盘补搜」）
  ③ 选项条 2 列网格：左「只体检 / 先复制一份再改」｜ 右「收编到 Footages+下拉 / 关掉本次启动的 AE」
  ④ 按钮行：主按钮独占一行左；`停止` `打开日志文件夹` `缺失清单` `手动模式…` 次级右对齐
  ⑤ 三宫格（已在线 / 本次重连 / 仍缺失），数字大，空值 `--` 用 DIM
  ⑥ 日志区，高度自适应，空状态一行灰字

负责：
  · 窗口与控件装配、事件绑定、把用户动作翻译成 `runner.Run` + `runner.start`
  · 接收 `Run` 的轮询结果（poll）并刷新界面
  · R12 提示条（`updater.stamp_changed` 每 20 秒一次 + 「立即重启」）
  · 诊断包按钮（`diag.write_zip`）

不负责：
  · 任何业务判断（关不关 AE、启动哪种模式、索引三态全是下层的事）
  · 任何 subprocess / taskkill（§2：ui 里出现即违规，check_imports 会拦）
  · 写报告、建索引

依赖：`.theme` `.widgets` `.worker` `..runner` `..cfg` `..paths` `..updater` `..const` `..reports` `..diag`
"""

import os
import tkinter as tk
from tkinter import filedialog, simpledialog, ttk

from .. import cfg as v2cfg
from .. import const, diag, mapping, paths, reports, runner, updater
from . import theme as T
from . import widgets as W
from . import worker

POLL_MS = 400            # 轮询间隔（一次 poll 不 sleep、不阻塞）
UPDATE_MS = 20000        # R12：每 20 秒比一次 exe 指纹
DIALOG_TITLE = "拾链"

# 素材收进工程的三态文案：一个下拉说清「做不做 + 怎么做」（屏幕上不许有两个并列开关）
# 2026-11-15 拍板：「收编」→「收进工程」（术语换人话），下拉项不再重复标签前缀
CONS_OFF = "不收进"
CONS_COPY = "复制到 Footages"
CONS_MOVE = "移进 Footages"
CONS_CHOICES = (CONS_OFF, CONS_COPY, CONS_MOVE)
CONS_LABEL = "素材收进工程"
# 选项区两列共用的行高（8 的倍数）。左右两列都按它排 3 行，同一行的左标签与右控件
# 才不会错位 —— 抽检看到过「左列两条之间多出约 36 px 空档、右列没有」。
OPT_ROW_H = 24

# 日志区的中文短标签表：**只做显示层映射**，日志文件里仍然写 RELINK/MISSING/SKIP/LAUNCH/A2
# （售后排障要 grep 原标签，绝不能改 runner 写文件的内容）
LOG_TAG_CN = (
    ("RELINKED", "重连"), ("RELINK", "重连"), ("MISSING", "缺失"), ("FAILED", "失败"),
    ("SKIP", "跳过"), ("LAUNCH", "已启动 AE"), ("A2", "等待脚本"), ("INDEX", "补搜"),
    ("SUMMARY", "汇总"), ("SAVED", "已保存"), ("SAVE", "保存"), ("NOTE", "注意"),
    ("WARN", "警告"), ("FAIL", "失败"), ("TIME", "耗时"), ("CONSOLIDATE", "收进工程"),
    ("CLOSE", "关闭"), ("CANCEL", "已取消"), ("DONE", "完成"), ("OPEN", "打开"),
    ("SCRIPT", "脚本"), ("PROJECT", "工程"), ("ERR", "错误"),
)

HELP_TEXT = """【三步】

1. 点「选工程…」挑一个 .aep
2. 目录列表里放上素材可能在的地方（「自动加目录」会按工程路径猜几个）
3. 点「开始修复」

【它会做什么】

· 先复制一份再改（默认勾上），原工程一个字节都不动
· 让 AE 打开那份副本，按文件名把丢失的素材重新接上
· 结果写在工程的 .ae_doctor\\ 里，过程日志在本程序的 logs 目录

【还缺几条，怎么办】

「替换映射…」里可以把认不出的素材手工指到本地的某个文件（改过名的、换了扩展名的都行）。
指过一次就记住；「这次还缺的」里选中一条点「指定替身…」最快。
目标文件被移走时，日志会如实写「映射表指向的文件不存在」，不会假装接上。

【AE 那点事】

勾上「跑完关掉本程序启动的 AE」才会关，只关这一次由本程序拉起的那个。
不是本程序启动的 AE 不会被关；已经有 AE 在跑时，会先提示关掉再开始。
"""


class MappingDialog(object):
    """「替换映射…」对话框（1.1.0）。

    设计口径和主界面是同一套，不是另起一张脸：
      · 底是 `T.BG`，内容装在 `W.card` 里（同主界面六块卡片的描边与底色）
      · 间距只用 8 的倍数（`T.PAD/PAD_S/PAD_L`），字只用 `T.FONT*`
      · 强调色（`primary=True`）**只在「保存」这一处**，且没改动时压暗成灰底灰字
        （全界面至多两处强调色的老规矩）
      · 列表用 Tk 原生 Listbox 配自绘 `SlimScroll`：不用 ttk.Treeview，因为它在深色
        主题下会自带系统表头浅灰，和主界面打架（p3_ui 的 L 项探针就是抓这个的）

    两列为什么是两个 Listbox：中文文件名宽度不一，用一个 Listbox 拼「a -> b」永远
    对不齐。两列 + 同步选择/同步滚动，才是真对齐 —— 这套「一对列表」被包成
    `_table()`，已指定的替身与这次缺的都用它（两处一份实现）。
    """

    W = 780                 # 设计宽度；内容更宽时以内容为准

    def __init__(self, parent, table=None, missing=None, on_saved=None, log=None):
        self.parent = parent
        self.table = dict(table or {})
        # 1.1.7：打开时的快照。存盘走三方合并（base/mine/disk），
        # 否则界面开着一天、中间命令行跑过一轮，后存的会把对方的整表吃掉。
        self._base = dict(self.table)
        self.missing = list(missing or [])
        self.on_saved = on_saved
        self.log = log or (lambda msg, tag=None: None)
        self._dirty = False
        self._sync_lock = False
        self._rows = []

        top = tk.Toplevel(parent)
        self.top = top
        top.title("替换映射")
        top.configure(bg=T.BG)
        top.transient(parent)
        top.resizable(False, False)
        top.bind("<Escape>", lambda e: self.close())

        root = tk.Frame(top, bg=T.BG)
        root.pack(fill="both", expand=True, padx=T.PAD_L, pady=T.PAD_L)

        head = tk.Frame(root, bg=T.BG)
        head.pack(fill="x")
        tk.Label(head, text="替换映射", bg=T.BG, fg=T.FG,
                 font=T.FONT_TITLE).pack(side="left")
        tk.Label(head, text="认不出的素材，指一次就记住", bg=T.BG, fg=T.DIM,
                 font=T.FONT_SMALL).pack(side="left", padx=(T.PAD, 2), pady=(6, 0))
        W.flat_button(head, "关闭", self.close).pack(side="right")

        # ---------------- 卡片一：已指定的替身
        card = W.card(root)
        card.pack(fill="x", pady=(T.PAD, 0))
        inner = tk.Frame(card, bg=T.CARD)
        inner.pack(fill="x", padx=T.PAD_L, pady=T.PAD)

        hrow = tk.Frame(inner, bg=T.CARD)
        hrow.pack(fill="x")
        cap = W.caption(hrow, "已指定的替身")
        cap.pack(side="left")
        W.Tip(cap, "左列是工程里认不出的那个名字，右列是我这儿实际要用的文件。\n"
                   "命中就直接用它、不再走自动匹配；指过的会记住，下次不用再指。\n"
                   "目标文件被移走会如实报「映射表指向的文件不存在」，不假装接上。")
        self.lb_count = W.caption(hrow, "")
        self.lb_count.pack(side="right")

        self._hline = tk.Frame(inner, bg=T.LINE, height=1)
        self._hline.pack(fill="x", pady=(T.PAD_S, 0))

        self.body, self.lb_key, self.lb_val = self._table(
            inner, 6, headers=("工程里认不出的名字", "实际用的文件"))
        self.empty = tk.Frame(inner, bg=T.CARD)
        tk.Label(self.empty, text="还没有指定过替身", bg=T.CARD, fg=T.DIM,
                 font=T.FONT).pack(pady=(T.PAD_L, T.PAD_S))
        tip = ("在下面「这次还缺的」里选中一条点「指定替身…」，\n"
               "或者直接点「添加替身…」自己挑一个文件。") if self.missing else \
              ("认不出的素材，点「添加替身…」把它指到本地那份文件上，\n"
               "指过一次就记住，下次同一个工程不用再指。")
        tk.Label(self.empty, text=tip, bg=T.CARD, fg=T.DIM,
                 font=T.FONT_SMALL, justify="center").pack()

        bar = tk.Frame(inner, bg=T.CARD)
        bar.pack(fill="x", pady=(T.PAD, 0))
        W.flat_button(bar, "添加替身…", self.add_one).pack(side="left")
        W.flat_button(bar, "删除所选", self.del_one).pack(side="left", padx=(T.PAD_S, 0))
        self.btn_save = W.flat_button(bar, "保存", self.save_all, primary=True)
        self.btn_save.pack(side="right")

        # ---------------- 卡片二：这次还缺的（只有跑过并且真缺件时才出现）
        self.lb_miss = None
        self.lb_miss_path = None
        if self.missing:
            card2 = W.card(root)
            card2.pack(fill="x", pady=(T.PAD, 0))
            inner2 = tk.Frame(card2, bg=T.CARD)
            inner2.pack(fill="x", padx=T.PAD_L, pady=T.PAD)
            h2 = tk.Frame(inner2, bg=T.CARD)
            h2.pack(fill="x")
            cap2 = W.caption(h2, "这次还缺 %d 条" % len(self.missing))
            cap2.configure(fg=T.WARN_C)
            cap2.pack(side="left")
            W.Tip(cap2, "选中一条，点右边「指定替身…」挑出你本地的那份。\n"
                        "存下来就写进替身表，下次同一个工程直接按它接。")
            W.flat_button(h2, "指定替身…", self.assign).pack(side="right")
            wrap, self.lb_miss, self.lb_miss_path = self._table(
                inner2, min(6, max(2, len(self.missing))),
                headers=("缺的名字", "工程里记的原路径"))
            wrap.pack(fill="x", pady=(T.PAD_S, 0))
            for m in self.missing:
                self.lb_miss.insert("end", m.get("name") or "?")
                self.lb_miss_path.insert("end", m.get("original") or "")
            self._fit_scroll(self.lb_miss)

        self.var_msg = tk.StringVar(value="选中任意一行，这里会显示完整路径")
        self.lb_msg = tk.Label(root, textvariable=self.var_msg, bg=T.BG, fg=T.DIM,
                               font=T.FONT_SMALL, anchor="w")
        self.lb_msg.pack(fill="x", pady=(T.PAD_S, 0))

        self._render()
        self._center()
        try:
            top.grab_set()
        except tk.TclError:
            pass

    # ------------------------------------------------------------ 装配小件

    @staticmethod
    def _mk_list(master, height):
        """统一样式的列表。`exportselection=False` 是两个列表能同步选择的前提：
        默认 True 时，选中另一个列表会把上一个的选择清掉。"""
        return tk.Listbox(master, height=height, bg=T.FIELD, fg=T.FG,
                          selectbackground=T.ACCENT, selectforeground="#ffffff",
                          highlightthickness=0, borderwidth=0, font=T.FONT,
                          activestyle="none", exportselection=False)

    def _table(self, parent, height, weights=(4, 7), headers=None):
        """一对同步的列表（左窄右宽）+ 一条自绘细滚动条，可选两列小标题。

        返回 `(frame, 左列表, 右列表)`。同步的三件事都在这里一次接好：
        选择、鼠标滚轮、滚动条视野 —— 两处调用方都不用再操心。

        小标题必须和列表在**同一个 grid** 里：滚动条那一列会占掉 10px，
        放到另一个 Frame 里各算列宽，权重分下来边界就会差几像素（考屏可见）。
        """
        f = tk.Frame(parent, bg=T.CARD)
        f.columnconfigure(0, weight=weights[0], uniform="map")
        f.columnconfigure(1, weight=weights[1], uniform="map")
        top_row = 0
        if headers:
            for i, text in enumerate(headers):
                tk.Label(f, text=text, bg=T.CARD, fg=T.DIM, font=T.FONT_SMALL,
                         anchor="w").grid(row=0, column=i, sticky="ew",
                                          padx=(0, 0) if i == 0 else (T.PAD, 0),
                                          pady=(0, T.PAD_S))
            top_row = 1
        left = self._mk_list(f, height)
        left.grid(row=top_row, column=0, sticky="nsew")
        right = self._mk_list(f, height)
        right.grid(row=top_row, column=1, sticky="nsew", padx=(T.PAD, 0))
        sb = W.SlimScroll(f, left, track=T.FIELD, thumb=T.BTN, bg=T.CARD)
        sb.grid(row=top_row, column=2, sticky="ns", padx=(T.PAD_S, 0))
        left._sb = sb            # 内容没超一屏时由 _fit_scroll 收掉它
        f.rowconfigure(top_row, weight=1)
        left.configure(yscrollcommand=lambda a, b: (sb.set(a, b), self._sync_view(left, right)))
        left.bind("<<ListboxSelect>>", lambda e: self._sync_sel(left, right))
        right.bind("<<ListboxSelect>>", lambda e: self._sync_sel(right, left))
        for w in (left, right):
            w.bind("<MouseWheel>", lambda e, a=left, b=right: self._on_wheel(e, a, b))
        left.bind("<Delete>", lambda e: self.del_one())
        return f, left, right

    def _center(self):
        """宽度取 max(设计宽度, 内容需要)，高度**跟着内容走**，再对齐父窗口居中。

        高度写死过一个 620，考屏出来卡片底下是一片空黑 —— 对话框里最贵的像素
        是「什么都没说」的那几行，宁可靠内容撑。
        """
        self.top.update_idletasks()
        try:
            px, py = self.parent.winfo_rootx(), self.parent.winfo_rooty()
            pw = self.parent.winfo_width()
        except tk.TclError:
            px = py = pw = 0
        w = max(self.W, self.top.winfo_reqwidth())
        h = self.top.winfo_reqheight()
        x = px + max(0, (pw - w) // 2)
        y = max(0, py + 40)
        self.top.geometry("%dx%d+%d+%d" % (w, h, x, y))

    # ------------------------------------------------------------ 渲染

    def _render(self):
        """把 self.table 摊到两列上；空表时换成空状态（别留两个空框）。"""
        self._rows = mapping.entries(self.table)
        for lb in (self.lb_key, self.lb_val):
            lb.delete(0, "end")
        for k, v in self._rows:
            self.lb_key.insert("end", k)
            self.lb_val.insert("end", v)
        # 行数少的时候别留一排空格子：高度跟着内容收
        self.lb_key.configure(height=min(6, max(3, len(self._rows))))
        self.lb_val.configure(height=min(6, max(3, len(self._rows))))
        self.lb_count.configure(text=("%d 条" % len(self._rows)) if self._rows else "")
        if self._rows:
            self.empty.pack_forget()
            self.body.pack(fill="x", after=self._hline)
            self.lb_key.yview_moveto(0)
            self.lb_val.yview_moveto(0)
            self._fit_scroll(self.lb_key)
        else:
            self.body.pack_forget()
            self.empty.pack(fill="x", after=self._hline)
            self._say("")
        if self._rows and not self.var_msg.get():
            self.var_msg.set("选中任意一行，这里会显示完整路径")
        self._set_dirty(self._dirty)

    def _say(self, text, tag=None):
        self.var_msg.set(text or "")
        self.lb_msg.configure(fg={"ok": T.OK_C, "bad": T.BAD_C}.get(tag, T.DIM))

    def _set_dirty(self, val):
        """保存键只在「改过」时亮。禁用时连底色一起压暗：主按钮是强调色底的，
        只把字变灰的话，整块紫仍然像可点（2026-11-16 考屏看到的就是这个假活跃）。"""
        self._dirty = bool(val)
        if self._dirty:
            self.btn_save.configure(state="normal", bg=T.ACCENT, fg="#ffffff")
        else:
            self.btn_save.configure(state="disabled", bg=T.BTN, fg=T.BTN_DEAD)

    # ------------------------------------------------------------ 联动

    @staticmethod
    def _fit_scroll(lb):
        """行数没超过列表高度就把细滚动条收掉。

        深色底上一根永远不满的竖线很容易被当成「这列还没加载完」——
        装饰只有在该出现的时候才出现。
        """
        sb = getattr(lb, "_sb", None)
        if sb is None:
            return
        if lb.size() > int(lb.cget("height")):
            sb.grid()
        else:
            sb.grid_remove()

    @staticmethod
    def _sync_view(src, dst):
        try:
            dst.yview_moveto(src.yview()[0])
        except tk.TclError:
            pass

    def _sync_sel(self, src, dst):
        if self._sync_lock:
            return
        sel = src.curselection()
        self._sync_lock = True
        try:
            dst.selection_clear(0, "end")
            if sel:
                dst.selection_set(sel[0])
                dst.activate(sel[0])
        finally:
            self._sync_lock = False
        # 长路径在列表里必然被截断：选中「已指定」的哪条，就把完整一行摊在页脚
        if sel and src is self.lb_key and sel[0] < len(self._rows):
            k, v = self._rows[sel[0]]
            self._say("%s  ->  %s" % (k, v))

    @staticmethod
    def _on_wheel(e, left, right):
        step = -1 if getattr(e, "delta", 0) > 0 else 1
        left.yview_scroll(step, "units")
        right.yview_scroll(step, "units")
        return "break"

    # ------------------------------------------------------------ 动作

    def add_one(self):
        f = filedialog.askopenfilename(parent=self.top, title="选「实际要用」的那个文件")
        if not f:
            return
        key = simpledialog.askstring("指的是哪个名字",
                                     "工程里认不出的那个文件名（也可填原完整路径）：",
                                     parent=self.top, initialvalue=os.path.basename(f))
        if not key or not key.strip():
            return
        self.table = mapping.add(self.table, key, f)
        self._set_dirty(True)
        self._render()
        self._say("已加入 %s —— 记得点「保存」" % key.strip())

    def del_one(self):
        sel = self.lb_key.curselection()
        if not sel or sel[0] >= len(self._rows):
            self._say("先在左边选中一条再删")
            return
        key = self._rows[sel[0]][0]
        self.table = mapping.remove(self.table, key)
        self._set_dirty(True)
        self._render()
        self._say("已移除 %s —— 记得点「保存」" % key)

    def save_all(self):
        try:
            merged, st = mapping.save_merged(self.table, self._base)
        except OSError as e:
            self._say("没存上：%s" % e, "bad")
            return
        self.table = merged
        self._base = dict(merged)
        self._set_dirty(False)
        self._render()
        n = len(self.table)
        extra = ""
        if st.get("kept"):
            extra = "，其中 %d 条是别处新增的（已一并保留）" % st["kept"]
        self.log("MAP     替身表已保存 %d 条：%s%s" % (n, mapping.default_path(), extra), "dim")
        self._say("已保存 %d 条替身%s —— 下次跑就按它接" % (n, extra), "ok")
        if self.on_saved is not None:
            self.on_saved(n)

    def assign(self):
        """把「这次缺的」里选中的一条指到本地某个文件，并存盘（点这一下就是要它生效）。"""
        if self.lb_miss is None:
            return
        sel = self.lb_miss.curselection()
        if not sel:
            self._say("先在列表里选中一条缺件")
            return
        m = self.missing[sel[0]]
        # 键必须用「文件原名」：JSX 里查表用的是 File.name / 完整路径，
        # 不是 AE 里的素材项名字（两者常不一样，用错就等于没指）。
        orig = (m.get("original") or "").replace("\\", "/")
        key = os.path.basename(orig) if orig and orig != "(no file)" else (m.get("name") or "")
        if not key:
            self._say("这条没记到原文件名，改用「添加替身…」手动填", "bad")
            return
        f = filedialog.askopenfilename(parent=self.top, title="选「%s」的替身" % key)
        if not f:
            return
        self.table = mapping.add(self.table, key, f)
        try:
            merged, st = mapping.save_merged(self.table, self._base)
        except OSError as e:
            self._set_dirty(True)
            self._render()
            self._say("没存上：%s" % e, "bad")
            return
        self.table = merged
        self._base = dict(merged)
        self._render()
        if st.get("kept"):
            self.log("MAP     另有 %d 条是别处新增的，已保留" % st["kept"], "dim")
        self.log("MAP     %s -> %s" % (key, f), "dim")
        self._say("已记下：%s —— 下次跑就按它接" % key, "ok")
        if self.on_saved is not None:
            self.on_saved(len(self.table))

    def close(self):
        try:
            self.top.grab_release()
        except tk.TclError:
            pass
        self.top.destroy()


class MainWindow(object):
    """主窗口。测试可以传一个自己造的 `tk.Tk` 进来（见 tests/test_p3_ui.py）。"""

    def __init__(self, root=None, cfg_data=None, installs=None):
        self.cfg = cfg_data if cfg_data is not None else (v2cfg.load_cfg() or {})
        self.root = root
        self._owns_root = root is None
        if self.root is None:
            self.root = tk.Tk()

        self.run_obj = None
        self.running = False
        # 1.1.7 批量：queue = 还没跑的工程；batch_total/done 用来显示「第几个/共几个」
        self.queue = []
        self.batch_total = 0
        self.batch_done = 0
        self.batch_rows = []
        self.inbox = worker.Inbox()          # 后台线程 → 主线程的日志/事件通道
        self.upd_bar = None
        self.upd_seen = None
        self.upd_dismissed = None
        self._stamp_path = updater.exe_path()
        self.upd_seen = updater.stamp(self._stamp_path)

        self.installs = list(installs) if installs is not None else self._safe_installs()
        self._build()
        self._arm_update_poll()

    # ------------------------------------------------------------ 装配

    @staticmethod
    def _safe_installs():
        try:
            from .. import aehost
            return aehost.find_installs()
        except Exception:
            return []

    def _build(self):
        r = self.root
        r.title("%s %s" % (const.APP_NAME, const.APP_VER))
        r.configure(bg=T.BG)
        r.geometry("%dx%d" % T.WIN_SIZE)
        r.minsize(760, 640)
        st = ttk.Style(r)
        # 原生 vista 主题会无视下面这些配色（下拉框、滚动条会白成一块），clam 才认颜色
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("Slim.Horizontal.TProgressbar", troughcolor=T.CARD, background=T.ACCENT,
                     bordercolor=T.CARD, lightcolor=T.ACCENT, darkcolor=T.ACCENT, thickness=4)
        # ttk 的默认控件是浅色的，在深色窗口里会亮成一块白 —— 逐个改成暗色
        st.configure("TCombobox", fieldbackground=T.FIELD, background=T.BTN, foreground=T.FG,
                     arrowcolor=T.DIM, bordercolor=T.FIELD_LINE, lightcolor=T.FIELD,
                     darkcolor=T.FIELD, selectbackground=T.FIELD, selectforeground=T.FG)
        st.map("TCombobox", fieldbackground=[("readonly", T.FIELD)],
               foreground=[("readonly", T.FG)], background=[("readonly", T.BTN)])
        st.configure("TScrollbar", background=T.BTN, troughcolor=T.FIELD, bordercolor=T.FIELD,
                     arrowcolor=T.DIM, lightcolor=T.BTN, darkcolor=T.BTN)
        r.option_add("*TCombobox*Listbox.background", T.FIELD)
        r.option_add("*TCombobox*Listbox.foreground", T.FG)
        r.option_add("*TCombobox*Listbox.selectBackground", T.ACCENT)
        r.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

        self._build_header()
        self._build_block_project()     # ①
        self._build_block_dirs()        # ②
        self._build_block_options()     # ③
        self._build_block_buttons()     # ④
        self._build_block_kpi()         # ⑤
        self._build_block_log()         # ⑥
        # 2026-11-15 P0：**启动时一次都不许写 settings.json**。
        # 原来这里有一句 `self._persist()`：界面按默认值建好，就把默认值原样写回。
        # 只要读配置那一步失败（真机上是一次带 BOM 的文件读不出来），
        # 这一句就会把「读不到」变成「用空值覆盖」—— 用户的工程路径与目录当场消失。
        # 正确姿势是：启动只读；用户真的动了某个开关，才由下面的 trace 写盘。
        self._cfg_last = self._current_updates()
        # 2026-11-15 t-18：内部赋值（截图工具、自动化、将来「恢复上次工程」）不许写盘。
        # 用一个计数闸门而不是「断开 trace 再连回去」——后者漏一次 reconnect 就永久失联。
        self._quiet = 0
        for v in (self.var_proj, self.var_ae, self.var_dry, self.var_copy, self.var_consmode,
                  self.var_save, self.auto_close, self.var_idx):
            v.trace_add("write", lambda *a: self._persist())

    def _build_header(self):
        head = tk.Frame(self.root, bg=T.BG)
        head.pack(fill="x", padx=20, pady=(16, 10))
        self.head_frame = head
        W.logo(head, T.BG).pack(side="left")
        ht = tk.Frame(head, bg=T.BG)
        ht.pack(side="left", padx=(10, 0))
        tk.Label(ht, text=const.APP_NAME, bg=T.BG, fg="#ffffff",
                 font=T.FONT_TITLE).pack(anchor="w")
        tk.Label(ht, text="修好 AE 工程丢失的素材", bg=T.BG, fg=T.DIM,
                 font=T.FONT_SMALL).pack(anchor="w")
        tk.Label(head, text="v" + const.APP_VER, bg=T.BTN, fg=T.DIM,
                 font=T.FONT_SMALL, padx=8, pady=2).pack(side="right", padx=(8, 0))
        self.btn_diag = W.flat_button(head, "导出诊断包", self.export_diag)
        self.btn_diag.pack(side="right", padx=(8, 0))
        W.Tip(self.btn_diag, "把最近的日志、设置、系统信息、AE 安装和崩溃记录打成一个 zip。\n"
                             "出问题时发这个 zip 就够了，不用截图。")
        self.btn_help = W.flat_button(head, "使用说明", self.show_help)
        self.btn_help.pack(side="right")
        # 「手动模式…」原来孤零零贴在主操作行最右（右侧一大片空白），移到这一排
        # 跟「使用说明 / 导出诊断包」放一起：都是不常点的入口，层级也一致。
        self.btn_manual = W.flat_button(head, "手动模式…", self.start_manual)
        self.btn_manual.pack(side="right", padx=(0, 8))
        W.Tip(self.btn_manual, "只生成脚本、不启动 AE。适合在 AE 里手动跑一次（Run Script File）。")

    # ① 工程文件 + AE 版本
    def _build_block_project(self):
        c = W.card(self.root)
        self.card_proj = c
        c.pack(fill="x", padx=20)
        inner = tk.Frame(c, bg=T.CARD)
        inner.pack(fill="x", padx=T.PAD_L, pady=W.T.PAD)

        top = tk.Frame(inner, bg=T.CARD)
        top.pack(fill="x")
        W.caption(top, "工程文件").pack(side="left")
        tk.Label(top, text="点【选工程】挑 .aep", bg=T.CARD, fg="#5d6472",
                 font=T.FONT_SMALL).pack(side="left", padx=(T.PAD, 0))
        arow = tk.Frame(top, bg=T.CARD)
        arow.pack(side="right")
        self.var_ae = tk.StringVar()
        self.ae_map = dict(self.installs)
        names = []
        for name, _path in self.installs:
            for year in range(const.AE_MIN_YEAR, const.AE_MAX_YEAR + 1):
                if name.endswith(" %d" % year):
                    names.append(name)
                    break
        self.cb_ae = ttk.Combobox(arow, textvariable=self.var_ae, values=names,
                                  state="readonly", width=22, font=T.FONT)
        if names:
            want = self.cfg.get("ae") or ""
            self.var_ae.set(want if want in names else names[0])
            self.cb_ae.current(names.index(self.var_ae.get()))
            self.cb_ae.pack(side="left", padx=(T.PAD, 0), ipady=1)
        else:
            tk.Label(arow, text="没装 AE %d–%d" % (const.AE_MIN_YEAR, const.AE_MAX_YEAR),
                     bg=T.CARD, fg=T.BAD_C, font=T.FONT_SMALL).pack(side="left", padx=T.PAD_S)

        row = tk.Frame(inner, bg=T.CARD)
        row.pack(fill="x", pady=(T.PAD, 0))
        self.var_proj = tk.StringVar(value=self.cfg.get("project") or "")
        self.ent_proj = tk.Entry(row, textvariable=self.var_proj, state="readonly",
                                 readonlybackground=T.FIELD, bg=T.FIELD, fg=T.FG,
                                 relief="flat", bd=0, font=T.FONT, highlightthickness=1,
                                 highlightbackground=T.FIELD_LINE, highlightcolor=T.ACCENT)
        self.ent_proj.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, T.PAD))
        W.flat_button(row, "选工程…", self.pick_project).pack(side="left")

        # 1.1.0 手工映射表：认不出的素材由人指一次，指过的记住
        mrow = tk.Frame(inner, bg=T.CARD)
        mrow.pack(fill="x", pady=(T.PAD_S, 0))
        self.btn_map = W.flat_button(mrow, "替换映射…", self.show_mapping)
        self.btn_map.pack(side="left")
        self.lb_map = tk.Label(mrow, text="", bg=T.CARD, fg="#5d6472", font=T.FONT_SMALL)
        self.lb_map.pack(side="left", padx=(T.PAD_S, 0))
        self.lb_batch = tk.Label(mrow, text="", bg=T.CARD, fg=T.ACCENT, font=T.FONT_SMALL)
        self.lb_batch.pack(side="right")
        self._refresh_map_hint()

    # ② 素材搜索目录
    def _build_block_dirs(self):
        c = W.card(self.root)
        self.card_dirs = c
        c.pack(fill="x", padx=20, pady=(T.PAD, 0))
        inner = tk.Frame(c, bg=T.CARD)
        inner.pack(fill="x", padx=T.PAD_L, pady=T.PAD)

        top = tk.Frame(inner, bg=T.CARD)
        top.pack(fill="x")
        W.caption(top, "素材搜索目录").pack(side="left")
        self.var_idx = tk.BooleanVar(value=bool(self.cfg.get("index_search", True)))
        t_idx = W.Toggle(top, "没找到就全盘搜", self.var_idx, bg=T.CARD)
        t_idx.pack(side="right")
        W.Tip(t_idx, "跑完还缺的，按文件名全盘再搜一遍，找到的目录自动加进列表，再点一次开始。\n"
                     "只按文件名匹配，同名文件可能认错，跑完看日志 补搜 行。")

        row = tk.Frame(inner, bg=T.CARD)
        row.pack(fill="x", pady=(T.PAD_S, 0))
        self.lb_dirs = tk.Listbox(row, height=3, bg=T.FIELD, fg=T.FG, selectbackground=T.ACCENT,
                                  selectforeground="#ffffff", highlightthickness=0, borderwidth=0,
                                  font=T.FONT, activestyle="none")
        self.lb_dirs.pack(side="left", fill="x", expand=True)
        # 自绘滚动条：系统 Scrollbar 在这台机器上无视配色（实测 bg/troughcolor 全被忽略，
        # 考屏是一条 10 px 浅灰竖条），见 widgets.SlimScroll 的 docstring
        sb = W.SlimScroll(row, self.lb_dirs, track=T.FIELD, thumb=T.BTN, bg=T.CARD)
        sb.pack(side="left", fill="y", padx=(T.PAD_S, 0))
        self.sb_dirs = sb
        # 2026-11-15 P0：**配置里的目录一律照收，不按「此刻是否存在」过滤**。
        # 原来这里有 `if os.path.isdir(d)`：移动硬盘没插、网络盘没连、外置盘换盘符时，
        # 用户的目录在界面上直接消失，接着任何一次写配置就把它们永久抹掉 ——
        # 与「读不到 BOM 就当没有配置」是同一类伤害。目录暂时不在，跑的时候自然扫不到，
        # 那是运行期的事实，不该在启动时改写用户的设置。
        for d in (self.cfg.get("dirs") or []):
            if isinstance(d, str) and d.strip():
                self.lb_dirs.insert("end", d)
        btns = tk.Frame(row, bg=T.CARD)
        btns.pack(side="left", fill="y", padx=(T.PAD, 0))
        W.flat_button(btns, "添加…", self.add_dir).pack(fill="x", pady=(0, T.PAD_S))
        W.flat_button(btns, "移除", self.del_dir).pack(fill="x", pady=(0, T.PAD_S))
        W.flat_button(btns, "自动加目录", self.guess_dirs).pack(fill="x")

    # ③ 选项条（2 列网格）
    def _build_block_options(self):
        c = W.card(self.root)
        self.card_opts = c
        c.pack(fill="x", padx=20, pady=(T.PAD, 0))
        inner = tk.Frame(c, bg=T.CARD)
        inner.pack(fill="x", padx=T.PAD_L, pady=T.PAD)

        # 两列**真网格**：3 行 × 2 列，行高由 grid 统一给，两边绝不会错位。
        # 原来左右各自 pack，左列 3 个 Toggle、右列「下拉 + 1 个 Toggle」行高不一致，
        # 抽检看到左列两条之间多出约 36 px 空档（右侧没有）。
        grid = tk.Frame(inner, bg=T.CARD)
        self.opt_grid = grid
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1, uniform="optcol")
        grid.columnconfigure(1, weight=1, uniform="optcol")
        left = tk.Frame(grid, bg=T.CARD)
        left.grid(row=0, column=0, sticky="nw")
        right = tk.Frame(grid, bg=T.CARD)
        right.grid(row=0, column=1, sticky="nw")
        # 两边都按 OPT_ROW_H 行高走 3 行：左列 3 个 Toggle，右列「Footages 下拉」「关 AE」「空」。
        # 行高统一 → 同一行的左标签与右控件中心线对齐（探针 N 量的是控件坐标差，肉眼看齐）。
        for fr in (left, right):
            fr.columnconfigure(0, weight=1)
            for r in range(3):
                fr.rowconfigure(r, minsize=OPT_ROW_H)

        self.var_dry = tk.BooleanVar(value=bool(self.cfg.get("dry_run", False)))
        self.var_copy = tk.BooleanVar(value=bool(self.cfg.get("copy_first", True)))
        self.var_consmode = tk.StringVar(value=self._cons_text())
        self.var_save = tk.BooleanVar(value=bool(self.cfg.get("save_project", True)))
        self.auto_close = tk.BooleanVar(value=bool(self.cfg.get("close_ae", False)))

        t_dry = W.Toggle(left, "只检查，不改工程", self.var_dry)
        t_dry.grid(row=0, column=0, sticky="w")
        W.Tip(t_dry, "只报告，不重连、不保存。想先看缺多少条就勾它。")
        t_copy = W.Toggle(left, "修复前先备份原工程", self.var_copy)
        t_copy.grid(row=1, column=0, sticky="w")
        W.Tip(t_copy, "修复直接改你打开的工程，动手前先备份一份 <名>_backup.aep 当还原点。\n"
                      "备份放在原工程旁边；不勾则直接改、不留备份。")

        # 收编到 Footages：一个下拉说清「做不做 + 怎么做」，不再用「复选框 + 另一个下拉」并列
        crow = tk.Frame(right, bg=T.CARD)
        crow.grid(row=0, column=0, sticky="w")
        tk.Label(crow, text=CONS_LABEL, bg=T.CARD, fg=T.FG,
                 font=T.FONT).pack(side="left")
        self.cb_cons = ttk.Combobox(crow, textvariable=self.var_consmode, width=24,
                                    state="readonly", font=T.FONT, values=CONS_CHOICES)
        self.cb_cons.pack(side="left", padx=(T.PAD, 0))
        W.Tip(self.cb_cons, "不收进：只重连，文件留在原地。\n"
                            "复制：Footages 里多一份（安全，占空间）。\n"
                            "移进：复制后确认大小一致，再删原文件（省空间，不能撤销）。")
        t_close = W.Toggle(right, "跑完关掉本程序启动的 AE", self.auto_close)
        t_close.grid(row=1, column=0, sticky="w")
        t_save = W.Toggle(left, "跑完保存工程", self.var_save)
        t_save.grid(row=2, column=0, sticky="w")
        W.Tip(t_save, "跑完在 AE 里按 Ctrl+S 把结果存进工程。取消勾选就只改不存，需自己保存。")
        W.Tip(t_close, "只关这一次由本程序拉起的 AfterFX（按进程号）。\n"
                       "不是本程序启动的 AE 不会被关；检测到已有 AE 会提示先关掉再开始。\n"
                       "取消勾选，AE 跑完留在打开状态，方便看结果。")

    # ④ 主操作行（主按钮与「停止」同排；三颗结果类按钮在日志卡标题行）
    def _build_block_buttons(self):
        wrap = tk.Frame(self.root, bg=T.BG)
        self.frame_buttons = wrap
        wrap.pack(fill="x", padx=20, pady=(T.PAD, 0))

        row = tk.Frame(wrap, bg=T.BG)
        row.pack(fill="x")
        self.btn_run = W.flat_button(row, const.RUN_TEXT, self.start, primary=True)
        self.btn_run.configure(font=T.FONT_SUBTITLE)
        self.btn_run.pack(side="left", ipady=2)
        self.btn_stop = W.flat_button(row, "停止", self.stop)
        self.btn_stop.configure(state="disabled")
        self.btn_stop.pack(side="left", padx=(T.PAD, 0), ipady=2)
        # 「手动模式…」已移到头部次级按钮组（原来孤零零贴在主操作行最右，
        # 右侧一大片空白，还跟「开始修复」挤在同一视线里，层级不清）。

        # 结果行与状态行合并成一行（原来「同一批数字」在屏幕上出现四次）
        self.var_result = tk.StringVar()
        self.var_status = tk.StringVar()
        self.var_line = tk.StringVar()
        # 上次退出时记着的工程还会自动填回来（本程序只记自己的设置，不碰 AE），
        # 那种情况下说「还没选工程」是假话 —— 换成「还没跑过」。
        self._result_text = const.LOG_EMPTY_HINT if self.cfg.get("project") else const.EMPTY_HINT
        self._result_tag = None
        self.lb_result = tk.Label(wrap, textvariable=self.var_line, bg=T.BG, fg=T.DIM,
                                  justify="left", anchor="w", wraplength=840, font=T.FONT)
        self.lb_result.pack(anchor="w", pady=(T.PAD_S, 0))
        self._refresh_line()

        self.prog = ttk.Progressbar(self.root, mode="indeterminate",
                                    style="Slim.Horizontal.TProgressbar")
        self._prog_slot = True

    # ⑤ 三宫格
    def _build_block_kpi(self):
        c = W.card(self.root)
        self.kpi_card = c
        c.pack(fill="x", padx=20, pady=(T.PAD, 0))
        krow = tk.Frame(c, bg=T.CARD, height=66)
        krow.pack(fill="x")
        krow.pack_propagate(False)
        self.k_ok = self._kpi(krow, "已在线", 0)
        self._sep(krow, 1)
        self.k_fix = self._kpi(krow, "本次接上", 2)
        self._sep(krow, 3)
        self.k_miss = self._kpi(krow, "还缺", 4)

    def _kpi(self, parent, label, col):
        cell = tk.Frame(parent, bg=T.CARD)
        cell.grid(row=0, column=col, sticky="nsew", padx=T.PAD_L, pady=T.PAD)
        parent.columnconfigure(col, weight=1)
        v = tk.Label(cell, text="--", bg=T.CARD, fg=T.DIM, font=T.FONT_NUM)
        v.pack(anchor="w")
        tk.Label(cell, text=label, bg=T.CARD, fg=T.DIM, font=T.FONT_NUM_LABEL).pack(anchor="w")
        return v

    def _sep(self, parent, col):
        f = tk.Frame(parent, bg=T.LINE, width=1)
        f.grid(row=0, column=col, sticky="ns")

    # ⑥ 日志区（标题行放「日志」+ 三颗结果类按钮）
    def _build_block_log(self):
        c = W.card(self.root)
        self.log_card = c
        c.pack(fill="both", expand=True, padx=20, pady=(T.PAD, T.PAD_L))
        head = tk.Frame(c, bg=T.CARD)
        head.pack(fill="x", padx=T.PAD_L, pady=(T.PAD_S, 0))
        cap = tk.Label(head, text="日志", bg=T.CARD, fg=T.FG, font=T.FONT_SUBTITLE)
        cap.pack(side="left")
        W.Tip(cap, "报告与缺件清单在工程的 .ae_doctor 里，过程日志在本程序的 logs 目录。\n"
                   "日志前缀是排障用的机器标签（RELINK / MISSING / SKIP / LAUNCH / A2），屏幕上显示中文。\n"
                   "出问题把「导出诊断包」发出来即可。")
        self.btn_log = W.flat_button(head, "日志文件夹", self.open_log_dir)
        self.btn_log.pack(side="right")
        self.btn_miss = W.flat_button(head, "缺失清单", self.show_missing)
        self.btn_miss.configure(state="disabled")
        self.btn_miss.pack(side="right", padx=(0, T.PAD))
        # 1.1.6：只重试缺件（跑完且真缺件时才可点）
        self.btn_only = W.flat_button(head, "只重试缺件", self.retry_missing)
        self.btn_only.configure(state="disabled")
        self.btn_only.pack(side="right", padx=(0, T.PAD))
        W.Tip(self.btn_only, "只按上一次的缺件清单重跑那几条，不重扫全盘。\n"
                             "补齐素材、或指定过替身之后点它，几秒就能看到结果。")
        self.btn_report = W.flat_button(head, "工程文件夹", self.open_report_dir)
        self.btn_report.pack(side="right", padx=(0, T.PAD))

        wrap = tk.Frame(c, bg=T.CARD)
        wrap.pack(fill="both", expand=True, padx=1, pady=(T.PAD_S, 1))
        self.txt = tk.Text(wrap, bg=T.LOG_BG, fg=T.LOG_FG, insertbackground=T.FG,
                           font=T.MONO, borderwidth=0, highlightthickness=0, wrap="none",
                           padx=12, pady=10, height=8)
        self.txt.pack(side="left", fill="both", expand=True)
        sb = W.SlimScroll(wrap, self.txt, track=T.LOG_BG, thumb=T.BTN, bg=T.LOG_BG)
        sb.pack(side="left", fill="y")
        self.sb_log = sb
        self.txt.configure(state="disabled")
        for tag, color in (("ok", T.OK_C), ("bad", T.BAD_C), ("warn", T.WARN_C),
                           ("dim", T.DIM), ("head", "#b79cff")):
            self.txt.tag_configure(tag, foreground=color)
        self._log_empty = True
        self.txt.configure(state="normal")
        self.txt.insert("end", const.LOG_EMPTY_HINT, "dim")
        self.txt.configure(state="disabled")

    # ------------------------------------------------------------ 小工具

    @staticmethod
    def display_line(msg):
        """把机器标签换成中文短标签（**只影响屏幕显示**，日志文件里仍是原标签）。"""
        s0 = msg or ""
        for tag, cn in LOG_TAG_CN:
            if s0.startswith(tag):
                rest = s0[len(tag):]
                if rest == "" or rest[0] in (" ", "\t"):
                    return cn + rest
        return s0

    def log(self, msg, tag=None):
        """往日志区追加一行（**只能在主线程调**）。"""
        self._log_empty = False
        self.txt.configure(state="normal")
        self.txt.insert("end", self.display_line(msg) + "\n", tag or ())
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _clear_log(self):
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")

    def _refresh_line(self):
        """结果行与状态行合成一行：`结果 · 状态`（状态为空时只显示结果）。"""
        st = self.var_status.get()
        self.var_line.set(("%s · %s" % (self._result_text, st)) if st else self._result_text)
        self.var_result.set(self._result_text)

    def set_result(self, text, tag=None):
        self._result_text = text or ""
        self._result_tag = tag
        self.lb_result.configure(fg={"ok": T.OK_C, "warn": T.WARN_C,
                                     "bad": T.BAD_C}.get(tag, T.DIM))
        self._refresh_line()

    def set_status(self, text):
        """状态位：PID / 计时 / 下一步这类次要信息，与结果同行显示。"""
        self.var_status.set(text or "")
        self._refresh_line()

    def _cons_text(self):
        """把配置里的 (consolidate, consolidate_mode) 折成一条下拉文案。

        2026-11-15 t-18：磁盘上现在只存机器值 `off/copy/move`，但**必须能读**三种
        历史中文文案（`不收进` / `复制到 Footages` / `移进 Footages`）和更早的
        布尔 `consolidate` —— 归一化统一交给 `cfg.consolidate_mode_value()`，
        这里只负责机器值 → 界面文案。
        """
        from .. import cfg as _cfg
        mode = _cfg.consolidate_mode_value(
            self.cfg.get("consolidate_mode"), self.cfg.get("consolidate", False))
        return {"off": CONS_OFF, "move": CONS_MOVE}.get(mode, CONS_COPY)

    def _cons_on(self):
        return self.var_consmode.get() != CONS_OFF

    def _roots(self):
        return [self.lb_dirs.get(i) for i in range(self.lb_dirs.size())]

    def _current_updates(self):
        """界面上这九项此刻的值 —— `_persist()` 唯一要写的东西。

        2026-11-15 t-18：`consolidate_mode` **只写机器值** `off/copy/move`，
        不写界面文案 —— 界面文案改字不该变成磁盘格式变化。
        """
        from .. import cfg as _cfg
        return {
            "project": self.var_proj.get(),
            "dirs": self._roots(),
            "ae": self.var_ae.get(),
            "dry_run": bool(self.var_dry.get()),
            "copy_first": bool(self.var_copy.get()),
            "save_project": bool(self.var_save.get()),
            "consolidate": self._cons_on(),
            "consolidate_mode": _cfg.consolidate_mode_value(
                self.var_consmode.get(), self._cons_on()),
            "close_ae": bool(self.auto_close.get()),
            "index_search": bool(self.var_idx.get()),
        }

    def _persist(self):
        """写配置。**只在值真的变了才写**（2026-11-15 P0）。

        两个作用：① 启动、以及界面上任何「程序自己改自己」的动作都不会产生写入
        ——配置文件的 mtime 只在用户动手时才变；② 值没变就不写，省得每次点击
        都把 settings.json 的 mtime 顶到最新（看起来像「程序一直在改我的配置」）。

        t-18 追加：程序内部赋值走 `_set_project_quiet()`，它把 `_quiet` 抬起来，
        于是这条 trace 进来也直接返回 —— 内部动作一次都不写盘。
        """
        if getattr(self, "_quiet", 0):
            return
        try:
            upd = self._current_updates()
        except Exception:
            return
        if upd == getattr(self, "_cfg_last", None):
            return
        try:
            v2cfg.merge_cfg(upd)
            self._cfg_last = upd
        except Exception:
            pass

    def _set_project_quiet(self, p):
        """**内部**改工程路径：界面跟着变，但不写盘（t-18）。

        谁该用它：截图工具、自动化脚本、将来「恢复上次工程」这类「程序自己设置」。
        谁不该用它：用户在「选工程…」对话框里确认之后的 `set_project()` ——
        那是用户真实编辑，必须落盘一次。

        实现用一个计数而不是「先摘 trace 再挂回去」：摘挂一旦漏一次，
        这个输入框就永久不再持久化，属于「不会报错的静默失能」。
        """
        self._quiet = getattr(self, "_quiet", 0) + 1
        try:
            self.var_proj.set(p)
        finally:
            self._quiet = max(0, getattr(self, "_quiet", 1) - 1)

    # ------------------------------------------------------------ 用户动作

    def pick_project(self):
        """选工程 —— 支持多选（接一整季/多集时不用一个个跑）。"""
        try:
            ps = filedialog.askopenfilenames(title="选 AE 工程（可按住 Ctrl 多选）",
                                             filetypes=[("After Effects 工程", "*.aep"),
                                                        ("所有文件", "*.*")])
        except Exception:
            ps = ()
        if not ps:
            return
        if isinstance(ps, str):          # 个别 Tk 版本返回 "a b" 这种串
            ps = ps.split()
        self.adopt_projects(list(ps))

    def adopt_projects(self, ps):
        """接住一批工程：第一个当当前工程，其余排队，跑完自动接下一个。"""
        ps = [x for x in (ps or []) if x]
        if not ps:
            return
        self.queue = list(ps[1:])
        self.batch_total = len(ps)
        self.batch_done = 0
        self.batch_rows = []
        self.set_project(ps[0])
        self._refresh_batch_hint()
        if len(ps) > 1:
            self.log("BATCH    已排 %d 个工程，跑完一个自动接下一个" % len(ps), "dim")

    def _refresh_batch_hint(self):
        if not hasattr(self, "lb_batch"):
            return
        if self.batch_total > 1:
            cur = min(self.batch_done + 1, self.batch_total)
            self.lb_batch.configure(text="批量 %d/%d" % (cur, self.batch_total))
        else:
            self.lb_batch.configure(text="")

    def set_project(self, p):
        """用户真实编辑（「选工程…」对话框确认 / 拖进来的文件）：必须落盘，且**只落一次**。

        t-18：赋值期间先把写入闸门抬起来，等目录也补完，最后统一 `_persist()` 一次。
        不这么做的话「工程变了」和「目录补了」会各写一次 —— 用户点一下选工程，
        settings.json 的 mtime 被顶两次。
        """
        self._set_project_quiet(p)
        if not self._roots():
            self.guess_dirs(silent=True)
        self._persist()

    def add_dir(self):
        d = filedialog.askdirectory(title="素材可能在哪个目录里")
        if d:
            self.lb_dirs.insert("end", d)
            self._persist()

    def del_dir(self):
        sel = list(self.lb_dirs.curselection())
        for i in reversed(sel):
            self.lb_dirs.delete(i)
        self._persist()

    def guess_dirs(self, silent=False):
        from .. import scanner
        p = self.var_proj.get()
        if not p:
            if not silent:
                self.set_result("先选工程才能猜目录", "warn")
            return []
        dirs = scanner.guess_dirs(p)
        have = set(self._roots())
        added = 0
        for d in dirs:
            if d not in have:
                self.lb_dirs.insert("end", d)
                added += 1
        self._persist()
        if not silent:
            self.set_result("新增 %d 个目录" % added, "ok")
        return dirs

    def _opts(self, only_missing=False):
        from .. import cfg as _cfg
        mode = _cfg.consolidate_mode_value(
            self.var_consmode.get(), self._cons_on())
        return runner.Options(
            dry=bool(self.var_dry.get()),
            save=bool(self.var_save.get()),
            consolidate=self._cons_on(),
            cons_dir="",
            cons_mode=("move" if mode == "move" else "copy"),
            copy_project=bool(self.var_copy.get()),
            close_own_ae=bool(self.auto_close.get()),
            only_missing=bool(only_missing),
        )

    def _ae_exe(self):
        return self.ae_map.get(self.var_ae.get(), "")

    def start(self, manual=False, only_missing=False):
        if self.running:
            return
        proj = self.var_proj.get().strip()
        if not proj:
            self.set_result("先选工程", "warn")
            return
        if not os.path.isfile(proj):
            self.set_result("工程文件不在了：%s" % proj, "bad")
            return
        self._persist()
        self._clear_log()
        run = runner.Run(proj, self._roots(), self._opts(only_missing),
                         log=lambda m, tag=None: self.inbox.push(("log", m, tag)),
                         ae_exe=self._ae_exe())
        if manual:
            from .. import aehost
            run.force_mode = aehost.LaunchMode.MANUAL
        self.run_obj = run
        self.running = True
        self._dry_run_used = bool(self.var_dry.get())   # 跑完态的状态行要按它换口径
        self.btn_run.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.btn_miss.configure(state="disabled")
        self.btn_only.configure(state="disabled")
        self.set_result("准备中…", None)
        self.set_status("准备中…")
        if not self.prog.winfo_ismapped():
            self.prog.pack(fill="x", padx=20, pady=(T.PAD, 0), before=self.kpi_card)
        self.prog.configure(mode="indeterminate", value=0)
        self.prog.start(12)
        self._sync_update_bar()
        worker.spawn(lambda: self._start_worker(run), name="aemd-start")
        self.root.after(POLL_MS, self._poll)

    def _start_worker(self, run):
        """后台线程：`Run.start()` 会阻塞（A2 最多等 90 秒 marker），所以不能放主线程。"""
        try:
            run.start()
            self.inbox.push(("started", getattr(run.mode, "value", ""), ""))
        except Exception as e:
            self.inbox.push(("start_failed", type(e).__name__, str(e)))

    def stop(self):
        # 1.1.7：点停止就**整批**停（清空队列），否则跑完这一个又会自己起下一个
        self.queue = []
        self.batch_total = 0
        self.batch_done = 0
        self._refresh_batch_hint()
        if self.run_obj is not None:
            self.run_obj.cancel()
        self.btn_stop.configure(state="disabled")
        self.set_status("正在停下…")

    def _stop_prog(self):
        """停进度条并复位到 0：不停下来会留一条实心色块（v1.9.8 截图里就有这个毛边）。"""
        try:
            self.prog.stop()
            self.prog.configure(mode="determinate", value=0)
            self.prog.pack_forget()      # 空闲时不占一条空槽
        except tk.TclError:
            pass

    def _abort_start(self):
        """启动阶段就失败了（参数错 / 复制失败 / 生成脚本失败）：把界面恢复到「没在跑」。"""
        self.running = False
        self._stop_prog()
        self.btn_run.configure(state="normal", text=const.RUN_TEXT, command=self.start)
        self.btn_stop.configure(state="disabled")
        self.run_obj = None
        self._sync_update_bar()

    # ------------------------------------------------------------ 轮询

    def _poll(self):
        try:
            self._drain()
            if self.run_obj is None:
                return
            p = self.run_obj.poll()
            self._render(p)
            if p.finished:
                self._finish(p)
                return
            if self.running:
                self.root.after(POLL_MS, self._poll)
        except tk.TclError:
            return

    def _drain(self):
        for item in self.inbox.drain():
            kind = item[0]
            if kind == "log":
                self.log(item[1], item[2])
            elif kind == "started":
                if item[1] == "manual":
                    self.log("MANUAL  这次不起 AE：请在 AE 里 File > Scripts > Run Script File 跑 %s"
                             % (self.run_obj.jsx_path if self.run_obj else ""), "dim")
            elif kind == "start_failed":
                name, msg = item[1], item[2]
                if name == "BlockedByForeignAE":
                    self.log("SKIP     已有 AE 在跑，本次不启动", "warn")
                    self.btn_run.configure(text=const.NEED_CLOSE_TEXT, command=self._probe_ae)
                    self.set_result("AE 已经开着，先关掉再点开始", "warn")
                else:
                    self.log("ERROR   %s: %s" % (name, msg), "bad")
                    self.set_result("没能开始：%s" % msg, "bad")
                    self._abort_start()

    def _render(self, p):
        for line in p.lines:
            self.log(line)
        if p.status_text:
            self.set_status(p.status_text)
        if p.state is runner.State.BLOCKED:
            self.btn_run.configure(text=const.NEED_CLOSE_TEXT, command=self._probe_ae)
            return
        if p.state in (runner.State.STARTING, runner.State.RUNNING):
            self.set_status(p.status_text or "运行中…")
        # 失败/取消的正文统一由 _finish 打（那里只打一次，这里不打，免得重复两遍）

    def _finish(self, p):
        # 失败/取消的原因在这里打进日志：一次运行只有这一处落账（_render 不重复打）
        if p.message and p.state in (runner.State.FAILED, runner.State.CANCELLED):
            for line in str(p.message).splitlines():
                self.log(line)
        self.running = False
        self._stop_prog()
        self.btn_run.configure(state="normal", text=const.RUN_TEXT, command=self.start)
        self.btn_stop.configure(state="disabled")
        self._sync_update_bar()
        res = getattr(self.run_obj, "result", None)
        st = (res.stats if res is not None else (p.report or {}).get("stats") or {})
        if st:
            ok_n = st.get("ok")
            # 0 已在线 用灰绿：它表示「没有坏链接」，不是「成功」，不该和强调色抢眼
            self.k_ok.configure(text=str(st.get("ok", "--")),
                                fg=T.OK_DIM if ok_n is not None else T.DIM)
            self.k_fix.configure(text=str(st.get("fixed", "--")), fg=T.ACCENT)
            miss = st.get("missing", 0) or 0
            self.k_miss.configure(text=str(st.get("missing", "--")),
                                  fg=T.BAD_C if miss else T.OK_C)
            self.set_result(p.message or reports.summary_line(st),
                            "ok" if not miss else "warn")
            # 跑完别一直挂「就绪」：换成能马上照做的下一步
            if getattr(self, "_dry_run_used", False):
                self.set_status("已检查完，报告在工程的 .ae_doctor 里")
            elif self.var_save.get():
                self.set_status("已保存，新工程和原件在同一目录")
            else:
                self.set_status("没保存：到 AE 里 Ctrl+S")
        else:
            self.set_result(p.message or "没拿到结果。", "bad")
        if res is not None:
            for w in res.warnings:
                self.log("WARN      %s" % w, "warn")
            for f in res.legacy_files:
                self.log("NOTE      工程目录里还有老版本留下的文件：%s（没有自动删）" % f, "dim")
            # 1.1.9：列出工程引用的第三方插件（不是"缺失"，是"用到了"——换机时好对照）
            tp = getattr(res, "third_party_plugins", None) or []
            if tp:
                self.log("PLUGINS   此工程引用了 %d 个第三方插件：%s"
                         % (len(tp), "、".join(tp)), "dim")
        mp = self.run_obj.missing_path() if self.run_obj is not None else ""
        self.btn_miss.configure(state="normal" if mp else "disabled")
        # 1.1.6：有缺件清单才让「只重试缺件」可点（没缺件时它没有意义）
        self.btn_only.configure(state="normal" if mp else "disabled")
        self.btn_report.configure(state="normal")
        self._advance_batch(p)

    def _advance_batch(self, p):
        """一批工程的推进：记一行结果 → 有下一个就自动起 → 全跑完给汇总。

        为什么自动起而不是等用户点：批量点的就是"我一次挑了 8 集，你去跑"；
        每集都要回来点一下，那多选就没意义了。
        """
        if not self.batch_total:
            return
        self.batch_done += 1
        res = getattr(self.run_obj, "result", None)
        st = (res.stats if res is not None else (p.report or {}).get("stats") or {})
        proj = getattr(self.run_obj, "project", "") or self.var_proj.get()
        good = p.state is runner.State.DONE
        self.batch_rows.append({
            "project": proj, "ok": good,
            "fixed": int(st.get("fixed") or 0),
            "missing": int(st.get("missing") or 0),
            "msg": p.message or "",
        })
        if self.queue:
            nxt = self.queue.pop(0)
            self.log("")
            self.log("==== 下一个 [%d/%d]：%s"
                     % (self.batch_done + 1, self.batch_total, nxt), "dim")
            self._set_project_quiet(nxt)
            self.var_proj.set(nxt)
            self._refresh_batch_hint()
            # 给 AE 收尾留一点时间（它刚被关/刚保存），再起下一个
            self.root.after(600, self.start)
            return

        n_ok = len([r for r in self.batch_rows if r["ok"]])
        n_bad = len(self.batch_rows) - n_ok
        tot_f = sum(r["fixed"] for r in self.batch_rows)
        tot_m = sum(r["missing"] for r in self.batch_rows)
        self.log("")
        self.log("==== 批量结果：%d 个工程，%d 成功 / %d 失败 ===="
                 % (len(self.batch_rows), n_ok, n_bad))
        for r in self.batch_rows:
            self.log("  %-4s  %-34s  接上 %d / 缺 %d%s"
                     % ("ok" if r["ok"] else "FAIL", os.path.basename(r["project"] or "?"),
                        r["fixed"], r["missing"],
                        ("" if r["ok"] else "  " + (r["msg"] or ""))))
        self.log("  合计：接上 %d 条，仍缺 %d 条" % (tot_f, tot_m))
        self.set_result("批量完成：%d 个工程，%d 成功 / %d 失败；共接上 %d 条，仍缺 %d 条"
                        % (len(self.batch_rows), n_ok, n_bad, tot_f, tot_m),
                        "ok" if (n_bad == 0 and tot_m == 0) else "warn")
        self.set_status("批量已跑完（%d 个工程）" % len(self.batch_rows))
        self.batch_total = 0
        self.batch_done = 0
        self._refresh_batch_hint()

    def _probe_ae(self):
        """点了「请先关闭 AE」之后：重新探一次，没有 AE 了就把主按钮恢复。"""
        try:
            from .. import aehost
            left = aehost.procs()
        except Exception:
            left = []
        if left:
            self.set_result("还有 %d 个 AE 在跑，关掉再点一次"
                            % (len(left), ", ".join(str(p) for p, _w in left)), "warn")
            return
        self.btn_run.configure(text=const.RUN_TEXT, command=self.start)
        self.set_result("AE 已关闭，可以开始", "ok")

    # ------------------------------------------------------------ 结果按钮

    def open_log_dir(self):
        d = paths.log_dir()
        try:
            paths.ensure_dir(d)
            os.startfile(d)
        except Exception as e:
            self.set_result("打不开日志文件夹（%s）" % e, "bad")

    def open_report_dir(self):
        p = self.var_proj.get()
        d = os.path.dirname(p) if p else ""
        if not d or not os.path.isdir(d):
            self.set_result("还没选工程", "warn")
            return
        try:
            os.startfile(d)
        except Exception as e:
            self.set_result("打不开（%s）" % e, "bad")

    # ------------------------------------------------------------ 1.1.0 手工映射表

    def _map_table(self):
        """当前替身表（读盘）。没有 / 读坏了都是空表 —— `mapping.load` 不抛。"""
        return mapping.load()

    def _refresh_map_hint(self):
        n = len(self._map_table())
        self.lb_map.configure(text=("已指定 %d 条替身" % n) if n else
                              "认不出的素材可以在这里指一次")

    def show_mapping(self):
        """「替换映射…」：已指定的替身 + 这次还缺的那几条，一屏管完。"""
        res = getattr(self.run_obj, "result", None) if self.run_obj is not None else None
        miss = list(getattr(res, "missing", []) or []) if res is not None else []
        MappingDialog(self.root, table=self._map_table(), missing=miss,
                      on_saved=self._after_map_saved, log=self.log)

    def _after_map_saved(self, n):
        """对话框存过一次之后：刷新工程卡上的计数，并把结果说在结果行上。"""
        self._refresh_map_hint()
        self.set_result("替身表已保存（%d 条）—— 下次跑就按它接" % n, "ok")

    def show_missing(self):
        p = self.run_obj.missing_path() if self.run_obj is not None else ""
        if not p or not os.path.isfile(p):
            self.set_result("这次没有缺件清单", "warn")
            return
        try:
            os.startfile(p)
        except Exception as e:
            self.set_result("打不开缺件清单（%s）" % e, "bad")

    def retry_missing(self):
        """只重试上一次没接上的那几条（用工程 .ae_doctor 里的缺件清单）。

        为什么值得单独一个按钮：大工程重跑一轮要重扫全盘、重开工程、重新逐条比对；
        而"我已经把缺的那 11 个素材拷进目录了"这种情况，只想再试这 11 条。
        """
        p = self.run_obj.missing_path() if self.run_obj is not None else ""
        if not p or not os.path.isfile(p):
            self.set_result("没有上一次的缺件清单，先完整跑一次", "warn")
            return
        self.start(only_missing=True)

    def start_manual(self):
        self.start(manual=True)

    def export_diag(self):
        self.set_result("正在收集诊断信息…", None)
        self.root.update_idletasks()
        p = diag.write_zip()
        if p:
            self.log("DIAG    诊断包已生成：%s" % p, "dim")
            self.set_result("诊断包已生成：%s" % p, "ok")
            try:
                os.startfile(os.path.dirname(p))
            except Exception:
                pass
        else:
            self.set_result("诊断包生成失败（详情看控制台输出）。", "bad")

    def show_help(self):
        top = tk.Toplevel(self.root)
        top.title("使用说明")
        top.configure(bg=T.BG)
        txt = tk.Text(top, bg=T.CARD, fg=T.FG, font=T.FONT, wrap="word", bd=0,
                      padx=T.PAD_L, pady=T.PAD, width=64, height=28)
        txt.insert("1.0", HELP_TEXT)
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True, padx=T.PAD, pady=T.PAD)

    # ------------------------------------------------------------ R12 重启条

    def _arm_update_poll(self):
        try:
            self.root.after(UPDATE_MS, self.poll_update)
        except tk.TclError:
            pass

    def poll_update(self):
        """R12：每 20 秒比一次 exe 指纹，变了露提示条（非模态、可关、修复中置灰）。"""
        try:
            cur = updater.stamp_changed(self.upd_seen, self._stamp_path)
            if cur and cur != self.upd_dismissed:
                self.upd_seen = cur
                self._show_update_bar()
        except tk.TclError:
            return
        finally:
            self._arm_update_poll()

    def _show_update_bar(self):
        if self.upd_bar is not None:
            self._sync_update_bar()
            return
        bar = tk.Frame(self.root, bg=T.PANEL, highlightbackground=T.LINE, highlightthickness=1)
        self.upd_bar = bar
        tk.Label(bar, text="已更新到新版，重启即可", bg=T.PANEL, fg=T.FG,
                 font=T.FONT).pack(side="left", padx=(T.PAD_L, 0), pady=T.PAD_S)
        W.flat_button(bar, "立即重启", self.restart_now).pack(side="left", padx=T.PAD)
        close = tk.Label(bar, text="×", bg=T.PANEL, fg=T.DIM, font=T.FONT, cursor="hand2")
        close.pack(side="right", padx=T.PAD)
        close.bind("<Button-1>", lambda e: self._dismiss_update_bar())
        bar.pack(fill="x", before=self.head_frame)
        self._sync_update_bar()

    def _sync_update_bar(self):
        if self.upd_bar is None:
            return
        try:
            btn = [w for w in self.upd_bar.winfo_children()
                   if isinstance(w, tk.Button) and w.cget("text") in ("立即重启", "跑完再重启")]
            for b in btn:
                if self.running:
                    b.configure(state="disabled", text="跑完再重启")
                else:
                    b.configure(state="normal", text="立即重启")
        except tk.TclError:
            pass

    def _dismiss_update_bar(self):
        self.upd_dismissed = self.upd_seen
        if self.upd_bar is not None:
            try:
                self.upd_bar.pack_forget()
                self.upd_bar.destroy()
            except tk.TclError:
                pass
            self.upd_bar = None

    def restart_now(self):
        """R12：起新进程 + 自己退出（**绝不 taskkill AE**）。"""
        if self.running:
            return
        try:
            updater.restart(self._stamp_path)
        except Exception as e:
            self.set_result("起新版本失败（%s），手动关掉本窗口再打开" % e, "bad")
            return
        self.root.quit()
        self.root.destroy()


def run():
    """入口：建窗口并进入 mainloop。"""
    win = MainWindow()
    try:
        from . import appicon
        win.root.iconphoto(True, appicon.photo(win.root, scale=1))   # 任务栏/Alt-Tab
    except Exception:
        pass
    win.root.mainloop()
    return win


__all__ = ["MainWindow", "run"]