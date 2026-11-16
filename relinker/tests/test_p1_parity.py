# -*- coding: utf-8 -*-
"""P1 对拍：v2 的 scanner / jsx / reports / everything 与 v1.9.8 逐字节 / 逐值比对。

跑法（在 media-doctor-v2 目录下）：

    python -m tests.test_p1_parity

为什么要对拍而不是"看代码对不对"：这四块是从一代搬过来的，行为必须与用户
已经用了一个多月的 v1.9.8 **完全一致**（差一个字节的 JSX 就可能让 AE 读不到脚本）。
所以参照物不是"我以为应该怎样"，而是**把 v1.9.8 的 app.py 当只读库 import 进来**跑一遍。

四组对拍：
  A. jsx.render   ↔ app.build_jsx        同一组输入 → 生成的 JSX 文本逐字节相同
  B. scanner.short_key ↔ app._short_key  语料双向一致
  C. scanner.build_index ↔ app.build_media_index  同一棵临时树 → JSON 文件逐字节相同
  D. reports.count_relink_log ↔ app._count_relink_log  真实日志夹具 → 三个计数相同
  另加：reports.summary_line 复现真机那句话；everything.index_state 三态（桩掉 subprocess）
"""

import io
import importlib.util
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from aemd import console

console.harden()   # 默认 GBK 控制台下也不许因为符号打不出来而崩

V1_APP = r"C:\Users\李\Documents\deepseek-harness\default-workspace\ae-tools\media-doctor\app.py"
FIXTURE_DIR = r"D:\ae\别人工程\again (1)\again\.ae_doctor"
# v1.9.8 那一代把结果件留在工程根 —— 同一批数字（32/0/21/11/1）的另一个出处
LEGACY_FIXTURE_DIR = r"D:\ae\别人工程\again (1)\again"

PASS, FAIL, SKIPPED = [], [], []


def ok(name):
    PASS.append(name)
    print("  PASS  %s" % name)


def bad(name, why):
    FAIL.append((name, why))
    print("  FAIL  %s\n        %s" % (name, why))


def skip(name, why):
    SKIPPED.append((name, why))
    print("  SKIP  %s\n        %s" % (name, why))


class SkipTest(Exception):
    pass


def case(name):
    def deco(fn):
        try:
            fn()
            ok(name)
        except SkipTest as e:
            skip(name, str(e))
        except Exception as e:
            bad(name, "%s: %s" % (type(e).__name__, e))
        return fn
    return deco


def load_v1():
    """把 v1.9.8 的 app.py 当只读库加载（它 import tkinter 但不会建窗口）。"""
    spec = importlib.util.spec_from_file_location("v1_app_for_parity", V1_APP)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["v1_app_for_parity"] = mod
    spec.loader.exec_module(mod)
    return mod


APP = load_v1()


# ----------------------------------------------------------------- A. JSX 逐字节

def _apply_known_diffs(text):
    """把 v2 的已知差异替回 v1.9.8 原文（登记表在 `aemd.jsx.KNOWN_DIFFS`）。

    对拍的意义在于「除登记过的修复外一字不差」——放宽成「包含」或「相似」就等于放弃对拍。
    """
    from aemd import jsx
    out = text
    for new_bit, old_bit in jsx.KNOWN_DIFFS:
        if new_bit not in out:
            raise AssertionError("KNOWN_DIFFS 登记的 v2 片段没出现在模板里：%r" % (new_bit[:60],))
        out = out.replace(new_bit, old_bit, 1)
    return out


