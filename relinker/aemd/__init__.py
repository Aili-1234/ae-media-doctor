# -*- coding: utf-8 -*-
"""拾链 Relinker 1.0.0 —— 包根。

这里是 APP_NAME / APP_VER 的**唯一来源**。其它模块不许自己写版本字符串，
也不许从 settings.json 里读版本（那是数据，不是程序身份）。

依赖：无（本模块只放常量，import 它不会拉起任何别的东西）。
"""

APP_NAME = "拾链"
APP_VER = "1.1.11"
APP_EN_NAME = "Relinker"        # 英文副名：只出现在说明.txt 与 About 里