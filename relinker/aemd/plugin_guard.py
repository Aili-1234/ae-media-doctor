# -*- coding: utf-8 -*-
"""aemd.plugin_guard —— 修复期「禁用/恢复第三方 AE 插件」（1.1.9 方案：适配所有环境）。

背景（2026-11-17 真机反复验证）：
  只要软件**自动打开工程**，第三方插件（实测 Optical Flares，但也可能是任何别的）
  就会弹窗口把 AE 主线程卡死（CPU 归零、永久无响应）；而「先禁用第三方插件再开工程」
  就能正常打开 —— 用户手动打开能过，本质也是因为他那一下没触发插件窗口。

所以最普适、对任何用户零门槛的做法是：
  修复前，拾链**自动禁用所有第三方插件**（改名 `.aex` → `.aex.off`），
  让 AE 干净地启动、打开工程、跑脚本接素材；
  修复完成后，**恢复**这些插件（`.off` → `.aex`）。
  用户从头到尾只需要做一件事：选好问题工程、点开始。

为什么「禁第三方、保留官方」而不是「全禁」：
  `Effects/ Format/ Keyframe/ Extensions` 是 AE 自带的核心（禁了 AE 可能起不来），
  顶层那些 `.aex` 和 `Trapcode/ VideoCopilot/ RSMB6AE/ Cineware by Maxon` 才是第三方。

三条硬安全（都对应真实风险）：
  1. **改名前先落盘清单**（plugin_guard.json）：就算中途崩了，下次启动能按清单恢复，
     绝不把用户的插件留在 `.off` 状态「消失」。
  2. **只改名、不改内容、不删除**：`.aex` ↔ `.aex.off` 是可逆的元数据操作，
     最坏情况恢复不了，文件也还在（只是名字多了 `.off`）。
  3. **官方目录白名单**：只在「第三方」范围里动，官方插件一个都不碰。
"""

import io
import json
import os

from . import const

#: AE 官方/自带插件目录（这些**绝不动**，禁了 AE 可能起不来）。
#: 其余 Plug-ins 顶层 `.aex` 文件 + 非此白名单的子目录，都算第三方。
#: 注意 `Cineware by Maxon` 虽挂 Maxon 名，却是 AE 捆绑的渲染引擎（2026-11-14 随 AE 安装），
#: 禁了会影响 AE 打开含 Cineware 的工程 —— 归入「自带」。
OFFICIAL_PLUGIN_DIRS = frozenset(("effects", "format", "keyframe", "extensions",
                                   "(adobepsl)", "cineware by maxon"))

#: 清单文件名（放 %APPDATA%\AE素材医生\，跨进程可读，崩溃后靠它恢复）
GUARD_LIST_NAME = "plugin_guard.json"

#: 禁用后缀
DISABLE_SUFFIX = ".off"


def _guard_list_path():
    return os.path.join(const.APP_HOME, GUARD_LIST_NAME)


def plug_ins_dir(ae_exe):
    """从 AfterFX.exe 路径推出 Plug-ins 目录（不存在返回空串）。"""
    try:
        d = os.path.dirname(os.path.abspath(str(ae_exe)))
        return os.path.join(d, "Plug-ins")
    except (TypeError, ValueError):
        return ""


def common_plugins_dirs():
    r"""Adobe 共享插件目录（`Common\Plug-ins\7.0\MediaCore` 等），与 AE 版本无关。

    BCC（BorisFX Continuum）、Sapphire、Trapcode、Magic Bullet 等大量插件都装在这里，
    而不是 `Support Files\Plug-ins`。缺失判定必须扫这里，否则会把它们误判成「没装」。
    返回存在的目录列表（可能为空）。
    """
    out = []
    for adobe in const.ADOBE_DIRS:
        common = os.path.join(adobe, "Common", "Plug-ins")
        if not os.path.isdir(common):
            continue
        try:
            for ver in os.listdir(common):
                mc = os.path.join(common, ver, "MediaCore")
                if os.path.isdir(mc) and mc not in out:
                    out.append(mc)
        except OSError:
            continue
    return out


def _walk_aex(base):
    """递归列出 `base` 下所有 `.aex` 的完整路径（确定性排序）。"""
    out = []
    if not base or not os.path.isdir(base):
        return out
    for r, _d, fs in os.walk(base):
        for f in fs:
            if f.lower().endswith(".aex"):
                out.append(os.path.join(r, f))
    out.sort(key=lambda x: x.lower())
    return out


def all_installed_aex(ae_exe):
    r"""该 AE 能加载到的**全部** `.aex`（Support Files\Plug-ins + Common 共享目录）。

    用于「缺失判定」——判断工程引用的插件到底装没装。跟 `_third_party_aex`（只挑
    Support Files 里的第三方、用于禁用）是两回事：判定要全、禁用要准。
    """
    out = _walk_aex(plug_ins_dir(ae_exe))
    for mc in common_plugins_dirs():
        for p in _walk_aex(mc):
            if p not in out:
                out.append(p)
    out.sort(key=lambda x: x.lower())
    return out


