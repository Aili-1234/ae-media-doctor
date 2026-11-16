# -*- coding: utf-8 -*-
"""aemd.cfg —— settings.json 的读写（R11：先读后写，未知键永远不丢）。

负责：
  · `load_cfg()` / `save_cfg()` / `merge_cfg()` 三个函数，就是全部
  · `merge_cfg()` 是**唯一对外的写入口**：先读旧文件 → update 本次键 → 整份写回。
  · `App._persist()` 之类的界面动作必须走 merge_cfg，不许自己组装 dict 覆盖。

不负责：
  · 任何路径解析（那是 aemd.paths）
  · 判断配置项的业务语义（哪些开关还在用、默认值多少 → 调用方）
  · 报告落盘（reports.py）

依赖：`.const`（APP_NAME）、`.`（APP_VER，写进 version 字段）

⚠ 对拍口径：本文件的三个函数必须与 v1.9.8 的 `app.load_cfg/save_cfg/merge_cfg`
行为**逐字节一致**（tests/test_parity.py 拿真 v1.9.8 的 app.py 当参照）。
所以 `io.open(..., encoding="utf-8")` 与 `json.dump(..., ensure_ascii=False, indent=2)`
的参数一个字都别改 —— 它们是产物的字节形状。
"""

import io
import json
import os

from . import APP_VER
from .const import DATA_DIR_NAME


def cfg_dir():
    """配置目录：%APPDATA%\\AE素材医生\\（不存在就建；建不出来也返回路径）。"""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, DATA_DIR_NAME)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def cfg_path():
    return os.path.join(cfg_dir(), "settings.json")


def load_cfg():
    """读配置；文件不存在、坏了、不是 dict —— 一律返回 {}，绝不抛。

    ⚠ 2026-11-15 P0：**必须能读带 BOM 的文件**。真机上出现过一次「配置被洗」：
    用户用 PowerShell 改过 settings.json（PS 5.1 默认写 UTF-8 **带 BOM**），
    而我们用 `encoding="utf-8"` 读 → `json.load` 抛 `Unexpected UTF-8 BOM`
    → 这里 except 成 `{}` → 界面按默认值建好之后又把默认值写了回去
    → project / dirs 全被清空。BOM 不是「坏文件」，是合法 JSON 的一种常见包装，
    读的一侧必须容错：`utf-8-sig` 对「有 BOM」和「没有 BOM」都能读。
    """
    try:
        with io.open(cfg_path(), encoding="utf-8-sig") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _dump_raw(d):
    """真正落盘的那一步。**不对外**（§8：save 只吃 merge_cfg 的返回值）。

    故意带下划线：谁想绕开 merge_cfg 直接覆盖配置，就得先敲一个下划线函数名 ——
    让这种意图在 review 时看得见。

    落盘方式（2026-11-15 P0）：**临时文件 + 原子替换**，UTF-8 无 BOM。
    旧实现直接 `open(path, "w")` —— 写到一半被杀就留下半截 JSON，下次读失败
    又会被当成「没有配置」，等于把用户设置洗成默认值。原子替换让文件要么是旧的、
    要么是新的，不存在半截状态。
    """
    path = cfg_path()
    tmp = path + ".tmp"
    try:
        # 注意：**不要**给 newline 传值。v1.9.8 用的是 `io.open(path, "w")`，
        # 在 Windows 上会把 "\n" 翻成 "\r\n"；对拍测试比的是文件字节，
        # 这里一旦写成 newline="\n"，七组用例全红（2026-11-15 实测）。
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def merge_cfg(updates):
    """把若干键合并进 settings.json —— 先读后写，绝不整份覆盖。（R11 / §8）

    v1.9.2 的坑：`_persist()` 每次开关变动都按「已知字段表」重写 settings.json，
    表里没有用户勾过的键（ae_conflict）就被抹掉，用户下次又得重新勾一遍。
    这里统一走合并写：**未知键一律保留**，只覆盖本次更新的那些。
    """
    cfg = load_cfg()
    if not isinstance(cfg, dict):
        cfg = {}
    cfg.update(updates or {})
    cfg["version"] = APP_VER
    _dump_raw(cfg)
    return cfg


def save(d):
    """§8 的对外名字：只接受 merge_cfg() 的返回值（或等价 dict），语义等同 merge。

    保留这个名字是为了让「写配置」只有一个入口，而入口的名字就叫 save。
    实现上仍然合并写 —— 直接覆盖的路径不存在。
    """
    return merge_cfg(d)


# ---------------------------------------------------------------- 枚举型配置项
#
# `consolidate_mode` 曾经把**界面显示文案**直接当值存进 settings.json：
#
#     "consolidate_mode": "复制到 Footages"
#
# 后果是「改一句界面文字 = 改一次磁盘格式」：文案一改，用户配置里的旧值就成了
# 不认识的值，要么读不出来、要么触发一次莫名其妙的迁移写。2026-11-15 那次
# 截图工具把用户配置写坏，根子就在这里。
#
# 现在落盘只认机器值 `off / copy / move`，显示文案由 ui 层翻译；读入一侧必须
# 兼容历史上出现过的三种中文值（以及更早的布尔 `consolidate`）。
# 注意：**只做归一化，不在这里落盘** —— 写入时机归 ui 层（用户真动过才写）。

CONS_MODES = ("off", "copy", "move")

# 历史值 → 机器值。键包含 2026-11-15 之前落过盘的三种中文文案。
_LEGACY_CONS_MODE = {
    "不收进": "off",
    "复制到 Footages": "copy",
    "移进 Footages": "move",
}

# 更宽松的前缀/子串判据（防止文案改过一两个字之后旧值读不出来）
_CONS_MODE_HINTS = (("移动", "move"), ("复制", "copy"), ("不收", "off"))


def consolidate_mode_value(raw, consolidate=None):
    """把任何历史形态的 `consolidate_mode` 归一成机器值 `off / copy / move`。

    参数：
      raw          —— settings.json 里读到的原值（可能是机器值、中文旧文案、
                      空串、None、甚至 True/False）
      consolidate  —— 早期只存布尔 `consolidate` 时的兜底依据（True→copy）

    判据顺序：机器值 → 历史中文表 → 前缀提示 → 布尔兜底 → `off`。
    永远返回三个机器值之一，绝不抛、绝不返回空。
    """
    if isinstance(raw, str):
        s = raw.strip()
        low = s.lower()
        if low in CONS_MODES:
            return low
        if s in _LEGACY_CONS_MODE:
            return _LEGACY_CONS_MODE[s]
        for hint, mode in _CONS_MODE_HINTS:
            if hint in s:
                return mode
    if isinstance(raw, bool):
        return "copy" if raw else "off"
    return "copy" if consolidate else "off"


__all__ = ["cfg_dir", "cfg_path", "load_cfg", "merge_cfg", "save",
           "CONS_MODES", "consolidate_mode_value"]