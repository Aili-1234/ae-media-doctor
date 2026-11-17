# -*- coding: utf-8 -*-
"""tests/test_mapping.py —— 手工映射表（1.1.0 / t-22）的回归。

它要钉死四件事：
  1. 读：正常表读得进来（键统一小写、反斜杠统一成正斜杠），带 BOM 也要读得进来；
     坏 JSON / 不是对象 / map 不是对象 / 值不是字符串 —— 一律空表，**不抛**。
  2. 查：先查原完整路径、再查原文件名（越具体越优先）。
  3. 接：生成的 JSX 里真的有这张表（`var MAPPING = {...}`），并且查表块在三道闸
     （占位符残留 / ES3 保留字 / node --check）里都不出事；空表时与老行为一致。
  4. 不越界：老报告缺 `mapped` 键要兜底成 0，六个老统计字段一个都不许变；
     没配映射时结论句与 v1.9.8 逐字相同。

跑法：`python -m tests.test_mapping`（退出码 0 = 全过）。
"""

import io
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aemd import console  # noqa: E402

console.harden()

from aemd import jsx, mapping, reports, runner  # noqa: E402

FAIL = []
OK = []


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


def _tmpdir():
    d = tempfile.mkdtemp(prefix="aemd_map_")
    return d


def _write(path, text, encoding="utf-8"):
    with io.open(path, "w", encoding=encoding, newline="") as fh:
        fh.write(text)
    return path


# ---------------------------------------------------------------- 1. 读

@check("load：正常表读出小写键 + 正斜杠；键值两侧空白被清掉")
def _():
    d = _tmpdir()
    p = os.path.join(d, "mapping.json")
    _write(p, json.dumps({"version": 1, "map": {
        "  旧名字.MP4 ": r"D:\素材\新名字.mp4",
        "D:/老工程/Footage/A.png": "D:/素材/a-修好的.png",
    }}, ensure_ascii=False))
    t = mapping.load(p)
    need(t.get("旧名字.mp4") == "D:/素材/新名字.mp4", "文件名键没规范化：%r" % (t,))
    need(t.get("d:/老工程/footage/a.png") == "D:/素材/a-修好的.png", "完整路径键没规范化：%r" % (t,))


@check("load：带 BOM 的表也能读（PowerShell 5.1 改过就会带 BOM）")
def _():
    d = _tmpdir()
    p = os.path.join(d, "mapping.json")
    _write(p, json.dumps({"version": 1, "map": {"a.mp4": "D:/x/a.mp4"}}), encoding="utf-8-sig")
    need(mapping.load(p).get("a.mp4") == "D:/x/a.mp4", "带 BOM 的表没读出来")


@check("load：坏 JSON / 不是对象 / map 不是对象 / 值不是字符串 -> 空表，不抛")
def _():
    d = _tmpdir()
    cases = [
        "{ 这不是 json",
        "[]",
        '{"version":1,"map":[1,2,3]}',
        '{"version":1,"map":{"a.mp4":123,"b.mp4":"D:/b.mp4"}}',
        '{"map":{}}',
    ]
    for i, text in enumerate(cases):
        p = os.path.join(d, "bad%d.json" % i)
        _write(p, text)
        t = mapping.load(p)
        need(isinstance(t, dict), "第 %d 例返回的不是 dict：%r" % (i, t))
        if i == 3:
            need(t.get("a.mp4") is None, "非字符串的值不该进表：%r" % (t,))
            need(t.get("b.mp4") == "D:/b.mp4", "合法的邻居被连坐了：%r" % (t,))
    need(mapping.load(os.path.join(d, "根本不存在.json")) == {}, "文件不存在应当空表")


# ---------------------------------------------------------------- 2. 查