@case("A. jsx.render vs v1.9.8 build_jsx：生成的 JSX 逐字节相同")
def _():
    from aemd import jsx
    cases = [
        # (project, roots, dry, consolidate, cons_dir, save, out_dir, depth, cons_mode, work_dir, log_path)
        (r"D:\ae\again_fixed.aep", [r"D:\ae\素材", r"E:\素材 02"], False, False, "", True,
         r"D:\ae\.ae_doctor", 6, "copy", None, None),
        (r"D:\中文 工程\再次 (1).aep", [r"E:\素材\02\G-Xi 02补帧镜头包"],
         True, True, r"E:\素材\收编", False, r"D:\中文 工程\.ae_doctor", 3, "move", None, ""),
        ("C:/tmp/a.aep", [], False, True, "D:/cons", True, "C:/tmp/.ae_doctor", 10,
         "移动", r"C:\Users\x\AppData\Roaming\AE素材医生\work",
         r"C:\Users\x\AppData\Roaming\AE素材医生\logs\relink-a-20261115-150000.log"),
    ]
    old_ver = APP.APP_VER
    APP.APP_VER = "9.9.9"
    try:
        for i, (proj, roots, dry, cons, cdir, save, out, depth, mode, work, logp) in enumerate(cases, 1):
            v1 = APP.build_jsx(proj, roots, dry, cons, cdir, save, out, depth=depth,
                               cons_mode=mode, launch_ms=1750000000000, work_dir=work, log_path=logp)
            # 参照物先用 jsx.KNOWN_DIFFS 还原成 v1.9.8 的原样：登记的都是**真修过的 bug**
            # （D5 日志句柄、D6 TIME 起点）。不放宽任何阈值，只把这几处替回去再比字节。
            v2 = jsx.render(proj, roots, dry, cons, cdir, save, out, depth=depth,
                            cons_mode=mode, launch_ms=1750000000000, work_dir=work, log_path=logp,
                            app_ver="9.9.9")
            v2 = _apply_known_diffs(v2)
            if v1 != v2:
                for n, (a, b) in enumerate(zip(v1.splitlines(), v2.splitlines()), 1):
                    if a != b:
                        raise AssertionError("第 %d 例第 %d 行不同：v1=%r v2=%r" % (i, n, a, b))
                raise AssertionError("第 %d 例行数不同：v1=%d v2=%d"
                                     % (i, len(v1.splitlines()), len(v2.splitlines())))
            if len(v1.encode("utf-8")) != len(v2.encode("utf-8")):
                raise AssertionError("第 %d 例字节数不同" % i)
    finally:
        APP.APP_VER = old_ver


@case("A2. 真模板三闸：占位符零残留、无 ES3 保留字、node --check 通过")
def _():
    from aemd import jsx
    text = jsx.render(r"D:\ae\again.aep", [r"D:\ae"], False, False, "", True,
                      r"D:\ae\.ae_doctor", launch_ms=1750000000000)
    left = jsx.residual_placeholders(text)
    if left:
        raise AssertionError("占位符残留：%r" % (left,))
    viol = jsx.es3_violations(text)
    if viol:
        raise AssertionError("ES3 保留字：%r" % (viol[:3],))
    problems = jsx.validate(text, node_check=True)
    if problems:
        raise AssertionError("validate 报问题：%r" % (problems[:3],))
    # 反向：故意漏一个占位符，必须被抓到（D4 / F-10）
    broken = text.replace('maxDepth: 6', 'maxDepth: __DEPTH__')
    if not jsx.residual_placeholders(broken):
        raise AssertionError("漏填的 __DEPTH__ 没被 residual_placeholders 抓到")


# ----------------------------------------------------------------- B. short_key

SHORT_KEY_CORPUS = [
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
]


@case("B. scanner.short_key vs v1.9.8 _short_key：语料 15 条完全一致")
def _():
    from aemd import scanner
    diff = []
    for name in SHORT_KEY_CORPUS:
        a, b = APP._short_key(name), scanner.short_key(name)
        if a != b:
            diff.append((name, a, b))
    if diff:
        raise AssertionError("规则不一致：%r" % (diff[:3],))


# ----------------------------------------------------------------- C. 索引 JSON

@case("C. scanner.build_index vs v1.9.8 build_media_index：同一棵树 -> JSON 逐字节相同")
def _():
    from aemd import scanner
    d = tempfile.mkdtemp(prefix="aemd_idx_")
    try:
        tree = os.path.join(d, "素材")
        subs = [os.path.join(tree, "a", "b"), os.path.join(tree, "c"), os.path.join(tree, "中文目录")]
        for p in [tree] + subs:
            os.makedirs(p, exist_ok=True)
        names = [("clip1.mp4", tree), ("clip2.MOV", subs[0]), ("clip3.mp3", subs[0]),
                 ("中文素材.exr", subs[1]), ("still.PNG", subs[1]), ("readme.txt", subs[1]),
                 ("same_name_01.mp4", subs[2]), ("same_name_01.mov", subs[2]),
                 ("same.name.mp4", subs[2]), ("noext", subs[2])]
        for fn, where in names:
            io.open(os.path.join(where, fn), "wb").write(b"x")
        # 超出深度的目录（depth=2 时 subs 深度 2 里的 b 深度 3 不该被扫到）
        p1 = os.path.join(d, "v1.json")
        p2 = os.path.join(d, "v2.json")
        n1 = APP.build_media_index([tree], 2, p1)
        n2 = scanner.build_index([tree], 2, p2)
        if n1 != n2:
            raise AssertionError("收录条数不同：v1=%d v2=%d" % (n1, n2))
        b1, b2 = io.open(p1, "rb").read(), io.open(p2, "rb").read()
        if b1 != b2:
            j1, j2 = json.loads(b1.decode("utf-8")), json.loads(b2.decode("utf-8"))
            for k in ("count", "exact", "short"):
                if j1.get(k) != j2.get(k):
                    raise AssertionError("字段 %s 不同：v1=%r v2=%r" % (k, j1.get(k), j2.get(k)))
            raise AssertionError("JSON 字节不同但字段相同（键顺序/空格差异）")
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ----------------------------------------------------------------- D. 报告与日志

