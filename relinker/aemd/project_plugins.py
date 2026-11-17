# -*- coding: utf-8 -*-
"""aemd.project_plugins —— 解析 .aep 工程引用了哪些**效果插件**（1.1.9）。

背景（用户需求）：修素材路径时，用户还想知道「这个工程依赖哪些插件、其中哪些是
第三方（可能没装/被禁用）」，免得修完素材才发现某个效果是缺失的 —— 让他**不用
打开第二遍去比对**。

怎么解析：`.aep` 是二进制，但效果插件的**显示名**以明文 UTF-8 存在 `sspcfnam`
字段里（结构：`sspcfnam` + 4 字节长度 + `Utf8`/`ANSI` + 4 字节大端字符串长度 + 字符串）。
真机验证：again.aep 里能干净地提取出 28 个插件名（Optical Flares / BCC 上色 / CC Jaws …）。

边界（诚实声明）：
  · 只认 `Utf8`/`ANSI` 两种编码标记，其它（UTF-16 等）跳过 —— 宁可漏，不解析错。
  · 返回的是**显示名**，与 `.aex` 文件名（match name）不是一回事，不做强行对应。
  · 本模块只做「提取 + 分官方/第三方」，不碰 AE、不碰界面。
"""

import os
import re
import struct

from . import const


def _is_third_party(name, third_party_signs):
    """按「第三方特征」判一个插件显示名是不是第三方。

    第三方插件名有明显的可识别特征：BCC_/S_（Sapphire）、Optical、Deep Glow、
    Bokeh、Shine、Trapcode 系列等。用调用方传入的特征列表（来自 AE 第三方 .aex 文件名
    归一化 + 常见第三方前缀）做包含匹配；匹配不上就按官方算（宁可漏报第三方，也不把
    官方误标成第三方吓唬用户）。

    特殊处理 `S_`（Sapphire）：它的显示名在中文本地化后是「S_光线」「S_投影」这种
    「S_ + 中文」，去空格后 `s_` 下划线还在、但后面是中文 —— 所以单独看「名字以 s_ 开头」。
    """
    low = (name or "").lower()
    compact = low.replace(" ", "").replace("_", "")
    if low.startswith("s_"):            # Sapphire：S_xxx（含中文本地化「S_光线」）
        return True
    for sign in third_party_signs:
        if sign and sign in compact:
            return True
    return False


#: 1.1.10：`.aep` 最多只读这么多个字节再找 sspcfnam。
#: 插件名全在工程前半段（真机 again.aep 4.7 MB，命中点都在 1 MB 内），
#: 整文件读会把 100 MB 级工程整个搬进内存 —— 那是一次白给的峰值。
MAX_SCAN_BYTES = 32 * 1024 * 1024


def extract(project_path):
    """从 .aep 提取引用的效果插件显示名 → 去重有序列表（按首次出现顺序）。

    返回 [] 表示「没解析到 / 不是 .aep / 文件读不了」—— 都不当错误，调用方降级为
    「不知道引用了哪些插件」。
    """
    path = str(project_path or "")
    if not path or os.path.splitext(path)[1].lower() != ".aep":
        return []
    try:
        with open(path, "rb") as fh:
            data = fh.read(MAX_SCAN_BYTES)
    except OSError:
        return []
    if not data:
        return []

    names, seen = [], set()
    idx = 0
    pat = b"sspcfnam"
    while True:
        i = data.find(pat, idx)
        if i < 0:
            break
        idx = i + len(pat)
        pos = i + len(pat) + 4          # 跳过 sspcfnam 后的 4 字节长度
        if pos + 4 > len(data):
            break
        tag = data[pos:pos + 4]
        if tag not in (b"Utf8", b"ANSI"):
            continue
        q = pos + 4
        if q + 4 > len(data):
            break
        ln = struct.unpack(">I", data[q:q + 4])[0]
        if not 0 < ln <= 200:
            continue
        raw = data[q + 4:q + 4 + ln]
        try:
            name = raw.decode("utf-8", "replace")
        except Exception:
            continue
        name = name.strip("\x00 \t\r\n")
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def third_party_of(project_path, third_party_signs=None):
    """工程里引用的**第三方**插件显示名列表（去重、保序）。

    `third_party_signs` 不传时用 AE 实际装的第三方 .aex 文件名归一化出来的特征，
    外加一批已知第三方前缀（Sapphire S_、BorisFX BCC 等）。
    """
    names = extract(project_path)
    if not names:
        return []
    if third_party_signs is None:
        third_party_signs = default_third_party_signs()
    return [n for n in names if _is_third_party(n, third_party_signs)]


