# -*- coding: utf-8 -*-
"""tests/test_cfg_migrate.py —— 配置加载/迁移的回归（2026-11-15 P0）。

真机事故：`%APPDATA%\\AE素材医生\\settings.json` 被洗成默认值。
两个缺陷叠加：
  ① 用户用 PowerShell 改过那份文件（PS 5.1 默认写 UTF-8 **带 BOM**），
     我们用 `encoding="utf-8"` 读 → `json.load` 抛 `Unexpected UTF-8 BOM`
     → `load_cfg` 吞成 `{}`；
  ② 界面 `_build()` 末尾无条件 `_persist()` —— 于是「读不到」被写成了「空值」，
     用户的工程路径与三个搜索目录当场消失。

本文件把这两条钉死，并补上「启动不写盘」「未知键不丢」「原子写盘」的断言。
跑法：`python -m tests.test_cfg_migrate`（退出码 0 = 全过）。
"""

import io
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aemd import console  # noqa: E402

console.harden()

import aemd.cfg as cfg  # noqa: E402

FAIL = []
OK = []

LEGACY = {
    "version": "1.9.6",
    "project": "D:/ae/别人工程/again (1)/again/again.aep",
    "dirs": ["D:/素材/A", "D:/素材/B", "D:/素材/C"],
    "close_ae": False,
    "ae_conflict": "auto",          # v1.9.x 的键，v2 已经不读它 —— 但不许丢
    "未来新键": 1,                    # 未来的版本可能加别的键 —— 也不许丢
}


