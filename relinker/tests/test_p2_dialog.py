# -*- coding: utf-8 -*-
"""tests.test_p2_dialog —— 「崩溃修复选项」提示框的处理（2026-11-15 真机回归发现的拦路虎）。

事故形状（真机）：AE 上次没正常退出（崩溃 / 被关掉 / 被强杀）后，下一次启动会先弹一个
**标题为空的 `#32770`**：「崩溃修复选项 —— 我们检测到您的上次会话发生崩溃……」
按钮是 以安全模式启动 / 重置首选项 / 管理增效工具 / **继续**（默认焦点在「继续」）。

不点它：AE 永远不出主窗口 → Startup 脚本永远不执行 → marker 永远不出现
→ 我们等满 90 s 判 A2 失败 → 降级 -r → 新实例被单实例转发进那个卡住的实例
→ 再等 240 s → 失败。用户看到的是「点了开始，什么都没发生，等了四分钟报错」。

为什么是「按回车」而不是「找按钮点击」：这个框是 AE 自绘（DroverLord），四个按钮
不是原生子控件，枚举不到 `Button` 子窗口、拿不到 control id —— 只能靠默认焦点 +
回车。而默认焦点就在「继续」上（截图可证），所以回车 = 点「继续」，
**不会**误触「以安全模式启动」「重置首选项」。

覆盖：
  A 认出「标题为空的 #32770」是启动提示，而有标题的 `#32770`（打开项目 / 解析字体）不是
  B 同一个窗口只按一次（按不动不刷屏）
  C 总次数受 const.A2_ENTER_MAX 限制
  D 走到 A2 等待循环里：出现提示 → 按回车 → 记日志 → marker 出现即返回 True
  E -r 路径（runner._poll_starting）也会按，且不重复按
  F 没有提示时不按（不误伤）
"""
import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from aemd import console  # noqa: E402

console.harden()

import aemd.aehost as aehost      # noqa: E402
import aemd.const as const        # noqa: E402
import aemd.runner as runner      # noqa: E402
import aemd.winproc as winproc    # noqa: E402


def dlg(hwnd, title=""):
    return {"hwnd": hwnd, "cls": "#32770", "title": title, "visible": True, "enabled": True}


class Stub(object):
    """把 winproc 的两个函数换成桩，并记下每次按键。"""

    def __init__(self, dialogs):
        self.dialogs = dialogs
        self.pressed = []
        self._orig = (winproc.startup_dialogs, winproc.press_enter)

    def __enter__(self):
        winproc.startup_dialogs = lambda pid: list(self.dialogs)
        winproc.press_enter = lambda hwnd: (self.pressed.append(hwnd), True)[1]
        return self

    def __exit__(self, *exc):
        winproc.startup_dialogs, winproc.press_enter = self._orig
        return False


