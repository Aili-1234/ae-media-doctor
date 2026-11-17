# -*- coding: utf-8 -*-
"""tests/test_jsx_sequence.py —— 用 node 真跑图像序列判据（1.1.7）。

为什么不只做静态断言：`seqParts` / `seqFrames` / `seqGuard` 里全是
「正则怎么切」「同目录怎么数」这类**只有跑起来才知道对不对**的逻辑。
静态检查只能证明"代码在"，证明不了"数字对"。所以这里把这三个函数从渲染出的
JSX 里原样切出来，配一个极小的 Folder stub（用 node 的 fs），在真实临时目录上跑。

三条要证明的事：
  A. 判形态：img_0001.png 认得出、2024.png / clip12.mov / img_0001.psd 认不出
  B. 数帧  ：同目录同前缀同位数同扩展名的**才**计入（img_0001.jpg 不许混进来）
  C. 说话  ：帧号变了 -> SEQ-OFFSET；一致 -> SEQ-OK；原来不是序列 -> SEQ-WARN；
             只有一帧的编号文件 -> 一个字都不写（不吵人）

跑法：`python -m tests.test_jsx_sequence`（退出码 0 = 全过）。
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from aemd import console  # noqa: E402

console.harden()

from aemd import jsx  # noqa: E402

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


HARNESS = r'''
var fs = require("fs"), path = require("path");
var OUT = [];
function L(s){ OUT.push(s); }
function extOf(s){ var i = s.lastIndexOf("."); return i < 0 ? "" : s.substring(i + 1).toLowerCase(); }
function baseOf(s){ var i = s.lastIndexOf("."); return i < 0 ? s : s.substring(0, i); }
function baseNameOf(p){
  var s2 = String(p || "").replace(/\\/g, "/");
  return s2.substring(s2.lastIndexOf("/") + 1).toLowerCase();
}
function Folder(dir){ this.dir = dir; this.exists = fs.existsSync(dir); }
Folder.prototype.getFiles = function(mask){
  var out = [];
  var pre = String(mask).replace(/\*$/, "").toLowerCase();
  var list = fs.readdirSync(this.dir);
  for (var i = 0; i < list.length; i++){
    if (list[i].toLowerCase().indexOf(pre) === 0){
      out.push({ name: list[i], fsName: path.join(this.dir, list[i]) });
    }
  }
  return out;
};
__FUNCS__
var cases = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
var res = { parts: {}, frames: [], logs: [] };
for (var i = 0; i < cases.parts.length; i++){
  var n = cases.parts[i];
  var sp = seqParts(n);
  res.parts[n] = sp ? { prefix: sp.prefix, num: sp.num, digits: sp.digits, ext: sp.ext } : null;
}
for (var j = 0; j < cases.frames.length; j++){
  res.frames.push({ key: cases.frames[j].key, n: seqFrames(cases.frames[j].path) });
}
for (var k = 0; k < cases.guards.length; k++){
  var g = cases.guards[k];
  OUT.length = 0;
  seqGuard({ name: g.name }, g.orig, g.target);
  res.logs.push(OUT.slice());
}
console.log(JSON.stringify(res));
'''


def _render():
    return jsx.render(r"D:\ae\again.aep", [r"D:\ae"], False, False, "", True,
                      r"D:\ae\.ae_doctor", app_ver="1.1.7")


def _cut(text):
    """把 seqParts / seqFrames / dirOf / seqGuard 整段切出来（含 SEQ_EXT 与缓存）。"""
    a = text.index("var SEQ_EXT")
    b = text.index("function relink(item){", a)
    return text[a:b]


def _run(cases):
    node = shutil.which("node")
    need(node, "找不到 node —— 这套判据只能靠真跑来验，没 node 就没法验")
    d = tempfile.mkdtemp(prefix="aemd_seq_")
    try:
        cut = _cut(_render())
        harness = HARNESS.replace("__FUNCS__", cut)
        hp = os.path.join(d, "harness.js")
        cp = os.path.join(d, "cases.json")
        io.open(hp, "w", encoding="utf-8", newline="\n").write(harness)
        io.open(cp, "w", encoding="utf-8", newline="\n").write(
            json.dumps(cases, ensure_ascii=False))
        out = subprocess.run([node, hp, cp], capture_output=True, timeout=120)
        text = out.stdout.decode("utf-8", "replace") + out.stderr.decode("utf-8", "replace")
        need(out.returncode == 0, "harness 退出码 %s：%s" % (out.returncode, text.strip()[-500:]))
        try:
            return json.loads(out.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
        except Exception:
            raise AssertionError("harness 没吐 JSON：%s" % text.strip()[-500:])
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _tree():
    """造三组真实文件：5 帧序列 / 1 帧编号图 / 带 .jpg 干扰的同前缀。"""
    d = tempfile.mkdtemp(prefix="aemd_seqtree_")
    seq = os.path.join(d, "seq")
    one = os.path.join(d, "one")
    mix = os.path.join(d, "mix")
    for p in (seq, one, mix):
        os.makedirs(p)
    for i in range(1, 6):
        io.open(os.path.join(seq, "img_%04d.png" % i), "wb").write(b"x")
    io.open(os.path.join(seq, "img_0001.jpg"), "wb").write(b"x")   # 同前缀、不同扩展名
    io.open(os.path.join(seq, "other.png"), "wb").write(b"x")
    io.open(os.path.join(seq, "img_0001.psd"), "wb").write(b"x")   # psd 不是序列
    io.open(os.path.join(one, "only_0001.png"), "wb").write(b"x")
    for i in (10, 11, 12):
        io.open(os.path.join(mix, "shot_%04d.png" % i), "wb").write(b"x")
    return d, seq, one, mix


@check("A 判形态：认得出序列帧，也不把年份/视频/psd 误判成序列")
def _():
    cases = {"parts": ["img_0001.png", "a_01.exr", "2024.png", "clip12.mov",
                       "img_0001.psd", "shot2_0010.tif", "noext", "x.png"],
             "frames": [], "guards": []}
    r = _run(cases)
    p = r["parts"]
    need(p["img_0001.png"] == {"prefix": "img_", "num": "0001", "digits": 4, "ext": "png"},
         "img_0001.png 解析错：%r" % (p["img_0001.png"],))
    need(p["a_01.exr"] == {"prefix": "a_", "num": "01", "digits": 2, "ext": "exr"},
         "两位数帧号解析错：%r" % (p["a_01.exr"],))
    need(p["shot2_0010.tif"]["prefix"] == "shot2_", "前缀带数字时切错了：%r" % (p["shot2_0010.tif"],))
    for bad in ("2024.png", "clip12.mov", "img_0001.psd", "noext", "x.png"):
        need(p[bad] is None, "%s 不该被当成序列帧：%r" % (bad, p[bad]))


@check("B 数帧：只数同前缀+同位数+同扩展名，jpg 干扰与单帧都不算")
def _():
    d, seq, one, mix = _tree()
    try:
        cases = {"parts": [], "guards": [],
                 "frames": [{"key": "seq", "path": os.path.join(seq, "img_0001.png").replace("\\", "/")},
                            {"key": "one", "path": os.path.join(one, "only_0001.png").replace("\\", "/")},
                            {"key": "mix", "path": os.path.join(mix, "shot_0010.png").replace("\\", "/")}]}
        r = _run(cases)
        got = dict((x["key"], x["n"]) for x in r["frames"])
        need(got["seq"] == 5, "5 帧序列数出 %r（img_0001.jpg 不该被算进来）" % (got["seq"],))
        need(got["one"] == 1, "单帧目录数出 %r" % (got["one"],))
        need(got["mix"] == 3, "3 帧序列数出 %r" % (got["mix"],))
    finally:
        shutil.rmtree(d, ignore_errors=True)


@check("C 说话：OFFSET / OK / WARN / 单帧不吭声，四种情形各一句")
def _():
    d, seq, one, mix = _tree()
    try:
        def p(*parts):
            return os.path.join(*parts).replace("\\", "/")
        cases = {"parts": [], "frames": [], "guards": [
            {"name": "o1", "orig": p(seq, "img_0005.png"), "target": p(seq, "img_0001.png")},
            {"name": "o2", "orig": p(seq, "img_0001.png"), "target": p(seq, "img_0001.png")},
            {"name": "o3", "orig": p(seq, "photo.png"), "target": p(seq, "img_0001.png")},
            {"name": "o4", "orig": p(seq, "img_0001.png"), "target": p(seq, "other.png")},
            {"name": "o5", "orig": p(one, "x.png"), "target": p(one, "only_0001.png")},
        ]}
        logs = _run(cases)["logs"]
        need("SEQ-OFFSET" in "".join(logs[0]), "帧号 0005 -> 0001 必须报 SEQ-OFFSET：%r" % (logs[0],))
        need("0005" in "".join(logs[0]) and "0001" in "".join(logs[0]), "OFFSET 要写清两侧帧号：%r" % (logs[0],))
        need("SEQ-OK" in "".join(logs[1]), "同帧号应当 SEQ-OK：%r" % (logs[1],))
        need("SEQ-WARN" in "".join(logs[2]), "原来不是序列必须 SEQ-WARN：%r" % (logs[2],))
        need("SEQ-WARN" in "".join(logs[3]), "原来是序列、接上不是序列必须 SEQ-WARN：%r" % (logs[3],))
        need(logs[4] == [], "只有一帧的编号图不该写任何日志（会变成噪音）：%r" % (logs[4],))
    finally:
        shutil.rmtree(d, ignore_errors=True)


@check("D 它只是日志：不动 stats、不改匹配规则、自检通过")
def _():
    t = _render()
    for bad in ("stats.seq", "stats.warn++", "stats.seqWarn"):
        need(bad not in t, "不该引入新的统计字段：%s" % bad)
    for must in ("stats.mapped++", "stats.ambiguous++", "全名匹配", "基名匹配"):
        need(must in t, "原有逻辑被改坏了：%s" % must)
    need(t.index("seqGuard(item, curPath, target);") > t.index('L("RELINKED  '),
         "seqGuard 必须在 replace / RELINKED 之后调用（接上才谈得上核对）")
    problems = jsx.validate(t)
    need(not problems, "自检没过：%r" % (problems,))


@check("E 对拍口径：整块能被 KNOWN_DIFFS 删回 v1.9.8 的样子")
def _():
    t = _render()
    need(t.count("SEQ-WARN") == 2, "SEQ-WARN 应出现 2 处（两个分支），实得 %d" % t.count("SEQ-WARN"))
    for new_bit, old_bit in jsx.KNOWN_DIFFS:
        need(new_bit in t, "登记片段没出现在渲染结果里：%r" % (new_bit[:40],))
        t = t.replace(new_bit, old_bit, 1)
    for gone in ("seqParts", "seqFrames", "SEQ-OFFSET", "seqGuard("):
        need(gone not in t, "删完还剩 %s：说明没登记进 KNOWN_DIFFS" % gone)


def main():
    print("拾链 —— 图像序列判据（tests/test_jsx_sequence.py，真跑 node）")
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
