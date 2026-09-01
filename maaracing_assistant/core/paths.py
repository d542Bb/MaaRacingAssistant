#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户数据目录解析（%APPDATA%/MaaRacingAssistant，与程序安装目录解耦）。

更新程序/覆盖安装不影响历史数据；APPDATA 缺失时回退到包根旁 data/（源码运行场景）。
"""

from __future__ import annotations

import os
from pathlib import Path


def user_data_dir() -> Path:
    """用户数据根目录：%APPDATA%/MaaRacingAssistant（无 APPDATA 时回退包根旁 data/）。"""
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / "MaaRacingAssistant"
    return Path(__file__).resolve().parent.parent.parent / "data"