class StartupDialogTest(unittest.TestCase):

    def test_A_only_untitled_32770_counts(self):
        """A：标题为空的 #32770 才算启动提示。"""
        wins = [
            {"hwnd": 1, "cls": "#32770", "title": "", "visible": True},
            {"hwnd": 2, "cls": "#32770", "title": "打开项目", "visible": True},
            {"hwnd": 3, "cls": "#32770", "title": "解析字体", "visible": True},
            {"hwnd": 4, "cls": "AE_CApplication_25.3", "title": "Adobe After Effects", "visible": True},
            {"hwnd": 5, "cls": "#32770", "title": "   ", "visible": False},
        ]
        orig = winproc.windows_of
        winproc.windows_of = lambda pid: list(wins)
        try:
            got = [w["hwnd"] for w in winproc.startup_dialogs(1234)]
        finally:
            winproc.windows_of = orig
        self.assertEqual(got, [1], "只有标题为空且可见的 #32770 才算；实得 %r" % got)

    def test_B_same_dialog_pressed_once(self):
        """B：同一个窗口只按一次（按不动不许刷屏）。"""
        seen = set()
        logs = []
        with Stub([dlg(0x111)]) as st:
            n1 = aehost.press_startup_prompts(1234, seen, lambda s, tag=None: logs.append(s))
            n2 = aehost.press_startup_prompts(1234, seen, lambda s, tag=None: logs.append(s))
        self.assertEqual((n1, n2), (1, 0), "第二次不该再按")
        self.assertEqual(st.pressed, [0x111])
        self.assertEqual(len(logs), 1, "只该记一条日志，实得 %r" % logs)
        self.assertIn("崩溃修复选项", logs[0])

    def test_C_capped_by_const(self):
        """C：总共最多按 const.A2_ENTER_MAX 次。"""
        seen = set()
        logs = []
        with Stub([dlg(0x201), dlg(0x202), dlg(0x203), dlg(0x204)]) as st:
            pressed = 0
            for _ in range(6):
                pressed += aehost.press_startup_prompts(1234, seen, lambda s, tag=None: logs.append(s))
        self.assertEqual(pressed, const.A2_ENTER_MAX, "应受 A2_ENTER_MAX=%d 限制" % const.A2_ENTER_MAX)
        self.assertEqual(len(st.pressed), const.A2_ENTER_MAX)

    def test_D_pressed_inside_a2_wait_loop(self):
        """D：A2 等待循环里出现提示 → 按回车 → marker 出现 → 返回 True。"""
        logs = []
        state = {"pressed": False}

        class FakeClock(object):
            def __init__(self):
                self.t = 0.0

            def now(self):
                self.t += 0.6
                return self.t

        clk = FakeClock()
        orig_now, orig_sleep = aehost._now, aehost._sleep
        orig_read = aehost._read_text
        aehost._now = clk.now
        aehost._sleep = lambda s: None
        # 按过回车之后 marker 才出现 —— 这正是真机的顺序
        aehost._read_text = lambda p: "rid-1" if state["pressed"] else ""

        class FakePopen(object):
            pid = 1234

            def poll(self):
                return None

        try:
            with Stub([dlg(0x311)]) as st:
                winproc.press_enter = lambda hwnd: (st.pressed.append(hwnd),
                                                    state.update(pressed=True), True)[2]
                ok = aehost.wait_script_alive(30, run_id="rid-1", popen=FakePopen(),
                                              log=lambda s, tag=None: logs.append(s))
        finally:
            aehost._now, aehost._sleep, aehost._read_text = orig_now, orig_sleep, orig_read
        self.assertTrue(ok, "按了回车之后 marker 出现，应返回 True")
        self.assertEqual(st.pressed, [0x311])
        self.assertTrue(any("崩溃修复选项" in s for s in logs), "日志里要说明发生了什么：%r" % logs)

    def test_E_runner_r_path_also_presses(self):
        """E：-r / 手动那条路径（runner.poll() 公共路径）也会按。

        为什么是 poll()：日志文件一被创建（哪怕 0 字节）就算 started，之后不再进
        _poll_starting —— 只放在那里会漏掉「已经 started、脚本其实没跑」的整段
        （2026-11-15 真机第二轮就漏了）。所以按对话框放在 poll() 里。
        """
        run = runner.Run(project=__file__, roots=[HERE])
        run.log = lambda s, tag=None: None
        run.launch = type("L", (), {"pid": 4321})()
        seen = set()
        run._prompt_seen = seen
        run.log_path = os.path.join(HERE, "_no_such_log_for_test.txt")
        with Stub([dlg(0x411)]) as st:
            run.poll()
            run.poll()
        self.assertEqual(st.pressed, [0x411], "第一次按、第二次因为记过 hwnd 不再按")

    def test_F_no_dialog_no_press(self):
        """F：没有提示框就一次都不按（不许误伤）。"""
        with Stub([]) as st:
            n = aehost.press_startup_prompts(1234, set(), None)
        self.assertEqual((n, st.pressed), (0, []))