def check(name):
    def deco(fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            FAIL.append((name, "%s: %s" % (type(e).__name__, e)))
        else:
            OK.append(name)
        return fn
    return deco


def need(cond, msg):
    if not cond:
        raise AssertionError(msg)


class Sandbox(object):
    """把 %APPDATA% 指到临时目录，测完还原。"""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="aemd_cfg_")
        self.old = os.environ.get("APPDATA")

    def __enter__(self):
        os.environ["APPDATA"] = self.dir
        return self

    def __exit__(self, *a):
        if self.old is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self.old
        shutil.rmtree(self.dir, ignore_errors=True)
        return False

    def path(self):
        return os.path.join(self.dir, "AE素材医生", "settings.json")

    def write(self, data, bom=False):
        os.makedirs(os.path.dirname(self.path()), exist_ok=True)
        enc = "utf-8-sig" if bom else "utf-8"
        with io.open(self.path(), "w", encoding=enc, newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return self.path()

    def read_bytes(self):
        with open(self.path(), "rb") as f:
            return f.read()


# --------------------------------------------------------------------- 用例

@check("A 旧版配置（含未知键）能原样读进来")
def t_a():
    with Sandbox() as sb:
        sb.write(LEGACY)
        got = cfg.load_cfg()
        for k, v in LEGACY.items():
            need(got.get(k) == v, "键 %r 丢了或变了：%r != %r" % (k, got.get(k), v))


@check("B 带 BOM 的 settings.json 必须能读（本次 P0 的根因）")
def t_b():
    with Sandbox() as sb:
        sb.write(LEGACY, bom=True)
        raw = sb.read_bytes()
        need(raw[:3] == b"\xef\xbb\xbf", "夹具没写成 BOM：%r" % raw[:3])
        got = cfg.load_cfg()
        need(got.get("project") == LEGACY["project"], "带 BOM 时 project 读不出来：%r" % got)
        need(got.get("dirs") == LEGACY["dirs"], "带 BOM 时 dirs 读不出来：%r" % got.get("dirs"))


@check("C merge 只改一个键 —— 其它键（含未知键）逐键保留")
def t_c():
    with Sandbox() as sb:
        sb.write(LEGACY)
        got = cfg.merge_cfg({"project": "E:/new.aep"})
        need(got["project"] == "E:/new.aep", "本次键没写进去")
        for k in ("dirs", "close_ae", "ae_conflict", "未来新键"):
            need(got.get(k) == LEGACY[k], "键 %r 被 merge 抹掉了" % k)
        reread = cfg.load_cfg()
        need(reread.get("dirs") == LEGACY["dirs"], "落盘后 dirs 丢了")
        need(reread.get("未来新键") == 1, "落盘后未来新键丢了")


@check("D 落盘：无 BOM、无 int 残留临时文件")
def t_d():
    with Sandbox() as sb:
        sb.write(LEGACY)
        cfg.merge_cfg({"project": "E:/x.aep"})
        raw = sb.read_bytes()
        need(raw[:3] != b"\xef\xbb\xbf", "落盘写出了 BOM")
        need(b'"_relink' not in raw, "落盘内容异常")
        leftovers = [p for p in os.listdir(os.path.dirname(sb.path())) if p.endswith(".tmp")]
        need(not leftovers, "临时文件没清掉：%r" % leftovers)


@check("E 启动不写盘：构造主界面后 settings.json 一个字节都不变")
def t_e():
    import tkinter as tk
    import aemd.ui.window as win

    with Sandbox() as sb:
        sb.write(LEGACY, bom=True)
        before = sb.read_bytes()
        before_mtime = os.path.getmtime(sb.path())

        calls = []
        real = cfg._dump_raw
        cfg._dump_raw = lambda d: (calls.append(d), real(d))[1]
        try:
            root = tk.Tk()
            root.withdraw()
            try:
                win.MainWindow(root=root, installs=[])
            finally:
                try:
                    root.destroy()
                except Exception:
                    pass
        finally:
            cfg._dump_raw = real

        need(calls == [], "启动阶段写了 %d 次配置" % len(calls))
        need(sb.read_bytes() == before, "启动后 settings.json 字节变了")
        need(os.path.getmtime(sb.path()) == before_mtime, "启动后 settings.json 的 mtime 变了")


@check("F 用户真改了才写一次，且旧键仍在不丢")
def t_f():
    import tkinter as tk
    import aemd.ui.window as win
    import aemd.const as const

    with Sandbox() as sb:
        sb.write(LEGACY)
        calls = []
        real = cfg.merge_cfg
        cfg.merge_cfg = lambda d: (calls.append(dict(d)), real(d))[1]
        try:
            root = tk.Tk()
            root.withdraw()
            try:
                w = win.MainWindow(root=root, installs=[])
                # 界面初始化完（值都没变）→ 不该写
                need(calls == [], "构造完就写了配置：%d 次" % len(calls))
                w.auto_close.set(True)      # 用户勾了复选框
            finally:
                try:
                    root.destroy()
                except Exception:
                    pass
        finally:
            cfg.merge_cfg = real

        need(len(calls) == 1, "改一个开关应该只写一次，实际 %d 次" % len(calls))
        after = cfg.load_cfg()
        need(after.get("close_ae") is True, "改的键没落盘")
        need(after.get("dirs") == LEGACY["dirs"], "改开关把 dirs 弄丢了")
        need(after.get("未来新键") == 1, "改开关把未知键弄丢了")
        need(after.get("version") == const.APP_VER, "version 没更新")


@check("G 文件真的坏掉（不是 BOM）时，仍然回退成 {}，不抛")
def t_g():
    with Sandbox() as sb:
        os.makedirs(os.path.dirname(sb.path()), exist_ok=True)
        with io.open(sb.path(), "w", encoding="utf-8") as f:
            f.write("{ this is not json ")
        need(cfg.load_cfg() == {}, "坏文件应该回退成 {}，实际 %r" % cfg.load_cfg())


@check("H consolidate_mode 归一化：机器值 / 三种中文旧值 / 布尔兜底 / 未知值都落到 off|copy|move")
def t_h():
    cases = [
        ("off", None, "off"),
        ("copy", None, "copy"),
        ("move", None, "move"),
        ("COPY ", None, "copy"),                       # 大小写与空格
        ("不收进", None, "off"),                        # 历史上落过盘的三种中文值
        ("复制到 Footages", None, "copy"),
        ("移进 Footages", None, "move"),
        ("移动到 Footages", None, "move"),              # 文案改过一两个字也要认
        ("复制一份进工程", None, "copy"),
        ("不收编 Footages（旧）", None, "off"),
        ("", True, "copy"),                            # 只有布尔 consolidate 的更早配置
        ("", False, "off"),
        (None, True, "copy"),
        (None, None, "off"),
        (True, None, "copy"),                          # 值本身就是布尔
        (False, None, "off"),
        ("莫名其妙", None, "off"),                      # 完全认不出来 → 绝不当成 copy 去动素材
    ]
    for raw, cons, want in cases:
        got = cfg.consolidate_mode_value(raw, cons)
        need(got == want, "归一化错：raw=%r consolidate=%r 期望 %r 实际 %r" % (raw, cons, want, got))
    need(set(cfg.consolidate_mode_value(r) for r in ("不收进", "复制到 Footages", "移进 Footages"))
         <= set(cfg.CONS_MODES), "归一化结果跑出了 off/copy/move 之外的值")


@check("I 迁移往返：旧中文值读进来显示中文，用户一动手就变机器值，其它键不丢")
def t_i():
    import tkinter as tk
    import aemd.ui.window as win

    with Sandbox() as sb:
        old = dict(LEGACY)
        old["consolidate"] = True
        old["consolidate_mode"] = "移进 Footages"        # 2026-11-15 之前落盘的形态
        sb.write(old)

        root = tk.Tk()
        root.withdraw()
        try:
            w = win.MainWindow(root=root, installs=[])
            need(w.var_consmode.get() == "移进 Footages",
                 "界面上没读到旧中文值：%r" % (w.var_consmode.get(),))   # 显示仍是中文
            w.var_dry.set(True)                          # 用户动了另一个开关 → 触发落盘
        finally:
            try:
                root.destroy()
            except Exception:
                pass

        after = cfg.load_cfg()
        need(after.get("consolidate_mode") == "move",
             "落盘的不是机器值：%r" % (after.get("consolidate_mode"),))
        need(after.get("consolidate") is True, "consolidate 布尔没保住")
        need(after.get("dirs") == LEGACY["dirs"], "迁移把 dirs 弄丢了")
        need(after.get("ae_conflict") == "auto", "迁移把旧键 ae_conflict 弄丢了")
        need(after.get("未来新键") == 1, "迁移把未知键弄丢了")


@check("J 起窗 → 内部赋值 → 关窗：settings.json 字节与 mtime 全程不变")
def t_j():
    import tkinter as tk
    import aemd.ui.window as win

    with Sandbox() as sb:
        sb.write(LEGACY)
        before = sb.read_bytes()
        before_mtime = os.path.getmtime(sb.path())

        calls = []
        real = cfg.merge_cfg
        cfg.merge_cfg = lambda d: (calls.append(dict(d)), real(d))[1]
        try:
            root = tk.Tk()
            root.withdraw()
            try:
                w = win.MainWindow(root=root, installs=[])
                need(sb.read_bytes() == before, "起窗就把配置写了")
                w._set_project_quiet(r"D:/ae/别的工程/x.aep")       # 内部赋值（截图/自动化）
                w._set_project_quiet(r"D:/ae/别的工程/y.aep")
                need(calls == [], "内部赋值写盘了 %d 次" % len(calls))
                need(sb.read_bytes() == before, "内部赋值之后配置字节变了")
            finally:
                try:
                    root.destroy()
                except Exception:
                    pass
        finally:
            cfg.merge_cfg = real

        need(sb.read_bytes() == before, "关窗后配置字节变了")
        need(os.path.getmtime(sb.path()) == before_mtime, "关窗后 mtime 变了")


@check("K 用户编辑恰好写一次（选工程 + 补目录也只写一次）")
def t_k():
    import tkinter as tk
    import aemd.ui.window as win

    with Sandbox() as sb:
        base = dict(LEGACY)
        base["dirs"] = []                                # 空目录 → set_project 会顺手猜目录
        sb.write(base)

        calls = []
        real = cfg.merge_cfg
        cfg.merge_cfg = lambda d: (calls.append(dict(d)), real(d))[1]
        try:
            root = tk.Tk()
            root.withdraw()
            try:
                w = win.MainWindow(root=root, installs=[])
                need(calls == [], "构造完就写了配置")
                w.set_project(r"D:/ae/别人工程/again (1)/again/again.aep")
            finally:
                try:
                    root.destroy()
                except Exception:
                    pass
        finally:
            cfg.merge_cfg = real

        need(len(calls) == 1, "用户选一次工程应该只写一次，实际 %d 次" % len(calls))
        after = cfg.load_cfg()
        need(after.get("project", "").endswith("again.aep"), "工程路径没落盘：%r" % (after.get("project"),))


def main():
    print("拾链 1.0.0 —— 配置加载回归（tests/test_cfg_migrate.py）")
    print("-" * 66)
    for name in OK:
        print("  ok    %s" % name)
    for name, err in FAIL:
        print("  FAIL  %s" % name)
        print("        %s" % err)
    print("-" * 66)
    if FAIL:
        print("失败 %d 项 / 通过 %d 项" % (len(FAIL), len(OK)))
        return 1
    print("ALL PASS（%d 项全部通过）" % len(OK))
    return 0


if __name__ == "__main__":
    sys.exit(main())