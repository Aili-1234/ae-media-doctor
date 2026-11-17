# -*- coding: utf-8 -*-
"""P3 界面探针：单屏密度六块 + 结果一句话 + R12 重启条 + 诊断包。

跑法（在 media-doctor-v2 目录下）：

    python -m tests.test_p3_ui

为什么要拿真 Tk 窗口跑，而不是"读代码看着对"：界面这一层最容易出的错
不是语法，而是**装错了位置**——复选框串行错位、次级按钮跑到左边、
三宫格空值用了主文字色、结果句丢了 ambiguous 尾巴。这些只有真的建出窗口、
问控件自己「你在第几列 / 你挂在哪边」才查得出来。

安全：本测试**不启动 AE**、不碰真 settings.json（`merge_cfg` 打桩）、
不写真 `%APPDATA%`（`const.APP_HOME/LOG_DIR/WORK_DIR` 指向临时目录）。
"""

import io
import os
import shutil
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from aemd import console

console.harden()   # 默认 GBK 控制台下也不许因为符号打不出来而崩

import tkinter as tk
from tkinter import ttk

from aemd import const, diag, paths, runner
from aemd.ui import theme as T
from aemd.ui import widgets as W
from aemd.ui import window as U

PASS, FAIL = [], []


def ok(name):
    PASS.append(name)
    print("  PASS  %s" % name)


def bad(name, why):
    FAIL.append((name, why))
    print("  FAIL  %s\n        %s" % (name, why))


def case(name):
    def deco(fn):
        try:
            fn()
            ok(name)
        except Exception as e:
            bad(name, "%s: %s" % (type(e).__name__, e))
        return fn
    return deco


def need(cond, msg):
    if not cond:
        raise AssertionError(msg)


class Sandbox(object):
    """把数据目录挪到临时目录，并把 merge_cfg 打成空操作。"""

    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix="aemd_p3ui_")
        self.saved = (const.APP_HOME, const.LOG_DIR, const.WORK_DIR)
        self.app_home = os.path.join(self.tmp, "AE素材医生")
        self.logs = os.path.join(self.app_home, "logs")
        self.work = os.path.join(self.app_home, "work")
        const.APP_HOME, const.LOG_DIR, const.WORK_DIR = self.app_home, self.logs, self.work
        # cfg.cfg_dir() 只看 %APPDATA%，**不认 const.APP_HOME** —— 只挪常量的话，
        # 界面用例里任何一次 _persist() 都会写进真实配置（2026-11-17 就这么把用户的
        # settings.json 写成了临时目录里的「甲.aep」）。这里连环境变量一起沙箱。
        self.saved_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = self.tmp
        os.makedirs(self.logs, exist_ok=True)
        os.makedirs(self.work, exist_ok=True)
        from aemd import cfg as v2cfg
        self.real_merge = v2cfg.merge_cfg
        v2cfg.merge_cfg = lambda d=None, **k: dict(d or {})
        return self

    def __exit__(self, *a):
        const.APP_HOME, const.LOG_DIR, const.WORK_DIR = self.saved
        if getattr(self, "saved_appdata", None) is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self.saved_appdata
        from aemd import cfg as v2cfg
        v2cfg.merge_cfg = self.real_merge
        return False


class Win(object):
    """建一个真窗口，退出时销毁（每个用例一个，互不干扰）。"""

    def __enter__(self):
        # 每个窗口都进沙箱。以前 Sandbox 只在「诊断包」那一个用例里手动开过，其余用例
        # 全靠"没人写盘"侥幸安全 —— 2026-11-17 加了批量用例（会调 set_project → _persist），
        # 当场把用户真实的 settings.json 写成了临时目录里的「甲.aep」。
        self.sb = Sandbox()
        self.sb.__enter__()
        self.root = tk.Tk()
        self.root.withdraw()          # 用例期间不弹到用户脸上
        self.w = U.MainWindow(root=self.root, cfg_data={}, installs=[])
        self.root.update_idletasks()
        return self.w

    def __exit__(self, *a):
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        finally:
            self.sb.__exit__(*a)
        return False


# ------------------------------------------------------------------ A. 六块

