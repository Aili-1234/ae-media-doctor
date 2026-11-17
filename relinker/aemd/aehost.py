# -*- coding: utf-8 -*-
"""aemd.aehost —— AE 探测与启动管线（R1 / R3 / R4 / R5 都落在这里）。

负责：
  · `find_installs()` 列出本机 AE 安装（显示名 → AfterFX.exe 路径）
  · `procs()` 当前 AE 情况（复用 winproc，不自己碰 ctypes）
  · `ensure_ready()` 启动决策与执行：先查别的 AE（R3），再按 `LaunchMode` 选路
  · `prepare_startup()/wait_script_alive()` A2：Startup 注入 + 守卫 + marker 轮询
  · `close_own_ae(pid, reason)` —— **全库唯一的关 AE 函数**（R1 / §8）
        只接受本程序自己拉起的 PID；别的 AE 一律不碰、不弹窗、只写一行 SKIP 日志
  · `LaunchMode` 枚举：把「怎么启动」这件事显式建模（一代错误 #4）

不负责：
  · 生成 JSX 文本（jsx.py 写文件；本模块只拿现成的 jsx 路径）
  · 写报告（reports.py）、编流程（runner.py）
  · 界面与文案（ui 层拿到的只是「结果对象」，不是弹窗）
  · 直接 taskkill（那是 winproc.terminate_tree；本模块是唯一允许调它的地方）

依赖：`.const`、`.paths`、`.winproc`

═══════════════════ 一代的三个坑，v2 的结构性防线 ═══════════════════
1) 关 AE 的入口散落四处、缺省 True，用户没勾也把 AE 关了（R1/R2）：
   → 全库唯一关闭函数 `close_own_ae(pid, reason)`，只认 `_OWN_PIDS`；
     界面层 import 不到 winproc，也就拿不到 taskkill（§8 test_never_kill_foreign_ae）。
2) 检测到已有 AE 时还硬启动，单实例冲突 → 脚本送不进去、用户以为软件坏了（R3）：
   → `ensure_ready()` 第一步就是「有别人的 AE？BLOCKED，不启动」，
     抛 `BlockedByForeignAE`，由 ui 翻成「请先关闭 AE」。
3) `-r` 跑完 AE 自己就退，被当成「AE 神秘消失」追了一下午（一代错误 #4）：
   → 启动方式用 `LaunchMode` 显式建模，退出语义写在枚举的 docstring 里：
     A2_STARTUP = 写 Startup 脚本 + 无参数启动（**AE 活着**，脚本跑完自删）
     B_RAISE    = `-r` 启动（**AE 会自己退**，所以要重新拉起并打开 fixed 工程）
     MANUAL     = 生成脚本给用户手动跑（我们完全不启动 AE）
"""

import enum
import io
import os
import shutil
import subprocess
import time

from . import const, paths, winproc


class LaunchMode(enum.Enum):
    """本程序启动 AE 的三种方式（R4）。退出语义是枚举的一部分，别在别处补注释。"""

    A2_STARTUP = "a2"   # 首选：jsx 写 Startup 目录，无参数启动 AE；脚本跑完自删；**AE 保留**
    B_RAISE = "b"       # 回退：`-r` 跑脚本；**已知副作用：跑完 AE 自己退出** → 需重新拉起
    MANUAL = "manual"   # 兜底：不启动，只生成脚本 + 三步指引让用户自己跑

    def keeps_ae_alive(self):
        return self is LaunchMode.A2_STARTUP

    def verified_switches(self):
        """该模式要用到的命令行开关（必须在 const.VERIFIED_SWITCHES 里，R5）。"""
        return () if self is LaunchMode.A2_STARTUP else (("-r",) if self is LaunchMode.B_RAISE else ())


class BlockedByForeignAE(Exception):
    """R3：已经有别人的 AE 在跑 → 本次不启动、不关任何进程。"""

    def __init__(self, pids):
        self.pids = list(pids)
        super().__init__("已有 AE 在运行：%s" % (self.pids,))


# 本进程真正拉起过的 AE pid（`close_own_ae` 的唯一白名单）。
# 为什么不按「exe 路径匹配进程」判归属：AE 单实例，用户自己开的那只路径**完全相同**，
# 按路径判会把用户的 AE 当自己的关掉（Everything 服务那次就是同款错误的另一个版本）。
_OWN_PIDS = set()

# 时间与睡眠做成模块级引用，测试里直接换掉（否则测 A2 超时要真等 90 秒）
_now = time.time
_sleep = time.sleep