@case("D. reports.count_relink_log vs v1.9.8 _count_relink_log：真机日志夹具一致")
def _():
    from aemd import reports
    log = r"C:\Users\李\AppData\Roaming\AE素材医生\logs\relink-again-20261115-144225.log"
    if not os.path.isfile(log):
        raise AssertionError("日志夹具不见了：%s" % log)
    a = APP._count_relink_log(log)
    b = reports.count_relink_log(log)
    if tuple(a) != tuple(b):
        raise AssertionError("计数不同：v1=%r v2=%r" % (a, b))
    if b != (21, 0, 11):
        raise AssertionError("真机数字对不上：%r（应为 接上21 / 在线0 / 缺11）" % (b,))


@case("D2. reports 解析真机报告：stats 与 summary_line 复现「21 / 11 / 1」")
def _():
    from aemd import reports
    # 夹具位置有两个：v2 的 .ae_doctor\ 与 v1.9.8 留在工程根的老文件。
    # 2026-11-15 16:43 那次真机回归在开跑时清掉了 .ae_doctor（clear_state 会清旧结果），
    # 只剩工程根这一份 —— 两份都能复现同一批数字，所以按优先级找，都没有就跳过（不自己编夹具）。
    cand = [FIXTURE_DIR, LEGACY_FIXTURE_DIR]
    fdir = ""
    for d in cand:
        if os.path.isfile(os.path.join(d, "_relink_report.json")):
            fdir = d
            break
    if not fdir:
        raise SkipTest("没有真机报告夹具（找过 %s）—— 跑一轮真修复后会生成，先跳过"
                       % "、".join(cand))
    rep = reports.parse_report(os.path.join(fdir, "_relink_report.json"))
    if rep is None:
        raise AssertionError("报告解析返回 None（夹具在 %s）" % fdir)
    st = rep["stats"]
    expect = {"total": 32, "ok": 0, "fixed": 21, "missing": 11, "ambiguous": 1, "zeroByte": 0}
    # 1.1.0 起 stats 多一个 mapped；老报告没有这个键 → 必须补 0（不是缺键、不是 None）。
    # 只比这六个老字段，再说清 mapped 的来源，别把新增字段当成对拍失败。
    got = dict((k, st.get(k)) for k in expect)
    if got != expect:
        raise AssertionError("stats 不同：%r" % (st,))
    if st.get("mapped") != 0:
        raise AssertionError("老报告的 mapped 必须兜底成 0，实际 %r（stats=%r）" % (st.get("mapped"), st))
    line = reports.summary_line(st)
    want = "接上 21 条，还缺 11 条，另有 1 条是按同名文件猜的，可能要核对"
    if line != want:
        raise AssertionError("summary_line 不一致：%r" % (line,))
    if reports.log_writable(rep) is not True:
        raise AssertionError("logWritable 应为 True，实际 %r" % (reports.log_writable(rep),))
    miss = reports.parse_missing(os.path.join(fdir, "_relink_missing.txt"))
    if len(miss) != 11:
        raise AssertionError("缺件解析出 %d 条（应为 11）" % len(miss))
    if not miss[0]["name"].endswith("_6fac5f.mp4") or not miss[0]["original"].startswith("E:\\素材\\02\\"):
        raise AssertionError("缺件首条字段不对：%r" % (miss[0],))


