# -*- coding: utf-8 -*-
"""aemd.const —— 全局常量：路径、超时、版本区间、文案、白名单。

负责：
  · 所有「魔法数字」与「魔法字符串」的唯一出处（超时、保留份数、版本区间）
  · 软件自身的目录布局（%APPDATA%\\AE素材医生\\logs、…\\work）
  · 工程目录里的结果子目录名与结果文件名
  · 界面文案常量（改字只改这里）
  · 素材扩展名白名单、文件名非法字符表（正则/字符集白名单）

不负责：
  · 创建目录（那是 cfg.ensure_dir / cfg.state_dir_of 的事）
  · 读写配置文件（cfg.py）
  · 任何 I/O、任何 Windows API 调用 —— 本模块**纯数据 + 纯函数**，import 无副作用

依赖：无（只 import os 用于拼路径；不 import 任何 aemd 子模块）

命名对照：v1.9.8 把这些散在 app.py 顶部，名字基本沿用，便于对拍时逐项核对。
"""

import os

from . import APP_NAME, APP_VER   # noqa: F401  （重导出，方便 `from aemd.const import APP_VER`）

# ---------------------------------------------------------------- 启动等待
# 宁可早点说清楚，也不要干等（v1.9.4 定的口径，v2.0 原样继承）：
# 实测这台机器 AE 冷启动 14–15 秒，最差 53 秒。所以：
#   40 秒体检一次：进程在不在？有没有主窗口？据此说清卡在哪一层；
#   连进程都没有 → 60 秒判死（AE 没起来，再等也没用）；
#   进程在但没窗口 → 90 秒判死（卡在初始化，继续等只会更烦）；
#   任何失败都会先「结算」已经接上的素材，别让成果跟着超时一起蒸发。
START_WAIT_S = 90           # 进程在、还没跑脚本，最多等这么久
NO_PROC_WAIT_S = 60         # 连进程都没有，最多等这么久
DIAG_AT_S = 40              # 什么时候做那次体检
SCRIPT_WAIT_S = 600         # 脚本已经开始执行后的上限
A2_ALIVE_WAIT_S = 90        # R4：A2（Startup 注入）等 alive.txt 标记的上限

# 版本区间：ExtendScript 接口从 2020 起稳定，超出这个区间的安装不进下拉框。
# （v1.9.2 硬编码 2021 起，装了 2020 的人选不到——文档却写「2020–2026 均可用」。）
AE_MIN_YEAR = 2020
AE_MAX_YEAR = 2026

# ---------------------------------------------------------------- 软件目录
# 数据目录名**不跟显示名走**：改名（拾链）不能把用户已有的
# settings.json / logs / engine 副本丢在旧目录里（R8 的延续）。
DATA_DIR_NAME = "AE素材医生"
APP_HOME = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), DATA_DIR_NAME)
LOG_DIR = os.path.join(APP_HOME, "logs")     # R8：运行日志
WORK_DIR = os.path.join(APP_HOME, "work")    # R8：临时件（jsx 副本、素材索引）
LOG_KEEP = 50                                # logs\ 只留最新这么多个 relink-*.log
WORK_KEEP = 20                               # work\ 只留最新这么多个临时件

# ---------------------------------------------------------------- 工程目录
STATE_DIRNAME = ".ae_doctor"                 # R8：工程里只留这个子目录
# 工程目录 `.ae_doctor\` 里的结果件（跑之前先清，避免读到上一轮的旧结果）。
# v1.9.8 起日志搬进软件目录，工程里只留用户要看的结果。
REPORT_FILES = ("_relink_report.json", "_relink_missing.txt", "_relink_moved.txt")
REPORT_JSON = "_relink_report.json"
MISSING_TXT = "_relink_missing.txt"
MOVED_TXT = "_relink_moved.txt"

# ---------------------------------------------------------------- JSX 落点
# R9：JSX 本体必须落在纯 ASCII 路径，AE 才不会因为路径里的中文读不到脚本。
# 报告则通过 __REPORT_DIR__ 写回工程目录（脚本里由 CFG 传进去）。
JSX_TARGET_DIR = r"C:\ProgramData\AEMediaDoctor"
WORK_JSX = "_media_doctor.jsx"               # 手动模式的脚本副本，落 WORK_DIR
WORK_INDEX_JSON = "_media_index.json"        # 预扫描索引，落 WORK_DIR
WORK_INDEX_LINES = "_media_index.lines"      # 1.1.9 扁平化安全索引（JSX 优先读，免 eval）
MANUAL_JSX = "manual_fix.jsx"

