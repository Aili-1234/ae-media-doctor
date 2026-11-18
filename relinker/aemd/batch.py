# -*- coding: utf-8 -*-
"""批量任务队列（第二档 ⑦ 方案 A 的**纯逻辑**部分，不碰 Tk、不碰 AE）。

为什么单独成模块：队列、持久化、子进程调用这三件事都可以在没有界面的情况下测，
把它们和 Tk 混在一起就只能靠人眼看了。界面（`aemd/ui/batch.py`）只负责显示。

一个任务 = 一个 .aep 的绝对路径。跑法**复用 CLI**（`复位.exe --cli <aep>`）：
每项独立进程，互不污染；界面按行解析它的 stdout 得出进度。
"""
import io
import os
import subprocess
import time

#: 状态机。SKIPPED = 找不到文件/不是 .aep，连跑都不跑。
PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
SKIPPED = "skipped"

STATUS_TEXT = {
    PENDING: "等排到",
    RUNNING: "正在跑…",
    DONE: "接上了",
    FAILED: "失败",
    SKIPPED: "跳过",
}

EXIT_TEXT = {
    0: "成功",
    1: "跑完了但有错",
    2: "参数/工程有问题",
    3: "已有 AE 在跑（没动它）",
    4: "AE 没起来",
    5: "脚本跑完但没落盘",
}


def normalize(path):
    """统一成绝对路径；空值返回空串。"""
    p = (path or "").strip().strip('"')
    if not p:
        return ""
    return os.path.abspath(p)


def is_aep(path):
    return normalize(path).lower().endswith(".aep")


def add_paths(items, paths):
    """把 paths 追加进 items（items 是 str 列表）：去重、保持顺序、非 .aep 忽略。

    返回 (新的 items, 实际加进去几条)。
    """
    out = list(items or [])
    seen = set(x.lower() for x in out)
    added = 0
    for raw in paths or []:
        p = normalize(raw)
        if not p or not is_aep(p):
            continue
        if p.lower() in seen:
            continue
        out.append(p)
        seen.add(p.lower())
        added += 1
    return out, added


def move_item(items, index, delta):
    """上移/下移一项；越界就原样返回。"""
    out = list(items or [])
    j = index + delta
    if index < 0 or index >= len(out) or j < 0 or j >= len(out):
        return out
    out[index], out[j] = out[j], out[index]
    return out


def remove_index(items, index):
    out = list(items or [])
    if 0 <= index < len(out):
        del out[index]
    return out


class Item(object):
    """队列里的一项 + 它的运行状态。"""

    __slots__ = ("path", "status", "fixed", "missing", "exit_code", "note",
                 "started_at", "ended_at")

    def __init__(self, path):
        self.path = normalize(path)
        self.status = PENDING
        self.fixed = 0
        self.missing = 0
        self.exit_code = None
        self.note = ""
        self.started_at = 0.0
        self.ended_at = 0.0

    # ---- 展示 ----
    @property
    def name(self):
        return os.path.basename(self.path) or self.path

    @property
    def folder(self):
        return os.path.dirname(self.path)

    def status_text(self):
        base = STATUS_TEXT.get(self.status, self.status)
        if self.status == DONE:
            return "%s · 接上 %d 条%s" % (
                base, self.fixed, "，还缺 %d" % self.missing if self.missing else "")
        if self.status == FAILED:
            tail = self.note or EXIT_TEXT.get(self.exit_code, "")
            return ("%s · %s" % (base, tail)) if tail else base
        if self.status == SKIPPED:
            return "%s · %s" % (base, self.note or "文件不在了")
        return base

    def seconds(self):
        if not self.started_at:
            return 0.0
        end = self.ended_at or time.time()
        return max(0.0, end - self.started_at)

    def exists(self):
        return os.path.isfile(self.path)

    def mark_skipped(self, why):
        self.status = SKIPPED
        self.note = why
        return self