@check("lookup：完整路径优先于同名文件（越具体越优先）")
def _():
    t = {"a.mp4": "D:/通用/a.mp4", "d:/老工程/a.mp4": "D:/专用/a.mp4"}
    need(mapping.lookup("a.mp4", r"D:\老工程\a.mp4", table=t) == "D:/专用/a.mp4",
         "完整路径没优先：%r" % (mapping.lookup("a.mp4", r"D:\老工程\a.mp4", table=t),))
    need(mapping.lookup("a.mp4", r"D:\别的工程\a.mp4", table=t) == "D:/通用/a.mp4",
         "路径没命中时应该退回文件名")
    need(mapping.lookup("没有.mp4", None, table=t) == "", "查不到应当返回空串")


@check("add / remove / entries：不改入参，删不存在的键不报错")
def _():
    t0 = {"a.mp4": "D:/a.mp4"}
    t1 = mapping.add(t0, "B.MP4", r"D:\b.mp4")
    need(t0 == {"a.mp4": "D:/a.mp4"}, "add 改了入参")
    need(t1.get("b.mp4") == "D:/b.mp4", "add 没规范化：%r" % (t1,))
    t2 = mapping.remove(t1, "b.mp4")
    need("b.mp4" not in t2 and "a.mp4" in t2, "remove 结果不对：%r" % (t2,))
    need(mapping.remove(t2, "没有这个键") == t2, "删不存在的键应当原样返回")
    need(mapping.entries(t1) == sorted(t1.items()), "entries 没排序")


# ---------------------------------------------------------------- 3. 存

@check("save：往返一致、写 version、不带 BOM、不留 .tmp")
def _():
    d = _tmpdir()
    p = os.path.join(d, "mapping.json")
    table = {"a.mp4": "D:/素材/a.mp4", "中文 名字.png": "D:/素材/中文.png"}
    out = mapping.save(table, p)
    need(out == p, "save 返回的路径不对：%r" % (out,))
    raw = io.open(p, "rb").read()
    need(not raw.startswith(b"\xef\xbb\xbf"), "落盘文件带了 BOM（读的人会崩）")
    data = json.loads(raw.decode("utf-8"))
    need(data.get("version") == mapping.VERSION, "没写 version：%r" % (data,))
    need(mapping.load(p) == table, "往返不一致：%r" % (mapping.load(p),))
    need(not os.path.exists(p + ".tmp"), "留下了 .tmp 临时文件")


@check("js_object：中文键不转义成 \\uXXXX（ExtendScript 里要能读懂）")
def _():
    s = mapping.js_object({"旧名字.mp4": "D:/新.mp4"})
    need("旧名字.mp4" in s, "中文键被转义了：%r" % (s,))
    need(s.startswith("{") and s.endswith("}"), "不是对象字面量：%r" % (s,))
    need(mapping.js_object({}) == "{}", "空表应是 {}：%r" % (mapping.js_object({}),))


# ---------------------------------------------------------------- 4. 接进 JSX

def _render(**kw):
    args = dict(project=r"D:\ae\again.aep", roots=[r"D:\ae"], dry=False, consolidate=False,
                cons_dir="", save=True, out_dir=r"D:\ae\.ae_doctor", app_ver="1.1.0")
    args.update(kw)
    return jsx.render(args["project"], args["roots"], args["dry"], args["consolidate"],
                      args["cons_dir"], args["save"], args["out_dir"],
                      depth=args.get("depth", 6), cons_mode="copy", launch_ms=0,
                      work_dir=r"C:\ProgramData\AEMediaDoctor\work", log_path="",
                      report_dir=args["out_dir"], state_dir=".ae_doctor",
                      app_ver=args["app_ver"], mapping=kw.get("mapping"))


@check("render：映射表整张进了 JSX（中文与正斜杠都原样）")
def _():
    t = _render(mapping={"旧名字.mp4": "D:/素材/新名字.mp4"})
    line = [l for l in t.splitlines() if l.startswith("var MAPPING")]
    need(len(line) == 1, "MAPPING 那行应当恰好一行：%r" % (line,))
    need("旧名字.mp4" in line[0] and "D:/素材/新名字.mp4" in line[0], "映射没进 JSX：%r" % (line[0],))


