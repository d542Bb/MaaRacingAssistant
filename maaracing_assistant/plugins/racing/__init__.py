# -*- coding: utf-8 -*-
"""极速狂飙插件包。

插件自包含契约：代码、模板、pipeline、YOLO 模型全部位于本目录内，
registry 按 manifest.py 自动发现；整个目录拷走即卸载、放入即安装。
"""

from pathlib import Path

# 插件根目录（plugins/racing/）
PLUGIN_DIR = Path(__file__).resolve().parent
# MAA Resource bundle 根（post_bundle 入口；image/ + pipeline/ + onnx/ 均在其中）
RES_DIR = PLUGIN_DIR / "resources"
# YOLO 模型（插件自带；onnx/ 目录名避开 MAA Resource 保留的 model/ 语义）
MODEL_PATH = RES_DIR / "onnx" / "model.onnx"