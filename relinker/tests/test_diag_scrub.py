# -*- coding: utf-8 -*-
"""tests/test_diag_scrub.py —— 诊断包脱敏（1.1.5）。

为什么单独立一条：诊断包是"发给别人"的东西，里面原本带着本机用户名、机器名和 SID
（2026-11-16 实测：Windows 事件原文里有 `DESKTOP-XXXXXXX\\李` 与 `S-1-5-21-...-1001`，
日志里还有一堆 `C:\\Users\\李\\...`）。这类泄露不会让软件崩，只会让人不敢把包发出去。

覆盖：
  A 机器名\\用户名 -> 占位符
  B 用户目录路径 -> 占位符（保留盘符与目录结构）
  C SID -> 占位符
  D 事件原文里那种 "User Name: ..." -> 占位符
  E 无关内容（程序目录、正常中文）一个字都不许动
  F 打包落盘的那份确实被处理过（真打一个 zip 打开看）

跑法：`python -m tests.test_diag_scrub`（退出码 0 = 全过）。
"""

import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aemd import console  # noqa: E402

console.harden()

from aemd import const, diag  # noqa: E402

FAIL = []
OK = []

MACHINE_USER = "DESKTOP-7NDPQ5U" + "\\" + "李"
USER_PATH = "C:" + "\\" + "Users" + "\\" + "李" + "\\AppData\\Roaming"
SID = "S-1-5-21-3380781882-1234-5678-1001"


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


@check("A 机器名+用户名 -> 占位符（不留下任何用户名残片）")
def _():
    out = diag.scrub("来自 " + MACHINE_USER + " 的登录")
    need("李" not in out, "用户名没被替换掉：%r" % (out,))
    need("DESKTOP-****" in out, "机器名没打码：%r" % (out,))


@check("B 用户目录路径 -> 占位符，盘符与后面的目录结构保留")
def _():
    out = diag.scrub(USER_PATH + "\\AE素材医生\\logs\\a.log")
    need("李" not in out, "路径里的用户名漏了：%r" % (out,))
    need(out.startswith("C:\\Users\\<user>"), "结构应当保留成 C:\\Users\\<user>\\...：%r" % (out,))
    need("AE素材医生" in out, "不该把产品目录也抹掉：%r" % (out,))


@check("C SID -> 占位符")
def _():
    out = diag.scrub("SID " + SID + " 出现在事件里")
    need("1001" not in out, "SID 尾部没打码：%r" % (out,))
    need("S-1-5-21-**" in out, "SID 没被替换：%r" % (out,))


@check("D 事件原文的 User Name 行 -> 占位符")
def _():
    out = diag.scrub("User Name: " + MACHINE_USER)
    need("李" not in out, "User Name 后面的值漏了：%r" % (out,))


@check("E 无关内容一个字都不动（避免脱敏把证据也一起脱掉）")
def _():
    keep = ["C:\\Program Files\\Adobe\\Adobe After Effects 2025",
            "接上 21 条，还缺 11 条", "D:\\ae\\别人工程\\again (1)\\again.ahead"]
    for k in keep:
        need(diag.scrub(k) == k, "不该改的内容被改了：%r -> %r" % (k, diag.scrub(k)))


@check("F 真打一个诊断包：zip 里找不到用户名 / SID")
def _():
    import tempfile
    tmp = tempfile.mkdtemp(prefix="aemd_diag_")
    real_home, real_logs = const.APP_HOME, const.LOG_DIR
    try:
        const.APP_HOME = tmp
        const.LOG_DIR = os.path.join(tmp, "logs")
        os.makedirs(const.LOG_DIR, exist_ok=True)
        # 造一份"带用户名"的日志，逼它必须走脱敏
        with io.open(os.path.join(const.LOG_DIR, "probe.log"), "w", encoding="utf-8") as fh:
            fh.write("OPEN " + USER_PATH + "\\again.aep\n")
            fh.write("NOTE " + MACHINE_USER + " / " + SID + "\n")
        p = diag.write_zip(dest_dir=tmp)
        need(p and os.path.isfile(p), "诊断包没生成：%r" % (p,))
        with zipfile.ZipFile(p) as z:
            names = z.namelist()
            blob = ""
            for n in names:
                if n.endswith((".json", ".txt", ".log")):
                    blob += z.read(n).decode("utf-8", "replace")
        need(any(n.endswith("probe.log") for n in names), "构造的日志没进包：%r" % (names,))
        need("李" not in blob, "zip 里仍有用户名（%d 字节）" % len(blob))
        need(SID not in blob, "zip 里仍有完整 SID（说明 collect 之后没走脱敏）")
        need(MACHINE_USER not in blob, "zip 里仍有 机器名\\用户名 原串")
    finally:
        const.APP_HOME, const.LOG_DIR = real_home, real_logs


def main():
    print("拾链 —— 诊断包脱敏回归（tests/test_diag_scrub.py）")
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