@case("D3. reports 兜底：坏 JSON / 缺文件 / 老位置报告都不抛")
def _():
    from aemd import reports
    d = tempfile.mkdtemp(prefix="aemd_rep_")
    try:
        p = os.path.join(d, "_relink_report.json")
        io.open(p, "w", encoding="utf-8").write("{ not json")
        if reports.parse_report(p) is not None:
            raise AssertionError("坏 JSON 应该返回 None")
        io.open(p, "w", encoding="utf-8").write("[1,2,3]")
        if reports.parse_report(p) is not None:
            raise AssertionError("JSON 是数组应该返回 None")
        if reports.parse_report(os.path.join(d, "nope.json")) is not None:
            raise AssertionError("文件不存在应该返回 None")
        io.open(p, "w", encoding="utf-8").write('{"relinked": 3}')
        rep = reports.parse_report(p)
        if rep["stats"]["fixed"] != 0 or rep["missing"] != []:
            raise AssertionError("缺 stats/missing 时兜底不对：%r" % (rep,))
        # 老位置（工程根）也能找到
        os.makedirs(os.path.join(d, "old"), exist_ok=True)
        io.open(os.path.join(d, "old", "_relink_report.json"), "w", encoding="utf-8").write("{}")
        if reports.find_report(os.path.join(d, "sd"), os.path.join(d, "old")) is None:
            raise AssertionError("老位置报告没找到")
        if reports.find_report(os.path.join(d, "sd"), os.path.join(d, "nope")) is not None:
            raise AssertionError("都没有时应该返回 None")
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ----------------------------------------------------------------- E. Everything 三态

@case("E. everything.index_state 三态（桩掉 subprocess，绝不真跑 es.exe）")
def _():
    from aemd import everything

    class R:
        def __init__(self, code, out=b""):
            self.returncode, self.stdout = code, out

    real = everything.subprocess.run
    try:
        cases = [
            # (returncode, stdout, 期望)
            (0, b"1384871\n", everything.IndexState.OK),
            (0, b"0\n", everything.IndexState.EMPTY),
            (8, b"Error 8: Everything IPC not found.\n", everything.IndexState.UNKNOWN),
            (0, b"", everything.IndexState.UNKNOWN),
            (0, b"not a number\n", everything.IndexState.UNKNOWN),
            (1, b"12\n", everything.IndexState.UNKNOWN),
        ]
        for code, out, want in cases:
            everything.subprocess.run = lambda *a, _c=code, _o=out, **k: R(_c, _o)
            got = everything.index_state("es.exe")
            if got is not want:
                raise AssertionError("退出码 %r 输出 %r → %r（期望 %r）" % (code, out, got, want))

        def boom(*a, **k):
            raise OSError("es.exe 不见了")
        everything.subprocess.run = boom
        if everything.index_state("es.exe") is not everything.IndexState.UNKNOWN:
            raise AssertionError("起不来时必须是 UNKNOWN（不能是 EMPTY，否则会弹 UAC）")
    finally:
        everything.subprocess.run = real


@case("E2. 服务≠客户端：合成进程里服务被两道判据都排除")
def _():
    from aemd import everything
    real_cim, real_alive, real_exe = everything._cim_everything, everything.winproc.is_alive, everything.bundled_everything
    exe = r"C:\Users\x\AppData\Roaming\AE素材医生\engine\Everything\everything.exe"
    rows = [
        {"pid": 6288, "session": 0, "path": exe, "cmdline": '"%s" -svc' % exe},   # 服务
        {"pid": 7777, "session": 1, "path": exe, "cmdline": '"%s" -startup -minimized' % exe},  # 客户端
        {"pid": 8888, "session": 1, "path": r"C:\Program Files\Everything\everything.exe",
         "cmdline": "everything.exe"},                                            # 用户自己装的
    ]
    try:
        everything._cim_everything = lambda: rows
        everything.winproc.is_alive = lambda pid: True
        everything.bundled_everything = lambda: exe
        if everything.client_pids() != [7777]:
            raise AssertionError("client_pids 应只返回 7777，实际 %r" % (everything.client_pids(),))
        if everything.service_pids() != [6288]:
            raise AssertionError("service_pids 应只返回 6288，实际 %r" % (everything.service_pids(),))
    finally:
        everything._cim_everything, everything.winproc.is_alive, everything.bundled_everything = real_cim, real_alive, real_exe


def main():
    print("P1 对拍：v2 vs v1.9.8（参照物是 2026-11-15 已部署的 v1.9.8 app.py）")
    print("")
    print("%d PASS · %d SKIP · %d FAIL" % (len(PASS), len(SKIPPED), len(FAIL)))
    print("")
    if FAIL:
        for n, w in FAIL:
            print("  FAIL  %s —— %s" % (n, w))
        print("")
        print("FAIL")
        return 1
    if SKIPPED:
        for n, w in SKIPPED:
            print("  SKIP  %s —— %s" % (n, w))
        print("")
    print("ALL PASS（%d 组对拍一致，%d 组因缺夹具跳过）" % (len(PASS), len(SKIPPED)))
    return 0


if __name__ == "__main__":
    sys.exit(main())