# -*- coding: utf-8 -*-
"""tests/test_project_plugins.py —— 解析 .aep 引用的效果插件（1.1.9）。"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aemd import console  # noqa: E402
console.harden()

from aemd import project_plugins  # noqa: E402

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


def _mk_aep(names):
    """造一个含 sspcfnam 字段的假 .aep（结构：sspcfnam + 4字节长度 + Utf8 + 4字节大端长度 + 名）。"""
    import struct
    buf = bytearray(b"\x00" * 16)          # 头部噪音
    for n in names:
        b = n.encode("utf-8")
        buf += b"sspcfnam" + struct.pack(">I", 0x10) + b"Utf8" + struct.pack(">I", len(b)) + b
    d = tempfile.mkdtemp()
    p = os.path.join(d, "proj.aep")
    open(p, "wb").write(bytes(buf))
    return p


@check("A. extract 提取 sspcfnam 字段里的插件显示名（去重保序）")
def _():
    p = _mk_aep(["Optical Flares", "Deep Glow", "Optical Flares", "CC Jaws"])
    got = project_plugins.extract(p)
    need(got == ["Optical Flares", "Deep Glow", "CC Jaws"], "提取不对：%r" % got)


@check("B. 非 .aep / 文件不存在 → 返回空列表，不抛")
def _():
    need(project_plugins.extract("/no/such/file.txt") == [], "非 aep 应返回空")
    need(project_plugins.extract("") == [], "空路径应返回空")


@check("C. third_party_of 能识别 BCC/S_/Optical 等第三方，且不误标官方")
def _():
    p = _mk_aep(["BCC 上色", "S_光线", "Optical Flares", "Shine",
                 "亮度和对比度", "马赛克", "CC Jaws", "曲线"])
    got = project_plugins.third_party_of(p, third_party_signs={
        "bcc", "opticalflares", "deepglow", "shine", "bokeh", "sapphire"})
    need("BCC 上色" in got, "BCC 没识别")
    need("S_光线" in got, "S_ 没识别")
    need("Optical Flares" in got, "Optical Flares 没识别")
    need("Shine" in got, "Shine 没识别")
    for official in ("亮度和对比度", "马赛克", "CC Jaws", "曲线"):
        need(official not in got, "官方插件被误标第三方：%s" % official)


@check("D. default_third_party_signs 至少含常见第三方前缀")
def _():
    signs = project_plugins.default_third_party_signs()
    for must in ("bcc", "sapphire", "opticalflares", "trapcode", "videocopilot"):
        need(must in signs, "缺第三方特征 %s" % must)


@check("E. 不误判官方：Transform/Format/Plugin 等通用词插件不算第三方")
def _():
    """回归护栏：form/plugin/element 这类短子串会误命中官方插件（Transform/Format/Plugin）。
    2026-11-17 抓到这个 bug —— 去掉危险短子串后，官方插件必须一个都不误判。"""
    signs = project_plugins.default_third_party_signs()
    for official in ("Transform", "Format", "Plugin", "Elementary", "Camera",
                     "Gradient Ramp", "Turbulent Displace", "亮度和对比度", "马赛克", "曲线"):
        need(not project_plugins._is_third_party(official, signs),
             "官方插件被误判成第三方：%s" % official)
    # 第三方仍要认得出
    for tp in ("BCC 上色", "S_光线", "Optical Flares", "Shine", "Particular", "Element 3D"):
        need(project_plugins._is_third_party(tp, signs),
             "第三方漏判：%s" % tp)


@check("F. missing_of：工程引用了但 AE 没装的才判缺失，缺失 0 返回空列表")
def _():
    """核心：显示名 ↔ 文件名 归一化匹配。BCC/S_ 没装 -> 缺失；Bokeh/DeepGlow/Optical 装了 -> 不缺失。"""
    p = _mk_aep(["BCC Brightness-Contrast", "S_Rays", "Bokeh", "Deep Glow",
                 "Optical Flares", "Shine"])
    installed = [r"C:\AE\Plug-ins\Bokeh.aex",
                 r"C:\AE\Plug-ins\DeepGlow2.aex",
                 r"C:\AE\Plug-ins\OpticalFlares.aex"]
    got = project_plugins.missing_of(p, installed_aex=installed,
                                     third_party_signs={"bcc", "sapphire", "bokeh",
                                                        "deepglow", "opticalflares", "shine"})
    need("BCC Brightness-Contrast" in got, "BCC 应判缺失")
    need("S_Rays" in got, "S_ 应判缺失")
    need("Shine" in got, "Shine 应判缺失")
    for present in ("Bokeh", "Deep Glow", "Optical Flares"):
        need(present not in got, "%s 已装却被判缺失" % present)


@check("G. missing_of：全部已装时返回空列表（缺失 0）")
def _():
    p = _mk_aep(["Bokeh", "Optical Flares"])
    installed = [r"C:\AE\Plug-ins\Bokeh.aex", r"C:\AE\Plug-ins\OpticalFlares.aex"]
    got = project_plugins.missing_of(p, installed_aex=installed,
                                     third_party_signs={"bokeh", "opticalflares"})
    need(got == [], "全部已装应返回空列表，实得 %r" % got)


def main():
    print("拾链 —— 工程插件解析（tests/test_project_plugins.py）")
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
