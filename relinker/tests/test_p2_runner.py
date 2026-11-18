# -*- coding: utf-8 -*-
"""P2 打桩测试：runner 的 start -> poll -> finish 全链路（不启动真 AE、不弹窗）。

跑法（在 media-doctor-v2 目录下）：

    python -m tests.test_p2_runner

为什么全打桩：AE 正被别的实验占着，真启动会打断别人的对照窗口。这里验证的是**流程与
判据**，不是 AE 本身：

  A. 正常一条线：起 AE -> 等脚本 -> 读报告 -> 结算（stats 与那句话都能复现真机读数）
  B. 别人的 AE 在跑 -> start() 抛 BlockedByForeignAE，状态 BLOCKED，一个进程都没起
  C. 脚本一直没开始 -> 到点失败，并且**先结算已接上几条**（F-17 的结算半边）
  D. 报告说日志没写成（logWritable: false）-> 结算带一条 WARN（F-14）
  E. 「跑完关掉这次打开的 AE」只在用户勾了的时候才发生，且只关自己那只
  F. 复制工程：默认复制到 `_fixed.aep`，原工程一字节不动
  G. run_cli 的退出码契约：2 参数错 / 3 AE 被占 / 4 复制失败 / 5 生成脚本失败 / 0 成功
  H. 工程根还有老版本中间件 -> 结算结果里点名（不自动删）
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from aemd import console

console.harden()

from aemd import aehost, const, jsx, paths, runner, scanner, winproc

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


def need(cond, why):
    if not cond:
        raise AssertionError(why)


REPORT_OK = {
    "script": "AE素材医生 1.9.8",
    "project": r"D:\ae\again_fixed.aep",
    "logWritable": True,
    "dryRun": False,
    "stats": {"total": 32, "ok": 0, "fixed": 21, "missing": 11,
              "ambiguous": 1, "zeroByte": 0},
    "relinked": [{"name": "a.png"}],
    "missing": [{"name": "b.png", "original": "X:/b.png"}],
}

LOG_OK = (
    "OPEN     D:/ae/again_fixed.aep\n"
    "SCRIPT   开始\n"
    "RELINKED Again & Again.mp3\n"
    "RELINKED b.png\n"
    "MISSING  c.png\n"
    "SUMMARY  total=3\n"
)


class FakeProc(object):
    def __init__(self, pid):
        self.pid = pid
        self._exit = None

    def poll(self):
        return self._exit


class Ctx(object):
    """把落盘位置挪到临时目录，把「起进程 / 等标记」换成桩。"""

    def __init__(self, foreign=None):
        self.dir = tempfile.mkdtemp(prefix="aemd_run_")
        self.marker = os.path.join(self.dir, "progdata")
        self.logdir = os.path.join(self.dir, "logs")
        self.workdir = os.path.join(self.dir, "work")
        self.projdir = os.path.join(self.dir, "proj")
        # 假的 Adobe 偏好根：prepare_startup 挑 Startup 目录时只能挑到这里
        self.prefs = os.path.join(self.dir, "AdobePrefs")
        self.startup = os.path.join(self.prefs, "25.3", "Scripts", "Startup")
        for d in (self.marker, self.logdir, self.workdir, self.projdir, self.startup):
            paths.ensure_dir(d)
        self.project = os.path.join(self.projdir, "again.aep")
        with open(self.project, "w", encoding="utf-8") as fh:
            fh.write("fake aep")
        self.material = os.path.join(self.projdir, "Footages")
        paths.ensure_dir(self.material)

        self.clock = {"t": 5000.0}
        self.logs = []
        self.launched = []
        self.closed = []
        self.foreign = list(foreign or [])
        self._saved = {}
        self.proc_seq = 7000
        self.shelled = []          # 1.1.9 热启动：记下我们"双击"过哪些工程（绝不真开文件）

    def patch(self, obj, name, value):
        self._saved[(id(obj), name)] = (obj, name, getattr(obj, name))
        setattr(obj, name, value)

    def __enter__(self):
        self.patch(const, "JSX_TARGET_DIR", self.marker)
        self.patch(const, "LOG_DIR", self.logdir)
        self.patch(const, "WORK_DIR", self.workdir)
        # ADOBE_PREFS 必须一起挪走：不挪的话 prepare_startup() 会挑到**真实**的用户
        # Startup 目录（%APPDATA%\Adobe\After Effects\25.3\Scripts\Startup）并往里写
        # 启动器 —— 2026-11-15 真出过这个事故：测试一边报 ALL PASS，一边往用户 Startup
        # 里塞 _aemd_*.jsx（打桩测试有真副作用，假绿的另一种形态）。
        self.patch(const, "ADOBE_PREFS", self.prefs)
        self.patch(paths, "is_ascii_path", lambda p: True)
        self.patch(runner, "_now", lambda: self.clock["t"])
        self.patch(runner, "_sleep", lambda s: None)
        self.patch(scanner, "build_index", lambda roots, depth, out: 0)

        def fake_launch(args, cwd=None):
            proc = FakeProc(self.proc_seq + len(self.launched) + 1)
            self.launched.append((list(args), cwd, proc))
            return proc

        def no_popen(*_a, **_k):
            raise AssertionError("测试里出现了真 subprocess.Popen：打桩漏了一个入口")

        self.patch(runner, "_shell_open", lambda p: self.shelled.append(p))
        self.patch(aehost, "_launch", fake_launch)
        self.patch(aehost, "_sleep", lambda s: None)
        self.patch(aehost, "procs", lambda: list(self.foreign))
        self.patch(aehost, "wait_script_alive", lambda *a, **k: True)
        self.patch(aehost, "_node_check", lambda text: True)
        self.patch(winproc, "terminate_tree", lambda pid, wait=0: True)
        self.patch(subprocess, "Popen", no_popen)
        # 1.1.12：冷启动判定要看「真 AE 进程在不在」，打桩环境里那个假 pid 只要
        # 恰好撞上一个活的系统进程，`_cold_start()` 就会真、等待上限被拉长到
        # COLD_START_WAIT_S，C 项就会偶发「应失败，实际 starting」（实测撞过一次）。
        # 打桩测试里没有真 AE，直接把它钉成 False。
        self.patch(runner.Run, "_cold_start", lambda self: False)
        self.patch(aehost, "close_own_ae",
                   lambda pid, reason, log=None, wait=8, force=False, save=True: self.closed.append((pid, reason)) or True)
        return self

    def __exit__(self, *exc):
        for (obj, name, old) in self._saved.values():
            setattr(obj, name, old)
        self._saved.clear()
        shutil.rmtree(self.dir, ignore_errors=True)
        return False

    def log(self, msg, tag=None):
        self.logs.append(str(msg))

    def tick(self, sec):
        self.clock["t"] += float(sec)

    def write_log(self, text=LOG_OK):
        p = os.path.join(self.logdir, "_pending")
        del p
        return text

    # 让脚本「开始跑」：在 run.log_path 上写日志
    def script_started(self, run, text=LOG_OK):
        paths.ensure_dir(os.path.dirname(run.log_path))
        with open(run.log_path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def script_done(self, run, report=None):
        paths.ensure_dir(run.state_dir)
        with open(os.path.join(run.state_dir, const.REPORT_JSON), "w", encoding="utf-8") as fh:
            json.dump(report or REPORT_OK, fh, ensure_ascii=False)
        if (report or REPORT_OK).get("stats", {}).get("missing"):
            with open(os.path.join(run.state_dir, const.MISSING_TXT), "w", encoding="utf-8") as fh:
                fh.write("# time\n# hint\nb.png\tX:/b.png\nc.png\tX:/c.png\n")


def make_run(ctx, **kw):
    opt = kw.pop("options", None) or runner.Options(copy_project=False)
    return runner.Run(ctx.project, [ctx.material], opt, log=ctx.log, **kw)


# ----------------------------------------------------------------- A. 正常一条线

@case("A. 正常一条线：STARTING -> RUNNING -> DONE，stats 与那句话复现真机读数")
def _():
    with Ctx() as ctx:
        run = make_run(ctx, force_mode=aehost.LaunchMode.A2_STARTUP)
        run.start()
        need(run.state is runner.State.STARTING, "start() 后应是 STARTING，实际 %s" % run.state.value)
        need(len(ctx.launched) == 1, "没起 AE")

        p = run.poll()
        need(p.state is runner.State.STARTING, "还没日志时应是 STARTING，实际 %s" % p.state.value)
        need("启动 AE 中" in p.status_text, "状态文案不对：%r" % p.status_text)

        ctx.script_started(run)
        p = run.poll()
        need(p.state is runner.State.RUNNING, "有日志后应是 RUNNING，实际 %s" % p.state.value)
        need(any("RELINKED" in ln for ln in p.lines), "日志新行没发出去：%r" % (p.lines,))

        ctx.script_done(run)
        p = run.poll()
        need(p.state is runner.State.DONE, "报告出现后应是 DONE，实际 %s" % p.state.value)
        need(p.report and p.report["stats"]["fixed"] == 21, "报告没解析对")
        res = run.result
        need(res is not None, "没生成 Result")
        need(res.stats["fixed"] == 21 and res.stats["missing"] == 11, "stats 不对：%r" % (res.stats,))
        need(res.summary == "接上 21 条，还缺 11 条，另有 1 条是按同名文件猜的，可能要核对",
             "那句话不对：%r" % (res.summary,))
        need(len(res.missing) == 2, "缺件没解析：%r" % (res.missing,))
        need(res.log_writable is True, "logWritable 不对")
        need(not os.path.exists(os.path.join(ctx.workdir, const.WORK_INDEX_JSON)),
             "跑完 work 目录没清（索引还在）")


# ----------------------------------------------------------------- B. 别人的 AE

@case("B. 已有别人的 AE -> start() 抛 BlockedByForeignAE，一个进程都没起")
def _():
    with Ctx(foreign=[(8888, True)]) as ctx:
        run = make_run(ctx)
        raised = None
        try:
            run.start()
        except aehost.BlockedByForeignAE as e:
            raised = e
        need(raised is not None, "没抛 BlockedByForeignAE")
        need(raised.pids == [8888], "pids 不对：%r" % (raised.pids,))
        need(run.state is runner.State.BLOCKED, "状态应是 BLOCKED，实际 %s" % run.state.value)
        need(ctx.launched == [], "居然起了进程")
        need(ctx.closed == [], "居然关了进程")
        need(any("SKIP" in m for m in ctx.logs), "没有 SKIP 日志：%r" % (ctx.logs,))
        # BLOCKED 是终态：再 poll 不该乱跑
        need(run.poll().state is runner.State.BLOCKED, "BLOCKED 后 poll 状态变了")


# ----------------------------------------------------------------- C. 超时结算

@case("C. 脚本一直没开始 -> 到点失败，并先结算已接上几条")
def _():
    with Ctx() as ctx:
        run = make_run(ctx, force_mode=aehost.LaunchMode.B_RAISE)
        run.start()
        ctx.tick(const.NO_PROC_WAIT_S + 1)
        p = run.poll()
        need(p.state is runner.State.FAILED, "应失败，实际 %s" % p.state.value)
        need("没能完成" in p.message, "失败话术不对：%r" % p.message)
        # 这一轮日志文件还没生成（脚本从未执行），所以结算行是「一条素材都没接上」；
        # 有日志时才是「先结算一下：本次已接上 N 条…」。两者都算做了结算。
        need(("一条素材都没接上" in p.message) or ("先结算一下" in p.message),
             "没做结算：%r" % p.message)
        need("过程日志" in p.message, "没给日志路径：%r" % p.message)
        need("三条排查" in p.message, "没给排查建议：%r" % p.message)


# ----------------------------------------------------------------- D. logWritable

@case("D. 报告说日志没写成（logWritable: false）-> 结算带 WARN（F-14）")
def _():
    with Ctx() as ctx:
        run = make_run(ctx, force_mode=aehost.LaunchMode.A2_STARTUP)
        run.start()
        bad_rep = json.loads(json.dumps(REPORT_OK))
        bad_rep["logWritable"] = False
        ctx.script_started(run)
        ctx.script_done(run, bad_rep)
        p = run.poll()
        need(p.state is runner.State.DONE, "应 DONE，实际 %s" % p.state.value)
        need(run.result.log_writable is False, "log_writable 没读到")
        need(any("过程日志没写成" in w for w in run.result.warnings),
             "没有 WARN：%r" % (run.result.warnings,))
        need(any("过程日志没写成" in m for m in ctx.logs), "WARN 没落日志：%r" % (ctx.logs,))


# ----------------------------------------------------------------- E. 关 AE

@case("E. 只有勾了才关，且只关自己那只")
def _():
    with Ctx() as ctx:
        run = make_run(ctx, force_mode=aehost.LaunchMode.A2_STARTUP)   # 默认不关
        run.start()
        ctx.script_started(run)
        ctx.script_done(run)
        run.poll()
        need(ctx.closed == [], "没勾也关 AE 了：%r" % (ctx.closed,))

    with Ctx() as ctx:
        run = make_run(ctx, options=runner.Options(copy_project=False, close_own_ae=True),
                       force_mode=aehost.LaunchMode.A2_STARTUP)
        run.start()
        mine = run.launch.pid
        ctx.script_started(run)
        ctx.script_done(run)
        run.poll()
        need(ctx.closed == [(mine, "跑完关闭")], "关的不是自己那只：%r" % (ctx.closed,))


# ----------------------------------------------------------------- F. 备份工程（方案甲）

@case("F. 默认（1.1.13）：先复制源工程再改，原件一字节不动")
def _():
    with Ctx() as ctx:
        before = open(ctx.project, "rb").read()
        opt = runner.Options(copy_project=True)
        run = runner.Run(ctx.project, [ctx.material], opt, log=ctx.log,
                         force_mode=aehost.LaunchMode.A2_STARTUP)
        run.start()
        need(run.copied_path, "没复制工作副本")
        need(os.path.basename(run.copied_path) == "again_fixed.aep",
             "副本名不对：%r" % os.path.basename(run.copied_path))
        # 夹具在沙箱里是 `again.aep`，副本因此是 `again_fixed.aep`（同目录、不同文件）。
        need(os.path.dirname(run.copied_path) == ctx.projdir, "副本位置不对：%r" % run.copied_path)
        need(os.path.isfile(run.copied_path), "副本文件不存在")
        need(run.work == run.copied_path, "work 应指向副本，实际 %r" % run.work)
        need(not run.backup_path, "走副本这条路不该再备份")
        need(open(ctx.project, "rb").read() == before, "原工程被动了（应当一字节不动）")


@case("F2. 不勾「先复制」时才直接改原工程，并留 _backup.aep 还原点")
def _():
    with Ctx() as ctx:
        before = os.path.getsize(ctx.project)
        opt = runner.Options(copy_project=False)
        run = runner.Run(ctx.project, [ctx.material], opt, log=ctx.log,
                         force_mode=aehost.LaunchMode.A2_STARTUP)
        run.start()
        need(not run.copied_path, "没勾复制却造了副本")
        need(run.work == ctx.project, "work 应指向原工程，实际 %r" % run.work)
        need(run.backup_path and os.path.basename(run.backup_path) == "again_backup.aep",
             "还原点不对：%r" % run.backup_path)
        need(os.path.getsize(ctx.project) == before, "prepare 阶段就动了原工程")


# ----------------------------------------------------------------- G. CLI 退出码

@case("G. run_cli 退出码：2 参数错 / 3 AE 被占 / 4 复制失败 / 5 脚本失败 / 0 成功")
def _():
    def cli(argv, **kw):
        lines = []
        code = runner.run_cli(argv, log=lambda m, tag=None: lines.append(str(m)), **kw)
        return code, "\n".join(lines)

    # 2：缺工程 / 不认识的参数
    code, _t = cli([])
    need(code == 2, "空参数应返回 2，实际 %d" % code)
    code, text = cli(["--nope"])
    need(code == 2, "未知参数应返回 2，实际 %d" % code)
    need("用法" in text, "没打用法")

    # 3：AE 被占（force 一只别人的 AE）
    with Ctx(foreign=[(1234, True)]) as ctx:
        code, text = cli([ctx.project])
        need(code == 3, "AE 被占应返回 3，实际 %d" % code)
        need("--yes" in text, "没给出 --yes 出口")

    # 4：备份失败
    with Ctx() as ctx:
        ctx.patch(paths, "make_work_copy", lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("复制写不进去")))
        run_opt = runner.Options(copy_project=True)
        r = runner.Run(ctx.project, [ctx.material], run_opt, log=ctx.log)
        try:
            r.start()
            raise AssertionError("复制失败却没抛")
        except runner.CopyProjectError:
            pass

    # 5：生成脚本失败
    with Ctx() as ctx:
        ctx.patch(jsx, "build", lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("JSX 占位符没填完：__DEPTH__")))
        r = runner.Run(ctx.project, [ctx.material], runner.Options(copy_project=False), log=ctx.log)
        try:
            r.start()
            raise AssertionError("脚本失败却没抛")
        except runner.ScriptGenError:
            pass

    # 0：成功（先把报告写好，再跑 CLI；时间用假钟，别真等）
    with Ctx() as ctx:
        ctx.patch(aehost, "wait_script_alive", lambda *a, **k: True)

        real_start = runner.Run.start

        def start_then_fake(self):
            real_start(self)
            ctx.script_started(self)
            ctx.script_done(self)
            self.started_at = runner._now()

        ctx.patch(runner.Run, "start", start_then_fake)
        code, text = cli([ctx.project, "--manual"], poll_interval=0)
        need(code == 0, "成功应返回 0，实际 %d；输出：%s" % (code, text[-500:]))
        need("接上 21 条" in text, "成功输出里没有那句话：%s" % text[-300:])


# ----------------------------------------------------------------- H. 老文件提示

@case("H. 工程根还有老版本中间件 -> 结果里点名（不自动删）")
def _():
    with Ctx() as ctx:
        legacy = os.path.join(ctx.projdir, "_relink_log.txt")
        with open(legacy, "w", encoding="utf-8") as fh:
            fh.write("RELINKED x\n")
        run = make_run(ctx, force_mode=aehost.LaunchMode.A2_STARTUP)
        run.start()
        ctx.script_started(run)
        ctx.script_done(run)
        run.poll()
        need("_relink_log.txt" in run.result.legacy_files, "没识别出老文件：%r" % (run.result.legacy_files,))
        need(os.path.exists(legacy), "居然把老文件删了")
        need(any("旧版文件" in m for m in ctx.logs), "没有提示行：%r" % (ctx.logs,))


# ----------------------------------------------------------------- I. 两路输出顺序

@case("I. 控制台两路输出：JSX 的 SUMMARY/SAVED/DONE-OK 一定早于 Run 的 DONE/NOTE")
def _():
    """t-21 的回归：控制台是两路交织（JSX 逐行回显 + Run 自己打的 DONE/NOTE）。

    `Run.finish()` 跑在 `run.poll()` 里，那一刻 JSX 尾巴还没被读出来，于是 DONE 会插在
    SUMMARY 前面。修法见 `run_cli` 里的 pending 缓冲。这里盯的就是那句话：
    SUMMARY 的下标必须小于 DONE / NOTE 的下标。
    """
    tail_log = (
        "OPEN     D:/ae/again_fixed.aep\n"
        "RELINKED Again & Again.mp3\n"
        "MISSING  c.png\n"
        "SUMMARY  total=3\n"
        "SAVED    D:/ae/again_fixed.aep\n"
        "DONE-OK  total=3\n"
    )
    with Ctx() as ctx:
        ctx.patch(aehost, "wait_script_alive", lambda *a, **k: True)
        real_start = runner.Run.start

        def start_then_fake(self):
            real_start(self)
            ctx.script_started(self, text=tail_log)     # 日志带 SUMMARY / SAVED / DONE-OK
            ctx.script_done(self)
            self.started_at = runner._now()

        ctx.patch(runner.Run, "start", start_then_fake)
        lines = []
        code = runner.run_cli([ctx.project, "--manual"],
                              log=lambda m, tag=None: lines.append(str(m)),
                              poll_interval=0)
        text = "\n".join(lines)
        need(code == 0, "应返回 0，实际 %d；输出尾部：%s" % (code, text[-400:]))
        i_sum = text.find("SUMMARY")
        need(i_sum >= 0, "控制台里没看到 JSX 的 SUMMARY 回显：%s" % text[-400:])
        for key in ("DONE ", "NOTE "):
            i = text.find(key)
            if i >= 0:
                need(i > i_sum, "%s 出现在 SUMMARY 前面了（第 %d 字符 vs %d）：%s"
                     % (key.strip(), i, i_sum, text[-500:]))
        need("DONE-OK" in text, "控制台里没看到 DONE-OK：%s" % text[-300:])


# ----------------------------------------------------------------- main

@case("J. AE 卡死（UI 线程无响应）-> 25 秒起就报警，最迟 75 秒判失败，不干等 240 秒")
def _():
    """2026-11-16 真机：拾链拉起 AE 打开工程时死锁，界面上只挂着一个「打开项目」进度框。

    旧逻辑会按 COLD_START_WAIT_S=240 或 START_WAIT_S=90 一路等，用户体感是「怎么这么慢」。
    新逻辑：主窗口出现但 WM_NULL 超时（is_hung），就把上限压到 HUNG_WAIT_S 并写一条 DIAG。
    """
    ctx = Ctx()
    with ctx:
        run = make_run(ctx)
        run.start()
        # 主窗口在、但整个 UI 线程无响应
        old_mw, old_hung = winproc.main_window, winproc.is_hung
        winproc.main_window = lambda pid: {"hwnd": 0xBEEF, "cls": "AE_CApplication_25.3",
                                           "title": "Adobe After Effects", "visible": True,
                                           "enabled": False}
        winproc.is_hung = lambda hwnd, timeout_ms=1200: True
        try:
            run.state = runner.State.RUNNING
            run.log_path = os.path.join(ctx.logdir, "_never_created.log")
            ctx.tick(const.HUNG_CHECK_AT_S + 2)
            p = run.poll()
        finally:
            winproc.main_window, winproc.is_hung = old_mw, old_hung
        need(p.state is runner.State.STARTING, "卡死还没到 HUNG_WAIT_S，不该提前失败")
        need(p.limit_s <= const.HUNG_WAIT_S,
             "卡死后上限应压到 HUNG_WAIT_S=%d，实得 %r" % (const.HUNG_WAIT_S, p.limit_s))
        need(any("无响应" in str(m) for m in ctx.logs),
             "必须有一条说清「无响应/卡死」的 DIAG，实得：%r" % ctx.logs[-3:])

        # 到点判失败，且失败原因要带「更像卡死」
        ctx.tick(const.HUNG_WAIT_S + 5)
        winproc.main_window = lambda pid: {"hwnd": 0xBEEF}
        winproc.is_hung = lambda hwnd, timeout_ms=1200: True
        try:
            p2 = run.poll()
        finally:
            winproc.main_window, winproc.is_hung = old_mw, old_hung
        need(p2.state is runner.State.FAILED, "超过 HUNG_WAIT_S 应当判失败，实得 %r" % (p2.state,))
        need("卡死" in (p2.message or ""), "失败原因要写明更像卡死：%r" % (p2.message,))


@case("K. 进度 X/N：从日志行数出「已处理 / 总数」（大工程不再只有一个转圈）")
def _():
    """用户 2026-11-17 提的：大工程没有进度，只有日志滚动。

    JSX 本来就一行一素材地写日志，所以不必改脚本接口 —— 只要在 runner 侧数出来。
    这条测的是纯函数，不碰 AE、不碰文件。
    """
    lines = [
        "OPEN      D:////ae////again.aep",
        "ITEMS     32",
        "OK        a.mp4  |  D:/ae/a.mp4",
        "RELINKED  b.mp4  |  D:/missing/b.mp4  =>  D:/ae/b.mp4",
        "MISSING   c.mp4  |  E:/x/c.mp4  |  本地无同名文件",
        "TIME      逐条重连  +12.3s",
    ]
    done, total = runner.Run._scan_progress(lines)
    need((done, total) == (3, 32), "应当数出 3/32，实得 %r" % ((done, total),))
    need(runner.Run._scan_progress([]) == (0, 0), "空日志应当是 0/0")
    need(runner.Run._scan_progress(["ITEMS     0"]) == (0, 0), "空工程不该崩")
    # 边界：ITEMS 后面不是数字（脚本被改坏）时不能抛
    done2, total2 = runner.Run._scan_progress(["ITEMS     abc", "OK  x"])
    need((done2, total2) == (1, 0), "坏 ITEMS 行应当忽略，实得 %r" % ((done2, total2),))


@case("L. 结果区的提醒：从日志里只挑「接上了但要核一眼」的行，最多 5 条")
def _():
    """1.1.7：SEQ-* / LAYER-WARN / RELINK-SUSPECT / ZERO 这几类，用户不该靠翻日志才发现。

    它们都发生在「已经写成 RELINKED」之后 —— 光看结论行是干净的，问题在细节里。
    """
    d = tempfile.mkdtemp(prefix="aemd_warn_")
    try:
        run = runner.Run(project=__file__, roots=[ROOT])
        run.log_path = os.path.join(d, "x.log")
        open(run.log_path, "w", encoding="utf-8").write(
            "OPEN      D:/a.aep\n"
            "OK        a.mp4  |  D:/a.mp4\n"
            "RELINKED  b.mp4  |  E:/b.mp4  =>  D:/b.mp4  [全名匹配]\n"
            "SEQ-OFFSET c_0001.png  |  D:/c_0001.png  |  帧号 0005 -> 0001\n"
            "LAYER-WARN d.psd  |  D:/d.psd  |  请核对图层\n"
            "ZERO      e.mov  |  D:/e.mov  (文件 0 字节)\n"
            "RELINK-SUSPECT f.mp4  |  D:/f.mp4  |  没真接上\n"
            "SEQ-WARN  g.png  |  D:/g.png  |  原来是序列\n"
            "ZERO      j.mov  |  D:/j.mov  (文件 0 字节)\n"
            "SEQ-OK    h_0001.png  |  D:/h_0001.png  |  5 帧\n"
            "RELINKED  i.mp4  |  E:/i.mp4  =>  D:/i.mp4  [基名匹配(2 个候选)]\n"
            "SUMMARY   total=9 ok=1 fixed=4 missing=0 ambiguous=0 zeroByte=1 mapped=0\n")
        got = run._collect_warns()
        need(len(got) == 6, "应当是 5 条提醒 + 1 条折行说明，实得 %d 条：%r" % (len(got), got))
        need(got[-1].startswith("…另有"), "超限要折成一句「另有 N 条」，实得 %r" % (got[-1],))
        blob = "\n".join(got)
        for must in ("SEQ-OFFSET", "LAYER-WARN", "ZERO", "RELINK-SUSPECT"):
            need(must in blob, "少了 %s：%r" % (must, got))
        need("SEQ-OK" not in blob, "SEQ-OK 是正常行，不该占提醒位")
        need("OK        a.mp4" not in blob, "普通 OK 行不该进结果区")

        run.log_path = os.path.join(d, "nope.log")
        need(run._collect_warns() == [], "日志不存在必须返回空表，不能抛")
    finally:
        shutil.rmtree(d, ignore_errors=True)


@case("M. AE 中途退出：连续两次查不到 AfterFX 就判失败，不白等 10 分钟")
def _():
    """2026-11-17 真机：AE 退出后 CLI 又傻等 5 分钟，最后是人手动停掉的。

    脚本跑起来之后，旧逻辑只看 SCRIPT_WAIT_S=600 秒。AE 都没了，脚本不可能再写完。
    判据用「有没有任何 AfterFX」而不是 pid：AE 崩溃修复后会自己重启，pid 会变。
    """
    ctx = Ctx()
    with ctx:
        run = make_run(ctx)
        run.start()
        run.state = runner.State.RUNNING
        run.log_path = os.path.join(ctx.logdir, "_never_created.log")
        old = aehost.procs
        aehost.procs = lambda: []
        try:
            run._alive_at = 0.0
            ctx.tick(10)
            # 直接打运行期那段：poll() 会先按 marker 判"脚本开没开始"，这里要测的
            # 就是「已经开始跑了、然后 AE 没了」
            p1 = run._poll_running([], True)
            need(p1.state is runner.State.RUNNING,
                 "只查一次不算数（可能正好在重启），实得 %r" % (p1.state,))
            ctx.tick(16)
            p2 = run._poll_running([], True)
        finally:
            aehost.procs = old
        need(p2.state is runner.State.FAILED, "连续两次没有 AE 应当判失败，实得 %r" % (p2.state,))
        need("AE 进程已经不在了" in (p2.message or ""), "失败原因要说人话：%r" % (p2.message,))

        # 反向：AE 一直在（哪怕 pid 变了）就不许误判
        ctx2 = Ctx()
        with ctx2:
            run2 = make_run(ctx2)
            run2.start()
            run2.state = runner.State.RUNNING
            run2.log_path = os.path.join(ctx2.logdir, "_never_created.log")
            aehost.procs = lambda: [(999, "whatever")]
            try:
                run2._alive_at = 0.0
                ctx2.tick(10)
                run2._poll_running([], True)
                ctx2.tick(16)
                p3 = run2._poll_running([], True)
            finally:
                aehost.procs = old
            need(p3.state is runner.State.RUNNING, "AE 在跑却判失败（会误杀）：%r" % (p3.state,))


@case("N. 插件窗口卡死：点名 + 20 秒收场，不再干等 600 秒")
def _():
    """2026-11-17 真机（基线，不经过本工具）：打开 again.aep 到 24 秒时
    Video Copilot 的 Optical Flares 弹出 VCWINDOW，CPU 立刻归零，61 秒仍不动。
    这种等下去是纯浪费 —— 要立刻点名并收场，而不是按 SCRIPT_WAIT_S 干等 10 分钟。
    """
    ctx = Ctx()
    with ctx:
        run = make_run(ctx)
        run.start()
        run.state = runner.State.RUNNING
        run.log_path = os.path.join(ctx.logdir, "_never_created.log")
        old = (aehost.procs, aehost.plugin_block, aehost.capture_hung)
        aehost.procs = lambda: [(999, "whatever")]
        aehost.plugin_block = lambda pid, seen=None, log=None: {
            "hwnd": 5, "cls": "VCWINDOW", "title": "Optical Flares"}
        aehost.capture_hung = lambda *a, **k: ""
        try:
            run._alive_at = 0.0
            # elapsed 平时由 poll() 刷新；这里直接打 _poll_running，得自己给
            run.elapsed = 5.0
            p1 = run._poll_running([], True)
            need(p1.state is runner.State.RUNNING,
                 "刚看见插件窗口就判失败太急（可能只是闪一下），实得 %r" % (p1.state,))
            need("插件窗口" in (p1.status_text or ""),
                 "状态行要让人看见卡在选择上：%r" % (p1.status_text,))
            run.elapsed = 31.0
            p2 = run._poll_running([], True)
        finally:
            (aehost.procs, aehost.plugin_block, aehost.capture_hung) = old
            run._block_since = 0.0
        need(p2.state is runner.State.FAILED, "过 20 秒应当判失败，实得 %r" % (p2.state,))
        need("Optical Flares" in (p2.message or ""), "失败原因要点名是谁挡的：%r" % (p2.message,))


def main():
    total = len(PASS) + len(FAIL)
    print("")
    print("P2 打桩测试 —— runner 全链路（%d 项）" % total)
    print("")
    if FAIL:
        print("FAILED：%d 项通过，%d 项失败" % (len(PASS), len(FAIL)))
        for name, why in FAIL:
            print("  - %s：%s" % (name, why))
        return 1
    print("ALL PASS（%d 项全部通过）" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())