class StartupInjection:
    """A2 注入的落点集合（四个文件：一个在 Startup 目录，三个在 JSX_TARGET_DIR）。

    `run_id` 是一次运行的唯一标识：守卫、alive、done 三个文件里写的都是它，
    Python 侧只认自己这一次的 id —— 上一轮留下的标记绝不采信。
    """

    def __init__(self, run_id, launcher, guard, alive, done, target):
        self.run_id = run_id
        self.launcher = launcher
        self.guard = guard
        self.alive = alive
        self.done = done
        self.target = target          # 真正干活的 relink.jsx（launcher 用 $.evalFile 调它）

    @property
    def token(self):
        """旧名兼容（aehost 早期版本叫 token；语义就是 run_id）。"""
        return self.run_id

    def cleanup(self):
        """撤掉注入物：删 launcher 与守卫（marker 留着，出问题好复盘）。"""
        _remove_quiet(self.launcher)
        _remove_quiet(self.guard)

    def finish(self):
        """**正常收尾**：四个文件全撤（launcher / guard / alive / done）。

        D7：A2 成功后这三个标记原来会一直留在 `C:\\ProgramData\\AEMediaDoctor\\` 里，下一轮虽然
        按 runId 不采信，但对用户是垃圾、对排障是噪音。失败路径**不调这个**——那时标记就是现场
        证据，必须留下。
        """
        _remove_quiet(self.launcher)
        _remove_quiet(self.guard)
        _remove_quiet(self.alive)
        _remove_quiet(self.done)


class LaunchResult:
    """一次启动的结果。`pid` 只可能是我们自己拉起的那个；MANUAL 时是 None。"""

    def __init__(self, mode, exe="", popen=None, fallback_from=None, startup=None, started_at=None):
        self.mode = mode
        self.exe = exe
        self.popen = popen
        self.fallback_from = fallback_from      # 从哪个模式降级来的（A2 超时填 A2_STARTUP）
        self.startup = startup                  # A2 的 StartupInjection（B/手动为 None）
        self.started_at = started_at if started_at is not None else _now()

    @property
    def pid(self):
        proc = self.popen
        pid = getattr(proc, "pid", None)
        return pid if isinstance(pid, int) else None

    def describe(self):
        if self.mode is LaunchMode.MANUAL:
            return "手动模式（本工具没有启动 AE）"
        return "已启动 AE PID %s（%s）" % (self.pid, self.mode.value)


def _remove_quiet(p):
    try:
        os.remove(p)
    except OSError:
        pass


def _write_text(p, text):
    paths.ensure_dir(os.path.dirname(p) or ".")
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _read_text(p):
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def json_quote(s):
    """JS 字符串字面量（反斜杠转正斜杠 + 转义引号），与 jsx.js_str 同规则。"""
    return '"' + str(s).replace("\\", "/").replace('"', '\\"') + '"'


# ---------------------------------------------------------------- 安装与进程

def find_installs():
    """扫 ADOBE_DIRS 找 After Effects <年>\\Support Files\\AfterFX.exe → [(显示名, 路径)]。

    按显示名倒序（2025 排在 2024 前面）。用 normcase 去重：环境变量与写死路径经常
    指向同一个目录，不去重下拉框里会出现两条一模一样的「Adobe After Effects 2025」。
    """
    out, seen = [], set()
    for base in dict.fromkeys(const.ADOBE_DIRS):
        if not base or not os.path.isdir(base):
            continue
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for name in names:
            if not name.lower().startswith("adobe after effects"):
                continue
            exe = os.path.join(base, name, "Support Files", "AfterFX.exe")
            if not os.path.isfile(exe):
                continue
            key = os.path.normcase(os.path.abspath(exe))
            if key in seen:
                continue
            seen.add(key)
            out.append((name, exe))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def procs():
    """当前 AE 进程 [(pid, 有可见窗口)]（透传 winproc；调用点因此不必 import winproc）。"""
    return winproc.ae_procs()


def year_from_exe(ae_exe):
    """从 `...\\Adobe After Effects 2025\\Support Files\\AfterFX.exe` 里抠出 2025。"""
    for part in str(ae_exe).replace("/", "\\").split("\\"):
        if part.lower().startswith("adobe after effects"):
            tail = part[len("adobe after effects"):].strip()
            if tail.isdigit():
                return int(tail)
    return None


# ---------------------------------------------------------------- A2 Startup 注入

def startup_dirs():
    """所有存在的 `<APPDATA>\\Adobe\\After Effects\\<版本>\\Scripts\\Startup`，新版本在前。"""
    base = const.ADOBE_PREFS
    out = []
    try:
        names = sorted(os.listdir(base), reverse=True)
    except OSError:
        return out
    for name in names:
        d = os.path.join(base, name, *const.STARTUP_SUBDIR)
        if os.path.isdir(d):
            out.append((name, d))
    return out


