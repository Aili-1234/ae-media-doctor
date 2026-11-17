# -*- coding: utf-8 -*-
"""tests/test_plugin_guard.py —— 修复期「禁用/恢复第三方插件」的安全网（1.1.9）。

目标：保证这条「自动禁第三方插件 → 跑修复 → 恢复」的路：
  · 只禁第三方（顶层 .aex + 非官方目录），官方目录（Effects/Format/Keyframe/Extensions/Cineware）一个不碰
  · 禁用/恢复成对、幂等、可逆；就算中途崩了，下次启动靠落盘清单恢复
  · 清单文件不落到工程目录、不写 Adobe 安装目录（只改文件名，不改内容）

跑法：`python -m tests.test_plugin_guard`（退出码 0 = 全过）。
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aemd import console  # noqa: E402
console.harden()

from aemd import plugin_guard  # noqa: E402

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


def _mk_ae_tree(root):
    """造一个假 AE：Plug-ins 下有官方目录 + 第三方目录 + 顶层 .aex。返回 (exe, 官方清单, 第三方清单)。"""
    base = os.path.join(root, "Support Files", "Plug-ins")
    os.makedirs(base, exist_ok=True)
    official = []
    for off in ("Effects", "Format", "Keyframe", "Extensions", "Cineware by Maxon"):
        d = os.path.join(base, off)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "official_%s.aex" % off.replace(" ", "_"))
        open(p, "w").write("off")
        official.append(p)
    third = []
    os.makedirs(os.path.join(base, "Trapcode"), exist_ok=True)
    p = os.path.join(base, "Trapcode", "Particular.aex")
    open(p, "w").write("third")
    third.append(p)
    for name in ("OpticalFlares.aex", "Saber.aex"):
        p = os.path.join(base, name)
        open(p, "w").write("third")
        third.append(p)
    exe = os.path.join(root, "Support Files", "AfterFX.exe")
    open(exe, "w").write("exe")
    return exe, official, third


@check("A. 只禁第三方，官方目录一个不碰")
def _():
    root = tempfile.mkdtemp()
    exe, official, third = _mk_ae_tree(root)
    got = plugin_guard._third_party_aex(exe)
    got_norm = [os.path.normcase(x) for x in got]
    need(len(got) == len(third), "第三方数量不对：%r" % [os.path.basename(x) for x in got])
    for p in third:
        need(os.path.normcase(p) in got_norm, "漏了第三方 %s" % p)
    for p in official:
        need(os.path.normcase(p) not in got_norm, "官方插件被误判成第三方：%s" % p)


@check("B. disable 改名 .off 并落清单；restore 改回 .aex 并删清单")
def _():
    root = tempfile.mkdtemp()
    exe, official, third = _mk_ae_tree(root)
    offs = plugin_guard.disable(exe)
    need(len(offs) == len(third), "禁用数量不对：%d != %d" % (len(offs), len(third)))
    for p in third:
        need(os.path.isfile(p + plugin_guard.DISABLE_SUFFIX), "没改成 .off：%s" % p)
        need(not os.path.isfile(p), ".aex 应该已经消失：%s" % p)
    for p in official:
        need(os.path.isfile(p), "官方插件被动了：%s" % p)
    # 清单已落盘
    need(os.path.isfile(plugin_guard._guard_list_path()), "清单没落盘")

    n = plugin_guard.restore()
    need(n == len(third), "恢复数量不对：%d != %d" % (n, len(third)))
    for p in third:
        need(os.path.isfile(p), "没恢复成 .aex：%s" % p)
        need(not os.path.isfile(p + plugin_guard.DISABLE_SUFFIX), ".off 残留：%s" % p)
    need(not os.path.isfile(plugin_guard._guard_list_path()), "清单没删")


@check("C. 崩溃残留：recover_if_left 靠清单把 .off 抢救回来")
def _():
    root = tempfile.mkdtemp()
    exe, official, third = _mk_ae_tree(root)
    plugin_guard.disable(exe)          # 禁用后"崩溃"，不调 restore
    # 模拟下次启动：清单还在，.off 还在
    need(os.path.isfile(plugin_guard._guard_list_path()), "清单应还在")
    n = plugin_guard.recover_if_left()
    need(n == len(third), "抢救数量不对：%d != %d" % (n, len(third)))
    for p in third:
        need(os.path.isfile(p), "没抢救回 .aex：%s" % p)


@check("D. 幂等：没有残留时 recover_if_left 返回 0、什么都不改")
def _():
    root = tempfile.mkdtemp()
    exe, official, third = _mk_ae_tree(root)
    need(plugin_guard.recover_if_left() == 0, "没残留应返回 0")
    for p in third:
        need(os.path.isfile(p), "没禁用却被动了：%s" % p)


def main():
    print("拾链 —— 插件禁用/恢复安全网（tests/test_plugin_guard.py）")
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
