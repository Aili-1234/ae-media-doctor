# -*- coding: utf-8 -*-
"""aemd.scanner —— 素材索引：先在自己人这边扫一遍，减少 JSX 里的磁盘遍历。

负责：
  · `build_index(roots, depth, out_path)`：os.scandir 递归扫素材 → 写 `_media_index.json`
  · `short_key(name)`：素材短名规则（**必须与 JSX 里的 shortKey 完全一致**）
  · `collect_roots(...)`：把用户给的目录 + 自动推断结果合成去重后的搜索根列表
  · `same_content(a, b)`：D1 撞名时比内容（前 1 MB sha256），不是只看大小

不负责：
  · 真的去"重连"工程里的素材（那是 AE 里的 JSX 干的活）
  · 决定扫描深度上限（调用方给；界面上是「深度」选项）
  · 全盘兜底搜索（那是 everything.py）

依赖：`.const`（MEDIA_EXT / SAME_CONTENT_SAMPLE）。**不碰** winproc / everything / ui。

⚠ 与 JSX 的一致性约束：`short_key()` 的规则（先切扩展名、再切第一个点、小写、strip）
  在 Python 和 ExtendScript 里各有一份实现，改一边就必须改另一边 ——
  `tools/shortkey_parity.py` 会拿真实样例双向对拍（Python ↔ node 跑模板里的 shortKey）。
"""

import hashlib
import io
import json
import os

from . import const


def short_key(name):
    """`1 (1).119.880fps.QE.HW.VFIF.cb4018.mp4` → `1 (1)`（和 JSX shortKey 同规则）。

    规则逐字来自一代 `app._short_key`，与模板里 `shortKey()` 一一对应：
      1. 先按**最后一个点**切掉扩展名
      2. 全部小写
      3. 再切掉**第一个点**及其右边（`.119.880fps…` 这一串是帧率说明，不是名字的一部分）
      4. 两端 strip
    注意第 3 步的 `dot > 0`：以点开头的隐藏文件（`.DS_Store`）不会被切成空串。
    """
    base = name.rsplit(".", 1)[0] if "." in name else name
    base = base.lower()
    dot = base.find(".")
    if dot > 0:
        base = base[:dot]
    return base.strip()


