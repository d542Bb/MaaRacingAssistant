# -*- coding: utf-8 -*-
"""pytest 全局配置。

为 `bid_strategy` 提供可导入路径：策略模块只依赖标准库，直接以「模块目录」导入，
**不经过** maaracing_assistant 包的 __init__（会触发 registry → racing/treasure 模块，
拉入 raa/vgamepad/opencv 等重依赖，拖慢单测并污染 CI）。
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent

# 把 modules 目录加入 sys.path，使 `from bid_strategy import ...` 生效
_MODULES = _PROJ / "maaracing_assistant" / "modules"
if str(_MODULES) not in sys.path:
    sys.path.insert(0, str(_MODULES))