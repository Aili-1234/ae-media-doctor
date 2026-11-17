#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2.0 自检（P0 版）—— 不启动界面、不碰 AE、不写用户的 settings.json。

    python tools/selftest.py          # 在 media-doctor-v2 目录下跑
    退出码 0 = 全绿

和 v1.9.8 的 tools/selftest.py 的关系：
  一代的 11 项检查里，凡是「用真实源码验证行为」的那几条在这里原样继承；
  凡是要等实现搬完（scanner 的 shortKey、JSX 生成、报告解析）的，先标 SKIP 并写清
  在哪个阶段补齐 —— SKIP 不算通过，它会出现在最后的统计里，不会被悄悄忘掉。
"""

import io
import json
import os
import py_compile
import re
import subprocess
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# GBK 控制台防线（第 0 件事，早于任何 print）：统一走 aemd.console.harden()。
# 一代表里 `✓`、v2 的 `↔` 都在 cp936 下 UnicodeEncodeError、退出码变 2，
# 看起来像自检失败。本文件自己的输出只许「显示得难看」，不许「崩」。
from aemd import console        # noqa: E402

import aemd                     # noqa: E402  （模块级：多处断言要读 APP_VER，别在函数里各自 import）

console.harden()

CHILD_ENV_MARK = "AEMD_V2_SELFTEST_CHILD"


def _decode(b):
    """把子进程吐的字节解成文本：utf-8 严格 → cp936 严格 → utf-8 replace。

    顺序有讲究：main.py 在管道下会切到 UTF-8（见 `_harden_console`），所以正常路径
    是第一步就成；只有别人直接跑本项检查、绕过了那个切换时才会退到 cp936。
    任何情况下都**不许**把 U+FFFD 再打到 GBK 控制台上（那正是上一轮崩的原因）。
    """
    if b is None:
        return ""
    if isinstance(b, str):
        return b
    for enc in ("utf-8", "cp936"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")

OK = []
SKIP = []
BAD = []


def check(name):
    def deco(fn):
        try:
            fn()
            OK.append(name)
            print("  ok    %s" % name)
        except SkipTest as e:
            SKIP.append((name, str(e)))
            print("  SKIP  %s（%s）" % (name, e))
        except Exception as e:
            BAD.append((name, "%s: %s" % (type(e).__name__, e)))
            print("  FAIL  %s" % name)
            traceback.print_exc()
        return fn
    return deco


class SkipTest(Exception):
    pass


def need(cond, msg):
    if not cond:
        raise AssertionError(msg)


# ---------------------------------------------------------------- 1. 语法与入口

@check("全部源码可编译，且没有 SyntaxWarning")
def _():
    bad = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "build", "dist")]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                py_compile.compile(p, doraise=True, cfile=os.path.join(tempfile.gettempdir(), "aemd_v2_%s.pyc" % abs(hash(p))))
            except Exception as e:
                bad.append("%s: %s" % (os.path.relpath(p, ROOT), e))
    need(not bad, "编译失败：%s" % "; ".join(bad))


@check("`python main.py --ver` 打印版本号（从 aemd.APP_VER 读，不写死）")
def _():
    # 只收字节，自己解码：让子进程的编码与父进程的 locale 解耦（GBK 控制台回归的根因）。
    out = subprocess.run([sys.executable, os.path.join(ROOT, "main.py"), "--ver"],
                         capture_output=True, cwd=ROOT, timeout=60)
    stdout, stderr = _decode(out.stdout), _decode(out.stderr)
    need(out.returncode == 0, "--ver 退出码 %s，stderr=%s" % (out.returncode, stderr))
    need(aemd.APP_VER in stdout, "--ver 输出里没有版本号 %r：%r" % (aemd.APP_VER, stdout))
    print("        → %s" % stdout.strip())


@check("GBK 控制台下自检自身必须 0 FAIL（子进程 PYTHONIOENCODING=gbk 复跑）")
def _():
    # 挡一类回归：输出里出现 GBK 编不了的字符 → 自检自己崩 → 看起来像代码坏了。
    # 这条用例就是拿「用户双击的那台普通控制台」的编码再跑一遍自己。
    if os.environ.get(CHILD_ENV_MARK):
        raise SkipTest("这是被本项拉起来的子进程，跳过自身以避递归")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "gbk"
    env[CHILD_ENV_MARK] = "1"
    out = subprocess.run([sys.executable, os.path.join(HERE, "selftest.py")],
                         capture_output=True, cwd=ROOT, env=env, timeout=300)
    text = _decode(out.stdout) + _decode(out.stderr)
    need(out.returncode == 0, "子进程（GBK）退出码 %s：%s" % (out.returncode, text.strip()[-600:]))
    need("\ufffd" not in text, "子进程输出里出现了 U+FFFD（编码已经对不上了）")
    need("ALL PASS" in text, "子进程没有打印 ALL PASS：%s" % text.strip()[-400:])


@check("每个可执行脚本在 GBK 控制台下都必须 exit 0（元测试，防同族回归）")
def _():
    # 这条是有来历的：同一个坑 2026-11-15 一天里踩了三次（v1 的 ✓、main.py --ver 的
    # position 13、test_p1_parity 的 ↔）。人肉记住「哪些符号 GBK 编不出来」不可靠，
    # 所以改成机器盯：把 tests/ 与 tools/*_parity.py 逐个按用户那台普通控制台的编码跑一遍。
    if os.environ.get(CHILD_ENV_MARK):
        raise SkipTest("这是被本项拉起来的子进程，跳过自身以避递归")
    targets = []
    tests_dir = os.path.join(ROOT, "tests")
    for fn in sorted(os.listdir(tests_dir)):
        if fn.endswith(".py") and not fn.startswith("__"):
            targets.append(os.path.join(tests_dir, fn))
    for fn in sorted(os.listdir(HERE)):
        if fn.endswith("_parity.py"):
            targets.append(os.path.join(HERE, fn))
    need(targets, "一个目标脚本都没找到（tests/ 与 tools/*_parity.py 都空？）")
    bad = []
    for path in targets:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "gbk"
        out = subprocess.run([sys.executable, path], capture_output=True,
                             cwd=ROOT, env=env, timeout=600)
        text = _decode(out.stdout) + _decode(out.stderr)
        name = os.path.relpath(path, ROOT)
        if out.returncode != 0:
            bad.append("%s -> exit %s：%s" % (name, out.returncode, text.strip()[-400:]))
        elif "\ufffd" in text:
            bad.append("%s -> 输出里有 U+FFFD（编码对不上）" % name)
    need(not bad, "GBK 控制台下跑不通：\n" + "\n".join(bad))
    print("        → %d 个脚本在 GBK 下全部 exit 0：%s"
          % (len(targets), ", ".join(os.path.relpath(t, ROOT) for t in targets)))


@check("打桩测试必须零真副作用（跑完 test_p2_* 不许动 Startup / AE / 用户目录）")
def _():
    # 这条的来历：2026-11-15 15:44，test_p2_runner 的 Ctx 漏 patch `const.ADOBE_PREFS`，
    # 于是 prepare_startup() 往**真实**用户 Startup 目录写了 _aemd_*.jsx，还把真 AE 起/杀
    # 了一遍 —— 测试照样报 ALL PASS。和 paths.py 的 import 期快照是同一族：假绿。
    # 判据不靠人看：跑之前拍三张快照，跑完逐项比对。
    if os.environ.get(CHILD_ENV_MARK):
        raise SkipTest("这是被本项拉起来的子进程，跳过自身以避递归")
    import aemd.const as const
    import aemd.paths as paths
    import aemd.winproc as winproc

    def snap():
        startup = {}
        base = const.ADOBE_PREFS
        try:
            for name in sorted(os.listdir(base)):
                d = os.path.join(base, name, *const.STARTUP_SUBDIR)
                if os.path.isdir(d):
                    startup[d] = sorted(os.listdir(d))
        except OSError:
            pass
        return {
            "afterfx": sorted(winproc.snapshot_pids("AfterFX.exe")),
            "startup": startup,
            "work": sorted(os.listdir(const.WORK_DIR)) if os.path.isdir(const.WORK_DIR) else [],
            "logs": sorted(os.listdir(const.LOG_DIR)) if os.path.isdir(const.LOG_DIR) else [],
        }

    before = snap()
    targets = [os.path.join(ROOT, "tests", "test_p2_aehost.py"),
               os.path.join(ROOT, "tests", "test_p2_runner.py")]
    for path in targets:
        need(os.path.isfile(path), "找不到 %s" % path)
        env = dict(os.environ)
        env[CHILD_ENV_MARK] = "1"
        out = subprocess.run([sys.executable, path], capture_output=True,
                             cwd=ROOT, env=env, timeout=600)
        text = _decode(out.stdout) + _decode(out.stderr)
        need(out.returncode == 0,
             "%s 没跑通（exit %s）：%s" % (os.path.basename(path), out.returncode, text[-400:]))
    after = snap()
    bad = []
    for key in ("afterfx", "startup", "work", "logs"):
        if before[key] != after[key]:
            bad.append("%s 变了：跑前 %r -> 跑后 %r" % (key, before[key], after[key]))
    need(not bad, "打桩测试产生了真副作用（打桩没打全）：\n" + "\n".join(bad))
    print("        → AfterFX %d 个 · Startup 目录 %d 个 · work/logs 条目数与跑前一致"
          % (len(after["afterfx"]), len(after["startup"])))


@check("首启测试必须零真副作用：跑完 test_cfg_migrate 后真实 settings.json 逐字节不变")
def _():
    # 这条的来历：2026-11-15 16:43，首启把用户 462 B 的 settings.json 洗成 273 B
    # （工程路径与三个搜索目录没了）。根因不在测试而在产品：启动就无条件写配置 +
    # 读配置不容 BOM。所以这里加一道闸门，盯的是「首启这条路径」而不是某个函数：
    # 跑一遍配置回归（它内部全在临时目录里折腾），然后核对**真实**配置文件的
    # 字节与 mtime 都没被碰到。
    if os.environ.get(CHILD_ENV_MARK):
        raise SkipTest("这是被本项拉起来的子进程，跳过自身以避递归")
    import aemd.cfg as cfg

    path = cfg.cfg_path()
    before = None
    if os.path.isfile(path):
        with open(path, "rb") as f:
            before = f.read()
        before_mtime = os.path.getmtime(path)
    else:
        before_mtime = None

    targets = [os.path.join(ROOT, "tests", "test_cfg_migrate.py"),
               os.path.join(ROOT, "tests", "test_p3_ui.py")]
    env = dict(os.environ)
    env[CHILD_ENV_MARK] = "1"
    for target in targets:
        need(os.path.isfile(target), "找不到 %s" % target)
        out = subprocess.run([sys.executable, target], capture_output=True,
                             cwd=ROOT, env=env, timeout=300)
        text = _decode(out.stdout) + _decode(out.stderr)
        need(out.returncode == 0, "%s 没跑通（exit %s）：%s"
             % (os.path.basename(target), out.returncode, text[-400:]))

    if before is None:
        need(not os.path.isfile(path), "跑测试反而造出了 settings.json")
    else:
        with open(path, "rb") as f:
            after = f.read()
        need(after == before, "跑测试把真实 settings.json 改了（%d B -> %d B）"
             % (len(before), len(after)))
        need(os.path.getmtime(path) == before_mtime, "跑测试把真实 settings.json 的 mtime 碰了")
    print("        → 真实 settings.json %s 字节不变"
          % (("%d B" % len(before)) if before is not None else "（不存在）"))


@check("版本号只有一个来源（__init__.py），且与方案一致")
def _():
    import aemd
    need(aemd.APP_VER.count(".") == 2, "aemd.APP_VER 应是三段版本号，实际 %r" % aemd.APP_VER)
    src = io.open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    need(aemd.APP_VER not in src, "main.py 里硬编码了版本号（应该读 aemd.APP_VER）")


# ---------------------------------------------------------------- 2. 配置（R11）

@check("写配置先读后写：未知键活下来")
def _():
    from aemd import cfg
    d = tempfile.mkdtemp(prefix="aemd_cfg_")
    p = os.path.join(d, "settings.json")
    io.open(p, "w", encoding="utf-8").write('{"some_unknown_key": "keep-me", "dry_run": true}')
    old = cfg.cfg_path
    cfg.cfg_path = lambda: p
    try:
        cfg.merge_cfg({"dry_run": False})
        obj = json.loads(io.open(p, encoding="utf-8").read())
    finally:
        cfg.cfg_path = old
    need(obj.get("some_unknown_key") == "keep-me", "未知键丢了：%r" % obj)
    need(obj.get("dry_run") is False, "目标键没写进去：%r" % obj)


@check("坏文件不炸：坏 JSON / 非对象 / 空文件都退化成 {}")
def _():
    from aemd import cfg
    d = tempfile.mkdtemp(prefix="aemd_cfg_bad_")
    old = cfg.cfg_path
    try:
        for content in ("{ this is not json", "[]", "", "null", '{"a": NaN}'):
            p = os.path.join(d, "s%d.json" % abs(hash(content)))
            io.open(p, "w", encoding="utf-8").write(content)
            cfg.cfg_path = lambda p=p: p
            got = cfg.load_cfg()
            need(isinstance(got, dict), "%r 读出来不是 dict：%r" % (content, got))
    finally:
        cfg.cfg_path = old


@check("cfg 的公开写入口只有 merge_cfg / save")
def _():
    from aemd import cfg
    public = [n for n in dir(cfg) if not n.startswith("_")]
    need("save_cfg" not in public, "存在公开的 save_cfg（整份覆盖入口）")
    need("merge_cfg" in public and "save" in public, "缺 merge_cfg / save")


# ---------------------------------------------------------------- 3. 路径（R9）

@check("路径四件事：日志进 logs、临时件进 work、结果进 .ae_doctor、JSX 落 ProgramData")
def _():
    from aemd import const, paths
    import ctypes
    need(paths.log_dir() == const.LOG_DIR, "log_dir 与 const 不一致")
    need(paths.work_dir() == const.WORK_DIR, "work_dir 与 const 不一致")
    sd = paths.state_dir_of(os.path.join(tempfile.gettempdir(), "aemd_state_test"))
    need(os.path.isdir(sd), "state_dir_of 没建出目录：%s" % sd)
    need(os.path.basename(sd) == const.STATE_DIRNAME, "目录名不是 %r：%s" % (const.STATE_DIRNAME, sd))
    # 隐藏位：必须是 HIDDEN|DIRECTORY，不能把目录位写没
    attr = ctypes.windll.kernel32.GetFileAttributesW(sd)
    need(attr & 0x02, "state_dir 没有 HIDDEN 位（attr=0x%X）" % attr)
    need(os.path.isdir(sd), "state_dir 写完属性后不再是目录（0x02 把 DIRECTORY 位抹了）")
    need(attr & 0x10, "state_dir 丢了 DIRECTORY 位（attr=0x%X）" % attr)
    jp = paths.jsx_target_path("D:\\任意\\工程")
    need(paths.is_ascii_path(jp), "JSX 目标路径含非 ASCII：%s" % jp)


@check("日志文件名：relink-<工程名>-<时间戳>.log，非法字符换成下划线")
def _():
    from aemd import paths
    p = paths.log_path_for("D:\\ae\\x", "again (1)")
    name = os.path.basename(p)
    need(name.startswith("relink-again (1)-"), "文件名不对：%s" % name)
    need(re.search(r"-\d{8}-\d{6}\.log$", name), "时间戳格式不对：%s" % name)
    p2 = paths.log_path_for("D:\\ae\\x", 'a:b*c?"d<e>f|g\\h/i')
    name2 = os.path.basename(p2)
    need(not re.search(r'[\\/:*?"<>|]', name2[7:]), "非法字符没清干净：%s" % name2)
    need(name2.startswith("relink-a_b_c_"), "非法字符替换结果不对：%s" % name2)


@check("日志只留最近 LOG_KEEP 个（滚动逻辑的纯函数部分）")
def _():
    from aemd import const, paths
    d = tempfile.mkdtemp(prefix="aemd_prune_")
    olds = []
    for i in range(const.LOG_KEEP + 7):
        p = os.path.join(d, "relink-p-%03d.log" % i)
        io.open(p, "w").write("x")
        os.utime(p, (1000 + i, 1000 + i))     # 人为拉开 mtime，i 越大越新
        olds.append(p)
    n = paths.prune_dir(d, "relink-", ".log", const.LOG_KEEP)
    left = sorted(os.listdir(d))
    need(len(left) == const.LOG_KEEP, "删除后剩 %d 个，期望 %d" % (len(left), const.LOG_KEEP))
    need(n == 7, "prune_dir 报告删了 %d 个，期望 7" % n)
    need(os.path.basename(olds[-1]) in left, "把最新的那个删了")
    need(os.path.basename(olds[0]) not in left, "最老的那个没被删")


# ---------------------------------------------------------------- 4. 进程（R4）

@check("进程探测：ae_procs 不炸、返回形状正确、is_alive 对死 pid 为 False")
def _():
    from aemd import winproc
    procs = winproc.ae_procs()
    need(isinstance(procs, list), "ae_procs 返回的不是 list：%r" % type(procs))
    for it in procs:
        need(isinstance(it, tuple) and len(it) == 2, "ae_procs 元素形状不对：%r" % (it,))
        need(isinstance(it[0], int) and isinstance(it[1], bool), "ae_procs 元素类型不对：%r" % (it,))
    need(winproc.is_alive(0) is False, "is_alive(0) 应该是 False")
    need(winproc.is_alive(-1) is False, "is_alive(-1) 应该是 False")
    need(winproc.is_alive(999999999) is False, "is_alive(不存在的 pid) 应该是 False")


@check("进程探测：自己一定活着（is_alive 的 err=5 分支不能被当成死）")
def _():
    from aemd import winproc
    me = os.getpid()
    need(winproc.is_alive(me) is True, "is_alive(自己=%d) 返回 False —— err=5 分支错了" % me)


@check("AE 开关白名单：含 -r 且带实测证据，且没有任何 AE 未验证开关")
def _():
    from aemd import aehost, const
    need("-r" in const.VERIFIED_SWITCHES, "-r 不在 VERIFIED_SWITCHES 里")
    for sw in const.VERIFIED_SWITCHES:
        need(sw in const.VERIFIED_SWITCH_EVIDENCE, "%r 没有实测证据" % sw)
    need(aehost.LaunchMode.B_RAISE.verified_switches() == ("-r",),
         "B_RAISE 的开关应该恰好是 (-r,)：%r" % (aehost.LaunchMode.B_RAISE.verified_switches(),))
    need(aehost.LaunchMode.A2_STARTUP.verified_switches() == (),
         "A2_STARTUP 不该带任何命令行开关")
    need(aehost.LaunchMode.A2_STARTUP.keeps_ae_alive() is True, "A2 应该保活 AE（脚本跑完不能自退）")
    need(aehost.LaunchMode.B_RAISE.keeps_ae_alive() is False, "-r 会让 AE 跑完自退")


@check("IndexState 三态：探测不到不等于索引为空")
def _():
    from aemd import everything
    need(everything.IndexState.OK.is_ok() is True, "OK.is_ok() 应为 True")
    need(everything.IndexState.EMPTY.is_ok() is False, "EMPTY.is_ok() 应为 False")
    need(everything.IndexState.UNKNOWN.is_ok() is False, "UNKNOWN.is_ok() 应为 False")
    need(everything.IndexState.UNKNOWN != everything.IndexState.EMPTY,
         "UNKNOWN 与 EMPTY 必须是两个值（一代就是把它们当成同一个 0）")


# ---------------------------------------------------------------- 5. 更新（P3 用）

@check("更新检查的纯函数：stamp 三元组 / 变了才返回")
def _():
    from aemd import updater
    d = tempfile.mkdtemp(prefix="aemd_upd_")
    p = os.path.join(d, "x.exe")
    io.open(p, "w").write("v1")
    st = updater.stamp(p)
    need(isinstance(st, tuple) and len(st) == 3, "stamp 不是三元组：%r" % (st,))
    need(st[0] == p and st[1] == 2, "stamp 内容不对：%r" % (st,))
    need(updater.stamp_changed(st, p) is None, "文件没变却报了变化")
    io.open(p, "a").write("v2")
    os.utime(p, (st[2] + 5, st[2] + 5))
    changed = updater.stamp_changed(st, p)
    need(changed is not None and changed[1] == 4, "文件变了没报出来：%r" % (changed,))
    need(updater.stamp(os.path.join(d, "nope.exe"))[1] == -1, "不存在的文件应该返回大小 -1")
    # 两个不同的文件不能被当成同一个（踩过：不带 path 时它去 stamp 入口脚本，永远"变了"）
    q = os.path.join(d, "y.exe")
    io.open(q, "w").write("v1")
    need(updater.stamp(q) != updater.stamp(p), "不同文件算出了同一个 stamp")


# ---------------------------------------------------------------- 6. 参数分流

@check("拖文件传参：.aep 进工程、目录进目录、未知扩展名丢弃")
def _():
    import main as m
    d = tempfile.mkdtemp(prefix="aemd_args_")
    aep = os.path.join(d, "a.aep")
    io.open(aep, "w").write("x")
    txt = os.path.join(d, "b.txt")
    io.open(txt, "w").write("x")
    projects, dirs = m.split_args([aep, d, txt, "", os.path.join(d, "missing.aep")])
    need(projects == [aep], "工程列表不对：%r" % (projects,))
    need(dirs == [d], "目录列表不对：%r" % (dirs,))


# ---------------------------------------------------------------- 7. §8.1 新增防线（D1–D8）

@check("D1 撞名判据：same_content 比内容不比大小（读不了返回 None，不是 True）")
def _():
    from aemd import const, scanner
    need(const.SAME_CONTENT_SAMPLE == 1024 * 1024, "内容指纹采样不是 1 MB")
    need(const.DUP_SUFFIX.startswith("_"), "改名后缀应以 _ 开头：%r" % const.DUP_SUFFIX)
    d = tempfile.mkdtemp(prefix="aemd_d1_")
    a, b, c = (os.path.join(d, n) for n in ("a.mp4", "b.mp4", "c.mp4"))
    io.open(a, "wb").write(b"HEAD" + b"x" * 100)
    io.open(b, "wb").write(b"HEAD" + b"x" * 100)      # 同尺寸同内容
    io.open(c, "wb").write(b"HEAD" + b"y" * 100)      # 同尺寸**不同**内容
    need(os.path.getsize(a) == os.path.getsize(c), "夹具坏了：大小应相同")
    need(scanner.same_content(a, b) is True, "同尺寸同内容应判为同一份")
    need(scanner.same_content(a, c) is False, "同尺寸不同内容被判成同一份（F-12 复现了）")
    need(scanner.same_content(a, os.path.join(d, "nope.mp4")) is None,
         "读不了应返回 None（判不了 ≠ 相同）")


@check("D2 报告可写标记：logWritable 键名已定义")
def _():
    from aemd import const
    need(const.REPORT_KEY_LOG_WRITABLE == "logWritable",
         "键名不是 logWritable：%r" % const.REPORT_KEY_LOG_WRITABLE)


@check("D3 分段结算：40/60/90 秒三个常量有序，结算标记在位")
def _():
    from aemd import const
    vals = (const.DIAG_AT_S, const.NO_PROC_WAIT_S, const.START_WAIT_S)
    need(vals == (40, 60, 90), "分段常量应为 (40,60,90)：%r" % (vals,))
    need(const.PARTIAL_SETTLE_TAG == "PARTIAL", "结算标记应为 PARTIAL")


@check("D4 占位符残留：正则能认出没填的 __XXX__（node --check 认不出这个）")
def _():
    from aemd import jsx
    got = jsx.residual_placeholders('var a = ""; // __REPORT_DIR__ 没填\nvar b = __WORK_DIR__;')
    need(got == ["__REPORT_DIR__", "__WORK_DIR__"], "残留识别不对：%r" % (got,))
    need(jsx.residual_placeholders('var a = "D:/x/.ae_doctor"; var b = 1;') == [],
         "正常脚本被误报有残留")
    need(jsx.residual_placeholders("") == [], "空串应返回空列表")
    need(jsx.residual_placeholders('var dir = "";') == [],
         "空字符串不该被当成占位符 —— 所以判据是残留扫描而不是 node --check")


@check("D5 隐藏属性：HIDDEN 加上去了，DIRECTORY 位还在（不能整体写成 0x02）")
def _():
    from aemd import paths
    import ctypes
    sd = paths.state_dir_of(os.path.join(tempfile.gettempdir(), "aemd_state_d5"))
    attr = ctypes.windll.kernel32.GetFileAttributesW(sd)
    need(attr & 0x02, "没有 HIDDEN：0x%X" % attr)
    need(attr & 0x10, "没有 DIRECTORY：0x%X（说明是整体覆盖 0x02）" % attr)
    need(os.path.isdir(sd), "目录已经被写坏（isdir False）")


@check("D6 单实例：关旧 AE 后的等待窗口常量在位且有上限")
def _():
    from aemd import const
    need(const.AE_RESTART_GRACE_S == 2.0, "宽限起点应为 2 秒")
    need(const.AE_RESTART_GRACE_MAX_S == 6.0, "宽限上限应为 6 秒")
    need(const.AE_RESTART_GRACE_MAX_S > const.AE_RESTART_GRACE_S, "上限必须大于起点")


@check("D7 事件时间：解析器在位，容差 900 秒")
def _():
    import datetime
    from aemd import const, diag
    need(const.WEVT_FUTURE_TOLERANCE_S == 900, "未来容差应为 900 秒")
    need(callable(diag.parse_event_time), "diag.parse_event_time 不存在（D7 没预留）")
    # P3 起已实现：带 Z 的串默认按**本地**时间解（E-11：wevtutil 的 Z 不是 UTC）
    now = datetime.datetime(2026, 11, 15, 15, 0, 0)
    need(diag.parse_event_time("2026-11-15T12:19:52.000Z", now=now)
         == datetime.datetime(2026, 11, 15, 12, 19, 52), "D7 没有按本地时间解")


@check("D8 / U-02 写入白名单：Adobe 安装目录与插件目录永远不许写")
def _():
    from aemd import paths
    for bad in (r"C:\Program Files\Adobe\Adobe After Effects 2025\Support Files\Plug-ins",
                r"C:\Program Files (x86)\Adobe\x.dll",
                "c:/program files/adobe/x",
                r"C:\Program Files\BorisFX\Sapphire 2024.5\lib64\x.dll"):
        need(paths.is_forbidden_write(bad), "应判定为禁写却放过了：%s" % bad)
    for good in (r"D:\ae\别人工程\again\.ae_doctor\_relink_report.json",
                 r"C:\ProgramData\AEMediaDoctor\relink.jsx",
                 os.path.join(os.environ.get("APPDATA") or "C:\\", "AE素材医生", "logs", "x.log")):
        need(not paths.is_forbidden_write(good), "误判为禁写：%s" % good)


# ---------------------------------------------------------------- 8. 待搬实现（P1/P2）

@check("F-01 工作副本三级退避：同目录 → 同目录带时间戳 → AppData work\\")
def _():
    from aemd import paths
    proj = r"D:\ae\别人工程\again\again.aep"
    got = paths.plan_work_copy(proj)
    need(len(got) == 3, "应该给三级候选，实际 %d 个" % len(got))
    need(got[0] == r"D:\ae\别人工程\again\again_fixed.aep", "一级不对：%r" % got[0])
    need(got[1].startswith(r"D:\ae\别人工程\again\again_fixed_") and got[1].endswith(".aep"),
         "二级应是同目录带时间戳：%r" % got[1])
    need(os.path.dirname(got[2]) == paths.work_dir(), "三级应退到 work\\：%r" % got[2])
    need(len(set(got)) == 3, "三级候选不能重复：%r" % (got,))
    # 换扩展名也不能丢（.aepx / 大写）
    g2 = paths.plan_work_copy(r"D:\x\B.AEPX")
    need(g2[0].endswith("_fixed.AEPX"), "扩展名要原样保留：%r" % g2[0])


@check("P1 四模块可用：短名 / 报告一句话 / JSX 生成落盘 / 索引状态三态")
def _():
    from aemd import everything, jsx, reports, scanner
    # 1) 短名规则
    need(scanner.short_key("1 (1).119.880fps.QE.mp4") == "1 (1)", "短名规则不对")
    need(scanner.short_key("Again & Again.mp3") == "again & again", "短名规则不对（带空格&）")
    # 2) 一句话统计
    need(reports.summary_line({"fixed": 21, "missing": 11, "ambiguous": 1})
         == "接上 21 条，还缺 11 条，另有 1 条是按同名文件猜的，可能要核对", "统计文案不对")
    need(reports.summary_line({"fixed": 3, "missing": 0}) == "接上 3 条，还缺 0 条",
         "无猜测时不该追加尾巴")
    # 3) JSX 生成：真模板 → 占位符零残留 → 真落盘
    d = tempfile.mkdtemp(prefix="aemd_p1jsx_")
    text = jsx.render(r"D:\ae\a.aep", [r"D:\ae"], False, False, "", True,
                      os.path.join(d, ".ae_doctor"), launch_ms=1750000000000)
    need(jsx.residual_placeholders(text) == [], "生成的 JSX 还有占位符残留")
    need(jsx.es3_violations(text) == [], "模板里出现了 ES3 保留字")
    p = jsx.build(r"D:\ae\a.aep", [r"D:\ae"], False, False, "", True,
                  os.path.join(d, ".ae_doctor"), launch_ms=1750000000000,
                  path=os.path.join(d, "relink.jsx"))
    need(os.path.isfile(p) and os.path.getsize(p) > 10000, "JSX 没落盘或太小：%r" % p)
    # 4) 索引三态：桩掉 subprocess，绝不真跑 es.exe
    class R:
        def __init__(self, code, out):
            self.returncode, self.stdout = code, out
    real = everything.subprocess.run
    try:
        for code, out, want in ((0, b"17\n", everything.IndexState.OK),
                                (0, b"0\n", everything.IndexState.EMPTY),
                                (8, b"Error 8: IPC not found\n", everything.IndexState.UNKNOWN),
                                (0, b"", everything.IndexState.UNKNOWN)):
            everything.subprocess.run = lambda *a, _c=code, _o=out, **k: R(_c, _o)
            got = everything.index_state("es.exe")
            need(got is want, "退出码 %r 输出 %r → %r（期望 %r）" % (code, out, got, want))
    finally:
        everything.subprocess.run = real


@check("P2 启动管线：A2 五项必备措施 / 只关自己那只 / CLI 退出码契约")
def _():
    from aemd import aehost, const, runner
    # 1) §12 校准过的常量必须在位（判据只能是 marker 有没有，不能是启动多久）
    need(int(const.A2_MARKER_TIMEOUT_S) == 90, "A2 标记超时不是 90 秒")
    need(0 < float(const.A2_POLL_INTERVAL_S) <= 1, "A2 轮询间隔不合理")
    need(tuple(const.A2_MODAL_CHECK_AT_S) == (30, 60), "模态框检测时点不对")
    # 2) 四分支枚举
    need([m.value for m in aehost.LaunchMode] == ["a2", "b", "manual"],
         "LaunchMode 三值不对：%r" % ([m.value for m in aehost.LaunchMode],))
    need(aehost.LaunchMode.A2_STARTUP.keeps_ae_alive(), "A2 应该让 AE 留着")
    need(aehost.LaunchMode.B_RAISE.verified_switches() == ("-r",), "-r 是 B 的唯一开关")
    need(aehost.LaunchMode.A2_STARTUP.verified_switches() == (), "A2 不带任何开关")
    # 3) 启动器五项：自删第一句 / 守卫在前 / done 在 evalFile 之后 / runId / 异常不 rethrow
    src = aehost._launcher_source(
        os.path.join(tempfile.gettempdir(), const.A2_GUARD),
        os.path.join(tempfile.gettempdir(), const.A2_ALIVE),
        os.path.join(tempfile.gettempdir(), const.A2_DONE),
        r"C:\ProgramData\AEMediaDoctor\relink.jsx",
        r"C:\ProgramData\AEMediaDoctor\Startup\_x.jsx", "run9")
    need(src.index("me.remove()") < src.index("eGuard"), "自删不是第一条语句")
    need(src.index("eGuard") < src.index("eDone"), "守卫与 done 顺序不对")
    need(src.index("$.evalFile") < src.index('f.open("w")'), "done 写早了")
    need("runId" in src and "run9" in src, "runId 没带进启动器")
    need("ERR|" in src and "throw" not in src.split("catch")[-1],
         "捕获段不许再 throw（N-3：未捕获异常会弹模态框、永不超时）")
    need("$.fileName" not in src, "不许碰 $.fileName（N-1：波浪号 + URL 编码）")
    # 4) close_own_ae 只认本次拉起的那只：陌生人一律回 False 且不杀
    killed = []
    real = aehost.winproc.terminate_tree
    try:
        aehost.winproc.terminate_tree = lambda pid, **k: killed.append(pid)
        need(aehost.close_own_ae(999999, "test") is False, "陌生人也被关了")
        need(killed == [], "陌生人被 terminate_tree 了：%r" % (killed,))
    finally:
        aehost.winproc.terminate_tree = real
    # 5) CLI 退出码契约（v1.9.8 的 0/1/2/3/4/5，语义不许漂）
    need(issubclass(runner.CopyProjectError, Exception), "缺 CopyProjectError（退出码 4）")
    need(issubclass(runner.ScriptGenError, Exception), "缺 ScriptGenError（退出码 5）")
    need(issubclass(aehost.BlockedByForeignAE, Exception), "缺 BlockedByForeignAE（退出码 3）")
    try:
        runner.parse_cli(["--what"])
    except ValueError:
        pass
    else:
        raise AssertionError("未知参数必须抛 ValueError（退出码 2）")
    o = runner.parse_cli(["p.aep", "--dry", "--no-save", "--roots", r"D:\a;D:\b", "--ae", "2025"])
    need(o["dry"] and not o["save"] and len(o["roots"]) == 2 and o["ae"] == "2025",
         "parse_cli 解析不对：%r" % (o,))


@check("P3 界面与诊断：六块在位、结果句带 ambiguous、重启不碰 AE、诊断包真出 zip")
def _():
    import inspect
    import zipfile
    from aemd import const, diag, paths, reports, updater
    from aemd.ui import widgets, window

    # 1) 六块与关键控件：源码层（这里不建窗口，真窗口由 tests/test_p3_ui.py 负责）
    src = inspect.getsource(window.MainWindow)
    for attr in ("card_proj", "card_dirs", "card_opts", "frame_buttons", "kpi_card", "log_card"):
        need('self.%s' % attr in src, "主窗口少了第 %s 块" % attr)
    for m in ("pick_project", "start", "stop", "open_log_dir", "show_missing",
              "export_diag", "poll_update", "restart_now", "_probe_ae"):
        need(hasattr(window.MainWindow, m), "主窗口少了方法 %s" % m)
    # 2) 控件不再抛 NotImplementedError（P0 的空壳必须都填了）
    wsrc = inspect.getsource(widgets)
    need("raise NotImplementedError" not in wsrc, "widgets.py 里还有空壳")
    need("raise NotImplementedError" not in inspect.getsource(diag), "diag.py 里还有空壳")
    need('uniform="u"' in wsrc, "选项条的两列网格没有等宽约束（会串行错位）")
    # 3) 结果一句话：用户亲口要的那一整句（含 ambiguous 尾巴）
    line = reports.summary_line({"fixed": 21, "missing": 11, "ambiguous": 1})
    need(line == "接上 21 条，还缺 11 条，另有 1 条是按同名文件猜的，可能要核对",
         "结果句不对：%r" % (line,))
    # 4) 重启：两种形态的命令行正确，且**不含**任何 AE 相关命令（绝不许 taskkill AE）
    args = updater.restart_args(r"C:\x\拾链.exe")
    need(args, "restart_args 空")
    need(all("taskkill" not in str(a).lower() for a in args), "重启命令里出现了 taskkill")
    need(all("afterfx" not in str(a).lower() for a in args), "重启命令里出现了 AfterFX")
    # 5) 诊断包：真在临时目录打出一个 zip，里面有人能先看的摘要
    tmp = tempfile.mkdtemp(prefix="aemd_p3_")
    real = const.APP_HOME
    try:
        const.APP_HOME = tmp
        p = diag.write_zip()
        need(p and os.path.isfile(p), "诊断包没生成：%r" % (p,))
        with zipfile.ZipFile(p) as z:
            names = z.namelist()
            need("diagnose.json" in names and "diagnose.txt" in names, "包里缺摘要：%r" % names)
    finally:
        const.APP_HOME = real
    # 6) D7 事件时间的功能检查（本地时间原样、未来太多才回退）
    import datetime
    now = datetime.datetime(2026, 11, 15, 15, 0, 0)
    need(diag.parse_event_time("2026-11-15T12:19:52.000Z", now=now)
         == datetime.datetime(2026, 11, 15, 12, 19, 52), "D7 本地时间解错了")


@check("手工映射表（1.1.0）：tests/test_mapping.py 全过（读表 / 查表 / 进 JSX / 不越界）")
def _():
    # 第 32 项。为什么不并进上面那条元测试：那条只管"GBK 下别崩"，本条要检查
    # 映射表的**语义**（键规范化、坏 JSON 退空、老报告兜底 0、结论句不变）。
    p = os.path.join(ROOT, "tests", "test_mapping.py")
    need(os.path.isfile(p), "找不到 tests\\test_mapping.py")
    out = subprocess.run([sys.executable, p], capture_output=True, cwd=ROOT, timeout=180)
    text = _decode(out.stdout) + _decode(out.stderr)
    need(out.returncode == 0, "test_mapping 退出码 %s：%s" % (out.returncode, text.strip()[-600:]))
    need("ALL PASS" in text, "test_mapping 没打印 ALL PASS：%s" % text.strip()[-400:])


@check("插件禁用/恢复（1.1.9）：tests/test_plugin_guard.py 全过")
def _():
    p = os.path.join(ROOT, "tests", "test_plugin_guard.py")
    need(os.path.isfile(p), "找不到 tests\\test_plugin_guard.py")
    out = subprocess.run([sys.executable, p], capture_output=True, cwd=ROOT, timeout=120)
    text = _decode(out.stdout) + _decode(out.stderr)
    need(out.returncode == 0, "test_plugin_guard 退出码 %s：%s" % (out.returncode, text.strip()[-600:]))
    need("ALL PASS" in text, "test_plugin_guard 没打印 ALL PASS：%s" % text.strip()[-400:])


@check("工程插件解析（1.1.9）：tests/test_project_plugins.py 全过")
def _():
    p = os.path.join(ROOT, "tests", "test_project_plugins.py")
    need(os.path.isfile(p), "找不到 tests\\test_project_plugins.py")
    out = subprocess.run([sys.executable, p], capture_output=True, cwd=ROOT, timeout=120)
    text = _decode(out.stdout) + _decode(out.stderr)
    need(out.returncode == 0, "test_project_plugins 退出码 %s：%s" % (out.returncode, text.strip()[-600:]))
    need("ALL PASS" in text, "test_project_plugins 没打印 ALL PASS：%s" % text.strip()[-400:])


@check("真机夹具随仓库走：tests/fixtures 三件齐全且能复现 21/11/1")
def _():
    # 第 33 项。来历：p1_parity 的 D2 原先读工程目录 `.ae_doctor`，而每轮开跑都会
    # clear_state() 清掉上一次结果 —— 于是它变成「有就跑、没就 SKIP」，而 SKIP 在汇总里
    # 跟 PASS 长得一样。这是最危险的一种绿。现在夹具进仓库，缺了必须红。
    from aemd import reports
    fx = os.path.join(ROOT, "tests", "fixtures")
    for fn in ("relink_machine.log", "report_machine.json", "missing_machine.txt"):
        p = os.path.join(fx, fn)
        need(os.path.isfile(p), "夹具缺失（应当随仓库走）：%s" % p)
        need(os.path.getsize(p) > 100, "夹具 %s 只有 %d 字节，像被截断过"
             % (fn, os.path.getsize(p) if os.path.exists(p) else -1))
    rep = reports.parse_report(os.path.join(fx, "report_machine.json"))
    need(rep is not None, "报告夹具解析不出来（JSON 坏了？）")
    st = rep["stats"]
    need((st.get("total"), st.get("fixed"), st.get("missing"), st.get("ambiguous")) == (32, 21, 11, 1),
         "夹具里的真机数字被改了：%r" % (st,))
    n = len(reports.parse_missing(os.path.join(fx, "missing_machine.txt")))
    need(n == 11, "缺件夹具应当 11 条，实得 %d" % n)


def main():
    print("%s %s —— 自检（不启动界面、不碰 AE）" % (aemd.APP_NAME, aemd.APP_VER))
    print("项目根：%s" % ROOT)
    print("")
    print("%d ok · %d SKIP · %d FAIL" % (len(OK), len(SKIP), len(BAD)))
    for name, why in SKIP:
        print("  SKIP  %s —— %s" % (name, why))
    for name, why in BAD:
        print("  FAIL  %s —— %s" % (name, why))
    print("")
    if BAD:
        print("FAIL")
        return 1
    print("ALL PASS（%d 项通过，%d 项留待后续阶段，0 项失败）" % (len(OK), len(SKIP)))
    return 0


if __name__ == "__main__":
    sys.exit(main())