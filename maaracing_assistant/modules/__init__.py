# -*- coding: utf-8 -*-
"""活动模块化框架（过渡态）：racing 已插件化（maaracing_assistant.plugins.racing，
由 core/registry 自动扫描注册）；treasure 尚未迁移，暂保留手动注册（P3 完成后移除）。"""

from maaracing_assistant.core.registry import MODULE_REGISTRY
from maaracing_assistant.modules.treasure_module import TreasureModule

# treasure 未插件化前的临时手动注册（P3 迁移到 plugins/treasure 后删除本文件注册逻辑）
MODULE_REGISTRY[TreasureModule.ID] = TreasureModule
