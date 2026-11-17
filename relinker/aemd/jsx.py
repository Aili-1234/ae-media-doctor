# -*- coding: utf-8 -*-
"""aemd.jsx —— JSX 模板、生成、校验（R9 / R10 / D4 / F-10 / F-18）。

负责：
  · `JSX_TEMPLATE`：ExtendScript 模板（v1.9.8 那份**原样搬**，315 行，见文件末尾）
  · `target_path()`：JSX 本体落点的**唯一出口**（断言纯 ASCII，R9 / F-19）
  · `render()`：填占位符 → 返回文本（与一代逐字节一致，供对拍）
  · `build()`：render → validate → 落盘 → 返回落盘路径
  · `validate()`：占位符残留（D4）+ ES3 保留字（R10）+ 可选 `node --check` 三闸
  · `js_str()` / `js_list()`：Python 值 → JS 字面量

不负责：
  · 判断「要不要跑脚本」（runner）
  · 决定工程目录（那是 paths；本模块绝不往工程目录写 JSX）
  · 执行脚本（执行发生在 AE 里；本模块只保证文件放到 AE 读得到的位置）

依赖：`.const`、`.paths`。**不许** import winproc / aehost / runner / ui。

⚠ 一代的两个坑（都已在下方代码里落成断言）：
  · JSX 落在含中文/空格/括号的工程目录 → `-r` 静默失败（AE 读不到脚本，不报错）。
    → v2 落点只有 `C:\\ProgramData\\AEMediaDoctor\\`，target_path() 里断言 isascii()。
  · 模板里出现 ES3 保留字（`var short` / `var int`）→ AE 拒绝执行整个脚本。
    → validate() 的 ES3 扫描 + node --check 双闸。
"""


import os
import re
import subprocess

from . import const, paths
from .mapping import js_object as _js_object

# D4（§8.1）：占位符残留必须当场炸。
# 一代教训 F-10：模板里 `__LAUNCH_MS__` 忘填 → 生成出来的 JSX 里是个合法标识符，
# `node --check` 完全合法，于是假通过，跑到 AE 里 `var CFG = {...}` 求值时 ReferenceError，
# 脚本立刻中止、连日志都建不出来，CLI 只能干等超时。
# 所以判据不是"语法对不对"，而是"还有没有没填的占位符"。
PLACEHOLDER_RE = re.compile(r"__[A-Z][A-Z0-9_]*__")

# ES3/ExtendScript 里不能当标识符的保留字（AE 会整体拒绝执行，报「非法使用保留字」）
ES3_RESERVED = (
    "class", "enum", "int", "char", "long", "short", "byte", "boolean",
    "float", "double", "goto", "native", "synchronized", "throws",
    "transient", "volatile", "abstract", "final", "private", "public",
    "protected", "implements", "interface", "package", "import",
    "export", "extends", "super", "const", "let", "yield", "await",
)


#: 1.1.0 手工映射表的 JSX 片段。**只在这里定义一次**：
#: 模板正文里必须原样出现（`tests\test_mapping.py` 断言出现次数 = 1），
#: KNOWN_DIFFS 也引用同一份常量 —— 否则改一处、忘另一处，对拍就会以「片段没出现」炸。
#: 为什么整块登记为差异：v1.9.8 没有这个能力，对拍要把这一整块删掉才能逐字节比。
MAPPING_JSX_BLOCK = r"""    /* ===== 1.1.0 手工映射表：全名没中时，先问用户自己指过的替身 =====
       查表顺序「原完整路径 → 原文件名」，越具体越优先。
       命中且目标文件在 → 直接用；目标文件不在 → 记 MISSING 并写明是这条映射指错了，
       绝不悄悄退回后面的自动匹配（那会让用户以为「我指的没生效」，排查最费时间）。 */
    if (!cand){
        var mapKey = curPath ? String(curPath).toLowerCase() : "";
        var mapHit = (mapKey && MAPPING[mapKey]) ? MAPPING[mapKey] : "";
        if (!mapHit && name) mapHit = MAPPING[String(name).toLowerCase()] || "";
        if (mapHit){
            var mapFile = new File(mapHit);
            if (exists(mapFile) && sizeOf(mapFile) > 0){
                cand = mapFile.fsName;
                how = "映射表";
                stats.mapped++;
            } else {
                stats.missing++;
                var mapReason = "映射表指向的文件不存在: " + mapHit;
                missingItems.push({ name: item.name, original: curPath, reason: mapReason });
                missLines.push(name + "\t" + curPath);
                L("MISSING   " + item.name + "  |  " + curPath + "  |  " + mapReason);
                return;
            }
        }
    }

"""


