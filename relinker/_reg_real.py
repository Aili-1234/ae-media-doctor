"""拾链 1.0.0 真机回归驱动（一次性）。

用分发后的实物 C:\\插件\\自己的插件和辅助工具\\素材修复2\\拾链.exe 跑一轮：
  python _reg_real.py            跑全套（前清 → CLI → 存活采样 → 后清 → 写证据）
原始采样写 artifacts/p4-reg-real.txt，控制台同时打印。
"""
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import os
import subprocess
import sys
import time

EXE = r"C:\插件\自己的插件和辅助工具\素材修复2\拾链.exe"
PROJ = r"D:\ae\别人工程\again (1)\again\again.aep"
PROJ_DIR = os.path.dirname(PROJ)
ART = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")
OUT = os.path.join(ART, "p4-reg-real.txt")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aemd import winproc  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_NULL = 0x0000
SMTO_ABORTIFHUNG = 0x0002
EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

lines = []


def say(s=""):
    print(s)
    lines.append(s)


def win_title(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 2)
    user32.GetWindowTextW(hwnd, buf, n + 2)
    return buf.value


def responding(hwnd):
    res = wt.DWORD(0)
    ok = user32.SendMessageTimeoutW(hwnd, WM_NULL, 0, 0, SMTO_ABORTIFHUNG, 300,
                                    ctypes.byref(res))
    return bool(ok)


def ae_windows():
    """[(pid, hwnd, title, responding, visible)]"""
    out = []
    keep = set(ae_pid_set())

    def cb(hwnd, _lp):
        pid = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in keep:
            title = win_title(hwnd)
            out.append((pid.value, int(hwnd), title, responding(hwnd),
                        bool(user32.IsWindowVisible(hwnd))))
        return True

    EnumProc(cb)(0, 0)
    return out


def ae_pid_set():
    return set(winproc.snapshot_pids("AfterFX.exe"))


def startup_entries():
    base = os.path.join(os.environ["APPDATA"], "Adobe", "After Effects")
    rows = []
    if os.path.isdir(base):
        for ver in sorted(os.listdir(base)):
            d = os.path.join(base, ver, "Scripts", "Startup")
            if os.path.isdir(d):
                for f in sorted(os.listdir(d)):
                    rows.append(os.path.join(ver, "Scripts", "Startup", f))
    return rows


def work_files():
    d = os.path.join(os.environ["APPDATA"], "AE素材医生", "work")
    if not os.path.isdir(d):
        return []
    return sorted(os.listdir(d))


def log_dir_files():
    d = os.path.join(os.environ["APPDATA"], "AE素材医生", "logs")
    if not os.path.isdir(d):
        return []
    return sorted(os.listdir(d))


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def listing(d):
    if not os.path.isdir(d):
        return []
    out = []
    for root, dirs, files in os.walk(d):
        rel = os.path.relpath(root, d)
        for f in sorted(files):
            out.append(os.path.normpath(os.path.join(rel, f)))
    return sorted(out)


def snap(tag):
    st = {
        "tag": tag,
        "ae_pids": sorted(ae_pid_set()),
        "startup": startup_entries(),
        "work": work_files(),
        "proj_files": listing(PROJ_DIR),
        "again_md5": md5(PROJ),
        "settings": None,
    }
    sp = os.path.join(os.environ["APPDATA"], "AE素材医生", "settings.json")
    if os.path.isfile(sp):
        try:
            with open(sp, encoding="utf-8") as f:
                st["settings"] = json.load(f)
        except Exception as e:
            st["settings"] = "unreadable: %s" % e
    return st


