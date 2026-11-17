# -*- coding: utf-8 -*-
"""aemd.paths —— 全部落点路径的唯一出口（R8 / R9 / §8「工程目录不许被搞脏」）。

四条落点，各归各位：

  %APPDATA%\\AE素材医生\\logs\\      运行日志（relink-<工程>-<时间戳>.log，留最新 50 个）
  %APPDATA%\\AE素材医生\\work\\      临时件（jsx 副本、素材索引；跑完删、失败留）
  <工程目录>\\.ae_doctor\\           报告 / 缺件清单 / 移动清单（带 Hidden 属性）
  C:\\ProgramData\\AEMediaDoctor\\   JSX 本体（**必须**纯 ASCII，R9）

负责：
  · `log_dir()` / `work_dir()` / `state_dir_of()` / `jsx_target_path()` 四个出口
  · `ensure_dir()`、`log_path_for()`、`prune_dir()`、`clear_state()`、`clear_work()`
  · 给目录打 Hidden 属性（读原值再或上 0x02，**绝不整体覆盖**）

不负责：
  · 写日志内容（runner/diag 负责写，本模块只管路径）
  · 判断「跑成功还是失败」（清不清 work\\ 由 runner 决定调用时机）
  · 读写 settings.json（cfg.py）

依赖：`.const`。**不许** import cfg / winproc / runner / ui —— 它是依赖图的最低层之一。

⚠ 反例（一代的坑，v2 用本模块从结构上堵掉）：
  · 日志曾写在 `<工程>\\_relink_log.txt`，用户的工程目录被搞脏 → 现在 log_dir 只指 %APPDATA%
  · `.ae_doctor` 曾用 `SetFileAttributesW(d, 0x02)` 直接覆盖，把 DIRECTORY 位抹掉，
    目录变成「文件」（资源管理器打不开、os.path.isdir() 返回 False）→ 现在必须 | 0x02
  · JSX 曾落在含中文的工程目录，`-r` 静默失败 → 现在 jsx_target_path 只指纯 ASCII 目录
"""

import os
import time

from . import const
from .const import (FORBIDDEN_WRITE_PREFIXES, ILLEGAL_FILENAME_CHARS, JSX_TARGET_DIR,
                    LOG_DIR, LOG_KEEP, MANUAL_JSX, REPORT_FILES, STATE_DIRNAME,
                    WORK_DIR, WORK_INDEX_JSON, WORK_JSX)

# ⚠ 三个目录常量在函数里一律走 `const.XXX`，不要用上面 import 进来的那份快照：
#   快照在 import 时就固定了，测试把它改掉（临时目录）不会生效，会出现「日志写到了
#   真 %APPDATA%，而测试以为写进了沙箱」这种查半天的假绿。


# ---------------------------------------------------------------- 四个出口
def log_dir():
    """运行日志目录（R8：只在这里，永远不在工程目录里）。"""
    ensure_dir(const.LOG_DIR)
    return const.LOG_DIR


def work_dir():
    """临时件目录（R8：跑完清、失败留，供复现）。"""
    ensure_dir(const.WORK_DIR)
    return const.WORK_DIR


def state_dir_of(out_dir):
    """工程目录下的结果子目录 `<工程>/.ae_doctor/`，返回绝对路径（并保证存在）。

    ⚠ 属性必须**读原值再或上 0x02 (HIDDEN)**：直接 `SetFileAttributesW(d, 0x02)`
    会把 `FILE_ATTRIBUTE_DIRECTORY(0x10)` 一起抹掉，目录立刻变成「文件」——
    v1.9.8 踩过一次，`os.path.isdir()` 当场返回 False。这里保留这条注释当护栏。
    """
    d = os.path.join(out_dir, STATE_DIRNAME)
    ensure_dir(d)
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        at = k32.GetFileAttributesW(d)
        if at is not None and at != -1:
            k32.SetFileAttributesW(d, at | 0x02)   # FILE_ATTRIBUTE_HIDDEN
    except Exception:
        pass
    return d


def jsx_target_path(out_dir):
    """JSX 本体落点 —— **全库唯一出口**（R9 / §8）。

    参数 `out_dir` 只用来做"降级备份"（ProgramData 不可写时退到工作目录），
    正常路径永远是 `C:\\ProgramData\\AEMediaDoctor\\`（纯 ASCII）。
    调用方不许自己拼路径，也不许把工程目录传进来当落点。
    """
    del out_dir  # 一代签名带它；v2 保留形参只为接口兼容，正常不用
    d = const.JSX_TARGET_DIR
    if not ensure_dir(d):
        d = os.path.join(work_dir(), "jsx")
        ensure_dir(d)
    return d