def parse_line(line):
    """从 CLI 的一行输出里抠出结构化信息；抠不到返回 {}。

    只认这几种（其余是逐条 RELINKED/MISSING，界面不需要）：
      `SUMMARY total=32 ok=0 fixed=21 missing=11 ...`
      `DONE-OK`
      `FAIL ...` / `FATAL ...`
    """
    s = (line or "").strip()
    out = {}
    if not s:
        return out
    if s.startswith("SUMMARY"):
        for token in s.split():
            if "=" in token:
                k, v = token.split("=", 1)
                try:
                    out[k.strip()] = int(v)
                except ValueError:
                    pass
    elif s.startswith("DONE-OK"):
        out["done_ok"] = True
    elif s.startswith("FAIL") or s.startswith("FATAL"):
        out["error"] = s[:200]
    return out


def run_one(exe, item, on_line=None, timeout=1800, popen=None):
    """跑一项（阻塞）。返回 item 本身，状态已更新。

    on_line(line) 用来把输出实时喂给界面；popen 只给测试替换用。
    不是 .aep / 文件不在 → 直接 SKIPPED，不启动任何进程。
    """
    if not is_aep(item.path):
        return item.mark_skipped("不是 .aep")
    if not item.exists():
        return item.mark_skipped("文件不在了")

    item.status = RUNNING
    item.started_at = time.time()
    item.note = ""
    spawn = popen or subprocess.Popen
    code = None
    err = ""
    try:
        proc = spawn([exe, "--cli", item.path],
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if on_line:
                    on_line(line)
                info = parse_line(line)
                if "fixed" in info:
                    item.fixed = info.get("fixed", item.fixed)
                    item.missing = info.get("missing", item.missing)
                if info.get("error"):
                    err = info["error"]
            code = proc.wait(timeout=timeout)
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass
    except Exception as e:            # 起不来 / 超时 / 被停
        err = str(e)
        code = None
    item.ended_at = time.time()
    item.exit_code = code
    if code == 0:
        item.status = DONE
    else:
        item.status = FAILED
        item.note = err or EXIT_TEXT.get(code, "退出码 %s" % (code,))
    return item


def load_queue(cfg_get, key="batch_projects"):
    """从 settings 里读上次的队列（坏数据退空、非 .aep 过滤）。"""
    raw = cfg_get(key) or []
    if not isinstance(raw, (list, tuple)):
        return []
    items, _ = add_paths([], [x for x in raw if isinstance(x, str)])
    return items


def save_queue(items, merge_cfg, key="batch_projects"):
    """把队列写回 settings（走 merge_cfg：先读后写，未知键不丢）。"""
    clean, _ = add_paths([], [x for x in (items or []) if isinstance(x, str)])
    merge_cfg({key: clean})
    return clean


def summary_line(items):
    """一行总览，给界面顶部用。"""
    n = len(items)
    if not n:
        return "队列是空的，点「添加工程…」把要修的 .aep 加进来"
    done = sum(1 for i in items if i.status == DONE)
    failed = sum(1 for i in items if i.status == FAILED)
    skipped = sum(1 for i in items if i.status == SKIPPED)
    fixed = sum(i.fixed for i in items if i.status == DONE)
    parts = ["共 %d 个" % n]
    if done:
        parts.append("成功 %d（接上 %d 条）" % (done, fixed))
    if failed:
        parts.append("失败 %d" % failed)
    if skipped:
        parts.append("跳过 %d" % skipped)
    left = n - done - failed - skipped
    if left:
        parts.append("还剩 %d" % left)
    return " · ".join(parts)


def write_report(items, path):
    """把整批结果写成一份文本报告（放 logs 目录，用户可整份发出来）。"""
    lines = ["复位 批量修复报告", "生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"), ""]
    for i, it in enumerate(items, 1):
        lines.append("%d. %s" % (i, it.path))
        lines.append("   状态：%s   用时 %.1f 秒   退出码 %s"
                     % (it.status_text(), it.seconds(), it.exit_code))
        if it.note:
            lines.append("   说明：%s" % it.note)
    lines.append("")
    lines.append(summary_line(items))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return path