@case("A. 六块全在：工程 / 目录 / 选项 / 按钮 / 三宫格 / 日志")
def _():
    with Win() as w:
        need(w.card_proj.winfo_exists() and w.card_dirs.winfo_exists()
             and w.card_opts.winfo_exists() and w.kpi_card.winfo_exists()
             and w.log_card.winfo_exists(), "有卡片没建出来")
        need(w.frame_buttons.winfo_exists(), "按钮行没建出来")
        # 每块的关键控件
        need(w.ent_proj.cget("state") == "readonly", "工程路径框应只读")
        need(w.cb_ae.winfo_exists(), "AE 版本下拉没了")
        need(w.lb_dirs.size() == 0, "目录列表初始应为空")
        need(w.btn_run.cget("text") == const.RUN_TEXT, "主按钮文案不对：%r" % w.btn_run.cget("text"))
        need(w.k_ok.cget("text") == "--" and w.k_fix.cget("text") == "--"
             and w.k_miss.cget("text") == "--", "三宫格初始应是 --")
        need(w.txt.cget("state") == "disabled", "日志区初始应只读")
        need(const.LOG_EMPTY_HINT in w.txt.get("1.0", "end"), "日志空状态那行灰字没了")


# ------------------------------------------------------- B. 选项条两列网格

@case("B. 选项条是 2 列网格：左列只体检/先复制，右列一个 Footages 下拉 + 关 AE")
def _():
    with Win() as w:
        g = w.opt_grid
        self_children = [c for c in g.grid_slaves(row=0)]
        need(len(self_children) == 2, "选项网格应恰好两列，实际 %d" % len(self_children))
        left = [c for c in self_children if int(c.grid_info()["column"]) == 0][0]
        right = [c for c in self_children if int(c.grid_info()["column"]) == 1][0]
        need(isinstance(left, tk.Frame) and isinstance(right, tk.Frame), "两列应是 Frame")
        need(left.grid_info()["row"] == right.grid_info()["row"] == 0, "两列应同一行（否则就是串行错位）")

        def flatten(fr):
            out = list(fr.winfo_children())
            for c in list(out):
                if isinstance(c, tk.Frame):
                    out.extend(c.winfo_children())
            return out

        lt = [c for c in flatten(left) if isinstance(c, W.Toggle)]
        rt = [c for c in flatten(right) if isinstance(c, W.Toggle)]
        # 2026-11-15 文案精简：左列 3 个（只检查 / 先复制 / 跑完保存工程）
        need(len(lt) == 3, "左列应是 3 个 Toggle，实际 %d" % len(lt))
        # 右列只允许 1 个 Toggle：Footages 已经并进下拉，屏幕上不许有两个并列开关
        need(len(rt) == 1, "右列应只有 1 个 Toggle（关 AE 那个），实际 %d" % len(rt))
        rtexts = [t.lb.cget("text") for t in rt]
        need(any(x.strip() == "跑完关掉本程序启动的 AE" for x in rtexts),
             "右列的 Toggle 应是关 AE：%r" % (rtexts,))
        need(not any("你自己" in x or "你的" in x for x in rtexts),
             "复选框里不许再出现第二人称：%r" % (rtexts,))
        # Footages：一个三态下拉说清「做不做 + 怎么做」
        combos = [c for c in flatten(right) if isinstance(c, ttk.Combobox)]
        need(len(combos) == 1, "右列应恰好一个下拉（收编到 Footages），实际 %d" % len(combos))
        values = tuple(combos[0].cget("values"))
        need(values == U.CONS_CHOICES, "收编下拉的选项不对：%r" % (values,))
        need(U.CONS_OFF == "不收进", "三态第一项应能明确表示不收进")
        labels = [c.cget("text") for c in flatten(right)
                  if isinstance(c, tk.Label) and c.cget("text")]
        need(any("素材收进工程" in str(x) for x in labels),
             "下拉前面应有一句标签说明它是什么：%r" % (labels,))
        # 左列文案（§5 的 2 列排版清单 + 文案精简表）
        for want in ("只检查", "修复前先备份原工程", "跑完保存工程"):
            need(any(want in t.lb.cget("text") for t in lt), "缺复选框：%r" % want)


# --------------------------------------------------------- C. 主次按钮层级

