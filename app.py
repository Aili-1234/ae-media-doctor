#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AE 素材医生 —— 检查并修复 After Effects 工程里丢失的素材链接

原理：不碰二进制，调用 AE 自己的 ExtendScript 接口（footageItem.replace）
      逐条把离线素材重连到本地同名文件，然后保存工程。
      工程默认先复制一份再改，原件不动。

依赖：只用 Python 标准库（tkinter）。AE 需要已安装。
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_NAME = "AE 素材医生"
APP_VER = "1.6"


def cfg_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "AE素材医生")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def cfg_path():
    return os.path.join(cfg_dir(), "settings.json")


def load_cfg():
    try:
        with io.open(cfg_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_cfg(d):
    try:
        with io.open(cfg_path(), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
BG = "#16181d"
PANEL = "#1c1f26"
LINE = "#2a2e38"
FG = "#d7dce5"
DIM = "#8b93a4"
ACCENT = "#7c5cff"
CARD = "#1a1d24"
CARD_LINE = "#272b35"
DEEPBLUE = "#2f4fa8"
OK_C = "#7ed3a0"
WARN_C = "#e0a96c"
BAD_C = "#e07a6c"
FONT = ("Microsoft YaHei UI", 9)
MONO = ("Consolas", 9)

ADOBE_DIRS = [
    r"C:\Program Files\Adobe",
    r"D:\Program Files\Adobe",
    r"C:\Program Files (x86)\Adobe",
]

JSX_TEMPLATE = r"""/*
 * AE 素材医生 生成的重连脚本（基于 _relink.jsx v2.0）
 * 由 GUI 写入配置，请勿手工修改；改选项请回到软件里。
 */
var CFG = {
    project: __PROJECT__,
    searchRoots: __ROOTS__,
    maxDepth: __DEPTH__,
    dryRun: __DRYRUN__,
    consolidate: __CONSOLIDATE__,
    consolidateDir: __CONSOLIDATE_DIR__,
    save: __SAVE__,
    alertOnFinish: false,
    reportDir: __REPORT_DIR__
};

var OUT = CFG.reportDir || (CFG.project.substring(0, CFG.project.lastIndexOf("/")));
var LOG = OUT + "/_relink_log.txt";
var MISS = OUT + "/_relink_missing.txt";
var JSONF = OUT + "/_relink_report.json";

var MEDIA_EXT = ["mp4","mov","mkv","avi","m4v","webm","mxf","wmv","flv",
                 "mp3","wav","aif","aiff","m4a","aac","ogg","flac",
                 "jpg","jpeg","png","exr","tif","tiff","psd","webp","gif","dpx","hdr"];

var lines = [], missLines = [], relinked = [], missingItems = [];
var stats = { total:0, ok:0, fixed:0, ambiguous:0, missing:0, broken:0 };

function L(s){ lines.push(s); }
function extOf(s){ var i=s.lastIndexOf("."); return i<0?"":s.substring(i+1).toLowerCase(); }
function baseOf(s){ var i=s.lastIndexOf("."); return i<0?s:s.substring(0,i); }
function shortKey(s){
    var n = baseOf(s).toLowerCase();
    var dot = n.indexOf(".");
    if (dot > 0) n = n.substring(0, dot);
    return n.replace(/^\s+|\s+$/g, "");
}
function isMedia(n){
    var e = extOf(n);
    for (var i=0;i<MEDIA_EXT.length;i++) if (MEDIA_EXT[i]===e) return true;
    return false;
}
function esc(s){
    if (s===null||s===undefined) return "";
    return String(s).replace(/\\/g,"\\\\").replace(/"/g,'\\"').replace(/\r?\n/g,"\\n");
}

var idxExact = {}, idxShort = {}, scanned = 0;

function addIndex(path, name){
    var k = name.toLowerCase();
    if (!idxExact[k]) idxExact[k] = [];
    idxExact[k].push(path);
    var sk = shortKey(name) + "|" + extOf(name);
    if (!idxShort[sk]) idxShort[sk] = [];
    idxShort[sk].push(path);
}

function scan(dir, depth){
    if (depth > CFG.maxDepth) return;
    var f = new Folder(dir);
    if (!f.exists) return;
    var list = f.getFiles();
    for (var i=0;i<list.length;i++){
        var it = list[i];
        if (it instanceof Folder) scan(it.fsName, depth+1);
        else if (it instanceof File) { if (isMedia(it.name)) { addIndex(it.fsName, it.name); scanned++; } }
    }
}

function exists(fr){ if(!fr) return false; try { return fr.exists; } catch(e){ return false; } }
function sizeOf(fr){ try { return fr.length; } catch(e){ return -1; } }

function relink(item){
    var src = item.mainSource;
    if (!(src instanceof FileSource)) return;

    stats.total++;
    var cur = src.file;
    var curPath = cur ? cur.fsName : "(no file)";

    if (exists(cur) && sizeOf(cur) > 0){
        stats.ok++;
        L("OK        " + item.name + "  |  " + curPath);
        return;
    }
    if (exists(cur) && sizeOf(cur) === 0){
        stats.broken++;
        L("ZERO      " + item.name + "  |  " + curPath + "  (文件 0 字节)");
    }

    var name = cur ? String(decodeURI(cur.name)) : "";
    var cand = null, how = "";

    var exact = idxExact[name.toLowerCase()];
    if (exact && exact.length >= 1){ cand = exact[0]; how = "全名匹配"; }

    if (!cand && name){
        var sk = shortKey(name) + "|" + extOf(name);
        var short = idxShort[sk];
        if (short && short.length >= 1){
            cand = short[0];
            how = "基名匹配(" + short.length + " 个候选)";
            if (short.length > 1) stats.ambiguous++;
        }
    }

    if (!cand){
        stats.missing++;
        var rec = { name: item.name, original: curPath, reason: name ? "本地无同名文件" : "工程未记录文件名" };
        missingItems.push(rec);
        missLines.push(name + "\t" + curPath);
        L("MISSING   " + item.name + "  |  " + curPath + "  |  " + rec.reason);
        return;
    }

    if (CFG.dryRun){
        stats.fixed++;
        L("WOULD-FIX " + item.name + "  |  " + curPath + "  =>  " + cand + "  [" + how + "]");
        return;
    }

    try {
        var target = cand;
        if (CFG.consolidate){
            var dir = new Folder(CFG.consolidateDir);
            if (!dir.exists) dir.create();
            var dest = new File(dir.fsName + "/" + new File(cand).name);
            if (!dest.exists) new File(cand).copy(dest.fsName);
            target = dest.fsName;
        }
        item.replace(new File(target));
        stats.fixed++;
        relinked.push({ item: item.name, from: curPath, to: target, how: how });
        L("RELINKED  " + item.name + "  |  " + curPath + "  =>  " + target + "  [" + how + "]");
    } catch (e){
        stats.missing++;
        missingItems.push({ name: item.name, original: curPath, reason: "replace 失败: " + e.toString() });
        missLines.push(name + "\t" + curPath);
        L("FAILED    " + item.name + "  |  " + e.toString());
    }
}

var f = new File(LOG); f.encoding = "UTF-8"; f.open("w");
function flush(){ f.writeln(lines.join("\n")); f.close(); }

try {
    var proj = app.project;
    var openedHere = false;
    var want = new File(CFG.project);
    if (!proj || !proj.file || decodeURI(proj.file.fsName) !== decodeURI(want.fsName)){
        L("OPEN      " + CFG.project);
        app.open(want);
        proj = app.project;
        openedHere = true;
    } else {
        L("USE-OPEN  " + decodeURI(proj.file.fsName));
    }

    L("SCRIPT    AE 素材医生 v__APPVER__   AE " + app.version + (CFG.dryRun ? "   [只体检]" : ""));
    L("PROJECT   " + decodeURI(proj.file.fsName));
    L("ITEMS     " + proj.numItems);

    for (var r=0;r<CFG.searchRoots.length;r++){
        var before = scanned;
        scan(CFG.searchRoots[r], 0);
        L("SCAN      " + CFG.searchRoots[r] + "  -> +" + (scanned-before) + " 文件");
    }
    L("INDEX     " + scanned + " 个素材文件");

    for (var i=1;i<=proj.numItems;i++){
        if (proj.item(i) instanceof FootageItem) relink(proj.item(i));
    }

    L("");
    L("SUMMARY   total=" + stats.total + " ok=" + stats.ok + " fixed=" + stats.fixed +
      " missing=" + stats.missing + " ambiguous=" + stats.ambiguous + " zeroByte=" + stats.broken);

    if (!CFG.dryRun && CFG.save && (stats.fixed > 0 || openedHere)){
        proj.save();
        L("SAVED     " + decodeURI(proj.file.fsName));
    } else if (CFG.dryRun){
        L("SKIP-SAVE (只体检)");
    }
    flush();

    var mf = new File(MISS); mf.encoding = "UTF-8"; mf.open("w");
    mf.writeln("# 缺失素材清单  " + (new Date()).toString());
    mf.writeln("# 把这些文件放进素材目录后，重跑一次即可接上");
    for (var m=0;m<missLines.length;m++) mf.writeln(missLines[m]);
    mf.close();

    var js = '{\n  "script": "AE Media Doctor v__APPVER__",\n  "project": "' + esc(decodeURI(proj.file.fsName)) + '",\n';
    js += '  "dryRun": ' + (CFG.dryRun ? "true" : "false") + ',\n';
    js += '  "stats": { "total": ' + stats.total + ', "ok": ' + stats.ok + ', "fixed": ' + stats.fixed +
          ', "missing": ' + stats.missing + ', "ambiguous": ' + stats.ambiguous + ', "zeroByte": ' + stats.broken + ' },\n';
    js += '  "relinked": [\n';
    for (var k=0;k<relinked.length;k++){
        var rr = relinked[k];
        js += '    { "item": "' + esc(rr.item) + '", "to": "' + esc(rr.to) + '", "how": "' + esc(rr.how) + '" }' + (k<relinked.length-1?",":"") + '\n';
    }
    js += '  ],\n  "missing": [\n';
    for (var k2=0;k2<missingItems.length;k2++){
        var mm = missingItems[k2];
        js += '    { "name": "' + esc(mm.name) + '", "original": "' + esc(mm.original) + '", "reason": "' + esc(mm.reason) + '" }' + (k2<missingItems.length-1?",":"") + '\n';
    }
    js += '  ]\n}\n';
    var jf = new File(JSONF); jf.encoding = "UTF-8"; jf.open("w"); jf.write(js); jf.close();
    L("DONE-OK");
    flush();
} catch (e){
    L("FATAL " + e.toString() + " line " + e.line);
    try { flush(); } catch (e2) {}
}
"""


def find_ae_installs():
    out = []
    for base in ADOBE_DIRS:
        if not os.path.isdir(base):
            continue
        for name in os.listdir(base):
            if not name.lower().startswith("adobe after effects"):
                continue
            exe = os.path.join(base, name, "Support Files", "AfterFX.exe")
            if os.path.isfile(exe):
                out.append((name, exe))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def js_str(s):
    return '"' + str(s).replace("\\", "/").replace('"', '\\"') + '"'


def js_list(items):
    return "[" + ", ".join(js_str(i) for i in items) + "]"


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n/1:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


class Tip:
    """鼠标悬停提示。"""

    def __init__(self, widget, text):
        self.w = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _=None):
        if self.tip:
            return
        x = self.w.winfo_rootx() + 8
        y = self.w.winfo_rooty() + self.w.winfo_height() + 6
        self.tip = tk.Toplevel(self.w)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry("+%d+%d" % (x, y))
        tk.Label(self.tip, text=self.text, bg="#232833", fg="#d7dce6", bd=0,
                 padx=10, pady=6, justify="left",
                 font=("Microsoft YaHei UI", 9)).pack()

    def _hide(self, _=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class Toggle(tk.Frame):
    """方框勾选：选中是深蓝方块 + 白色对勾。"""

    def __init__(self, master, text, var, bg=None, **kw):
        bg = bg or CARD
        super().__init__(master, bg=bg, **kw)
        self.var = var
        self.cv = tk.Canvas(self, width=18, height=18, bg=bg, highlightthickness=0, bd=0)
        self.cv.pack(side="left")
        self.lb = tk.Label(self, text=text, bg=bg, fg=FG, font=FONT, cursor="hand2")
        self.lb.pack(side="left", padx=(7, 0))
        for w in (self.cv, self.lb):
            w.bind("<Button-1>", self._click)
            w.bind("<Enter>", lambda e: self.lb.configure(fg="#ffffff"))
            w.bind("<Leave>", lambda e: self.lb.configure(fg=FG))
        self.var.trace_add("write", lambda *a: self._draw())
        self._draw()

    def _click(self, _=None):
        self.var.set(not self.var.get())

    def _draw(self):
        c = self.cv
        c.delete("all")
        if self.var.get():
            c.create_rectangle(1, 1, 17, 17, fill=DEEPBLUE, outline="#5a7bdc")
            c.create_line(4.6, 9.2, 7.6, 12.2, 13.4, 5.8, fill="#ffffff",
                          width=2, capstyle="round", joinstyle="round")
        else:
            c.create_rectangle(1, 1, 17, 17, fill="#20242c", outline="#454e61")


def card(master, **kw):
    f = tk.Frame(master, bg=CARD, highlightbackground=CARD_LINE, highlightthickness=1, bd=0, **kw)
    return f


def caption(master, text, bg=None):
    return tk.Label(master, text=text, bg=bg or CARD, fg=DIM,
                    font=("Microsoft YaHei UI", 8), anchor="w")


def flat_button(master, text, command, primary=False, width=None):
    bg = ACCENT if primary else "#232833"
    fg = "#ffffff" if primary else FG
    hover = "#8b6bff" if primary else "#2e3542"
    b = tk.Button(master, text=text, command=command, bg=bg, fg=fg,
                  activebackground=hover, activeforeground=fg,
                  disabledforeground="#5d6472", relief="flat", bd=0, cursor="hand2",
                  font=("Microsoft YaHei UI", 9, "bold" if primary else "normal"),
                  padx=16, pady=8 if primary else 6, highlightthickness=0,
                  width=width)
    b.bind("<Enter>", lambda e: b.configure(bg=hover) if str(b["state"]) != "disabled" else None)
    b.bind("<Leave>", lambda e: b.configure(bg=bg) if str(b["state"]) != "disabled" else None)
    b._bg, b._hover = bg, hover
    return b


def logo(parent, bg):
    cv = tk.Canvas(parent, width=34, height=34, bg=bg, highlightthickness=0, bd=0)
    cv.create_oval(1, 1, 33, 33, fill="#6e4bf0", outline="")
    cv.create_rectangle(14, 8, 20, 26, fill="#ffffff", outline="")
    cv.create_rectangle(8, 14, 26, 20, fill="#ffffff", outline="")
    return cv


HELP_TEXT = r"""【它是什么】
AE 素材医生用来修复「别人发来的 AE 工程打开后素材全丢」的问题。
它不改工程二进制，而是调用 After Effects 自己的脚本接口：
打开工程 → 逐个检查素材是否在线 → 在指定目录里找同名文件 → 用 AE 官方 API 重新链接 → 保存。
所以修完的工程和你在 AE 里手动「替换素材」一模一样。

【三步用法】
1  选择 .aep 工程
   点「选择…」，选中那个素材丢失的工程。程序会自动把工程同级目录和 Footages 文件夹加进搜索目录。
2  确认素材目录
   素材放在哪就「添加…」哪个文件夹；可以加多个，按顺序查找。子目录会自动递归（最多 6 层）。
   不确定就先点「自动推断」。
3  点「开始检查」
   · 第一次建议勾「只体检」——只看不改，先弄清缺多少；
   · 正式修复保持「先复制一份再改」，会生成 工程名_fixed.aep，原件不动。

【它是怎么匹配的】
· 第一优先：文件名完全相同（不区分大小写）。
· 第二优先：短名匹配。工程的素材叫 1 (1).mp4，目录里是
  1 (1).119.880fps.QE.HW.VFIF...cb4018.mp4，也会被认出来（按「1 (1)」+ 扩展名匹配）。
  多个候选时取第一个，并在报告里记为 ambiguous。

【结果怎么看】
底部三个数字：已在线 / 本次重连 / 仍缺失。
工程同目录会生成：
  _relink_log.txt      逐条记录（OK / RELINKED / MISSING / FAILED）
  _relink_missing.txt  缺的东西，一行一个：文件名 + 工程里记录的原路径
  _relink_report.json  统计与重连清单，程序靠它显示结果

【找不到的素材：系统索引补搜】
一轮跑完如果还有 MISSING，程序会自动拿这些文件名去索引里再找一遍。
优先用 Everything（本程序自带便携版，在 engine\Everything 下）：
· 没在跑就拿起来，镜像没建好会申请一次管理员权限（弹一次 UAC，之后一直可用）
· 命中后自动把命中文件所在目录加进「素材搜索目录」，再点一次「开始检查」就能接上
· 只按文件名匹配、不比对内容：同名不同文件（比如 02.mp4）可能认错，跑完先看日志 INDEX 行的 + 目录对不对
· 每个缺失名最多取前 3 条命中，去重后加目录；不会自动重跑，也不会删你手动加过的目录
没有自带的/起不来时，退回 Windows 搜索索引（只搜已建索引的磁盘，默认只有 C 盘）。
两边都搜不到，才是本机真没有：按 _relink_missing.txt 回去要文件，放进素材目录再跑一次。

【会弹对话框是正常的】
程序通过 AE 的命令行脚本接口工作。AE 启动时如果弹：
  · 「崩溃修复选项」→ 点「继续」
  · 「解析字体」→ 点「确定」
不点掉，脚本不会继续执行，界面会一直停在运行中。

【注意】
· 不要在 AE 里同时开两个实例跑本工具；跑之前最好先关掉 AE 里打开的其他工程。
· 「收编到工程 Footages」会把找到的素材复制进工程目录，适合修完打包发给别人，代价是占空间。
· 「只体检」不会保存工程，可以放心点。
· 「完成后关闭 AE」默认勾选，免得 AE 一直占用你的内存。

【命令行（可选）】
python app.py --cli "D:\工程\xxx.aep" --dry
  --dry                 只体检
  --no-save             不保存工程
  --roots "D:\a;E:\b"  指定素材搜索目录（分号分隔）
退出码：0 成功，1 超时（多半是 AE 弹窗没点），2 没找到 AE。
"""



# ---------------- 系统索引快搜 ----------------
PS_INDEX = r"""param([string]$InFile,[string]$OutFile)
$ErrorActionPreference = 'SilentlyContinue'
$names = Get-Content -LiteralPath $InFile -Encoding UTF8
$conn = New-Object System.Data.OleDb.OleDbConnection "Provider=Search.CollatorDSO;Extended Properties='Application=Windows';"
$conn.Open()
$out = New-Object System.Collections.Generic.List[string]
foreach ($n in $names) {
  if ([string]::IsNullOrWhiteSpace($n)) { continue }
  $safe = $n.Replace("'", "''")
  $cmd = $conn.CreateCommand()
  $cmd.CommandText = "SELECT TOP 40 System.ItemUrl FROM SYSTEMINDEX WHERE System.FileName = '$safe'"
  $r = $cmd.ExecuteReader()
  while ($r.Read()) { $out.Add($n + "`t" + $r.GetValue(0)) }
  $r.Close()
}
$conn.Close()
$out | Out-File -LiteralPath $OutFile -Encoding UTF8
"""



def _url_to_path(u):
    """索引返回的是 file:C:/... 形式；解码成 Windows 路径。"""
    from urllib.parse import unquote
    if not u:
        return ""
    if u.lower().startswith("file:"):
        u = u[5:]
    u = u.lstrip("/")
    u = unquote(u)
    u = u.replace("/", "\\")
    return u

def app_dir():
    """程序所在目录（打包后就是 exe/脚本所在目录）。"""
    f = globals().get("__file__") or ""
    return os.path.dirname(os.path.abspath(f)) if f else os.getcwd()


def engine_dir():
    return os.path.join(app_dir(), "engine", "Everything")


def bundled_everything():
    p = os.path.join(engine_dir(), "everything.exe")
    return p if os.path.isfile(p) else ""


def find_everything():
    """按优先级找 es.exe：自带引擎 → PATH → 已安装的 Everything。"""
    p = os.path.join(engine_dir(), "es.exe")
    if os.path.isfile(p):
        return p
    p = shutil.which("es.exe")
    if p:
        return p
    for p in (r"C:\Program Files\Everything\es.exe",
              r"C:\Program Files (x86)\Everything\es.exe"):
        if os.path.isfile(p):
            return p
    return None


_BUNDLED_STARTED = False


def bundled_pids():
    """列出正在运行的、路径等于本程序自带 everything.exe 的进程。"""
    exe = bundled_everything()
    if not exe:
        return []
    try:
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='everything.exe'\" | "
              "Where-Object { $_.ExecutablePath -eq '%s' } | ForEach-Object { $_.ProcessId }" % exe)
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=20)
        out = r.stdout.decode("mbcs", "ignore")
        return [int(x) for x in out.split() if x.strip().isdigit()]
    except Exception:
        return []


