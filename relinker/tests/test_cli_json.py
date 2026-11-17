# -*- coding: utf-8 -*-
"""tests/test_cli_json.py —— CLI 的 `--json`（1.1.7）。

为什么要这个：批处理 / CI / 别的工具要接拾链，就得有一个**稳定**的 stdout，
而现在的 stdout 是给人看的日志（结论句、路径、提示全混在一起）。
`--json` 把 stdout 留给一坨 JSON，日志改走 stderr —— 于是 `拾链 --cli x.aep --json | jq .`
才成立。

三条要证明的事：
  A. 参数错：stdout 只有 JSON（带 code/error），用法与日志都在 stderr，退出码仍 2
  B. 键名稳定：result_json 的字段是契约，改键等于断别人的脚本
  C. 不加 --json 时一个字都不变（老调用方零影响）

跑法：`python -m tests.test_cli_json`（退出码 0 = 全过）。
"""

import contextlib
import io
import json
import os
import sys

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


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = runner.run_cli(argv)
    return code, out.getvalue(), err.getvalue()


@check("A --json 且参数错：stdout 只有 JSON，用法落在 stderr，退出码还是 2")
def _():
    code, out, err = _run(["--json", "--definitely-not-a-flag"])
    need(code == 2, "退出码应当是 2，实得 %r" % (code,))
    lines = [x for x in out.strip().splitlines() if x.strip()]
    need(len(lines) == 1, "stdout 应当只有一行 JSON，实得 %d 行：%r" % (len(lines), out[:300]))
    doc = json.loads(lines[0])
    need(doc.get("ok") is False, "失败时 ok 必须为 false：%r" % (doc,))
    need(doc.get("code") == 2, "code 应当是 2：%r" % (doc,))
    need("参数有问题" in (doc.get("error") or ""), "error 要说清原因：%r" % (doc,))
    need("--cli" in err, "用法应当打在 stderr：%r" % (err[:200],))
    need("用法" not in out, "stdout 里混进了给人看的日志")


@check("B result_json：字段名是契约（stats / mapped / missing / warnings / 路径）")
def _():
    class Res(object):
        summary = "接上 2 条，还缺 1 条"
        stats = {"total": 3, "ok": 0, "fixed": 2, "missing": 1,
                 "ambiguous": 0, "zeroByte": 0, "mapped": 1}
        missing = [{"name": "a.mp4", "original": "E:/a.mp4", "reason": "本地无同名文件"}]
        warnings = ["SEQ-OFFSET x | 帧号 0005 -> 0001"]
        report_path = "D:/p/.ae_doctor/_relink_report.json"
        log_path = "D:/logs/x.log"
        missing_path = "D:/p/.ae_doctor/_relink_missing.txt"
        elapsed = 12.34

    doc = runner.result_json(Res())
    need(doc["ok"] is True and doc["code"] == 0, "成功时 ok/code 不对：%r" % (doc,))
    need(doc["mapped"] == 1, "mapped 要单独给一份（批处理最常看这个）")
    need(doc["stats"]["fixed"] == 2, "stats 要原样带出来")
    need(doc["missing"] == [{"name": "a.mp4", "original": "E:/a.mp4"}],
         "missing 只带 name/original，多余的 reason 不该漏出去：%r" % (doc["missing"],))
    need(doc["warnings"] == ["SEQ-OFFSET x | 帧号 0005 -> 0001"], "warnings 要原样带")
    for k in ("report", "log", "missing_txt", "elapsed_s"):
        need(k in doc, "缺字段 %s" % k)
    need(doc["elapsed_s"] == 12.3, "elapsed_s 保留一位小数：%r" % (doc["elapsed_s"],))
    # 必须能直接 json.dumps（有不可序列化对象就说明漏了转换）
    json.dumps(doc, ensure_ascii=False)


@check("C 不加 --json：行为与以前一模一样（stdout 照旧是日志）")
def _():
    code, out, err = _run(["--definitely-not-a-flag"])
    need(code == 2, "退出码应当还是 2，实得 %r" % (code,))
    need("参数有问题" in out, "老模式下提示应当还在 stdout：%r" % (out[:200],))
    need(err.strip() == "", "老模式不该往 stderr 写东西：%r" % (err[:200],))


@check("D parse_cli：--json 与 --only-missing 可以同时给")
def _():
    opt = runner.parse_cli(["--json", "--only-missing", "x.aep"])
    need(opt["json"] is True and opt["only_missing"] is True, "两个开关要能共存：%r" % (opt,))
    need(opt["project"].endswith("x.aep"), "工程路径没解析出来：%r" % (opt["project"],))
    need(runner.parse_cli(["x.aep"])["json"] is False, "默认必须是 False（不改变老行为）")


def main():
    print("拾链 —— CLI --json（tests/test_cli_json.py）")
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