@case("C. 主操作行只有「开始修复 / 停止」；手动模式进头部次级组；结果类按钮在日志卡标题行")
def _():
    with Win() as w:
        need(w.btn_run.pack_info()["side"] == "left", "主按钮应在左")
        # 2026-11-15 版式：主按钮与「停止」同排，不再各占一行
        need(w.btn_stop.master is w.btn_run.master, "「停止」应与主按钮同排")
        need(w.btn_stop.pack_info()["side"] == "left", "「停止」应在主按钮右边")
        # 2026-11-15 二次排版：「手动模式…」移出主操作行（原来贴在主操作行最右，
        # 右侧一大片空白，且与主按钮同视线、层级不清）→ 进头部「使用说明/导出诊断包」那组。
        need(w.btn_manual.master is not w.btn_run.master,
             "手动模式不该再留在主操作行")
        need(w.btn_manual.master is w.btn_help.master,
             "手动模式应与「使用说明 / 导出诊断包」同排")
        need(w.btn_manual.pack_info()["side"] == "right", "手动模式应在头部右侧次级按钮组")
        # 主操作行右侧不许再挂孤儿按钮：那一行只允许主按钮与停止两颗
        row_children = [c for c in w.frame_buttons.winfo_children()
                        if c is not w.lb_result]
        need(len([c for c in row_children if c is not w.prog]) <= 2,
             "主操作行只该有主按钮与停止，实际 %d 个控件" % len(row_children))
        # 三颗结果类按钮移进日志卡标题行（右侧）
        for b, name in ((w.btn_report, "工程文件夹"), (w.btn_miss, "缺失清单"), (w.btn_log, "日志文件夹")):
            need(b.master is not w.btn_run.master, "%s 应移进日志卡标题行" % name)
            need(b.pack_info()["side"] == "right", "%s 应在标题行右对齐，实际 %r"
                 % (name, b.pack_info()["side"]))
        need(w.btn_report.master.master is w.log_card, "结果类按钮应挂在日志卡里")
        # 结果行与状态行同处一行（同一个 Label）
        need(w.lb_result.master is w.frame_buttons, "结果行应在按钮区")


# --------------------------------------------- D/E. 三宫格 + 一句话结果

class _Res(object):
    def __init__(self, stats, summary="", warnings=None, legacy=None):
        self.stats = stats
        self.summary = summary
        self.warnings = warnings or []
        self.legacy_files = legacy or []


class _Poll(object):
    def __init__(self, state, message="", report=None):
        self.state = state
        self.message = message
        self.report = report
        self.lines = []
        self.status_text = ""
        self.finished = state.finished()


def _finish(w, stats, message="", summary=""):
    w.run_obj = type("R", (), {})()
    w.run_obj.result = _Res(stats, summary)
    w.run_obj.missing_path = lambda: ""
    w._finish(_Poll(runner.State.DONE, message))


@case("D. 三宫格：掉数字、仍缺失>0 用警示色、空值用 DIM")
def _():
    with Win() as w:
        need(w.k_ok.cget("fg") == T.DIM, "空值应 DIM 灰")
        _finish(w, {"ok": 0, "fixed": 21, "missing": 11, "ambiguous": 1})
        need(w.k_fix.cget("text") == "21", "本次重连应为 21，实际 %r" % w.k_fix.cget("text"))
        need(w.k_miss.cget("text") == "11" and w.k_miss.cget("fg") == T.BAD_C,
             "仍缺失 11 应该用警示色")
        _finish(w, {"ok": 3, "fixed": 0, "missing": 0})
        need(w.k_miss.cget("fg") == T.OK_C, "缺 0 时应是正常色")
        # 「0 已在线」不许用饱和绿：它表示没有坏链接，不是成功，别跟强调色抢
        need(w.k_ok.cget("fg") == T.OK_DIM,
             "0 已在线 应该用灰绿 OK_DIM，实际 %r" % (w.k_ok.cget("fg"),))
        need(T.OK_DIM != T.OK_C, "OK_DIM 不能和 OK_C 同色")


