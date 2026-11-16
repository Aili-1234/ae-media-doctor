# -*- coding: utf-8 -*-
"""aemd.runner —— 一次「体检 / 修复」的完整流程（P2）。

负责：
  · `Run`：准备 → 启动 → 轮询 → 结算这一条时间线（界面与 CLI 都走同一个 Run）
  · `run_cli(argv)`：命令行入口与**退出码契约**（0 成功 / 1 超时 / 2 参数或 AE 问题 /
    3 AE 占用且关不掉 / 4 复制工程失败 / 5 生成脚本失败）
  · 把 P1 已经实现好的能力接起来：jsx.render 生成脚本、scanner.build_index 预扫、
    reports.parse_report 读结果、aehost.ensure_ready 决定怎么启动 AE

不负责：
  · 任何界面（没有 tkinter、没有弹窗；进度通过回调 `log(msg, tag)` 与 `Poll` 出去）
  · 直接起进程 / 关进程（aehost 的事；本模块只拿 `LaunchResult`）
  · 生成 JSX 文本本身（jsx 模块）、解析报告字段（reports 模块）
  · 决定「要不要关 AE」（那是 `Options.close_own_ae`，且只作用于自己拉起的那只）

依赖：`.const`、`.paths`、`.jsx`、`.scanner`、`.reports`、`.aehost`、`.winproc`（只读进程信息）

═══════════════════════ 线程模型（一代踩过的坑）═══════════════════════
一代把 `Popen` 和轮询都放在后台线程里，还从后台线程直接调 `widget.after()`，结果报
`RuntimeError: main thread is not in main loop`。v2 的约定：

  · `Run` 的**所有**方法都在调用者线程里跑（界面就是主线程）。
  · 需要后台干活的地方（预扫描、建索引）由调用者用 `ui.worker.spawn` 起线程，
    干完用 `ui.worker.post` 回主线程改界面 —— `Run` 自己不建线程。
  · 因此 `Run.poll()` 是**纯同步**的：调一次、看一眼、立刻返回，不 sleep 不等。
"""

import enum
import json
import os
import time

from . import aehost, const, jsx, mapping, paths, reports, scanner, winproc


class State(enum.Enum):
    """一次运行的状态机。`BLOCKED` 是终态：别人的 AE 在跑，我们什么都没做。"""

    PENDING = "pending"
    STARTING = "starting"    # AE 已拉起，还没看到脚本开始跑
    RUNNING = "running"      # 日志已出现 = 脚本在跑
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"

    def finished(self):
        return self in (State.DONE, State.FAILED, State.CANCELLED, State.BLOCKED)


class Options:
    """一次运行的选项（与界面上的勾选一一对应）。默认值取 v1.9.8 的语义。"""

    def __init__(self, dry=False, save=True, consolidate=False, cons_dir="",
                 cons_mode="copy", depth=6, copy_project=True, close_own_ae=False,
                 mapping_path=""):
        self.dry = bool(dry)
        self.save = bool(save)
        self.consolidate = bool(consolidate)
        self.cons_dir = cons_dir or ""
        self.cons_mode = cons_mode or "copy"
        self.depth = int(depth)
        self.copy_project = bool(copy_project)
        # 只有用户自己勾了才会关我们自己拉起的那个 AE；缺省 False（一代反过来，是错误 #1）
        self.close_own_ae = bool(close_own_ae)
        # 1.1.0 手工映射表：空 = 用 %APPDATA%\AE素材医生\mapping.json（界面/默认都走这里）
        self.mapping_path = mapping_path or ""


class CopyProjectError(RuntimeError):
    """复制工程失败（三级退避全挂）→ CLI 退出码 4。"""


class ScriptGenError(RuntimeError):
    """生成 JSX 失败（写不进 / 自检没过）→ CLI 退出码 5。"""


class Poll:
    """一次轮询的结果快照。界面照着它刷新，不需要再问 Run 要别的。"""

    def __init__(self, state, elapsed=0.0, lines=None, report=None, message="",
                 status_text="", limit_s=0):
        self.state = state
        self.elapsed = elapsed
        self.lines = list(lines or [])       # 新增日志行（只给没给过的新行）
        self.report = report                 # 解析好的报告 dict（DONE 时才有）
        self.message = message               # 一段给人看的话（失败原因 / 结算摘要）
        self.status_text = status_text       # 状态栏那一行
        self.limit_s = limit_s               # 当前这段等待的上限（给「最多等 N 秒」用）

    @property
    def finished(self):
        return self.state.finished()


