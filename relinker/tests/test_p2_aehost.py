# -*- coding: utf-8 -*-
"""P2 打桩测试：aehost 的四个分支（不启动真正的 AE、不弹任何窗口）。

跑法（在 media-doctor-v2 目录下）：

    python -m tests.test_p2_aehost

为什么全打桩：本机 AE 正被别的实验占用（A2 真机验证），任何一次真实启动都会毁掉
别人的对照窗口。所以这里只验证**决策与秩序**：

  A. 别人的 AE 在跑 -> BlockedByForeignAE，绝不启动、绝不关进程（R3）
  B. A2 成功：先写守卫、再放启动器；alive 标记出现才算 A2 成立
  C. A2 超时 -> 记 FALLBACK、撤掉注入物、关掉自己那只、改用 `-r` 重来
  D. 本机没有 Startup 目录 -> 直接 `-r`（记 FALLBACK，不当错误）
  E. 没有 jsx -> 手动模式，一个进程都不起
  F. close_own_ae 只认自己拉起的 pid（别人的 AE 写 SKIP 行、返回 False）
  G. wait_script_alive 的三种判据（token 对上 / 对不上 / AE 早退）
  H. 启动器源码的守卫与「不重复执行」语义（文本级 + node --check 语法级）
"""

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from aemd import console

console.harden()   # 默认 GBK 控制台下也不许因为符号打不出来而崩

from aemd import aehost, const, paths, winproc

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


# ----------------------------------------------------------------- 打桩工具

class FakeProc(object):
    """假的 Popen：真启动被禁，只记参数；poll() 可以指定退出码。"""

    def __init__(self, pid, exit_code=None):
        self.pid = pid
        self._exit = exit_code
        self.killed = False

    def poll(self):
        return self._exit


class Sandbox(object):
    """把「会碰真实文件系统 / 真实进程」的几个入口换成临时目录与桩。"""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="aemd_p2_")
        self.marker_dir = os.path.join(self.dir, "ProgramData_AEMediaDoctor")
        self.prefs_dir = os.path.join(self.dir, "AdobePrefs")
        self.startup = os.path.join(self.prefs_dir, "25.3", "Scripts", "Startup")
        paths.ensure_dir(self.marker_dir)
        paths.ensure_dir(self.startup)
        self.launched = []
        self.closed = []
        self.logs = []
        self._saved = {}

    # --- 保存/替换/还原
    def patch(self, obj, name, value):
        self._saved[(id(obj), name)] = (obj, name, getattr(obj, name))
        setattr(obj, name, value)

    def __enter__(self):
        self.patch(const, "JSX_TARGET_DIR", self.marker_dir)
        self.patch(const, "ADOBE_PREFS", self.prefs_dir)
        # 临时目录可能落在中文路径下（C:\Users\李\...），这里只测秩序，不测 ASCII 策略
        self.patch(paths, "is_ascii_path", lambda p: True)

        counter = {"pid": 5000}

        def fake_launch(args, cwd=None):
            counter["pid"] += 1
            proc = FakeProc(counter["pid"])
            self.launched.append((list(args), cwd, proc))
            return proc

        self.patch(aehost, "_launch", fake_launch)
        self.patch(aehost, "_sleep", lambda s: None)
        self.patch(aehost, "procs", lambda: [])

        # D1 之后 prepare_startup 会真的走到 node 预检（Startup 目录不再要求 ASCII，
        # 于是「不满足条件」不再提前返回）。node 预检本身用 subprocess.run —— 它会走
        # subprocess.Popen，撞到下面那道兜底闸。这里把预检打桩成「通过」，预检本身由
        # H 用例真跑 node 覆盖（那一段不装兜底闸）。
        self.patch(aehost, "_node_check", lambda text: True)

        # 兜底闸：万一某个用例漏打桩，真起进程/真杀进程就当场炸，不许静默发生。
        # 2026-11-15 的事故就是这么来的（测试往真实 Startup 写启动器、还起了 AE）。
        def no_popen(*_a, **_k):
            raise AssertionError("测试里出现了真 subprocess.Popen：打桩漏了一个入口")

        self.patch(subprocess, "Popen", no_popen)
        self.patch(winproc, "terminate_tree", lambda pid, wait=0: True)
        return self

    def __exit__(self, *exc):
        for (obj, name, old) in self._saved.values():
            setattr(obj, name, old)
        self._saved.clear()
        shutil.rmtree(self.dir, ignore_errors=True)
        return False

    def log(self, msg, tag=None):
        self.logs.append(str(msg))

    def fake_close(self, pid, reason, log=None, wait=8, **kw):
        # force=... 是 D1 之后新加的：A2 超时改用 kill_tree 强关，签名要能吃下
        self.force_kw = kw
        self.closed.append((pid, reason))
        return True


