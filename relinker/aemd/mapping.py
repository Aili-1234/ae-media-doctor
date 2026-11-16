"""手工映射表：用户自己把「工程里认不出的原文件名」指到「我这儿实际有的那个文件」。

为什么需要它
------------
四层自动匹配（全名 / 基名 / 全盘索引 / 同名猜）总有认不出的：改了名的、换了扩展名的、
只有一半名字对的。这种时候无论重连几遍都救不回来，只能人来指一次。指过之后应该记住，
下次同一个工程再跑就不用再指 —— 于是有这张表。

它**不做什么**
--------------
* 不猜。命中映射就直接用；映射指向的文件不存在就记 MISSING 并说明原因，绝不悄悄退回自动匹配
  （那会让用户以为「我指的没生效」，排查时最费时间）。
* 不搬文件。表里存的是「去哪儿拿」，搬不搬由「收编 Footages」那套独立决定。
* 不参与索引。它不是候选来源，只在自动匹配全落空之后才查。
* 不改工程。写工程永远是 relink 那一步的事。

表长什么样
----------
``%APPDATA%\\AE素材医生\\mapping.json``::

    {"version": 1,
     "map": {"旧名字.mp4": "D:/素材/新名字.mp4",
             "d:/老工程/footage/a.png": "D:/素材/a-修好的.png"}}

键有两种，都可以用：
* **原文件名（小写）** —— 同名的都算，最省事；
* **原完整路径（小写）** —— 只对那一个生效，用于「同一个文件名在不同目录里有不同替身」。

查表时**先查完整路径、再查文件名**（越具体越优先）。

坏文件策略
----------
JSON 坏了、不是对象、map 不是对象、键值不是字符串 —— 一律当空表返回、**不抛**。
理由和 cfg 一样：这张表只是加速手段，读不懂就不该拦住用户干活。
"""

import io
import json
import os

from . import const

#: 文件名（放在 %APPDATA%\AE素材医生\ 下，和 settings.json 同处）
MAP_FILE = "mapping.json"

#: 表结构版本。将来换结构时按它迁移；现在只有 1。
VERSION = 1

#: 一个键最长允许多少字符（防手滑把整段日志粘进来）
MAX_KEY = 512


def default_path():
    """映射表默认位置。**运行时**读 const，测试可以 patch 过去。"""
    return os.path.join(const.APP_HOME, MAP_FILE)


def _clean_key(k):
    """键规范化：去空白、转小写、反斜杠统一成正斜杠。非字符串返回空串。"""
    if not isinstance(k, str):
        return ""
    s = k.strip().replace("\\", "/")
    if not s or len(s) > MAX_KEY:
        return ""
    return s.lower()


def _clean_val(v):
    """值规范化：去空白、反斜杠改正斜杠。非字符串返回空串。"""
    if not isinstance(v, str):
        return ""
    s = v.strip().replace("\\", "/")
    return s


def normalize(table):
    """把任意 dict 洗成「小写正斜杠键 -> 正斜杠值」，丢掉不合法的条目。

**唯一**的规范化实现：load / save / js_object 三处都调它。原先三处各抄了一遍
    （键去空白、转小写、反斜杠换正斜杠、长度上限、非字符串丢弃），改一处忘一处
    就会出现「存进去能查、序列化进 JSX 又查不到」这种最难查的偏差。
    """
    out = {}
    for k, v in (table or {}).items():
        ck, cv = _clean_key(k), _clean_val(v)
        if ck and cv:
            out[ck] = cv
    return out


def load(path=None):
    """读映射表 → ``{小写键: 目标路径}``。任何读取/解析失败都返回空 dict，不抛。"""
    p = path or default_path()
    try:
        with io.open(p, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    raw = data.get("map")
    if not isinstance(raw, dict):
        return {}
    return normalize(raw)


def save(table, path=None):
    """整表落盘（临时文件 + 原子替换，UTF-8 无 BOM）。返回落盘路径。

    `table` 里非法的键值会被丢掉而不是让整次保存失败 —— 用户点「保存」时
    最不希望看到的是「因为一条坏记录，别的都没存上」。
    """
    p = path or default_path()
    clean = normalize(table)
    paths_dir = os.path.dirname(p)
    if paths_dir:
        try:
            os.makedirs(paths_dir, exist_ok=True)
        except OSError:
            pass
    text = json.dumps({"version": VERSION, "map": clean},
                      ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp = p + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)
    return p


def lookup(name, original_path=None, table=None):
    """查替身。先查原完整路径，再查原文件名；都没有返回空串。"""
    t = load() if table is None else table
    if not isinstance(t, dict):
        return ""
    if original_path:
        hit = t.get(_clean_key(original_path))
        if hit:
            return hit
    if name:
        return t.get(_clean_key(name)) or ""
    return ""


def add(table, name_or_path, target):
    """往表里加/改一条，返回新表（不改入参）。"""
    out = dict(table or {})
    k, v = _clean_key(name_or_path), _clean_val(target)
    if k and v:
        out[k] = v
    return out


def remove(table, name_or_path):
    """从表里删一条，返回新表（不改入参）。删不存在的键不报错。"""
    out = dict(table or {})
    out.pop(_clean_key(name_or_path), None)
    return out


def entries(table):
    """给界面用：``[(键, 目标路径)]``，按键排序。"""
    return sorted((table or {}).items())


def js_object(table):
    """转成 JS 对象字面量，供 JSX 里的 ``CFG.mapping`` 用。

    键值都已经规范化（小写、正斜杠），所以这边只做 JSON 序列化 + 反斜杠保底：
    JSON 的 ``\\\\`` 在 JS 里也是合法转义，但路径里不该再有反斜杠了。
    """
    return json.dumps(normalize(table), ensure_ascii=False, sort_keys=True)