def _third_party_aex(ae_exe):
    """该 AE 版本 Plug-ins 里**第三方** `.aex` 的完整路径列表（不含官方目录里的）。

    判据：顶层 `.aex` 文件 = 第三方；子目录里的 `.aex` 只有不在 OFFICIAL_PLUGIN_DIRS
    白名单里才算第三方。返回按路径排序的列表（确定性，方便测试与恢复）。
    """
    base = plug_ins_dir(ae_exe)
    if not base or not os.path.isdir(base):
        return []
    out = []
    try:
        names = os.listdir(base)
    except OSError:
        return []
    for name in names:
        p = os.path.join(base, name)
        if os.path.isfile(p) and name.lower().endswith(".aex"):
            out.append(p)
        elif os.path.isdir(p) and name.lower() not in OFFICIAL_PLUGIN_DIRS:
            for r, _d, fs in os.walk(p):
                for f in fs:
                    if f.lower().endswith(".aex"):
                        out.append(os.path.join(r, f))
    out.sort(key=lambda x: x.lower())
    return out


def _write_guard_list(items):
    """把「已禁用的 .off 路径」清单落盘（先写 .tmp 再 replace，防半写）。"""
    p = _guard_list_path()
    tmp = p + ".tmp"
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"off": items}, fh, ensure_ascii=False)
        os.replace(tmp, p)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _read_guard_list():
    """读回清单里的 `.off` 路径列表；读不到/坏内容返回 []。"""
    p = _guard_list_path()
    if not os.path.isfile(p):
        return []
    try:
        with io.open(p, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return [str(x) for x in (d.get("off") or []) if x]
    except (OSError, ValueError):
        return []


def _clear_guard_list():
    try:
        os.remove(_guard_list_path())
    except OSError:
        pass


def disable(ae_exe, log=None):
    """禁用该 AE 版本的所有第三方插件 → 返回被禁用的 `.off` 路径列表。

    已处于 `.off` 的不重复处理；清单覆盖式重写（每次都以最新一次为准）。
    任何单个改名失败都不抛 —— 跳过并记日志，保证「能禁多少禁多少」，剩下的
    交给 run 层的 plugin_block 点名。
    """
    emit = log or (lambda s, tag=None: None)
    aexs = _third_party_aex(ae_exe)
    offs = []
    for p in aexs:
        off = p + DISABLE_SUFFIX
        if os.path.isfile(off):
            offs.append(off)          # 已经是 .off（上一轮没恢复），直接记
            continue
        try:
            os.rename(p, off)
            offs.append(off)
        except OSError as e:
            emit("PLUGIN   禁用失败（跳过）：%s（%s）" % (os.path.basename(p), e), "warn")
    if offs:
        _write_guard_list(offs)
        emit("PLUGIN   已临时禁用 %d 个第三方插件（修复完会恢复）" % len(offs), "dim")
    return offs


def restore(log=None):
    """把清单里的 `.off` 恢复成 `.aex`，删清单。返回恢复个数。

    恢复是**幂等且尽力**的：每个 `.off` 都尝试改回，单个失败跳过；全部处理完才删清单。
    就算清单里某个文件已经不在（用户自己删了），也不影响其它恢复。
    """
    emit = log or (lambda s, tag=None: None)
    offs = _read_guard_list()
    n = 0
    for off in offs:
        if not off.endswith(DISABLE_SUFFIX):
            continue
        back = off[: -len(DISABLE_SUFFIX)]
        if os.path.isfile(off):
            try:
                os.rename(off, back)
                n += 1
            except OSError as e:
                emit("PLUGIN   恢复失败（跳过）：%s（%s）" % (os.path.basename(off), e), "warn")
        elif os.path.isfile(back):
            # 已经是 .aex（可能别处已恢复），也算恢复成功
            n += 1
    _clear_guard_list()
    if n:
        emit("PLUGIN   已恢复 %d 个插件" % n, "dim")
    return n


def recover_if_left(log=None):
    """启动时调用：上次若因崩溃没恢复，这里把残留的 `.off` 抢救回来。

    返回恢复个数（0 = 没有残留，正常）。这个函数的存在本身就是安全网 ——
    保证插件绝不会因为「跑一半崩了」而被永久留在禁用状态。
    """
    emit = log or (lambda s, tag=None: None)
    if not os.path.isfile(_guard_list_path()):
        return 0
    n = restore(log=emit)
    if n:
        emit("PLUGIN   检测到上次残留的禁用状态，已自动恢复 %d 个插件" % n, "warn")
    return n


__all__ = ["disable", "restore", "recover_if_left", "plug_ins_dir", "common_plugins_dirs",
           "all_installed_aex", "GUARD_LIST_NAME", "DISABLE_SUFFIX", "OFFICIAL_PLUGIN_DIRS"]