@case("E. 一句话结论必须带 ambiguous 尾巴（用户亲口要的口径）")
def _():
    with Win() as w:
        _finish(w, {"ok": 0, "fixed": 21, "missing": 11, "ambiguous": 1})
        got = w.var_result.get()
        need(got == "接上 21 条，还缺 11 条，另有 1 条是按同名文件猜的，可能要核对",
             "结果句不对：%r" % got)
        # 没有猜的就不许硬加尾巴
        _finish(w, {"ok": 3, "fixed": 2, "missing": 0, "ambiguous": 0})
        need(w.var_result.get() == "接上 2 条，还缺 0 条", "无猜的时候不该有尾巴：%r" % w.var_result.get())


# ------------------------------------------------------------ F. 失败/取消

@case("F. 失败：原因逐行进日志，主按钮恢复可点")
def _():
    with Win() as w:
        w.running = True
        w.run_obj = type("R", (), {})()
        w.run_obj.result = None
        w.run_obj.missing_path = lambda: ""
        w._finish(_Poll(runner.State.FAILED, message="没能完成：等了 90 秒，AE 一直没开始执行脚本"))
        need(w.running is False, "失败后 running 应为 False")
        need(w.btn_run.cget("text") == const.RUN_TEXT, "主按钮应恢复原文案")
        need(str(w.btn_run.cget("state")) == "normal", "主按钮应可再点")
        need("没能完成" in w.txt.get("1.0", "end"), "失败原因没进日志")
        need(w.var_result.get().startswith("没能完成"), "结果区应显示失败原因")


@case("G. 已有别人的 AE：主按钮变「请先关闭 AE」，且命令指向重新探测")
def _():
    with Win() as w:
        w.inbox.push(("start_failed", "BlockedByForeignAE", "PID 4321"))
        w._drain()
        need(w.btn_run.cget("text") == const.NEED_CLOSE_TEXT,
             "主按钮文案应变成「关掉了，重试」，实际 %r" % w.btn_run.cget("text"))
        need("先关掉再点开始" in w.var_result.get(),
             "结果区应提示先关 AE：%r" % w.var_result.get())


# --------------------------------------------------------- H. R12 重启条

@case("H. 指纹变了出横条；修复中置灰；× 关掉不再来；重启只起新进程")
def _():
    with Win() as w:
        need(w.upd_bar is None, "一开始不该有横条")
        real_stamp_changed = U.updater.stamp_changed
        seen = {"n": 0}

        def fake_changed(st, path=None):
            seen["n"] += 1
            return ("C:\\x\\拾链.exe", 2234583, 1763200000) if seen["n"] == 1 else None

        try:
            U.updater.stamp_changed = fake_changed
            w.poll_update()
        finally:
            U.updater.stamp_changed = real_stamp_changed
        need(w.upd_bar is not None, "指纹变了应该出横条")
        btns = [b for b in w.upd_bar.winfo_children() if isinstance(b, tk.Button)]
        need(len(btns) == 1 and btns[0].cget("text") == "立即重启", "横条上应有「立即重启」")
        # 修复中置灰
        w.running = True
        w._sync_update_bar()
        need(btns[0].cget("text") == "跑完再重启", "修复中应改成「跑完再重启」")
        need(str(btns[0].cget("state")) == "disabled", "修复中应禁用重启")
        w.running = False
        w._sync_update_bar()
        need(str(btns[0].cget("state")) == "normal", "空闲时应恢复可点")
        # × 关掉：本次不再提示
        w._dismiss_update_bar()
        need(w.upd_bar is None, "× 关掉后横条应该没了")
        need(w.upd_dismissed == w.upd_seen, "应记住「关掉了这一版」")
        # 重启：只起新进程，不动任何 AE
        started = []
        real_restart = U.updater.restart
        try:
            U.updater.restart = lambda p=None: started.append(p) or True
            w.restart_now()
        finally:
            U.updater.restart = real_restart
        need(len(started) == 1, "应该起了 1 个新进程，实际 %d" % len(started))


# ------------------------------------------------------------ I. 诊断包

