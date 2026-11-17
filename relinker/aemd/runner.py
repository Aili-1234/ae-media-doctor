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
import io
import json
import os
import sys
import time

from . import aehost, const, jsx, mapping, paths, plugin_guard, project_plugins, reports, scanner, winproc


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
                 mapping_path="", only_missing=False, warm_open=False, attach=False,
                 close_existing=False):
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
        # 1.1.6 只重试缺件：只用上一次的 _relink_missing.txt，不重扫全盘
        self.only_missing = bool(only_missing)
        # 1.1.9 热启动（默认关）：AE 起好之后再把工程交给它打开（等同双击）。
        # 2026-11-17 真机试过：这条路**没能**绕开 Optical Flares 的死锁（两次都在同一处卡），
        # 所以默认不用，只留开关（Options(warm_open=True) / 以后再说）。
        # 默认关 = 行为与 1.1.7 完全一致，不给已经有戏的路径添新变量。
        self.warm_open = bool(warm_open)
        # 1.1.9 附着模式：用户自己在 AE 里开好了工程，脚本绝不 app.open()。
        # 这是"手动打开"这条最可靠路径的落点 —— 用户来找拾链前就是这么打开工程的。
        self.attach = bool(attach)
        # 1.1.9 检测到已有 AE 时：勾了「自动关掉再跑」就自动关掉它继续，否则 BLOCKED 等用户手关
        self.close_existing = bool(close_existing)


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
                 elapsed=0.0, legacy_files=None, warnings=None,
                 third_party_plugins=None, missing_plugins=None):
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
        # 1.1.9：工程引用的第三方插件显示名（界面展示用）
        self.third_party_plugins = list(third_party_plugins or [])
        # 1.1.9：缺失插件（工程引用了但 AE 没装），缺失 0 就空列表
        self.missing_plugins = list(missing_plugins or [])


# 时间与睡眠做成模块级引用（测试里换掉，不必真等 90 秒）
_now = time.time
_sleep = time.sleep