# ---------------------------------------------------------------- A2 Startup 注入（R4）
# A2 = 把启动脚本放进 AE 的用户级 Startup 目录，然后**无参数**启动 AE：
# 脚本随 AE 启动执行、跑完自删，**AE 留在原地**（这是 -r 做不到的）。
# 下列常量全部按 2026-11-15 的 A2 真机实验（报告：ae-tools\A2-启动注入-真机验证-2026-11-15.md）
# 校准；数据再好也不许删掉「保守闸门」——见 A2_MARKER_TIMEOUT_S 的注释。
#
# 三个落点都在 JSX_TARGET_DIR（纯 ASCII，ExtendScript 才读得稳），不在工程里、不在 work\：
A2_GUARD = "a2_guard.txt"            # 授权令牌：注入前写 runId；脚本第一件事核对它
A2_ALIVE = "a2_alive.txt"            # 脚本已开始执行（Python 侧轮询这个，内容 = runId）
# 完成标记：**活干完之后才写**。绝不能写早（写早了，一次失败会被记成完成）。
A2_DONE = "a2_done.txt"
A2_GUARD_SEP = "\n"                  # 守卫文件内容 = 一行 runId
# 真机实测：脚本在启动后约 14 s 开始执行（首次冷启动那轮到 57 s 才出窗口），
# 所以判据只能是「marker 有没有」，不能是「启动多久」。90 s 是闸门，没出现就降级 -r。
#: **冷启动**的额外容忍（2026-11-15 16:0x 实测）：这台机器在没有外网时 AE 2025 冷启动
#: 98.1 秒才出主窗口（等 dme.adobe.io 超时 + 插件加载）。此前的 90 秒闸门会在「AE 还在
#: 正常启动」时就把我们自己的实例强关掉，于是 A2 永远超时、-r 又被单实例转发进卡住的实例
#: ——真机验收第 2/3 轮就是这么失败的。所以判据分两段：
#:   · 还没出主窗口 → 是冷启动慢，给 COLD_START_WAIT_S / A2_COLD_START_TIMEOUT_S；
#:   · 出了主窗口却还没跑脚本 → 才是真卡住，仍只看 A2_MARKER_TIMEOUT_S / START_WAIT_S。
COLD_START_WAIT_S = 240
#: 主窗口已经出现、但整个 UI 线程无响应（卡死）时的判据与上限。
#: 2026-11-16 真机：AE 打开工程时死锁，界面上只是挂着一个「打开项目」进度框，
#: 旧逻辑会按 START_WAIT_S=90 甚至 COLD_START 240 秒干等，用户只能看着发呆。
HUNG_CHECK_AT_S = 25
HUNG_WAIT_S = 75

#: 1.1.9：真死锁的判据（两条同时成立才算，见 runner._poll_running）。
#: 2026-11-17 取证：插件窗口（Optical Flares / VCWINDOW）一冒出来，CPU 就归零、
#: 主窗口再也回不了 WM_NULL，Esc / WM_CLOSE 都无效 —— 这种等下去没有任何意义。
#: 所以：认得出来的插件窗口只给 PLUGIN_BLOCK_WAIT_S 秒；认不出的（只是无响应 + CPU 不动）
#: 给 DEADLOCK_WAIT_S 秒，都比 SCRIPT_WAIT_S（10 分钟）短得多。
PLUGIN_BLOCK_WAIT_S = 20
DEADLOCK_WAIT_S = 30
#: 1.1.9 热启动：先把 AE 起好、等主窗口能响应，再把工程交给它打开（等同双击）。
#: 这是绕开「插件窗口把冷启动打开锁死」的那条路（用户手动一直是这么做的）。
#: 脚本侧等工程打开的上限也是这个值 —— 两边同一个数，日志才对得上。
WARM_OPEN_WAIT_S = 180
#: 死锁采样间隔 / 判"CPU 没动"的阈值（秒）。5 秒里 CPU 涨不到 0.3 秒就算闲着。
HANG_SAMPLE_S = 5.0
IDLE_CPU_S = 0.3
A2_COLD_START_TIMEOUT_S = 240