class CrashDialogClickTest(unittest.TestCase):
    """1.1.9：回车按不动「崩溃修复选项」时，改用鼠标点「继续」。

    为什么加这条：2026-11-17 用户指出 —— 日志写着"已按「继续」"，框其实还在，
    最后是他自己用鼠标点的。这个框是自绘的，PostMessage 会被吞，
    SetForegroundWindow 会被前台锁挡掉；真实鼠标点（先点标题栏激活、再点右下角「继续」）才稳。
    """

    def _stub(self, still_seq, press_ok=True, click_ok=True):
        orig = (winproc.startup_dialogs, winproc.press_enter, winproc.still_open,
                winproc.click_continue)
        pressed, clicked = [], []
        winproc.startup_dialogs = lambda pid: [dlg(0x501)]
        winproc.press_enter = lambda h: (pressed.append(h), press_ok)[1]
        winproc.click_continue = lambda h: (clicked.append(h), click_ok)[1]
        seq = list(still_seq)
        winproc.still_open = lambda h, timeout_ms=900: (seq.pop(0) if seq else True)
        aehost._PRESS_TRIES.clear()
        return orig, pressed, clicked

    def _restore(self, orig):
        (winproc.startup_dialogs, winproc.press_enter, winproc.still_open,
         winproc.click_continue) = orig
        aehost._PRESS_TRIES.clear()

    def test_O_enter_fails_then_click_works(self):
        """O：回车没关掉 → 点一次鼠标 → 关掉了 → 日志说清是"用鼠标点"的。"""
        logs = []
        # still_open 依次：按完回车 True（还在）→ 点完鼠标 False（没了）
        orig, pressed, clicked = self._stub([True, False])
        try:
            n = aehost.press_startup_prompts(1234, set(), lambda s, tag=None: logs.append(s))
        finally:
            self._restore(orig)
        self.assertEqual(n, 1)
        self.assertEqual(pressed, [0x501])
        self.assertEqual(clicked, [0x501], "回车无效时必须补一次鼠标点击")
        self.assertTrue(any("鼠标点「继续」" in s for s in logs), "日志要写明改用了鼠标：%r" % logs)

    def test_P_both_fail_asks_user_once(self):
        """P：两种都按不动 → 明确请用户手点，且同一窗口这一拍只点一次（不刷屏）。"""
        logs = []
        orig, pressed, clicked = self._stub([True, True, True], click_ok=True)
        try:
            aehost.press_startup_prompts(1234, set(), lambda s, tag=None: logs.append(s))
        finally:
            self._restore(orig)
        self.assertEqual(len(clicked), 1, "同一拍不该连点：%r" % (clicked,))
        self.assertTrue(any("请手动点一次" in s for s in logs), "要请用户手点：%r" % logs)