class Result:
    """结算结果（DONE 时由 `Run.finish()` 生成）。"""

    def __init__(self, report=None, stats=None, summary="", missing=None,
                 log_writable=None, log_path="", report_path="", missing_path="",
                 elapsed=0.0, legacy_files=None, warnings=None):
        self.report = report or {}
        self.stats = dict(stats or {})
        self.summary = summary
        self.missing = list(missing or [])
        self.log_writable = log_writable
        self.log_path = log_path
        self.report_path = report_path
        self.missing_path = missing_path
        self.elapsed = elapsed
        self.legacy_files = list(legacy_files or [])
        self.warnings = list(warnings or [])


# 时间与睡眠做成模块级引用（测试里换掉，不必真等 90 秒）
_now = time.time
_sleep = time.sleep


class Run(object):
    """一次运行。用法：`r = Run(project, roots, opts, log=cb)` → `r.start()` → 循环 `r.poll()`。"""

    def __init__(self, project, roots=None, options=None, log=None, ae_exe="",
                 force_mode=None, token=None):
        self.project = str(project or "")
        self.roots = [r for r in (roots or []) if r]
        self.opt = options or Options()
        self.log = log or (lambda msg, tag=None: None)
        self.ae_exe = ae_exe or ""
        self.force_mode = force_mode
        self.token = token or ("%08x" % (int(_now() * 1000) & 0xFFFFFFFF))

        # 下面这些在 start() 里填好
        self.state = State.PENDING
        self.work = ""             # 真正被脚本打开的工程（可能是 _fixed.aep 副本）
        self.copy_path = ""        # 复制出来的工程（没复制则空）
        self.state_dir = ""
        self.log_path = ""
        self.jsx_path = ""
        self.index_path = ""
        self.launch = None         # aehost.LaunchResult
        self.mode = None           # aehost.LaunchMode
        self.result = None
        self.report = None
        self.started_at = 0.0
        self.elapsed = 0.0
        self._seen_log = 0         # 已经发出去的日志行数
        self._diag_done = False
        self._hung_logged = False
        self._diag = {}
        self._prompt_seen = set()  # 已经按过回车的「崩溃修复选项」窗口
        self.cancelled = False

    # ------------------------------------------------------------ 准备

    def prepare(self):
        """准备阶段：定路径、复制工程、预扫描、生成 JSX。**不启动 AE**。

        失败语义（与退出码契约对齐）：
          · 工程不存在 / 没找到 AE / 参数错 → ValueError
          · 复制工程三级都失败 → RuntimeError（CLI 退出码 4）
          · 生成脚本失败 → 本模块抛 OSError（CLI 退出码 5）

        预扫描失败**不算失败**（v1.9.8 也一样：只记一行，脚本里还会再扫一遍）。
        """
        if not self.project or not os.path.isfile(self.project):
            raise ValueError("工程文件不存在：%s" % (self.project or "（空）"))

        exe = self.ae_exe
        if not exe:
            installs = aehost.find_installs()
            if not installs:
                raise ValueError("没找到能用的 After Effects（2020–%d）" % const.AE_MAX_YEAR)
            exe = installs[0][1]
        self.ae_exe = exe

        if not self.roots:
            self.roots = scanner.guess_dirs(self.project)
            if self.roots:
                self.log("SCAN    自动推断素材目录 %d 个" % len(self.roots), "dim")

        # ① 复制工程（可选）—— 三级退避，绝不动用户的原工程
        self.work = self.project
        if self.opt.copy_project and not self.opt.dry:
            try:
                self.copy_path = paths.make_work_copy(self.project, log=self.log)
                self.work = self.copy_path
                self.log("COPY    已复制到 %s（原工程不动）" % self.work, "dim")
            except RuntimeError as e:
                raise CopyProjectError("复制工程失败：%s" % e)

        # ② 路径三件套：工程旁的结果目录、软件自己的日志与临时目录
        out_dir = os.path.dirname(os.path.abspath(self.work))
        self.state_dir = paths.state_dir_of(out_dir)
        paths.clear_state(self.state_dir)
        stems = os.path.splitext(os.path.basename(self.project))[0]
        self.log_path = paths.log_path_for(out_dir, stems)
        paths.prune_logs()
        paths.ensure_dir(const.WORK_DIR)
        paths.clear_work()

        # ③ 预扫描（纯优化：让 AE 内部的查找先命中 Python 建好的索引）
        self.index_path = os.path.join(const.WORK_DIR, const.WORK_INDEX_JSON)
        try:
            n = scanner.build_index(self.roots, min(self.opt.depth, 6), self.index_path)
            self.log("SCAN    预扫描完成，索引 %s 个文件" % n, "dim")
        except Exception as e:            # 预扫描失败只是少一层优化，不能拦住用户
            self.log("SCAN    预扫描失败（退回 AE 内扫描）：%s" % e, "warn")

        # ④ 生成 JSX 并落到唯一合法目录（R9：非 ASCII 路径会毁掉一切）
        # 1.1.0：先把手工映射表读出来塞进脚本。表坏了 / 没有都只是空表（mapping.load 不抛），
        # 所以「读映射失败」永远不会变成「本次跑不了」。
        table = mapping.load(self.opt.mapping_path or None)
        if table:
            self.log("MAP     手工映射 %d 条（%s）"
                     % (len(table), self.opt.mapping_path or "默认位置"), "dim")
        try:
            self.jsx_path = jsx.build(
                self.work, self.roots, self.opt.dry, self.opt.consolidate,
                self.opt.cons_dir, self.opt.save, self.state_dir,
                depth=self.opt.depth, cons_mode=self.opt.cons_mode,
                work_dir=const.WORK_DIR, log_path=self.log_path,
                state_dir=self.state_dir, launch_ms=0, mapping=table)
        except (OSError, RuntimeError) as e:
            raise ScriptGenError("生成脚本失败：%s" % e)
        self.log("SCRIPT 脚本已生成 %s" % self.jsx_path, "dim")
        return self

    # ------------------------------------------------------------ 启动

    def start(self):
        """准备 + 启动 AE。别人的 AE 在跑 → 抛 `BlockedByForeignAE`（状态置 BLOCKED）。

        一道闸门：`ensure_ready` 里第一件事就是查进程；本方法把它翻成 BLOCKED 状态，
        界面据此把主按钮改成「请先关闭 AE」，**不启动、不关进程、不弹窗**（R3 / U-09）。
        """
        if self.state is not State.PENDING:
            raise RuntimeError("这次运行已经开过了（state=%s）" % self.state.value)
        self.prepare()
        aehost.clear_markers()
        self.started_at = _now()
        try:
            self.mode, self.launch = aehost.ensure_ready(
                [("After Effects", self.ae_exe)], log=self.log, force_mode=self.force_mode,
                jsx_path=self.jsx_path, token=self.token)
        except aehost.BlockedByForeignAE as e:
            self.state = State.BLOCKED
            self.log("SKIP     已有 AE PID %s，本次不启动（请用户先关闭）"
                     % ",".join(str(p) for p in e.pids), "warn")
            raise
        self.state = State.STARTING if self.mode is not aehost.LaunchMode.MANUAL else State.RUNNING
        self.log("LAUNCH  %s" % self.launch.describe(), "dim")
        return self

    # ------------------------------------------------------------ 轮询

    def poll(self):
        """看一眼现在到哪了。**不 sleep、不阻塞**，界面用 `after()` 反复调它。"""
        if self.state.finished():
            return self._snapshot()

        if self.cancelled:
            return self._cancel()

        self.elapsed = _now() - self.started_at
        lines = self._drain_log()

        # ① 报告出现 = 脚本干完了（不管 AE 还开不开着）
        rep = self._read_report()
        if rep is not None:
            self.report = rep
            return self._finish(lines)

        # ② 手动模式没有进程可等，脚本由用户自己跑 → 只看报告
        if self.mode is aehost.LaunchMode.MANUAL:
            return Poll(self.state, self.elapsed, lines,
                        status_text="等你手动跑脚本（跑完这里会自动结算）")

        # ③ 启动失败（Popen 抛异常）
        err = getattr(self.launch, "launch_error", None) if self.launch else None
        if err:
            return self._fail("启动 AE 失败：%s" % err, lines)

        # ④ 只要还有脚本没跑完，每拍都顺手按掉 AE 挡路的对话框：
        #    · 崩溃修复选项（标题为空的 #32770）—— 不点它 AE 出不了主窗口
        #    · 「解析字体」「打开项目」进度框 —— 它们跑在事件循环之外，排进
        #      app.scheduleTask 的脚本永远等不到执行机会（实测 616 秒不动，按一次回车
        #      4 秒内就跑完）
        #    注意：不能只放在 _poll_starting 里 —— 脚本一创建日志文件（哪怕 0 字节）
        #    就算 started，之后再也不进那个分支（2026-11-15 真机第二轮踩到）。
        if not self.state.finished():
            pid = getattr(self.launch, "pid", None) if self.launch else None
            aehost.press_startup_prompts(pid, self._prompt_seen, self.log)
            # 1.1.1：打开工程时的「缺素材」提示框也会把 app.open() 卡住，一并按掉
            aehost.dismiss_missing_dialogs(pid, self._prompt_seen, self.log)
            aehost.dismiss_progress_dialogs(pid, self._prompt_seen, self.log)

        started = bool(self.log_path) and os.path.exists(self.log_path)
        if not started:
            return self._poll_starting(lines)
        return self._poll_running(lines, started)

    def _poll_starting(self, lines):
        """等脚本开始跑这一段（F-17：40 秒还没动就诊断一次，别让用户对着空窗口等）。"""
        el = self.elapsed
        # 崩溃修复提示：AE 启动时若弹了「崩溃修复选项」，不点它永远出不了主窗口。
        # A2 那条路径在 aehost.wait_script_alive 里已经处理；-r / 手动这两条走这里。
        # 按掉挡路对话框的逻辑统一在 `poll()` 里做（日志一创建就算 started，
        # 只放在这里会漏掉「已 started 但脚本其实没跑」的整段）。这里只留诊断。
        if el >= const.DIAG_AT_S and not self._diag_done:
            self._diag_done = True
            self._diag = self.diagnose()
            for m in self._diag.get("messages", []):
                self.log("DIAG    %s" % m, "warn")
        if self._cold_start():
            limit = const.COLD_START_WAIT_S
        else:
            limit = const.START_WAIT_S if self._diag.get("alive") else const.NO_PROC_WAIT_S

        # 1.1.2：区分「慢」与「死」。AE 打开工程时如果 UI 线程卡住，界面上只会挂着一个
        # 「打开项目」进度框，按也按不掉（它是自绘的、没有按钮、连默认焦点都没有）。
        # 旧逻辑会一路等到 START_WAIT_S / COLD_START 上限 —— 用户体感就是「怎么这么慢」。
        hung = False
        if el >= const.HUNG_CHECK_AT_S:
            try:
                mw = winproc.main_window(getattr(self.launch, "pid", None) or 0)
                hung = bool(mw) and winproc.is_hung(mw["hwnd"])
            except Exception:
                hung = False
            if hung:
                limit = min(limit, const.HUNG_WAIT_S)
                if not self._hung_logged:
                    self._hung_logged = True
                    self.log("DIAG    AE 主窗口无响应（打开工程时卡死，不是启动慢）—— 常见原因：插件冲突、"
                             "字体解析、网络盘/移动硬盘掉线。%d 秒后仍不动就判失败，届时手动开一次该工程"
                             "确认真能打开，再排查插件。" % (const.HUNG_WAIT_S,), "warn")

        if el > limit:
            # 失败也先结算一次（v1.9.8 的 _fail_slow 就这么做）：用户最想知道的不是
            # 「失败」，而是「已经救回来几条」「改动还在不在内存里」。
            why = "等了 %d 秒，AE 一直没开始执行脚本" % int(el)
            if hung:
                why += "（AE 主窗口全程无响应：更像卡死，不是慢）"
            return self._fail(why, lines, partial=True)
        return Poll(State.STARTING, el, lines, limit_s=limit,
                    status_text="启动 AE 中… %ds / 最多等 %ds" % (int(el), limit))

    def _cold_start(self):
        """AE 进程在、但还没有主窗口 —— 是**冷启动慢**，不是卡住。

        2026-11-15 实测：无外网时 AE 2025 冷启动 98.1 秒才出窗口。把它和「窗口出来了
        却不跑脚本」分开计，才不会把正常启动中的实例当失败强关掉。
        """
        pid = getattr(self.launch, "pid", None) if self.launch else None
        if not pid:
            return False
        try:
            from . import winproc
            return winproc.is_alive(pid) and not winproc.has_main_window(pid)
        except Exception:
            return False

    def _poll_running(self, lines, started):
        self.state = State.RUNNING
        el = self.elapsed
        if el > const.SCRIPT_WAIT_S:
            return self._fail("脚本跑了 %d 分钟还没结束" % int(el // 60), lines, partial=True)
        return Poll(State.RUNNING, el, lines, limit_s=const.SCRIPT_WAIT_S,
                    status_text="正在重连… %ds" % int(el))

    # ------------------------------------------------------------ 结算

    def finish(self, report=None):
        """生成 `Result`（幂等）。CLI 与界面都从 `self.result` / `Poll.report` 取数。"""
        if self.result is not None:
            return self.result
        rep = report if report is not None else (self.report or {})
        stats = rep.get("stats") or {}
        stats = dict((k, _as_int(stats.get(k))) for k in
                     ("total", "ok", "fixed", "missing", "ambiguous", "zeroByte"))
        missing = reports.parse_missing(self.missing_path) if self.missing_path else []
        warnings = []
        lw = rep.get(const.REPORT_KEY_LOG_WRITABLE, None)
        if lw is False:
            warnings.append("WARN      过程日志没写成（目录可能只读），以报告为准")
        self.result = Result(
            report=rep, stats=stats, summary=reports.summary_line(stats),
            missing=missing, log_writable=lw, log_path=self.log_path,
            report_path=self.report_path, missing_path=self.missing_path,
            elapsed=self.elapsed, legacy_files=self.legacy_files(), warnings=warnings)
        paths.clear_work()
        return self.result

    @property
    def report_path(self):
        return os.path.join(self.state_dir, const.REPORT_JSON) if self.state_dir else ""

    @property
    def missing_path(self):
        p = os.path.join(self.state_dir, const.MISSING_TXT) if self.state_dir else ""
        return p if p and os.path.exists(p) else ""

    def legacy_files(self):
        """工程根上还留着的老版本中间件（不自动删，只提示一行 —— U-06 / 老用户平滑）。"""
        out = []
        d = os.path.dirname(os.path.abspath(self.work or self.project or ""))
        for name in ("_relink_log.txt", "_relink_report.json", "_relink_missing.txt",
                     "_media_doctor.jsx", "_media_index.json"):
            if d and os.path.exists(os.path.join(d, name)):
                out.append(name)
        return out

    def cancel(self):
        """用户点停止：不打 AE、只置标志，下一次 `poll()` 结算（不关 AE，除非用户勾了）。"""
        self.cancelled = True
        return self

    # ------------------------------------------------------------ 诊断

    def diagnose(self):
        """F-17：把「AE 到底卡在哪」说成人话。返回 {alive, window, messages}。

        一代在这里只打一句「等了 N 秒」，用户只能猜。三种形态对应完全不同的处置：
          · 一个进程都没有      → AE 根本没起来（安装/权限/杀软）
          · 有进程但都没窗口    → 卡在初始化（等 CC、崩溃恢复、缺字体、许可框、插件初始化）
          · 有窗口              → 多半是弹了模态对话框挡在前面（等用户点掉）
        """
        procs = aehost.procs()
        alive = bool(procs)
        window = any(w for _p, w in procs)
        msgs = []
        if not procs:
            msgs.append("一个 AfterFX 进程都没有 —— AE 根本没起来。"
                        "常见原因：AE 装在别的盘/别的版本、安全软件拦截、安装已损坏。")
        elif not window:
            msgs.append("进程在但还没有窗口 —— 卡在初始化。"
                        "常见原因：等他 Adobe 账号/CC 校验、上次崩溃后的恢复提示、"
                        "缺字体弹框、许可对话框、插件初始化卡住。")
        else:
            msgs.append("AE 有窗口但脚本还没开始跑 —— 通常是 AE 弹了模态对话框挡在前面，"
                        "点掉它（或按 Esc）就会继续。")
        return {"alive": alive, "window": window, "messages": msgs}

    def partial_settlement(self):
        """F-17 的结算半边：脚本中途失败时，先把「已经接上几条」算出来再收摊。"""
        fixed, _ok, missing = reports.count_relink_log(self.log_path)
        if not os.path.exists(self.log_path or ""):
            return "一条素材都没接上（日志文件都没生成）"
        parts = ["先结算一下：本次已接上 %d 条，日志里仍记缺 %d 条" % (fixed, missing)]
        if fixed:
            parts.append("改动还在 AE 内存里 —— AE 还开着的话按 Ctrl+S 就能落盘。")
        if self.missing_path:
            parts.append("缺件清单：%s" % self.missing_path)
        return " ".join(parts)

    # ------------------------------------------------------------ 内部

    def _drain_log(self):
        """读日志文件的新增部分（只发新行，不重复、不整文件重读）。"""
        if not self.log_path or not os.path.exists(self.log_path):
            return []
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            return []
        new = lines[self._seen_log:]
        self._seen_log = len(lines)
        return new

    def _read_report(self):
        p = self.report_path
        if not p or not os.path.exists(p):
            return None
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None            # 半截文件：等下一轮再看，不要当成失败
        if not isinstance(data, dict):
            return None
        self.report = data
        return data

    def _finish(self, lines):
        self.state = State.DONE
        self.elapsed = _now() - self.started_at
        self.result = None
        res = self.finish(self.report)
        for w in res.warnings:
            self.log(w, "warn")
        self.log("DONE    %s" % res.summary, "ok")
        if res.legacy_files:
            self.log("NOTE    工程目录还有 %d 个旧版文件：%s（没删，可手动清）"
                     % (len(res.legacy_files), "、".join(res.legacy_files)), "dim")
        self._maybe_close_own_ae()
        self._cleanup_startup()
        return Poll(State.DONE, self.elapsed, lines, report=self.report,
                    message=res.summary, status_text=res.summary)

    def _cleanup_startup(self):
        """D7：A2 正常收尾时把 launcher / guard / alive / done 四个文件一起撤掉。

        失败路径不调（那时标记是现场证据）。任何一处删不掉都不该影响结果，所以整段吞异常。
        """
        inj = getattr(self.launch, "startup", None) if self.launch else None
        if inj is None:
            return
        try:
            inj.finish()
        except Exception:
            pass

    def _fail(self, reason, lines, partial=False):
        self.state = State.FAILED
        self.elapsed = _now() - self.started_at
        msg = ["没能完成：%s" % reason]
        if partial:
            msg.append(self.partial_settlement())
        msg.append("过程日志 %s（临时件留在 %s，便于复现）" % (self.log_path, const.WORK_DIR))
        msg.append("三条排查：① 点掉 AE 的弹窗，或按 Esc ② 关掉已经开着的 AE 再试 "
                   "③ 少放几个目录，别把整块盘加进来")
        text = " ".join(msg)
        for part in msg:
            self.log("FAIL    %s" % part, "bad")
        return Poll(State.FAILED, self.elapsed, lines, message=text, status_text="没能完成 —— %s" % reason)

    def _cancel(self):
        self.state = State.CANCELLED
        self.elapsed = _now() - self.started_at
        text = "已取消 —— 没拿到结果。" + self.partial_settlement()
        self.log("CANCEL  %s" % text, "warn")
        return Poll(State.CANCELLED, self.elapsed, [], message=text, status_text="已取消")

    def _maybe_close_own_ae(self):
        """唯一会自动关 AE 的地方：用户勾了「跑完关掉这次打开的 AE」且确实是自己拉起的。"""
        if not self.opt.close_own_ae:
            return
        pid = self.launch.pid if self.launch else None
        if pid:
            aehost.close_own_ae(pid, "跑完关闭", self.log)

    def _snapshot(self):
        return Poll(self.state, self.elapsed, [], report=self.report,
                    message=self.result.summary if self.result else "",
                    status_text=self.result.summary if self.result else self.state.value)


def _drain_tail(run, emit, interval=0.2, quiet_rounds=3, max_s=3.0):
    """把日志文件的尾巴读完（D4/D5）。

    时序是：JSX 写完报告 → 紧接着写 DONE-OK / SAVED 几行 → 才真正结束。而本工具的
    轮询一看到报告就判完成、跳出循环，于是这几行永远读不到：用户看到「结算行先于
    日志尾行」，DONE-OK 也 5/5 缺失。这里在结算前补一段「连续几轮没有新行就停」的
    收尾读取，最多等 max_s 秒 —— 不引入固定 sleep，正常情况几乎零等待。
    """
    quiet = 0
    t0 = _now()
    while quiet < quiet_rounds and _now() - t0 < max_s:
        try:
            got = run._drain_log()
        except Exception:
            got = []
        if got:
            quiet = 0
            for line in got:
                emit(line)
        else:
            quiet += 1
        if quiet < quiet_rounds:
            _sleep(interval)


def _as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# ================================================================ CLI

USAGE = """用法：
  拾链.exe --cli <工程.aep> [--dry] [--no-save] [--roots 目录1;目录2]
                              [--ae 2025] [--yes] [--close-ae] [--manual]
                              [--mapping 映射表.json]

  --dry      只体检，不改工程
  --no-save  改了但不自动保存
  --roots    素材搜索目录（分号分隔；不给就自动推断）
  --ae       指定 AE 大版本（如 2025）
  --yes      检测到自己开着的 AE 时，允许关掉它再跑（默认拒绝）
  --close-ae 跑完把**本次拉起的那个** AE 关掉（默认不关；旧 --yes 的「跑完关」语义搬到这里）
  --manual   只生成脚本，不启动 AE
  --mapping  手工映射表（默认 %APPDATA%\\AE素材医生\\mapping.json）

退出码：0 成功 / 1 超时 / 2 参数或 AE 问题 / 3 AE 占用且关不掉 / 4 复制工程失败 / 5 生成脚本失败
"""


def parse_cli(argv):
    """解析 CLI 参数 → dict。不认识的参数抛 ValueError（退出码 2 的来源）。"""
    out = {"project": "", "dry": False, "save": True, "roots": [], "ae": "",
           "yes": False, "close_ae": False, "manual": False, "mapping": ""}
    args = list(argv)
    if not args:
        raise ValueError("缺少工程路径")
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--dry":
            out["dry"] = True
        elif a == "--no-save":
            out["save"] = False
        elif a == "--yes":
            out["yes"] = True
        elif a == "--close-ae":
            out["close_ae"] = True
        elif a == "--manual":
            out["manual"] = True
        elif a == "--roots":
            i += 1
            if i >= len(args):
                raise ValueError("--roots 后面要跟目录")
            out["roots"] = [x for x in args[i].split(";") if x]
        elif a == "--mapping":
            i += 1
            if i >= len(args):
                raise ValueError("--mapping 后面要跟映射表文件")
            out["mapping"] = args[i]
        elif a == "--ae":
            i += 1
            if i >= len(args):
                raise ValueError("--ae 后面要跟版本号")
            out["ae"] = args[i]
        elif a.startswith("-"):
            raise ValueError("不认识的参数：%s" % a)
        else:
            if out["project"]:
                raise ValueError("只能给一个工程文件")
            out["project"] = a
        i += 1
    if not out["project"]:
        raise ValueError("缺少工程路径")
    return out


def run_cli(argv, log=None, poll_interval=0.5, max_loop_s=None):
    """命令行跑一次 → 退出码（契约见模块 docstring）。全同步，不建线程。"""
    emit = log or (lambda msg, tag=None: print(msg))
    try:
        opt = parse_cli(argv)
    except ValueError as e:
        emit("参数有问题：%s" % e, "bad")
        emit(USAGE)
        return 2

    ae_exe = ""
    if opt["ae"]:
        want = str(opt["ae"])
        for name, exe in aehost.find_installs():
            if want in name:
                ae_exe = exe
                break
        if not ae_exe:
            emit("没找到 AE %s（已装的有：%s）"
                 % (want, "、".join(n for n, _e in aehost.find_installs()) or "一个都没有"), "bad")
            return 2

    # D3：默认**不关**本次 AE。--close-ae 才关（旧 --yes 的「跑完关」并到这里），
    # 关的仍然只可能是我们自己拉起的那只（aehost.close_own_ae 的白名单）。
    options = Options(dry=opt["dry"], save=opt["save"], copy_project=not opt["dry"],
                      close_own_ae=opt["close_ae"], mapping_path=opt["mapping"])
    # t-21：控制台两路输出对齐。控制台其实是两路交织 ——
    #   ①JSX 日志逐行回显（SUMMARY / SAVED / DONE-OK…）
    #   ②Run 自己打的 DONE / NOTE / WARN
    # `Run.finish()` 是在 `run.poll()` 里跑的，那一刻 JSX 尾巴（SUMMARY、SAVED）还没被读出来，
    # 于是控制台上 DONE 会插在 SUMMARY 前面 —— 日志文件本身顺序是对的，是控制台看着像坏了。
    # 做法：Run 的输出先进 pending，每轮 poll 结束后若还没结束就立刻吐（启动阶段照常实时可见），
    # 只有「结束这一轮」先 drain JSX 尾巴、再 flush pending ⇒ SUMMARY 一定早于最后的 DONE/NOTE。
    pending = []

    def plog(msg, tag=None):
        pending.append((msg, tag))

    def flush_pending():
        while pending:
            msg, tag = pending.pop(0)
            emit(msg, tag)

    run = Run(opt["project"], opt["roots"], options, log=plog, ae_exe=ae_exe,
              force_mode=aehost.LaunchMode.MANUAL if opt["manual"] else None)

    # R3：别人的 AE 在跑。--yes 时允许关掉（CLI 才有的显式授权），否则直接 3
    foreign = aehost.procs()
    if foreign:
        if not opt["yes"]:
            emit("检测到已有 AE 在运行（PID %s）—— 请先关掉它，或加 --yes 让本工具关掉再跑"
                 % ",".join(str(p) for p, _w in foreign), "bad")
            return 3
        for pid, _w in foreign:
            emit("CLOSE   已关掉你开着的 AE PID %s（--yes）" % pid)
            try:
                from . import winproc
                winproc.terminate_tree(pid, wait=8)
            except Exception as e:
                emit("关不掉 AE PID %s：%s" % (pid, e), "bad")
                return 3
        _sleep(2.5)      # 单实例锁释放需要一点时间

    try:
        run.start()
    except aehost.BlockedByForeignAE:
        flush_pending()
        return 3
    except CopyProjectError as e:       # 复制工程失败
        flush_pending()
        emit("%s" % e, "bad")
        return 4
    except ScriptGenError as e:         # 生成脚本失败
        flush_pending()
        emit("%s" % e, "bad")
        return 5
    except ValueError as e:
        flush_pending()
        emit("%s" % e, "bad")
        return 2
    flush_pending()

    t0 = _now()
    while True:
        p = run.poll()
        for line in p.lines:
            emit(line)
        if p.finished:
            # D4/D5：报告落盘后 JSX 还会补写 DONE-OK / SAVED 这几行（见 jsx 模板结尾）。
            # 这一轮 poll() 已经 finished，不会再 drain，所以在这里把尾巴单独读出来 ——
            # 否则「结算行」会先于日志尾行打印，DONE-OK 也就永远看不到。
            _drain_tail(run, emit)
            flush_pending()        # JSX 尾行先出完，再出 Run 的 DONE/NOTE
            break
        flush_pending()
        if p.state is State.STARTING and run._diag_done and not getattr(run, "_diag_printed", False):
            run._diag_printed = True
            for m in run._diag.get("messages", []):
                emit("诊断：%s" % m, "warn")
        if max_loop_s is not None and _now() - t0 > max_loop_s:
            emit("（测试中止：超过 max_loop_s）", "bad")
            return 1
        _sleep(poll_interval)

    if p.state is State.DONE:
        res = run.result or run.finish()
        flush_pending()
        emit("")
        emit(res.summary)
        emit("日志 %s" % res.log_path)
        emit("报告 %s" % res.report_path)
        if res.missing_path:
            emit("缺件清单 %s" % res.missing_path)
        return 0
    flush_pending()
    emit("")
    emit(p.message or "没能完成")
    return 1