def _shell_open(path):
    """把文件交给「已经在跑」的 AE 打开 —— 等同用户在资源管理器里双击。

    单独拆成一个函数是为了打桩：测试里绝不能真去开文件（会弹窗、会抢焦点）。
    2026-11-17 真机对照：先开 AE 再双击工程能过，冷启动带工程会卡在插件窗口上。
    """
    os.startfile(path)     # noqa: S606  （Windows 专用；本工具只跑 Windows）


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
        self.work = ""             # 脚本要修的工程 —— 方案甲后恒等于原工程（不再有 _fixed 副本）
        self.copy_path = ""        # 保留旧字段兼容（已废弃，指向空）
        self.backup_path = ""      # 备份路径（还原点；没备份则空）
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
        self.done_items = 0        # 已处理素材条数（进度用）
        self.total_items = 0       # 工程里素材总条数（ITEMS 行给出的）
        self._diag_done = False
        self._hung_logged = False
        self._alive_at = 0.0        # 上次查「AE 还在不在」的时刻（节流用）
        self._ae_gone = 0           # 连续几次查不到 AE
        self._block_since = 0.0     # 第一次看见「插件窗口挡路」的时刻（1.1.9）
        self._hang_at = 0.0         # 上次采「无响应 / CPU 有没有动」的时刻（节流用，1.1.9）
        self._cpu_last = None       # 上次采到的进程 CPU 秒数（1.1.9）
        self._dead_since = 0.0      # 「无响应 + CPU 不动」连续了多久（1.1.9）
        self._hung_saved = False    # 现场只存一次
        self.warm = False           # 1.1.9 热启动：AE 起好之后由我们把工程交给它
        self._warm_sent = False     # 工程已经交出去了（只交一次）
        self._warm_at = 0.0         # 上次探测「AE 能不能响应」的时刻（节流用）
        self._plugins_disabled = False  # 1.1.9：本轮是否禁用了第三方插件（跑完要恢复）
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
        # 1.1.9 附着模式要先定下来：显式 attach，或走手动模式（用户 Run Script File），
        # 脚本都绝不 app.open()。必须在复制工程**之前**算好 —— 手动模式不复制副本，
        # 脚本直接对用户已打开的工程跑修复。
        self.attach = bool(self.opt.attach) or (self.force_mode is aehost.LaunchMode.MANUAL)

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

        # ① 备份原工程（可选）—— 方案甲：修复**直接改用户打开的原工程**，先备份一份当还原点。
        # 为什么改成"备份"而不是"复制副本再修副本"：用户来找拾链时打开的就是原工程，
        # 他最想看到的是"我眼前这个工程被治好"；再搞一个 _fixed 副本让用户去开，是多余的心智负担，
        # 而且手动模式下副本路径跟用户打开的工程对不上（2026-11-17 真机踩到）。
        # 所以：脚本修的永远是 self.work == 原工程；备份只是还原点，不参与修复。
        self.work = self.project
        self.backup_path = ""
        if self.opt.copy_project and not self.opt.dry:
            try:
                self.backup_path = paths.make_backup(self.project, log=self.log)
            except RuntimeError as e:
                raise CopyProjectError("备份工程失败：%s" % e)

        # ② 路径三件套：工程旁的结果目录、软件自己的日志与临时目录
        out_dir = os.path.dirname(os.path.abspath(self.work))
        self.state_dir = paths.state_dir_of(out_dir)
        only = []
        if self.opt.only_missing:
            mp = os.path.join(self.state_dir, const.MISSING_TXT)
            only = [x.get("name") for x in reports.parse_missing(mp) if x.get("name")]
            if not only:
                raise ValueError("没有上一次的缺件清单（%s）—— 先完整跑一次，再用「只重试缺件」" % mp)
            self.log("ONLY    只重试上一次没接上的 %d 条（不重扫全盘）" % len(only), "dim")
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

        # 1.1.9 热启动能不能用：得看这台机器上 A2（Startup 目录）这条路通不通 ——
        # 只有 A2 才谈得上"先起 AE、再交工程"，`-r`/手动那两条不是这个节奏。
        self.warm = bool(self.opt.warm_open) and self._can_warm()
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
                state_dir=self.state_dir, launch_ms=0, mapping=table, only_missing=only,
                warm_open=self.warm, warm_wait=const.WARM_OPEN_WAIT_S if self.warm else 0,
                attach=self.attach)
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
        # 1.1.9 安全网：先抢救「上次崩了没恢复」的插件（幂等，无残留则什么都不做）。
        try:
            plugin_guard.recover_if_left(log=self.log)
        except Exception:
            pass
        self.prepare()
        aehost.clear_markers()
        # 1.1.9 方案：自动模式下，启动 AE 前先把第三方插件禁掉（改名 .off），
        # 让 AE 干净地启动、打开工程 —— 这些插件（Optical Flares 等）会在打开工程时
        # 弹窗把主线程卡死。跑完（无论成败）在 _finish/_fail/_cancel 里恢复。
        # 只对「自动打开」这条路禁；attach（用户自己开工程）不需要 —— 用户手动能过。
        if (not self.attach and not self.opt.dry
                and self.force_mode is not aehost.LaunchMode.MANUAL):
            try:
                n = plugin_guard.disable(self.ae_exe, log=self.log)
                if n:
                    self._plugins_disabled = True
            except Exception as e:
                self.log("PLUGIN   禁用第三方插件出错（跳过，不影响跑）：%s" % e, "warn")
        self.started_at = _now()
        try:
            self.mode, self.launch = aehost.ensure_ready(
                [("After Effects", self.ae_exe)], log=self.log, force_mode=self.force_mode,
                jsx_path=self.jsx_path, token=self.token)
        except aehost.BlockedByForeignAE as e:
            # 1.1.9：勾了「自动关掉再跑」→ 关掉现有 AE 后重试一次（不抛 BLOCKED）。
            if self.opt.close_existing and e.pids:
                self.log("CLOSE    检测到已有 AE（PID %s），按设置自动关掉再继续"
                         % ",".join(str(p) for p in e.pids), "warn")
                for pid in e.pids:
                    try:
                        aehost.close_own_ae(pid, "用户勾了自动关闭", log=self.log, wait=6, force=True)
                    except Exception:
                        pass
                try:
                    self.mode, self.launch = aehost.ensure_ready(
                        [("After Effects", self.ae_exe)], log=self.log,
                        force_mode=self.force_mode, jsx_path=self.jsx_path, token=self.token)
                except aehost.BlockedByForeignAE:
                    self._restore_plugins()
                    self.state = State.BLOCKED
                    self.log("SKIP     关掉后仍有 AE 在跑，本次不启动", "warn")
                    raise
            else:
                self._restore_plugins()
                self.state = State.BLOCKED
                self.log("SKIP     已有 AE PID %s，本次不启动（请用户先关闭）"
                         % ",".join(str(p) for p in e.pids), "warn")
                raise
        self.state = State.STARTING if self.mode is not aehost.LaunchMode.MANUAL else State.RUNNING
        self.log("LAUNCH  %s" % self.launch.describe(), "dim")
        return self

    def _restore_plugins(self):
        """跑完/失败/取消时恢复被禁的第三方插件（幂等，只有真禁过才动）。"""
        if not self._plugins_disabled:
            return
        self._plugins_disabled = False
        try:
            plugin_guard.restore(log=self.log)
        except Exception as e:
            self.log("PLUGIN   恢复插件出错（请检查 Plug-ins 目录）：%s" % e, "bad")

    # ------------------------------------------------------------ 轮询

    def poll(self):
        """看一眼现在到哪了。**不 sleep、不阻塞**，界面用 `after()` 反复调它。"""
        if self.state.finished():
            return self._snapshot()

        if self.cancelled:
            return self._cancel()

        self.elapsed = _now() - self.started_at
        lines = self._drain_log()
        d_, t_ = self._scan_progress(lines)
        self.done_items += d_
        if t_:
            self.total_items = t_

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
            # 1.1.7：不认识的框（如「设置窗口」）只会把 open 卡死，Esc 掉并报出标题
            aehost.dismiss_unknown_dialogs(pid, self._prompt_seen, self.log)
            aehost.dismiss_progress_dialogs(pid, self._prompt_seen, self.log)

        started = bool(self.log_path) and os.path.exists(self.log_path)
        if not started:
            return self._poll_starting(lines)
        return self._poll_running(lines, started)

    def _capture_hung(self, why=""):
        """把卡死现场落盘（只存一次）。任何失败都吞掉 —— 存证据不该把主流程搞挂。"""
        if self._hung_saved:
            return ""
        self._hung_saved = True
        try:
            ev = aehost.capture_hung(getattr(self.launch, "pid", 0) or 0,
                                     os.path.dirname(self.log_path) if self.log_path else "",
                                     getattr(self.launch, "exe", "") or "")
        except Exception:
            return ""
        if ev and self.log is not None:
            self.log("DIAG    卡死现场已存到 %s（%s：窗口 / 插件 / 版本）—— 下次复现请连同它一起发"
                     % (ev, why or "卡死"), "dim")
        return ev or ""

    def _can_warm(self):
        """这台机器上「A2 + 热启动」这条路通不通（看 Startup 目录挑不挑得到）。"""
        try:
            year = aehost.year_from_exe(self.ae_exe)
            return bool(aehost.startup_dir(year))
        except Exception:
            return False

    def _warm_tick(self):
        """热启动：等 AE 主窗口能响应了，再把工程"双击"给它。

        为什么不在启动参数里直接带工程：那条路等于冷启动就打开 —— 2026-11-17 真机
        取证，Optical Flares 的窗口正是在那一刻把 AE 主线程锁死（CPU 归零、61 秒不动）。
        用户手动能过，是因为他先让 AE 起来、再双击工程；这里照抄那个顺序。
        """
        if self._warm_sent or not self.warm or self.mode is not aehost.LaunchMode.A2_STARTUP:
            return
        if _now() - self._warm_at < 2.0:
            return
        self._warm_at = _now()
        pid = getattr(self.launch, "pid", None) or 0
        if not pid:
            return
        try:
            mw = winproc.main_window(pid)
            if not mw or winproc.is_hung(mw["hwnd"]):
                return          # 还没起来 / 还在忙，下一拍再看
        except Exception:
            return
        self._warm_sent = True
        try:
            _shell_open(self.work)
            self.log("WARM      AE 已就绪，把工程交给它打开（等同双击）：%s" % self.work, "dim")
        except Exception as e:
            self.log("WARM      打不开工程（%s）—— 等脚本自己开" % e, "warn")

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
                    # 1.1.7：卡死当下就把现场抓下来（窗口清单 / 插件清单 / 版本）。
                    # 以前只会说"卡了"，事后没人能判断是谁 —— 这次开始不再空手。
                    self._capture_hung("打开工程卡死")

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
        # 1.1.9 热启动：脚本就位了，轮到我们把工程交给 AE（只交一次）
        self._warm_tick()
        if el > const.SCRIPT_WAIT_S:
            return self._fail("脚本跑了 %d 分钟还没结束" % int(el // 60), lines, partial=True)

        # 1.1.7：AE 都没了还在等脚本，只会白等 10 分钟
        # （2026-11-17 真机实测：AE 退出后 CLI 又傻等 5 分钟，最后是人手动停掉的）。
        # 判据用「有没有任何 AfterFX」而不是「我们那只 pid 还在不在」：
        # AE 崩溃修复后会自己重启，pid 会变，按 pid 判会误杀。
        if _now() - self._alive_at >= self._ALIVE_EVERY_S:
            self._alive_at = _now()
            try:
                alive = bool(aehost.procs())
            except Exception:
                alive = True
            self._ae_gone = 0 if alive else (self._ae_gone + 1)
        if self._ae_gone >= self._ALIVE_GONE_LIMIT:
            return self._fail("AE 进程已经不在了（脚本没跑完），这次没接上 —— 日志最后一行是它停的地方",
                              lines, partial=True)

        # 1.1.9：脚本已经开跑、但 AE 被卡住不干活 —— 以前这里只按 SCRIPT_WAIT_S 干等 10 分钟。
        # 2026-11-17 真机时间线（基线，不经过本工具）：打开工程 22.6s 出现「打开项目」，
        # 24.1s Video Copilot 的 Optical Flares（VCWINDOW）冒出来，CPU 立刻归零，
        # 61 秒仍然一动不动，Esc / WM_CLOSE 都无效。这种等下去是纯浪费 —— 用户以为"在跑"。
        blk = None
        try:
            blk = aehost.plugin_block(self.launch.pid if self.launch else 0,
                                      self._prompt_seen, self.log)
        except Exception:
            blk = None
        if blk:
            if not self._block_since:
                self._block_since = el
            if el - self._block_since >= const.PLUGIN_BLOCK_WAIT_S:
                self._capture_hung("插件窗口卡死")
                return self._fail("AE 被插件窗口卡住了：「%s」一直挂着，打开工程停在原地"
                                  "（不是缺素材，也不是慢）—— 换插件 / 临时挪走插件 / 换 AE 版本，"
                                  "上面 DIAG 那三行里有具体做法"
                                  % ((blk.get("title") or "").strip() or blk.get("cls") or "?"),
                                  lines, partial=True)
        else:
            self._block_since = 0.0

        # 认不出来的死锁：**主窗口回不了消息 + 进程 CPU 不动**，两条同时成立才算。
        # 只看第一条会误杀「正常加载」（那时主线程忙着读盘，一样不回消息，实测 cpu+1.3s/1.5s）。
        if not blk and _now() - self._hang_at >= const.HANG_SAMPLE_S:
            self._hang_at = _now()
            pid = self.launch.pid if self.launch else 0
            cpu = None
            try:
                cpu = winproc.cpu_seconds(pid)
            except Exception:
                cpu = None
            prev, self._cpu_last = self._cpu_last, cpu
            hung = False
            try:
                mw = winproc.main_window(pid)
                hung = bool(mw) and winproc.is_hung(mw["hwnd"])
            except Exception:
                hung = False
            idle = cpu is not None and prev is not None and (cpu - prev) < const.IDLE_CPU_S
            if hung and idle:
                if not self._dead_since:
                    self._dead_since = el
                    if not self._hung_logged:
                        self._hung_logged = True
                        self.log("DIAG    AE 主窗口无响应、进程也不吃 CPU —— 这是卡死，不是慢。"
                                 "%d 秒后仍不动就收场（现场已存下来）" % (const.DEADLOCK_WAIT_S,), "warn")
                        self._capture_hung("运行期死锁")
                if el - self._dead_since >= const.DEADLOCK_WAIT_S:
                    return self._fail("AE 卡死了：%d 秒里主窗口一次都没响应、CPU 也没动 —— "
                                      "这次接不上，现场在日志目录的 hung-*.txt 里"
                                      % int(el - self._dead_since), lines, partial=True)
            else:
                self._dead_since = 0.0

        if blk:
            txt = "AE 卡在插件窗口上（%s）… %ds" % (
                (blk.get("title") or "").strip() or blk.get("cls") or "?", int(el))
        elif self.total_items:
            txt = "正在重连… %d/%d  %ds" % (self.done_items, self.total_items, int(el))
        else:
            txt = "正在重连… 已处理 %d 条  %ds" % (self.done_items, int(el))
        return Poll(State.RUNNING, el, lines, limit_s=const.SCRIPT_WAIT_S, status_text=txt)

    # ------------------------------------------------------------ 结算

    #: 1.1.7：AE 存活探测的节流与判据（见 _poll_running）
    _ALIVE_EVERY_S = 5.0
    _ALIVE_GONE_LIMIT = 2

    #: 需要提到结果区的日志标签（1.1.7）。只收「接上了、但可能有问题」这一类 ——
    #: 用户不必翻日志就知道手上这份结果有几处要自己核。
    #: ZERO 也算：0 字节素材被 replace 上去，AE 里看着是接上了，实际播不动。
    _WARN_TAGS = ("SEQ-OFFSET ", "SEQ-WARN  ", "LAYER-WARN ", "RELINK-SUSPECT ", "ZERO      ")
    _WARN_LIMIT = 5

    def _collect_warns(self):
        """从本次日志里抓需要人核一眼的行（最多 LIMIT 条，其余折成一句）。

        **存原始行**而不是改写成中文短句：用户拿着这句话能在日志里 grep 到同一处，
        改写过的文案反而对不上号。
        """
        p = self.log_path
        if not p or not os.path.isfile(p):
            return []
        hits = []
        try:
            with io.open(p, encoding="utf-8", errors="replace") as fh:
                for ln in fh:
                    t = ln.rstrip()
                    if t.startswith(self._WARN_TAGS):
                        hits.append(t)
        except OSError:
            return []
        if len(hits) > self._WARN_LIMIT:
            extra = len(hits) - self._WARN_LIMIT
            return hits[:self._WARN_LIMIT] + [
                "…另有 %d 条同类提醒，见日志 %s" % (extra, os.path.basename(p))]
        return hits

    def finish(self, report=None):
        """生成 `Result`（幂等）。CLI 与界面都从 `self.result` / `Poll.report` 取数。"""
        if self.result is not None:
            return self.result
        rep = report if report is not None else (self.report or {})
        stats = rep.get("stats") or {}
        stats = dict((k, _as_int(stats.get(k))) for k in
                     ("total", "ok", "fixed", "missing", "ambiguous", "zeroByte", "mapped"))
        missing = reports.parse_missing(self.missing_path) if self.missing_path else []
        warnings = []
        lw = rep.get(const.REPORT_KEY_LOG_WRITABLE, None)
        # 这里存**纯文本**：界面统一加「WARN      」前缀。以前这里自己带了一遍，
        # 界面上就成了「WARN      WARN      过程日志…」（一直没人看见，因为极少触发）。
        if lw is False:
            warnings.append("过程日志没写成（目录可能只读），以报告为准")
        warnings.extend(self._collect_warns())
        # 1.1.9：提取工程引用的第三方插件 + 缺失插件，写日志 + 塞进结果供界面展示。
        # 让用户不用打开第二遍去比对「这个工程还依赖哪些可能缺失的插件」。
        tp, missing_plugins = [], []
        try:
            tp = project_plugins.third_party_of(self.project)
            missing_plugins = project_plugins.missing_of(self.project)
        except Exception:
            tp, missing_plugins = [], []
        if missing_plugins:
            self.log("PLUGIN-MISS 此工程引用的第三方插件缺失 %d 个（AE 里没装）：%s"
                     % (len(missing_plugins), "、".join(missing_plugins)), "warn")
        elif tp:
            self.log("PLUGINS  此工程引用了 %d 个第三方插件，全部已装" % len(tp), "dim")
        self.result = Result(
            report=rep, stats=stats, summary=reports.summary_line(stats),
            missing=missing, log_writable=lw, log_path=self.log_path,
            report_path=self.report_path, missing_path=self.missing_path,
            elapsed=self.elapsed, legacy_files=self.legacy_files(), warnings=warnings,
            third_party_plugins=tp, missing_plugins=missing_plugins)
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

    @staticmethod
    def _scan_progress(lines):
        """从 JSX 日志行里数出 (已处理, 总数)；数不出来返回 (0, 0)。

        一行一素材：`OK / ZERO / RELINKED / MISSING / FAILED / WOULD-FIX` 各算处理过一条；
        `ITEMS   N` 由脚本开头写出，就是总数。界面拿它显示「17/32」，
        比干等一个转圈有用得多（2026-11-17 用户提的：大工程没进度）。
        """
        done = 0
        total = 0
        for ln in lines or []:
            if ln.startswith("ITEMS"):
                parts = ln.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    total = int(parts[1])
            elif ln.startswith(("OK ", "ZERO ", "RELINKED ", "MISSING ", "FAILED ", "WOULD-FIX ")):
                done += 1
        return done, total

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
        self._restore_plugins()          # 1.1.9：恢复被禁的第三方插件
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
        self._restore_plugins()          # 1.1.9：失败也恢复被禁的第三方插件
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
        self._restore_plugins()          # 1.1.9：取消也恢复被禁的第三方插件
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
  拾链.exe --cli <工程.aep> [更多 .aep 或目录…] [--dry] [--no-save] [--roots 目录1;目录2]
                              [--ae 2025] [--yes] [--close-ae] [--manual]
                              [--mapping 映射表.json] [--only-missing] [--json]

  --dry      只体检，不改工程
  --no-save  改了但不自动保存
  --roots    素材搜索目录（分号分隔；不给就自动推断）
  --ae       指定 AE 大版本（如 2025）
  --yes      检测到自己开着的 AE 时，允许关掉它再跑（默认拒绝）
  --close-ae 跑完把**本次拉起的那个** AE 关掉（默认不关；旧 --yes 的「跑完关」语义搬到这里）
  --manual   只生成脚本，不启动 AE
  --only-missing  只重试上一次没接上的那几条（用工程 .ae_doctor 里的缺件清单）
  --json     只往 stdout 打一坨结构化结果，日志走 stderr（方便批处理 / 接别的工具）
  --mapping  手工映射表（默认 %APPDATA%\\AE素材医生\\mapping.json）

退出码：0 成功 / 1 超时 / 2 参数或 AE 问题 / 3 AE 占用且关不掉 / 4 复制工程失败 / 5 生成脚本失败
"""


def parse_cli(argv):
    """解析 CLI 参数 → dict。不认识的参数抛 ValueError（退出码 2 的来源）。"""
    out = {"project": "", "projects": [], "dry": False, "save": True, "roots": [], "ae": "",
           "yes": False, "close_ae": False, "manual": False, "mapping": "",
           "only_missing": False, "json": False}
    args = list(argv)
    if not args:
        raise ValueError("缺少工程路径")
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--cli":
            # 1.1.7：--cli 后面可以跟多个（文件 / 目录），连续吸收到下一个 -- 开关为止。
            # 这样 `--cli a.aep b.aep D://工程目录 --dry` 与老的单工程写法都成立。
            i += 1
            got = []
            while i < len(args) and not str(args[i]).startswith("-"):
                got.append(args[i])
                i += 1
            if not got:
                raise ValueError("--cli 后面要跟至少一个 .aep（或装着 .aep 的目录）")
            out["projects"].extend(got)
            continue
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
        elif a == "--only-missing":
            out["only_missing"] = True
        elif a == "--json":
            out["json"] = True
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
            # 位置参数也当工程收（`拾链 a.aep b.aep`），不再报"只能给一个"
            out["projects"].append(a)
        i += 1
    if not out["projects"]:
        raise ValueError("缺少工程路径")
    out["project"] = out["projects"][0]
    return out


def expand_projects(specs, max_depth=1):
    """把「文件 / 目录 / 多个」摊平成工程清单 → ``(projects, notes)``。

    目录只往下走 **两层**（目录本身 + 一层子目录）：剪辑工程通常就是
    `本季/第01集/xxx.aep` 这个深度。再深就容易扫到缓存目录、备份目录，
    反而把用户没想处理的东西卷进来 —— 批量最怕的不是慢，是"顺手改了不该改的"。
    """
    out, notes, seen = [], [], set()

    def add(path):
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            return
        seen.add(key)
        out.append(os.path.abspath(path))

    def walk(d, depth):
        try:
            names = sorted(os.listdir(d))
        except OSError as e:
            notes.append("%s 读不了：%s" % (d, e))
            return
        for n in names:
            fp = os.path.join(d, n)
            if os.path.isdir(fp):
                if depth < max_depth:
                    walk(fp, depth + 1)
            elif n.lower().endswith(".aep"):
                add(fp)

    for s0 in (specs or []):
        s1 = (s0 or "").strip().strip('"')
        if not s1:
            continue
        if os.path.isdir(s1):
            walk(s1, 0)
        elif os.path.isfile(s1):
            if s1.lower().endswith(".aep"):
                add(s1)
            else:
                notes.append("不是 .aep，跳过：%s" % s1)
        else:
            notes.append("找不到：%s" % s1)
    return out, notes


def _argv_for(project, opt):
    """把选项还原成 argv（批量里每个工程复用同一组开关，且**不带 --json**）。"""
    a = ["--cli", project]
    if opt.get("dry"):
        a.append("--dry")
    if not opt.get("save", True):
        a.append("--no-save")
    if opt.get("yes"):
        a.append("--yes")
    if opt.get("close_ae"):
        a.append("--close-ae")
    if opt.get("manual"):
        a.append("--manual")
    if opt.get("only_missing"):
        a.append("--only-missing")
    if opt.get("ae"):
        a.extend(["--ae", opt["ae"]])
    if opt.get("mapping"):
        a.extend(["--mapping", opt["mapping"]])
    if opt.get("roots"):
        a.extend(["--roots", ";".join(opt["roots"])])
    return a


def result_json(res):
    """`Result` → 可序列化 dict（`--json` 的 stdout 内容）。

    单独抽出来是为了能单测：批处理调用方靠这些键名写脚本，键名一改就断，
    所以它得有自己的测试，而不是"跑一次看着对"。
    """
    st = dict(res.stats or {})
    return {
        "ok": True,
        "code": 0,
        "summary": res.summary,
        "stats": st,
        "mapped": int(st.get("mapped") or 0),
        "missing": [{"name": m.get("name") or "", "original": m.get("original") or ""}
                    for m in (res.missing or [])],
        "warnings": list(res.warnings or []),
        "report": res.report_path,
        "log": res.log_path,
        "missing_txt": res.missing_path,
        "elapsed_s": round(float(res.elapsed or 0.0), 1),
    }


def run_cli(argv, log=None, poll_interval=0.5, max_loop_s=None, collect=None):
    """命令行跑一次 → 退出码（契约见模块 docstring）。全同步，不建线程。"""
    # --json：日志一律走 stderr，stdout 只留最后一坨 JSON。
    # 在 parse_cli 之前就分流 —— 参数错也要输出结构化结果，否则调用方拿不到原因。
    as_json = "--json" in argv
    if as_json and log is None:
        def emit(msg, tag=None):
            print(msg, file=sys.stderr, flush=True)
    else:
        emit = log or (lambda msg, tag=None: print(msg))

    def done(code, error="", extra=None):
        """统一出口：非 --json 时只返回退出码（行为与以前一模一样）。

        `collect` 是给批量用的：每个工程跑完把自己的结果写回那一行，
        免得为了汇总再去解析 stdout。
        """
        if collect is not None:
            if error:
                collect["error"] = error
            if extra:
                collect.update(extra)
        if not as_json:
            return code
        doc = {"ok": code == 0, "code": code}
        if error:
            doc["error"] = error
        if extra:
            doc.update(extra)
        print(json.dumps(doc, ensure_ascii=False), flush=True)
        return code

    try:
        opt = parse_cli(argv)
    except ValueError as e:
        emit("参数有问题：%s" % e, "bad")
        emit(USAGE)
        return done(2, "参数有问题：%s" % e)

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
            return done(2, "没找到 AE %s" % want)

    # 1.1.7 批量：--cli 后面可能是一个文件、一个目录、或一串。先摊平再决定走哪条路。
    projects, notes = expand_projects(opt.get("projects") or [opt["project"]])
    for n in notes:
        emit("NOTE     %s" % n, "dim")
    if not projects:
        emit("没有可处理的工程（%s）" % "、".join(opt.get("projects") or []), "bad")
        return done(2, "没有可处理的工程")
    if len(projects) > 1:
        return _run_many(projects, opt, emit, as_json, done, poll_interval, max_loop_s)
    opt["project"] = projects[0]

    # D3：默认**不关**本次 AE。--close-ae 才关（旧 --yes 的「跑完关」并到这里），
    # 关的仍然只可能是我们自己拉起的那只（aehost.close_own_ae 的白名单）。
    options = Options(dry=opt["dry"], save=opt["save"], copy_project=not opt["dry"],
                      close_own_ae=opt["close_ae"], mapping_path=opt["mapping"],
                      only_missing=opt["only_missing"], close_existing=bool(opt["yes"]))
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
    # 例外：--manual（附着模式）时 AE 开着是**正常**的 —— 用户就是自己开着 AE、
    # 打开工程后让脚本对当前工程跑修复的，这里绝不关、也绝不拒绝。
    foreign = aehost.procs()
    if foreign and not opt["manual"]:
        if not opt["yes"]:
            emit("检测到已有 AE 在运行（PID %s）—— 请先关掉它，或加 --yes 让本工具关掉再跑"
                 % ",".join(str(p) for p, _w in foreign), "bad")
            return done(3, "已有 AE 在运行：%s" % ",".join(str(p) for p, _w in foreign))
        for pid, _w in foreign:
            emit("CLOSE   已关掉你开着的 AE PID %s（--yes）" % pid)
            try:
                from . import winproc
                winproc.terminate_tree(pid, wait=8)
            except Exception as e:
                emit("关不掉 AE PID %s：%s" % (pid, e), "bad")
                return done(3, "关不掉 AE PID %s：%s" % (pid, e))
        _sleep(2.5)      # 单实例锁释放需要一点时间

    try:
        run.start()
    except aehost.BlockedByForeignAE:
        flush_pending()
        return done(3, "有别的 AE 占着单实例锁")
    except CopyProjectError as e:       # 复制工程失败
        flush_pending()
        emit("%s" % e, "bad")
        return done(4, "%s" % e)
    except ScriptGenError as e:         # 生成脚本失败
        flush_pending()
        emit("%s" % e, "bad")
        return done(5, "%s" % e)
    except ValueError as e:
        flush_pending()
        emit("%s" % e, "bad")
        return done(2, "%s" % e)
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
            return done(1, "测试中止：超过 max_loop_s")
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
        return done(0, extra=result_json(res))
    flush_pending()
    emit("")
    emit(p.message or "没能完成")
    return done(1, p.message or "没能完成")


def _short_path(path, width=46):
    """汇总表里的工程名：只留「父目录/文件名」，太长就从左边截。"""
    p = str(path or "").replace("\\", "/")
    parts = [x for x in p.split("/") if x]
    s2 = "/".join(parts[-2:]) if len(parts) >= 2 else p
    return s2 if len(s2) <= width else "…" + s2[-width:]


def _run_many(projects, opt, emit, as_json, done, poll_interval=0.5, max_loop_s=None):
    """串行跑一批工程，最后给一张汇总表。

    为什么串行：AE 是单实例的，并行只会互相抢锁；批量真正的价值是"一觉醒来都跑完了"，
    不是"快 3 分钟"。单个失败**不打断**其余 —— 批量最怕跑到第 7 个就停，
    用户还得自己挑剩下哪些没跑。
    """
    rows = []
    totals = {"projects": len(projects), "ok": 0, "failed": 0,
              "fixed": 0, "missing": 0, "mapped": 0, "elapsed_s": 0.0}
    for i, proj in enumerate(projects, 1):
        emit("")
        emit("==== [%d/%d] %s ====" % (i, len(projects), proj))
        row = {"project": proj, "code": 1}
        # 内层不带 --json：stdout 只能有一坨（外层这一坨），否则管道里就是垃圾
        code = run_cli(_argv_for(proj, opt), log=emit,
                       poll_interval=poll_interval, max_loop_s=max_loop_s, collect=row)
        row["code"] = code
        rows.append(row)
        st = row.get("stats") or {}
        if code == 0:
            totals["ok"] += 1
        else:
            totals["failed"] += 1
        for k in ("fixed", "missing", "mapped"):
            totals[k] += int(st.get(k) or 0)
        totals["elapsed_s"] = round(totals["elapsed_s"] + float(row.get("elapsed_s") or 0.0), 1)

    emit("")
    emit("==== 批量结果：%d 个工程，%d 成功 / %d 失败 ===="
         % (len(rows), totals["ok"], totals["failed"]))
    for r in rows:
        if r["code"] == 0:
            emit("  ok    %-48s %s" % (_short_path(r["project"]), r.get("summary") or ""))
        else:
            emit("  FAIL  %-48s %s" % (_short_path(r["project"]),
                                       r.get("error") or "退出码 %s" % r["code"]))
    emit("  合计：接上 %d 条，仍缺 %d 条%s"
         % (totals["fixed"], totals["missing"],
            ("，其中 %d 条按映射表接上" % totals["mapped"]) if totals["mapped"] else ""))
    code = 0 if totals["failed"] == 0 else 1
    return done(code, "" if code == 0 else "%d 个工程失败" % totals["failed"],
                {"projects": rows, "totals": totals})
