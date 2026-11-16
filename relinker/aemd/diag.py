# -*- coding: utf-8 -*-
"""aemd.diag —— 诊断包（「下次出问题别再靠截图」）。

负责：
  · `collect()`：把一次故障现场能拿到的证据收进一个 dict
      - 最近 3 个运行日志（`paths.log_dir()`）
      - settings.json（路径保留，其它原样）
      - 系统信息：OS 版本、CPU 核数、内存、磁盘剩余
      - AE 安装列表（版本、AfterFX.exe 路径、AfterFXLib.dll 签名状态、.bak 清单）
      - 最近 7 天 AfterFX 相关的 Application Error / Hang 事件
      - 程序版本、exe 指纹、Python 版本、是否冻结
  · `write_zip()`：写成 `%APPDATA%\\AE素材医生\\拾链-diag-<时间戳>.zip`

不负责：
  · 判断故障原因（人看 zip 看出来，代码不猜）
  · 上传：**不联网**，一个字节都不往外发
  · 画界面（一个按钮调 write_zip 就够）

依赖：`.const`、`.paths`、`.updater`、`.aehost`（安装清单）、标准库

⚠ 一代的坑：一份 dump 分析报告里，崩溃现场的关键信息（进程/模块/时间线）
  全靠人工一条条 rediscover。诊断包就是为了让下一次只需一个 zip。
⚠ 本模块**只读**：不启动 AE、不碰 Startup 目录、不碰 work\\ 里的临时件
  （work 目录归 runner 管，诊断包只读 logs 与 settings）。
"""

import datetime
import json
import os
import platform
import shutil
import subprocess
import sys
import zipfile

from . import aehost, const, paths, updater

KEEP_LOGS = 3          # 包里带最近几个运行日志
EVENT_DAYS = 7         # 最近几天的崩溃事件
EVENT_MAX = 40         # 最多带多少条


# --------------------------------------------------------------- 系统信息

