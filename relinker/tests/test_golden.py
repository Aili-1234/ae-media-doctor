# -*- coding: utf-8 -*-
"""L1 单元测试（P0 版）：配置写入与 v1.9.8 **逐字节对拍**。

    python -m tests.test_golden          # 在 media-doctor-v2 目录下跑
    python tests/test_golden.py          # 直接跑也行

为什么要拿真 v1.9.8 当参照，而不是"我自己觉得对"：
  merge_cfg 的正确性不是"逻辑看起来对"，而是**产物字节相同**。
  同一个初始文件、同一串合并操作，v1.9.8 与 v2.0 写出来的 settings.json
  必须一个字都不差（包括缩进、键序、ensure_ascii=False 的中文原样、有没有末尾换行）。
  差一个字节就说明 v2 改了某个行为细节 —— 那必须在 P1 之前解释清楚或改回去。

做法：
  · 用 importlib 从磁盘加载 v1.9.8 的 app.py（只读；它 import 时不建窗口）
  · 两边各自把 `cfg_path` 指到独立临时文件
  · 两边把 `APP_VER` 都改成同一个值（否则 version 字段天然不同，比的是噪声）
  · 跑同一串操作 → 比较最终文件字节 + 返回 dict
"""

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
V2 = os.path.dirname(HERE)                      # media-doctor-v2
REWRITE = os.path.dirname(V2)                   # ae-tools\rewrite
AE_TOOLS = os.path.dirname(REWRITE)             # ae-tools
V198_APP = os.path.join(AE_TOOLS, "media-doctor", "app.py")

sys.path.insert(0, V2)

import aemd.cfg as v2cfg                        # noqa: E402
from aemd import console                        # noqa: E402

console.harden()   # 默认 GBK 控制台下也不许因为符号打不出来而崩

PINNED_VER = "9.9.9"                            # 两边统一成同一个版本号再比


def load_v198():
    """把 v1.9.8 的 app.py 当成一个模块加载进来（只读、不启动界面）。"""
    if not os.path.isfile(V198_APP):
        raise RuntimeError("找不到 v1.9.8 参照实现：%s" % V198_APP)
    spec = importlib.util.spec_from_file_location("aemd_golden_v198", V198_APP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CASES = [
    # (名字, 初始文件内容 or None, 操作序列)
    ("空文件 + 一次合并", None, [{"dry_run": True}]),
    ("已有未知键 + 一次合并",
     '{\n  "some_unknown_key": "keep-me",\n  "dirs": ["D:\\\\素材"]\n}',
     [{"close_ae": False}]),
    ("坏 JSON + 一次合并", "{ this is not json", [{"a": 1}]),
    ("JSON 是数组不是对象", "[]", [{"a": 1}]),
    ("连续三次合并（模拟界面反复 _persist）", None,
     [{"dry_run": True}, {"close_ae": False}, {"dry_run": False}]),
    ("中文键值 + 反斜杠路径", None,
     [{"素材目录": ["D:\\\\素材 中文\\\\(1)"], "dry_run": True}]),
    ("同名键覆盖 + 新键追加",
     '{\n  "dry_run": true,\n  "old": 1\n}', [{"dry_run": False, "new": 2}]),
]


def run_case(v198, name, initial, ops):
    """跑一个用例，返回 (两边的最终字节, 两边返回的 dict)。"""
    d = tempfile.mkdtemp(prefix="aemd_golden_")
    try:
        pA = os.path.join(d, "v198.json")     # v1.9.8 写这个
        pB = os.path.join(d, "v2.json")       # v2.0 写这个
        for p in (pA, pB):
            if initial is None:
                if os.path.exists(p):
                    os.remove(p)
            else:
                io.open(p, "w", encoding="utf-8").write(initial)

        old_path_198 = v198.cfg_path
        old_ver_198 = v198.APP_VER
        old_path_v2 = v2cfg.cfg_path
        old_ver_v2 = v2cfg.APP_VER
        v198.cfg_path = lambda: pA
        v198.APP_VER = PINNED_VER
        v2cfg.cfg_path = lambda: pB
        v2cfg.APP_VER = PINNED_VER
        try:
            retA = retB = None
            for updates in ops:
                retA = v198.merge_cfg(dict(updates))
                retB = v2cfg.merge_cfg(dict(updates))
        finally:
            v198.cfg_path = old_path_198
            v198.APP_VER = old_ver_198
            v2cfg.cfg_path = old_path_v2
            v2cfg.APP_VER = old_ver_v2

        def _bytes(p):
            try:
                return io.open(p, "rb").read()
            except IOError:
                return b"<missing>"

        return _bytes(pA), _bytes(pB), retA, retB
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_merge_cfg_parity():
    v198 = load_v198()
    fails = []
    for name, initial, ops in CASES:
        a, b, retA, retB = run_case(v198, name, initial, ops)
        if a != b:
            fails.append("%s：文件字节不同\n  v1.9.8=%r\n  v2.0  =%r" % (name, a, b))
            continue
        if retA != retB:
            fails.append("%s：返回值不同\n  v1.9.8=%r\n  v2.0  =%r" % (name, retA, retB))
            continue
        # 附加自检：v2 自己还要满足 R11 的语义断言
        if name.startswith("已有未知键"):
            obj = json.loads(b.decode("utf-8"))
            if obj.get("some_unknown_key") != "keep-me":
                fails.append("%s：R11 破了 —— 未知键丢了：%r" % (name, obj))
        print("  PASS  %s（%d 字节，%d 次合并）" % (name, len(a), len(ops)))
    return fails


def test_merge_cfg_only_entry():
    """§8：写配置只能走 merge_cfg / save，直写函数必须带下划线（对外不可见）。"""
    fails = []
    public = [n for n in dir(v2cfg) if not n.startswith("_")]
    if "save_cfg" in public:
        fails.append("cfg 里还有公开的 save_cfg（整份覆盖入口）：%r" % public)
    if "save" not in public or "merge_cfg" not in public:
        fails.append("cfg 缺少 save / merge_cfg：%r" % public)
    if not hasattr(v2cfg, "_dump_raw"):
        fails.append("cfg 缺少 _dump_raw（真正落盘的那一步）")
    else:
        print("  PASS  写配置只有 merge_cfg/save 两个入口，落盘在 _dump_raw（私有）")
    return fails


def main():
    print("L1 test_golden —— 配置写入与 v1.9.8 逐字节对拍")
    print("参照实现：%s" % V198_APP)
    fails = []
    fails += test_merge_cfg_parity()
    fails += test_merge_cfg_only_entry()
    print("")
    if fails:
        print("FAIL %d 项：" % len(fails))
        for f in fails:
            print("  - %s" % f)
        return 1
    print("ALL PASS（%d 个用例 + 1 项结构断言）" % len(CASES))
    return 0


if __name__ == "__main__":
    sys.exit(main())