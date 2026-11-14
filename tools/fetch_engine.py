#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载 Everything（便携版）与 ES 命令行工具，解包到 engine/Everything/。

用法：
    python tools/fetch_engine.py           # 下载并解包到 <项目>/engine/Everything
    python tools/fetch_engine.py --check   # 只检查是否已就绪

为什么单独下载而不随仓库分发：
    Everything 是 voidtools 的软件，按它自己的许可分发，本仓库只提供取用脚本。
"""
import io
import os
import re
import ssl
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEST = os.path.join(ROOT, "engine", "Everything")

PAGE = "https://www.voidtools.com/downloads/"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def ctx():
    c = ssl.create_default_context()
    try:
        c.check_hostname = False
        c.verify_mode = ssl.CERT_NONE
    except Exception:
        pass
    return c


def get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout, context=ctx()).read()


def pick_urls(html):
    """从下载页里挑出 x64 的 Everything 便携版与 ES 命令行工具。"""
    pairs = re.findall(r'href="([^"]*?Everything-1\.4[^"]*?x64\.zip)"', html)
    es = re.findall(r'href="([^"]*?ES-1\.1[^"]*?x64\.zip)"', html)
    norm = lambda u: u if u.startswith("http") else "https://www.voidtools.com/" + u.lstrip("/")
    return (norm(pairs[0]) if pairs else None), (norm(es[0]) if es else None)


def unpack(data, out_dir, wanted):
    """从 zip 里取需要的文件（忽略目录层级差异）。"""
    got = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for n in z.namelist():
            base = os.path.basename(n)
            if base.lower() in wanted:
                os.makedirs(out_dir, exist_ok=True)
                with z.open(n) as f, open(os.path.join(out_dir, base), "wb") as g:
                    g.write(f.read())
                got.append(base)
    return got


def ready():
    need = ["everything.exe", "es.exe"]
    return all(os.path.isfile(os.path.join(DEST, n)) for n in need)


def main():
    if "--check" in sys.argv:
        print("engine ready" if ready() else "engine missing")
        return 0 if ready() else 1
    if ready():
        print("已经就绪：", DEST)
        return 0

    print("读取下载页…")
    html = get(PAGE).decode("utf-8", "ignore")
    ev_url, es_url = pick_urls(html)
    if not (ev_url and es_url):
        print("没能从下载页解析出链接，请手动下载：", PAGE)
        return 2

    os.makedirs(DEST, exist_ok=True)
    print("下载", ev_url)
    got = unpack(get(ev_url, 120), DEST, {"everything.exe", "everything.lng", "es.exe"})
    print("  得到", got)
    print("下载", es_url)
    got2 = unpack(get(es_url, 120), DEST, {"es.exe"})
    print("  得到", got2)

    if ready():
        print("完成：", DEST)
        return 0
    print("缺少文件，请检查网络后重试")
    return 1


if __name__ == "__main__":
    sys.exit(main())