#: 与 v1.9.8 的**有意差异**（逐字节对拍脚本靠这张表把参照物还原，而不是放宽阈值）。
#: 每条 = (v2 里的片段, v1.9.8 里的原片段)。只允许登记真修过 bug 的地方。
#: 空串作旧的片段 = 纯新增（v1.9.8 里没有），对拍时直接删掉。
KNOWN_DIFFS = [
    # 2026-11-15 产品改名（AE 素材医生 → 拾链 Relinker 1.0.0）：
    # 只动产品名，不动脚本行为。对拍时把名字还回去再比字节。
    (" * 拾链 生成的重连脚本", " * AE 素材医生 生成的重连脚本"),
    ('L("SCRIPT    拾链 v', 'L("SCRIPT    AE 素材医生 v'),
    # D5：flush() 之后 L() 再写会写进已关闭的文件（被 catch 吞掉）→ DONE-OK 5/5 缺失
    ("""function L(s){
  /* D5：中途 flush() 会把文件关掉，之后再 L() 就写进一个已关闭的文件、被 catch 静默吞掉
     （真机验收里 DONE-OK 5/5 缺失就是这个原因）。所以写之前先确保句柄是开的。 */
  if (!LOG_OPEN) { try { LOG_OPEN = f.open("a"); } catch(e){ LOG_OPEN = false; } }
    lines.push(s);""",
     """function L(s){
    lines.push(s);"""),
    # 同上：flush 里同步把 LOG_OPEN 置回 false，否则上面的重开判据永远不触发
    ("""function flush(){ try { f.close(); } catch(e){} LOG_OPEN = false; }""",
     """function flush(){ try { f.close(); } catch(e){} }"""),
    # 2026-11-15 P0（真机 0 字节日志）：ExtendScript 的 writeln 是带缓冲的，
    # 只在 flush/close 时落盘 —— AE 被杀或崩掉时，日志文件是 0 字节、现场全丢。
    # 每写一行就跟一次 flush：几十行日志，代价可忽略，换来「死也留个话」。
    ("""    try { if (LOG_OPEN) { f.writeln(s); f.flush(); } } catch(e){}""",
     """    try { if (LOG_OPEN) f.writeln(s); } catch(e){}"""),
    # D5 副作用：报告里的 logWritable 改用 LOG_OK（当初有没有打开成功），
    # 否则 flush 之后写报告会误报「日志文件没写成」
    ("""
try { LOG_OPEN = f.open("w"); } catch(e){ LOG_OPEN = false; }
var LOG_OK = LOG_OPEN;      /* D5 副作用修正：报告里写「当初有没有打开成功」，不看此刻句柄 */""",
     """
try { LOG_OPEN = f.open("w"); } catch(e){ LOG_OPEN = false; }"""),
    # 注意：这里含 JS 转义 `\n`，必须用 raw 字符串，否则 Python 会把它变成一个真换行、对不上
    (r"""    js += '  "logWritable": ' + (LOG_OK ? "true" : "false") + ',\n';""",
     r"""    js += '  "logWritable": ' + (LOG_OPEN ? "true" : "false") + ',\n';"""),
    # D6：TIME 的起点原来是「构建 JSX 的时刻」，不是 AE 进程创建时刻 → 报 178.7s
    ("""if (CFG.launchMs) { L("TIME      AE 启动→脚本开始  +" + (((new Date()).getTime() - CFG.launchMs) / 1000).toFixed(1) + "s"); }""",
     """L("TIME      AE 启动→脚本开始  +" + (((new Date()).getTime() - CFG.launchMs) / 1000).toFixed(1) + "s");"""),
    # ---------------------------------------------------------------- 1.1.0 手工映射表
    # 四条纯新增（v1.9.8 没有映射表），对拍时整段删掉/替回即可：
    #   ① var MAPPING 那一行  ② relink() 里的查表分支  ③ stats 的 mapped 计数
    #   ④ SUMMARY 与报告 JSON 里的 mapped 字段
    # 注意 ① 在**渲染后**是 `var MAPPING = {};`（占位符已被替成空表），
    # 所以这里写的是渲染后的样子 —— 对拍用的 render 不传 mapping。
    ("/* 1.1.0 手工映射表：键是小写原文件名或原完整路径，值是目标文件的绝对路径 */\n"
     "var MAPPING = {};\n\n", ""),
    (MAPPING_JSX_BLOCK, ""),
    ("var stats = { total:0, ok:0, fixed:0, ambiguous:0, missing:0, broken:0, mapped:0 };",
     "var stats = { total:0, ok:0, fixed:0, ambiguous:0, missing:0, broken:0 };"),
    ('" zeroByte=" + stats.broken + " mapped=" + stats.mapped);',
     '" zeroByte=" + stats.broken);'),
    ("""', "zeroByte": ' + stats.broken + ', "mapped": ' + stats.mapped + ' },\\n';""",
     """', "zeroByte": ' + stats.broken + ' },\\n';"""),
    # 1.1.1：file 为 null 时用 missingFootagePath 找回原路径（缺件清单与映射表的完整路径键都靠它）
    (r"""    /* 1.1.1：file 为 null 时（素材彻底丢失）AE 仍记得它原来在哪。
       这个属性不读，缺件清单里就只剩 "(no file)"，手工映射表的「完整路径」键也立不起来。 */
    var missPath = "";
    try { if (src.missingFootagePath) missPath = String(src.missingFootagePath); } catch(e){}
    var curPath = cur ? cur.fsName : (missPath || "(no file)");""",
     r"""    var curPath = cur ? cur.fsName : "(no file)";"""),
    # 1.1.1：同目录消歧用的两个小工具（纯新增）
    (r"""
/* 1.1.1：同名多候选时，用「原来所在目录名」当消歧依据。
   同一文件名散落在多个素材盘时，目录名（如 02补帧镜头包 / Footages）往往还保留着，
   比「谁先扫到用谁」准得多 —— 两个独立的开源实现都是这么做的。 */
function parentNameOf(p){
    var s = String(p || "").replace(/\\/g, "/");
    var i = s.lastIndexOf("/");
    if (i <= 0) return "";
    var j = s.lastIndexOf("/", i - 1);
    return s.substring(j + 1, i).toLowerCase();
}
function pickSameParent(list, want){
    if (!want || !list) return null;
    for (var i = 0; i < list.length; i++){
        if (parentNameOf(list[i]) === want) return list[i];
    }
    return null;
}
""", ""),
    # 1.1.1：全名多候选 -> 先按原目录名筛
    (r"""    var origDir = parentNameOf(curPath);
    var exact = idxExact[name.toLowerCase()];
    if (exact && exact.length >= 1){
        if (exact.length > 1){
            /* 1.1.1：多候选取「原目录名相同」的那个；一个都没对上才退回首个，并在 how 里标明。 */
            var sameDir = pickSameParent(exact, origDir);
            cand = sameDir || exact[0];
            how = sameDir ? "全名匹配(同目录优先)" : "全名匹配(多候选取首个)";
            alts = exact.slice(0, 4);
        } else {
            cand = exact[0]; how = "全名匹配";
        }
    }""",
     r"""    var exact = idxExact[name.toLowerCase()];
    if (exact && exact.length >= 1){
        cand = exact[0]; how = "全名匹配";
        if (exact.length > 1) alts = exact.slice(1, 4);
    }"""),
    # 1.1.1：基名多候选 -> 同样先按原目录名筛（筛中不计 ambiguous）
    (r"""        var shortHits = idxShort[sk];
        if (shortHits && shortHits.length >= 1){
            if (shortHits.length > 1){
                /* 1.1.1：基名撞车同样先按原目录名筛；筛中就不算「猜」，不计 ambiguous。 */
                var shortSame = pickSameParent(shortHits, origDir);
                cand = shortSame || shortHits[0];
                if (shortSame){
                    how = "基名匹配(" + shortHits.length + " 个候选,同目录优先)";
                } else {
                    stats.ambiguous++;
                    how = "基名匹配(" + shortHits.length + " 个候选)";
                }
                alts = shortHits.slice(0, 4);
            } else {
                cand = shortHits[0];
                how = "基名匹配(" + shortHits.length + " 个候选)";
            }
        }""",
     r"""        var shortHits = idxShort[sk];
        if (shortHits && shortHits.length >= 1){
            cand = shortHits[0];
            how = "基名匹配(" + shortHits.length + " 个候选)";
            if (shortHits.length > 1){
                stats.ambiguous++;
                alts = shortHits.slice(1, 4);
            }
        }"""),
    # 1.1.1：replace 之后复查 missingFootagePath（只记 RELINK-SUSPECT，不动统计）
    (r"""        item.replace(new File(target));
        /* 1.1.1：replace() 不抛异常 ≠ 真的接上了。AE 仍把这条标成离线时如实留一句；
           不并进统计 —— 不同版本对 missingFootagePath 的清空时机不一致，
           先让它「说话」，真机跑两轮攒够证据再决定要不要计入 missing。 */
        var stillMissing = false;
        try { stillMissing = !!(item.mainSource && item.mainSource.missingFootagePath); } catch(e3){}
        if (stillMissing){
            L("RELINK-SUSPECT " + item.name + "  |  " + target + "  |  replace 没报错，但素材仍标记离线（可能没真接上）");
        }
""",
     r"""        item.replace(new File(target));
"""),
    # 1.1.5：分层 PSD/PSB/AI 重连后提示核对图层（只加一行日志，不动统计）
    (r"""        /* 1.1.5：分层 PSD/PSB/AI 是最容易被「接上但塌层」的一类 —— replace() 之后
           图层结构可能就没了，而用户只看到日志写着成功。AE 侧没有可靠的
           「这张到底是不是分层导入」判据（得看 .aepx 里的导入方式），所以对这类
           扩展名一律提示核对：宁可多一句，也不让人以为万事大吉。 */
        var lext = extOf(target);
        if (lext === "psd" || lext === "psb" || lext === "ai"){
            L("LAYER-WARN " + item.name + "  |  " + target + "  |  分层素材（." + lext + "）已重连，请核对图层结构是否完整");
        }
""", ""),
    # 1.1.6：只重试缺件的名单（渲染后是空数组，对拍要把这整块删掉）
    (r"""/* 1.1.6 只重试缺件：非空时只处理名单里的素材（名字来自 _relink_missing.txt 第一列） */
var ONLY = [];

""", ""),
    # 1.1.6：名单匹配用的两个小工具（纯新增；尾部空行留给模板原有的分隔）
    (r"""
/* 1.1.6：只重试缺件时判断「这条要不要处理」。
   名单来自上一次的缺件清单（第一列），所以三个名字都认：当前文件名、缺件原路径的
   文件名、素材项名 —— 任意一种对上就处理，避免因为改名而漏掉。 */
function baseNameOf(p){
    var s2 = String(p || "").replace(/\\/g, "/");
    return s2.substring(s2.lastIndexOf("/") + 1).toLowerCase();
}
function wantedByOnly(it){
    if (!ONLY || !ONLY.length) return true;
    var s2 = it.mainSource;
    var n1 = "", n2 = "";
    try { if (s2 && s2.file) n1 = String(s2.file.name).toLowerCase(); } catch(e){}
    try { if (s2 && s2.missingFootagePath) n2 = baseNameOf(s2.missingFootagePath); } catch(e){}
    var nm = String(it.name || "").toLowerCase();
    for (var i2 = 0; i2 < ONLY.length; i2++){
        if (ONLY[i2] === n1 || ONLY[i2] === n2 || ONLY[i2] === nm) return true;
    }
    return false;
}
""", ""),
    # 1.1.6：主循环按名单过滤
    (r"""    for (var i=1;i<=proj.numItems;i++){
        var ci = proj.item(i);
        if (!(ci instanceof FootageItem)) continue;
        /* 1.1.6：只重试缺件时跳过不在名单里的（日志留一行，便于事后核对跳过了什么） */
        if (!wantedByOnly(ci)) { L("SKIP-ONLY " + ci.name); continue; }
        relink(ci);
    }""",
     r"""    for (var i=1;i<=proj.numItems;i++){
        if (proj.item(i) instanceof FootageItem) relink(proj.item(i));
    }"""),
]


