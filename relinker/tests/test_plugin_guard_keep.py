# -*- coding: utf-8 -*-
"""tests/test_plugin_guard_keep.py —— 1.1.10「脚本面板宿主不禁用 + 脚本永不改」的安全网。

背景（用户 2026-11-17 结论）：插件恢复后 FX Console 这类脚本面板不会自动重载，
要重启 AE 才回来。更好的做法是**一开始就不禁用面板宿主**：
  · `Plug-ins\\VideoCopilot\\FXConsole.aex` 是面板宿主，不禁用 → 面板一直在；
  · `.jsx` / `.jsxbin` 脚本本工具从来不碰（只改名 `.aex`）；
  · 真会卡死打开工程的是效果插件（Optical Flares 这类），它们照旧禁用/恢复。

跑法：`python -m tests.test_plugin_guard_keep`（退出码 0 = 全过）。
"""

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


def _mk_tree(root):
    """假 AE：效果插件（要被禁）+ 面板宿主（不许禁）+ 面板脚本（永不碰）。"""
    base = os.path.join(root, "Support Files", "Plug-ins")
    os.makedirs(os.path.join(base, "VideoCopilot"), exist_ok=True)
    os.makedirs(os.path.join(base, "Effects"), exist_ok=True)
    keep = os.path.join(base, "VideoCopilot", "FXConsole.aex")
    open(keep, "w").write("panel-host")
    official = os.path.join(base, "Effects", "official_effects.aex")
    open(official, "w").write("off")
    thirds = []
    for name in ("OpticalFlares.aex", "Saber.aex"):
        p = os.path.join(base, name)
        open(p, "w").write("effect")
        thirds.append(p)
    panels = os.path.join(root, "Support Files", "Scripts", "ScriptUI Panels")
    os.makedirs(panels, exist_ok=True)
    jsx = os.path.join(panels, "FX Console.jsx")
    open(jsx, "w").write("// panel")
    jsxbin = os.path.join(panels, "tools.jsxbin")
    open(jsxbin, "w").write("bin")
    exe = os.path.join(root, "Support Files", "AfterFX.exe")
    open(exe, "w").write("exe")
    return exe, keep, official, thirds, jsx, jsxbin


@check("A. 面板宿主 FXConsole.aex 不进禁用名单（名单里仍只算效果插件）")
def _():
    root = tempfile.mkdtemp()
    exe, keep, official, thirds, _jsx, _bin = _mk_tree(root)
    need(not plugin_guard.is_kept_aex(thirds[0]), "效果插件不该被当成面板宿主")
    need(plugin_guard.is_kept_aex(keep), "FXConsole.aex 应被认成面板宿主")
    need(plugin_guard.is_kept_aex(keep.upper()), "大小写不该影响判定")


@check("B. disable 后：面板宿主与脚本原地不动，效果插件变 .off")
def _():
    root = tempfile.mkdtemp()
    exe, keep, official, thirds, jsx, jsxbin = _mk_tree(root)
    offs = plugin_guard.disable(exe)
    need(len(offs) == len(thirds), "禁用数量应等于效果插件数：%d != %d" % (len(offs), len(thirds)))
    need(os.path.isfile(keep), "面板宿主被禁用了（不许）")
    need(not os.path.isfile(keep + plugin_guard.DISABLE_SUFFIX), "面板宿主留下了 .off")
    need(os.path.isfile(official), "官方插件被动过")
    need(os.path.isfile(jsx) and os.path.isfile(jsxbin), ".jsx/.jsxbin 被改名了（不许）")
    for p in thirds:
        need(os.path.isfile(p + plugin_guard.DISABLE_SUFFIX), "效果插件没被禁用：%s" % p)


@check("C. 日志把「保留面板宿主」与「脚本不改名」写清楚")
def _():
    root = tempfile.mkdtemp()
    exe, _keep, _official, _thirds, _jsx, _bin = _mk_tree(root)
    lines = []
    plugin_guard.disable(exe, log=lambda s, tag=None: lines.append(s))
    text = "\n".join(lines)
    need("保留 1 个脚本面板宿主" in text, "缺少「保留面板宿主」日志：%r" % text)
    need("SCRIPT" in text and ".jsxbin" in text, "缺少「脚本不改名」日志：%r" % text)


@check("D. restore 只恢复效果插件，面板宿主与脚本保持原状")
def _():
    root = tempfile.mkdtemp()
    exe, keep, official, thirds, jsx, jsxbin = _mk_tree(root)
    plugin_guard.disable(exe)
    n = plugin_guard.restore()
    need(n == len(thirds), "恢复数量不对：%d != %d" % (n, len(thirds)))
    for p in thirds:
        need(os.path.isfile(p), "没恢复成 .aex：%s" % p)
        need(not os.path.isfile(p + plugin_guard.DISABLE_SUFFIX), ".off 残留：%s" % p)
    need(os.path.isfile(keep), "面板宿主不见了")
    need(os.path.isfile(jsx) and os.path.isfile(jsxbin), "脚本不见了")


@check("E. 上一轮遗留的 FXConsole.aex.off 会被改回 .aex（自愈）")
def _():
    root = tempfile.mkdtemp()
    exe, keep, official, thirds, jsx, jsxbin = _mk_tree(root)
    os.rename(keep, keep + plugin_guard.DISABLE_SUFFIX)   # 模拟旧版本留下的状态
    plugin_guard.disable(exe)
    need(os.path.isfile(keep), "遗留的 .off 没被改回 .aex")
    need(not os.path.isfile(keep + plugin_guard.DISABLE_SUFFIX), "遗留 .off 仍在")
    plugin_guard.restore()
    need(os.path.isfile(keep), "restore 之后面板宿主不见了")


def main():
    print("拾链 — 脚本面板宿主不禁用（tests/test_plugin_guard_keep.py）")
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
