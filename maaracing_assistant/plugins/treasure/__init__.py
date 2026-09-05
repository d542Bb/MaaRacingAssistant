# -*- coding: utf-8 -*-
"""巅峰鉴宝插件包。

插件自包含契约：代码、模板图、ROI 配置全部位于本目录 resources/ 内，
registry 按 manifest.py 自动发现；整个目录拷走即卸载、放入即安装。
"""

from pathlib import Path

# 插件根目录（plugins/treasure/）
PLUGIN_DIR = Path(__file__).resolve().parent
# 资源根与分类目录（模板图 image/、ROI 配置 config/）
RES_DIR = PLUGIN_DIR / "resources"
IMAGE_DIR = RES_DIR / "image"
CONFIG_DIR = RES_DIR / "config"
