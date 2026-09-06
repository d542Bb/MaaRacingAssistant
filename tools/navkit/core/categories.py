#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ROI 类别集合（NavKit Core · 与内容无关）。

调试台以「类别」组织可校准的 ROI：每类一个段（如 treasure 的 stage/actions/ocr/
appraisers/eggs）。本模块交付「哪些类别、校验、缺省填充、顶层元数据」的通用骨架；
**不**含任何具体类别的语义（哪个 key 代表什么阶段/按钮由 adapter 声明）。

三个能力（迁移自 NavKit 控制台/server.py）：
- 顶层元数据(`_schema_ver`/`reference_size`)与 `_` 前缀元数据键的统一处理。
- 类目段校验：每段必须为 object；`rect` 须为 4 个 [0,1] 数字；`templates` 为合法
  模板名数组；可选 `threshold` 为 [0,1] 数字。
- 缺省填充（对缺失段补空或 adapter 提供的缺省条目，幂等不覆盖）。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from maaracing_assistant.core.render_plan import RenderPlan
from tools.navkit.core.session import TPL_RE


class CategoriesError(ValueError):
    """类目配置结构校验失败。"""


class CategoryDefs:
    """描述一个模块的 ROI 类别集合及其缺省归属。

    Parameters:
        name:      模块标识（如 "treasure"），用于日志/路径区分布局。
        categories:可校准的分类名（如 ("stage","actions","ocr")）。顺序有意义（前端展示）。
        default_items: 缺省填充的 {cat: {key: item}}，仅当段内缺该 key 时补入（幂等）。
    """

    def __init__(
        self,
        categories: tuple[str, ...],
        *,
        name: str = "",
        default_items: dict[str, dict[str, dict[str, Any]]] | None = None,
    ):
        if not categories:
            raise CategoriesError("categories 不能为空")
        self.categories = tuple(categories)
        self.name = name or "module"
        self.default_items = default_items or {}

    def has(self, cat: str) -> bool:
        return cat in self.categories

    # ---- 校验 ----
    def validate(self, data: dict) -> None:
        """对已加载的 ROI dict 做结构校验；不合法抛 CategoriesError。"""
        if not isinstance(data, dict):
            raise CategoriesError("ROI 配置必须是 JSON 对象")
        if not isinstance(data.get("reference_size"), (list, tuple)) or len(data.get("reference_size", [])) != 2:
            raise CategoriesError("缺少 reference_size 或格式非法（须为 [W,H]）")
        for cat in self.categories:
            seg = data.get(cat)
            if not isinstance(seg, dict):
                raise CategoriesError(f"缺少 {cat} 段")
            for key, val in seg.items():
                if key.startswith("_"):  # 段内元数据键，跳过
                    continue
                if not isinstance(val, dict):
                    raise CategoriesError(f"{cat}.{key} 必须是对象")
                rect = val.get("rect")
                if not (isinstance(rect, list) and len(rect) == 4
                        and all(isinstance(n, (int, float)) and 0.0 <= n <= 1.0 for n in rect)):
                    raise CategoriesError(f"{cat}.{key}.rect 必须是 4 个 [0,1] 数字")
                tpls = val.get("templates", [])
                if not isinstance(tpls, list):
                    raise CategoriesError(f"{cat}.{key}.templates 必须是数组")
                for t in tpls:
                    if not isinstance(t, str) or not TPL_RE.match(t):
                        raise CategoriesError(f"{cat}.{key}.templates 含非法模板名: {t!r}")
                th = val.get("threshold", None)
                if th is not None and not (
                    isinstance(th, (int, float)) and not isinstance(th, bool) and 0.0 <= th <= 1.0
                ):
                    raise CategoriesError(f"{cat}.{key}.threshold 必须是 [0,1] 数字或省略")

    # ---- 缺省填充 ----
    def fill_defaults(self, data: dict) -> dict:
        """补齐缺失的分类段与缺省条目（幂等，不覆盖已存在内容）。返回同一 dict。"""
        for cat in self.categories:
            if not isinstance(data.get(cat), dict):
                data[cat] = {}
        for cat, items in self.default_items.items():
            seg = data.setdefault(cat, {})
            if not isinstance(seg, dict):
                data[cat] = {}
                seg = data[cat]
            for key, item in items.items():
                seg.setdefault(key, item)
        data.setdefault("reference_size", [1280, 720])
        return data

    # ---- 读取/保存 ----
    def load(self, rois_file: Path) -> dict:
        """读取 ROI JSON；文件不存在时抛 CategoriesError（不静默造默认掩盖配置缺失）。"""
        path = Path(rois_file)
        if not path.is_file():
            raise CategoriesError(f"ROI 配置文件不存在: {path}")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise CategoriesError(f"读取 ROI 配置失败: {path} ({e})") from None
        if not isinstance(data, dict):
            raise CategoriesError(f"ROI 配置顶层须为 object，收到: {type(data).__name__}")
        return data

    def save_atomic(self, data: dict, rois_file: Path) -> None:
        """原子保存：先写临时文件再 os.replace；校验通过才落盘。"""
        self.validate(data)
        path = Path(rois_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass