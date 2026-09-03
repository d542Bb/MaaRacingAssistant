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

# 手柄点击容差比例：落点在目标框中心 70% 区域内即可按 A（ROI 本对标整个可交互
# 区域，无需微调到中心像素级精确；防太宽松取 70% 而非全框）。
GAMEPAD_BOX_TOL_RATIO = 0.35


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
        self._shoo_cooldown_until_ts = 0.0  # 光标避让冷却截止（monotonic 秒）

    def bind_gamepad(self, capture, gpad, model_path=None, confirm_button=None):
        """绑定后台(手柄)点击所需的能力：截图帧源 + 手柄 + 速度模型 + 确认按钮。

        由模块在需要手柄方式时按需注入（real 前台鼠标全程不触碰手柄能力，
        不创建虚拟手柄设备）；解除对自建手柄/截图的依赖。
        """
        from maaracing_assistant.core.gamepad_cursor import GamepadClicker
        self._gamepad_nav_cfg = (capture, gpad, model_path)
        self._gamepad = GamepadClicker(capture, gpad, model_path=model_path)
        if confirm_button is not None:
            self._gamepad.set_confirm_button(confirm_button)

    @property
    def gamepad_bound(self) -> bool:
        """后台(手柄)导航器是否已绑定（前台(鼠标)方式无需绑定，恒为 False）。"""
        return self._gamepad is not None

    def move_only(self, cx: float, cy: float, *,
                  on_progress=None, should_abort=None, box=None) -> bool:
        """只把光标/手柄光标移动到目标位置，不执行点击（避让、悬停等场景）。

        通用原语：gamepad 模式导航到位不按 A；real 模式移鼠标光标不点击。
        临时置位 intent 语义并保证恢复，调用方无需关心模式差异。
        """
        prev = self.intent
        self.intent = True
        try:
            return self.click(cx, cy, on_progress=on_progress,
                              should_abort=should_abort, box=box)
        finally:
            self.intent = prev

    # ---------- 光标驻留看守（避让）----------

    SHOO_COOLDOWN_S = 4.0  # 避让冷却（monotonic）：含避让导航耗时 + 稳定观察窗
    # 避让点候选方向（归一化偏移）：下→上→右→左→远上，取第一个不压区域者
    SHOO_DIRECTIONS = ((0.0, 0.14), (0.0, -0.14), (0.13, 0.0), (-0.13, 0.0), (0.0, -0.30))

    def auto_shoo(self, guard_rects, *, radius_px: float,
                  frame_size: tuple[int, int],
                  next_center: tuple[float, float] | None = None,
                  on_progress=None, should_abort=None) -> dict | None:
        """光标驻留看守：光标压住宿主指定的「需保持可识别」区域时，自动导航到
        邻近空白处（不点击）。由宿主主循环**每帧**驱动——阶段状态每帧更新后，
        激活区域集合自然跟随新阶段；失败后冷却过期自动重试。

        guard_rects：[(key, (x1,y1,x2,y2))] 归一化区域（宿主的领域知识，带 key
          供触发诊断）。
        radius_px：光标遮挡等效半径（圆盘+环+hover 高亮，帧像素）。
        next_center：宿主下一个点击意图的中心（归一化，可选）——它在遮挡区外时
          跳过避让（下一次点击导航会自然把光标带离），省一次专门导航；它在遮挡
          区内（或无意图/等待态）才避让——光标已在目标上时按 A 即可，挪走反而
          多此一举，也避免"点锚点按钮后自己挡自己"的震荡。

        返回 {"key": 命中区域, "point": (nx, ny) 避让点}；未触发返回 None。
        防抖/互斥设计：
          • 冷却（monotonic 秒）：避让后 SHOO_COOLDOWN_S 内不再触发（含导航耗时）
          • 避让走 move_only（intent 临时置位保证恢复），不产生点击
          • 光标位置取手柄导航器最近一次成功识别位；导航器未绑定/位置未知不触发
        """
        if self.mode != "gamepad" or self._gamepad is None:
            return None
        now = time.monotonic()
        if now < self._shoo_cooldown_until_ts:
            return None
        pos = self._gamepad.last_pos
        W, H = frame_size
        if not pos or W <= 0 or H <= 0 or not guard_rects:
            return None
        rx, ry = radius_px / W, radius_px / H
        nx, ny = pos[0] / W, pos[1] / H

        def _hit(x: float, y: float) -> str | None:
            for key, (x1, y1, x2, y2) in guard_rects:
                if x1 - rx <= x <= x2 + rx and y1 - ry <= y <= y2 + ry:
                    return key
            return None

        hit_key = _hit(nx, ny)
        if hit_key is None:
            return None  # 光标没压任何需识别区域
        if next_center is not None:
            ncx, ncy = float(next_center[0]), float(next_center[1])
            if _hit(ncx, ncy) is None:
                return None  # 下一个意图目标在干净区：点击导航自然带离，无需避让
        for dxn, dyn in self.SHOO_DIRECTIONS:
            px_ = min(0.97, max(0.03, nx + dxn))
            py_ = min(0.95, max(0.05, ny + dyn))
            if _hit(px_, py_) is None:
                self._shoo_cooldown_until_ts = now + self.SHOO_COOLDOWN_S
                self.move_only(px_, py_, on_progress=on_progress,
                               should_abort=should_abort)
                return {"key": hit_key, "point": (px_, py_)}
        return None  # 邻域全是需识别区（少见），放弃避让保持现状

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
              down_up_gap_ms: int = 30, move_pause_s: float = 0.4,
              on_progress=None, should_abort=None, box=None) -> bool:
        """按当前方式执行一次点击：归一化坐标 (cx, cy)（0~1，以客户区物理尺寸为锚）。

        on_progress：仅 gamepad 模式生效——手柄导航进度回调（approach 每 tick 上报
        光标实时位置），供调用方在同步阻塞的导航期间刷新 PEEP。
        should_abort：仅 gamepad 模式生效——停止信号检查（每 tick 调用），置位时
        立即中止导航（摇杆归中），模块「停止」即时生效。
        box：仅 gamepad 模式生效——目标框归一化宽高 (rw, rh)。提供时按 A 容差放宽
        为「框中心 70% 区域」（min(框宽,框高)×35% 像素半径）；缺省微调到中心。
        real 模式忽略后三者（SetCursorPos 精确到中心点）。
        返回是否执行成功：
        - real：SetCursorPos + SendInput 左键；intent 置位时只移光标不点击。
        - gamepad：手柄导航到目标像素 + 按 A 确认；intent 置位时只导航不确认。
        失败时返回 False（调用方保持指纹不推进，下帧重试）。
        """
        if self.mode == "gamepad":
            return self._click_gamepad(cx, cy, on_progress, should_abort, box)
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

    def _click_gamepad(self, cx, cy, on_progress=None, should_abort=None, box=None) -> bool:
        """后台(手柄)：手柄导航到目标像素 + 按 A 确认；意图模式只导航。

        should_abort 置位中止导航 → 返回 False（与执行失败同路径：指纹不推进，
        调用方主循环随后因停止信号退出，不再重试）。
        box 提供时换算像素容差半径 = min(框宽,框高)×35%（框中心 70% 区域）。
        """
        if self._gamepad is None:
            return False  # 未绑定手柄能力（缺 gamepad capability）
        # 归一化客户区坐标 → 客户区像素（导航器在 gamepad 画面内闭环）
        size = window_client_size(self.hwnd)
        if size is None or size[0] <= 0 or size[1] <= 0:
            return False
        cw, ch = size
        px = min(cw - 1, max(0, round(cx * cw)))
        py = min(ch - 1, max(0, round(cy * ch)))
        tol_px = None
        if box:
            try:
                bw, bh = float(box[0]), float(box[1])
                if bw > 0 and bh > 0:
                    tol_px = GAMEPAD_BOX_TOL_RATIO * min(bw * cw, bh * ch)
            except Exception:  # noqa: BLE001 —— 容差换算失败回退默认中心微调
                tol_px = None
        res = self._gamepad.approach((px, py), intent=self.intent,
                                     on_progress=on_progress,
                                     should_abort=should_abort, tol_px=tol_px)
        if not res.get("ok", False):
            return False
        self._last_pos = norm_to_screen(self.hwnd, cx, cy, cw, ch) or None
        return True