def build_index(roots, depth, out_path):
    """扫 roots（深度 depth）把素材名写成 JSON 索引，给 JSX 的 loadPyIndex 读。

    比在 AE 里用 `Folder.getFiles` 递归快一个数量级，而且不卡 AE 的界面线程。
    返回收录的素材条数。

    两个结构：
      exact：小写文件名 → [绝对路径…]        （精确匹配，主力）
      short：短名|小写扩展名 → [绝对路径…]    （同名不同帧率版本时兜底）

    落盘是「先写 .tmp 再 os.replace」：JSX 随时可能在读，绝不能让 AE 读到半个文件。
    ⚠ 遍历顺序是 `stack.pop()`（后进先出），与一代**刻意保持一致** ——
      同一个目录树扫出来的 JSON 才会逐字节相同（parity 测试就是靠这个）。
    """
    exact, short = {}, {}
    count = 0
    stack = [(r, 0) for r in roots]
    while stack:
        d, dep = stack.pop()
        if dep > depth:
            continue
        try:
            it = os.scandir(d)
        except OSError:
            continue
        try:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        stack.append((e.path, dep + 1))
                        continue
                    if not e.is_file(follow_symlinks=False):
                        continue
                    dot = e.name.rfind(".")
                    if dot < 0 or e.name[dot + 1:].lower() not in const.MEDIA_EXT:
                        continue
                    k = e.name.lower()
                    exact.setdefault(k, []).append(e.path)
                    sk = short_key(e.name) + "|" + e.name[dot + 1:].lower()
                    short.setdefault(sk, []).append(e.path)
                    count += 1
                except OSError:
                    continue
        finally:
            it.close()
    data = {"exact": exact, "short": short, "count": count}
    tmp = out_path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, out_path)

    # 1.1.9 安全：额外写一份**扁平化、逐行、无嵌套**的 `.lines`，给 JSX 安全解析用。
    # 为什么：JSON 版本 JSX 用 eval 读（AE 2020-2021 无 JSON.parse），eval 是任意代码通道。
    # `.lines` 每行一个条目、tab 分隔，JSX 用 split 逐行读，零 eval 风险。
    # 行格式：`exact\t<名字>\t<路径>` / `short\t<键>\t<路径>` / `count\t<N>`
    # tab 是 Windows 文件名的非法字符，路径里不可能出现，用它当分隔符安全。
    lines_tmp = out_path + ".lines.tmp"
    lines_out = os.path.join(os.path.dirname(out_path), const.WORK_INDEX_LINES)
    try:
        with io.open(lines_tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("count\t%d\n" % count)
            for k in exact:
                for p in exact[k]:
                    fh.write("exact\t%s\t%s\n" % (k, p))
            for k in short:
                for p in short[k]:
                    fh.write("short\t%s\t%s\n" % (k, p))
        os.replace(lines_tmp, lines_out)
    except OSError:
        # .lines 写不出来不影响主流程（JSON 还在，JSX 会退回 eval 读 JSON）
        try:
            os.remove(lines_tmp)
        except OSError:
            pass
    return count


def guess_dirs(project_file):
    """工程目录附近的候选搜索目录（顺序 = 一代 `App.guess_dirs`）。

    Footages 子目录排第一：AE 自己的「收编到 Footages」就是把素材搬到这里，
    重连时它命中率最高。
    """
    if not project_file:
        return []
    base = os.path.dirname(os.path.abspath(project_file))
    return [os.path.join(base, "Footages"), base,
            os.path.join(base, "素材"), os.path.dirname(base)]


def collect_roots(explicit_dirs, project_file=None, cfg_dirs=None, state_dirname=None):
    """合成搜索根：显式目录 + 配置里的 + 工程目录推断，去重且保序。

    A5（§spec-v1.9.8）：**绝不把工程自己的 `.ae_doctor` 当素材目录收进来**
    —— 里面放的是报告和缺件清单，收进来只会让索引白扫一遍、还可能把清单当素材。
    所以这里在收根时就把 state 目录剔掉（JSX 侧也有同样一道 `CFG.stateDir` 跳过）。
    """
    sd = state_dirname if state_dirname is not None else const.STATE_DIRNAME
    out = []
    for d in list(cfg_dirs or []) + list(explicit_dirs or []):
        if not d or not str(d).strip():
            continue
        p = os.path.abspath(str(d).strip())
        if os.path.basename(p).lower() == sd.lower():
            continue          # 报告目录不是素材目录
        if not os.path.isdir(p):
            continue
        if p not in out:
            out.append(p)
    for d in guess_dirs(project_file):
        p = os.path.abspath(d)
        if os.path.basename(p).lower() == sd.lower():
            continue
        if os.path.isdir(p) and p not in out:
            out.append(p)
    return out


def _sha_head(path, sample):
    h = hashlib.sha256()
    with io.open(path, "rb") as fh:
        h.update(fh.read(sample))
    return h.hexdigest()


def same_content(path_a, path_b, sample=None):
    """D1（§8.1）：两个同名候选是不是**同一个文件**（比前 1 MB 的 sha256）。

    一代教训 F-12 / O-03：收编素材撞名时只比 `sizeOf()`，同尺寸不同内容的两条素材
    会被当成同一条，第二条直接被丢掉（用户丢了素材却没有任何报错）。

    返回：
      True  —— 前 1 MB 内容相同（视为同一份，可以合并）
      False —— 内容不同（**必须**改名 `_2` 并记 `CONFLICT`，不许覆盖、不许丢）
      None  —— 读不了/判不了（文件不存在、被占用、权限不够）。**判不了 ≠ 相同**，
               调用方遇到 None 按"不同"处理（宁可多留一份，也不要悄悄丢素材）。

    为什么只比前 1 MB：素材是几百 MB 的视频，全量哈希会让收编时间翻几倍；
    同尺寸 + 前 1 MB 相同，在素材语境下已经是极强证据。
    """
    if not path_a or not path_b:
        return None
    n = const.SAME_CONTENT_SAMPLE if sample is None else int(sample)
    try:
        if os.path.getsize(path_a) != os.path.getsize(path_b):
            return False
        return _sha_head(path_a, n) == _sha_head(path_b, n)
    except (OSError, ValueError):
        return None