def startup_dir(prefer_year=None):
    """挑一个 Startup 目录：优先与 AE 大版本号匹配的，否则最新那个；没有则空串。

    `prefer_year` 传 **AE 版本年**（2020–2026）；AE 的偏好目录名是「25.3」这种短版本号，
    所以按 `年-2000` 前缀比。匹配不到就退到最新目录 —— 用户只装一个版本时那就是它；
    装多个版本时宁可注入最新那个，也不能因为匹配不上就放弃 A2（降级 B 是有代价的：
    跑完 AE 会自己退）。
    """
    cands = startup_dirs()
    if not cands:
        return ""
    if prefer_year:
        short = str(int(prefer_year) - 2000)
        for name, d in cands:
            if name == short or name.startswith(short + "."):
                return d
    return cands[0][1]


def _launcher_source(guard, alive, done, target, launcher, run_id):
    """生成 Startup 启动器源码（ES3 安全）。五个必备措施都在这里，数据再好也不许删。

    为什么用 `$.evalFile` 而不是把 relink 正文塞进来：正文三百多行、大量顶层 var 与
    try/catch，一旦裹进 `else { }` 里少一个大括号，整个脚本不执行 —— 而报错还落不到
    我们的日志里（AE 的 Startup 脚本报错是**弹模态框**，见 N-3）。用几十行启动器去
    evalFile 同一份文件，等于「无参启动 + 手动跑一次脚本」，语法风险归零。

    与 A2 真机结论（2026-11-15，报告 ae-tools\\A2-启动注入-真机验证-2026-11-15.md）对齐：
      ① **自删是第一条语句** —— 后面哪怕炸了，Startup 里也不会留一个下次还会跑的启动器
      ② 守卫只当「授权令牌」用；**完成标记 `done` 在活干完之后才写**，绝不提前
      ③ 标记里写的是本次 `run_id`，Python 只认自己这一次的 id（上一轮的标记一律无视）
      ④ 注入前 Python 侧先 `node --check` 预检（临时文件后缀必须 .js）
      ⑤ 抛异常时先写 `ERR|...` 到 done 再吞掉 —— **不 rethrow**：未捕获异常会让 AE 弹
         模态框且永不超时，用户只会看到「卡住」

    ⚠ N-1：Startup 通道里 `$.fileName` 是 `~/AppData/...%20...` 这种形态，任何字符串比较
       都会踩空 —— 所以本脚本**完全不碰** `$.fileName`，路径一律由 Python 传进来。
    """
    tpl = """// 拾链 - A2 Startup launcher (auto-generated; deletes itself first).
(function () {
    var runId = %(runid)s;
    var me = new File(%(me)s);
    try { if (me.exists) { me.remove(); } } catch (eSelf) {}
    var go = false;
    try {
        var g = new File(%(guard)s);
        if (g.exists) {
            g.open("r");
            var got = String(g.read()).replace(/\\s+$/, "");
            g.close();
            if (got === runId) { go = true; }
        }
    } catch (eGuard) { go = false; }
    try {
        var d = new File(%(done)s);
        if (d.exists) {
            d.open("r");
            var dg = String(d.read()).replace(/\\s+$/, "");
            d.close();
            if (dg === runId) { go = false; }
        }
    } catch (eDone) {}
    if (!go) { return; }
    try {
        var a = new File(%(alive)s);
        a.open("w"); a.write(runId); a.close();
    } catch (e1) {}
    var errText = "";
    // 干活要推迟到主循环转起来之后（2026-11-15 真机实证 + 协调者判断）：
    // Startup 脚本跑在 AE 主线程、主循环还没转起来的时候，此刻直接 evalFile
    // （脚本里会 app.open 目标工程）会与 AE 自己的「打开项目 / 解析字体」进度框互等，
    // 表现为「marker 有了、JSX 日志 0 字节、10 分钟不结束」。
    // **绝不能在这里同步 sleep 轮询等待**：阻塞会把消息泵按住，进度框更动不了（死锁）。
    // 正解 app.scheduleTask(code, delay, false)：把活交给事件循环。探针实测 25.3 上返回 1、
    // 脚本结束后约 1 秒被调起（探针见 artifacts\\a2-scheduletask-probe.txt）。
    // 万一不可用（返回假或抛异常）就**当场**直接执行 —— 退回原来的行为，不至于什么都不做。
    $.global.__aemdA2Run = function () {
        var errText = "";
        try {
            var t = new File(%(target)s);
            if (t.exists) { $.evalFile(t); }
        } catch (e2) {
            try {
                var emsg = (e2 && e2.message) ? e2.message : String(e2);
                var eline = (e2 && e2.line) ? String(e2.line) : "";
                errText = "ERR|" + emsg + "|" + eline;
            } catch (e2b) { errText = "ERR|unknown"; }
        }
        try {
            var f = new File(%(done)s);
            f.open("w"); f.write(errText !== "" ? errText : runId); f.close();
        } catch (e3) {}
    };
    var scheduled = false;
    try {
        scheduled = app.scheduleTask("__aemdA2Run()", %(delay)d, false);
    } catch (eSched) { scheduled = false; }
    if (!scheduled) {
        try { $.global.__aemdA2Run(); } catch (eDirect) {}
    }
})();
"""
    return tpl % {
        "runid": json_quote(run_id),
        "me": json_quote(launcher),
        "guard": json_quote(guard),
        "done": json_quote(done),
        "alive": json_quote(alive),
        "target": json_quote(target),
        "delay": int(const.A2_SCHEDULE_DELAY_MS),
    }