@check("render：查表块只出现一次，且 mapped 计数 / SUMMARY / 报告字段都在")
def _():
    t = _render(mapping={"a.mp4": "D:/a.mp4"})
    need(t.count("var mapHit") == 1, "查表块出现 %d 次（应当恰好 1 次）" % t.count("var mapHit"))
    need('" mapped=" + stats.mapped' in t, "SUMMARY 没带 mapped")
    need('\', "mapped": \' + stats.mapped' in t, "报告 JSON 的 stats 里没有 mapped")
    need("映射表指向的文件不存在: " in t, "目标不存在时的原因文案丢了")
    need('how = "映射表";' in t, "命中映射没写 how=映射表")


@check("render：空表 = `var MAPPING = {};`，与不配映射完全等价")
def _():
    a = _render()
    b = _render(mapping={})
    need("var MAPPING = {};" in a, "空表没渲染成 {}：%r" % ([l for l in a.splitlines() if "MAPPING" in l],))
    need(a == b, "不传 mapping 与传空表渲染结果不一致")


@check("validate：带映射的 JSX 过三闸（占位符零残留 / 无 ES3 保留字）")
def _():
    t = _render(mapping={"旧名字.mp4": "D:/素材/新名字.mp4"})
    problems = jsx.validate(t)
    need(not problems, "自检没过：%r" % (problems,))
    need(jsx.residual_placeholders(t) == [], "还有占位符没填")


@check("对拍口径：KNOWN_DIFFS 能把 1.1.0 的新增整段删回 v1.9.8 的样子")
def _():
    t = _render()
    for new_bit, old_bit in jsx.KNOWN_DIFFS:
        need(new_bit in t, "登记片段没出现在渲染结果里：%r" % (new_bit[:50],))
        t = t.replace(new_bit, old_bit, 1)
    need("MAPPING" not in t, "删完还剩 MAPPING：说明有片段漏登记")
    need("mapped" not in t, "删完还剩 mapped：说明有片段漏登记")
    need("1.1.0 手工映射表" not in t, "删完还剩注释块")


# ---------------------------------------------------------------- 5. 不越界

@check("reports：老报告缺 mapped 键 -> 兜底 0，六个老字段一字不变")
def _():
    d = _tmpdir()
    p = os.path.join(d, "r.json")
    _write(p, json.dumps({"stats": {"total": 32, "ok": 0, "fixed": 21,
                                    "missing": 11, "ambiguous": 1, "zeroByte": 0}}))
    st = reports.parse_report(p)["stats"]
    expect = {"total": 32, "ok": 0, "fixed": 21, "missing": 11, "ambiguous": 1, "zeroByte": 0}
    need(dict((k, st.get(k)) for k in expect) == expect, "老字段被改了：%r" % (st,))
    need(st.get("mapped") == 0, "mapped 没兜底成 0：%r" % (st.get("mapped"),))


@check("reports：新报告的 mapped 读得出来；结论句只在 mapped>0 时多一句")
def _():
    d = _tmpdir()
    p = os.path.join(d, "r.json")
    _write(p, json.dumps({"stats": {"total": 5, "ok": 3, "fixed": 2, "missing": 0,
                                    "ambiguous": 0, "zeroByte": 0, "mapped": 2}}))
    st = reports.parse_report(p)["stats"]
    need(st.get("mapped") == 2, "mapped 没读出来：%r" % (st.get("mapped"),))
    line = reports.summary_line(st)
    need("2 条是你指定替身接上的" in line, "结论句没带映射：%r" % (line,))
    base = reports.summary_line({"fixed": 21, "missing": 11, "ambiguous": 1, "mapped": 0})
    need(base == "接上 21 条，还缺 11 条，另有 1 条是按同名文件猜的，可能要核对",
         "没映射时结论句变了（老用户会以为版本退回）：%r" % (base,))


