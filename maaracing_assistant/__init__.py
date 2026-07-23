#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MaaRacingAssistant
巅峰极速 · 极速狂飙 自动刷分
MAA Framework + YOLOv8 ONNX + vgamepad
"""

# 版本号由 setuptools-scm 从 Git Tag 自动生成
# 手动修改无效！改版本请打 git tag vX.Y.Z 并推送
try:
    from ._version import version as __version__
except ImportError:
    # fallback：未安装包时（如直接运行脚本）
    __version__ = "0.0.0.dev"