def _node_check(text):
    """④ 注入前预检：`node --check` 过一遍启动器。返回 True/False/None（没装 node）。

    临时文件后缀**必须是 `.js`**：Node 24 对 `.jsx` 会按 ESM 解析并直接报
    `ERR_UNKNOWN_FILE_EXTENSION`/format 错，与脚本内容无关（P1 阶段被这个坑过一次）。
    没装 node 时返回 None —— 这层是「能检就检」，不能因为没装 node 就不走 A2。
    """
    node = shutil.which("node")
    if not node:
        return None
    paths.ensure_dir(const.JSX_TARGET_DIR)
    tmp = os.path.join(const.JSX_TARGET_DIR, "_aemd_launcher_check.js")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        r = subprocess.run([node, "--check", tmp], capture_output=True, timeout=120)
        if r.returncode != 0:
            _LAST_CHECK_ERROR["err"] = (r.stderr or b"").decode("utf-8", "replace")[:800]
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError) as e:
        _LAST_CHECK_ERROR["err"] = str(e)
        return False
    finally:
        _remove_quiet(tmp)


_LAST_CHECK_ERROR = {"err": ""}


def prepare_startup(jsx_path, run_id, prefer_year=None, log=None):
    """A2 第一步：先写守卫（授权令牌）、再放启动器。返回 `StartupInjection` 或 `None`。

    **顺序有讲究**：守卫必须早于启动器落盘。反过来的话 AE 可能先跑到脚本、看不到守卫，
    于是按「无守卫不跑」自删，本次 A2 白跑一场。反过来「先写守卫、脚本还没有」是安全的
    （守卫就是个 id 文件，没人会误读它）。

    返回 None 表示「本机不具备 A2 条件」：没有 Startup 目录 / 落点写不进去 /
    `node --check` 预检没过。调用方据此降级 B，**不当错误**。

    **ASCII 门只约束我们自己写的落点**（`target` 与 `marker_dir`：这两条路径是写进 JSX
    字符串、由 `$.evalFile` 去读的，上一代的正确经验）；**Startup 目录本身不做 ASCII 要求**
    —— A2 通道里那个路径从不经过命令行参数，文件是 AE 自己扫目录发现的。2026-11-15 真机
    验收 D1 就是这条错判把 A2 挡在门外：中文用户名下 `...\\Scripts\\Startup` 里的脚本被执行
    了三次 HIT，而我们却在这里 `return None`。
    """
    emit = log or (lambda s, tag=None: None)
    if not jsx_path:
        return None
    target = os.path.abspath(str(jsx_path))
    marker_dir = const.JSX_TARGET_DIR
    if not paths.is_ascii_path(os.path.dirname(target)) or not paths.is_ascii_path(marker_dir):
        emit("A2      我们自己的落点不是纯 ASCII（%s），放弃注入"
             % (os.path.dirname(target) if not paths.is_ascii_path(os.path.dirname(target))
                else marker_dir), "dim")
        return None
    sd = startup_dir(prefer_year)
    if not sd:
        emit("A2      找不到 AE 的 Startup 目录（%s\\<版本>\\Scripts\\Startup 不存在），"
             "放弃注入" % const.ADOBE_PREFS, "dim")
        return None
    run_id = str(run_id)
    guard = os.path.join(marker_dir, const.A2_GUARD)
    alive = os.path.join(marker_dir, const.A2_ALIVE)
    done = os.path.join(marker_dir, const.A2_DONE)
    launcher = os.path.join(sd, "_aemd_%s.jsx" % run_id)

    src = _launcher_source(guard, alive, done, target, launcher, run_id)
    chk = _node_check(src)
    if chk is False:
        emit("A2      启动器没通过 node --check 预检，放弃注入：%s"
             % (_LAST_CHECK_ERROR["err"] or "")[:200], "warn")
        return None

    try:
        _write_text(guard, run_id + const.A2_GUARD_SEP)
        # 上一轮的标记必须清掉：alive 留着会被当成本轮已执行；done 留着会让本轮不执行
        _remove_quiet(alive)
        _remove_quiet(done)
        _write_text(launcher, src)
    except OSError:
        _remove_quiet(guard)
        return None
    return StartupInjection(run_id, launcher, guard, alive, done, target)