class MissingFootageDialogTest(unittest.TestCase):
    """1.1.1：「打开工程时 AE 列的缺素材提示框」。

    这个框不按，`app.open()` 会一直卡着 —— 脚本一行都写不出来，日志里连 A2 都没有，
    用户看到的就是「点了开始，AE 开着但什么都不动」，最后等超时。
    安全口径：命中关键词才动手、只发回车、框里有搜索/定位/取消就一个键都不按。
    """

    def _stub(self, wins, texts=None, forbidden=None, pressed=None):
        """替掉 missing_dialogs / dialog_child_texts / dialog_has_forbidden / press_enter。"""
        orig = (winproc.missing_dialogs, winproc.dialog_child_texts,
                winproc.dialog_has_forbidden, winproc.press_enter)
        winproc.missing_dialogs = lambda pid: list(wins)
        winproc.dialog_child_texts = lambda hwnd, limit=8: list((texts or {}).get(hwnd, []))
        winproc.dialog_has_forbidden = lambda w: bool((forbidden or {}).get(w["hwnd"], False))
        winproc.press_enter = lambda hwnd: (pressed.append(hwnd), True)[1]
        return orig

    def test_G_keywords_only(self):
        """G：标题/框内文字命中「缺少 / 找不到 / missing」才算；进度框与空标题不算。"""
        wins = [
            {"hwnd": 11, "cls": "#32770", "title": "After Effects", "visible": True},
            {"hwnd": 12, "cls": "#32770", "title": "错误", "visible": True},
            {"hwnd": 13, "cls": "#32770", "title": "打开项目", "visible": True},
            {"hwnd": 14, "cls": "#32770", "title": "", "visible": True},
            {"hwnd": 15, "cls": "#32770", "title": "", "visible": True},
            {"hwnd": 16, "cls": "AE_CApplication_25.3", "title": "Adobe After Effects", "visible": True},
        ]
        texts = {11: ["3 files are missing", "确定"], 12: ["缺少 3 个文件", "确定"],
                 13: ["正在打开…"], 15: ["语法错误"]}
        # 这一条测的是 missing_dialogs 本身的判据，所以**不能**把它 stub 掉，
        # 只替 windows_of（窗口清单）与 dialog_child_texts（框内文字）。
        orig = (winproc.windows_of, winproc.dialog_child_texts)
        winproc.windows_of = lambda pid: list(wins)
        winproc.dialog_child_texts = lambda hwnd, limit=12: list(texts.get(hwnd, []))
        try:
            got = [w["hwnd"] for w in winproc.missing_dialogs(1234)]
        finally:
            winproc.windows_of, winproc.dialog_child_texts = orig
        self.assertIn(11, got, "标题为 After Effects 但框内写着 missing，应当认出来")
        self.assertIn(12, got, "框内写着「缺少 3 个文件」，应当认出来")
        self.assertNotIn(13, got, "「打开项目」进度框不是缺素材框")
        self.assertNotIn(14, got, "空标题归崩溃修复那条线，不在这里抢")
        self.assertNotIn(16, got, "不是 #32770 的一律不认")

    def test_H_forbidden_buttons_never_pressed(self):
        """H：框里有「搜索 / 定位 / 取消」时，一个键都不按，只记一条让人自己处理。"""
        pressed, logs = [], []
        orig = self._stub([{"hwnd": 21, "cls": "#32770", "title": "缺少文件", "visible": True}],
                          {21: ["缺少 2 个文件", "搜索…", "取消"]},
                          forbidden={21: True}, pressed=pressed)
        try:
            n = aehost.dismiss_missing_dialogs(1234, set(), lambda s, tag=None: logs.append(s))
        finally:
            (winproc.missing_dialogs, winproc.dialog_child_texts,
             winproc.dialog_has_forbidden, winproc.press_enter) = orig
        self.assertEqual((n, pressed), (0, []), "有危险按钮时绝不代按")
        self.assertTrue(any("请手动处理" in s for s in logs), "必须留下提示：%r" % logs)

    def test_I_capped_and_once(self):
        """I：同一窗口只按一次，总量受 const.A2_MISSING_ENTER_MAX 限制。"""
        wins = [{"hwnd": 300 + i, "cls": "#32770", "title": "缺少文件", "visible": True}
                for i in range(6)]
        pressed, logs = [], []
        orig = self._stub(wins, pressed=pressed)
        try:
            seen = set()
            total = 0
            for _ in range(6):
                total += aehost.dismiss_missing_dialogs(1234, seen, lambda s, tag=None: logs.append(s))
        finally:
            (winproc.missing_dialogs, winproc.dialog_child_texts,
             winproc.dialog_has_forbidden, winproc.press_enter) = orig
        self.assertEqual(total, const.A2_MISSING_ENTER_MAX,
                         "应受 A2_MISSING_ENTER_MAX=%d 限制" % const.A2_MISSING_ENTER_MAX)
        self.assertEqual(len(pressed), len(set(pressed)), "同一个 hwnd 不许按两次")

    def test_J_runner_poll_also_handles_it(self):
        """J：runner.poll() 公共路径里也会处理（日志一创建就算 started，别只放 _poll_starting）。"""
        pressed = []
        wins = [{"hwnd": 41, "cls": "#32770", "title": "缺少文件", "visible": True}]
        orig = self._stub(wins, pressed=pressed)
        try:
            run = runner.Run(project=__file__, roots=[HERE])
            run.log = lambda s, tag=None: None
            run.launch = type("L", (), {"pid": 4321})()
            run._prompt_seen = set()
            run.log_path = os.path.join(HERE, "_no_such_log_for_test.txt")
            run.poll()
            run.poll()
        finally:
            (winproc.missing_dialogs, winproc.dialog_child_texts,
             winproc.dialog_has_forbidden, winproc.press_enter) = orig
        self.assertEqual(pressed, [41], "第一次按、第二次因为记过 hwnd 不再按")


    def test_K_retry_when_dialog_survives(self):
        """K：自绘框吞掉第一次回车时，下一拍要再按一次；按掉之后不许再敲。

        2026-11-16 真机：崩溃修复框与「打开项目」都是 AE 自绘（DroverLord），
        PostMessage 的伪造按键可能整条被忽略 —— 以前每个 hwnd 只按一次，
        吞一次就永远停在门口。现在允许重试一次，并且「走了」之后立刻收手。
        """
        pressed = []
        state = {"alive": True}

        orig = (winproc.startup_dialogs, winproc.press_enter, winproc.still_open)
        winproc.startup_dialogs = lambda pid: [dlg(0x511)]
        winproc.press_enter = lambda hwnd: (pressed.append(hwnd), True)[1]
        winproc.still_open = lambda hwnd, timeout_ms=900: state["alive"]
        try:
            seen, logs = set(), []
            n1 = aehost.press_startup_prompts(1234, seen, lambda s, tag=None: logs.append(s))
            n2 = aehost.press_startup_prompts(1234, seen, lambda s, tag=None: logs.append(s))
            state["alive"] = False            # 第二下之后它走了
            n3 = aehost.press_startup_prompts(1234, seen, lambda s, tag=None: logs.append(s))
            n4 = aehost.press_startup_prompts(1234, seen, lambda s, tag=None: logs.append(s))
        finally:
            winproc.startup_dialogs, winproc.press_enter, winproc.still_open = orig
        self.assertEqual((n1, n2, n3, n4), (1, 1, 0, 0),
                         "第一次按、还在就再按一次、之后收手；实得 %r" % ((n1, n2, n3, n4),))
        self.assertEqual(len(pressed), 2, "总共只该按两次，实得 %r" % pressed)
        self.assertTrue(any("还在" in s for s in logs), "要如实记一句「按了但还在」：%r" % logs)