def residual_placeholders(text):
    """返回没被替掉的占位符（去重、保序）。非空 ⇒ build() 必须抛 RuntimeError。"""
    seen, out = set(), []
    for m in PLACEHOLDER_RE.finditer(text or ""):
        tok = m.group(0)
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def es3_violations(text):
    """模板里有没有把 ES3 保留字当标识符用（`var short = …` 会让 AE 拒绝执行）。"""
    bad = []
    for i, line in enumerate((text or "").splitlines(), 1):
        for w in ES3_RESERVED:
            if re.search(r"\b(var|const|let)\s+" + w + r"\b", line) or \
               re.search(r"\bfunction\s+" + w + r"\b", line):
                bad.append((i, w, line.strip()[:80]))
    return bad


def target_path(out_dir=None, name="relink.jsx"):
    """JSX 落点（R9）：永远纯 ASCII。**全库唯一出口**。

    参数 `out_dir` 不参与决策，保留只为接口兼容（一代用它做过降级备份的上下文）。
    返回值保证 `str.isascii()` —— 断言失败说明 paths 层被改坏了，必须当场炸。
    """
    d = paths.jsx_target_path(out_dir)
    p = os.path.join(d, name)
    assert paths.is_ascii_path(p), "JSX 落点必须纯 ASCII：%r" % (p,)
    return p


