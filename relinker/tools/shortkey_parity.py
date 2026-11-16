# -*- coding: utf-8 -*-
"""shortKey 双向对拍：Python 的 `scanner.short_key()` ↔ 模板里的 JS `shortKey()`。

跑法（在 media-doctor-v2 目录下）：

    python tools/shortkey_parity.py

为什么要专门一个工具：短名规则在两个地方各有一份实现（Python 建索引、AE 里 JSX 用它
兜底匹配）。两边差一个字符，结果就是"索引里有、AE 里匹配不上"，而且**不报错** ——
用户看到的是"素材明明在却还是缺"。所以拿 node 真跑一遍模板里的函数，跟 Python 对答案。

找不到 node → 打印 SKIP 并退出 0（CI 之外的环境不该因为没装 node 失败）。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from aemd import console

console.harden()

from aemd import jsx, scanner      # noqa: E402

CORPUS = [
    "1 (1).119.880fps.QE.HW.VFIF.S=1.0.VFI=official_4.15.FD=FORW-2.SR=realesr-animevideov3-x2_6fac5f.mp4",
    "1 (55).119.880fps.QE.HW.VFIF.S=1.0.VFI=official_4.15.FD=FORW-2.SR=realesr-animevideov3-x2_827d60.mp4",
    "Again & Again.mp3",
    "FILMBURN_SOFT_002(1).mp4",
    "hanabi_logo.mp4",
    "SnapInsta.to_AQPY05r3SvcTpgP948a531CjtTQUtVIVZnTR5Tllwoo3EtM5lQrewdC5Y38asaSZbsF670hd6AA8zPKTDRVrD9y9.mp4",
    "noext",
    "UPPER.MP4",
    ".hidden.mp4",
    "trailing.space .mp4",
    "中文 名字 (2).mov",
    "a.b.c.d.e.png",
    "   leading spaces .jpg",
    "x",
    "",
    "混排.119fps.v2_final.MOV",
    "only.dots...mp4",
    "  .mp4",
]


def js_functions():
    """从模板里切出 extOf / baseOf / shortKey 三个函数（切不到就炸，绝不静默跳过）。"""
    tpl = jsx.JSX_TEMPLATE
    a = tpl.index("function extOf")
    b = tpl.index("function isMedia")
    body = tpl[a:b]
    for need in ("function extOf", "function baseOf", "function shortKey"):
        if need not in body:
            raise RuntimeError("模板里找不到 %s —— 短名规则被改动了？" % need)
    return body


def run_node(node, names):
    script = js_functions() + """
var input = JSON.parse(require("fs").readFileSync(process.argv[2], "utf8"));
var out = [];
for (var i = 0; i < input.length; i++) out.push(shortKey(input[i]));
process.stdout.write(JSON.stringify(out));
"""
    fd, js = tempfile.mkstemp(suffix=".js")
    fd2, stdin_json = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(script.encode("utf-8"))
        with os.fdopen(fd2, "wb") as fh:
            fh.write(json.dumps(names, ensure_ascii=False).encode("utf-8"))
        r = subprocess.run([node, js, stdin_json], capture_output=True, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            raise RuntimeError("node 跑失败：%s" % (r.stderr or b"").decode("utf-8", "replace")[:300])
        return json.loads(r.stdout.decode("utf-8"))
    finally:
        for p in (js, stdin_json):
            try:
                os.unlink(p)
            except OSError:
                pass


def main():
    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        print("SKIP 没找到 node —— 跳过 JSX 侧对拍（Python 侧规则仍由 selftest 覆盖）")
        return 0
    print("shortKey 双向对拍：%d 条语料（node %s）" % (len(CORPUS), node))
    js_out = run_node(node, CORPUS)
    bad = []
    for name, got in zip(CORPUS, js_out):
        want = scanner.short_key(name)
        if got != want:
            bad.append((name, want, got))
    print("  语料 %d 条 · 一致 %d 条 · 不一致 %d 条"
          % (len(CORPUS), len(CORPUS) - len(bad), len(bad)))
    if bad:
        print("")
        for name, py, js in bad[:10]:
            print("  DIFF %r\n       Python=%r\n       JSX   =%r" % (name, py, js))
        print("")
        print("FAIL 短名规则两边不一致（改一边就必须改另一边）")
        return 1
    print("")
    print("ALL PASS（Python 与 ExtendScript 的短名规则逐条一致）")
    return 0


if __name__ == "__main__":
    sys.exit(main())