def clear_markers():
    """清掉上一轮的 guard/alive/done（每次运行开始时调，别读到上一轮的 run_id）。"""
    for name in (const.A2_GUARD, const.A2_ALIVE, const.A2_DONE):
        _remove_quiet(os.path.join(const.JSX_TARGET_DIR, name))


#: hwnd -> 已按次数。**按一次不等于按掉了**：自绘对话框可能整条忽略伪造按键，
#: 所以允许「还在就再按一次」，但最多两次，别变成无限敲回车。
_PRESS_TRIES = {}
_PRESS_MAX_PER_WINDOW = 2
_PRESS_TRIES_CAP = 64


def _tries(hwnd):
    if len(_PRESS_TRIES) > _PRESS_TRIES_CAP:
        _PRESS_TRIES.clear()
    return _PRESS_TRIES.get(hwnd, 0)


def _bump(hwnd):
    _PRESS_TRIES[hwnd] = _PRESS_TRIES.get(hwnd, 0) + 1


def _mark_done(hwnd):
    """这个窗口已经走了 —— 记成「按到头」，以后不再碰它（别对着空气敲回车）。"""
    _PRESS_TRIES[hwnd] = _PRESS_MAX_PER_WINDOW


def press_startup_prompts(pid, seen=None, log=None):
    """把 AE 启动期的「崩溃修复选项」按掉 —— 返回本次按了几下。

    只在**看到标题为空的 #32770** 时发一次回车（默认焦点就在「继续」）。同一个窗口
    只按一次（`seen` 记住 hwnd），总共最多 `const.A2_ENTER_MAX` 次 —— 按不动就认输，
    交给外侧的超时与诊断，不在这里死循环。

    A2 与 -r 两条启动路径都调它：崩溃修复提示与启动模式无关，谁启动都会遇到。
    """
    emit = log or (lambda s, tag=None: None)
    if not pid:
        return 0
    booked = seen if seen is not None else set()
    if len(booked) >= const.A2_ENTER_MAX:
        return 0
    try:
        cands = [w for w in winproc.startup_dialogs(pid) if _tries(w["hwnd"]) < _PRESS_MAX_PER_WINDOW]
    except Exception:
        return 0
    n = 0
    for w in cands[:1]:
        hwnd = w["hwnd"]
        attempt = _tries(hwnd) + 1
        _bump(hwnd)
        booked.add(hwnd)
        if not winproc.press_enter(hwnd):
            continue
        n += 1
        # 按完立刻复查：还在就明说「下一拍再试」——1.1.4 起真实输入优先，
        # 自绘框吞掉伪造按键这种情况才有救（2026-11-16 真机就是这么卡住的）。
        gone = not winproc.still_open(hwnd)
        if gone:
            _mark_done(hwnd)
        if not gone:
            emit("A2      AE 弹了「崩溃修复选项」，已按「继续」但它还在（第 %d 次）——"
                 "若下一拍仍在，请手动点一次「继续」（别点安全模式/重置首选项）" % attempt, "warn")
        else:
            emit("A2      AE 弹了「崩溃修复选项」（上次没正常退出），已点「继续」让它继续启动",
                 "warn")
    return n