@case("I. 诊断包：真打出一个 zip，里面有人能先看的摘要")
def _():
    with Sandbox() as sb:
        # 造一个假日志
        with open(os.path.join(sb.logs, "relink-x-20261115-120000.log"), "w",
                  encoding="utf-8") as f:
            f.write("OPEN    x.aep\nRELINKED a.mp4 -> D:\\m\\a.mp4\n")
        p = diag.write_zip()
        need(p and os.path.isfile(p), "诊断包没生成出来：%r" % p)
        need(p.startswith(sb.app_home), "诊断包应落在软件自己的目录里：%r" % p)
        with zipfile.ZipFile(p) as z:
            names = z.namelist()
            need("diagnose.json" in names and "diagnose.txt" in names, "缺 diagnose.json/txt：%r" % names)
            need(any(n.startswith("logs/") for n in names), "最近日志没进包：%r" % names)
            txt = z.read("diagnose.txt").decode("utf-8")
            need("诊断包" in txt, "摘要里没有标题")
            # t-21 修的那处：崩溃事件只有 raw_head/message，摘要曾印成「? id=? 」
            need("id=?" not in txt, "摘要里崩溃事件还是占位符：%s" % txt[-200:])
        need(os.path.getsize(p) > 200, "包太小，八成是空的")


@case("I2. 崩溃事件摘要：原文里能抠出时间 / 事件 ID / 来源")
def _():
    ev = {"raw_head": "Event[18]",
          "message": "Log Name: Application\nEvent ID: 1001\n"
                     "Date: 2026-11-14T19:49:01.4680000Z\nSource: Windows Error Reporting\n"}
    line = diag._event_line(ev)
    need("2026-11-14 19:49:01" in line, "没抠出时间：%r" % line)
    need("id=1001" in line, "没抠出事件 ID：%r" % line)
    need("Windows Error Reporting" in line, "没抠出来源：%r" % line)
    # 结构化字段优先
    line2 = diag._event_line({"time": "tl", "id": "8", "source": "src"})
    need(line2.startswith("tl") and "id=8" in line2 and "src" in line2, "结构化字段没优先：%r" % line2)
    # 什么都没有也不能抛
    need(isinstance(diag._event_line({}), str), "空事件应返回字符串")


@case("J. D7 事件时间：先按本地解，落在未来太多才回退 UTC")
def _():
    import datetime
    now = datetime.datetime(2026, 11, 15, 15, 0, 0)
    got = diag.parse_event_time("2026-11-15T12:19:52.000Z", now=now)
    need(got == datetime.datetime(2026, 11, 15, 12, 19, 52),
         "应原样当本地时间，实际 %r" % (got,))
    fut = diag.parse_event_time("2026-11-15T20:19:52.000Z", now=now)   # 未来 5 小时 → 当 UTC
    need(fut.hour != 20, "落在未来太多的应该回退成 UTC（本地会变成别的钟点），实际 %r" % (fut,))


# ------------------------------------------------------- K. 无横向滚动

@case("K. 900×820 下不出现横向滚动（窗口需求宽度 ≤ 900，三块卡片等宽）")
def _():
    with Win() as w:
        w.root.deiconify()
        w.root.geometry("%dx%d" % T.WIN_SIZE)
        for _i in range(3):
            w.root.update()
        need(w.root.winfo_reqwidth() <= T.WIN_SIZE[0] + 8,
             "内容比窗口还宽：reqwidth=%d > %d" % (w.root.winfo_reqwidth(), T.WIN_SIZE[0]))
        need(w.root.winfo_width() <= T.WIN_SIZE[0] + 8,
             "窗口实际宽度超了：%d" % w.root.winfo_width())
        # 三块卡片都是 fill="x"，实际宽度必须一致（对齐成网）；reqwidth 是内容宽度，不能拿来比
        ws = [w.card_proj.winfo_width(), w.card_dirs.winfo_width(), w.card_opts.winfo_width()]
        need(max(ws) - min(ws) <= 2, "三块卡片实际宽度不齐：%r" % (ws,))


# --------------------------------------- L/M. 像素级白块 + 跑完态状态行