def _memory_gb():
    """物理内存（GB）。ctypes 直接问系统，不引 psutil。"""
    try:
        import ctypes

        class MSTAT(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        m = MSTAT()
        m.dwLength = ctypes.sizeof(MSTAT)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return {"total_gb": round(m.ullTotalPhys / 1073741824.0, 1),
                    "avail_gb": round(m.ullAvailPhys / 1073741824.0, 1),
                    "load_pct": int(m.dwMemoryLoad)}
    except Exception as e:
        return {"unavailable": str(e)}
    return {"unavailable": "GlobalMemoryStatusEx 返回 0"}


def _disks():
    out = []
    for letter in ("C:", "D:", "E:"):
        try:
            u = shutil.disk_usage(letter + "\\")
            out.append({"drive": letter, "total_gb": round(u.total / 1073741824.0, 1),
                        "free_gb": round(u.free / 1073741824.0, 1)})
        except OSError:
            continue
    return out


def system_info():
    """OS / CPU / 内存 / 磁盘剩余（纯标准库；不引 psutil）。"""
    info = {"unavailable": None}
    try:
        info = {
            "os": platform.platform(),
            "os_release": "%s (%s)" % (platform.release(), platform.version()),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "cpu": platform.processor(),
            "memory": _memory_gb(),
            "disks": _disks(),
            "python": sys.version.split()[0],
            "frozen": bool(getattr(sys, "frozen", False)),
            "exe": updater.exe_path(),
            "stamp": list(updater.stamp()),
            "app": "%s %s" % (const.APP_NAME, const.APP_VER),
            "now": datetime.datetime.now().isoformat(timespec="seconds"),
        }
    except Exception as e:
        return {"unavailable": str(e)}
    return info


# --------------------------------------------------------------- AE 安装

def _signature_of(path):
    """AfterFXLib.dll 的 Authenticode 状态（只读）。

    这是 diag 里唯一的 subprocess：PowerShell 的 Get-AuthenticodeSignature。
    拿不到（没有 PowerShell / 没权限 / 超时）就如实写 unavailable，不猜。
    """
    try:
        ps = ("$s = Get-AuthenticodeSignature -LiteralPath '%s'; "
              "Write-Output ($s.Status.ToString() + '|' + $s.SignerCertificate.Subject)"
              % path.replace("'", "''"))
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, timeout=20,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        txt = r.stdout.decode("gbk", "replace").strip()
        if not txt:
            return {"status": "unavailable", "signer": ""}
        status, _, signer = txt.partition("|")
        return {"status": status.strip(), "signer": signer.strip()}
    except Exception as e:
        return {"status": "unavailable", "error": str(e), "signer": ""}


def ae_installs_info():
    """AE 安装清单 + 签名状态 + .bak 列表（只读，不改任何文件）。"""
    out = []
    try:
        installs = aehost.find_installs()
    except Exception as e:
        return [{"unavailable": str(e)}]
    for name, exe in installs:
        item = {"name": name, "exe": exe, "exe_exists": os.path.isfile(exe)}
        sup = os.path.dirname(exe)
        lib = os.path.join(sup, "AfterFXLib.dll")
        if os.path.isfile(lib):
            st = os.stat(lib)
            item["afterfxlib"] = {
                "path": lib, "size": st.st_size,
                "mtime": datetime.datetime.fromtimestamp(st.st_mtime)
                .isoformat(sep=" ", timespec="seconds"),
                "signature": _signature_of(lib),
            }
            baks = []
            for fn in ("AfterFXLib.dll.bak", "AfterFXLib.bak", "AfterFXLib.dll.orig"):
                p = os.path.join(sup, fn)
                if os.path.isfile(p):
                    s2 = os.stat(p)
                    baks.append({"file": fn, "size": s2.st_size,
                                 "signature": _signature_of(p)["status"]})
            item["bak"] = baks
        else:
            item["afterfxlib"] = {"unavailable": "没有 AfterFXLib.dll"}
        out.append(item)
    return out


# --------------------------------------------------------------- 事件日志

def parse_event_time(text, now=None):
    """D7（§8.1）：把 wevtutil 的 `2026-11-15T12:19:52.000Z` 解析成 datetime。

    一代教训（E-11）：wevtutil 输出带 `Z` 后缀，但**其实拿的是本地时间**。
    直接 `fromisoformat` 会当 UTC 处理，事件时间整体偏 8 小时，
    于是「安装和崩溃是不是同一分钟」这种判断全错（我因此一度误判时间线）。
    规则：先按本地时间解；只有落在**未来**超过 `WEVT_FUTURE_TOLERANCE_S` 秒，才回退成 UTC。
    """
    s = (text or "").strip()
    if not s:
        raise ValueError("空时间串")
    now = now or datetime.datetime.now()
    core = s[:-1] if s.endswith("Z") else s
    dt = datetime.datetime.fromisoformat(core.replace(" ", "T"))
    if dt.tzinfo is not None:
        # 解析成带时区的，统一折算成本地 naive（诊断包里全用本地时间）
        return dt.astimezone().replace(tzinfo=None)
    ahead = (dt - now).total_seconds()
    if ahead > const.WEVT_FUTURE_TOLERANCE_S:
        # 落在未来太多 → 把它当 UTC，换回本地
        dt = dt.replace(tzinfo=datetime.timezone.utc).astimezone().replace(tzinfo=None)
    return dt


def recent_crash_events(days=EVENT_DAYS):
    """最近 N 天 AfterFX 相关的 Application Error / Hang 事件（wevtutil，只读）。"""
    try:
        since = datetime.datetime.now() - datetime.timedelta(days=days)
        q = ("*[System[(EventID=1000 or EventID=1001 or EventID=1002) and "
             "TimeCreated[@SystemTime>='%s']]]" % since.astimezone(datetime.timezone.utc)
             .strftime("%Y-%m-%dT%H:%M:%S.000Z"))
        r = subprocess.run(["wevtutil", "qe", "Application", "/q:" + q, "/f:text",
                            "/c:%d" % EVENT_MAX, "/rd:true"],
                           capture_output=True, timeout=40,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        txt = r.stdout.decode("gbk", "replace")
    except Exception as e:
        return {"unavailable": str(e)}
    events, cur = [], {}
    for line in txt.splitlines():
        if line.startswith("Event["):
            if cur:
                events.append(cur)
            cur = {"raw_head": line}
        elif line.startswith("Date:"):
            try:
                cur["time"] = parse_event_time(line[5:]).isoformat(sep=" ", timespec="seconds")
            except Exception:
                cur["time"] = line[5:].strip()
        elif line.startswith("Event ID:"):
            cur["id"] = line[9:].strip()
        elif line.startswith("Source:"):
            cur["source"] = line[7:].strip()
        elif line.startswith("Level:"):
            cur["level"] = line[6:].strip()
        elif line.strip() and "Description" not in line and len(cur.get("message", "")) < 500:
            cur["message"] = (cur.get("message", "") + line.strip() + "\n")[:500]
    if cur:
        events.append(cur)
    return [e for e in events if "AfterFX" in json.dumps(e, ensure_ascii=False)
            or e.get("id") in ("1000", "1001")]


# --------------------------------------------------------------- 收集与打包

def _read_text(path, limit=200000):
    try:
        with open(path, "rb") as f:
            return f.read(limit).decode("utf-8", "replace")
    except OSError as e:
        return "读不出来：%s" % e


def collect():
    """收集诊断数据 → dict（全都 try/except，缺哪项就标 unavailable，不让整包失败）。"""
    data = {"app": "%s %s" % (const.APP_NAME, const.APP_VER),
            "generated": datetime.datetime.now().isoformat(timespec="seconds")}

    def safe(name, fn):
        try:
            data[name] = fn()
        except Exception as e:
            data[name] = {"unavailable": "%s: %s" % (type(e).__name__, e)}

    safe("system", system_info)
    safe("ae_installs", ae_installs_info)
    safe("crash_events", recent_crash_events)

    # settings.json（路径保留，其它原样）
    def settings():
        from . import cfg
        p = cfg.cfg_path()
        return {"path": p, "text": _read_text(p)}

    safe("settings", settings)

    # 最近几个运行日志（只读，不动 work\）
    def logs():
        d = paths.log_dir()
        if not os.path.isdir(d):
            return {"dir": d, "files": []}
        files = [os.path.join(d, n) for n in os.listdir(d) if n.lower().endswith(".log")]
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return {"dir": d, "files": [{"name": os.path.basename(p), "size": os.path.getsize(p),
                                     "text": _read_text(p)} for p in files[:KEEP_LOGS]]}

    safe("logs", logs)
    safe("env", lambda: {"PATH_head": os.environ.get("PATH", "")[:600],
                         "TEMP": os.environ.get("TEMP", ""),
                         "USERPROFILE": os.environ.get("USERPROFILE", ""),
                         "PYTHONIOENCODING": os.environ.get("PYTHONIOENCODING", "")})
    return data


def write_zip(dest_dir=None):
    """收集并打包，返回 zip 路径；失败返回 None（界面上报一句话，不弹异常）。"""
    try:
        d = dest_dir or const.APP_HOME
        paths.ensure_dir(d)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        zpath = os.path.join(d, "%s-diag-%s.zip" % (const.APP_NAME, stamp))
        data = collect()
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("diagnose.json", json.dumps(data, ensure_ascii=False, indent=2))
            summary = _summary_text(data)
            z.writestr("diagnose.txt", summary)
            for f in (data.get("logs") or {}).get("files") or []:
                if isinstance(f, dict) and f.get("name"):
                    z.writestr("logs/" + f["name"], f.get("text") or "")
            st = data.get("settings") or {}
            if isinstance(st, dict) and st.get("text"):
                z.writestr("settings.json", st["text"])
        return zpath
    except Exception:
        return None


def _event_line(e):
    """把一条崩溃事件压成一行。

    v1.0.0 的实测（t-21）抓到一个真瑕疵：`crash_events` 存的是 wevtutil 的原文
    （键只有 `raw_head` / `message`），而摘要这里按 `time` / `id` / `source` 取值，
    于是 diagnose.txt 印出来是「? id=? 」（两个事件都这样）。这里改成：
    有结构化字段就用字段，没有就从原文里抠 Date / Event ID / Source，最后兜底原文首行。
    """
    if not isinstance(e, dict):
        return str(e)[:120]
    if e.get("time") or e.get("id") or e.get("source"):
        return "%s  id=%s  %s" % (e.get("time", "?"), e.get("id", "?"),
                                  (e.get("source") or "")[:40])
    msg = e.get("message") or ""
    get = {}
    for line in msg.splitlines():
        for key in ("Date", "Event ID", "Source", "Task", "Level", "故障存储段", "事件名称"):
            if line.startswith(key + ":") or line.startswith(key + " "):
                get.setdefault(key, line.split(":", 1)[1].strip() if ":" in line else "")
    bits = []
    if get.get("Date"):
        bits.append("%s" % get["Date"].replace("T", " ")[:19])
    if get.get("Event ID"):
        bits.append("id=%s" % get["Event ID"])
    if get.get("Source"):
        bits.append(get["Source"][:40])
    name = e.get("raw_head") or ""
    if name:
        bits.append(name)
    if not bits:
        bits = [ (msg.strip().splitlines() or [""])[0][:80] ]
    return "  ".join(bits)


def _summary_text(data):
    """一份人先看的摘要（zip 里 diagnose.txt）。"""
    L = ["%s 诊断包" % data.get("app", ""), "生成时间：%s" % data.get("generated", ""), ""]
    sysd = data.get("system") or {}
    L.append("== 系统 ==")
    for k in ("os", "cpu_count", "cpu", "python", "frozen", "exe"):
        if k in sysd:
            L.append("  %-10s %s" % (k, sysd[k]))
    mem = sysd.get("memory") or {}
    if mem:
        L.append("  内存       %s" % json.dumps(mem, ensure_ascii=False))
    disks = sysd.get("disks") or []
    if disks:
        L.append("  磁盘       %s" % " | ".join(
            "%s 剩 %sG / %sG" % (d.get("drive"), d.get("free_gb"), d.get("total_gb"))
            for d in disks))
    L.append("")
    L.append("== AE 安装 ==")
    for it in data.get("ae_installs") or []:
        if "unavailable" in it:
            L.append("  %s" % it["unavailable"])
            continue
        lib = it.get("afterfxlib") or {}
        sig = (lib.get("signature") or {}).get("status", "?")
        L.append("  %-28s lib=%s 签名=%s .bak=%d" % (
            it.get("name"), lib.get("size", "-"), sig, len(it.get("bak") or [])))
    ev = data.get("crash_events")
    L.append("")
    L.append("== 最近崩溃事件 ==")
    if isinstance(ev, dict):
        L.append("  %s" % ev.get("unavailable", "无"))
    elif not ev:
        L.append("  无")
    else:
        for e in ev[:10]:
            L.append("  %s" % _event_line(e))
    logs = data.get("logs") or {}
    L.append("")
    L.append("== 运行日志（%s）==" % logs.get("dir", ""))
    for f in logs.get("files") or []:
        L.append("  %s  %s B" % (f.get("name"), f.get("size")))
    return "\n".join(L) + "\n"


__all__ = ["collect", "write_zip", "system_info", "ae_installs_info",
           "recent_crash_events", "parse_event_time"]