def dismiss_progress_dialogs(pid, seen=None, log=None):
    """按掉 AE 自己的启动期进度框（「解析字体」「打开项目」），返回按掉几个。

    为什么需要：这些框是 AE 自己的模态进度窗，运行在事件循环之外，
    我们把活排进 app.scheduleTask 同样排不进去 —— 真机上脚本等了 616 秒没动，
    对框按一次回车后 4 秒内就跑完（日志一次写出 20 KB）。
    只认 const.STARTUP_PROGRESS_TITLES 的标题；同一 hwnd 只按一次；有上限。
    """
    if not pid:
        return 0
    seen = seen if seen is not None else set()
    n = 0
    for w in winproc.progress_dialogs(pid, const.STARTUP_PROGRESS_TITLES):
        hwnd = w.get("hwnd")
        if not hwnd or _tries(hwnd) >= _PRESS_MAX_PER_WINDOW:
            continue
        if len(seen) >= const.A2_PROGRESS_ENTER_MAX:
            break
        texts = winproc.dialog_child_texts(hwnd)
        _bump(hwnd)
        winproc.press_enter(hwnd)
        seen.add(hwnd)
        if not winproc.still_open(hwnd):
            _mark_done(hwnd)
        n += 1
        if log is not None:
            extra = ("（框内文字：%s）" % " / ".join(texts)) if texts else ""
            log("A2      AE 卡在「%s」进度框上，已按回车让它过去%s"
                % (w.get("title") or "", extra), "dim")
    return n