EXE_2025 = r"C:\Program Files\Adobe\Adobe After Effects 2025\Support Files\AfterFX.exe"
INSTALLS = [("Adobe After Effects 2025", EXE_2025)]


# ----------------------------------------------------------------- A. 别人有 AE

@case("A. 已有别人的 AE -> BlockedByForeignAE（不启动、不关进程）")
def _():
    with Sandbox() as sb:
        sb.patch(aehost, "procs", lambda: [(4321, True)])
        raised = None
        try:
            aehost.ensure_ready(INSTALLS, log=sb.log, jsx_path=os.path.join(sb.dir, "relink.jsx"))
        except aehost.BlockedByForeignAE as e:
            raised = e
        need(raised is not None, "没抛 BlockedByForeignAE")
        need(raised.pids == [4321], "pids 不对：%r" % (raised.pids,))
        need(sb.launched == [], "居然启动了 AE：%r" % ([x[0] for x in sb.launched],))
        need(sb.closed == [], "居然关了进程")


# ----------------------------------------------------------------- B. A2 成功

@case("B. A2 成功：先写守卫再放启动器，alive 出现才算成立")
def _():
    with Sandbox() as sb:
        order = []
        real_write = aehost._write_text

        def spy_write(p, text):
            order.append(os.path.basename(p))
            return real_write(p, text)

        sb.patch(aehost, "_write_text", spy_write)
        sb.patch(aehost, "wait_script_alive", lambda *a, **k: True)
        jsx = os.path.join(sb.dir, "relink.jsx")
        with open(jsx, "w", encoding="utf-8") as fh:
            fh.write("// relink\n")

        mode, res = aehost.ensure_ready(INSTALLS, log=sb.log, jsx_path=jsx, token="tok1")

        need(mode is aehost.LaunchMode.A2_STARTUP, "mode=%r" % (mode,))
        need(res.fallback_from is None, "不该有 fallback")
        need(res.pid == 5001, "pid=%r" % (res.pid,))
        need(sb.launched[0][0] == [EXE_2025], "A2 必须无参数启动，实际 %r" % (sb.launched[0][0],))
        need(order == [const.A2_GUARD, "_aemd_tok1.jsx"], "落盘顺序不对：%r" % (order,))
        need(os.path.isfile(os.path.join(sb.marker_dir, const.A2_GUARD)), "守卫没落盘")
        need(os.path.isfile(res.startup.launcher), "启动器没落盘")
        need(res.startup.target == os.path.abspath(jsx), "target 不对：%r" % (res.startup.target,))
        need(any("A2" in m for m in sb.logs), "没有 A2 日志：%r" % (sb.logs,))


# ----------------------------------------------------------------- C. A2 超时降级

@case("C. A2 超时 -> FALLBACK + 撤注入物 + 关自己那只 + 改 -r")
def _():
    with Sandbox() as sb:
        sb.patch(aehost, "wait_script_alive", lambda *a, **k: False)
        sb.patch(aehost, "close_own_ae", sb.fake_close)
        jsx = os.path.join(sb.dir, "relink.jsx")

        mode, res = aehost.ensure_ready(INSTALLS, log=sb.log, jsx_path=jsx, token="tok2")

        need(mode is aehost.LaunchMode.B_RAISE, "mode=%r" % (mode,))
        need(res.fallback_from is aehost.LaunchMode.A2_STARTUP, "fallback_from=%r" % (res.fallback_from,))
        need(sb.closed == [(5001, "A2 超时未执行脚本")], "没关 A2 那只：%r" % (sb.closed,))
        need(not os.path.isfile(os.path.join(sb.marker_dir, const.A2_GUARD)), "守卫没撤掉")
        need(len(sb.launched) == 2, "应该起两次（A2 + -r），实际 %d" % len(sb.launched))
        need(sb.launched[1][0] == [EXE_2025, "-r", jsx], "B 模式参数不对：%r" % (sb.launched[1][0],))
        need(res.pid == 5002, "pid 应是第二只：%r" % (res.pid,))
        need(any("FALLBACK" in m for m in sb.logs), "没有 FALLBACK 日志：%r" % (sb.logs,))


# ----------------------------------------------------------------- D. 没 Startup 目录

