#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""鉴宝 DebugStudio adapter（统一计划 P3，首次认领）。

adapter 的职责：声明鉴宝模块「有哪些可校准类别 + 缺省归属 + 路径布局」，并复用
core 的 session/categories/reader/renderer 完成浏览与匹配。generic studio 不在此处
理解 OCR/出价内容——那属于 treasure 领域，本文件只把「类目集合」交给 core。

结构落点（复制自 treasure_debug_studio，但不改动原 tools/treasure_debug_studio，
保持向后兼容）：
    - 类别：stage / actions / ocr / appraisers / eggs
    - ROI 文件：plugins/treasure/resources/treasure_rois.json
    - 截图根：debug/treasure/（会话目录）
"""
from __future__ import annotations

from pathlib import Path

from tools.debug_studio.core.categories import CategoryDefs
from tools.debug_studio.core.session import SessionBrowser

PROJ = Path(__file__).resolve().parent.parent.parent.parent

CATEGORIES: tuple[str, ...] = ("stage", "actions", "ocr", "appraisers", "eggs")

# v2 缺省归属（与 treasure_debug_studio/server.py 的 DEFAULT_* 保持一致，幂等补填）。
DEFAULT_ACTIONS = {
    "bid_confirm_red_btn": {"rect": [0.0, 0.0, 0.0, 0.0], "templates": ["bid_confirm_red_btn.png"]},
    "confirm_red_btn": {"rect": [0.0, 0.0, 0.0, 0.0], "templates": ["confirm_red_btn.png"]},
    "settle_collect_red_btn": {"rect": [0.0, 0.0, 0.0, 0.0], "templates": ["settle_collect_red_btn.png"]},
}
DEFAULT_APPRAISERS = {
    "appraiser_p1_caroline": {"prio": 1, "rect": [0.03, 0.18, 0.97, 0.92],
                              "templates": ["appraiser_p1_caroline.png"], "threshold": 0.72},
    "appraiser_p2_shotaro": {"prio": 2, "rect": [0.03, 0.18, 0.97, 0.92],
                             "templates": ["appraiser_p2_shotaro.png"], "threshold": 0.72},
}
DEFAULT_ITEMS = {
    "actions": DEFAULT_ACTIONS,
    "appraisers": DEFAULT_APPRAISERS,
}


def make_category_defs() -> CategoryDefs:
    return CategoryDefs(CATEGORIES, name="treasure", default_items=DEFAULT_ITEMS)


def rois_path() -> Path:
    return PROJ / "maaracing_assistant" / "plugins" / "treasure" / "resources" / "treasure_rois.json"


def session_dir() -> Path:
    return PROJ / "debug" / "treasure"


def make_session_browser() -> SessionBrowser:
    return SessionBrowser(session_dir())


def template_dir() -> Path:
    return PROJ / "maaracing_assistant" / "plugins" / "treasure" / "resources"