@check("CLI：--mapping 能解析；缺值报参数错；Options 默认空")
def _():
    opt = runner.parse_cli([r"D:\ae\again.aep", "--mapping", r"D:\ae\map.json"])
    need(opt["mapping"] == r"D:\ae\map.json", "--mapping 没解析出来：%r" % (opt.get("mapping"),))
    need(runner.parse_cli([r"D:\ae\again.aep"])["mapping"] == "", "默认应当是空（走默认位置）")
    try:
        runner.parse_cli([r"D:\ae\again.aep", "--mapping"])
    except ValueError:
        pass
    else:
        raise AssertionError("--mapping 后面缺文件时应当报参数错")
    need(runner.Options().mapping_path == "", "Options 默认 mapping_path 应当是空串")
    need("--mapping" in runner.USAGE, "用法里没写 --mapping")


@check("merge：别人新增的条目必须保留（这就是并发保护本身）")
def _():
    base = {"a.mp4": "D:/x/a.mp4"}
    mine = {"a.mp4": "D:/x/a.mp4", "b.mp4": "D:/x/b.mp4"}
    disk = {"a.mp4": "D:/x/a.mp4", "c.mp4": "D:/x/c.mp4"}   # 命令行中间加的
    out, st = mapping.merge(base, mine, disk)
    need("c.mp4" in out, "别处新增的 c.mp4 被吃掉了：%r" % (out,))
    need(out.get("b.mp4") == "D:/x/b.mp4", "自己新增的 b.mp4 丢了：%r" % (out,))
    need(st["kept"] == 1, "统计应当说保留了 1 条别人的，实得 %r" % (st,))
    need(st["total"] == 3, "合并后应当 3 条，实得 %r" % (st,))


@check("merge：我删掉的不能被盘上的旧记录复活")
def _():
    base = {"a.mp4": "D:/a.mp4", "b.mp4": "D:/b.mp4"}
    mine = {"a.mp4": "D:/a.mp4"}          # 用户点了「删除所选」删掉 b
    disk = {"a.mp4": "D:/a.mp4", "b.mp4": "D:/b.mp4"}
    out, st = mapping.merge(base, mine, disk)
    need("b.mp4" not in out, "删掉的 b.mp4 又活了：%r（用户会以为删除按钮坏了）" % (out,))
    need(st["removed"] == 1, "统计应当说删了 1 条，实得 %r" % (st,))


@check("merge：同一个键两边都改 -> 以「刚动手的这次」为准")
def _():
    out, st = mapping.merge({"a.mp4": "D:/old.mp4"}, {"a.mp4": "D:/new.mp4"},
                            {"a.mp4": "D:/other.mp4"})
    need(out["a.mp4"] == "D:/new.mp4", "冲突时应当以本次为准，实得 %r" % (out["a.mp4"],))
    need(st["kept"] == 0, "冲突不该算「保留别人的」：%r" % (st,))


@check("save_merged：端到端落盘后两条都在，且不留 .tmp")
def _():
    d = tempfile.mkdtemp(prefix="aemd_mapmerge_")
    try:
        p = os.path.join(d, "mapping.json")
        mapping.save({"a.mp4": "D:/a.mp4"}, p)
        base = mapping.load(p)                       # 界面打开时看到的
        mapping.save({"a.mp4": "D:/a.mp4", "c.mp4": "D:/c.mp4"}, p)   # 命令行中间加了一条
        merged, st = mapping.save_merged({"a.mp4": "D:/a.mp4", "b.mp4": "D:/b.mp4"}, base, p)
        disk = mapping.load(p)
        need(set(disk) == {"a.mp4", "b.mp4", "c.mp4"},
             "落盘结果应当三条都在，实得 %r" % (sorted(disk),))
        need(merged == disk, "返回值与盘上不一致")
        need(st["kept"] == 1, "统计应当保留 1 条：%r" % (st,))
        need(not os.path.exists(p + ".tmp"), "留下了 .tmp 临时文件")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    print("拾链 1.1.0 —— 手工映射表回归（tests/test_mapping.py）")
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
