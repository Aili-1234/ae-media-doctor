# -*- coding: utf-8 -*-
"""tests/test_batch_ui.py —— 批量任务列表窗（第二档 ⑦ 方案 A，1.1.11）。

和 `tests/test_batch.py`（1.1.7 的多工程接力）不是一回事：那边测 `runner.expand_projects`
与 CLI 收多个工程；这里测**看得见的队列窗**——排队、改顺序、逐项状态、整批报告。

要证明的六件事：
  A. 队列：去重保序、非 .aep 挡掉、上移下移、删除
  B. 状态文案：等排到 / 接上 N 条 / 失败带原因 / 跳过带原因
  C. 解析：CLI 的 SUMMARY / DONE-OK / FAIL 三种行
  D. 持久化：save → load 往返（坏数据退空表）
  E. 界面：主窗点「批量…」建出真窗口，队列行数对、总览对，再点一次不新建
  F. 副作用：建窗与点批量**一次都不写 settings**

跑法：`python -m tests.test_batch_ui`（退出码 0 = 全过）。
安全：不启动 AE；`APPDATA` 与 `merge_cfg` 全部沙箱化。
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from aemd import console  # noqa: E402

console.harden()

from aemd import batch, const  # noqa: E402

OK, FAIL = [], []


def check(name):
    def deco(fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            FAIL.append((name, "%s: %s" % (type(e).__name__, e)))
            print("  FAIL  %s\n        %s: %s" % (name, type(e).__name__, e))
        else:
            OK.append(name)
            print("  PASS  %s" % name)
        return fn
    return deco


def need(cond, msg):
    if not cond:
        raise AssertionError(msg)


class Sandbox(object):
    """APPDATA + merge_cfg 全打桩：建窗、点按钮都不许碰真配置。"""

    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix="aemd_batchui_")
        self.saved = (const.APP_HOME, const.LOG_DIR, const.WORK_DIR,
                      os.environ.get("APPDATA"))
        home = os.path.join(self.tmp, "AE素材医生")
        const.APP_HOME = home
        const.LOG_DIR = os.path.join(home, "logs")
        const.WORK_DIR = os.path.join(home, "work")
        os.makedirs(const.LOG_DIR, exist_ok=True)
        os.makedirs(const.WORK_DIR, exist_ok=True)
        os.environ["APPDATA"] = self.tmp
        from aemd import cfg as v2cfg
        self.cfg = v2cfg
        self.real_merge = v2cfg.merge_cfg
        self.writes = 0

        def fake_merge(d=None, **k):
            self.writes += 1
            return dict(d or {})

        v2cfg.merge_cfg = fake_merge
        return self

    def __exit__(self, *a):
        from aemd import cfg as v2cfg
        v2cfg.merge_cfg = self.real_merge
        const.APP_HOME, const.LOG_DIR, const.WORK_DIR, saved_appdata = self.saved
        if saved_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = saved_appdata
        return False


# ------------------------------------------------------------------ A. 队列

@check("A 队列：去重保序、非 .aep 挡掉、上移下移、删除")
def _():
    a = r"D:\proj\第01集\a.aep"
    b = r"D:\proj\第02集\b.aep"
    got, added = batch.add_paths([], [a, b, a, r"D:\proj\x.txt", ""])
    need(added == 2, "应加 2 条，实际 %d" % added)
    need([os.path.basename(x) for x in got] == ["a.aep", "b.aep"], "去重保序错：%s" % got)
    moved = batch.move_item(got, 0, 1)
    need([os.path.basename(x) for x in moved] == ["b.aep", "a.aep"], "上移错：%s" % moved)
    need(batch.move_item(got, 0, -1) == got, "越界不该动")
    need(len(batch.remove_index(got, 0)) == 1, "删除错")
    need(batch.remove_index(got, 99) == got, "越界删除不该动")


# ------------------------------------------------------------------ B. 文案

@check("B 状态文案：等排到 / 接上 N 条 / 失败带原因 / 跳过带原因")
def _():
    it = batch.Item(r"D:\proj\a.aep")
    need(it.status_text() == "等排到", "默认文案错：%s" % it.status_text())
    it.status, it.fixed, it.missing = batch.DONE, 21, 11
    t = it.status_text()
    need("接上 21" in t and "还缺 11" in t, "成功文案错：%s" % t)
    it2 = batch.Item(r"D:\proj\b.aep")
    it2.status, it2.exit_code, it2.note = batch.FAILED, 3, "已有 AE 在跑"
    need("已有 AE 在跑" in it2.status_text(), "失败文案错：%s" % it2.status_text())
    it3 = batch.Item(r"D:\proj\c.aep").mark_skipped("文件不在了")
    need("跳过" in it3.status_text(), "跳过文案错：%s" % it3.status_text())


# ------------------------------------------------------------------ C. 解析

@check("C 解析：SUMMARY / DONE-OK / FAIL 三种 CLI 行")
def _():
    info = batch.parse_line(
        "SUMMARY   total=32 ok=0 fixed=21 missing=11 ambiguous=1 zeroByte=0 mapped=0")
    need(info.get("fixed") == 21 and info.get("missing") == 11, "SUMMARY 抠错：%s" % info)
    need(batch.parse_line("DONE-OK").get("done_ok") is True, "DONE-OK 没认出来")
    need("error" in batch.parse_line("FAIL  AE 没起来"), "FAIL 没认出来")
    need(batch.parse_line("RELINKED  某素材.mp4  =>  D:\\x.mp4") == {}, "普通行不该产出")


# ------------------------------------------------------------------ D. 持久化

@check("D 持久化：save → load 往返，坏数据退空表")
def _():
    store = {}

    def fake_merge(d=None, **k):
        store.update(d or {})
        return dict(d or {})

    got = batch.save_queue([r"D:\p\a.aep", r"D:\p\b.aep"], fake_merge)
    need(len(got) == 2, "save 返回条数错")
    back = batch.load_queue(lambda k: store.get(k))
    need(len(back) == 2, "load 回来条数错：%s" % back)
    need(batch.load_queue(lambda k: "不是列表") == [], "坏数据没退空表")


# ------------------------------------------------------------------ E/F 界面

@check("E+F 界面：点「批量…」建真窗、行数与总览对、不重复建、零写盘")
def _():
    with Sandbox() as sb:
        import tkinter as tk
        from aemd.ui import window as U

        root = tk.Tk()
        root.withdraw()
        try:
            w = U.MainWindow(root=root, cfg_data={"batch_projects":
                                                  [r"D:\p\a.aep", r"D:\p\b.aep",
                                                   r"D:\p\c.aep"]},
                             installs=[])
            root.update_idletasks()
            need(hasattr(w, "btn_batch"), "头部没有「批量…」按钮")
            writes_before = sb.writes
            win = w.open_batch()
            root.update_idletasks()
            need(win is not None, "open_batch 没返回窗口")
            need(len(win.items) == 3, "队列应有 3 项，实际 %d" % len(win.items))
            need(win.lb.size() == 3, "列表应显示 3 行，实际 %d" % win.lb.size())
            need("共 3 个" in win.lb_sum.cget("text"), "总览错：%s" % win.lb_sum.cget("text"))
            again = w.open_batch()
            need(again is win, "再点一次不该新建窗口")
            need(sb.writes == writes_before, "建窗/点批量写了 %d 次配置（必须 0）"
                 % (sb.writes - writes_before))
            win.destroy()
        finally:
            try:
                root.destroy()
            except Exception:
                pass


def main():
    print("== 批量任务列表窗（⑦ 方案 A）==")
    if FAIL:
        print("\nFAIL %d 项：" % len(FAIL))
        for n, why in FAIL:
            print("  - %s：%s" % (n, why))
        return 1
    print("\nALL PASS：%d 项全过" % len(OK))
    return 0


if __name__ == "__main__":
    sys.exit(main())