def js_str(s):
    """Python 字符串 → JS 双引号字面量。

    与一代逐字节一致：反斜杠一律换成 `/`（ExtendScript 认正斜杠），引号转义。
    """
    return '"' + str(s).replace("\\", "/").replace('"', '\\"') + '"'


def js_list(items):
    """字符串列表 → JS 数组字面量。"""
    return "[" + ", ".join(js_str(i) for i in items) + "]"


def render(project, roots, dry, consolidate, cons_dir, save, out_dir, depth=6,
           cons_mode="copy", launch_ms=None, work_dir=None, log_path=None,
           report_dir=None, state_dir=None, app_ver=None, mapping=None,
           only_missing=None):
    """把模板填成 JSX 文本（不落盘）。**与 v1.9.8 build_jsx() 逐字节一致**。

    `report_dir` / `state_dir` / `app_ver` 是 v2 的新入口（一代直接读模块常量），
    默认值复现一代行为，所以对拍时不用传。
    """
    if launch_ms is None:
        import time
        launch_ms = int(time.time() * 1000)
    mode = "move" if (str(cons_mode) == "move" or str(cons_mode).startswith("移动")) else "copy"
    repl = [
        ("__PROJECT__", js_str(project)),
        ("__ROOTS__", js_list(roots)),
        ("__DEPTH__", str(depth)),
        ("__DRYRUN__", "true" if dry else "false"),
        ("__CONSOLIDATE__", "true" if consolidate else "false"),
        ("__CONSOLIDATE_MODE__", mode),
        ("__CONSOLIDATE_DIR__", js_str(cons_dir)),
        ("__LAUNCH_MS__", str(int(launch_ms))),
        ("__SAVE__", "true" if save else "false"),
        ("__REPORT_DIR__", js_str(report_dir if report_dir is not None else out_dir)),
        ("__WORK_DIR__", js_str(work_dir or out_dir)),
        ("__LOG_PATH__", js_str(log_path or "")),
        ("__STATE_DIR__", js_str(state_dir if state_dir is not None else const.STATE_DIRNAME)),
        ("__APPVER__", app_ver if app_ver is not None else __import__("aemd").APP_VER),
        # 1.1.0：手工映射表整个塞进 JSX（键已规范化：小写、正斜杠）。空表 → `{}`，
        # 于是「没配映射」与 v1.9.8 行为完全相同（对拍靠 KNOWN_DIFFS 把这一行删掉）。
        ("__MAPPING__", _js_object(mapping or {})),
        # 1.1.6：只重试缺件 -> 名单（小写）。空列表 = 全量跑，与旧行为完全一致。
        ("__ONLY_MISSING__", js_list([str(x).lower() for x in (only_missing or []) if x])),
    ]
    jsx = JSX_TEMPLATE
    for k, v in repl:
        jsx = jsx.replace(k, v)
    left = residual_placeholders(jsx)
    if left:
        # D4：宁可当场炸，也不要生成一个"语法正确但语义错"的脚本
        raise RuntimeError("JSX 占位符没填完：" + "、".join(left))
    return jsx