def default_third_party_signs():
    """默认的第三方插件识别特征（小写、去空格）。

    两部分拼起来：
      · 常见第三方前缀/关键词（Sapphire、BorisFX Continuum、Trapcode、Video Copilot…）
      · 运行时从 AE Plug-ins 目录扫到的第三方 .aex 文件名（动态，适配不同机器）
    """
    signs = {
        "bcc", "sapphire", "opticalflares", "deepglow", "bokeh",
        "shine", "trapcode", "particular", "saber",
        "twitch", "nebulo", "tracery", "rsmb", "redgiant", "videocopilot",
        "boris", "continuum", "maxon", "cineware", "frischluft",
        "rowbyte", "revision", "element3d", "frischluftdepth",
    }
    # 动态补充：扫 AE 的第三方 .aex 文件名（去 .aex、去空格、小写）
    try:
        from . import aehost, plugin_guard
        for _name, exe in aehost.find_installs():
            base = plugin_guard.plug_ins_dir(exe)
            if not base or not os.path.isdir(base):
                continue
            try:
                entries = os.listdir(base)
            except OSError:
                continue
            for e in entries:
                low = e.lower()
                if low.endswith(".aex"):
                    signs.add(low[:-4].replace("_", "").replace(" ", ""))
    except Exception:
        pass
    return signs


def _norm(s):
    """归一化插件名，让「显示名 ↔ 文件名」能对上。

    例：`Deep Glow` → `deepglow`；`DeepGlow2.aex` → `deepglow`；`Optical Flares` → `opticalflares`。
    规则：去扩展名、去空格/下划线/连字符、去尾部数字后缀、小写。
    """
    s = os.path.basename(str(s or "")).lower()
    s = re.sub(r"\.(aex|plugin|bundle)$", "", s)
    s = re.sub(r"[\s_\-]", "", s)
    s = re.sub(r"\d+$", "", s)
    return s


def missing_of(project_path, installed_aex=None, third_party_signs=None):
    """工程引用了、但当前 AE 没装的**第三方插件** → 缺失插件名列表（去重、保序）。

    `installed_aex` 不传时，自动扫 AE 的全部 .aex（含 Common 共享目录）。

    匹配策略（针对「中文显示名 ↔ 英文文件名」这个鸿沟，按**插件套件**判，不逐字判）：
      · 显示名带 `BCC` 前缀 → 看 AE 有没有任何 BorisFX/BCC 文件（文件名 bcc 开头或路径含 borisfx）
      · 显示名带 `S_` 前缀 → 看 AE 有没有任何 Sapphire 文件（路径含 sapphire）
      · 其余 → 归一化全串匹配（Optical Flares ↔ opticalflares.aex）

    诚实边界：中文显示名（「上色」「光线」）无法和英文文件名（Colorize/Rays）逐字对应，
    所以这里判到「插件套件」粒度：`BCC 上色` 只要 AE 装了**任何** BCC 插件就算「已装」。
    这足以回答用户真正关心的「这套插件装没装」。
    """
    names = third_party_of(project_path, third_party_signs=third_party_signs)
    if not names:
        return []
    if installed_aex is None:
        try:
            from . import aehost, plugin_guard
            installed_aex = []
            for _name, exe in aehost.find_installs():
                installed_aex.extend(plugin_guard.all_installed_aex(exe))
        except Exception:
            installed_aex = []
    installed_norms = {_norm(x) for x in installed_aex if _norm(x)}
    installed_paths_low = {x.lower() for x in installed_aex}

    def _has_bcc():
        return any("borisfx" in p for p in installed_paths_low) or \
               any(n.startswith("bcc") for n in installed_norms)

    def _has_sapphire():
        return any("sapphire" in p for p in installed_paths_low)

    missing = []
    for name in names:
        low = (name or "").lower()
        hit = False
        if low.startswith("bcc"):
            hit = _has_bcc()
        elif low.startswith("s_"):
            hit = _has_sapphire()
        if not hit:
            n = _norm(name)
            hit = n in installed_norms
        if not hit:
            n = _norm(name)
            hit = any(n in ins or ins in n for ins in installed_norms
                      if len(ins) >= 4 and len(n) >= 4)
        if not hit:
            missing.append(name)
    return missing


__all__ = ["extract", "third_party_of", "missing_of", "default_third_party_signs"]

