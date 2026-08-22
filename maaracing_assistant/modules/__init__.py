# -*- coding: utf-8 -*-
"""活动模块化框架：将导航、对局等活动拆分为可独立注册与编排的模块。"""

from maaracing_assistant.core.registry import MODULE_REGISTRY
from maaracing_assistant.modules.racing_module import RacingModule
from maaracing_assistant.modules.treasure_module import TreasureModule

# 内置模块注册（导入即注册，供 create_module / get_module_info 使用）
MODULE_REGISTRY[RacingModule.ID] = RacingModule
MODULE_REGISTRY[TreasureModule.ID] = TreasureModule