def capture_hung(pid, out_dir="", ae_exe="", log=None):
    """把卡死瞬间的证据写成一个 .txt，返回路径；任何失败返回空串（**绝不抛**）。

    为什么单独落盘而不是只写日志：日志是逐行的纯文本，塞不下窗口清单和插件清单；
    而且用户发日志时爱截断。单独一个文件，下次复现直接"就发这个"，不用教。
    """
    emit = log or (lambda s, tag=None: None)
    try:
        from . import diag
        text = diag.hung_report(pid, ae_exe=ae_exe)
        d = out_dir or paths.log_dir()
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        fpath = os.path.join(d, "hung-%s.txt" % time.strftime("%Y%m%d-%H%M%S"))
        with io.open(fpath, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return fpath
    except Exception as e:
        try:
            emit("DIAG    卡死证据没写成（%s: %s）—— 不影响本次运行"
                 % (type(e).__name__, e), "dim")
        except Exception:
            pass
        return ""


def dismiss_missing_dialogs(pid, seen=None, log=None):
    """按掉「打开工程时 AE 列的缺素材提示框」，返回按掉几个。

    为什么需要：这个框会把 `app.open()` 卡住 —— 脚本一行都写不出来，日志里一行 A2 都没有，
    用户看到的就是「点了开始，AE 开着但什么都不动」，最后等超时。

    三道安全闸（比「崩溃修复选项」更严，因为这里点错代价更大）：
      ① 必须是**有标题**的 `#32770`，且标题/子控件文字命中缺素材关键词（`winproc.missing_dialogs`）；
      ② 框里出现「取消 / 搜索 / 定位 / 浏览 / 替换 / 重置」任一，**一个键都不按**
         （取消会让工程半开，搜索会再弹一个文件浏览器，把整条启动链堵死）；
      ③ 同一 hwnd 只按一次，总量 ≤ `const.A2_MISSING_ENTER_MAX`。

    按键方式沿用「崩溃修复选项」那条线：发一次回车（默认键是确定/继续，不是搜索）。
    每次都会把框内文字写进日志 —— 真实标题与按钮文字就是这样攒出来的，下一版用它收紧判据。
    """
    emit = log or (lambda s, tag=None: None)
    if not pid:
        return 0
    booked = seen if seen is not None else set()
    if len(booked) >= const.A2_MISSING_ENTER_MAX:
        return 0
    try:
        cands = [w for w in winproc.missing_dialogs(pid) if w["hwnd"] not in booked]
    except Exception:
        return 0
    n = 0
    for w in cands:
        hwnd = w.get("hwnd")
        if len(booked) >= const.A2_MISSING_ENTER_MAX:
            break
        booked.add(hwnd)
        texts = winproc.dialog_child_texts(hwnd)
        if winproc.dialog_has_forbidden(w):
            emit("A2      检测到「%s」提示框，但里面有搜索/定位/取消类按钮 —— 不替用户点，请手动处理：%s"
                 % (w.get("title") or "缺素材", " / ".join(texts) or "（无文字）"), "warn")
            continue
        if winproc.press_enter(hwnd):
            n += 1
            if not winproc.still_open(hwnd):
                _mark_done(hwnd)
            extra = ("（框内文字：%s）" % " / ".join(texts)) if texts else ""
            emit("A2      AE 弹了缺素材提示框「%s」，已按回车继续（缺的素材随后照常重连）%s"
                 % (w.get("title") or "", extra), "warn")
    return n


def wait_script_alive(deadline_s, run_id=None, popen=None, log=None):
    """A2 第二步：等 alive 标记出现（= 脚本真的开始执行了）。超时返回 False。

    期间同时盯四件事：
      ① marker 出现没有（只认本轮 run_id，上一轮留下的标记一律无视）
      ② 我们自己拉起的 AE 退了没有（退了就别等满 90 秒，直接判 A2 失败去降级）
      ③ **模态框**：脚本解析失败/抛未捕获异常时 AE 会弹框且永不超时，到了
         `A2_MODAL_CHECK_AT_S` 的时点主动把这些对话框点掉（N-3）。只对对话框发
         WM_CLOSE，绝不对主窗口发。
      ④ **崩溃修复提示**（真机回归才发现的拦路虎）：上次 AE 没正常退出（崩溃、
         被关掉、被强杀）后，下一次启动会先弹一个标题为空的 `#32770`
         「崩溃修复选项」，不点它 AE 永远不出主窗口、Startup 脚本永远不执行。
         这里对「标题为空的 #32770」发一次回车 = 点默认的「继续」；
         **绝不**去点「以安全模式启动」「重置首选项」（那会改用户的 AE 环境）。
    """
    emit = log or (lambda s, tag=None: None)
    t0 = _now()
    deadline_s = float(deadline_s)
    checks = sorted(float(x) for x in const.A2_MODAL_CHECK_AT_S)
    done_checks = set()
    handled_dialogs = set()
    alive = os.path.join(const.JSX_TARGET_DIR, const.A2_ALIVE)
    pid = getattr(popen, "pid", None) if popen is not None else None
    while True:
        # 冷启动（还没出主窗口）按 COLD_START 口径放行；出窗口后回到常规闸门
        if pid and winproc.is_alive(pid) and not winproc.has_main_window(pid):
            limit = max(deadline_s, const.A2_COLD_START_TIMEOUT_S)
        else:
            limit = deadline_s
        if _now() - t0 >= limit:
            break
        got = _read_text(alive).strip()
        if got and (run_id is None or got == str(run_id)):
            return True
        elapsed = _now() - t0
        # ④ 崩溃修复提示：标题为空的 #32770，按一次回车（= 点「继续」）
        press_startup_prompts(pid, handled_dialogs, emit)
        for at in checks:
            if elapsed >= at and at not in done_checks:
                done_checks.add(at)
                pid = getattr(popen, "pid", None) if popen is not None else None
                if pid:
                    try:
                        n = winproc.close_modal_dialogs(pid)
                    except Exception:
                        n = 0
                    if n:
                        emit("A2      %ds 时发现 %d 个模态框挡在前面，已点掉（脚本可能已抛错）"
                             % (int(at), n), "warn")
        if popen is not None:
            try:
                if popen.poll() is not None:
                    return False
            except Exception:
                pass
        _sleep(const.A2_POLL_INTERVAL_S)
    return False


# ---------------------------------------------------------------- 启动与关闭

def _launch(args, cwd=None):
    """真正起进程的**唯一**入口（测试把它换掉就不会真启动 AE）。"""
    return subprocess.Popen(list(args), cwd=cwd or None, creationflags=const.NO_WINDOW)


def _wait_vacuum(log=None, max_s=None, grace_s=None):
    """D6：等进程列表变空、再等一小会儿 —— 单实例程序会把参数转给还没退干净的旧进程。"""
    max_s = const.AE_RESTART_GRACE_MAX_S if max_s is None else max_s
    grace_s = const.AE_RESTART_GRACE_S if grace_s is None else grace_s
    t0 = _now()
    while _now() - t0 < max_s:
        if not procs():
            break
        _sleep(0.2)
    _sleep(grace_s)


def ensure_ready(installs, log=None, force_mode=None, jsx_path=None, token=None):
    """决定并执行启动方式 → `(LaunchMode, LaunchResult)`；别人的 AE 在跑就抛 BlockedByForeignAE。

    降级顺序（R4）：A2 Startup → B `-r` → 手写脚本给用户。`force_mode` 可以钉死某一级
    （测试与排障用）。A2 的判据只认**alive 标记**：脚本真的开始跑了才算 A2 成立；
    仅仅「AE 起来了」不算 —— 否则一次白启动会被记成 A2 成功，用户下次照样踩同一个坑。
    """
    emit = log or (lambda s, tag=None: None)

    running = procs()
    if running:
        raise BlockedByForeignAE([p for p, _w in running])

    ae_exe = ""
    if isinstance(installs, str):
        ae_exe = installs
    elif installs:
        first = installs[0]
        if isinstance(first, (tuple, list)) and len(first) > 1:
            ae_exe = first[1]
        else:
            ae_exe = str(first)
    year = year_from_exe(ae_exe) if ae_exe else None

    mode = force_mode
    if mode is None:
        if jsx_path and startup_dir(year):
            mode = LaunchMode.A2_STARTUP
        elif jsx_path:
            mode = LaunchMode.B_RAISE
        else:
            mode = LaunchMode.MANUAL

    fallback_from = None
    if mode is LaunchMode.A2_STARTUP:
        if not jsx_path:
            mode = LaunchMode.MANUAL
        else:
            inj = prepare_startup(jsx_path, token or "a2", year, log=emit)
            if inj is not None:
                t_launch = _now()          # D6：AE 进程创建时刻（TIME 行的唯一正确起点）
                proc = _launch([ae_exe], cwd=os.path.dirname(ae_exe))
                _OWN_PIDS.add(proc.pid)
                emit("A2      已把启动脚本放进 Startup，无参数启动 AE（PID %s）；"
                     "判据只有「marker 有没有出现」，不看启动用了多久" % proc.pid)
                if wait_script_alive(const.A2_MARKER_TIMEOUT_S, inj.run_id, proc, log=emit):
                    emit("A2      脚本已开始执行（alive 标记出现，runId=%s），AE 会留在原地"
                         % inj.run_id)
                    emit("TIME    AE 启动→脚本开始  +%.1fs（按 AE 进程创建时刻算）"
                         % (_now() - t_launch), "dim")
                    return LaunchMode.A2_STARTUP, LaunchResult(
                        LaunchMode.A2_STARTUP, ae_exe, proc, None, inj)
                emit("FALLBACK A2 等 %d 秒没看到脚本执行标记，降级到 -r"
                     % const.A2_MARKER_TIMEOUT_S, "warn")
                inj.cleanup()
                # force：这只 AE 是我们自己拉起来的、而且已经卡住，必须真的关掉，
                # 否则下一句 -r 会被单实例转发进它，用户看到「什么也没发生」。
                close_own_ae(proc.pid, "A2 超时未执行脚本", log, force=True)
                fallback_from = LaunchMode.A2_STARTUP
            else:
                emit("FALLBACK A2 没准备好（原因见上一行：找不到 Startup 目录 / "
                     "我们自己的落点不是纯 ASCII / node 预检没过），降级到 -r", "warn")
                fallback_from = LaunchMode.A2_STARTUP
            mode = LaunchMode.B_RAISE

    if mode is LaunchMode.B_RAISE:
        if not jsx_path:
            raise ValueError("B 模式需要 jsx_path")
        proc = _launch([ae_exe, "-r", jsx_path], cwd=os.path.dirname(ae_exe))
        _OWN_PIDS.add(proc.pid)
        emit("LAUNCH  已用 -r 启动 AE（PID %s）；注意：脚本跑完 AE 会自己退出" % proc.pid)
        return LaunchMode.B_RAISE, LaunchResult(
            LaunchMode.B_RAISE, ae_exe, proc, fallback_from)

    emit("MANUAL  没有启动 AE（按手动模式生成脚本给用户）")
    return LaunchMode.MANUAL, LaunchResult(LaunchMode.MANUAL, ae_exe, None, fallback_from)


def close_own_ae(pid, reason, log=None, wait=8, force=False):
    """**全库唯一的关 AE 入口**（R1/R2/§8）。

    只关自己拉起的那个 pid：不在 `_OWN_PIDS` 里的一律拒绝（写 SKIP 行、返回 False）。
    一代那四处散落的调用点 + 缺省 True，就是这么把用户自己的 AE 关掉的。
    """
    emit = log or (lambda s, tag=None: None)
    if isinstance(pid, (tuple, list)):
        pid = pid[0] if pid else None
    if pid is None or pid not in _OWN_PIDS:
        emit("SKIP     检测到 AE PID %s（不是本次拉起的），不关闭" % pid, "warn")
        return False
    try:
        ok = (winproc.kill_tree(pid, wait=wait) if force else winproc.terminate_tree(pid, wait=wait))
    except Exception as e:
        emit("CLOSE    关闭 AE PID %s 失败：%s" % (pid, e), "bad")
        return False
    if ok:
        _OWN_PIDS.discard(pid)
        emit("CLOSE    已关掉 AE PID %s（原因：%s）" % (pid, reason or "未注明"), "dim")
        _wait_vacuum(log)
        return True
    emit("CLOSE    关闭 AE PID %s 未确认（进程可能已退出）" % pid, "warn")
    return False


def forget(pid):
    """测试/排障用：把一个 pid 从白名单摘掉（不关进程）。"""
    _OWN_PIDS.discard(pid)