# ---------------------------------------------------------------- 工具
def ensure_dir(p):
    """建目录（已存在也算成功）；建不出来返回 False，不抛。"""
    if not p:
        return False
    try:
        os.makedirs(p, exist_ok=True)
        return True
    except Exception:
        return False


def log_path_for(out_dir, project_name):
    """本次运行日志的完整路径：`<logs>\\relink-<工程名>-<时间戳>.log`。

    `project_name` 必须是**原始工程名**（again），不是工作副本名（again_fixed）——
    工作副本是程序自己造的，写进日志名会让用户找不到对应关系。
    工程名里的 Windows 非法字符换成 `_`（工程名可以来自「另存为」的任意字符串）。
    """
    del out_dir
    stem = "%s-%s" % (project_name, time.strftime("%Y%m%d-%H%M%S"))
    for ch in ILLEGAL_FILENAME_CHARS:
        stem = stem.replace(ch, "_")
    return os.path.join(log_dir(), "relink-" + stem + ".log")


def prune_dir(d, prefix, suffix, keep):
    """只留最新 `keep` 个匹配 `prefix*suffix` 的文件，返回删掉几个。

    按 mtime 倒序保留（不靠名字排序：时间戳名字恰好也有序，但 mtime 更可靠）。
    删不掉不算错 —— 用户可能正开着那个日志文件。
    """
    if not d or keep < 0:
        return 0
    try:
        names = [n for n in os.listdir(d) if n.startswith(prefix) and n.endswith(suffix)]
    except Exception:
        return 0
    items = []
    for n in names:
        p = os.path.join(d, n)
        try:
            items.append((os.path.getmtime(p), p))
        except OSError:
            continue
    items.sort(reverse=True)
    removed = 0
    for _, p in items[keep:]:
        try:
            os.remove(p)
            removed += 1
        except OSError:
            pass
    return removed


def prune_logs(keep=LOG_KEEP):
    """R8：logs\\ 只留最新 50 个 relink-*.log（每次启动/跑完调一次）。"""
    return prune_dir(const.LOG_DIR, "relink-", ".log", keep)


def clear_state(sd):
    """跑之前清掉 `.ae_doctor\\` 里的上一轮结果（只清结果件，不删目录）。"""
    if not sd:
        return
    for name in REPORT_FILES:
        try:
            os.remove(os.path.join(sd, name))
        except OSError:
            pass


def clear_work():
    """跑完/失败收尾时清 work\\ 的临时件（jsx 副本、素材索引、手动脚本）。

    日志不在 work\\（R8：日志进 logs\\），所以这个函数不碰日志。
    """
    for name in (WORK_JSX, WORK_INDEX_JSON, MANUAL_JSX):
        try:
            os.remove(os.path.join(WORK_DIR, name))
        except OSError:
            pass


def is_ascii_path(p):
    """§8 的断言工具：路径能否被 AE/ExtendScript 安全吃下（纯 ASCII）。"""
    try:
        str(p).encode("ascii")
        return True
    except (UnicodeEncodeError, AttributeError):
        return False


def plan_work_copy(project_path):
    """F-01：工作副本的三级退避落点（**只算路径，不复制** —— 复制在 runner）。

    一级：同目录 `<名>_fixed.aep`
    二级：同目录 `<名>_fixed_<YYYYmmdd-HHMMSS>.aep`
    三级：`work\\<名>_fixed.aep`（工程目录只读/被占用时兜底）
    返回候选列表（保序）；全失败由 runner 拼成 RuntimeError，消息用「；」分隔每次尝试。

    为什么是"算路径"而不是"我来复制"：复制失败要带上下文日志（哪一级、报什么错），
    那是 runner 的活儿；paths 只负责**顺序**。这个顺序来自一代实测：
    目标文件被 ASE（AE 的自动保存）或杀软实时扫描按住时，`copy2` 会抛
    `PermissionError [Errno 13]`，此时换带时间戳的名字通常就过了；连写入都过不去
    （工程目录只读、U 盘拔了）才退到 `%APPDATA%\\AE素材医生\\work`。
    时间戳只取到秒，同一秒重跑会撞名 —— runner 遇到已存在会自己加 `_2`（见 const.DUP_SUFFIX）。
    """
    import datetime
    p = os.path.abspath(str(project_path))
    stem, ext = os.path.splitext(p)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return [
        stem + "_fixed" + ext,
        "%s_fixed_%s%s" % (stem, stamp, ext),
        os.path.join(WORK_DIR, "%s_fixed_%s%s" % (os.path.basename(stem), stamp, ext)),
    ]


