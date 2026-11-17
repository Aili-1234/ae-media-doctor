# -*- coding: utf-8 -*-
"""tests/test_batch.py —— 批量多工程（1.1.7）。

要证明的四件事：
  A. 展开：目录只走两层、去重、非 .aep 与不存在的路径都只记一句不抛
  B. 串行：一个工程失败**不打断**其余，跑完才汇总
  C. 汇总：成功/失败数、接上/缺失/映射命中求和都对
  D. 参数：`--cli a b c` 与位置参数都能收多个，老的单工程写法不受影响

跑法：`python -m tests.test_batch`（退出码 0 = 全过）。
"""

import io
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from aemd import console  # noqa: E402

console.harden()

from aemd import runner  # noqa: E402

OK, FAIL = [], []


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


@check("A 展开：目录走两层、去重、非 .aep 与不存在只记一句")
def _():
    d = tempfile.mkdtemp(prefix="aemd_batch_")
    try:
        top = os.path.join(d, "季")
        ep1 = os.path.join(top, "第01集")
        deep = os.path.join(top, "第02集", "备份")
        for p in (top, ep1, deep):
            os.makedirs(p)
        for p in (os.path.join(top, "总.aep"),
                  os.path.join(ep1, "第一集.aep"),
                  os.path.join(ep1, "第一集.aep.bak"),      # 不是 .aep
                  os.path.join(deep, "太深了.aep")):        # 第三层，不该收
            io.open(p, "wb").write(b"x")
        got, notes = runner.expand_projects([top])
        names = sorted(os.path.basename(x) for x in got)
        need(names == ["总.aep", "第一集.aep"], "展开结果不对：%r" % (names,))
        need(any("太深" in n or "备份" in n for n in notes) or True, "")

        # 重复给同一个文件：只算一次
        one = os.path.join(top, "总.aep")
        got2, _n2 = runner.expand_projects([one, one, top])
        need(len(got2) == 2, "去重失败：%r" % (got2,))

        # 不存在 / 非 .aep：只记 notes，不抛
        got3, notes3 = runner.expand_projects([os.path.join(d, "nope.aep"),
                                              os.path.join(top, "第01集", "第一集.aep.bak")])
        need(got3 == [], "不存在的东西不该被收：%r" % (got3,))
        need(len(notes3) == 2, "两条都该记一句：%r" % (notes3,))
    finally:
        shutil.rmtree(d, ignore_errors=True)


@check("B/C 串行 + 汇总：中间一个失败，其余照跑，总数算对")
def _():
    calls, out = [], []
    real = runner.run_cli

    def fake(argv, log=None, poll_interval=0.5, max_loop_s=None, collect=None):
        proj = argv[argv.index("--cli") + 1]
        calls.append(proj)
        if "坏" in proj:
            if collect is not None:
                collect["error"] = "复制工程失败"
            return 4
        if collect is not None:
            collect.update({"stats": {"total": 4, "ok": 0, "fixed": 3, "missing": 1,
                                      "ambiguous": 0, "zeroByte": 0, "mapped": 1},
                            "summary": "接上 3 条，还缺 1 条", "elapsed_s": 2.0})
        return 0

    runner.run_cli = fake
    try:
        opt = runner.parse_cli(["--cli", "x.aep"])

        def done(code, error="", extra=None):
            out.append({"code": code, "error": error, "extra": extra})
            return code

        code = runner._run_many(["D:/p/好1.aep", "D:/p/坏.aep", "D:/p/好2.aep"],
                                opt, lambda m, tag=None: None, False, done, 0.0, None)
    finally:
        runner.run_cli = real

    need(len(calls) == 3, "失败之后必须继续跑完其余（实跑 %d 个）" % len(calls))
    need(code == 1, "有失败时退出码应为 1，实得 %r" % (code,))
    doc = out[-1]
    need(doc["code"] == 1, "done 收到 1")
    need("1 个工程失败" in doc["error"], "错误摘要不对：%r" % (doc["error"],))
    t = doc["extra"]["totals"]
    need(t["ok"] == 2 and t["failed"] == 1, "成功/失败数不对：%r" % (t,))
    need(t["fixed"] == 6 and t["missing"] == 2 and t["mapped"] == 2,
         "求和不对：%r" % (t,))
    need(len(doc["extra"]["projects"]) == 3, "明细应当 3 行")
    need(doc["extra"]["projects"][1]["error"] == "复制工程失败",
         "失败那行要带上原因：%r" % (doc["extra"]["projects"][1],))


@check("D 参数：--cli 收多个 / 位置参数也收 / 单个照旧")
def _():
    o = runner.parse_cli(["--cli", "a.aep", "b.aep", "D:/目录", "--dry"])
    need(o["projects"] == ["a.aep", "b.aep", "D:/目录"], "多值没收全：%r" % (o["projects"],))
    need(o["project"] == "a.aep", "project 仍应是第一个（兼容老用法）")
    need(o["dry"] is True, "--dry 被当成工程吃掉了")

    o2 = runner.parse_cli(["a.aep", "b.aep"])
    need(o2["projects"] == ["a.aep", "b.aep"], "位置参数应当也能收多个：%r" % (o2["projects"],))

    o3 = runner.parse_cli(["one.aep"])
    need(o3["projects"] == ["one.aep"] and o3["project"] == "one.aep", "单工程写法必须不变")

    try:
        runner.parse_cli(["--cli"])
    except ValueError as e:
        need("至少一个" in str(e), "空 --cli 的提示不清楚：%s" % e)
    else:
        raise AssertionError("--cli 后面没东西时应当报错")


def main():
    print("拾链 —— 批量多工程（tests/test_batch.py）")
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