def stop_bundled_everything(log=None):
    """关掉本程序拉起来的 Everything；用户自己装的那份不动。"""
    global _BUNDLED_STARTED
    pids = bundled_pids()
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=15)
        except Exception:
            pass
    _BUNDLED_STARTED = False
    if log and pids:
        log("INDEX     已关闭内置 Everything（随开关同步）")
    return len(pids)


def everything_running():
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq everything.exe", "/NH"],
                           capture_output=True, timeout=15)
        out = r.stdout.decode("mbcs", "ignore").lower()
        return "everything.exe" in out
    except Exception:
        return False


def _index_count(es):
    try:
        r = subprocess.run([es, "-get-result-count"], capture_output=True, timeout=15)
        return int((r.stdout.decode("mbcs", "ignore") or "0").strip() or 0)
    except Exception:
        return 0


def ensure_everything(log=None):
    """没装/没跑就拉起自带 Everything；镜像没建好时申请一次管理员权限。"""
    global _BUNDLED_STARTED
    es = find_everything()
    if not es:
        return None
    if everything_running() and _index_count(es) > 0:
        return es
    exe = bundled_everything()
    if not exe:
        return es if everything_running() else None
    if not everything_running():
        try:
            subprocess.Popen([exe, "-startup", "-minimized"],
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            _BUNDLED_STARTED = True
        except Exception:
            pass
        if log:
            log("INDEX     启动自带 Everything，等待索引…")
        for _ in range(12):
            time.sleep(1)
            if _index_count(es) > 0:
                return es
    if _index_count(es) == 0:
        if log:
            log("INDEX     索引为空，申请管理员权限重建（会弹一次 UAC）")
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Start-Process -FilePath '%s' -ArgumentList '-startup','-minimized' -Verb RunAs"
                            % exe], capture_output=True, timeout=30)
            _BUNDLED_STARTED = True
        except Exception:
            pass
        for _ in range(20):
            time.sleep(1)
            if _index_count(es) > 0:
                if log:
                    log("INDEX     Everything 索引就绪")
                return es
    return es