def is_forbidden_write(p):
    """U-02：这个路径是不是"永远不许写"的用户资产区（Adobe 安装目录 / 插件目录）。

    一代有过往 `C:\\Program Files\\Adobe\\...\\Plug-ins` 写东西的念头（段 2 授权就是为它），
    v2 从根上不许：任何落点都要先过这一关。只做前缀比较（大小写不敏感、规范化反斜杠）。
    """
    try:
        norm = os.path.normcase(os.path.abspath(str(p))).replace("/", "\\")
    except (TypeError, ValueError):
        return False
    for pref in FORBIDDEN_WRITE_PREFIXES:
        if norm == os.path.normcase(pref) or norm.startswith(os.path.normcase(pref) + "\\"):
            return True
    return False


def plan_backup(project_path):
    """1.1.9 方案甲：备份原工程的落点（只算路径，不复制 —— 复制在 runner）。

    目标名 `<名>_backup.aep`，放在**同目录**。为什么放同目录而不是 work\\：
    备份是给用户"修坏了能还原"的还原点，放在工程旁边用户才找得到、才敢用。
    已存在就换带时间戳的名字（同秒撞名由 runner 加 `_2`）。
    """
    import datetime
    p = os.path.abspath(str(project_path))
    stem, ext = os.path.splitext(p)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return [
        stem + const.BACKUP_SUFFIX + ext,
        "%s%s_%s%s" % (stem, const.BACKUP_SUFFIX, stamp, ext),
    ]


def make_backup(project_path, log=None):
    """1.1.9 方案甲：把原工程复制成 `<名>_backup.aep` 作为还原点，返回备份路径。

    三级退避减到两级（同目录 backup、带时间戳 backup）—— 备份放工程旁才有意义，
    工程目录整片只读时直接报错让用户知道"这次没法留备份"，而不是悄悄写到别处
    （写到别处用户找不到，等于没备份）。

    为什么允许覆盖已存在的 `_backup.aep`：那是上一次修复留下的旧备份，本来该被本轮替换。
    被 AE/杀软按住时 `copy2` 抛 PermissionError [Errno 13] → 换带时间戳名字。
    """
    import shutil
    errs = []
    for cand in plan_backup(project_path):
        try:
            if is_forbidden_write(cand):
                errs.append("%s -> 落在禁写区（Adobe 安装/插件目录），跳过" % cand)
                continue
            ensure_dir(os.path.dirname(cand) or ".")
            shutil.copy2(project_path, cand)
            if log:
                log("BACKUP  已备份原工程到 %s（修复直接改原工程，这份留着还原）"
                    % os.path.basename(cand))
            return cand
        except OSError as e:
            errs.append("%s -> %s" % (cand, e))
    raise RuntimeError("备份工程失败：%s" % "；".join(errs))


def make_work_copy(project_path, log=None, candidates=None):
    """F-01 的**执行**半边：按 `plan_work_copy` 的三级顺序真复制一份工作副本。

    返回复制成功的路径；三级全失败抛 `RuntimeError`，消息里带每一级的目标与原始报错
    （一代的教训：只说「复制失败」用户没法自查，必须说清是哪一级、报的是 13 还是 5）。

    为什么允许覆盖已存在的 `_fixed.aep`：那是上一轮留下的工作副本，本来就该被本轮替换。
    但它正被 AE/杀软按住时 `copy2` 会抛 `PermissionError [Errno 13]` —— 这时自动换带
    时间戳的名字（第二级），不打断用户；工程目录整片只读才退到 work\\（第三级）。
    """
    import shutil
    cands = list(candidates) if candidates else plan_work_copy(project_path)
    ensure_dir(WORK_DIR)
    errs = []
    for cand in cands:
        try:
            if is_forbidden_write(cand):
                errs.append("%s -> 落在禁写区（Adobe 安装/插件目录），跳过" % cand)
                continue
            ensure_dir(os.path.dirname(cand) or ".")
            shutil.copy2(project_path, cand)
            if log:
                log("COPY  工作副本：%s" % os.path.basename(cand))
            return cand
        except OSError as e:
            errs.append("%s -> %s" % (cand, e))
    raise RuntimeError("复制工程失败（三级都写不进去）：%s" % "；".join(errs))


__all__ = ["log_dir", "work_dir", "state_dir_of", "jsx_target_path", "ensure_dir",
           "log_path_for", "prune_dir", "prune_logs", "clear_state", "clear_work",
           "is_ascii_path", "is_forbidden_write", "plan_work_copy", "make_work_copy",
           "plan_backup", "make_backup"]