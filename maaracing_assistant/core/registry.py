# -*- coding: utf-8 -*-
"""活动模块注册表：自动扫描 plugins/*/manifest.py 发现插件，并提供查询与创建。

- 插件 = plugins/ 下每个含 manifest.py 的子目录；
- manifest 契约：ID（唯一标识）+ MODULE_CLASS（定位模块类）；
  NAME / STAGE_ORDER / REQUIRES / REQUIRES_GAMEPAD_EXCLUSIVE 从模块类读取（单一来源）；
- 单插件加载失败（如缺依赖）仅记 WARNING 跳过，不阻断整体；
- 剥离 = 删目录，安装 = 丢入自包含目录，GUI 列表自动随之变化。
"""

from __future__ import annotations

import importlib
from pathlib import Path

from maaracing_assistant.core.base import ActivityContext, ActivityModule
from maaracing_assistant.core.logger import logger

MODULE_REGISTRY: dict[str, type[ActivityModule]] = {}

# 插件根目录：maaracing_assistant/plugins
_PLUGINS_ROOT = Path(__file__).resolve().parent.parent / "plugins"


def _discover_plugins() -> None:
    """扫描 plugins/*/manifest.py 并注册模块类（幂等、容错）。"""
    if not _PLUGINS_ROOT.is_dir():
        return
    for entry in sorted(_PLUGINS_ROOT.iterdir()):
        if not entry.is_dir() or not (entry / "manifest.py").exists():
            continue
        pkg = f"maaracing_assistant.plugins.{entry.name}"
        try:
            manifest = importlib.import_module(f"{pkg}.manifest")
            mod_id = getattr(manifest, "ID", None)
            cls_ref = getattr(manifest, "MODULE_CLASS", None)
            if not mod_id or not cls_ref or mod_id in MODULE_REGISTRY:
                continue
            path, _, attr = cls_ref.rpartition(".")
            mod = importlib.import_module(f"{pkg}.{path}" if path else pkg)
            cls = getattr(mod, attr)
            if not (isinstance(cls, type) and issubclass(cls, ActivityModule)):
                logger.log(f"[registry] 插件 {entry.name}: {cls_ref} 非 ActivityModule 子类，跳过", "WARNING")
                continue
            MODULE_REGISTRY[mod_id] = cls
            logger.log(f"[registry] 已注册插件模块: {mod_id}", "DEBUG")
        except Exception as exc:  # noqa: BLE001 —— 缺依赖等按插件隔离，不影响其它插件
            logger.log(f"[registry] 插件 {entry.name} 加载失败，跳过: {exc!r}", "WARNING")


_discover_plugins()


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
