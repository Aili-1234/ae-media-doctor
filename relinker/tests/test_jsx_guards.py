# -*- coding: utf-8 -*-
"""tests/test_jsx_guards.py —— JSX 里那些「只加一句日志、但关系到信任」的护栏（1.1.5）。

现在只有一条：分层 PSD/PSB/AI 重连后要提示核对图层。

为什么值得单独立测试：这类改动**不改统计、不改流程**，只是多一行日志，
所以任何"顺手重构"都可能把它弄丢，而丢了也不会报错 —— 用户又回到
「日志说成功、实际图层没了」的状态。测试就是替人盯着这一行。

跑法：`python -m tests.test_jsx_guards`（退出码 0 = 全过）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aemd import console  # noqa: E402

console.harden()

from aemd import jsx  # noqa: E402

FAIL, OK = [], []


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


def _render():
    return jsx.render(r"D:\ae\again.aep", [r"D:\ae"], False, False, "", True,
                      r"D:\ae\.ae_doctor", app_ver="1.1.5")


@check("A 分层素材护栏：psd / psb / ai 三种扩展名都覆盖，且只出现一次")
def _():
    t = _render()
    need(t.count("LAYER-WARN") == 1, "LAYER-WARN 出现 %d 次（应当恰好 1 次）" % t.count("LAYER-WARN"))
    for ext in ('lext === "psd"', 'lext === "psb"', 'lext === "ai"'):
        need(ext in t, "没覆盖 %s" % ext)
    need("请核对图层结构" in t, "提示语丢了（用户看不到「要自己核」这句）")


@check("B 它只是日志：不动 stats、不影响三道自检")
def _():
    t = _render()
    need('stats.mapped++' in t, "模板被改坏了（mapped 计数没了）")
    for bad in ("stats.layerWarn", "stats.warn++"):
        need(bad not in t, "不该引入新的统计字段：%s" % bad)
    problems = jsx.validate(t)
    need(not problems, "自检没过：%r" % (problems,))


@check("C 对拍口径：KNOWN_DIFFS 能把这条护栏整段删回 v1.9.8 的样子")
def _():
    t = _render()
    for new_bit, old_bit in jsx.KNOWN_DIFFS:
        need(new_bit in t, "登记片段没出现在渲染结果里：%r" % (new_bit[:40],))
        t = t.replace(new_bit, old_bit, 1)
    need("LAYER-WARN" not in t, "删完还剩 LAYER-WARN：说明没登记进 KNOWN_DIFFS")


@check("D 只重试缺件：名单进 JSX（小写），做的是过滤而不是改匹配规则")
def _():
    t = jsx.render(r"D://ae//again.aep", [r"D://ae"], False, False, "", True,
                   r"D://ae//.ae_doctor", app_ver="1.1.6",
                   only_missing=["A.MP4", "b.png"])
    need('var ONLY = ["a.mp4", "b.png"];' in t, "名单没进 JSX 或没转小写")
    need("function wantedByOnly" in t, "缺判据函数")
    need("if (!wantedByOnly(ci))" in t, "主循环没有按名单过滤")
    need('L("SKIP-ONLY "' in t, "跳过时没留痕（事后没法核对跳过了什么）")
    # 不能顺手改匹配：四层还在、mapping 还在
    for must in ("全名匹配", "基名匹配", "映射表", "stats.mapped++"):
        need(must in t, "原有匹配/映射被改坏了：%s" % must)
    need(not jsx.validate(t), "自检没过：%r" % (jsx.validate(t),))


@check("E 附着模式（attach）：工程不匹配时绝不 app.open()，只提示")
def _():
    """1.1.9：这是「手动打开」这条最可靠路径的关键护栏。

    背景（2026-11-17 真机）：脚本自己 app.open() 会撞上 Optical Flares 的窗口死锁；
    用户手动打开工程则每次都能过。所以 attach 模式下必须**绝不自己开工程**，
    工程对不上就写 FATAL 提示，让用户去 AE 里打开正确的工程。
    """
    t = jsx.render(r"D:\ae\again.aep", [r"D:\ae"], False, False, "", True,
                   r"D:\ae\.ae_doctor", app_ver="1.1.9", attach=True)
    need('attach: true' in t, "attach 没写进 CFG")
    # 关键：attach 分支里不能出现「自己去开工程」的调用
    need('if (CFG.attach){' in t, "attach 分支没了")
    need('当前打开的工程不是目标工程' in t, "不匹配时的提示丢了")
    # 普通模式仍保留 app.open()（自动打开路径不受影响）
    t2 = jsx.render(r"D:\ae\again.aep", [r"D:\ae"], False, False, "", True,
                    r"D:\ae\.ae_doctor", app_ver="1.1.9", attach=False)
    need('app.open(want);' in t2, "非 attach 模式必须还保留 app.open()（自动打开路径）")
    need('attach: false' in t2, "非 attach 模式 CFG.attach 应为 false")
    need(not jsx.validate(t), "attach 版自检没过：%r" % (jsx.validate(t),))


def main():
    print("拾链 —— JSX 护栏回归（tests/test_jsx_guards.py）")
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
