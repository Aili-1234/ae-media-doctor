# -*- coding: utf-8 -*-
"""aemd.reports —— 报告解析与落盘（工程 `.ae_doctor\\` 里那三个文件）。

负责：
  · `parse_report(path)`：读 `_relink_report.json` → dict（total/ok/fixed/missing/ambiguous…）
  · `parse_missing(path)`：读 `_relink_missing.txt` → 缺件条目列表
  · `summary_line(st)`：把统计翻成给人看的一句话（「已重连 21 条 · 缺 11 条 · 另有 1 条按同文件名猜的」）
  · `find_report(...)`：按落点优先级找报告（新位置 `.ae_doctor` 优先，老位置兜底）
  · `count_relink_log(path)`：CLI / 超时结算「已经接上几条」用

不负责：
  · 写报告（**报告由 AE 里的 JSX 写**；本模块只读）
  · 打开文件夹、弹窗、生成清单（界面层的事）
  · 决定报告放在哪（paths）

依赖：`.const`（REPORT_* / STATE_DIRNAME）。**不碰** tkinter / winproc / everything。

⚠ 一代的坑（两条都在下面落成行为）：
  1. 报告曾直接写在工程根目录，把用户工程搞脏。v1.9.8 起只写 `<工程>\\.ae_doctor\\`，
     v2 沿用（R8 / U-06）。读的时候**必须兼容老位置**。
  2. `logWritable=false`（报告目录只读，日志没写成）时，一代会一直等日志出现，
     白等满 90 秒（F-14）。判据在 JSON 里，读到就必须信 JSON：见 `log_writable()`。
"""

import io
import json
import os

from . import const


def parse_report(path):
    """读报告 JSON → dict；文件不存在 / 坏 JSON / 不是对象 → None（**绝不抛**）。

    `stats` 子字典做了兜底：缺字段按 0 补齐，界面拿到的永远是 int，
    免得 `--` 与 0 混在一起（一代 KPI 就出现过 `None` 显示成 `--` 的分支）。
    """
    if not path or not os.path.isfile(path):
        return None
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    st = data.get("stats")
    norm = {}
    # 1.1.0 新增 mapped（按手工映射表接上的条数）。老报告没有这个键 → 补 0，
    # 界面与 CLI 拿到的永远是 int，不会出现 None / "--" 混进结论句。
    for k in ("total", "ok", "fixed", "missing", "ambiguous", "zeroByte", "mapped"):
        try:
            norm[k] = int((st or {}).get(k) or 0)
        except (TypeError, ValueError):
            norm[k] = 0
    data["stats"] = norm
    if not isinstance(data.get("missing"), list):
        data["missing"] = []
    return data


def log_writable(report):
    """F-14 / D2：报告里 `logWritable` 为 False ⇒ 别等日志，直接以 JSON 为准。

    返回 True / False / None（老版本报告没有这个键 → None = 不知道，按老路走）。
    """
    if not isinstance(report, dict):
        return None
    v = report.get(const.REPORT_KEY_LOG_WRITABLE)
    if v is None:
        return None
    return bool(v)


def parse_missing(path):
    """读缺件清单 → [{"name","original"}]；不存在 → []。

    文件格式（由 JSX 写，见夹具 `_relink_missing.txt`）：
      · 以 `#` 开头的两行是表头注释（时间和提示语），跳过
      · 其余每行 `名字<TAB>原路径`
    分隔符是 TAB 而不是空格 —— 文件名里带空格是常态（`Again & Again.mp3`）。
    """
    out = []
    if not path or not os.path.isfile(path):
        return out
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.rstrip("\r\n")
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                name, original = parts[0].strip(), "\t".join(parts[1:]).strip()
                if not name:
                    continue
                out.append({"name": name, "original": original})
    except OSError:
        return out
    return out


def summary_line(st):
    """统计 dict → 一句话。**文案的唯一出处**（界面、CLI、日志共用同一句）。

    与 v1.9.8 `App._finish` 逐字一致：
      `已重连 21 条 · 缺 11 条`
      + 有猜的才追加 ` · 另有 1 条按同文件名猜的`
    """
    st = st or {}
    line = "接上 %s 条，还缺 %s 条" % (st.get("fixed", "--"), st.get("missing", "--"))
    amb = st.get("ambiguous", 0) or 0
    if amb:
        line += "，另有 %s 条是按同名文件猜的，可能要核对" % amb
    # 1.1.0：有映射命中的才提一句。**默认不出这句** —— 老报告 / 没配映射的用户
    # 看到的字与 v1.9.8 逐字相同（对拍与 p3_ui 的文案断言都靠这一点）。
    mp = st.get("mapped", 0) or 0
    if mp:
        line += "，其中 %s 条是你指定替身接上的" % mp
    return line


def count_relink_log(log_path):
    """数日志行 → (接上 rel, 在线 ok, 缺 miss)。

    一行一素材。`RELINKED` / `OK` / `MISSING`、`FAILED` 三种前缀（失败也算缺）。
    文件不存在返回 (0, 0, 0) —— 调用方据此说「脚本还没开始」。
    """
    rel = ok = miss = 0
    if not log_path or not os.path.isfile(log_path):
        return rel, ok, miss
    try:
        with io.open(log_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("RELINKED"):
                    rel += 1
                elif line.startswith("OK"):
                    ok += 1
                elif line.startswith(("MISSING", "FAILED")):
                    miss += 1
    except OSError:
        pass
    return rel, ok, miss


def find_report(state_dir, out_dir=None, name=const.REPORT_JSON):
    """按优先级找报告：`state_dir` 优先，其次工程根老位置；都没有 → None。

    兼容老位置的理由见模块 docstring：用户手里可能有 v1.9.7 及以前跑出来的报告。
    注意返回值可能是老位置的文件 —— 调用方要自己看 mtime 决定能不能当本次结果用。
    """
    cands = []
    if state_dir:
        cands.append(os.path.join(state_dir, name))
    if out_dir:
        cands.append(os.path.join(out_dir, name))
    for p in cands:
        if os.path.isfile(p):
            return p
    return None