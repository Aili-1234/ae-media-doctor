#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模块边界检查（§2 依赖方向 / §8 防线 / R5 开关白名单）。

    python tools/check_imports.py        # 在 media-doctor-v2 目录下跑
    退出码 0 = 全过

它盯四件事，全是「靠眼看会看漏」的：

1. **没有循环 import**（用 ast 建图，DFS 找环）
2. **依赖方向单向**：ui → runner → {aehost, jsx, scanner, reports} → {winproc, everything, cfg, paths, const}
   反向 import 直接报错（一代的 app.py 就是谁都能调谁，改了 A 坏 B）
3. **ui 层不许碰进程**：`subprocess` / `taskkill` / `aemd.winproc` 一律不许出现在 aemd/ui/*
   （§8：关 AE 的唯一入口 aehost.close_own_ae；界面连 import 的权利都没有）
4. **命令行开关白名单**（R5）：源码里出现的 `-x` 形式开关，必须在
   `const.VERIFIED_SWITCHES`（AE 的）或 `NON_AE_SWITCHES`（taskkill/powershell 等非 AE 的）里。
   新开关没进白名单 → 报错，逼着写清实测证据。
"""

import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)            # media-doctor-v2
sys.path.insert(0, ROOT)

from aemd import console

console.harden()

from aemd.const import VERIFIED_SWITCHES   # noqa: E402

# ---- 依赖方向的分层（数字越小越靠上；上层可以 import 下层，反之不许）----
LAYERS = {
    "ui.window": 0, "ui.widgets": 0, "ui.worker": 0, "ui.theme": 0, "ui": 0,
    "runner": 1,
    "aehost": 2, "jsx": 2, "scanner": 2, "reports": 2,
    "winproc": 3, "everything": 3, "cfg": 3, "paths": 3, "const": 3, "updater": 3, "diag": 3,
}

# 非 AE 的命令行开关：taskkill / powershell / es.exe 自己的参数。
# 这些不受 R5 约束（R5 管的是「怎么启动 AE」），但要列出来，免得它们藏进 AE 的 argv 里。
NON_AE_SWITCHES = {
    "/PID", "/T", "/F", "/IM", "/FI", "/NH", "/FO",            # taskkill / tasklist
    "-NoProfile", "-Command", "-ExecutionPolicy", "-WindowStyle",  # powershell
    "-NonInteractive", "-LiteralPath",                            # powershell（diag 取签名用）
    "-get-result-count", "-search", "-export-txt", "-n", "-a",   # es.exe（-n 条数上限 / -a 全路径）
    "-startup", "-minimized", "-install-service", "-start-service",  # everything.exe
    "-uninstall-service", "-svc", "-help", "-version",
    "--cli", "--ver", "--selfcheck", "--project", "--yes", "--no-wait",
    "-v", "-h", "--help",
    "-topmost",                                                   # Tk 窗口属性（截图工具用）
}

SKIP_DIRS = {"__pycache__", "build", "dist", "docs", ".git"}


def iter_py(root=ROOT):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def mod_name(path):
    """把文件路径翻成 aemd 包内的模块名（包外文件返回 None）。"""
    rel = os.path.relpath(path, ROOT).replace("\\", "/")
    if rel == "main.py":
        return "main"
    if not rel.startswith("aemd/"):
        return None
    parts = rel[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def scan_imports(tree, here):
    """返回本文件 import 到的 aemd 内模块名列表（相对 import 展开成绝对名）。"""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "aemd" or a.name.startswith("aemd."):
                    out.append(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:                      # from .x import y / from ..x import y
                base_parts = here.split(".")
                if node.module:
                    parts = base_parts[:len(base_parts) - node.level + 1] + node.module.split(".")
                else:
                    parts = base_parts[:len(base_parts) - node.level + 1]
                name = ".".join(p for p in parts if p)
                if name:
                    out.append(name)
            elif node.module and (node.module == "aemd" or node.module.startswith("aemd.")):
                out.append(node.module)
    # 去重保序
    seen, res = set(), []
    for m in out:
        if m not in seen:
            seen.add(m)
            res.append(m)
    return res


def build_graph():
    graph = {}
    for path in iter_py():
        me = mod_name(path)
        if me is None:
            continue
        try:
            src = open(path, encoding="utf-8").read()
            tree = ast.parse(src, filename=path)
        except SyntaxError as e:
            print("FAIL  语法错：%s -> %s" % (path, e))
            return None
        deps = []
        for m in scan_imports(tree, me):
            short = m[len("aemd."):] if m.startswith("aemd.") else m
            deps.append(short)
        graph[me] = deps
    return graph


def check_cycles(graph):
    fails = []
    state = {}

    def dfs(node, stack):
        state[node] = 1
        for dep in graph.get(node, []):
            if dep not in graph:
                continue
            if state.get(dep) == 1:
                cyc = stack[stack.index(dep):] + [dep] if dep in stack else stack + [dep]
                fails.append("循环 import：%s" % " → ".join(cyc))
            elif state.get(dep, 0) == 0:
                dfs(dep, stack + [dep])
        state[node] = 2

    for n in list(graph):
        if state.get(n, 0) == 0:
            dfs(n, [n])
    if not fails:
        print("  PASS  没有循环 import（%d 个模块）" % len(graph))
    return fails


def check_direction(graph):
    fails = []
    for me, deps in graph.items():
        if me not in LAYERS:
            continue
        for dep in deps:
            if dep not in LAYERS:
                continue
            if LAYERS[dep] > LAYERS[me]:
                fails.append("依赖方向反了：%s(L%d) → %s(L%d)"
                             % (me, LAYERS[me], dep, LAYERS[dep]))
    if not fails:
        print("  PASS  依赖方向单向（ui → runner → 能力层 → 基座）")
    return fails


def _code_only(path):
    """只留真正的代码：去掉注释与所有字符串字面量（含 docstring）。

    为什么要去掉字符串：`aemd/ui/__init__.py` 的模块说明里就写着
    「ui 不许出现 subprocess/taskkill」——那句话本身不是违规，
    一刀切 text 搜索会把规约当违规（P0 首跑就是这么误报 6 条的）。
    """
    import io as _io
    import tokenize
    src = _io.open(path, encoding="utf-8").read()
    pieces = []
    for tok in tokenize.generate_tokens(_io.StringIO(src).readline):
        ttype, tstr = tok.type, tok.string
        if ttype in (tokenize.COMMENT, tokenize.STRING):
            continue          # 注释与字符串（含 docstring）不参与判定
        if ttype in (tokenize.NL, tokenize.NEWLINE):
            pieces.append("\n")
            continue
        pieces.append(tstr)
    return "".join(pieces)


def check_ui_purity():
    fails = []
    banned = ("subprocess", "taskkill", "winproc", "Popen", "os.system", "shell=True")
    for path in iter_py(os.path.join(ROOT, "aemd", "ui")):
        code = _code_only(path)
        for token in banned:
            if token in code:
                fails.append("ui 层出现 %r：%s（§8：界面不许碰进程）"
                             % (token, os.path.basename(path)))
    if not fails:
        print("  PASS  ui 层没有 subprocess/taskkill/winproc（注释与字符串不算）")
    return fails


SWITCH_RE = re.compile(r'["\'](-[A-Za-z][A-Za-z0-9-]{0,20})["\']')


def check_switches():
    """R5：源码里出现的开关字面量必须已登记（AE 的进 VERIFIED_SWITCHES，其余进 NON_AE_SWITCHES）。"""
    fails = []
    seen = {}
    for path in iter_py():
        src = open(path, encoding="utf-8").read()
        for m in SWITCH_RE.finditer(src):
            tok = m.group(1)
            seen.setdefault(tok, set()).add(os.path.relpath(path, ROOT))
    allowed = set(VERIFIED_SWITCHES) | NON_AE_SWITCHES
    for tok, files in sorted(seen.items()):
        if tok not in allowed:
            fails.append("未登记的开关 %r（出现在 %s）—— 要进 const.VERIFIED_SWITCHES 必须附实测证据"
                         % (tok, "、".join(sorted(files))))
    if not fails:
        print("  PASS  开关字面量全部已登记（AE 白名单 %d 个：%s）"
              % (len(VERIFIED_SWITCHES), "、".join(VERIFIED_SWITCHES) or "空"))
    return fails


def check_verified_switches():
    """R5 的元检查：白名单里每一项都要有实测证据说明。"""
    fails = []
    from aemd.const import VERIFIED_SWITCH_EVIDENCE
    for sw in VERIFIED_SWITCHES:
        if sw not in VERIFIED_SWITCH_EVIDENCE:
            fails.append("VERIFIED_SWITCHES 里的 %r 没有实测证据（VERIFIED_SWITCH_EVIDENCE）" % sw)
    if "-r" not in VERIFIED_SWITCHES:
        fails.append("`-r` 不在白名单里 —— 它是目前唯一实测可用的 AE 开关（B 回退路径要用）")
    if not fails:
        print("  PASS  AE 开关白名单含 -r 且带实测证据说明")
    return fails


def main():
    print("module boundary check —— %s" % ROOT)
    graph = build_graph()
    if graph is None:
        return 1
    fails = []
    fails += check_cycles(graph)
    fails += check_direction(graph)
    fails += check_ui_purity()
    fails += check_verified_switches()
    fails += check_switches()
    print("")
    if fails:
        print("FAIL %d 项：" % len(fails))
        for f in fails:
            print("  - %s" % f)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())