@case("D. 没有 Startup 目录 -> 直接 -r（选路阶段就没打算 A2，不算错误）")
def _():
    with Sandbox() as sb:
        shutil.rmtree(sb.prefs_dir, ignore_errors=True)

        def boom(*a, **k):
            raise AssertionError("不该走到 A2 的 alive 轮询")

        sb.patch(aehost, "wait_script_alive", boom)
        jsx = os.path.join(sb.dir, "relink.jsx")
        mode, res = aehost.ensure_ready(INSTALLS, log=sb.log, jsx_path=jsx, token="tok3")

        need(mode is aehost.LaunchMode.B_RAISE, "mode=%r" % (mode,))
        # 选路阶段 `startup_dir()` 就是空的，所以压根没打算走 A2 —— fallback_from 为空才对；
        # 只有「打算走 A2、真去注入了、结果没成」才记 FALLBACK（那是真降级，见用例 C）。
        need(res.fallback_from is None, "没打算 A2 却记了 fallback：%r" % (res.fallback_from,))
        need(not any("A2" in m for m in sb.logs), "选路阶段不该出现 A2 日志：%r" % (sb.logs,))
        need(len(sb.launched) == 1, "只该起一只，实际 %d" % len(sb.launched))
        need(sb.launched[0][0] == [EXE_2025, "-r", jsx], "参数不对：%r" % (sb.launched[0][0],))


# ----------------------------------------------------------------- E. 手动模式

@case("E. 没有 jsx -> 手动模式，一个进程都不起")
def _():
    with Sandbox() as sb:
        mode, res = aehost.ensure_ready(INSTALLS, log=sb.log)
        need(mode is aehost.LaunchMode.MANUAL, "mode=%r" % (mode,))
        need(res.pid is None, "手动模式不该有 pid")
        need(sb.launched == [], "居然起了进程")
        need(not aehost.LaunchMode.MANUAL.keeps_ae_alive(), "手动模式不该宣称 AE 还活着")


# ----------------------------------------------------------------- F. 关 AE 白名单

@case("F. close_own_ae 只认自己拉起的 pid（别人那只写 SKIP 不关）")
def _():
    with Sandbox() as sb:
        killed = []
        sb.patch(winproc, "terminate_tree", lambda pid, wait=0: killed.append(pid) or True)
        jsx = os.path.join(sb.dir, "relink.jsx")
        sb.patch(aehost, "wait_script_alive", lambda *a, **k: True)
        mode, res = aehost.ensure_ready(INSTALLS, log=sb.log, jsx_path=jsx, token="tok4")
        need(mode is aehost.LaunchMode.A2_STARTUP, "前置不成立")

        need(aehost.close_own_ae(4321, "用户勾选", sb.log) is False, "居然关了别人的 AE")
        need(killed == [], "terminate_tree 被误调：%r" % (killed,))
        need(any("SKIP" in m for m in sb.logs), "没有 SKIP 日志：%r" % (sb.logs,))

        need(aehost.close_own_ae(res.pid, "跑完关闭", sb.log) is True, "自己的那只没关成")
        need(killed == [res.pid], "关错 pid：%r" % (killed,))
        need(any(("CLOSE" in m) and ("跑完关闭" in m) for m in sb.logs), "没有 CLOSE 日志：%r" % (sb.logs,))
        need(aehost.close_own_ae(res.pid, "再关一次", sb.log) is False, "重复关同一只应该被拒")


# ----------------------------------------------------------------- G. marker 轮询