def main():
    os.makedirs(ART, exist_ok=True)
    say("=== 拾链 1.0.0 真机回归 %s ===" % time.strftime("%Y-%m-%d %H:%M:%S"))
    say("EXE   %s" % EXE)
    say("EXE   大小 %d / mtime %s" % (os.path.getsize(EXE),
                                     time.strftime("%Y-%m-%d %H:%M:%S",
                                                   time.localtime(os.path.getmtime(EXE)))))
    say("工程  %s" % PROJ)

    pre = snap("pre")
    say("\n[pre] AfterFX=%s  Startup=%d 项  work=%s  日志数=%d"
        % (pre["ae_pids"] or "无", len(pre["startup"]), pre["work"] or "空",
           len(log_dir_files())))
    say("[pre] again.aep md5 = %s" % pre["again_md5"])
    say("[pre] 工程根 = %s" % pre["proj_files"])
    say("[pre] settings 里记着的工程 = %r"
        % ((pre["settings"] or {}).get("project") if isinstance(pre["settings"], dict) else pre["settings"]))

    say("\n[run] 启动 CLI（不带 --yes，不关 AE；控制台输出重定向到 artifacts\\p4-reg-console.txt）")
    con = os.path.join(ART, "p4-reg-console.txt")
    t0 = time.time()
    with open(con, "wb") as cf:
        proc = subprocess.Popen([EXE, "--cli", PROJ], stdout=cf,
                                stderr=subprocess.STDOUT, cwd=PROJ_DIR)
        # 存活采样：每 5 秒一次
        samples = []
        while proc.poll() is None:
            el = time.time() - t0
            wins = ae_windows()
            samples.append((round(el, 1), sorted(ae_pid_set()), wins))
            say("[t+%5.1fs] AfterFX=%s  窗口=%s"
                % (el, sorted(ae_pid_set()) or "无",
                   [(w[0], w[2][:46], "resp" if w[3] else "HUNG", "vis" if w[4] else "hid") for w in wins] or "无"))
            time.sleep(5)
        rc = proc.wait()
    run_s = time.time() - t0
    say("\n[run] exit=%d  用时 %.1f 秒" % (rc, run_s))
    with open(con, "r", encoding="utf-8", errors="replace") as f:
        body = f.read()
    say("[console]\n" + body.strip())

    after = []
    for delta in (30, 60):
        while time.time() - t0 < run_s + delta:
            time.sleep(1)
        wins = ae_windows()
        pids = sorted(ae_pid_set())
        state = [(w[0], w[2], "resp" if w[3] else "HUNG", "vis" if w[4] else "hid") for w in wins]
        after.append((delta, pids, state))
        say("[run+%ds] AfterFX=%s  窗口=%s" % (delta, pids or "无", state or "无"))

    post = snap("post")
    say("\n[post] AfterFX=%s  Startup=%d 项  work=%s  日志数=%d"
        % (post["ae_pids"] or "无", len(post["startup"]), post["work"] or "空",
           len(log_dir_files())))
    say("[post] again.aep md5 = %s  (%s)"
        % (post["again_md5"], "未变" if post["again_md5"] == pre["again_md5"] else "变了！"))

    added = [p for p in post["proj_files"] if p not in pre["proj_files"]]
    removed = [p for p in pre["proj_files"] if p not in post["proj_files"]]
    say("[post] 工程根新增 = %s" % (added or "无"))
    say("[post] 工程根消失 = %s" % (removed or "无"))

    say("\n[proj] .ae_doctor 内容 = %s"
        % (listing(os.path.join(PROJ_DIR, ".ae_doctor")) or "（不存在）"))

    logs = [p for p in log_dir_files() if p.startswith("relink-")]
    say("[logs] relink-*.log = %s" % (logs[-2:] or "无"))
    counts = {}
    tail = ""
    if logs:
        lp = os.path.join(os.environ["APPDATA"], "AE素材医生", "logs", logs[-1])
        with open(lp, encoding="utf-8", errors="replace") as f:
            text = f.read()
        for key in ("A2", "FALLBACK", "LAUNCH", "DONE-OK", "SUMMARY", "SAVED",
                    "RELINKED", "MISSING", "WARN", "TIME", "SKIP"):
            counts[key] = sum(1 for ln in text.splitlines() if ln.startswith(key))
        tail = "\n".join(text.splitlines()[-14:])
        say("[log] %s 行数=%d" % (logs[-1], len(text.splitlines())))
        say("[log] 分类计数 = %s" % counts)

    rep = os.path.join(PROJ_DIR, ".ae_doctor", "_relink_report.json")
    if os.path.isfile(rep):
        with open(rep, encoding="utf-8") as f:
            r = json.load(f)
        say("[report] stats=%s  logWritable=%s  relinked=%d  missing=%d"
            % (r.get("stats"), r.get("logWritable"), len(r.get("relinked") or []),
               len(r.get("missing") or [])))

    say("\n[log 尾 14 行]\n" + tail)

    # 判据小结
    say("\n=== 判据 ===")
    say("1. A2 注入 / FALLBACK=0 : A2=%s FALLBACK=%s"
        % (counts.get("A2"), counts.get("FALLBACK")))
    say("2. 脚本后 +30/+60 AE 存活: %s"
        % [(d, bool(p), st and st[0][2] == "resp") for d, p, st in after])
    say("3. 工程根新增 = %s" % (added or "无"))
    say("4. AfterFX %s→%s / Startup %d→%d / md5 %s"
        % (pre["ae_pids"] or "0", post["ae_pids"] or "0",
           len(pre["startup"]), len(post["startup"]),
           "未变" if pre["again_md5"] == post["again_md5"] else "变了"))

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n证据写入 %s" % OUT)


if __name__ == "__main__":
    main()