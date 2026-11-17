# -*- coding: utf-8 -*-
"""批量修复窗口（第二档 ⑦ 方案 A）。

界面只做三件事：列队、逐项跑、显示每项结果。
真正的跑法在 `aemd/batch.py`（复用 CLI，每项一个独立进程）。
"""
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

from .. import batch, cfg, const, paths
from . import theme as T
from . import widgets as W

POLL_MS = 120


def self_argv():
    """怎么用「我自己」去跑一项：打包后是 exe，源码态是 python main.py。"""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "main.py")
    return [sys.executable, main_py]


class BatchWindow(tk.Toplevel):
    def __init__(self, master):
        tk.Toplevel.__init__(self, master.root)
        self.app = master
        self.title("批量修复")
        self.configure(bg=T.BG)
        self.geometry("820x560")
        self.minsize(680, 460)
        self.transient(master.root)

        self.queue = queue.Queue()
        # 队列来源：优先用主窗当前那份配置（`master.cfg`）——它才是这一轮真正读到的值
        # （测试里可以注进 cfg_data，用户那边就是磁盘上那份）。主窗没有就自己读一遍。
        _all = getattr(master, "cfg", None)
        if _all is None:
            _all = cfg.load_cfg() or {}
        self.items = [batch.Item(p) for p in batch.load_queue(lambda k: _all.get(k))]
        self.running = False
        self.stop_flag = False

        self._build()
        self._refresh()

    # ------------------------------------------------------------ 版面
    def _build(self):
        head = tk.Frame(self, bg=T.BG)
        head.pack(fill="x", padx=16, pady=(14, 8))
        tk.Label(head, text="批量修复", bg=T.BG, fg="#ffffff",
                 font=T.FONT_TITLE).pack(side="left")
        self.lb_sum = tk.Label(head, text="", bg=T.BG, fg=T.DIM, font=T.FONT_SMALL)
        self.lb_sum.pack(side="left", padx=(12, 0))
        W.flat_button(head, "关闭", self._close).pack(side="right")

        btns = tk.Frame(self, bg=T.BG)
        btns.pack(fill="x", padx=16, pady=(0, 8))
        self.btn_add = W.flat_button(btns, "添加工程…", self.add_projects)
        self.btn_add.pack(side="left")
        self.btn_del = W.flat_button(btns, "移除所选", self.remove_selected)
        self.btn_del.pack(side="left", padx=(8, 0))
        W.flat_button(btns, "上移", lambda: self.move(-1)).pack(side="left", padx=(8, 0))
        W.flat_button(btns, "下移", lambda: self.move(1)).pack(side="left")
        self.btn_stop = W.flat_button(btns, "停止", self.stop)
        self.btn_stop.pack(side="right")
        self.btn_run = W.flat_button(btns, "开始全部", self.run_all, primary=True)
        self.btn_run.pack(side="right", padx=(0, 8))
        W.Tip(self.btn_run, "按列表顺序一个一个跑，每项都是独立的拾链进程。\n"
                            "跑的时候不会碰你自己的 AE —— 每项都按单实例规则来。")

        body = W.card(self)
        body.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        self.lb = tk.Listbox(body, bg=T.LOG_BG, fg=T.LOG_FG, font=T.FONT,
                            borderwidth=0, highlightthickness=0,
                            selectbackground=T.BTN, selectforeground="#ffffff",
                            activestyle="none", height=12)
        self.lb.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
        W.SlimScroll(body, self.lb, track=T.LOG_BG, thumb=T.BTN, bg=T.CARD).pack(
            side="left", fill="y", pady=10, padx=(0, 6))

        foot = tk.Frame(self, bg=T.BG)
        foot.pack(fill="x", padx=16, pady=(0, 12))
        self.txt = tk.Text(foot, bg=T.LOG_BG, fg=T.DIM, font=T.FONT_SMALL,
                           borderwidth=0, highlightthickness=0, height=6, wrap="none",
                           padx=8, pady=6)
        self.txt.pack(side="left", fill="both", expand=True)
        self.txt.configure(state="disabled")

    # ------------------------------------------------------------ 列表
    def _refresh(self):
        self.lb.delete(0, "end")
        for i, it in enumerate(self.items, 1):
            self.lb.insert("end", "%2d. %-34s  %s" % (i, it.name[:34], it.status_text()))
        self.lb_sum.configure(text=batch.summary_line(self.items))
        busy = self.running
        for b in (self.btn_add, self.btn_del, self.btn_run):
            b.configure(state="disabled" if busy else "normal")
        self.btn_stop.configure(state="normal" if busy else "disabled")

    def _selected(self):
        sel = self.lb.curselection()
        return sel[0] if sel else -1

    def _save(self):
        try:
            batch.save_queue([it.path for it in self.items], cfg.merge_cfg)
        except Exception:
            pass

    def add_projects(self):
        files = filedialog.askopenfilenames(
            parent=self, title="选要修的 .aep（可多选）",
            filetypes=[("After Effects 工程", "*.aep"), ("所有文件", "*.*")])
        if not files:
            return
        paths_in = list(files)
        cur = [it.path for it in self.items]
        new_list, added = batch.add_paths(cur, paths_in)
        if added:
            keep = {it.path for it in self.items}
            for p in new_list:
                if p not in keep:
                    self.items.append(batch.Item(p))
        self._save()
        self._refresh()

    def remove_selected(self):
        i = self._selected()
        if i < 0:
            return
        del self.items[i]
        self._save()
        self._refresh()

    def move(self, delta):
        i = self._selected()
        if i < 0:
            return
        cur = [it.path for it in self.items]
        new = batch.move_item(cur, i, delta)
        if new == cur:
            return
        by_path = {it.path: it for it in self.items}
        self.items = [by_path[p] for p in new]
        self._save()
        self._refresh()
        self.lb.selection_set(i + delta)

    # ------------------------------------------------------------ 跑
    def _log(self, line):
        self.txt.configure(state="normal")
        self.txt.insert("end", line[-400:] + "\n")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def run_all(self):
        if self.running or not self.items:
            return
        todo = [it for it in self.items if it.status in (batch.PENDING, batch.FAILED)]
        if not todo:
            messagebox.showinfo("批量修复", "队列里没有待跑的项（都跑过了）。", parent=self)
            return
        self.running = True
        self.stop_flag = False
        for it in todo:
            it.status = batch.PENDING
            it.note = ""
        self._refresh()
        t = threading.Thread(target=self._worker, args=(todo,), daemon=True)
        t.start()
        self.after(POLL_MS, self._drain)

    def _worker(self, todo):
        exe = self_argv()
        for idx, it in enumerate(todo, 1):
            if self.stop_flag:
                self.queue.put(("stopped", idx, len(todo)))
                break
            self.queue.put(("start", idx, len(todo), it))

            def on_line(line, _it=it):
                self.queue.put(("line", _it, line))

            batch_item = it
            try:
                # run_one 需要 [exe, ...] 的第一段是可执行文件，其余前置参数拼在后面
                runner = _Runner(exe, batch_item, on_line)
                runner.run()
            except Exception as e:
                it.status = batch.FAILED
                it.note = str(e)
            self.queue.put(("done", it))
        self.queue.put(("finish",))

    def _drain(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                kind = msg[0]
                if kind == "start":
                    _, idx, total, it = msg
                    self._log("— 第 %d/%d 个：%s" % (idx, total, it.path))
                elif kind == "line":
                    _, it, line = msg
                    if line.strip():
                        self._log("  " + line.strip())
                elif kind == "done":
                    self.lb.delete(0, "end")
                    for i, x in enumerate(self.items, 1):
                        self.lb.insert("end", "%2d. %-34s  %s"
                                       % (i, x.name[:34], x.status_text()))
                    self.lb_sum.configure(text=batch.summary_line(self.items))
                elif kind == "stopped":
                    self._log("已停止（剩下的没跑）")
                elif kind == "finish":
                    self.running = False
                    self._refresh()
                    self._write_report()
                    return
        except queue.Empty:
            pass
        if self.running:
            self.after(POLL_MS, self._drain)

    def _write_report(self):
        try:
            name = "batch-%s.txt" % time.strftime("%Y%m%d-%H%M%S")
            path = os.path.join(paths.LOG_DIR, name)
            batch.write_report(self.items, path)
            self._log("整批报告：" + path)
        except Exception as e:
            self._log("写整批报告失败：%s" % e)

    def stop(self):
        if self.running:
            self.stop_flag = True
            self._log("收到停止请求：当前这项跑完就停")

    def _close(self):
        if self.running:
            if not messagebox.askyesno("批量修复", "还在跑，真的要关掉吗？", parent=self):
                return
            self.stop_flag = True
        self.destroy()


class _Runner(object):
    """包一层：把 [python, main.py] 或 [exe] 前缀和 --cli 拼起来。"""

    def __init__(self, exe_argv, item, on_line):
        self.exe_argv = list(exe_argv)
        self.item = item
        self.on_line = on_line

    def run(self):
        import subprocess
        cmd = self.exe_argv + ["--cli", self.item.path]
        self.item.status = batch.RUNNING
        self.item.started_at = time.time()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                self.on_line(line)
                info = batch.parse_line(line)
                if "fixed" in info:
                    self.item.fixed = info.get("fixed", self.item.fixed)
                    self.item.missing = info.get("missing", self.item.missing)
            code = proc.wait()
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass
        self.item.ended_at = time.time()
        self.item.exit_code = code
        if code == 0:
            self.item.status = batch.DONE
        else:
            self.item.status = batch.FAILED
            self.item.note = batch.EXIT_TEXT.get(code, "退出码 %s" % (code,))
        return self.item
