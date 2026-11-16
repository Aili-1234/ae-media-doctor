# 拾链 · Relinker

> 给 After Effects 工程里**丢了的素材**重新接上。选一个 `.aep`，它自己去找、自己去连，最后给一份能看的报告。

这是 AE 素材医生（v1.x）的**重写版**：不是在同一份 `app.py` 上继续打补丁，而是按模块重写、逐字节对拍旧版行为、并把启动 AE 的整条链路（Startup 注入 / 对话框 / 超时）当成一等公民。

当前版本：**1.1.3** ｜ 平台：Windows 10/11 ｜ AE：2020–2026 ｜ 分发：免安装 onedir

---

## 它解决什么问题

换机器、接别人的工程、移动硬盘换盘符之后，AE 打开工程满屏红条（素材离线）。
手动一条条 `替换素材` 是几十次的右键 + 找文件。

拾链做的事：

1. **先复制一份再改**（默认勾选），原工程一个字节不动；复制失败会三级退避；
2. 让 AE 打开那份副本，**按文件名四层匹配**：全名 → 基名 → 全盘索引 → 同名猜测；
3. 匹配不上但你又明确知道用哪个文件的，写进**手工映射表**，指一次就记住；
4. 跑完给结论：`接上 N 条 / 还缺 M 条`，报告落在工程的 `.ae_doctor\` 里，过程日志在本程序 `logs\`；
5. 出问题能一键导出**诊断包**（系统信息 / AE 安装签名状态 / 崩溃事件 / 最近三份日志）。

## 和"网上那种 jsx 脚本"的区别（这几条是实测出来的，不是设计口号）

| 点 | 拾链 | 常见做法 |
|---|---|---|
| 原工程 | 默认复制副本，三级退避 | 直接在原文件上改 |
| 启动 AE | Startup 注入，**跑完 AE 不消失** | `-r` 跑脚本，脚本结束 AE 自己关 |
| 挡路对话框 | 自动按掉「崩溃修复选项」「解析字体」「打开项目」，以及打开工程时的缺素材提示 | 卡住等人点，或者干脆不处理 |
| 卡死 | 认出 UI 线程无响应，25 秒报警、75 秒判失败并说明原因 | 一路等 4 分钟超时 |
| 证据 | 日志逐行 flush + 诊断包 | 只有一个 log 文本 |

## 怎么跑（开发者）

```bat
python main.py                                   :: 开界面
python main.py --ver                             :: 打印版本
python main.py --cli "D:\ae\again.aep"           :: 无界面跑一次
python tools\selftest.py                         :: 32 项自检（不启动 AE）
python tools\check_imports.py                    :: 依赖方向 + 开关白名单
python packaging\build.ps1                       :: 打 onedir 包到 dist\拾链\
```

要求：Python 3.12（本机实测用 uv 的 3.12.15；系统 3.13 没带 tkinter 的机器会开不了界面）。

## 目录

```
relinker/
├─ main.py               入口（GUI / --cli 分派）
├─ aemd/                 业务层
│  ├─ const.py           所有常量（分段超时、开关白名单、对话框标题…）
│  ├─ cfg.py             配置读写（原子写、不洗用户配置）
│  ├─ paths.py           落点与目录策略（JSX 必须纯 ASCII）
│  ├─ scanner.py         素材预扫描 + 索引
│  ├─ jsx.py             JSX 模板 / 生成 / 三道自检（与旧版逐字节对拍）
│  ├─ mapping.py         手工映射表（认不出的素材，人指一次）
│  ├─ reports.py         报告解析与那句话
│  ├─ aehost.py          启动 AE：Startup 注入 / -r 回退 / 对话框 / 卡死判定
│  ├─ runner.py          一条时间线：准备 → 启动 → 轮询 → 结算
│  ├─ winproc.py         Win32：窗口、模态框、按键、进程
│  ├─ everything.py      Everything 便携版（全盘补搜）
│  ├─ diag.py            诊断包
│  └─ ui/                界面（window / widgets / theme / appicon）
├─ tests/                对拍、打桩、界面探针
├─ tools/                selftest / check_imports / 截图与对拍工具
└─ packaging/            说明.txt 与 build.ps1
```

## 已知问题（如实写）

- **AE 打开工程时可能硬卡死**（`IsHungAppWindow=1`）——候选是插件冲突（Optical Flares / Sapphire）、字体解析、掉线的网络盘；目前只做到"卡住能报警、能早失败"，**根因未定性**。要定性得做插件二分或 PageHeap。
- **不支持 PSD / AI 的分层重连**：`replace()` 会把分层导入塌成单层。遇到分层素材请手动 `替换素材` 并勾 `Composition - Cropped Layers`。
- **只支持 Windows**；图像序列目前按单文件名匹配，不保证对。
- 素材不在本机（原盘没插）救不回来 —— 这不是工具缺陷，日志会写清"指向的文件不存在"。

## 许可

沿用仓库根目录 `LICENSE`。