@case("G. wait_script_alive：token 对上 True / 对不上 False / AE 早退 False")
def _():
    with Sandbox() as sb:
        clock = {"t": 1000.0}
        sb.patch(aehost, "_now", lambda: clock["t"])
        sb.patch(aehost, "_sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
        alive = os.path.join(sb.marker_dir, const.A2_ALIVE)

        # 1) 标记一直不出现 -> 到点 False
        need(aehost.wait_script_alive(3.0, "tokX") is False, "空标记应该 False")

        # 2) 标记出现且 token 对上 -> True
        with open(alive, "w", encoding="utf-8") as fh:
            fh.write("tokX\n")
        need(aehost.wait_script_alive(3.0, "tokX") is True, "对上应该 True")

        # 3) token 对不上 -> False（上一轮留下的标记不许当成这一轮）
        need(aehost.wait_script_alive(3.0, "tokY") is False, "token 不符应该 False")

        # 4) AE 早退 -> 立刻 False（不等满 90 秒）
        os.remove(alive)
        clock["t"] = 1000.0
        need(aehost.wait_script_alive(90.0, "tokX", FakeProc(1, exit_code=0)) is False,
             "进程早退应该 False")
        need(clock["t"] - 1000.0 <= 1.0, "早退却等满了：%.1f 秒" % (clock["t"] - 1000.0))


# ----------------------------------------------------------------- H. 启动器源码

@case("H. 启动器：先自删、守卫/完成标记/runId/evalFile 都在，node --check 通过")
def _():
    real_popen = subprocess.Popen        # 本用例要真跑 node --check：先把兜底闸的原件收好
    with Sandbox() as sb:
        sb.patch(subprocess, "Popen", real_popen)
        src = aehost._launcher_source(
            os.path.join(sb.marker_dir, const.A2_GUARD),
            os.path.join(sb.marker_dir, const.A2_ALIVE),
            os.path.join(sb.marker_dir, const.A2_DONE),
            r"C:\ProgramData\AEMediaDoctor\relink.jsx",
            r"C:\ProgramData\AEMediaDoctor\Startup\_aemd_run1.jsx",
            "run1")
        for token in ("var runId = ", "eSelf", "eGuard", "eDone", "e1", "e2", "e3",
                      "$.evalFile", "me.remove()", "go = false", "go = true", "ERR|"):
            need(token in src, "启动器缺 %r" % (token,))
        # ① 自删必须是第一条语句（后面炸了也不留「下次还会跑」的启动器）
        need(src.index("me.remove()") < src.index("eGuard"),
             "自删不在守卫之前：%r" % (src.index("me.remove()"),))
        # ② 守卫先读、done 后读（done 判据要能压过守卫）
        need(src.index("eGuard") < src.index("eDone"), "守卫与 done 的顺序不对")
        # ③ 完成标记在 evalFile 之后写（写早了会把失败记成完成）
        need(src.index("$.evalFile") < src.index('f.open("w")'), "done 标记写早了")
        # ④ runId 全文出现（守卫/标记都带它）
        need(src.count("runId") >= 4, "runId 用得不够：%d" % src.count("runId"))
        # ⑤ 路径转正斜杠（AE 的 File() 只吃正斜杠）
        need(r"C:/ProgramData/AEMediaDoctor/relink.jsx" in src, "路径没转正斜杠")
        need("\\\\" not in src.split("$.evalFile")[1][:200], "evalFile 参数里还有反斜杠")

        # ⑥ 干活必须推迟到主循环之后（2026-11-15 真机：Startup 里直接 app.open 会与
        #    AE 自己的「打开项目/解析字体」进度框互等，10 分钟无日志），且**不许用 $.sleep**
        #    同步等待（会按死消息泵、死锁）。不可用时退回当场直接执行。
        need("app.scheduleTask" in src, "启动器没用 app.scheduleTask 推迟执行")
        need("scheduled = app.scheduleTask" in src, "没有真正调用 app.scheduleTask")
        need(str(int(const.A2_SCHEDULE_DELAY_MS)) in src, "scheduleTask 延迟没跟上常量")
        need("$.sleep" not in src, "启动器里出现了 $.sleep（同步等待会按死消息泵）")
        need(src.count("__aemdA2Run") >= 3, "__aemdA2Run 定义/注册/兜底调用不完整")
        need(src.index("$.global.__aemdA2Run = function") < src.index("scheduled = app.scheduleTask"),
             "先注册全局函数再 scheduleTask，顺序反了")

        node = shutil.which("node")
        if not node:
            return
        tmp = os.path.join(sb.dir, "launcher.js")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(src)
        r = subprocess.run([node, "--check", tmp], capture_output=True, timeout=120)
        need(r.returncode == 0, "node --check 失败：%s" % (r.stderr.decode("utf-8", "replace")[:400],))


@case("I. prepare_startup 清掉上一轮 alive 标记；clear_markers 清三个 marker")
def _():
    with Sandbox() as sb:
        jsx = os.path.join(sb.dir, "relink.jsx")
        alive = os.path.join(sb.marker_dir, const.A2_ALIVE)
        with open(alive, "w", encoding="utf-8") as fh:
            fh.write("old-token")
        inj = aehost.prepare_startup(jsx, "newtok")
        need(inj is not None, "prepare_startup 返回 None")
        need(not os.path.exists(alive), "上一轮 alive 标记没清掉（会把旧结果当这一轮）")

        for name, val in ((const.A2_GUARD, "g"), (const.A2_ALIVE, "a"), (const.A2_DONE, "d")):
            with open(os.path.join(sb.marker_dir, name), "w", encoding="utf-8") as fh:
                fh.write(val)
        aehost.clear_markers()
        left = [n for n in (const.A2_GUARD, const.A2_ALIVE, const.A2_DONE)
                if os.path.exists(os.path.join(sb.marker_dir, n))]
        need(left == [], "没清干净：%r" % (left,))


# ----------------------------------------------------------------- main

def main():
    print("")
    print("P2 打桩测试 —— aehost 四个分支（%d 项）" % (len(PASS) + len(FAIL)))
    print("")
    total = len(PASS) + len(FAIL)
    if FAIL:
        print("FAILED：%d 项通过，%d 项失败" % (len(PASS), len(FAIL)))
        for name, why in FAIL:
            print("  - %s：%s" % (name, why))
        return 1
    print("ALL PASS（%d 项全部通过）" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())