#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
赛车控制模块：YOLO 实时目标检测 + 虚拟手柄赛道控制
"""

import time
from typing import Any

import cv2
import numpy as np
import vgamepad as vg

from maa.custom_action import CustomAction
from maa.context import Context

from maaracing_assistant.yolo_detector import YOLODetector
from maaracing_assistant.logger import logger


class RacingLoop(CustomAction):
    # 路面 ROI（裁剪掉顶部分数条和底部仪表盘，让 YOLO 专注路面）
    # 1280×720 下 y=28%~78% → (0, 201, 1280, 561)
    ROI = (0, 201, 1280, 561)

    def __init__(self, model_path: str, debug=None):
        super().__init__()
        self.det = YOLODetector(model_path)
        self.debug = debug
        self.gpad = None
        self.last_dir = 0
        self.frame_id = 0
        self._running = True
        self._end_reason = ""  # 最近一次 _is_end 匹配的结果原因
        # 跳帧推理缓存
        self._cached_coins: list = []
        self._cached_cars: list = []
        self._cached_bonus: list = []
        self._cached_yolo_debug: list = []

        # 加载结束检测模板（任一匹配即认为本轮结束）
        from pathlib import Path
        self._end_templates: list[tuple[cv2.Mat, str, float]] = []  # (gray, name, threshold)
        proj = Path(__file__).resolve().parent.parent
        for tpl_file, label, threshold in [
            ("store_popup_template.jpg", "商店弹窗", 0.90),
            ("round1_end_template.jpg", "回合1结束", 0.55),
        ]:
            tpl_path = proj / "assets" / "resource" / "image" / tpl_file
            if tpl_path.exists():
                tpl = cv2.imread(str(tpl_path))
                if tpl is not None:
                    self._end_templates.append((cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY), label, threshold))

    def _create_pad(self):
        """创建新的虚拟手柄并发送归零握手，避免残留偏置"""
        if self.gpad is not None:
            try:
                del self.gpad
            except Exception:
                pass
            self.gpad = None
            time.sleep(0.1)
        self.gpad = vg.VX360Gamepad()
        # 发送 3 次全零报告，清掉驱动层可能的残留状态
        for _ in range(3):
            self.gpad.reset()
            self.gpad.right_trigger(value=0)
            self.gpad.left_trigger(value=0)
            self.gpad.left_joystick(x_value=0, y_value=0)
            self.gpad.right_joystick(x_value=0, y_value=0)
            self.gpad.update()
            time.sleep(0.05)
        logger.log("虚拟手柄已创建并归零")

    def _destroy_pad(self):
        """销毁虚拟手柄，释放设备"""
        if self.gpad is not None:
            try:
                self.gpad.reset()
                self.gpad.update()
            except Exception:
                pass
            try:
                del self.gpad
            except Exception:
                pass
            self.gpad = None
            logger.log("虚拟手柄已销毁")

    def stop(self):
        self._running = False
        self._destroy_pad()
        self.last_dir = 0
        self._cached_coins = []
        self._cached_cars = []
        self._cached_bonus = []
        self._cached_yolo_debug = []

    def _steer(self, direction: int):
        if self.gpad is None:
            return
        # 注意：不在此处控制 RT——RT 由 run() 的入口/finally 统一管理
        if direction == -1:
            self.gpad.left_joystick(x_value=-32768, y_value=0)
        elif direction == 1:
            self.gpad.left_joystick(x_value=32767, y_value=0)
        else:
            self.gpad.left_joystick(x_value=0, y_value=0)
        # 防御：确保右摇杆始终归中（本类不控制视角）
        self.gpad.right_joystick(x_value=0, y_value=0)
        self.gpad.update()

    def _cap(self, ctrl):
        try:
            job = ctrl.post_screencap()
            job.wait()
            img = job.get()
            if img is None:
                logger.log("截图 job.get() 返回 None", "WARNING")
                return None

            if hasattr(img, "numpy"):
                arr = img.numpy()
            elif isinstance(img, np.ndarray):
                arr = img
            elif hasattr(img, "__array__"):
                arr = np.asarray(img)
            else:
                logger.log(f"未知图像类型={type(img).__name__}", "WARNING")
                return None

            if arr is None or arr.size == 0 or arr.ndim < 3:
                logger.log(f"图像格式异常: shape={arr.shape if arr is not None else None}", "WARNING")
                return None
            return arr
        except Exception as e:
            logger.log(f"截图异常: {e}", "ERROR")
            return None

    def _is_end(self, img: np.ndarray) -> bool:
        """检查本轮是否结束：任一模板匹配即算结束"""
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        for tpl_gray, label, threshold in self._end_templates:
            # 检查画面尺寸 >= 模板尺寸，否则 matchTemplate 会崩溃
            if gray.shape[0] < tpl_gray.shape[0] or gray.shape[1] < tpl_gray.shape[1]:
                continue
            result = cv2.matchTemplate(gray, tpl_gray, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            if max_val > threshold:
                self._end_reason = label
                logger.log(f"检测到结束画面「{label}」，置信度={max_val:.3f}")
                return True
        return False

    # ---------- 黄色标线检测 ----------

    def _detect_lane(self, img_rgb: np.ndarray) -> dict | None:
        """检测道路两侧黄色标线，返回左右边界和道路中心"""
        h, w = img_rgb.shape[:2]

        # 检测区域：画面中下部 y=55%~80%（避开地平线模糊区和车头）
        y1, y2 = int(h * 0.55), int(h * 0.80)
        roi = img_rgb[y1:y2, :]

        hsv = cv2.cvtColor(roi, cv2.COLOR_RGB2HSV)
        # 黄色范围（H:15-35, S:80-255, V:80-255）
        lower = np.array([15, 80, 80])
        upper = np.array([35, 255, 255])
        mask = cv2.inRange(hsv, lower, upper)

        # 形态学去噪
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        yellow_x = np.where(mask > 0)[1]
        if len(yellow_x) < 20:  # 黄色像素太少，路况不清
            return None

        # 按画面中心分成左右两组
        center_x = w // 2
        left = yellow_x[yellow_x < center_x]
        right = yellow_x[yellow_x > center_x]

        if len(left) < 5 or len(right) < 5:  # 只找到一侧
            return None

        left_x = int(np.median(left))
        right_x = int(np.median(right))

        # 过滤异常值：标线间距应该合理（目标宽度 40%-80% 画面宽度）
        lane_w = right_x - left_x
        if lane_w < w * 0.3 or lane_w > w * 0.85:
            return None

        return {
            "left": left_x,
            "right": right_x,
            "center": (left_x + right_x) // 2,
        }

    # ---------- 全局路径规划 ----------

    def _keep_center(self, lane: dict, w: int) -> int:
        """保持道路中心：偏离太远就往回带"""
        # 车在道路中的位置（0~1）
        lane_w = lane["right"] - lane["left"]
        car_pos = (w // 2 - lane["left"]) / lane_w if lane_w > 0 else 0.5

        if car_pos < 0.20:
            logger.log(f"[LANE] 太靠左(pos={car_pos:.2f})，右转归中")
            return 1
        elif car_pos > 0.80:
            logger.log(f"[LANE] 太靠右(pos={car_pos:.2f})，左转归中")
            return -1
        return 0

    def _aim_at(self, target: tuple, w: int, lane: dict | None) -> int:
        """对准目标，结合车道中心作为参考"""
        cx = target[0]
        # 有车道信息时以车道中心为"正中"
        ref_center = lane["center"] if lane else w // 2
        deadzone = w * 0.06

        if cx < ref_center - deadzone:
            return -1
        elif cx > ref_center + deadzone:
            return 1
        return 0

    def _avoid(self, cars: list, bonus_cars: list, w: int, h: int, lane: dict | None) -> int:
        """在道路范围内避让障碍车"""
        DANGER_Y = h * 0.30  # 只关心中下部的车
        center_x = w // 2
        ref_center = lane["center"] if lane else center_x

        # 先选目标（跳板车 > 金币 > 车群）
        # 这里只处理障碍车避让

        # 过滤出危险区内的车
        threats = [c for c in cars if c[1] > DANGER_Y]
        if not threats:
            return 0

        # 选最近的威胁
        threat = max(threats, key=lambda c: c[1])
        tx, ty = threat[0], threat[1]

        # 横向车道宽度
        LANE_W = w * 0.12
        THREAT_RANGE = LANE_W * 1.8

        # 如果在横向范围内
        if abs(tx - ref_center) > THREAT_RANGE:
            return 0  # 不在当前车道，不构成威胁

        # 检查左右占道
        def in_lane(cx, cy):
            return cy > DANGER_Y and abs(cx - ref_center) < LANE_W * 2.2

        left_occ = any(c[0] < ref_center and in_lane(c[0], c[1]) for c in cars)
        right_occ = any(c[0] > ref_center and in_lane(c[0], c[1]) for c in cars)

        # 决策
        if tx < ref_center - LANE_W * 0.3:
            # 威胁在左 → 想往右躲
            if not right_occ:
                return 1
            if not left_occ:
                return -1
            return 0
        elif tx > ref_center + LANE_W * 0.3:
            # 威胁在右 → 想往左躲
            if not left_occ:
                return -1
            if not right_occ:
                return 1
            return 0
        else:
            # 正前方
            if not left_occ and right_occ:
                return -1
            if not right_occ and left_occ:
                return 1
            return 0

    def _decide(self, coins: list, cars: list, bonus_cars: list,
                lane: dict | None, w: int, h: int) -> int:
        """
        全局决策：标线基底 → 目标优先级 → 保持中心
        """
        # ========== 0. 边缘紧急修正（最高优先级） ==========
        if lane is not None:
            correction = self._keep_center(lane, w)
            if correction != 0:
                return correction

        # ========== 1. 跳板车 ==========
        if bonus_cars:
            target = max(bonus_cars, key=lambda b: b[1])
            aim = self._aim_at(target, w, lane)
            if aim != 0:
                cls = "左" if aim == -1 else "右"
                logger.log(f"[YOLO] bonus_car({target[0]:.0f},{target[1]:.0f})，{cls}转对准")
            else:
                logger.log(f"[YOLO] bonus_car({target[0]:.0f},{target[1]:.0f})，直冲")
            return aim

        # ========== 2. 障碍车避让（用道路范围约束） ==========
        # 先看附近有没有威胁
        DANGER_Y = h * 0.35
        near_cars = [c for c in cars if c[1] > DANGER_Y]
        if near_cars:
            return self._avoid(near_cars, bonus_cars, w, h, lane)

        # ========== 3. 金币 ==========
        if coins:
            # 选最近的金币（周围有同伴的加分）
            def coin_value(c):
                cx, cy = c[0], c[1]
                nearby = sum(1 for o in coins
                            if abs(o[0] - cx) < w * 0.2
                            and 0 < cy - o[1] < h * 0.3)
                return cy + nearby * 50
            target = max(coins, key=coin_value)
            aim = self._aim_at(target, w, lane)
            if aim != 0:
                cls = "左" if aim == -1 else "右"
                logger.log(f"[YOLO] 金币({target[0]:.0f})，{cls}转")
            return aim

        # ========== 4. 无目标 → 保持道路中心 ==========
        if lane is not None:
            return self._keep_center(lane, w)
        if self.frame_id % 15 == 0:
            logger.log("[YOLO] 无目标，直行")
        return 0

    def _run_impl(self, ctrl) -> bool:
        """赛车控制核心逻辑（被 run / run_direct 共用）"""
        logger.log("赛车控制启动")
        self._running = True
        self.frame_id = 0  # 重试时重置帧计数
        if self.debug is not None:
            self.debug.start_session("racing")
        self._create_pad()

        # ── 常量 ──
        YOLO_INTERVAL = 3          # 每 3 游戏帧做一次 YOLO 推理
        SLOW_CHECK = 15            # 每秒（~15fps）检一次商店/结束
        LABEL_LOG = 30             # 每 30 帧打一次日志
        DISK_SAVE = 15             # 每 15 帧写一次磁盘(≈5次YOLO帧)

        # 起步：按住 RT 加速（游戏内部有倒计时，车不会立即动）
        assert self.gpad is not None, "手柄未创建"
        self.gpad.right_trigger(value=255)
        self.gpad.update()

        try:
            while self._running:
                t0 = time.time()
                img = self._cap(ctrl)
                if img is None:
                    time.sleep(0.05)
                    continue

                self.frame_id += 1
                h, w = img.shape[:2]

                # ── 黄色标线检测（每帧，开销极低） ──
                lane = self._detect_lane(img)

                # ── 1Hz 检测：本轮结束（商店弹窗 / 回合1结束画面） ──
                if self.frame_id % SLOW_CHECK == 0 and self._is_end(img):
                    self._steer(0)
                    return True

                # ── 跳帧 YOLO 推理 ──
                if self.frame_id % YOLO_INTERVAL == 0:
                    coins, cars, bonus_cars, yolo_debug = self.det(img, roi=self.ROI)
                    self._cached_coins = coins
                    self._cached_cars = cars
                    self._cached_bonus = bonus_cars
                    self._cached_yolo_debug = yolo_debug
                coins = self._cached_coins
                cars = self._cached_cars
                bonus_cars = self._cached_bonus
                yolo_debug = self._cached_yolo_debug

                direction = self._decide(coins, cars, bonus_cars, lane, w, h)

                if self.frame_id % LABEL_LOG == 5:
                    logger.log(f"[YOLO] 帧#{self.frame_id} 金币={len(coins)} 障碍车={len(cars)} 跳板车={len(bonus_cars)} 方向={'左' if direction<0 else '右' if direction>0 else '直'}")

                if direction != self.last_dir:
                    self._steer(direction)
                    self.last_dir = direction

                # ── 调试帧 ──
                if self.debug is not None and (self.debug.enabled or self.debug.peep_enabled):
                    save_disk = self.debug.enabled
                    self.debug.save_frame(
                        img, detections=yolo_debug, lane=lane,
                        label=f"race_f{self.frame_id}_d{'L' if direction==-1 else 'S' if direction==0 else 'R'}",
                        save_to_disk=save_disk,
                    )

                elapsed = time.time() - t0
                sleep = max(0, 1 / 15 - elapsed)
                if sleep:
                    time.sleep(sleep)
        finally:
            self._destroy_pad()
            self.last_dir = 0
            logger.log("赛车控制停止")
        return False

    def run(self, context: Context, argv: dict) -> bool:  # type: ignore[override]
        """MAA Pipeline CustomAction 入口（保留兼容）"""
        return self._run_impl(context.controller)

    def run_direct(self, ctrl) -> bool:
        """绕过 MAA Pipeline 直接运行赛车控制"""
        return self._run_impl(ctrl)