def validate(text, node_check=False):
    """校验生成的 JSX，返回问题列表（空 = 通过）。

    三道闸：
      1. 占位符残留（D4 / F-10）—— 一代只栽在这一条上
      2. ES3 保留字（R10 / F-18）
      3. `node_check=True` 时真让 node 解析一遍（P4 的 CI 里跑；单测不必依赖 node）
    """
    problems = []
    left = residual_placeholders(text)
    if left:
        problems.append("占位符没填完：%s" % "、".join(left))
    for line, word, snippet in es3_violations(text):
        problems.append("ES3 保留字 %r 当标识符用了（第 %d 行）：%s" % (word, line, snippet))
    if node_check and not problems:
        problems.extend(_node_check(text))
    return problems


def _node_check(text):
    """让 node 真解析一遍。找不到 node 就当"没法查"（返回提示，不判失败）。"""
    import shutil
    import tempfile
    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        return []
    fd, p = tempfile.mkstemp(suffix=".js")   # 必须是 .js：node 不认 .jsx 扩展名（ESM get_format 直接报错）
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(text.encode("utf-8"))
        r = subprocess.run([node, "--check", p], capture_output=True, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode == 0:
            return []
        msg = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        return ["node --check 失败：%s" % (msg[0] if msg else "未知")]
    except Exception as e:
        return ["node --check 跑不起来：%s" % e]
    finally:
        try:
            os.unlink(p)
        except OSError:
            pass


def build(project, roots, dry, consolidate, cons_dir, save, out_dir, depth=6,
          cons_mode="copy", launch_ms=None, work_dir=None, log_path=None,
          report_dir=None, state_dir=None, path=None, mapping=None, only_missing=None):
    """render → validate → 落盘到 target_path() → 返回落盘路径。"""
    text = render(project, roots, dry, consolidate, cons_dir, save, out_dir, depth=depth,
                  cons_mode=cons_mode, launch_ms=launch_ms, work_dir=work_dir,
                  log_path=log_path, report_dir=report_dir, state_dir=state_dir,
                  mapping=mapping, only_missing=only_missing)
    problems = validate(text)
    if problems:
        raise RuntimeError("生成的 JSX 没通过自检：" + "；".join(problems))
    p = path or target_path(out_dir)
    paths.ensure_dir(os.path.dirname(p))
    tmp = p + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(text.encode("utf-8"))
    os.replace(tmp, p)
    return p


# ===== 以下 315 行是 v1.9.8 的 JSX_TEMPLATE，逐字节原样搬运（勿手改）=====
JSX_TEMPLATE = r"""/*
 * 拾链 生成的重连脚本（基于 _relink.jsx v2.0）
 * 由 GUI 写入配置，请勿手工修改；改选项请回到软件里。
 */
var CFG = {
    project: __PROJECT__,
    searchRoots: __ROOTS__,
    maxDepth: __DEPTH__,
    dryRun: __DRYRUN__,
    consolidate: __CONSOLIDATE__,
    consolidateMode: "__CONSOLIDATE_MODE__",
    consolidateDir: __CONSOLIDATE_DIR__,
    launchMs: __LAUNCH_MS__,
    save: __SAVE__,
    alertOnFinish: false,
    reportDir: __REPORT_DIR__,
    workDir: __WORK_DIR__,
    logPath: __LOG_PATH__,
    stateDir: __STATE_DIR__
};

/* 1.1.0 手工映射表：键是小写原文件名或原完整路径，值是目标文件的绝对路径 */
var MAPPING = __MAPPING__;

/* 1.1.6 只重试缺件：非空时只处理名单里的素材（名字来自 _relink_missing.txt 第一列） */
var ONLY = __ONLY_MISSING__;

var OUT = CFG.reportDir || (CFG.project.substring(0, CFG.project.lastIndexOf("/")));
var WORK = CFG.workDir || OUT;
/* v1.9.8：日志住软件自己的目录（%APPDATA%\AE素材医生\logs\），不再写进工程 */
var LOG = CFG.logPath || (OUT + "/_relink_log.txt");
var MISS = OUT + "/_relink_missing.txt";
var JSONF = OUT + "/_relink_report.json";

var MEDIA_EXT = ["mp4","mov","mkv","avi","m4v","webm","mxf","wmv","flv",
                 "mp3","wav","aif","aiff","m4a","aac","ogg","flac",
                 "jpg","jpeg","png","exr","tif","tiff","psd","webp","gif","dpx","hdr"];

var lines = [], missLines = [], relinked = [], missingItems = [], moved = [];
var stats = { total:0, ok:0, fixed:0, ambiguous:0, missing:0, broken:0, mapped:0 };

function L(s){
  /* D5：中途 flush() 会把文件关掉，之后再 L() 就写进一个已关闭的文件、被 catch 静默吞掉
     （真机验收里 DONE-OK 5/5 缺失就是这个原因）。所以写之前先确保句柄是开的。 */
  if (!LOG_OPEN) { try { LOG_OPEN = f.open("a"); } catch(e){ LOG_OPEN = false; } }
    lines.push(s);
    try { if (LOG_OPEN) { f.writeln(s); f.flush(); } } catch(e){}
}
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
/* decodeURI 遇到残缺的 %XX 会抛错，整个遍历就断了 —— 统一走这个安全版 */
function dec(s){
    if (s===null||s===undefined) return "";
    try { return decodeURI(String(s)); } catch(e){ return String(s); }
}

var idxExact = {}, idxShort = {}, scanned = 0;
var T0 = (new Date()).getTime();
function T(tag){ L("TIME      " + tag + "  +" + (((new Date()).getTime() - T0) / 1000).toFixed(1) + "s"); }

function loadPyIndex(){
    var f = new File(WORK + "/_media_index.json");
    if (!f.exists) return false;
    try {
        f.encoding = "UTF-8";
        f.open("r");
        var txt = f.read();
        f.close();
        var d = eval("(" + txt + ")");
        if (d && d.exact && d.short){
            idxExact = d.exact; idxShort = d.short; scanned = d.count || 0;
            return true;
        }
    } catch(e){ L("INDEX-ERR " + e.toString()); }
    return false;
}

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
        if (it instanceof Folder) {
            /* v1.9.8：工程的 .ae_doctor 里放的是报告和缺件清单，不是素材。
               搜索目录常含工程目录本身，这里显式跳过，别让结果件混进候选索引。 */
            if (it.name === CFG.stateDir) continue;
            scan(it.fsName, depth+1);
        }
        else if (it instanceof File) { if (isMedia(it.name)) { addIndex(it.fsName, it.name); scanned++; } }
    }
}

function exists(fr){ if(!fr) return false; try { return fr.exists; } catch(e){ return false; } }
function sizeOf(fr){ try { return fr.length; } catch(e){ return -1; } }

/* 1.1.1：同名多候选时，用「原来所在目录名」当消歧依据。
   同一文件名散落在多个素材盘时，目录名（如 02补帧镜头包 / Footages）往往还保留着，
   比「谁先扫到用谁」准得多 —— 两个独立的开源实现都是这么做的。 */
function parentNameOf(p){
    var s = String(p || "").replace(/\\/g, "/");
    var i = s.lastIndexOf("/");
    if (i <= 0) return "";
    var j = s.lastIndexOf("/", i - 1);
    return s.substring(j + 1, i).toLowerCase();
}
function pickSameParent(list, want){
    if (!want || !list) return null;
    for (var i = 0; i < list.length; i++){
        if (parentNameOf(list[i]) === want) return list[i];
    }
    return null;
}

/* 1.1.6：只重试缺件时判断「这条要不要处理」。
   名单来自上一次的缺件清单（第一列），所以三个名字都认：当前文件名、缺件原路径的
   文件名、素材项名 —— 任意一种对上就处理，避免因为改名而漏掉。 */
function baseNameOf(p){
    var s2 = String(p || "").replace(/\\/g, "/");
    return s2.substring(s2.lastIndexOf("/") + 1).toLowerCase();
}
function wantedByOnly(it){
    if (!ONLY || !ONLY.length) return true;
    var s2 = it.mainSource;
    var n1 = "", n2 = "";
    try { if (s2 && s2.file) n1 = String(s2.file.name).toLowerCase(); } catch(e){}
    try { if (s2 && s2.missingFootagePath) n2 = baseNameOf(s2.missingFootagePath); } catch(e){}
    var nm = String(it.name || "").toLowerCase();
    for (var i2 = 0; i2 < ONLY.length; i2++){
        if (ONLY[i2] === n1 || ONLY[i2] === n2 || ONLY[i2] === nm) return true;
    }
    return false;
}

function relink(item){
    var src = item.mainSource;
    if (!(src instanceof FileSource)) return;

    stats.total++;
    var cur = src.file;
    /* 1.1.1：file 为 null 时（素材彻底丢失）AE 仍记得它原来在哪。
       这个属性不读，缺件清单里就只剩 "(no file)"，手工映射表的「完整路径」键也立不起来。 */
    var missPath = "";
    try { if (src.missingFootagePath) missPath = String(src.missingFootagePath); } catch(e){}
    var curPath = cur ? cur.fsName : (missPath || "(no file)");

    if (exists(cur) && sizeOf(cur) > 0){
        stats.ok++;
        L("OK        " + item.name + "  |  " + curPath);
        return;
    }
    if (exists(cur) && sizeOf(cur) === 0){
        stats.broken++;
        L("ZERO      " + item.name + "  |  " + curPath + "  (文件 0 字节)");
    }

    var name = cur ? dec(cur.name) : "";
    var cand = null, how = "", alts = [];

    var origDir = parentNameOf(curPath);
    var exact = idxExact[name.toLowerCase()];
    if (exact && exact.length >= 1){
        if (exact.length > 1){
            /* 1.1.1：多候选取「原目录名相同」的那个；一个都没对上才退回首个，并在 how 里标明。 */
            var sameDir = pickSameParent(exact, origDir);
            cand = sameDir || exact[0];
            how = sameDir ? "全名匹配(同目录优先)" : "全名匹配(多候选取首个)";
            alts = exact.slice(0, 4);
        } else {
            cand = exact[0]; how = "全名匹配";
        }
    }

    /* ===== 1.1.0 手工映射表：全名没中时，先问用户自己指过的替身 =====
       查表顺序「原完整路径 → 原文件名」，越具体越优先。
       命中且目标文件在 → 直接用；目标文件不在 → 记 MISSING 并写明是这条映射指错了，
       绝不悄悄退回后面的自动匹配（那会让用户以为「我指的没生效」，排查最费时间）。 */
    if (!cand){
        var mapKey = curPath ? String(curPath).toLowerCase() : "";
        var mapHit = (mapKey && MAPPING[mapKey]) ? MAPPING[mapKey] : "";
        if (!mapHit && name) mapHit = MAPPING[String(name).toLowerCase()] || "";
        if (mapHit){
            var mapFile = new File(mapHit);
            if (exists(mapFile) && sizeOf(mapFile) > 0){
                cand = mapFile.fsName;
                how = "映射表";
                stats.mapped++;
            } else {
                stats.missing++;
                var mapReason = "映射表指向的文件不存在: " + mapHit;
                missingItems.push({ name: item.name, original: curPath, reason: mapReason });
                missLines.push(name + "\t" + curPath);
                L("MISSING   " + item.name + "  |  " + curPath + "  |  " + mapReason);
                return;
            }
        }
    }

    if (!cand && name){
        var sk = shortKey(name) + "|" + extOf(name);
        /* 变量名不能叫 short —— ES3/ExtendScript 里 short 是保留字，
           用它声明变量会让 AE 报「非法使用保留字」并拒绝执行整个脚本。
           （v1.9.2 起就带着这个 bug，因为现代 JS 允许 short 作变量名，一直没被发现。） */
        var shortHits = idxShort[sk];
        if (shortHits && shortHits.length >= 1){
            if (shortHits.length > 1){
                /* 1.1.1：基名撞车同样先按原目录名筛；筛中就不算「猜」，不计 ambiguous。 */
                var shortSame = pickSameParent(shortHits, origDir);
                cand = shortSame || shortHits[0];
                if (shortSame){
                    how = "基名匹配(" + shortHits.length + " 个候选,同目录优先)";
                } else {
                    stats.ambiguous++;
                    how = "基名匹配(" + shortHits.length + " 个候选)";
                }
                alts = shortHits.slice(0, 4);
            } else {
                cand = shortHits[0];
                how = "基名匹配(" + shortHits.length + " 个候选)";
            }
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
        var moveSrc = null;
        if (CFG.consolidate){
            var dir = new Folder(CFG.consolidateDir);
            if (!dir.exists) dir.create();
            var srcFile = new File(cand);
            var dest = new File(dir.fsName + "/" + srcFile.name);
            /* 同名但大小不同 → 改名落盘。
               否则 item.replace() 会指向 Footages 里那份旧文件，日志还写 RELINKED，属于「假装成功」。
               （v1.9.2 只判断 !dest.exists，撞名就静默接错素材。） */
            if (exists(dest) && sizeOf(dest) !== sizeOf(srcFile)){
                L("CONFLICT  " + srcFile.name + " 在 Footages 里已有同名但大小不同的文件");
                var stem2 = baseOf(srcFile.name), ext2 = extOf(srcFile.name), n = 2;
                while (exists(dest) && sizeOf(dest) !== sizeOf(srcFile) && n < 100){
                    dest = new File(dir.fsName + "/" + stem2 + "_" + n + (ext2 ? "." + ext2 : ""));
                    n++;
                }
                L("CONFLICT  改用 " + dest.name + "（两份文件都保留）");
            }
            if (!exists(dest)) srcFile.copy(dest.fsName);
            target = dest.fsName;
            if (String(CFG.consolidateMode) === "move" && exists(dest)
                && sizeOf(dest) === sizeOf(srcFile) && dest.fsName !== srcFile.fsName){
                moveSrc = srcFile;
            }
        }
        item.replace(new File(target));
        /* 1.1.1：replace() 不抛异常 ≠ 真的接上了。AE 仍把这条标成离线时如实留一句；
           不并进统计 —— 不同版本对 missingFootagePath 的清空时机不一致，
           先让它「说话」，真机跑两轮攒够证据再决定要不要计入 missing。 */
        var stillMissing = false;
        try { stillMissing = !!(item.mainSource && item.mainSource.missingFootagePath); } catch(e3){}
        if (stillMissing){
            L("RELINK-SUSPECT " + item.name + "  |  " + target + "  |  replace 没报错，但素材仍标记离线（可能没真接上）");
        }
        if (moveSrc){
            try {
                var gone = moveSrc.fsName;
                moveSrc.remove();
                moved.push(gone + "\t" + target);
                L("MOVED     " + gone + "  ->  " + target);
            } catch(e2){ L("MOVE-WARN " + moveSrc.fsName + " 删除失败，原文件已保留"); }
        }
        stats.fixed++;
        relinked.push({ item: item.name, from: curPath, to: target, how: how });
        L("RELINKED  " + item.name + "  |  " + curPath + "  =>  " + target + "  [" + how + "]");
        /* 1.1.5：分层 PSD/PSB/AI 是最容易被「接上但塌层」的一类 —— replace() 之后
           图层结构可能就没了，而用户只看到日志写着成功。AE 侧没有可靠的
           「这张到底是不是分层导入」判据（得看 .aepx 里的导入方式），所以对这类
           扩展名一律提示核对：宁可多一句，也不让人以为万事大吉。 */
        var lext = extOf(target);
        if (lext === "psd" || lext === "psb" || lext === "ai"){
            L("LAYER-WARN " + item.name + "  |  " + target + "  |  分层素材（." + lext + "）已重连，请核对图层结构是否完整");
        }
        for (var al=0; al<alts.length; al++) L("  ALT       " + alts[al]);
    } catch (e){
        stats.missing++;
        missingItems.push({ name: item.name, original: curPath, reason: "replace 失败: " + e.toString() });
        missLines.push(name + "\t" + curPath);
        L("FAILED    " + item.name + "  |  " + e.toString());
    }
}

/* 日志句柄。目录不可写时 open() 只返回 false、不抛错，writeln 的异常又被 L() 吞掉，
   界面就会白等 90 秒。这里把状态记下来，结尾写进 SUMMARY 让排查有据可依。 */
var f = new File(LOG); f.encoding = "UTF-8";
var LOG_OPEN = false;
try { LOG_OPEN = f.open("w"); } catch(e){ LOG_OPEN = false; }
var LOG_OK = LOG_OPEN;      /* D5 副作用修正：报告里写「当初有没有打开成功」，不看此刻句柄 */
function flush(){ try { f.close(); } catch(e){} LOG_OPEN = false; }

try {
    var proj = app.project;
    var openedHere = false;
    var want = new File(CFG.project);
    if (!proj || !proj.file || dec(proj.file.fsName) !== dec(want.fsName)){
        L("OPEN      " + CFG.project);
        app.open(want);
        proj = app.project;
        openedHere = true;
    } else {
        L("USE-OPEN  " + dec(proj.file.fsName));
    }

    L("SCRIPT    拾链 v__APPVER__   AE " + app.version + (CFG.dryRun ? "   [只体检]" : ""));
    L("PROJECT   " + dec(proj.file.fsName));
    L("ITEMS     " + proj.numItems);

    if (loadPyIndex()){
        L("INDEX     " + scanned + " 个素材文件   （Python 预扫描，AE 内不再扫盘）");
    } else {
        for (var r=0;r<CFG.searchRoots.length;r++){
            var before = scanned;
            scan(CFG.searchRoots[r], 0);
            L("SCAN      " + CFG.searchRoots[r] + "  -> +" + (scanned-before) + " 文件");
        }
        L("INDEX     " + scanned + " 个素材文件");
    }
    if (CFG.launchMs){
        if (CFG.launchMs) { L("TIME      AE 启动→脚本开始  +" + (((new Date()).getTime() - CFG.launchMs) / 1000).toFixed(1) + "s"); }
    }
    T("准备（载入索引）");

    for (var i=1;i<=proj.numItems;i++){
        var ci = proj.item(i);
        if (!(ci instanceof FootageItem)) continue;
        /* 1.1.6：只重试缺件时跳过不在名单里的（日志留一行，便于事后核对跳过了什么） */
        if (!wantedByOnly(ci)) { L("SKIP-ONLY " + ci.name); continue; }
        relink(ci);
    }
    T("逐条重连");

    L("");
    L("SUMMARY   total=" + stats.total + " ok=" + stats.ok + " fixed=" + stats.fixed +
      " missing=" + stats.missing + " ambiguous=" + stats.ambiguous + " zeroByte=" + stats.broken + " mapped=" + stats.mapped);

    if (!CFG.dryRun && CFG.save && (stats.fixed > 0 || openedHere)){
        proj.save();
        L("SAVED     " + dec(proj.file.fsName));
    } else if (CFG.dryRun){
        L("SKIP-SAVE (只体检)");
    }
    flush();

    var mf = new File(MISS); mf.encoding = "UTF-8"; mf.open("w");
    mf.writeln("# 缺失素材清单  " + (new Date()).toString());
    mf.writeln("# 把这些文件放进素材目录后，重跑一次即可接上");
    for (var m=0;m<missLines.length;m++) mf.writeln(missLines[m]);
    mf.close();

    var js = '{\n  "script": "AE Media Doctor v__APPVER__",\n  "project": "' + esc(dec(proj.file.fsName)) + '",\n';
    js += '  "logWritable": ' + (LOG_OK ? "true" : "false") + ',\n';
    js += '  "dryRun": ' + (CFG.dryRun ? "true" : "false") + ',\n';
    js += '  "stats": { "total": ' + stats.total + ', "ok": ' + stats.ok + ', "fixed": ' + stats.fixed +
          ', "missing": ' + stats.missing + ', "ambiguous": ' + stats.ambiguous + ', "zeroByte": ' + stats.broken + ', "mapped": ' + stats.mapped + ' },\n';
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

    /* 「移动源文件」是不可撤销动作，留一份账，用户可以照着搬回去 */
    if (moved.length){
        var mvf = new File(OUT + "/_relink_moved.txt");
        mvf.encoding = "UTF-8"; mvf.open("w");
        mvf.writeln("# 收编（移动）留痕   " + dec(proj.file.fsName));
        mvf.writeln("# 原路径 <TAB> 新路径");
        mvf.writeln("# 想撤销：把第二列的文件复制回第一列的位置，再把工程重跑一次");
        for (var q=0;q<moved.length;q++) mvf.writeln(moved[q]);
        mvf.close();
    }
    L("DONE-OK");
    flush();
} catch (e){
    L("FATAL " + e.toString() + " line " + e.line);
    try { flush(); } catch (e2) {}
}
"""
