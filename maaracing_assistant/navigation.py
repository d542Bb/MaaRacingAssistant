#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MaaRacingAssistant — 光标导航模块

提供：
  - ButtonDef：按钮配置定义
  - Navigation：光标追踪、模板匹配、归位、按钮导航、商店弹窗处理
"""

from __future__ import annotations

import time
from typing import Optional, TYPE_CHECKING

import cv2
import numpy as np
import vgamepad as vg
from pathlib import Path

if TYPE_CHECKING:
    from maaracing_assistant.controller import MaaRacingAssistantController

from maaracing_assistant.logger import logger


# ==================== 按钮配置 ====================

class ButtonDef:
    """按钮定义——只保留配置项"""
    __slots__ = ('name', 'pct', 'page_template', 'template_should_match', 'close_threshold')

    def __init__(self, name: str, pct: tuple, page_template: str = "",
                 template_should_match: bool = True, close_threshold: int = 65):
        self.name = name
        self.pct = pct                      # 屏幕百分比位置 (x%, y%)
        self.page_template = page_template  # 验证模板图文件名
        self.template_should_match = template_should_match  # True=匹配上算成功, False=消失算成功
        self.close_threshold = close_threshold              # 按 A 的距离阈值


# ==================== 导航控制类 ====================

class Navigation:
    """光标导航：模板匹配、光标识别追踪、归位、按钮导航、商店弹窗处理"""

    def __init__(self, proj: Path, debug, ctrl: MaaRacingAssistantController):
        self.proj = proj
        self.debug = debug
        self.ctrl = ctrl  # 父控制器引用，用于 _screencap / _get_gpad / _running

        # ── 光标识别状态 ──
        self._prev_frame_positions: set[tuple] = set()
        self._stationary_blacklist: dict[tuple, int] = {}
        self._last_candidates: list[dict] = []
        self._last_all_candidates: list[dict] = []
        self._last_stick = (0, 0)
        self._last_cursor_score = 0.0
        self._nav_close_threshold: int | None = None

    # ── 父控制器代理 ──

    @property
    def _running(self) -> bool:
        return self.ctrl._running

    def _get_gpad(self) -> vg.VX360Gamepad:
        return self.ctrl._get_gpad()

    def _screencap(self):
        return self.ctrl._screencap()

    # ── 工具方法 ──

    @staticmethod
    def _dist(p1: tuple, p2: tuple) -> float:
        return ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5

    def _interruptible_sleep(self, seconds: float):
        for _ in range(int(seconds / 0.1)):
            if not self._running:
                return
            time.sleep(0.1)

    def _stop_stick(self, gpad: vg.VX360Gamepad):
        gpad.left_joystick(x_value=0, y_value=0)
        gpad.update()
        self._last_stick = (0, 0)

    def _press_button(self, gpad: vg.VX360Gamepad, button, duration: float = 0.3):
        gpad.press_button(button)
        gpad.update()
        time.sleep(duration)
        gpad.release_button(button)
        gpad.update()

    # ---------- 模板匹配 ----------

    def _load_template(self, name: str) -> Optional[np.ndarray]:
        """加载模板图片（优先 png，其次 jpg），返回 RGB ndarray"""
        img_dir = self.proj / "assets" / "resource" / "image"
        for ext in (".png", ".jpg"):
            path = img_dir / f"{name}{ext}"
            if path.exists():
                img = cv2.imread(str(path), cv2.IMREAD_COLOR)
                if img is not None:
                    logger.log(f"模板已加载: {path.name} ({img.shape[1]}x{img.shape[0]})", "DEBUG")
                    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        logger.log(f"模板不存在: {name}.png/.jpg", "WARNING")
        return None

    def _find_template(self, img: np.ndarray, template: np.ndarray, threshold: float = 0.7,
                       scales=None, roi=None, use_gray: bool = False) -> tuple:
        """
        多尺度模板匹配，返回 (位置, 置信度, 缩放比例)
        位置格式: (x, y)，未找到返回 (None, best_val, best_scale)
        """
        if scales is None:
            scales = [0.8, 0.9, 1.0, 1.1, 1.2]

        search_img = img
        offset_x, offset_y = 0, 0
        if roi is not None:
            rx, ry, rw, rh = roi
            search_img = img[ry:ry + rh, rx:rx + rw]
            offset_x, offset_y = rx, ry
            logger.log(f"ROI搜索: ({rx},{ry},{rw}x{rh}), 全图={img.shape[1]}x{img.shape[0]}", "DEBUG")

        if use_gray:
            if search_img.ndim == 3:
                gray = cv2.cvtColor(search_img, cv2.COLOR_RGB2GRAY)
                search_img = cv2.equalizeHist(gray)
            tpl_gray = cv2.cvtColor(template, cv2.COLOR_RGB2GRAY)
            tpl = cv2.equalizeHist(tpl_gray)
            logger.log("灰度匹配已启用", "DEBUG")
        else:
            tpl = template

        best_val = 0.0
        best_loc = None
        best_scale = 1.0

        for scale in scales:
            resized = cv2.resize(tpl, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
            if resized.shape[0] > search_img.shape[0] or resized.shape[1] > search_img.shape[1]:
                continue
            result = cv2.matchTemplate(search_img, resized, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            if max_val > best_val:
                best_val = max_val
                best_loc = max_loc
                best_scale = scale

        if best_val >= threshold and best_loc is not None:
            w, h = template.shape[1], template.shape[0]
            cx = int(best_loc[0] + w * best_scale / 2) + offset_x
            cy = int(best_loc[1] + h * best_scale / 2) + offset_y
            logger.log(f"找到模板: 位置({cx},{cy}), 置信度={best_val:.3f}, scale={best_scale:.2f}", "DEBUG")
            return (cx, cy), best_val, best_scale
        logger.log(f"未找到模板: 最高置信度={best_val:.3f} < {threshold:.2f}", "DEBUG")
        return None, best_val, best_scale

    def _match_settings_page(self, img: np.ndarray, template: np.ndarray, threshold: float = 0.65) -> bool:
        """检测是否为设置页面"""
        h, w = img.shape[:2]
        roi = (0, 0, int(w * 0.5), int(h * 0.5))
        pos, conf, scale = self._find_template(
            img, template, threshold=threshold,
            scales=[0.8, 0.9, 1.0, 1.1, 1.2],
            roi=roi, use_gray=False)
        logger.log(f"设置页面匹配: 置信度={conf:.3f} > {threshold:.2f}? {pos is not None}")
        return pos is not None

    # ---------- 光标识别 ----------

    def _find_cursor_by_shape(self, img: np.ndarray, debug: bool = False, *,
                               last_known_pos: Optional[tuple] = None,
                               last_stick: Optional[tuple] = None) -> tuple:
        """
        基于几何形状识别白色圆形光标。
        返回: (位置(x,y), 圆度, 面积) 或 (None, 0, 0)
        """
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        _, binary = cv2.threshold(gray, 185, 255, cv2.THRESH_BINARY)
        hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
        _, sat_mask = cv2.threshold(hsv[:, :, 1], 30, 255, cv2.THRESH_BINARY_INV)
        binary = cv2.bitwise_and(binary, sat_mask)

        if debug:
            debug_dir = self.proj / "debug" / "diagnose"
            debug_dir.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(debug_dir / "cursor_binary.png"), binary)

        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best_cursor = None
        best_score = 0.0
        h_img, w_img = img.shape[:2]
        min_area = max(100, int(h_img * w_img * 0.00008))
        max_area = min(550, int(h_img * w_img * 0.006))

        self._last_all_candidates = []
        candidates = []

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_area or area > max_area:
                continue
            perimeter = cv2.arcLength(cnt, True)
            if perimeter < 1e-6:
                continue
            circularity = 4 * np.pi * area / (perimeter ** 2)
            x, y, w, h = cv2.boundingRect(cnt)
            aspect_ratio = min(w, h) / max(w, h) if max(w, h) > 0 else 0
            margin = max(w, h)
            near_edge = (x <= margin or y <= margin or
                         x + w >= w_img - margin or y + h >= h_img - margin)
            pos = (x + w // 2, y + h // 2)
            item = {
                "pos": pos, "area": area, "circularity": circularity,
                "aspect": aspect_ratio, "rect": (x, y, w, h), "near_edge": near_edge,
            }
            self._last_all_candidates.append(item)

            if area < 240:
                continue
            min_circ = 0.65 if near_edge else 0.82
            if circularity < min_circ or aspect_ratio < 0.70:
                continue
            candidates.append(item)

        for cand in candidates:
            circ = cand["circularity"]
            asp = cand["aspect"]
            near_edge = cand["near_edge"]
            area_score1 = 1.0 - abs(cand["area"] - 310) / 300
            area_score2 = 1.0 - abs(cand["area"] - 420) / 300
            area_score = max(area_score1, area_score2)
            area_score = max(0.0, min(1.0, area_score))
            circ_weight = 0.5 if near_edge else 0.65
            score = circ * circ_weight + asp * 0.15 + area_score * 0.20

            # 假光标静止检测
            if last_stick is not None:
                lx, ly = last_stick
                if lx != 0 or ly != 0:
                    found_in_prev = False
                    for prev_pos in self._prev_frame_positions:
                        if abs(cand["pos"][0] - prev_pos[0]) <= 5 and abs(cand["pos"][1] - prev_pos[1]) <= 5:
                            found_in_prev = True
                            break
                    if found_in_prev:
                        region_key = (round(cand["pos"][0] / 5) * 5, round(cand["pos"][1] / 5) * 5)
                        cnt = self._stationary_blacklist.get(region_key, 0) + 1
                        self._stationary_blacklist[region_key] = cnt
                        if cnt >= 3:
                            cand["blacklisted"] = True
                            continue
                        score -= cnt * 0.10
                    else:
                        region_key = (round(cand["pos"][0] / 5) * 5, round(cand["pos"][1] / 5) * 5)
                        self._stationary_blacklist.pop(region_key, None)

            # 运动一致性评分
            if last_known_pos is not None and last_stick is not None:
                lx, ly = last_stick
                if lx != 0 or ly != 0:
                    dx = cand["pos"][0] - last_known_pos[0]
                    dy = cand["pos"][1] - last_known_pos[1]
                    move_dist = (dx * dx + dy * dy) ** 0.5
                    if move_dist > 5:
                        stick_len = (lx * lx + ly * ly) ** 0.5
                        nx, ny = dx / move_dist, dy / move_dist
                        sx, sy = lx / stick_len, -ly / stick_len
                        alignment = nx * sx + ny * sy
                        score += alignment * 0.15

            if score > best_score:
                best_score = score
                best_cursor = (cand["pos"], circ, cand["area"])

        self._last_candidates = candidates
        self._prev_frame_positions = {c["pos"] for c in candidates}
        self._last_cursor_score = best_score

        # 光标丢失 → 延续拉黑
        if not best_cursor:
            for cand in candidates:
                if not cand.get("blacklisted") and last_stick is not None and last_stick != (0, 0):
                    region_key = (round(cand["pos"][0] / 5) * 5, round(cand["pos"][1] / 5) * 5)
                    if any(abs(cand["pos"][0] - prev_pos[0]) <= 5 and abs(cand["pos"][1] - prev_pos[1]) <= 5
                           for prev_pos in self._prev_frame_positions):
                        cnt = self._stationary_blacklist.get(region_key, 0) + 1
                        self._stationary_blacklist[region_key] = cnt
                        if cnt >= 3:
                            cand["blacklisted"] = True
                            logger.log(f"光标丢失但继续拉黑可疑候选: {cand['pos']} (帧{cnt})", "DEBUG")

        if best_cursor and best_score < 0.70:
            best_cursor = None
            best_score = 0.0

        if best_cursor:
            (cx, cy), circ, area = best_cursor
            logger.log(f"找到光标: ({cx},{cy}), 圆度={circ:.3f}, 面积={area:.1f}, 评分={best_score:.3f}", "DEBUG")
        else:
            if candidates:
                best = max(candidates, key=lambda c: c["circularity"])
                logger.log(f"未找到光标（无满足圆度/面积硬约束的候选）", "DEBUG")
                logger.log(f"  最佳候选: 圆度={best['circularity']:.3f}, 宽高比={best['aspect']:.3f}, 面积={best['area']:.1f}, 边缘={best['near_edge']}", "DEBUG")
            else:
                logger.log("未找到光标（无高亮轮廓）", "DEBUG")

        return best_cursor if best_cursor else (None, 0.0, 0)

    # ---------- 光标移动 ----------

    def _move_cursor_to_target(self, cursor_pos: tuple, target_pos: tuple,
                                gpad: vg.VX360Gamepad, stop_distance: int = 25,
                                w: int = 1280, h: int = 720) -> bool:
        """控制左摇杆移动光标到目标"""
        cx, cy = cursor_pos
        tx, ty = target_pos
        dx = tx - cx
        dy = ty - cy
        dist = (dx * dx + dy * dy) ** 0.5

        if dist < stop_distance:
            logger.log(f"光标已对齐: 距离={dist:.1f} < {stop_distance}", "DEBUG")
            return True

        DEADZONE = 4260
        MAX_AXIS = 8000
        min_dim = min(w, h)

        ALIGN_PX = max(12, int(min_dim * 0.025))   # 方向对齐像素阈值（~18px @ 720p）

        if abs(dy) < ALIGN_PX:
            ux = 1.0 if dx > 0 else -1.0
            uy = 0.0
        elif abs(dx) < 30:
            ux = 0.0
            uy = -1.0 if dy > 0 else 1.0
        else:
            ux = dx / dist
            uy = -dy / dist

        # 距离阈值自适应（基于屏幕尺寸百分比）
        FAR = int(min_dim * 0.20)        # > 20% = 远
        MID = int(min_dim * 0.10)        # > 10% = 中
        NEAR = int(min_dim * 0.05)       # > 5% = 近
        BASE = int(min_dim * 0.28)       # 速度归一化基数

        if dist > FAR:
            hold_time = 0.2
            speed = max(0.7, min(1.0, dist / BASE))
        elif dist > MID:
            hold_time = 0.1
            speed = max(0.55, dist / BASE)
        elif dist > NEAR:
            hold_time = 0.08
            speed = 0.45
        else:
            hold_time = 0.025
            speed = 0.28

        magnitude = MAX_AXIS * speed
        lx = int(ux * magnitude)
        ly = int(uy * magnitude)

        if lx != 0 and abs(lx) < DEADZONE:
            lx = DEADZONE if lx > 0 else -DEADZONE
        if ly != 0 and abs(ly) < DEADZONE:
            ly = DEADZONE if ly > 0 else -DEADZONE
        lx = max(-MAX_AXIS, min(MAX_AXIS, lx))
        ly = max(-MAX_AXIS, min(MAX_AXIS, ly))

        self._last_stick = (lx, ly)

        gpad.left_joystick(x_value=lx, y_value=ly)
        gpad.update()
        logger.log(f"移动光标: dx={dx}, dy={dy}, dist={dist:.1f}, 摇杆=({lx},{ly}), 保持={hold_time:.2f}s", "DEBUG")
        self._interruptible_sleep(hold_time)

        gpad.left_joystick(x_value=0, y_value=0)
        gpad.update()
        brake_time = 0.08 if dist < 35 else 0.05
        self._interruptible_sleep(brake_time)
        return False

    def _ensure_cursor(self, gpad: vg.VX360Gamepad):
        """如果截图找不到光标，4方向推摇杆搜索"""
        arr = self._screencap()
        if arr is not None:
            pos, _, _ = self._find_cursor_by_shape(arr)
            if pos is not None:
                logger.log(f"光标已找到: {pos}", "DEBUG")
                return pos
        for _, x, y in [("右下", 12000, 12000), ("左下", -12000, 12000),
                        ("右上", 12000, -12000), ("左上", -12000, -12000)]:
            if not self._running:
                return None
            gpad.left_joystick(x_value=x, y_value=y)
            gpad.update()
            time.sleep(0.4)
            self._stop_stick(gpad)
            time.sleep(0.3)
            arr = self._screencap()
            if arr is not None:
                pos, _, _ = self._find_cursor_by_shape(arr)
                if pos is not None:
                    return pos
        return None

    def _blind_move(self, gpad: vg.VX360Gamepad, last_pos: tuple, target: tuple, elapsed: float):
        """光标丢失后盲操推摇杆"""
        dx = target[0] - last_pos[0]
        dy = target[1] - last_pos[1]
        dist = (dx * dx + dy * dy) ** 0.5
        total_needed = dist / 310.0
        if elapsed >= total_needed + 0.3:
            self._stop_stick(gpad)
            return
        ux = dx / dist
        uy = -dy / dist
        lx = int(ux * 8000)
        ly = int(uy * 8000)
        if lx != 0 and abs(lx) < 4260:
            lx = 4260 if lx > 0 else -4260
        if ly != 0 and abs(ly) < 4260:
            ly = 4260 if ly > 0 else -4260
        gpad.left_joystick(x_value=max(-8000, min(8000, lx)),
                           y_value=max(-8000, min(8000, ly)))
        gpad.update()
        logger.log(f"盲操: 摇杆=({lx},{ly}), 已推{elapsed:.1f}s/需{total_needed:.1f}s", "DEBUG")
        self._interruptible_sleep(0.2)
        self._stop_stick(gpad)

    # ---------- 按钮交互 ----------

    def _press_and_verify(self, gpad: vg.VX360Gamepad, cursor_area: float,
                           dist_button: float, btn: ButtonDef) -> Optional[bool]:
        """按 A → 验证是否命中"""
        self._stop_stick(gpad)
        time.sleep(0.2)
        self._press_button(gpad, vg.XUSB_BUTTON.XUSB_GAMEPAD_A, duration=0.3)
        self._interruptible_sleep(1.0)

        if not btn.page_template:
            logger.log(f"「{btn.name}」无验证模板，按A完成")
            return True

        check_arr = self._screencap()
        if check_arr is not None and btn.page_template:
            matched = self._check_page_by_template(btn.page_template)
            if btn.template_should_match and matched:
                logger.log(f"页面已切换（模板「{btn.page_template}」匹配），导航完成")
                return True
            elif not btn.template_should_match and not matched:
                logger.log(f"页面已切换（模板「{btn.page_template}」已消失），导航完成")
                return True
            elif not btn.template_should_match and matched:
                logger.log(f"模板「{btn.page_template}」仍可见，页面未切换，收缩阈值", "WARNING")
                close_th = self._nav_close_threshold if self._nav_close_threshold is not None else btn.close_threshold
                self._nav_close_threshold = max(5, int(close_th * 0.65))
                self._interruptible_sleep(0.5)
                return None

        if check_arr is not None:
            check_pos, _, check_area = self._find_cursor_by_shape(check_arr)
            if check_pos is not None:
                area_drop = cursor_area - check_area
                if area_drop > 100:
                    logger.log(f"页面已切换（面积 {cursor_area}→{check_area}），导航完成")
                    return True
                elif btn.template_should_match:
                    logger.log(f"A 键未命中（面积 {cursor_area}→{check_area}），收缩阈值", "WARNING")
            elif dist_button < 45:
                logger.log("页面已切换（光标消失），导航完成")
                return True

        close_th = self._nav_close_threshold if self._nav_close_threshold is not None else btn.close_threshold
        self._nav_close_threshold = max(30, close_th - 15)
        self._interruptible_sleep(0.5)
        return False

    # ---------- 页面检测 ----------

    def _check_page_by_template(self, template_name: str) -> bool:
        """用 OpenCV 模板匹配检测页面是否已切换"""
        arr = self._screencap()
        if arr is None:
            return False
        template = self._load_template(template_name)
        if template is None:
            return False
        pos, conf, scale = self._find_template(arr, template, threshold=0.55,
                                                scales=[0.5, 0.7, 0.9, 1.0, 1.2, 1.5, 1.8])
        if self.debug.enabled or self.debug.peep_enabled:
            tr_list = []
            if pos is not None:
                tw = int(template.shape[1] * scale)
                th = int(template.shape[0] * scale)
                tr_list.append({
                    "pos": pos, "size": (tw, th),
                    "confidence": conf, "name": template_name
                })
            self.debug.save_frame(arr, template_rects=tr_list, label=f"tpl_{template_name}")
        if pos is not None:
            logger.log(f"模板「{template_name}」匹配成功，置信度={conf:.3f}")
            return True
        logger.log(f"模板「{template_name}」未匹配", "DEBUG")
        return False

    def _wait_for_template(self, template_name: str, timeout: float = 15.0, interval: float = 0.5) -> bool:
        """等待指定模板出现，超时返回 False"""
        for _ in range(int(timeout / interval)):
            if not self._running:
                return False
            if self._check_page_by_template(template_name):
                logger.log(f"等待到模板「{template_name}」")
                return True
            self._interruptible_sleep(interval)
        logger.log(f"等待模板「{template_name}」超时({timeout:.0f}s)", "WARNING")
        return False

    # ---------- 归位 ----------

    def homing(self) -> bool:
        """归位：持续按 B 直到识别到设置页面，再按一次 B 返回主界面"""
        template = self._load_template("settings_page_template")
        if template is None:
            logger.log("设置页面模板不存在，跳过归位", "WARNING")
            return False

        logger.log("开始归位：按 B 直到进入设置页面...")
        gpad = self._get_gpad()

        try:
            for i in range(15):
                if not self._running:
                    logger.log("收到停止信号，中断归位")
                    return False

                arr = self._screencap()
                template_match = None
                if arr is not None:
                    h, w = arr.shape[:2]
                    pos, conf, scale = self._find_template(
                        arr, template, threshold=0.65,
                        scales=[0.8, 0.9, 1.0, 1.1, 1.2],
                        roi=(0, 0, int(w * 0.5), int(h * 0.5)),
                        use_gray=False)
                    if pos is not None:
                        template_match = (pos, conf, scale)

                if arr is not None and (self.debug.enabled or self.debug.peep_enabled):
                    tr_list = []
                    if template_match:
                        pos, conf, scale = template_match
                        tw = int(template.shape[1] * scale)
                        th = int(template.shape[0] * scale)
                        tr_list.append({
                            "pos": pos, "size": (tw, th),
                            "confidence": conf, "name": "settings"
                        })
                    self.debug.save_frame(arr, template_rects=tr_list, label=f"homing_{i+1}")

                if arr is not None and template_match:
                    logger.log(f"归位完成：已识别到设置页面（第{i+1}次按B）")
                    self._press_button(gpad, vg.XUSB_BUTTON.XUSB_GAMEPAD_B, duration=0.3)
                    self._interruptible_sleep(2.0)
                    logger.log("已返回主界面，开始正式循环")
                    return True

                if arr is None:
                    logger.log("截图失败，继续按 B", "WARNING")
                else:
                    logger.log(f"第{i+1}次按 B...", "DEBUG")

                self._press_button(gpad, vg.XUSB_BUTTON.XUSB_GAMEPAD_B, duration=0.3)
                self._interruptible_sleep(1.5)

            logger.log("归位超时（15次按B未识别到设置页面），继续执行", "WARNING")
            return False
        finally:
            try:
                gpad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_B)
                gpad.update()
            except Exception:
                pass

    # ---------- 导航主循环 ----------

    def navigate_to_button(self, btn: ButtonDef) -> bool:
        """导航光标到按钮位置并按 A，用模板匹配/面积变化验证成功"""
        logger.log(f"导航到「{btn.name}」...")
        gpad = self._get_gpad()

        self.debug.start_session(btn.name)
        self._stationary_blacklist.clear()
        self._prev_frame_positions.clear()

        self._ensure_cursor(gpad)

        cursor_lost_start = None
        last_known_pos = None
        arr = None
        button_pos = None

        try:
            for attempt in range(30):
                if not self._running:
                    return False

                time.sleep(0.05)
                arr = self._screencap()
                if arr is None:
                    self._interruptible_sleep(0.5)
                    continue

                h, w = arr.shape[:2]
                button_pos = (int(w * btn.pct[0]), int(h * btn.pct[1]))
                cursor_pos, _, cursor_area = self._find_cursor_by_shape(
                    arr, last_known_pos=last_known_pos, last_stick=self._last_stick)
                close_th = self._nav_close_threshold if self._nav_close_threshold is not None else btn.close_threshold

                if cursor_pos is not None:
                    cursor_lost_start = None

                    if last_known_pos is not None:
                        jump = self._dist(cursor_pos, last_known_pos)
                        if (jump > 250 and cursor_pos[0] < w * 0.3 and cursor_pos[1] < h * 0.2
                                and cursor_area < 250):
                            logger.log(f"跳过缓存帧: {cursor_pos}(面积{cursor_area})←{last_known_pos}, 跳距={jump:.0f}", "DEBUG")
                            self.debug.save_frame(
                                arr, cursor_pos=cursor_pos, cursor_area=cursor_area,
                                cursor_score=self._last_cursor_score,
                                button_pos=button_pos, candidates=self._last_candidates,
                                all_candidates=self._last_all_candidates, label="skip_cache")
                            time.sleep(0.1)
                            continue

                    last_known_pos = cursor_pos
                    dist = self._dist(cursor_pos, button_pos)
                    logger.log(f"光标 {cursor_pos} → 按钮 {button_pos}  "
                               f"(dx={button_pos[0]-cursor_pos[0]}, dy={button_pos[1]-cursor_pos[1]})", "DEBUG")

                    self.debug.save_frame(
                        arr, cursor_pos=cursor_pos, cursor_area=cursor_area,
                        cursor_score=self._last_cursor_score,
                        button_pos=button_pos, candidates=self._last_candidates,
                        all_candidates=self._last_all_candidates,
                        dist=dist, label="found")

                    if dist < close_th:
                        logger.log(f"光标接近按钮：距离={dist:.1f}px（阈值={close_th}），按 A", "DEBUG")
                        result = self._press_and_verify(gpad, cursor_area, dist, btn)
                        if result is True:
                            self._nav_close_threshold = btn.close_threshold
                            return True
                        continue

                    stop_dist = max(8, int(close_th * 0.55))
                    self._move_cursor_to_target(cursor_pos, button_pos, gpad,
                                                stop_distance=stop_dist, w=w, h=h)
                else:
                    if cursor_lost_start is None:
                        cursor_lost_start = time.time()
                        logger.log("光标丢失，开始盲操", "DEBUG")

                    if time.time() - cursor_lost_start >= 2.0:
                        logger.log("光标盲操超过2秒，放弃本次导航", "WARNING")
                        self.debug.save_frame(
                            arr, cursor_pos=None, button_pos=button_pos,
                            candidates=self._last_candidates, all_candidates=self._last_all_candidates,
                            label="lost_timeout")
                        return False

                    if last_known_pos is not None:
                        self._blind_move(gpad, last_known_pos, button_pos, time.time() - cursor_lost_start)
                    else:
                        logger.log("盲操: 无已知位置，向东南搜索", "DEBUG")
                        gpad.left_joystick(x_value=4260, y_value=4260)
                        gpad.update()
                        self._interruptible_sleep(0.3)
                        self._stop_stick(gpad)

                    self.debug.save_frame(
                        arr, cursor_pos=None, button_pos=button_pos,
                        candidates=self._last_candidates, all_candidates=self._last_all_candidates,
                        label=f"blind_{time.time()-cursor_lost_start:.1f}s")
                    self._interruptible_sleep(0.3)
                    continue

                time.sleep(0.05)

            logger.log("导航超时", "WARNING")
            try:
                if arr is not None:
                    self.debug.save_frame(arr, cursor_pos=None, button_pos=button_pos, label="timeout")
            except Exception:
                pass
            return False
        finally:
            try:
                gpad.left_joystick(x_value=0, y_value=0)
                gpad.update()
            except Exception:
                pass

    # ---------- 商店弹窗 ----------

    def handle_store_popup(self) -> bool:
        """导航三成功后等待商店弹窗出现，按A关闭"""
        logger.log("等待商店弹窗出现...")
        if not self._wait_for_template("store_popup_template", timeout=15.0, interval=0.5):
            logger.log("未检测到商店弹窗（可能没有弹出），继续执行")
            return False
        logger.log("检测到商店弹窗，按A关闭")
        gpad = self._get_gpad()
        self._press_button(gpad, vg.XUSB_BUTTON.XUSB_GAMEPAD_A, duration=0.3)
        self._interruptible_sleep(1.0)
        if not self._check_page_by_template("store_popup_template"):
            logger.log("商店弹窗已关闭")
            return True
        logger.log("弹窗仍然存在，再按一次A", "WARNING")
        self._press_button(gpad, vg.XUSB_BUTTON.XUSB_GAMEPAD_A, duration=0.3)
        self._interruptible_sleep(0.5)
        return True
