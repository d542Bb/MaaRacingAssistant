#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""点击器：统一三种点击方式的执行（意图显示 / 真实点击 / 后台点击）。

背景（2026-08-31 实测结论，见 tools/test_real_click.py 调研）：
- 真实点击：SetCursorPos（可见移动）+ SendInput 左键。项目原有链路，需游戏在前台；
- 后台点击：SetCursorPos（真实光标就位）+ PostMessage 投递鼠标消息。
  实测《巅峰极速》校验 GetCursorPos 光标位置、不校验输入来源——光标真实就位后，
  PostMessage 即可在游戏处于后台/被遮挡时点击成功（"穿过其他窗口点击"）；
- 意图显示：只把光标移到目标位置（展示"程序想点哪"），不点击，供人工确认/手动操作。

用法：模块在"首部"持有 Clicker 并同步当前模式，所有点击统一走 click()；
切换模式只发生在设置页（sidecar set_click_mode → controller.click_mode → ctx.click_mode）。
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from maaracing_assistant.core.window_utils import (
    norm_to_screen,
    send_left_click,
    set_cursor_visible,
    window_client_size,
)

# ---- Win32 消息常量（后台点击） ----
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001

# 合法点击模式（与 GUI 设置页 data-clickmode 保持一致）
CLICK_MODES = ("intent", "real", "background")

_ud = ctypes.WinDLL("user32", use_last_error=True)
_ud.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_ud.PostMessageW.restype = wintypes.BOOL


class Clicker:
    """统一点击执行器：按 mode 执行点击/光标意图。

    所有调用点共用同一个实例（模块持有），模式只在"首部"（构造/设置页）切换。
    """

    def __init__(self, hwnd: int = 0, mode: str = "real",
                 background_hold_s: float = 0.15):
        self.hwnd = hwnd
        self.set_mode(mode)
        # 后台点击的「按下保持」时长：PostMessage 是异步投递，DOWN→UP 间隔太短
        # 游戏 UI 可能来不及完成点击判定/跳转（实测 50ms 会"点不动"，150ms 稳妥）。
        self.background_hold_s = max(0.05, background_hold_s)
        self._last_pos: tuple[int, int] | None = None  # 最近一次成功执行的屏幕坐标

    @property
    def last_pos(self) -> tuple[int, int] | None:
        """最近一次成功执行的屏幕坐标（供调用方日志/事件记录）。"""
        return self._last_pos

    # ---------- 模式 ----------

    def set_mode(self, mode: str) -> None:
        """切换点击模式（intent / real / background）。非法值直接报错，防止静默错点。"""
        if mode not in CLICK_MODES:
            raise ValueError(f"非法点击模式: {mode!r}，可选 {CLICK_MODES}")
        self.mode = mode

    @property
    def need_foreground(self) -> bool:
        """真实点击需要窗口在前台；后台点击 / 意图显示不需要（SetCursorPos 全局有效）。"""
        return self.mode == "real"

    # ---------- 执行 ----------

    def click(self, cx: float, cy: float, *,
              down_up_gap_ms: int = 30, move_pause_s: float = 0.4) -> bool:
        """按当前模式执行一次点击：归一化坐标 (cx, cy)（0~1，以客户区物理尺寸为锚）。

        返回是否执行成功：
        - intent：光标已移到目标（视为成功，指纹正常推进，用户可照着光标手动点击）；
        - real：SetCursorPos + SendInput 左键按下/抬起；
        - background：SetCursorPos（真实光标就位，过游戏 GetCursorPos 校验）+
          PostMessage 投递 MOUSEMOVE/LBUTTONDOWN/LBUTTONUP（客户区坐标）。
        失败时返回 False（调用方保持指纹不推进，下帧重试）。
        """
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
        if self.mode == "intent":
            return True  # 意图显示：光标已就位，不点击
        time.sleep(move_pause_s)  # 可见停顿（让用户看清 / 光标落位稳定）
        if self.mode == "real":
            return send_left_click(down_up_gap_ms)
        # background：真实光标已就位 → PostMessage 投递（客户区坐标，与截图帧同锚）。
        # 按下→抬起间隔用 background_hold_s（默认 150ms），模拟真实按下的保持时长；
        # 异步投递下太短的 DOWN→UP 会让游戏 UI 判定"点击未完成"（点不动，实测坑）。
        px = min(cw - 1, max(0, round(cx * cw)))
        py = min(ch - 1, max(0, round(cy * ch)))
        lparam = (py << 16) | (px & 0xFFFF)
        ok = _ud.PostMessageW(self.hwnd, WM_MOUSEMOVE, 0, lparam)
        time.sleep(0.05)
        ok = _ud.PostMessageW(self.hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam) and ok
        time.sleep(self.background_hold_s)
        ok = _ud.PostMessageW(self.hwnd, WM_LBUTTONUP, 0, lparam) and ok
        return bool(ok)
