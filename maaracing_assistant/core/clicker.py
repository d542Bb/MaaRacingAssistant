#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""点击器：统一「前台(鼠标) / 后台(手柄)」两种点击方式。

重构背景（2026-09-02 用户拍板，替代原 intent/real/background 三态）：
  - 前台(鼠标) real：SetCursorPos（可见移动）+ SendInput 左键。需游戏在前台。
  - 后台(手柄) gamepad：手柄光标导航（签名剖面识别 + 闭环趋近到位）+ 按 A 键确认。
    cursor_refactor 实现已沉淀进 core.gamepad_cursor（与鼠标逻辑同层）。
  - 意图开关（仅显示意图，独立于点击方式）：
      开启时程序只「导航到目标位置」，不执行确认点击——由用户自己按下/操作。
      鼠标 → 只移光标到目标；手柄 → 只导航到位不按 A。同一开关两者共用。

用法：模块在"首部"持有 Clicker 并同步当前模式，所有点击统一走 click()；
切换只发生在设置页（sidecar set_click_mode → controller.click_mode → ctx.click_mode）。
"""
from __future__ import annotations

import time

from maaracing_assistant.core.window_utils import (
    norm_to_screen,
    send_left_click,
    set_cursor_visible,
    window_client_size,
)

# 合法点击方式（与 GUI 设置页 data-clickmode 保持一致）
CLICK_MODES = ("real", "gamepad")


class Clicker:
    """统一点击执行器：按 mode 执行点击/光标意图。

    mode：real（前台=鼠标 SendInput）/ gamepad（后台=手柄导航+A 键确认）。
    intent：意图开关（仅显示意图）。置位时只导航到目标、不确认点击，由用户自己按。
            两种 mode 共用同一 intent 语义。
    所有调用点共用同一个实例（模块持有），模式只在"首部"（构造/设置页）切换。
    """

    def __init__(self, hwnd: int = 0, mode: str = "real", intent: bool = False):
        self.hwnd = hwnd
        self.set_mode(mode)
        self.intent = intent
        self._last_pos: tuple[int, int] | None = None  # 最近一次成功执行的屏幕坐标
        self._gamepad = None  # 手柄导航器（懒创建，绑定 controller 手柄/截图）
        self._gamepad_nav_cfg = None  # (capture, gpad, model_path)

    def bind_gamepad(self, capture, gpad, model_path=None, confirm_button=None):
        """绑定后台(手柄)点击所需的能力：截图帧源 + 手柄 + 速度模型 + 确认按钮。

        由 controller 在具备 gamepad 能力时注入；解除对自建手柄/截图的依赖。
        """
        from maaracing_assistant.core.gamepad_cursor import GamepadClicker
        self._gamepad_nav_cfg = (capture, gpad, model_path)
        self._gamepad = GamepadClicker(capture, gpad, model_path=model_path)
        if confirm_button is not None:
            self._gamepad.set_confirm_button(confirm_button)

    @property
    def last_pos(self) -> tuple[int, int] | None:
        """最近一次成功执行的屏幕坐标（供调用方日志/事件记录）。"""
        return self._last_pos

    # ---------- 模式 ----------

    def set_mode(self, mode: str) -> None:
        """切换点击方式（real / gamepad）。非法值直接报错，防止静默错点。"""
        if mode not in CLICK_MODES:
            raise ValueError(f"非法点击方式: {mode!r}，可选 {CLICK_MODES}")
        self.mode = mode

    def set_intent(self, intent: bool) -> None:
        """设置意图开关（仅显示意图）：置位后只导航不确认。"""
        self.intent = bool(intent)

    @property
    def need_foreground(self) -> bool:
        """前台(鼠标)点击需要窗口在前台；后台(手柄)点击不需要（导航在游戏画面内）。"""
        return self.mode == "real"

    # ---------- 执行 ----------

    def click(self, cx: float, cy: float, *,
              down_up_gap_ms: int = 30, move_pause_s: float = 0.4) -> bool:
        """按当前方式执行一次点击：归一化坐标 (cx, cy)（0~1，以客户区物理尺寸为锚）。

        返回是否执行成功：
        - real：SetCursorPos + SendInput 左键；intent 置位时只移光标不点击。
        - gamepad：手柄导航到目标像素 + 按 A 确认；intent 置位时只导航不确认。
        失败时返回 False（调用方保持指纹不推进，下帧重试）。
        """
        if self.mode == "gamepad":
            return self._click_gamepad(cx, cy)
        return self._click_real(cx, cy, down_up_gap_ms, move_pause_s)

    def _click_real(self, cx, cy, down_up_gap_ms, move_pause_s) -> bool:
        """前台(鼠标)：SetCursorPos + SendInput；意图模式只移光标。"""
        if not self.hwnd:
            return False
        size = window_client_size(self.hwnd)
        if size is None or size[0] <= 0 or size[1] <= 0:
            return False
        cw, ch = size
        pos = norm_to_screen(self.hwnd, cx, cy, cw, ch)
        if pos is None:
            return False
        sx, sy = pos
        if not set_cursor_visible(sx, sy):
            return False
        self._last_pos = (sx, sy)
        if self.intent:
            return True  # 意图模式：光标已就位，不点击（由用户自己按）
        time.sleep(move_pause_s)
        if not send_left_click(down_up_gap_ms):
            return False
        return True

    def _click_gamepad(self, cx, cy) -> bool:
        """后台(手柄)：手柄导航到目标像素 + 按 A 确认；意图模式只导航。"""
        if self._gamepad is None:
            return False  # 未绑定手柄能力（缺 gamepad capability）
        # 归一化客户区坐标 → 客户区像素（导航器在 gamepad 画面内闭环）
        size = window_client_size(self.hwnd)
        if size is None or size[0] <= 0 or size[1] <= 0:
            return False
        cw, ch = size
        px = min(cw - 1, max(0, round(cx * cw)))
        py = min(ch - 1, max(0, round(cy * ch)))
        res = self._gamepad.approach((px, py), intent=self.intent)
        if not res.get("ok", False):
            return False
        self._last_pos = norm_to_screen(self.hwnd, cx, cy, cw, ch) or None
        return True