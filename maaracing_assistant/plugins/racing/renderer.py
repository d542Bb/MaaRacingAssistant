#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
极速狂飙调试渲染器（阶段一：委托 DebugManager 内置默认视图）。

阶段一目标：通过 DebugManager 的 renderer token 机制接入渲染管线，
render_full/render_peep 直接委托 DebugManager 的 _render_full/_render_peep，
行为与重构前完全一致、零重复。

阶段二：将绘制逻辑（_draw_* 系列 + 组合渲染）从 DebugManager 迁入此类，
DebugManager 只保留基础设施（PEEP 线程/frame buffer/文件存盘）。
"""

from __future__ import annotations


class RacingDebugRenderer:
    """极速狂飙调试渲染器：委托 DebugManager 内置默认视图"""

    def __init__(self, debug):
        """构造：接收 DebugManager 引用，供访问 _render_full/_render_peep 等"""
        self._d = debug

    def render_full(self, frame_bgr, state):
        """全量绘制（存盘用），复用 DebugManager 内置默认视图"""
        return self._d._render_full(frame_bgr, **state.to_kwargs())

    def render_peep(self, frame_bgr, state):
        """精简绘制（PEEP 实时预览用），复用 DebugManager 内置默认视图"""
        return self._d._render_peep(frame_bgr, **state.to_kwargs())