A2_MARKER_TIMEOUT_S = 90
A2_POLL_INTERVAL_S = 0.4
# 模态框检测时点（秒）：脚本解析失败/未捕获异常时 AE 会弹模态框，且永远不会超时，
# 必须由 Python 侧主动去点掉（winproc.close_modal_dialogs）。
A2_MODAL_CHECK_AT_S = (30, 60)
# 「崩溃修复选项」提示框（标题为空的 #32770）：上次 AE 没正常退出就会出现，
# 不点它 AE 永远不出主窗口、Startup 脚本永远不执行（2026-11-15 真机回归第 3 轮实证）。
# 处理方式 = 给它发一次回车（默认焦点在「继续」），最多按 A2_ENTER_MAX 次。
A2_ENTER_MAX = 3
# AE 启动期**有标题**的 #32770 进度框（2026-11-15 真机：它们卡在事件循环之外，
# 我们排进 app.scheduleTask 的活要等它们过去才被调起 —— 实测等了 616 秒不动，
# 对框按一次回车后 4 秒内脚本就跑完）。只按这几个标题，别的一律不碰。
STARTUP_PROGRESS_TITLES = ("解析字体", "打开项目", "正在打开项目")
A2_PROGRESS_ENTER_MAX = 6
# 「工程缺素材」提示框最多按几次。比进度框更保守：这种框按错一下就可能
# 弹出文件浏览器、或把工程半开着结束，宁可少按、留给用户。
A2_MISSING_ENTER_MAX = 3
# A2 通道的执行时机（2026-11-15 真机回归 + 协调者判断）：
# Startup 脚本跑在 AE 主线程、主循环还没转起来的时候。此刻 app.open() 会与 AE 自己的
# 「打开项目 / 解析字体」进度框互等（进度框要靠消息泵推进），表现为「marker 有了、
# JSX 日志 0 字节、10 分钟不结束」。**不能用 $.sleep 轮询去等** —— sleep 是同步阻塞，
# 会把消息泵按住，进度框更动不了（死锁）。
# 正解是把干活推迟到主循环起来之后：启动器只做极轻的事（自删 + 写 marker + 注册
# app.scheduleTask 延迟任务），由事件循环去调真正的脚本。
A2_SCHEDULE_DELAY_MS = 800           # scheduleTask 的延迟（毫秒）
A2_SCHEDULE_WAIT_S = 25              # Python 等到「被调起」标记的上限（scheduleTask 不触发就降级）
A2_SCRIPT_EXPECT_MS = 229            # 只作日志参考，不做判据（实测脚本本体 229 ms）
A2_BASELINE_LAUNCH_S = 14            # 只作日志参考，不做判据（实测开始执行约 14 s）
# 旧名保留（aehost 早期版本引用过；新代码一律用上面两个）
A2_ALIVE_WAIT_S = A2_MARKER_TIMEOUT_S
A2_POLL_S = A2_POLL_INTERVAL_S
A2_LAUNCH_SETTLE_S = 3.0             # 无参启动后先等这么久再看 marker（AE 冷启动要时间）
STARTUP_SUBDIR = ("Scripts", "Startup")   # %APPDATA%\Adobe\After Effects\<版本>\Scripts\Startup

# ---------------------------------------------------------------- 界面文案
RUN_TEXT = "开始修复"
NEED_CLOSE_TEXT = "关掉了，重试"              # R3
MORE_CLOSED = "更多设置 ▾"
MORE_OPEN = "更多设置 ▴"
EMPTY_HINT = "还没选工程"
LOG_EMPTY_HINT = "还没跑过，点「开始修复」"

# ---------------------------------------------------------------- 数据白名单
# 素材扩展名（预扫描索引只收这些；JSX 侧有一份同样的判断，两边必须同步）
MEDIA_EXT = {"mp4", "mov", "mkv", "avi", "m4v", "webm", "mxf", "wmv", "flv",
             "mp3", "wav", "aif", "aiff", "m4a", "aac", "ogg", "flac",
             "jpg", "jpeg", "png", "exr", "tif", "tiff", "psd", "webp", "gif", "dpx", "hdr"}

# Windows 文件名里不许出现的字符（做日志/副本文件名时逐个换成 _）
ILLEGAL_FILENAME_CHARS = '\\/:*?"<>|'

# 拖进来的文件里，哪些扩展名算「工程」（其余（如 .ffx）忽略）
PROJECT_EXTS = (".aep",)

# 所有子进程（powershell / taskkill / tasklist / es）都不许弹控制台窗口：
# 否则关窗口时会闪一个蓝色 PowerShell 窗口。
# （放在 const 里，winproc/everything 共用同一份，别各写一个字面量。）
NO_WINDOW = 0x08000000      # subprocess.CREATE_NO_WINDOW