def index_lookup(names, timeout_per=20):
    """按文件名在系统索引里找绝对路径。返回 {小写名: [路径...]}。"""
    names = [n for n in dict.fromkeys(names) if n and n.strip()]
    hits = {}
    if not names:
        return hits
    es = ensure_everything()
    if es:
        for n in names:
            try:
                r = subprocess.run([es, "-n", "20", "-a", n], capture_output=True,
                                   timeout=timeout_per)
                txt = r.stdout.decode("mbcs", "ignore")
            except Exception:
                continue
            for line in txt.splitlines():
                line = line.strip().strip('"')
                if line and os.path.isfile(line):
                    hits.setdefault(n.lower(), []).append(line)
        return hits

    import tempfile
    d = tempfile.mkdtemp(prefix="docidx_")
    in_f = os.path.join(d, "names.txt")
    out_f = os.path.join(d, "hits.txt")
    ps_f = os.path.join(d, "q.ps1")
    io.open(in_f, "w", encoding="utf-8", newline="\n").write("\n".join(names))
    io.open(ps_f, "w", encoding="utf-8-sig", newline="\r\n").write(PS_INDEX)
    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", ps_f, "-InFile", in_f, "-OutFile", out_f],
                       capture_output=True, timeout=timeout_per * len(names) + 30)
        if os.path.isfile(out_f):
            for line in io.open(out_f, encoding="utf-8-sig", errors="ignore"):
                parts = line.rstrip("\r\n").split("\t")
                if len(parts) != 2 or not parts[1].strip():
                    continue
                path = _url_to_path(parts[1].strip())
                if path and os.path.isfile(path):
                    hits.setdefault(parts[0].strip().lower(), []).append(path)
    except Exception:
        pass
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return hits


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VER}")
        self.geometry("900x820")
        self.minsize(820, 640)
        self.configure(bg=BG)

        self.installs = find_ae_installs()
        self.proc = None
        self.worker = None
        self.running = False
        self.start_ts = 0
        self.report = None
        self.cfg = load_cfg()
        self.auto_close = tk.BooleanVar(value=bool(self.cfg.get("close_ae", True)))

        self._style()
        self._build()

    # ---------- 外观 ----------
    def _style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", background=BG, foreground=FG, font=FONT, fieldbackground=PANEL)
        st.configure("TFrame", background=BG)
        st.configure("Panel.TFrame", background=PANEL)
        st.configure("TLabel", background=BG, foreground=FG)
        st.configure("Dim.TLabel", background=BG, foreground=DIM)
        st.configure("Title.TLabel", background=BG, foreground="#ffffff",
                     font=("Microsoft YaHei UI", 15, "bold"))
        st.configure("Big.TLabel", background=PANEL, foreground="#ffffff",
                     font=("Microsoft YaHei UI", 18, "bold"))
        st.configure("TButton", background=PANEL, foreground=FG, borderwidth=1,
                     focusthickness=0, padding=(12, 6))
        st.map("TButton",
               background=[("active", "#262b36"), ("disabled", "#1a1d23")],
               foreground=[("disabled", "#5d6472")])
        st.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", borderwidth=0)
        st.map("Accent.TButton", background=[("active", "#8f73ff"), ("disabled", "#3a3550")])
        st.configure("TCheckbutton", background=BG, foreground=FG)
        st.map("TCheckbutton", background=[("active", BG)], foreground=[("active", "#ffffff")])
        st.configure("TEntry", fieldbackground=PANEL, foreground=FG, insertcolor=FG, borderwidth=1)
        st.configure("TCombobox", fieldbackground=PANEL, foreground=FG, background=PANEL,
                     arrowcolor=DIM, borderwidth=1)
        st.map("TCombobox",
               fieldbackground=[("readonly", PANEL), ("active", PANEL)],
               foreground=[("readonly", FG), ("active", FG)],
               selectbackground=[("readonly", PANEL)],
               selectforeground=[("readonly", FG)])
        self.option_add("*TCombobox*Listbox.background", PANEL)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.selectBackground", "#2e2650")
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        st.configure("TProgressbar", background=ACCENT, troughcolor=PANEL, borderwidth=0)
        st.configure("TLabelframe", background=BG, foreground=DIM, borderwidth=0)
        st.configure("TLabelframe.Label", background=BG, foreground=DIM)
        st.configure("Dark.TCombobox", fieldbackground=PANEL, background=PANEL,
                     foreground=FG, arrowcolor="#a9b4c4", bordercolor="#2b3140",
                     lightcolor="#2b3140", darkcolor="#2b3140", padding=4,
                     selectbackground=PANEL, selectforeground=FG)
        st.map("Dark.TCombobox",
               fieldbackground=[("readonly", PANEL), ("disabled", PANEL)],
               foreground=[("readonly", FG), ("disabled", "#5d6472")],
               background=[("readonly", PANEL), ("active", PANEL)],
               selectbackground=[("readonly", PANEL)],
               selectforeground=[("readonly", FG)],
               arrowcolor=[("readonly", "#a9b4c4"), ("active", "#d5dae3")])
        for name in ("Dark.Vertical.TScrollbar",):
            st.configure(name, background="#2f3542", troughcolor="#15181f",
                         bordercolor="#15181f", lightcolor="#15181f",
                         darkcolor="#15181f", arrowcolor="#8b96a8",
                         gripcount=0, arrowsize=8, relief="flat", width=10)
            st.map(name,
                   background=[("active", "#3f4759"), ("pressed", "#4c5569")],
                   arrowcolor=[("active", "#e2e6ee"), ("pressed", "#ffffff")])

    def _build(self):
        st = ttk.Style(self)
        st.configure("Slim.Horizontal.TProgressbar", troughcolor=CARD, background=ACCENT,
                     bordercolor=CARD, lightcolor=ACCENT, darkcolor=ACCENT, thickness=4)

        # 头部
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=20, pady=(18, 12))
        logo(head, BG).pack(side="left")
        ht = tk.Frame(head, bg=BG)
        ht.pack(side="left", padx=(10, 0))
        tk.Label(ht, text=APP_NAME, bg=BG, fg="#ffffff",
                 font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w")
        tk.Label(ht, text="检查并修复 AE 工程里丢失的素材链接", bg=BG, fg=DIM,
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w")
        tk.Label(head, text="v" + APP_VER, bg="#232833", fg=DIM,
                 font=("Microsoft YaHei UI", 8), padx=8, pady=2).pack(side="right", padx=(8, 0))
        flat_button(head, "使用说明", self.show_help).pack(side="right")

        # 工程卡片
        c1 = card(self)
        c1.pack(fill="x", padx=20)
        inner = tk.Frame(c1, bg=CARD)
        inner.pack(fill="x", padx=14, pady=12)
        caption(inner, "工程文件").pack(anchor="w")
        row = tk.Frame(inner, bg=CARD)
        row.pack(fill="x", pady=(5, 0))
        self.var_proj = tk.StringVar(value=self.cfg.get("project") or "")
        ent = tk.Entry(row, textvariable=self.var_proj, bg="#20242c", fg=FG, insertbackground=FG,
                       relief="flat", bd=0, font=FONT, highlightthickness=1,
                       highlightbackground="#2f3542", highlightcolor=ACCENT)
        ent.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        flat_button(row, "选择…", self.pick_project).pack(side="left")

        row2 = tk.Frame(inner, bg=CARD)
        row2.pack(fill="x", pady=(10, 0))
        tk.Label(row2, text="After Effects", bg=CARD, fg=DIM,
                 font=("Microsoft YaHei UI", 8)).pack(side="left")
        self.var_ae = tk.StringVar()
        self.ae_map = dict(self.installs)
        names = []
        for name, _path in self.installs:
            for year in range(2021, 2027):
                if name.endswith(" %d" % year):
                    names.append(name)
                    break
        self.cb_ae = ttk.Combobox(row2, textvariable=self.var_ae, values=names, style="Dark.TCombobox",
                                  state="readonly", width=30, font=FONT)
        if names:
            want = self.cfg.get("ae") or ""
            self.var_ae.set(want if want in names else names[0])
            self.cb_ae.current(names.index(self.var_ae.get()))
        else:
            tk.Label(row2, text="本机没有 2021–2026 的 After Effects，请先安装", bg=CARD, fg=BAD_C,
                     font=("Microsoft YaHei UI", 8)).pack(side="left", padx=8)
        self.cb_ae.pack(side="left", padx=(10, 0), ipady=3)

        # 目录卡片
        c2 = card(self)
        c2.pack(fill="x", padx=20, pady=(10, 0))
        inner2 = tk.Frame(c2, bg=CARD)
        inner2.pack(fill="x", padx=14, pady=12)
        t = tk.Frame(inner2, bg=CARD)
        t.pack(fill="x")
        caption(t, "素材搜索目录").pack(side="left")
        tk.Label(t, text="按顺序递归查找同名文件", bg=CARD, fg="#5d6472",
                 font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(8, 0))
        self.var_idx = tk.BooleanVar(value=bool(self.cfg.get("index_search", True)))
        self.var_idx.trace_add("write", lambda *a: self._sync_everything())
        t_idx = Toggle(t, "缺失后全盘补搜", self.var_idx, bg=CARD)
        t_idx.pack(side="right")
        Tip(t_idx, "一轮检查跑完还有缺失时，拿文件名到全盘再搜一遍（用自带 Everything）。\n"
                   "搜到就把所在目录加进上面的素材目录，再点一次「开始检查」即可接上。\n"
                   "注意：只按文件名匹配、不看内容，同名文件可能认错；\n"
                   "跑完请看日志里的 INDEX + 目录行，确认加进来的目录是对的。\n"
                   "首次使用会申请一次管理员权限（弹 UAC）；关掉则只按你指定的目录找。")

        listrow = tk.Frame(inner2, bg=CARD)
        listrow.pack(fill="x", pady=(6, 0))
        self.lb_dirs = tk.Listbox(listrow, height=4, bg="#20242c", fg=FG, selectbackground=DEEPBLUE,
                                  selectforeground="#ffffff", highlightthickness=0, borderwidth=0,
                                  font=FONT, activestyle="none")
        self.lb_dirs.pack(side="left", fill="x", expand=True)
        sb = ttk.Scrollbar(listrow, orient="vertical", command=self.lb_dirs.yview, style="Dark.Vertical.TScrollbar")
        sb.pack(side="left", fill="y")
        self.lb_dirs.configure(yscrollcommand=sb.set)
        for d in (self.cfg.get("dirs") or []):
            if isinstance(d, str) and os.path.isdir(d):
                self.lb_dirs.insert("end", d)
        btns = tk.Frame(listrow, bg=CARD)
        btns.pack(side="left", fill="y", padx=(8, 0))
        flat_button(btns, "添加…", self.add_dir).pack(fill="x", pady=(0, 4))
        flat_button(btns, "移除", self.del_dir).pack(fill="x", pady=(0, 4))
        flat_button(btns, "自动推断", self.guess_dirs).pack(fill="x")

        # 选项卡片
        c3 = card(self)
        c3.pack(fill="x", padx=20, pady=(10, 0))
        inner3 = tk.Frame(c3, bg=CARD)
        inner3.pack(fill="x", padx=14, pady=12)
        self.var_dry = tk.BooleanVar(value=bool(self.cfg.get("dry_run", False)))
        self.var_cons = tk.BooleanVar(value=bool(self.cfg.get("consolidate", False)))
        self.var_copy = tk.BooleanVar(value=bool(self.cfg.get("copy_first", True)))
        self.var_save = tk.BooleanVar(value=bool(self.cfg.get("save_project", True)))
        o1 = tk.Frame(inner3, bg=CARD)
        o1.pack(fill="x")
        Toggle(o1, "只体检（不改工程、不保存）", self.var_dry).pack(side="left")
        Toggle(o1, "先复制一份再改", self.var_copy).pack(side="left", padx=(22, 0))
        Toggle(o1, "收编到工程 Footages", self.var_cons).pack(side="left", padx=(22, 0))
        t_close = Toggle(o1, "完成后关闭本次打开的 AE", self.auto_close)
        t_close.pack(side="right")
        Tip(t_close, "只关本程序为了跑脚本而启动的那个 AfterFX（按进程号，先温和退出再强结束）。\n"
                   "你自己开的 AE 不会被关。\n"
                   "有缺失需要补搜时不会关，因为还要再跑一轮。\n"
                   "关掉这一项，AE 就留在打开状态给你看结果。")
        self._persist()
        for v in (self.var_dry, self.var_copy, self.var_cons, self.auto_close,
                  self.var_save, self.var_idx, self.var_ae, self.var_proj):
            v.trace_add("write", self._persist)

        # 操作条
        act = tk.Frame(self, bg=BG)
        act.pack(fill="x", padx=20, pady=(12, 0))
        self.btn_run = flat_button(act, "开始检查", self.start, primary=True)
        self.btn_run.pack(side="left")
        self.btn_stop = flat_button(act, "停止", self.stop)
        self.btn_stop.configure(state="disabled")
        self.btn_stop.pack(side="left", padx=(8, 0))
        flat_button(act, "打开报告目录", self.open_report_dir).pack(side="left", padx=(8, 0))
        flat_button(act, "缺失清单", self.show_missing).pack(side="left", padx=(8, 0))

        # 进度
        self.prog = ttk.Progressbar(self, mode="indeterminate", style="Slim.Horizontal.TProgressbar")
        self.prog.pack(fill="x", padx=20, pady=(12, 0))
        self.var_status = tk.StringVar(value="就绪")
        tk.Label(self, textvariable=self.var_status, bg=BG, fg=DIM,
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=20, pady=(6, 0))

        # 结果条
        kc = card(self)
        kc.pack(fill="x", padx=20, pady=(10, 0))
        krow = tk.Frame(kc, bg=CARD, height=62)
        krow.pack(fill="x")
        krow.pack_propagate(False)
        self.k_ok = self._kpi(krow, "已在线", "--", 0)
        self._sep(krow, 1)
        self.k_fix = self._kpi(krow, "本次重连", "--", 2)
        self._sep(krow, 3)
        self.k_miss = self._kpi(krow, "仍缺失", "--", 4)

        # 日志卡片
        lc = card(self)
        lc.pack(fill="both", expand=True, padx=20, pady=(10, 18))
        logwrap = tk.Frame(lc, bg=CARD)
        logwrap.pack(fill="both", expand=True, padx=1, pady=1)
        self.txt = tk.Text(logwrap, bg="#12141a", fg="#c8d0dc", insertbackground=FG,
                           font=MONO, borderwidth=0, highlightthickness=0, wrap="none",
                           padx=12, pady=10)
        self.txt.pack(side="left", fill="both", expand=True)
        tsb = ttk.Scrollbar(logwrap, orient="vertical", command=self.txt.yview, style="Dark.Vertical.TScrollbar")
        tsb.pack(side="left", fill="y")
        self.txt.configure(yscrollcommand=tsb.set, state="disabled")
        self.txt.tag_configure("ok", foreground=OK_C)
        self.txt.tag_configure("bad", foreground=BAD_C)
        self.txt.tag_configure("warn", foreground=WARN_C)
        self.txt.tag_configure("dim", foreground=DIM)
        self.txt.tag_configure("head", foreground="#b79cff")

    def _sep(self, parent, col):
        f = tk.Frame(parent, bg=CARD_LINE, width=1)
        f.grid(row=0, column=col, sticky="ns", pady=12)
        parent.grid_columnconfigure(col, weight=0)

    def _kpi(self, parent, label, value, col):
        cell = tk.Frame(parent, bg=CARD)
        cell.grid(row=0, column=col, sticky="nsew", padx=18, pady=8)
        v = tk.Label(cell, text=value, bg=CARD, fg="#ffffff",
                     font=("Microsoft YaHei UI", 19, "bold"), anchor="w")
        v.pack(anchor="w")
        tk.Label(cell, text=label, bg=CARD, fg=DIM,
                 font=("Microsoft YaHei UI", 8), anchor="w").pack(anchor="w")
        parent.grid_columnconfigure(col, weight=1)
        return v


    def show_help(self):
        win = tk.Toplevel(self)
        win.title("使用说明 - " + APP_NAME)
        win.configure(bg=BG)
        win.geometry("780x712")
        win.transient(self)
        try:
            win.iconbitmap(default="")
        except tk.TclError:
            pass

        head = tk.Frame(win, bg=BG)
        head.pack(fill="x", padx=18, pady=(16, 8))
        tk.Label(head, text="使用说明", bg=BG, fg="#ffffff",
                 font=("Microsoft YaHei UI", 14, "bold")).pack(side="left")
        tk.Label(head, text="  照着做就行，看不懂的段落可以跳过", bg=BG, fg=DIM,
                 font=("Microsoft YaHei UI", 8)).pack(side="left")

        box = card(win)
        box.pack(fill="both", expand=True, padx=18)
        txt = tk.Text(box, bg="#12141a", fg="#c8d0dc", font=("Microsoft YaHei UI", 9),
                      relief="flat", bd=0, highlightthickness=0, wrap="word",
                      padx=16, pady=14, spacing1=2, spacing3=3)
        txt.pack(side="left", fill="both", expand=True, padx=(1, 0), pady=1)
        sb = ttk.Scrollbar(box, orient="vertical", command=txt.yview, style="Dark.Vertical.TScrollbar")
        sb.pack(side="left", fill="y")
        txt.configure(yscrollcommand=sb.set)
        txt.tag_configure("h", foreground="#b79cff", font=("Microsoft YaHei UI", 10, "bold"),
                          spacing1=10, spacing3=4)
        txt.tag_configure("k", foreground="#ffffff", font=("Microsoft YaHei UI", 9, "bold"))
        for line in HELP_TEXT.splitlines():
            if line.startswith("【") and line.rstrip().endswith("】"):
                txt.insert("end", line + "\n", "h")
            else:
                txt.insert("end", line + "\n")
        txt.configure(state="disabled")

        foot = tk.Frame(win, bg=BG)
        foot.pack(fill="x", padx=18, pady=(6, 16))
        flat_button(foot, "关闭", win.destroy, primary=True).pack(side="right")

    # ---------- 交互 ----------
    def pick_project(self):
        p = filedialog.askopenfilename(title="选择 AE 工程",
                                       filetypes=[("After Effects 工程", "*.aep *.aepx"), ("所有文件", "*.*")])
        if p:
            self.var_proj.set(p)
            self.guess_dirs(keep_existing=False)

    def add_dir(self):
        d = filedialog.askdirectory(title="选择素材目录")
        if d:
            items = list(self.lb_dirs.get(0, "end"))
            if d not in items:
                self.lb_dirs.insert("end", d)
            self._persist()

    def del_dir(self):
        for i in reversed(self.lb_dirs.curselection()):
            self.lb_dirs.delete(i)
        self._persist()

    def guess_dirs(self, keep_existing=True):
        proj = self.var_proj.get().strip()
        if not proj:
            return
        base = os.path.dirname(proj)
        cand = [os.path.join(base, "Footages"), base,
                os.path.join(base, "素材"), os.path.dirname(base)]
        if not keep_existing:
            self.lb_dirs.delete(0, "end")
        have = set(self.lb_dirs.get(0, "end"))
        for c in cand:
            if os.path.isdir(c) and c not in have:
                self.lb_dirs.insert("end", c)
        self._persist()

    def log(self, s, tag=None):
        self.txt.configure(state="normal")
        self.txt.insert("end", s + "\n", tag or ())
        self.txt.see("end")
        self.txt.configure(state="disabled")

    # ---------- 运行 ----------
    def start(self):
        if self.running:
            return
        proj = self.var_proj.get().strip()
        if not proj or not os.path.isfile(proj):
            messagebox.showwarning(APP_NAME, "请先选择存在的 .aep 工程文件")
            return
        if not self.installs:
            messagebox.showerror(APP_NAME, "没找到 After Effects 安装目录")
            return
        roots = list(self.lb_dirs.get(0, "end"))
        if not roots:
            self.guess_dirs()
            roots = list(self.lb_dirs.get(0, "end"))
        if not roots:
            messagebox.showwarning(APP_NAME, "请至少添加一个素材搜索目录")
            return

        ae_name = self.var_ae.get().replace("（未安装）", "").strip()
        ae_exe = self.ae_map.get(ae_name)
        if not ae_exe:
            messagebox.showwarning(APP_NAME, "本机没有装 %s。\n请先安装该版本，或在上面选一个已安装的版本。" % ae_name)
            return

        # 工作副本
        work = proj
        if not self.var_dry.get() and self.var_copy.get():
            stem, ext = os.path.splitext(proj)
            work = stem + "_fixed" + ext
            try:
                shutil.copy2(proj, work)
            except Exception as e:
                messagebox.showerror(APP_NAME, f"复制工程失败：{e}")
                return

        out_dir = os.path.dirname(os.path.abspath(work))
        self.out_dir = out_dir
        for name in ("_relink_log.txt", "_relink_report.json", "_relink_missing.txt"):
            p = os.path.join(out_dir, name)
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

        jsx = JSX_TEMPLATE
        jsx = jsx.replace("__PROJECT__", js_str(work))
        jsx = jsx.replace("__ROOTS__", js_list(roots))
        jsx = jsx.replace("__DEPTH__", "6")
        jsx = jsx.replace("__DRYRUN__", "true" if self.var_dry.get() else "false")
        jsx = jsx.replace("__CONSOLIDATE__", "true" if self.var_cons.get() else "false")
        jsx = jsx.replace("__CONSOLIDATE_DIR__", js_str(os.path.join(out_dir, "Footages")))
        jsx = jsx.replace("__SAVE__", "true" if self.var_save.get() else "false")
        jsx = jsx.replace("__REPORT_DIR__", js_str(out_dir))
        jsx = jsx.replace("__APPVER__", APP_VER)
        jsx_path = os.path.join(out_dir, "_media_doctor.jsx")
        with open(jsx_path, "w", encoding="utf-8") as fh:
            fh.write(jsx)

        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")
        self.k_ok.configure(text="--", fg="#ffffff")
        self.k_fix.configure(text="--", fg="#ffffff")
        self.k_miss.configure(text="--", fg="#ffffff")

        self.running = True
        self.start_ts = time.time()
        self.report = None
        self.btn_run.configure(state="disabled", text="运行中…")
        self.btn_stop.configure(state="normal")
        self.prog.start(12)
        self.log(f"工程    {work}", "head")
        if work != proj:
            self.log(f"原件    {proj}  （不改动）", "dim")
        self.log(f"AE      {ae_exe}", "dim")
        for r in roots:
            self.log(f"搜索    {r}", "dim")
        self.log("")
        self.log("正在启动 After Effects，首次可能要 30–60 秒…", "warn")
        self.log("若 AE 弹出字体/缺文件提示，点「确定」或「继续」即可，脚本会接着跑。", "dim")
        self.log("")

        self.worker = threading.Thread(target=self._run, args=(ae_exe, jsx_path, out_dir, work), daemon=True)
        self.worker.start()
        self.after(400, self._poll)

    def _run(self, ae_exe, jsx_path, out_dir, work):
        try:
            self.proc = subprocess.Popen([ae_exe, "-r", jsx_path], cwd=os.path.dirname(ae_exe))
        except Exception as e:
            self._queue_log(f"启动 AE 失败：{e}", "bad")
            self.worker = None

    # 线程安全的日志缓冲
    _buf = []

    def _queue_log(self, s, tag=None):
        App._buf.append((s, tag))

    def _poll(self):
        if App._buf:
            for s, tag in App._buf:
                self.log(s, tag)
            App._buf = []

        out_dir = getattr(self, "out_dir", None)
        if out_dir and os.path.isdir(out_dir):
            logp = os.path.join(out_dir, "_relink_log.txt")
            if os.path.exists(logp):
                try:
                    with open(logp, "r", encoding="utf-8", errors="replace") as fh:
                        text = fh.read()
                    self._show_log_text(text)
                except OSError:
                    pass
            rep = os.path.join(out_dir, "_relink_report.json")
            if os.path.exists(rep) and self.report is None:
                try:
                    with open(rep, "r", encoding="utf-8", errors="replace") as fh:
                        self.report = json.load(fh)
                    self._finish()
                    return
                except (OSError, json.JSONDecodeError):
                    pass

        if self.running:
            el = int(time.time() - self.start_ts)
            self.var_status.set(f"运行中… {el}s    （AE 正在打开工程并逐条重连）")
            self.after(700, self._poll)

    def _show_log_text(self, text):
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        for line in text.splitlines():
            tag = None
            if line.startswith("RELINKED"):
                tag = "ok"
            elif line.startswith("MISSING") or line.startswith("FAILED"):
                tag = "bad"
            elif line.startswith("ZERO") or line.startswith("WOULD-FIX"):
                tag = "warn"
            elif line.startswith("SUMMARY"):
                tag = "head"
            elif line.startswith(("SCAN", "INDEX", "OPEN", "USE-OPEN", "SCRIPT", "PROJECT", "ITEMS", "SAVED")):
                tag = "dim"
            self.txt.insert("end", line + "\n", tag or ())
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _finish(self):
        self.running = False
        self.prog.stop()
        self.btn_run.configure(state="normal", text="开始检查")
        self.btn_stop.configure(state="disabled")
        st = (self.report or {}).get("stats", {})
        self.k_ok.configure(text=str(st.get("ok", "--")), fg=OK_C)
        self.k_fix.configure(text=str(st.get("fixed", "--")), fg=ACCENT)
        self.k_miss.configure(text=str(st.get("missing", "--")),
                              fg=BAD_C if st.get("missing", 0) else OK_C)
        if self.var_dry.get():
            self.var_status.set("体检完成（工程未改动）")
        else:
            self.var_status.set("修复完成，工程已保存")
        miss = (self.report or {}).get("missing", [])
        names = [m.get("name", "") for m in miss if m.get("name")]
        if self.var_idx.get() and names:
            self.var_status.set("正在用系统索引补搜 %d 个缺失文件…" % len(names))
            threading.Thread(target=self._index_backfill, args=(names,), daemon=True).start()
        elif self.auto_close.get() and self.proc is not None:
            self._close_ae()

    def _index_backfill(self, names):
        """缺失清单 → 系统索引查找 → 命中的父目录加进搜索目录。"""
        hits = index_lookup(names)
        dirs = []
        for name in names:
            for path in hits.get(name.lower(), [])[:3]:
                d = os.path.dirname(path)
                if d and d not in dirs:
                    dirs.append(d)
        have = set()
        try:
            have = {self.lb_dirs.get(i) for i in range(self.lb_dirs.size())}
        except Exception:
            pass
        added = [d for d in dirs if d not in have]
        found = sum(len(v) for v in hits.values())

        def apply():
            for d in added:
                self.lb_dirs.insert("end", d)
            src = "Everything" if find_everything() else "Windows 索引"
            if found:
                self.log("INDEX     " + src + " 命中 %d 个文件，新增 %d 个搜索目录" % (found, len(added)))
                for d in added:
                    self.log("INDEX     + " + d)
                self.var_status.set("索引补搜命中 %d 个，再点一次「开始检查」即可接上" % found)
            else:
                self.log("INDEX     " + src + " 未命中（本机可能确实没有，或所在磁盘没建索引）")
                self.var_status.set("索引未命中，缺失文件本机可能确实没有")
            self.log("INDEX     补搜结束后 AE 保持打开（需要你再跑一轮才开始重连）")
        self.after(0, apply)

    def _sync_everything(self):
        """开关关掉时立即收走内置 Everything；打开时只放行，不预启动。"""
        try:
            on = bool(self.var_idx.get())
        except Exception:
            return
        if on:
            self.log("INDEX     全盘补搜已打开（需要时才拉起 Everything）")
            return
        n = stop_bundled_everything(self.log)
        self.log("INDEX     全盘补搜已关闭，内置 Everything %s"
                 % ("已停止" if n else "本来就没在运行"))

    def _persist(self, *_a):
        if not hasattr(self, "var_dry") or not hasattr(self, "lb_dirs"):
            return
        try:
            save_cfg({
                "dry_run": bool(self.var_dry.get()),
                "copy_first": bool(self.var_copy.get()),
                "consolidate": bool(self.var_cons.get()),
                "close_ae": bool(self.auto_close.get()),
                "save_project": bool(self.var_save.get()),
                "index_search": bool(self.var_idx.get()),
                "ae": self.var_ae.get(),
                "project": self.var_proj.get(),
                "dirs": list(self.lb_dirs.get(0, "end")),
                "version": APP_VER,
            })
        except Exception:
            pass

    def _on_close(self):
        self._persist()
        try:
            stop_bundled_everything()
        except Exception:
            pass
        self.destroy()

    def _close_ae(self):
        """只结束本程序自己拉起的那个 AfterFX，别人的 AE 一律不碰。"""
        proc = self.proc
        pid = getattr(proc, "pid", None)
        if not pid:
            return

        def kill():
            for extra in ([], ["/F"]):
                try:
                    subprocess.run(["taskkill", "/PID", str(pid), "/T"] + extra,
                                   capture_output=True, timeout=20)
                except Exception:
                    pass
                for _ in range(10):
                    time.sleep(0.4)
                    try:
                        if proc.poll() is not None:
                            return
                    except Exception:
                        return
        threading.Thread(target=kill, daemon=True).start()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
        self.running = False
        self.prog.stop()
        self.btn_run.configure(state="normal", text="开始检查")
        self.btn_stop.configure(state="disabled")
        self.var_status.set("已停止")

    def open_report_dir(self):
        p = self.var_proj.get().strip()
        if not p:
            return
        base, ext = os.path.splitext(p)
        cand = base + "_fixed" + ext
        d = os.path.dirname(os.path.abspath(cand if os.path.exists(cand) else p))
        try:
            os.startfile(d)
        except OSError as e:
            messagebox.showerror(APP_NAME, str(e))

    def show_missing(self):
        p = self.var_proj.get().strip()
        if not p:
            return
        base, ext = os.path.splitext(p)
        cand = base + "_fixed" + ext
        d = os.path.dirname(os.path.abspath(cand if os.path.exists(cand) else p))
        f = os.path.join(d, "_relink_missing.txt")
        if not os.path.exists(f):
            messagebox.showinfo(APP_NAME, "还没有缺失清单，先跑一次检查")
            return
        win = tk.Toplevel(self)
        win.title("缺失素材清单")
        win.geometry("780x480")
        win.configure(bg=BG)
        txt = tk.Text(win, bg="#12141a", fg="#c8d0dc", font=MONO, borderwidth=0,
                      highlightthickness=0, padx=10, pady=8, wrap="none")
        txt.pack(fill="both", expand=True, padx=12, pady=12)
        with open(f, "r", encoding="utf-8", errors="replace") as fh:
            txt.insert("1.0", fh.read())
        txt.configure(state="disabled")


def build_jsx(project, roots, dry, consolidate, cons_dir, save, out_dir, depth=6):
    jsx = JSX_TEMPLATE
    jsx = jsx.replace("__PROJECT__", js_str(project))
    jsx = jsx.replace("__ROOTS__", js_list(roots))
    jsx = jsx.replace("__DEPTH__", str(depth))
    jsx = jsx.replace("__DRYRUN__", "true" if dry else "false")
    jsx = jsx.replace("__CONSOLIDATE__", "true" if consolidate else "false")
    jsx = jsx.replace("__CONSOLIDATE_DIR__", js_str(cons_dir))
    jsx = jsx.replace("__SAVE__", "true" if save else "false")
    jsx = jsx.replace("__REPORT_DIR__", js_str(out_dir))
    jsx = jsx.replace("__APPVER__", APP_VER)
    return jsx


def run_cli(argv):
    """无界面模式：AE素材医生 --cli <工程> [--dry] [--roots A;B] [--no-save]"""
    project = argv[0]
    dry = "--dry" in argv
    save = "--no-save" not in argv
    roots = []
    if "--roots" in argv:
        roots = [r for r in argv[argv.index("--roots") + 1].split(";") if r]
    if not roots:
        base = os.path.dirname(os.path.abspath(project))
        roots = [os.path.join(base, "Footages"), base]

    installs = find_ae_installs()
    if not installs:
        print("未找到 After Effects 安装")
        return 2

    work = project
    if not dry:
        stem, ext = os.path.splitext(project)
        work = stem + "_fixed" + ext
        shutil.copy2(project, work)

    out_dir = os.path.dirname(os.path.abspath(work))
    for name in ("_relink_log.txt", "_relink_report.json", "_relink_missing.txt"):
        p = os.path.join(out_dir, name)
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass

    jsx_path = os.path.join(out_dir, "_media_doctor.jsx")
    with open(jsx_path, "w", encoding="utf-8") as fh:
        fh.write(build_jsx(work, roots, dry, False, os.path.join(out_dir, "Footages"), save, out_dir))

    ae_exe = installs[0][1]
    print(f"AE   {ae_exe}")
    print(f"工程 {work}")
    subprocess.Popen([ae_exe, "-r", jsx_path], cwd=os.path.dirname(ae_exe))

    rep = os.path.join(out_dir, "_relink_report.json")
    t0 = time.time()
    while time.time() - t0 < 240:
        if os.path.exists(rep):
            try:
                with open(rep, "r", encoding="utf-8", errors="replace") as fh:
                    data = json.load(fh)
                st = data.get("stats", {})
                print(f"完成：已在线 {st.get('ok')} / 重连 {st.get('fixed')} / 缺失 {st.get('missing')}")
                return 0
            except (OSError, json.JSONDecodeError):
                pass
        time.sleep(1)
    print("超时：没有等到报告，可能 AE 弹了对话框等确认")
    return 1


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--cli":
        sys.exit(run_cli(sys.argv[2:]))
    app = App()
    app.protocol("WM_DELETE_WINDOW", app._on_close)
    app.mainloop()