# -*- coding: utf-8 -*-
"""<插件名> 插件清单：供 core/registry 自动扫描发现与注册。

契约：ID 唯一标识；MODULE_CLASS 定位模块类（plugins/<id>/<path>.py 的 <attr> 类）。
NAME / STAGE_ORDER / REQUIRES / REQUIRES_GAMEPAD_EXCLUSIVE / REQUIRED_ASSETS
从模块类读取（单一来源），不在 manifest 重复声明。
"""

ID = "sample"
MODULE_CLASS = "module.SampleModule"