class UnknownDialogTest(unittest.TestCase):
    """1.1.7：不认识的模态框用 Esc 关掉，并把标题说出来。

    2026-11-17 真机：AE 打开工程时弹了一个「设置窗口」（自绘、没有原生按钮），
    把主窗口压住，脚本卡在 app.open() 出不来 —— 日志一个字都不写。
    以前程序只说"有窗口挡着"，不说是哪个框，用户批量跑的时候无从处置。
    """

    def _stub(self, wins, texts=None, pressed=None):
        orig = (winproc.windows_of, winproc.dialog_child_texts, winproc.press_escape)
        winproc.windows_of = lambda pid: list(wins)
        winproc.dialog_child_texts = lambda hwnd, limit=12: list((texts or {}).get(hwnd, []))
        winproc.press_escape = lambda hwnd: (pressed.append(hwnd), True)[1]
        return orig

    def test_L_escs_unknown_titled_dialog(self):
        """L：有标题的陌生框 -> Esc 一次，日志里要有那个标题。"""
        pressed, logs = [], []
        wins = [{"hwnd": 71, "cls": "#32770", "title": "设置窗口", "visible": True}]
        orig = self._stub(wins, pressed=pressed)
        try:
            n = aehost.dismiss_unknown_dialogs(1234, set(), lambda s, tag=None: logs.append(s))
        finally:
            winproc.windows_of, winproc.dialog_child_texts, winproc.press_escape = orig
        self.assertEqual((n, pressed), (1, [71]), "应当按一次 Esc")
        self.assertTrue(any("设置窗口" in s for s in logs), "日志必须写出框标题：%r" % logs)
        self.assertTrue(any("Esc" in s for s in logs), "要说明用的是 Esc")

    def test_M_skips_known_dialogs(self):
        """M：空标题（崩溃框）/ 进度框 / 缺素材框 —— 一个都不碰（各有专线）。"""
        pressed = []
        wins = [
            {"hwnd": 81, "cls": "#32770", "title": "", "visible": True},
            {"hwnd": 82, "cls": "#32770", "title": "打开项目", "visible": True},
            {"hwnd": 83, "cls": "#32770", "title": "缺少文件", "visible": True},
            {"hwnd": 84, "cls": "AE_CApplication_25.3", "title": "主窗口", "visible": True},
        ]
        texts = {83: ["3 files are missing"]}
        orig = self._stub(wins, texts, pressed)
        try:
            n = aehost.dismiss_unknown_dialogs(1234, set(), lambda s, tag=None: None)
        finally:
            winproc.windows_of, winproc.dialog_child_texts, winproc.press_escape = orig
        self.assertEqual((n, pressed), (0, []), "认识的框不该在这里被按：%r" % (pressed,))

    def test_N_capped_and_once(self):
        """N：每个 hwnd 只按一次，单轮不超过 UNKNOWN_DIALOG_MAX；下一轮继续清剩下的。

        额度是"每轮"而不是"总共"：AE 真弹了 5 个陌生框时要能一个个清完，
        但同一个框绝不回头再按（否则就是对着空气反复敲 Esc）。
        """
        pressed = []
        wins = [{"hwnd": 90 + i, "cls": "#32770", "title": "怪框%d" % i, "visible": True}
                for i in range(5)]
        orig = self._stub(wins, pressed=pressed)
        try:
            booked = set()
            rounds = [aehost.dismiss_unknown_dialogs(1234, booked, lambda s, tag=None: None)
                      for _ in range(4)]
        finally:
            winproc.windows_of, winproc.dialog_child_texts, winproc.press_escape = orig
        self.assertEqual(rounds[0], aehost.UNKNOWN_DIALOG_MAX, "第一轮应当按到上限")
        self.assertEqual(rounds[-1], 0, "清完之后不许再来一遍")
        self.assertEqual(len(pressed), 5, "5 个框一共按 5 次，实得 %r" % (pressed,))
        self.assertEqual(len(set(pressed)), 5, "同一个 hwnd 不许按两次：%r" % (pressed,))


if __name__ == "__main__":
    unittest.main(verbosity=2)