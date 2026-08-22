# -*- coding: utf-8 -*-
"""
活动模块注册表：注册、查询与创建活动模块
"""

from __future__ import annotations

from maaracing_assistant.core.base import ActivityContext, ActivityModule

MODULE_REGISTRY: dict[str, type[ActivityModule]] = {}


def get_module_info(module_id: str) -> dict:
    """获取模块元信息，模块不存在时抛出 KeyError(module_id)"""
    cls = MODULE_REGISTRY[module_id]
    return {
        "id": module_id,
        "name": cls.NAME,
        "stages": cls.STAGE_ORDER,
        "requires": sorted(cls.REQUIRES),
        "requires_gamepad_exclusive": cls.REQUIRES_GAMEPAD_EXCLUSIVE,
    }


def create_module(module_id: str, ctx: ActivityContext) -> ActivityModule:
    """创建模块实例，模块不存在时抛出 KeyError(module_id)"""
    return MODULE_REGISTRY[module_id](ctx)