def _grab(root):
    """把窗口画下来取像素（和 tools/ui_shot.py 同一套 GDI 手法，见那里的注释）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "aemd_ui_shot", os.path.join(ROOT, "tools", "ui_shot.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    root.update_idletasks()
    root.update()
    w, h = root.winfo_width(), root.winfo_height()
    return mod.grab(int(root.winfo_id()), w, h)


def _col_brightest(pix, w, h, x0, x1, y0, y1):
    """返回指定矩形内最亮像素的亮度（0-255）与坐标。"""
    _w, _h, rgb = pix
    best, where = -1, None
    for y in range(max(0, y0), min(h, y1)):
        base = y * _w * 3
        for x in range(max(0, x0), min(w, x1)):
            i = base + x * 3
            b, g, r = rgb[i], rgb[i + 1], rgb[i + 2]
            lum = (r * 299 + g * 587 + b * 114) // 1000
            if lum > best:
                best, where = lum, (x, y)
    return best, where


@case("L. 目录列表右侧 20px 列里没有亮像素（白块探针：ttk 滚动条残留就是在这被抓的）")
def _():
    with Win() as w:
        for d in (r"D:\\ae\\a", r"D:\\ae\\b", r"D:\\ae\\c"):
            w.lb_dirs.insert("end", d)
        w.root.deiconify()
        w.root.geometry("%dx%d" % T.WIN_SIZE)
        w.root.attributes("-topmost", True)
        pix = _grab(w.root)
        _w, _h, _rgb = pix
        # 目录列表右侧：从列表右边界到 +22px（滚动条整条都在这个带里）
        x0 = w.lb_dirs.winfo_rootx() - w.root.winfo_rootx() + w.lb_dirs.winfo_width()
        y0 = w.lb_dirs.winfo_rooty() - w.root.winfo_rooty()
        y1 = y0 + w.lb_dirs.winfo_height()
        lum, where = _col_brightest(pix, T.WIN_SIZE[0], T.WIN_SIZE[1],
                                    x0, x0 + 22, y0, y1)
        need(lum < 120, "目录列表右侧有亮块：亮度 %d @ %r（阈值 120）" % (lum, where))
        # 日志区右侧同样的带（一代在这里也白过）
        x0b = w.txt.winfo_rootx() - w.root.winfo_rootx() + w.txt.winfo_width()
        yb0 = w.txt.winfo_rooty() - w.root.winfo_rooty()
        lumb, whereb = _col_brightest(pix, T.WIN_SIZE[0], T.WIN_SIZE[1],
                                      x0b, x0b + 22, yb0, yb0 + w.txt.winfo_height())
        need(lumb < 120, "日志区右侧有亮块：亮度 %d @ %r（阈值 120）" % (lumb, whereb))


@case("M. 结果行与状态行合成一行，且跑完不再挂「就绪」")
def _():
    with Win() as w:
        # 初始：还没有结果，也不挂「就绪」
        need(w.var_line.get() == const.EMPTY_HINT,
             "初始那一行应是空状态提示：%r" % (w.var_line.get(),))
        need("就绪" not in w.var_line.get(), "初始不该挂「就绪」")
        w.var_save.set(True)
        _finish(w, {"ok": 0, "fixed": 21, "missing": 11, "ambiguous": 1})
        line = w.var_line.get()
        st = w.var_status.get()
        need("就绪" not in line, "跑完还挂着「就绪」")
        need(st and st in line, "状态行应并存进同一行：line=%r status=%r" % (line, st))
        need("接上 21 条" in line, "结果句丢了：%r" % (line,))
        need("保存" in st or ".ae_doctor" in st, "跑完的状态应指向下一步：%r" % (st,))


@case("N. 选项区两列真的对齐：同一行左右中心线偏差 <= 2 px，行间距差 <= 1 px")
def _():
    import tkinter.font as tkfont

    with Win() as w:
        w.root.deiconify()
        w.root.update()
        g = w.opt_grid
        cols = {}
        for c in g.grid_slaves(row=0):
            cols[int(c.grid_info()["column"])] = c
        need(0 in cols and 1 in cols, "两列没排成 0/1")

        def rows_of(fr):
            """按行取该列里的控件（Toggle 或含 Combobox 的 Frame），返回 [(行号, 控件)]。"""
            out = []
            for ch in fr.winfo_children():
                info = ch.grid_info() or ch.pack_info() or {}
                if "row" in info:
                    out.append((int(info["row"]), ch))
            return sorted(out)

        def center_of(widget):
            """控件的垂直中心（相对根窗口）；文本控件按字体 ascent 折算文字中心。"""
            y = widget.winfo_rooty()
            h = widget.winfo_height()
            if isinstance(widget, tk.Label) or hasattr(widget, "cget"):
                try:
                    f = tkfont.Font(font=widget.cget("font"))
                    base = y + (h - f.metrics("linespace")) // 2 + f.metrics("ascent")
                    return base - f.metrics("ascent") / 2.0
                except tk.TclError:
                    pass
            return y + h / 2.0

        def inner_center(widget):
            if isinstance(widget, tk.Frame) and widget.winfo_children():
                return center_of(widget.winfo_children()[0])
            return center_of(widget)

        left_rows = rows_of(cols[0])
        right_rows = rows_of(cols[1])
        need(len(left_rows) >= 2 and len(right_rows) >= 2,
             "两列都该有多行：左 %d 右 %d" % (len(left_rows), len(right_rows)))
        by_row_l = {}
        for r, ch in left_rows:
            by_row_l.setdefault(r, ch)
        by_row_r = {}
        for r, ch in right_rows:
            by_row_r.setdefault(r, ch)

        worst = 0.0
        for r in sorted(set(by_row_l) & set(by_row_r)):
            a = inner_center(by_row_l[r])
            b = inner_center(by_row_r[r])
            diff = abs(a - b)
            worst = max(worst, diff)
            need(diff <= 2.0,
                 "第 %d 行左右中心线差 %.1f px（应 <= 2）" % (r, diff))

        def pitch(m):
            keys = sorted(m)
            return [m[keys[i + 1]].winfo_rooty() - m[keys[i]].winfo_rooty()
                    for i in range(len(keys) - 1)]

        pl, pr = pitch(by_row_l), pitch(by_row_r)
        need(pl and pr, "两侧行数不够算间距")
        n = min(len(pl), len(pr))
        for i in range(n):
            d = abs(pl[i] - pr[i])
            need(d <= 1, "第 %d 与第 %d 行的行间距两侧差 %d px（应 <= 1）：左 %r 右 %r"
                 % (i + 1, i + 2, d, pl, pr))
        w.root.withdraw()


@case("P. 批量：多选排进队列、提示「批量 1/N」、点停止整批清空")
def _():
    d = tempfile.mkdtemp(prefix="aemd_ui_batch_")
    try:
        ps = []
        for n in ("甲", "乙", "丙"):
            p = os.path.join(d, n + ".aep")
            io.open(p, "wb").write(b"x")
            ps.append(p)
        with Win() as w:
            w.adopt_projects(ps)
            need(w.batch_total == 3, "batch_total 应为 3，实得 %r" % (w.batch_total,))
            need(w.queue == ps[1:], "队列应当剩两个：%r" % (w.queue,))
            need(w.var_proj.get() == ps[0], "第一个应当成为当前工程：%r" % (w.var_proj.get(),))
            need(w.lb_batch.cget("text") == "批量 1/3",
                 "提示不对：%r" % (w.lb_batch.cget("text"),))

            # 再单选一个：批量的残留必须清掉（否则跑完会自己接着跑上次那批）
            w.adopt_projects([ps[2]])
            need(w.batch_total == 1 and w.queue == [], "单选应当清掉批量残留")
            need(w.lb_batch.cget("text") == "", "单选时不该显示批量提示")

            # 停止 = 整批停
            w.adopt_projects(ps)
            w.stop()
            need(w.queue == [] and w.batch_total == 0,
                 "点停止必须清空队列（不然跑完又自己起下一个）")
            need(w.lb_batch.cget("text") == "", "停止后批量提示应清掉")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    print("P3 界面探针（真 Tk 窗口，不启动 AE、不写 %APPDATA%）")
    print("")
    print("%d PASS · %d FAIL" % (len(PASS), len(FAIL)))
    print("")
    if FAIL:
        for n, w in FAIL:
            print("  FAIL  %s —— %s" % (n, w))
        print("")
        print("FAIL")
        return 1
    print("ALL PASS（%d 项全部通过）" % len(PASS))
    return 0


if __name__ == "__main__":
    sys.exit(main())