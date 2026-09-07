# -*- coding: utf-8 -*-
"""<插件名> 插件包。

插件自包含契约：代码、模板、pipeline、配置全部位于本目录内，
core/registry 按 manifest.py 自动发现；整个目录拷走即卸载、放入即安装。
"""

from pathlib import Path

# 插件根目录（plugins/<id>/）
PLUGIN_DIR = Path(__file__).resolve().parent
# 插件专属资源根（image/ + pipeline/ + config/ 均在其中）
RES_DIR = PLUGIN_DIR / "resources"