# Adobe 安装根目录候选（find_ae_installs 按顺序试，存在即扫）
ADOBE_DIRS = [
    os.path.join(os.environ.get("ProgramW6432") or r"C:\Program Files", "Adobe"),
    os.path.join(os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)", "Adobe"),
    os.path.join(os.environ.get("ProgramFiles") or r"C:\Program Files", "Adobe"),
    r"C:\Program Files\Adobe",
    r"C:\Program Files (x86)\Adobe",
    r"D:\Program Files\Adobe",
    r"D:\Program Files (x86)\Adobe",
    r"E:\Program Files\Adobe",
]

# AE 的用户级偏好根（Startup 脚本要落在这里面的 <版本>\Scripts\Startup；不需要管理员）
ADOBE_PREFS = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                           "Adobe", "After Effects")

# 进程名（统一小写，比较时也 lower）
AE_EXE = "afterfx.exe"
EVERYTHING_EXE = "everything.exe"
ES_EXE = "es.exe"

# ---------------------------------------------------------------- 命令行开关白名单（R5）
# 一代的教训：为了「别让 AE 弹框」试了 6 个开关（-s / -execute / -help / -noui …），
# 结果是 AE 弹了三次模态框糊在用户脸上。**只有实测过的开关才允许出现在本表里**，
# 其它写法必须当场被 tests/test_no_unverified_switch.py 拦住。
#
# 目前唯一实测可用的：`-r <jsx>`（跑脚本；**已知副作用：跑完 AE 自己会退出**，
# R4 的 B_RAISE 模式就是为兜这个副作用而存在的）。
# 往这里加开关的唯一门径：附上实测证据路径（日志/报告），并在 tests 里同步加断言。
VERIFIED_SWITCHES = ("-r",)

# 每个开关对应的实测证据（人看的东西；tests 会断言 VERIFIED_SWITCHES 里的键都在此）
VERIFIED_SWITCH_EVIDENCE = {
    "-r": "ae-tools\\AE-dump分析报告-2026-11-15.md 之后的 ae-cli-probe 实测："
          "-r 是唯一能把脚本送进 AE 且不弹框的开关；副作用是脚本跑完 AE 进程自己退出。",
}

# ---------------------------------------------------------------- §8.1 新增防线（D1–D8）
# 来源：ae-tools\rewrite\01-缺陷与教训清单.md；编号与方案 §8.1 一致。

# D1 收编撞名只比 sizeOf() → 同名候选要算内容指纹再决定是不是同一个文件
SAME_CONTENT_SAMPLE = 1024 * 1024        # 只算前 1 MB 的 sha256（整文件算太慢）
CONFLICT_TAG = "CONFLICT"                # 撞名撞内容的日志标记
DUP_SUFFIX = "_2"                        # 不同内容落盘时的改名后缀（_2 / _3 …）

# 1.1.9 方案甲：修复前把原工程备份成 `<名>_backup.aep`，然后**直接在原工程上修**。
# 备份是还原点，不是"工作副本"——脚本修的永远是用户打开的那个原工程，无副本概念。
BACKUP_SUFFIX = "_backup"

# D2 报告目录只读 → JSX 里写不进 JSON 时，靠这个键告知 Python「以日志为准」
REPORT_KEY_LOG_WRITABLE = "logWritable"

# D3 沉默硬等 → 分段常量（已在上面 START_WAIT_S / NO_PROC_WAIT_S / DIAG_AT_S）
PARTIAL_SETTLE_TAG = "PARTIAL"           # 任何失败先结算「本来在线/本次接上/仍缺」

# D4 占位符残留（`__REPORT_DIR__` 之类没填）必须在 build 时就炸，不能靠 node --check
PLACEHOLDER_PREFIX = "__"
PLACEHOLDER_SUFFIX = "__"

# D6 关旧 AE 后进程列表清空再等一会儿才启动（单实例会把参数吞掉）
AE_RESTART_GRACE_S = 2.0
AE_RESTART_GRACE_MAX_S = 6.0

# D7 wevtutil 的 `Date:` 带 Z 但其实是本地时间 → 只有落在未来超过这个秒数才按 UTC 回退
WEVT_FUTURE_TOLERANCE_S = 900

# U-02 写入白名单：这些前缀**永远不写**（插件目录、Adobe 安装目录属于用户资产）
FORBIDDEN_WRITE_PREFIXES = (
    r"C:\Program Files\Adobe",
    r"C:\Program Files\Common Files\Adobe",
    r"C:\Program Files (x86)\Adobe",
    r"C:\Program Files\BorisFX",
)